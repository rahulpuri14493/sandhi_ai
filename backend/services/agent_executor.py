import logging
import json
from datetime import datetime
from typing import Dict, Any, Optional
import httpx
from sqlalchemy.orm import Session
from models.job import Job, JobStatus, WorkflowStep
from models.agent import Agent
from models.communication import AgentCommunication
from models.audit_log import AuditLog
from models.mcp_server import MCPToolConfig, MCPServerConnection
from services.payment_processor import PaymentProcessor
from services.a2a_client import execute_via_a2a
from services.mcp_client import call_tool as mcp_call_tool
from services.db_schema_introspection import format_schema_for_prompt
from core.config import settings

logger = logging.getLogger(__name__)


def _safe_slug(name: str) -> str:
    """Slug for tool name (alphanumeric and underscore)."""
    if not name:
        return ""
    return "".join(c if c.isalnum() or c in "_-" else "_" for c in (name or "").strip())[:50]


def _parse_allowed_ids(value: Optional[str]) -> Optional[list]:
    """Parse JSON array string from job/step allowed_* columns to list of ints."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if isinstance(value, list):
        return value
    try:
        out = json.loads(value)
        return [int(x) for x in out] if isinstance(out, list) else None
    except (json.JSONDecodeError, TypeError, ValueError):
        return None


# OpenAI-style tool parameter schemas for MCP tool types (must match platform MCP server expectations)
def _input_schema_for_tool_type(tool_type: str) -> dict:
    sql_schema = {
        "type": "object",
        "properties": {"query": {"type": "string", "description": "SQL SELECT query (read-only)"}},
        "required": ["query"],
    }
    vector_schema = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Search query or embedding query"},
            "top_k": {"type": "integer", "description": "Max results", "default": 5},
        },
        "required": ["query"],
    }
    schemas = {
        "postgres": sql_schema,
        "mysql": sql_schema,
        "vector_db": vector_schema,
        "pinecone": vector_schema,
        "weaviate": vector_schema,
        "qdrant": vector_schema,
        "chroma": vector_schema,
        "elasticsearch": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query"},
                "index": {"type": "string", "description": "Index name"},
                "size": {"type": "integer", "default": 10},
            },
            "required": ["query"],
        },
        "filesystem": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Relative path under base_path"},
                "action": {"type": "string", "enum": ["read", "list"], "default": "read"},
            },
            "required": ["path"],
        },
        "s3": {
            "type": "object",
            "properties": {
                "key": {"type": "string", "description": "Object key or prefix"},
                "action": {"type": "string", "enum": ["get", "list"], "default": "get"},
            },
            "required": ["key"],
        },
        "slack": {
            "type": "object",
            "properties": {
                "channel": {"type": "string", "description": "Channel ID or name"},
                "message": {"type": "string", "description": "Message text"},
                "action": {"type": "string", "enum": ["list_channels", "send"], "default": "send"},
            },
        },
        "github": {
            "type": "object",
            "properties": {
                "repo": {"type": "string", "description": "owner/repo"},
                "path": {"type": "string", "description": "File path or 'issues'"},
                "action": {"type": "string", "enum": ["get_file", "list_issues", "search"], "default": "get_file"},
            },
        },
        "notion": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["search", "get_page", "get_database"], "default": "search"},
                "query": {"type": "string", "description": "Search query or page/database ID"},
            },
        },
        "rest_api": {
            "type": "object",
            "properties": {
                "method": {"type": "string", "enum": ["GET", "POST", "PUT", "PATCH", "DELETE"], "default": "GET"},
                "path": {"type": "string", "description": "Path or full URL"},
                "body": {"type": "object", "description": "JSON body for POST/PUT/PATCH"},
            },
            "required": ["path"],
        },
    }
    return schemas.get((tool_type or "").lower(), {"type": "object", "properties": {}})


def _openai_tools_from_mcp(available_mcp_tools: list) -> list:
    """Build OpenAI tools array from available_mcp_tools for function calling."""
    tools = []
    for t in available_mcp_tools or []:
        name = t.get("name") or ""
        if not name:
            continue
        description = t.get("description") or name
        if t.get("schema_metadata") or t.get("business_description"):
            description = description.rstrip(". ") + ". Database schema and business context are in the system message—use them to write correct SQL."
        tool_type = t.get("tool_type")
        schema = _input_schema_for_tool_type(tool_type) if tool_type else {"type": "object", "properties": {}}
        tools.append({
            "type": "function",
            "function": {
                "name": name,
                "description": description,
                "parameters": schema,
            },
        })
    return tools


class AgentExecutor:
    def __init__(self, db: Session):
        self.db = db
        self.payment_processor = PaymentProcessor(db)
    
    async def execute_job(self, job_id: int):
        """Execute a job by running all workflow steps"""
        job = self.db.query(Job).filter(Job.id == job_id).first()
        if not job:
            raise ValueError("Job not found")
        
        # Process payment first
        transaction = self.payment_processor.process_payment(job_id)
        
        # Get workflow steps in order
        workflow_steps = self.db.query(WorkflowStep).filter(
            WorkflowStep.job_id == job_id
        ).order_by(WorkflowStep.step_order).all()
        
        if not workflow_steps:
            job.status = JobStatus.FAILED
            job.failure_reason = "No workflow steps found for this job"
            self.db.commit()
            return
        
        previous_output = None
        
        try:
            # Strict sequential execution: steps run in step_order; each step gets previous_output only when depends_on_previous is True
            for step in workflow_steps:
                # Update step status
                step.status = "in_progress"
                step.started_at = datetime.utcnow()
                self.db.commit()
                
                # Log execution start
                self._log_action("workflow_step", step.id, "execution_started", {
                    "job_id": job_id,
                    "agent_id": step.agent_id
                })
                
                # Execute agent
                agent = self.db.query(Agent).filter(Agent.id == step.agent_id).first()
                if not agent:
                    raise ValueError(f"Agent {step.agent_id} not found")
                
                # Prepare input data
                # Independent steps (depends_on_previous=False) do not receive previous agent output.
                # Sequential steps receive previous_step_output for handoff.
                depends_on_previous = getattr(step, "depends_on_previous", True)
                if previous_output and depends_on_previous:
                    # Merge previous output with base input data from step
                    base_input = json.loads(step.input_data) if step.input_data else {}
                    input_data = {
                        **base_input,  # Job context, conversation, documents, etc.
                        "previous_step_output": previous_output  # Output from previous agent
                    }
                else:
                    # First step or independent step - use step's input data only (no previous output)
                    input_data = json.loads(step.input_data) if step.input_data else {}

                # Resolve which tools this step (agent) can use: step-level overrides; else job-level; else all business tools
                job_platform_ids = _parse_allowed_ids(getattr(job, "allowed_platform_tool_ids", None))
                job_conn_ids = _parse_allowed_ids(getattr(job, "allowed_connection_ids", None))
                step_platform_ids = _parse_allowed_ids(getattr(step, "allowed_platform_tool_ids", None))
                step_conn_ids = _parse_allowed_ids(getattr(step, "allowed_connection_ids", None))
                # Step explicit list (including []) overrides job; else use job list; else None = all business tools
                effective_platform = step_platform_ids if step_platform_ids is not None else job_platform_ids
                effective_conn = step_conn_ids if step_conn_ids is not None else job_conn_ids
                # Step can only use tools that are in job scope
                if job_platform_ids is not None and effective_platform is not None:
                    effective_platform = [x for x in effective_platform if x in job_platform_ids]
                if job_conn_ids is not None and effective_conn is not None:
                    effective_conn = [x for x in effective_conn if x in job_conn_ids]

                available_mcp_tools = self._get_available_mcp_tools(
                    job.business_id,
                    platform_tool_ids=effective_platform if effective_platform else None,
                    connection_ids=effective_conn if effective_conn else None,
                )
                if available_mcp_tools:
                    input_data["available_mcp_tools"] = available_mcp_tools
                    input_data["business_id"] = job.business_id
                    if step.step_order == 1:
                        self._log_action("job", job_id, "mcp_tool_discovery", {
                            "business_id": job.business_id,
                            "tool_count": len(available_mcp_tools),
                            "tool_names": [t.get("name") for t in available_mcp_tools[:20]],
                        })
                
                # Uploaded documents are requirement documents: agent must understand them, ask questions if any, else execute and answer.
                documents = input_data.get('documents', [])
                conversation = input_data.get('conversation', [])
                
                # Execution flow: 1) Get step's input_data (job context, requirement docs, conversation).
                # 2) Only hired agents (with api_endpoint) are supported – call agent's endpoint with api_key.
                # 3) If agent has plugin_config → run plugin. 4) Response is stored as step output.
                if not (agent.api_endpoint and (agent.api_endpoint or "").strip()):
                    raise ValueError(
                        f"Only hired agents with an API endpoint are supported. "
                        f"Agent '{agent.name}' (id={agent.id}) has no api_endpoint configured."
                    )
                logger.debug("========== Executing Step %s ==========", step.step_order)
                logger.debug("Agent: %s (hired endpoint)", agent.name)
                logger.debug("Agent endpoint: %s", agent.api_endpoint)
                logger.debug("Job Title: %s", input_data.get('job_title', 'N/A'))
                logger.debug("Job Description: %s...", (input_data.get('job_description') or 'N/A')[:100])
                
                # Log documents
                if documents:
                    logger.debug("Found %s document(s) to send to agent", len(documents))
                    for i, doc in enumerate(documents):
                        content_length = len(doc.get('content', '')) if doc.get('content') else 0
                        content_preview = doc.get('content', '')[:150] if doc.get('content') else 'EMPTY'
                        logger.debug("Document %s: %s type=%s content_length=%s preview=%s...", i + 1, doc.get('name', 'Unknown'), doc.get('type', 'unknown'), content_length, content_preview)
                else:
                    logger.warning("No documents found in input_data")
                
                # Log conversation
                if conversation:
                    questions = [item for item in conversation if item.get('type') == 'question']
                    answers = [item for item in conversation if item.get('type') == 'question' and item.get('answer')]
                    completions = [item for item in conversation if item.get('type') == 'completion']
                    logger.debug("Found %s conversation item(s) questions=%s answered=%s completions=%s", len(conversation), len(questions), len(answers), len(completions))
                    if completions:
                        logger.debug("Latest completion: %s...", (completions[-1].get('message') or 'N/A')[:100])
                else:
                    logger.warning("No conversation found in input_data")
                
                logger.debug("=================================================")
                
                # Execute agent (API or plugin)
                try:
                    output_data = await self._execute_agent(agent, input_data)
                    
                    # Update step with output
                    step.output_data = json.dumps(output_data)
                    step.status = "completed"
                    step.completed_at = datetime.utcnow()
                    step.cost = agent.price_per_task
                except Exception as step_error:
                    # Mark step as failed
                    step.status = "failed"
                    step.completed_at = datetime.utcnow()
                    error_msg = str(step_error)
                    if len(error_msg) > 500:
                        error_msg = error_msg[:500] + "..."
                    # Store error in output_data for reference
                    step.output_data = json.dumps({"error": error_msg})
                    self.db.commit()
                    
                    # Re-raise to mark job as failed
                    raise Exception(f"Workflow step {step.step_order} failed: {error_msg}")
                
                # Log communication if there's a previous step
                if previous_output is not None:
                    previous_step = workflow_steps[workflow_steps.index(step) - 1]
                    self._log_communication(previous_step, step, previous_output)
                
                previous_output = output_data
                self.db.commit()
                
                # Log execution completion
                self._log_action("workflow_step", step.id, "execution_completed", {
                    "job_id": job_id,
                    "agent_id": step.agent_id,
                    "output_size": len(str(output_data))
                })
            
            # Mark job as completed
            job.status = JobStatus.COMPLETED
            job.completed_at = datetime.utcnow()
            self.db.commit()
            
            # Distribute earnings
            self.payment_processor.distribute_earnings(job_id)
            
            # Log job completion
            self._log_action("job", job_id, "completed", {
                "total_cost": job.total_cost
            })
        
        except Exception as e:
            # Mark job as failed with reason
            job.status = JobStatus.FAILED
            error_message = str(e)
            # Truncate very long error messages
            if len(error_message) > 500:
                error_message = error_message[:500] + "..."
            job.failure_reason = error_message
            self.db.commit()
            
            # Log error
            self._log_action("job", job_id, "failed", {
                "error": error_message
            })
            raise
    
    async def _execute_agent(self, agent: Agent, input_data: Dict[str, Any]) -> Dict[str, Any]:
        """Execute agent via plugin, A2A protocol, or OpenAI-compatible (via platform adapter). Architecture runs A2A everywhere: native A2A agents or OpenAI endpoints via the adapter."""
        if agent.plugin_config:
            return await self._execute_plugin_agent(agent, input_data)
        if getattr(agent, "a2a_enabled", False):
            return await self._execute_a2a_agent(agent, input_data)
        # OpenAI-compatible endpoint: route through platform A2A adapter so architecture is A2A everywhere
        adapter_url = (getattr(settings, "A2A_ADAPTER_URL", None) or "").strip()
        if adapter_url:
            return await self._execute_via_adapter(agent, input_data, adapter_url)
        return await self._execute_api_agent(agent, input_data)
    
    async def _execute_a2a_agent(self, agent: Agent, input_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Execute agent via A2A protocol (JSON-RPC 2.0 SendMessage).
        Used when agent.a2a_enabled is True.
        input_data includes available_mcp_tools and previous_step_output for MCP/sequential collaboration.
        """
        url = (agent.api_endpoint or "").strip()
        if not url:
            raise ValueError(
                f"A2A-enabled agent must have api_endpoint configured. "
                f"Agent '{agent.name}' (id={agent.id}) has no api_endpoint."
            )
        api_key = (agent.api_key or "").strip() or None
        logger.debug("Executing agent '%s' via A2A: %s", agent.name, url)
        result = await execute_via_a2a(
            url,
            input_data,
            api_key=api_key,
            blocking=True,
            timeout=120.0,
        )
        # Return shape compatible with step output and _extract_agent_output_content
        return result

    async def _execute_via_adapter(self, agent: Agent, input_data: Dict[str, Any], adapter_url: str) -> Dict[str, Any]:
        """
        Execute OpenAI-compatible agent via platform A2A adapter. Adapter receives A2A
        and forwards to agent.api_endpoint with OpenAI payload; returns A2A response.
        When MCP tools are available, runs an agentic loop: send tools to the model,
        on tool_calls invoke the platform MCP server and append results, then call again
        until the model returns a final answer (no tool_calls).
        """
        url = (agent.api_endpoint or "").strip()
        if not url:
            raise ValueError(
                f"Agent must have api_endpoint configured. "
                f"Agent '{agent.name}' (id={agent.id}) has no api_endpoint."
            )
        api_key = (agent.api_key or "").strip() or None
        model = (getattr(agent, "llm_model", None) or "").strip() or "gpt-4o-mini"
        logger.debug("Executing agent '%s' via A2A adapter -> %s", agent.name, url)
        payload = self._format_for_openai(agent, input_data)
        messages = payload.get("messages", [])
        available_mcp_tools = input_data.get("available_mcp_tools") or []
        openai_tools = _openai_tools_from_mcp(available_mcp_tools) if available_mcp_tools else []
        business_id = input_data.get("business_id")
        max_tool_iterations = 5
        content = ""

        for iteration in range(max_tool_iterations):
            metadata = {
                "openai_url": url,
                "openai_api_key": api_key or "",
                "openai_model": model,
                "openai_messages": messages,
            }
            if openai_tools:
                metadata["openai_tools"] = openai_tools
            result = await execute_via_a2a(
                adapter_url,
                input_data,
                api_key=None,
                blocking=True,
                timeout=120.0,
                adapter_metadata=metadata,
            )
            content = result.get("content") or ""
            tool_calls = result.get("tool_calls")
            if not tool_calls or not business_id:
                break
            # Append assistant message with tool_calls
            messages.append({"role": "assistant", "content": content or None, "tool_calls": tool_calls})
            for tc in tool_calls:
                tc_id = tc.get("id")
                fn = tc.get("function") or {}
                tool_name = fn.get("name")
                args_raw = fn.get("arguments") or "{}"
                try:
                    args = json.loads(args_raw) if isinstance(args_raw, str) else args_raw
                except (json.JSONDecodeError, TypeError):
                    args = {}
                tool_result = await self._call_platform_mcp_tool(business_id, tool_name, args)
                messages.append({"role": "tool", "tool_call_id": tc_id, "content": tool_result})
            logger.info("MCP tool round %s: %s tool call(s), continuing agent loop", iteration + 1, len(tool_calls))

        return {"content": content}

    async def _execute_api_agent(self, agent: Agent, input_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Execute agent via its configured API endpoint (hired agent only).
        Only agents with api_endpoint are supported.
        """
        payload = self._format_input_for_agent(agent, input_data)
        headers = {"Content-Type": "application/json"}

        url = (agent.api_endpoint or "").strip()
        if not url:
            raise ValueError(
                f"Only hired agents with an API endpoint are supported. "
                f"Agent '{agent.name}' (id={agent.id}) has no api_endpoint configured."
            )
        if agent.api_key and (agent.api_key or "").strip():
            headers["Authorization"] = f"Bearer {(agent.api_key or '').strip()}"
        if isinstance(payload, dict):
            payload["model"] = payload.get("model") or "gpt-4o-mini"
        client_kwargs = {"timeout": 120.0, "verify": False}
        logger.debug("Executing agent '%s' via hired endpoint: %s", agent.name, url)

        payload_for_log = self._truncate_payload_for_log(payload, max_content_len=1500)
        logger.info("Request payload: %s", json.dumps(payload_for_log, indent=2, default=str))

        async with httpx.AsyncClient(**client_kwargs) as client:
            response = await client.post(url, json=payload, headers=headers)

            try:
                response_body = response.text
            except Exception:
                response_body = "(unable to read response body)"
            response_for_log = response_body if len(response_body) <= 2000 else response_body[:2000] + "\n... (truncated)"
            logger.info("Response status: %s %s", response.status_code, response.reason_phrase)
            logger.info("Response body: %s", response_for_log)

            if response.status_code >= 400:
                body = response_body if len(response_body) <= 800 else response_body[:800] + "..."
                logger.error("Agent API returned %s: %s", response.status_code, body)
                raise Exception(
                    f"Agent inference returned {response.status_code} {response.reason_phrase}. Response: {body}"
                )
            return response.json()

    def _extract_agent_output_content(self, prev_output: Any) -> str:
        """Extract the meaningful content from previous agent's output (OpenAI-compatible or raw)."""
        if prev_output is None:
            return "(no output)"
        if isinstance(prev_output, str):
            return prev_output.strip() or "(empty)"
        if isinstance(prev_output, dict):
            # OpenAI chat completion format
            choices = prev_output.get("choices") or []
            if choices and isinstance(choices[0], dict):
                msg = choices[0].get("message") or {}
                if isinstance(msg, dict):
                    content = msg.get("content")
                    if content is not None:
                        return str(content).strip() if content else "(empty)"
            # Fallback: try "content" or "result" at top level
            for key in ("content", "result", "output", "text"):
                if key in prev_output and prev_output[key]:
                    return str(prev_output[key]).strip()
        return json.dumps(prev_output, indent=2)

    def _truncate_payload_for_log(self, payload: Dict[str, Any], max_content_len: int = 1500) -> Dict[str, Any]:
        """Return a copy of the payload with long message content truncated for logging."""
        if not isinstance(payload, dict):
            return payload
        out = {}
        for k, v in payload.items():
            if k == "messages" and isinstance(v, list):
                out[k] = []
                for msg in v:
                    if isinstance(msg, dict) and "content" in msg and isinstance(msg["content"], str):
                        c = msg["content"]
                        truncated = c if len(c) <= max_content_len else c[:max_content_len] + f"... [truncated, total {len(c)} chars]"
                        out[k].append({**msg, "content": truncated})
                    else:
                        out[k].append(msg)
            else:
                out[k] = v
        return out

    def _format_input_for_agent(self, agent: Agent, input_data: Dict[str, Any]) -> Dict[str, Any]:
        """Format input data for hired agent API (OpenAI-compatible format)."""
        return self._format_for_openai(agent, input_data)
    
    def _format_for_openai(self, agent: Agent, input_data: Dict[str, Any]) -> Dict[str, Any]:
        """Format input data for OpenAI-compatible API"""
        messages = []
        
        # Get job information
        job_title = input_data.get('job_title', 'N/A')
        job_description = input_data.get('job_description', '')
        documents = input_data.get('documents', [])
        conversation = input_data.get('conversation', [])
        
        # Build comprehensive system message
        system_content = "You are an AI agent operating within the Sandhi AI platform. Your job is to answer correctly and deliver the requested output.\n\n"
        system_content += "BEHAVIOR:\n"
        system_content += "1. Read and understand the requirement documents and any existing Q&A. Use them as the source of truth.\n"
        system_content += "2. Execute the task and provide your complete, correct answer. Do NOT ask unnecessary or redundant questions.\n"
        system_content += "3. Only ask questions when information is strictly necessary and completely missing (e.g. a value that cannot be inferred). When in doubt, proceed with reasonable interpretation and answer.\n"
        system_content += "4. Give direct, accurate answers. Use exact values and criteria from the documents; do not deviate.\n\n"
        
        # Add agent context
        if input_data.get('agent_name'):
            system_content += f"Your Role: {input_data.get('agent_name')}"
            if input_data.get('agent_description'):
                system_content += f" - {input_data.get('agent_description')}"
            system_content += "\n\n"
        
        if documents:
            system_content += "═══════════════════════════════════════════════════════\n"
            system_content += "REQUIREMENT DOCUMENTS (primary source of truth)\n"
            system_content += "═══════════════════════════════════════════════════════\n\n"
            system_content += "The uploaded documents define the requirements. Answer based on these documents and any Q&A below. Prioritize correct execution over asking questions.\n\n"
            system_content += "JOB CONTEXT:\n"
            system_content += f"  - Job Title: {job_title}\n"
            if job_description and job_description.strip():
                system_content += f"  - Job Description: {job_description}\n"
            system_content += "\n"
            system_content += "Requirement document text is provided below. Use it to produce your answer.\n\n"
            system_content += "═══════════════════════════════════════════════════════\n\n"
        else:
            system_content += "═══════════════════════════════════════════════════════\n"
            system_content += "PRIMARY REQUIREMENTS:\n"
            system_content += "═══════════════════════════════════════════════════════\n\n"
            system_content += f"JOB TITLE: {job_title}\n\n"
            if job_description and job_description.strip():
                system_content += f"JOB DESCRIPTION:\n{job_description}\n\n"
            else:
                system_content += "JOB DESCRIPTION: (No description provided)\n\n"
            system_content += "═══════════════════════════════════════════════════════\n\n"
        
        # Use assigned_task when present (multi-agent workflow)
        assigned_task = input_data.get('assigned_task', '')
        step_order = input_data.get('step_order')
        total_steps = input_data.get('total_steps')
        has_previous_output = input_data.get('previous_step_output') is not None
        if assigned_task and assigned_task.strip():
            system_content += "═══════════════════════════════════════════════════════\n"
            system_content += "CRITICAL: YOUR ASSIGNED TASK (multi-agent workflow)\n"
            system_content += "═══════════════════════════════════════════════════════\n\n"
            if step_order is not None and total_steps is not None and total_steps > 1:
                system_content += f"You are Agent {step_order} of {total_steps}. "
                if has_previous_output:
                    system_content += "This is a sequential workflow: you will receive the previous agent's output below. Use it as your input.\n"
            system_content += "Perform ONLY your assigned subtask below. Do NOT perform work assigned to other agents.\n"
            system_content += "If the documents describe the full workflow, IGNORE the parts that are not your assignment.\n\n"
            system_content += f"YOUR TASK:\n{assigned_task.strip()}\n\n"
        else:
            if has_previous_output and step_order is not None and total_steps is not None and total_steps > 1:
                system_content += "SEQUENTIAL WORKFLOW: You receive the previous agent's output below. Use it as your input and continue the pipeline.\n\n"
            system_content += "TASK: Execute the job from the requirements above and provide your complete, correct answer. Do not ask questions unless something is strictly required and impossible to infer.\n"
        # MCP tool discovery: inform agent of available tools (platform + external) for collaboration
        available_mcp_tools = input_data.get("available_mcp_tools") or []
        if available_mcp_tools:
            system_content += "\n═══════════════════════════════════════════════════════\n"
            system_content += "AVAILABLE MCP TOOLS (data sources / APIs for this job)\n"
            system_content += "═══════════════════════════════════════════════════════\n\n"
            system_content += "The following tools are available. When the task requires running a query or fetching data, you MUST invoke the corresponding tool (use a function/tool call). "
            system_content += "Do NOT only describe or write the query in text—the platform runs the query only when you call the tool. "
            system_content += "Call the tool with the appropriate arguments (e.g. the SQL query); then use the tool result in your final answer.\n\n"
            for t in available_mcp_tools:
                system_content += f"  - {t.get('name', '')}: {t.get('description', '')}\n"
            system_content += "\n"
            # Database schema and business context for SQL tools (so the agent can write correct queries)
            schema_tools = [t for t in available_mcp_tools if t.get("schema_metadata") or t.get("business_description")]
            if schema_tools:
                system_content += "═══════════════════════════════════════════════════════\n"
                system_content += "DATABASE SCHEMA AND CONTEXT (use this to write correct SQL)\n"
                system_content += "═══════════════════════════════════════════════════════\n\n"
                for t in schema_tools:
                    tool_name = t.get("name", "")
                    system_content += f"--- Database context for tool: {tool_name} ---\n"
                    if t.get("business_description"):
                        system_content += f"Business context: {t.get('business_description')}\n"
                    if t.get("schema_metadata"):
                        try:
                            schema_dict = json.loads(t["schema_metadata"])
                            formatted = format_schema_for_prompt(schema_dict, max_chars=8000)
                            if formatted:
                                system_content += "Schema:\n" + formatted + "\n"
                        except (TypeError, json.JSONDecodeError):
                            pass
                    system_content += "\n"
                system_content += "Use the schema above to write valid SQL for the corresponding tool. Do not guess table or column names. "
                system_content += "Then invoke that tool with your SQL so the platform executes it and returns results; do not only show the SQL in your message.\n\n"
        messages.append({"role": "system", "content": system_content})
        
        # Add document content to messages with clear formatting (if documents exist)
        # Platform extracts text from uploaded files and sends it here so the agent can use it (no file access needed).
        if documents:
            # Only treat as non-extractable when it's a known error/placeholder from document_analyzer.
            # Do NOT reject real requirement text that happens to start with "[" or contain "requires".
            def _is_extracted_content(raw: str) -> bool:
                if not raw or not raw.strip():
                    return False
                s = raw.strip()
                if not s.startswith("["):
                    return True
                lower = s.lower()
                # Reject only known extraction-failure messages from document_analyzer
                if "[error extracting" in lower or "[error reading" in lower:
                    return False
                if "extraction requires" in lower and "library" in lower:
                    return False
                if "contains no extractable text" in lower or "contains no data" in lower:
                    return False
                if "unsupported file type" in lower:
                    return False
                return True

            logger.debug("Formatting %s requirement documents for API (content is inline below)", len(documents))
            messages.append({
                "role": "user",
                "content": "═══════════════════════════════════════════════════════\n📋 REQUIREMENT DOCUMENTS (text below):\n═══════════════════════════════════════════════════════\n\nThe requirement document text is in the next message(s). Use it to execute the job and provide your correct answer. Do not ask unnecessary questions.\n\nDocument(s) follow:"
            })

            for i, doc in enumerate(documents):
                doc_name = doc.get('name', 'Unknown')
                doc_type = doc.get('type', 'unknown')
                doc_content = (doc.get('content') or '').strip()

                logger.debug("Document %s: %s - Content length: %s chars", i + 1, doc_name, len(doc_content))

                if _is_extracted_content(doc_content):
                    doc_message = f"""═══════════════════════════════════════════════════════
📄 DOCUMENT {i+1}: {doc_name} (Type: {doc_type})
═══════════════════════════════════════════════════════

FULL TEXT OF DOCUMENT (use this as the requirement source):

{doc_content}

═══════════════════════════════════════════════════════
END DOCUMENT {i+1}: {doc_name}
═══════════════════════════════════════════════════════"""
                    messages.append({"role": "user", "content": doc_message})
                else:
                    # Extraction failed – instruct agent to use job title/description and still provide an answer
                    fallback = (
                        f"[Document {doc_name}: Text could not be extracted from this file. "
                        f"Use the JOB TITLE and JOB DESCRIPTION from the system message above as the requirements. "
                        f"Execute the task based on that and provide your complete answer. Do not refuse; infer from the job context if needed.]"
                    )
                    messages.append({"role": "user", "content": fallback})
                    logger.debug("Document %s: no extractable content – sent fallback (use job title/description)", doc_name)

            messages.append({
                "role": "user",
                "content": "═══════════════════════════════════════════════════════\nEND OF REQUIREMENT DOCUMENTS\n═══════════════════════════════════════════════════════\n\nExecute the job based on the requirement documents above and provide your complete, correct answer."
            })
        else:
            logger.debug("No documents provided - agent will work with job title and description only")
        
        # Convert conversation Q&A to messages (if any)
        if conversation:
            messages.append({
                "role": "user", 
                "content": "═══════════════════════════════════════════════════════\nCLARIFICATION QUESTIONS & ANSWERS:\n═══════════════════════════════════════════════════════\n\nThe following Q&A may provide additional context about the requirements:"
            })
            for item in conversation:
                if item.get('type') == 'question':
                    if item.get('question'):
                        messages.append({"role": "user", "content": f"Q: {item['question']}"})
                    if item.get('answer'):
                        messages.append({"role": "assistant", "content": f"A: {item['answer']}"})
                elif item.get('type') == 'analysis' and item.get('content'):
                    messages.append({"role": "assistant", "content": f"Analysis: {item['content']}"})
                elif item.get('type') == 'completion' and item.get('message'):
                    messages.append({"role": "assistant", "content": f"Summary: {item['message']}"})
            messages.append({
                "role": "user",
                "content": "═══════════════════════════════════════════════════════\nEND OF CLARIFICATION QUESTIONS & ANSWERS\n═══════════════════════════════════════════════════════"
            })
        
        # Add final task instruction
        task_instruction = "═══════════════════════════════════════════════════════\nWHAT TO DO NOW:\n═══════════════════════════════════════════════════════\n\n"
        task_instruction += "Using the requirement documents"
        if conversation:
            task_instruction += " and the Q&A above"
        task_instruction += ", provide your complete, correct answer. Execute the task; do not ask unnecessary questions. Only ask if something is strictly required and cannot be inferred.\n"
        # Inter-agent communication: pass previous agent's output (extract content, not raw API JSON)
        prev_output = input_data.get('previous_step_output')
        if prev_output is not None:
            prev_content = self._extract_agent_output_content(prev_output)
            task_instruction += "\n\n═══════════════════════════════════════════════════════\n"
            task_instruction += "INTER-AGENT COMMUNICATION (output from previous agent):\n"
            task_instruction += "The previous agent in this workflow produced the following. Use it as your input.\n"
            task_instruction += "═══════════════════════════════════════════════════════\n\n"
            task_instruction += prev_content
            task_instruction += "\n"
        messages.append({"role": "user", "content": task_instruction})
        
        # Build OpenAI-compatible payload for hired agent API
        payload = {
            "model": (getattr(agent, "llm_model", None) or "").strip() or "gpt-4o-mini",
            "messages": messages,
            "temperature": (
                getattr(agent, "temperature", None)
                if getattr(agent, "temperature", None) is not None
                else 0.7
            ),
        }
        return payload
    
    def _map_to_schema(self, input_schema: Dict[str, Any], input_data: Dict[str, Any]) -> Dict[str, Any]:
        """Map input_data to match the agent's input_schema"""
        # Simple mapping: if schema has properties, try to map them
        if isinstance(input_schema, dict) and 'properties' in input_schema:
            mapped = {}
            for key, schema_def in input_schema['properties'].items():
                # Try to find matching data
                if key in input_data:
                    mapped[key] = input_data[key]
                elif 'default' in schema_def:
                    mapped[key] = schema_def['default']
            return mapped if mapped else input_data
        
        # If no properties, return as-is
        return input_data
    
    async def _execute_plugin_agent(self, agent: Agent, input_data: Dict[str, Any]) -> Dict[str, Any]:
        """Execute agent via plugin (placeholder - would load and execute plugin)"""
        # TODO: Implement plugin execution
        # This would involve:
        # 1. Loading the plugin code
        # 2. Executing it in a sandboxed environment
        # 3. Returning the result
        raise NotImplementedError("Plugin execution not yet implemented")
    
    def _log_communication(self, from_step: WorkflowStep, to_step: WorkflowStep, data: Dict[str, Any]):
        """Log agent-to-agent communication"""
        from_agent = self.db.query(Agent).filter(Agent.id == from_step.agent_id).first()
        to_agent = self.db.query(Agent).filter(Agent.id == to_step.agent_id).first()
        
        if not from_agent or not to_agent:
            return
        
        communication = AgentCommunication(
            from_workflow_step_id=from_step.id,
            to_workflow_step_id=to_step.id,
            from_agent_id=from_agent.id,
            to_agent_id=to_agent.id,
            data_transferred=json.dumps(data),
            cost=from_agent.price_per_communication + to_agent.price_per_communication
        )
        self.db.add(communication)
        self.db.commit()
    
    def _get_available_mcp_tools(
        self,
        business_id: int,
        platform_tool_ids: Optional[list] = None,
        connection_ids: Optional[list] = None,
    ) -> list:
        """
        Return list of MCP tool descriptors for the business (tenant) for agent context.
        If platform_tool_ids/connection_ids are provided, only those tools are returned
        (per-step or per-job allowlist). Otherwise all active business tools are returned.
        """
        TOOL_TYPE_DESC = {
            "vector_db": "Vector database (semantic search)",
            "pinecone": "Pinecone vector store",
            "weaviate": "Weaviate vector store",
            "qdrant": "Qdrant vector store",
            "chroma": "Chroma vector store",
            "postgres": "PostgreSQL database",
            "mysql": "MySQL database",
            "elasticsearch": "Elasticsearch search",
            "filesystem": "File system access",
            "s3": "AWS S3 storage",
            "slack": "Slack integration",
            "github": "GitHub API",
            "notion": "Notion API",
            "rest_api": "REST API client",
        }
        tools = []
        platform_query = self.db.query(MCPToolConfig).filter(
            MCPToolConfig.user_id == business_id,
            MCPToolConfig.is_active == True,
        )
        if platform_tool_ids:
            platform_query = platform_query.filter(MCPToolConfig.id.in_(platform_tool_ids))
        platform = platform_query.order_by(MCPToolConfig.name).all()
        for t in platform:
            name = f"platform_{t.id}_{_safe_slug(t.name)}" if _safe_slug(t.name) else f"platform_{t.id}"
            desc = TOOL_TYPE_DESC.get(t.tool_type.value, t.tool_type.value)
            entry = {
                "name": name,
                "description": f"{desc}: {t.name}",
                "source": "platform",
                "platform_tool_id": t.id,
                "tool_type": t.tool_type.value,
            }
            if getattr(t, "schema_metadata", None):
                entry["schema_metadata"] = t.schema_metadata
            if getattr(t, "business_description", None):
                entry["business_description"] = t.business_description
            tools.append(entry)
        conn_query = self.db.query(MCPServerConnection).filter(
            MCPServerConnection.user_id == business_id,
            MCPServerConnection.is_active == True,
        )
        if connection_ids:
            conn_query = conn_query.filter(MCPServerConnection.id.in_(connection_ids))
        conns = conn_query.all()
        for c in conns:
            tools.append({
                "name": c.name,
                "description": f"External MCP server: {c.base_url}",
                "source": "external",
                "connection_id": c.id,
            })
        return tools

    async def _call_platform_mcp_tool(
        self, business_id: int, tool_name: str, arguments: Dict[str, Any]
    ) -> str:
        """Invoke a platform MCP tool by name; returns result content as string for tool result message."""
        base = (getattr(settings, "PLATFORM_MCP_SERVER_URL", None) or "").strip().rstrip("/")
        if not base:
            return json.dumps({"error": "Platform MCP server not configured"})
        extra_headers = {"X-MCP-Business-Id": str(business_id)}
        try:
            result = await mcp_call_tool(
                base_url=base,
                tool_name=tool_name,
                arguments=arguments,
                endpoint_path="/mcp",
                timeout=60.0,
                extra_headers=extra_headers,
            )
        except Exception as e:
            return json.dumps({"error": str(e)})
        content_list = result.get("content") or []
        texts = []
        for part in content_list:
            if isinstance(part, dict) and part.get("type") == "text":
                texts.append(part.get("text", ""))
        return "\n".join(texts) if texts else json.dumps(result)

    def _log_action(self, entity_type: str, entity_id: int, action: str, details: Dict[str, Any]):
        """Log an action to the audit log"""
        log_entry = AuditLog(
            entity_type=entity_type,
            entity_id=entity_id,
            action=action,
            details=json.dumps(details)
        )
        self.db.add(log_entry)
        self.db.commit()
