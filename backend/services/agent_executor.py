import logging
import json
import hashlib
import hmac
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional
import httpx
from sqlalchemy.orm import Session
from models.job import Job, JobStatus, WorkflowStep
from models.agent import Agent
from models.communication import AgentCommunication
from models.audit_log import AuditLog
from models.mcp_server import MCPToolConfig, MCPServerConnection
from services.payment_processor import PaymentProcessor
from services.a2a_client import execute_via_a2a
from services.mcp_client import call_tool as mcp_call_tool, list_tools as mcp_list_tools
from core.encryption import decrypt_json
from services.db_schema_introspection import format_schema_for_prompt
from services.job_file_storage import persist_file
from core.config import settings
from services.mcp_tool_input_schemas import input_schema_for_platform_tool_type
from core.artifact_contract import normalize_agent_output_for_artifact

logger = logging.getLogger(__name__)

# Tool types that fetch text from an external corpus (vector DB, PageIndex, etc.)
_RETRIEVAL_MCP_TOOL_TYPES = frozenset(
    {"pinecone", "vector_db", "weaviate", "qdrant", "chroma", "elasticsearch", "pageindex"}
)


def _sign_trusted_bootstrap_payload(
    *,
    tool_name: str,
    operation_type: str,
    schema: str,
    table: str,
    bootstrap_sql: Any,
) -> Optional[str]:
    secret = (settings.MCP_INTERNAL_SECRET or "").strip()
    if not secret:
        return None
    payload = {
        "tool_name": str(tool_name or "").strip(),
        "operation_type": str(operation_type or "").strip().lower(),
        "schema": str(schema or "").strip(),
        "table": str(table or "").strip(),
        "bootstrap_sql": bootstrap_sql,
    }
    msg = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hmac.new(secret.encode("utf-8"), msg.encode("utf-8"), hashlib.sha256).hexdigest()


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


def _get_workflow_collaboration_hint_from_job(job: Job) -> Optional[str]:
    """Get workflow_collaboration_hint from job conversation (BRD). Returns 'sequential', 'async_a2a', or None."""
    if not getattr(job, "conversation", None):
        return None
    try:
        conv = json.loads(job.conversation)
        if not isinstance(conv, list):
            return None
        for item in reversed(conv):
            hint = item.get("workflow_collaboration_hint") if isinstance(item, dict) else None
            if hint in ("sequential", "async_a2a"):
                return hint
    except (json.JSONDecodeError, TypeError):
        pass
    return None


def _apply_tool_visibility(tools: list, visibility: Optional[str]) -> list:
    """
    Apply tool_visibility so agents never receive credentials; only allowed tool metadata.
    full = full descriptors; names_only = name + short description only; none = no tools.
    """
    if visibility == "none" or not tools:
        return []
    if visibility == "names_only":
        return [
            {
                "name": t.get("name", ""),
                "description": (t.get("description") or t.get("name", ""))[:200],
                "source": t.get("source"),
                "platform_tool_id": t.get("platform_tool_id"),
                "connection_id": t.get("connection_id"),
                "tool_type": t.get("tool_type"),
                # Required for BYO routing and OpenAI parameters (read + write tool calls)
                "external_tool_name": t.get("external_tool_name"),
                "input_schema": t.get("input_schema"),
                "schema_metadata": t.get("schema_metadata"),
                "business_description": t.get("business_description"),
            }
            for t in tools
        ]
    return tools


def _parse_output_contract(raw: Optional[str]) -> Dict[str, Any]:
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return {}
    return data if isinstance(data, dict) else {}


def _parse_write_policy(contract: Dict[str, Any], write_targets_count: int) -> Dict[str, Any]:
    policy = contract.get("write_policy") if isinstance(contract, dict) else {}
    if not isinstance(policy, dict):
        policy = {}
    on_write_error = str(policy.get("on_write_error") or "fail_job").strip().lower()
    if on_write_error not in ("fail_job", "continue"):
        on_write_error = "fail_job"
    min_success_default = write_targets_count
    raw_min = policy.get("min_successful_targets", min_success_default)
    try:
        min_successful_targets = int(raw_min)
    except (TypeError, ValueError):
        min_successful_targets = min_success_default
    min_successful_targets = max(0, min(min_successful_targets, write_targets_count))
    return {
        "on_write_error": on_write_error,
        "min_successful_targets": min_successful_targets,
    }


def _input_schema_for_tool_type(tool_type: str) -> dict:
    """OpenAI-style parameters for platform MCP tools (same JSON Schema as internal tools/list)."""
    return input_schema_for_platform_tool_type(tool_type)


def _sanitize_platform_sql_tool_arguments(tool_type: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
    """Strip artifact/output-contract keys models sometimes mix into interactive Postgres/MySQL calls."""
    tt = (tool_type or "").strip().lower()
    if tt not in ("postgres", "mysql") or not isinstance(arguments, dict):
        return arguments
    out = {k: v for k, v in arguments.items() if k in ("query", "params")}
    return out


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
        # BYO MCP: use remote inputSchema so read/write arguments match the external server
        if (t.get("source") == "external") and t.get("input_schema"):
            schema = t.get("input_schema")
            if not isinstance(schema, dict):
                schema = {"type": "object", "properties": {}}
        else:
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

    async def _persist_output_artifact(self, job: Job, step: WorkflowStep, output_data: Dict[str, Any]) -> Dict[str, Any]:
        output_format = (getattr(job, "output_artifact_format", None) or "jsonl").strip().lower()
        if output_format not in ("jsonl", "json"):
            output_format = "jsonl"
        if output_format == "json":
            payload_bytes = json.dumps(output_data, ensure_ascii=False).encode("utf-8")
            filename = f"job_{job.id}_step_{step.step_order}_output.json"
        else:
            if isinstance(output_data, dict):
                records = output_data.get("records")
                if isinstance(records, list):
                    lines = [json.dumps(r, ensure_ascii=False) for r in records]
                else:
                    lines = [json.dumps(output_data, ensure_ascii=False)]
            else:
                lines = [json.dumps({"result": output_data}, ensure_ascii=False)]
            payload_bytes = ("\n".join(lines) + "\n").encode("utf-8")
            filename = f"job_{job.id}_step_{step.step_order}_output.jsonl"

        file_meta = await persist_file(filename, payload_bytes, "application/json", job_id=job.id)
        return {
            "artifact_id": str(uuid.uuid4()),
            "storage": file_meta.get("storage", "s3"),
            "bucket": file_meta.get("bucket"),
            "key": file_meta.get("key"),
            "path": file_meta.get("path"),
            "format": output_format,
            "size_bytes": int(file_meta.get("size", len(payload_bytes))),
            "created_at": datetime.utcnow().isoformat(),
        }

    async def _trigger_platform_write(self, *, business_id: int, write_spec: Dict[str, Any], artifact_ref: Dict[str, Any], step: WorkflowStep) -> Dict[str, Any]:
        tool_name = str(write_spec.get("tool_name", "")).strip()
        if not tool_name:
            raise ValueError("output_contract.write_targets[].tool_name is required for platform mode")
        operation_type = str(write_spec.get("operation_type", "upsert")).strip().lower()
        write_mode = str(write_spec.get("write_mode", "upsert")).strip().lower()
        runtime_target = dict(write_spec.get("target") or {})
        bootstrap_sql = runtime_target.pop("bootstrap_sql", None)
        arguments = {
            "artifact_ref": {
                "storage": artifact_ref.get("storage"),
                "bucket": artifact_ref.get("bucket"),
                "path": artifact_ref.get("key") or artifact_ref.get("path"),
                "format": artifact_ref.get("format"),
            },
            "target": runtime_target,
            "operation_type": operation_type,
            "write_mode": write_mode,
            "merge_keys": write_spec.get("merge_keys") or [],
            "idempotency_key": f"job-{step.job_id}-step-{step.id}-{artifact_ref.get('artifact_id')}",
            "options": {"step_order": step.step_order, **(write_spec.get("options") or {})},
            "tool_name": tool_name,
        }
        if bootstrap_sql is not None:
            schema = str(runtime_target.get("schema") or runtime_target.get("schema_name") or "").strip()
            table = str(runtime_target.get("table") or "").strip()
            sig = _sign_trusted_bootstrap_payload(
                tool_name=tool_name,
                operation_type=operation_type,
                schema=schema,
                table=table,
                bootstrap_sql=bootstrap_sql,
            )
            if sig:
                arguments["trusted_bootstrap"] = {
                    "bootstrap_sql": bootstrap_sql,
                    "sig": sig,
                }
        timeout = float(write_spec.get("timeout_seconds") or getattr(settings, "MCP_TOOL_DEFAULT_TIMEOUT_SECONDS", 60.0))
        return await mcp_call_tool(
            base_url=settings.PLATFORM_MCP_SERVER_URL.rstrip("/"),
            tool_name=tool_name,
            arguments=arguments,
            endpoint_path="/mcp",
            extra_headers={"X-MCP-Business-Id": str(business_id)},
            timeout=timeout,
        )
    
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
            job.execution_token = None
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

                # Strict BRD scope guard: if step is document-restricted, ensure only allowed docs are present.
                if input_data.get("document_scope_restricted"):
                    allowed_ids_raw = input_data.get("allowed_document_ids") or []
                    allowed_ids = {str(x) for x in allowed_ids_raw if str(x).strip()}
                    docs_in_payload = input_data.get("documents") or []
                    payload_doc_ids = {str((d or {}).get("id")) for d in docs_in_payload if isinstance(d, dict) and (d or {}).get("id")}
                    if not allowed_ids:
                        raise ValueError("Document scope restricted but allowed_document_ids is empty")
                    if payload_doc_ids and not payload_doc_ids.issubset(allowed_ids):
                        raise ValueError(
                            f"Policy violation: step {step.step_order} received out-of-scope BRDs. "
                            f"allowed={sorted(allowed_ids)} payload={sorted(payload_doc_ids)}"
                        )

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

                available_mcp_tools = await self._get_available_mcp_tools_async(
                    job.business_id,
                    platform_tool_ids=effective_platform if effective_platform is not None else None,
                    connection_ids=effective_conn if effective_conn is not None else None,
                )
                # Hybrid A2A: restrict what tool info agents see (credentials never shared)
                tool_visibility = getattr(step, "tool_visibility", None) or getattr(job, "tool_visibility", None) or "full"
                available_mcp_tools = _apply_tool_visibility(available_mcp_tools or [], tool_visibility)
                if available_mcp_tools:
                    input_data["available_mcp_tools"] = available_mcp_tools
                    input_data["business_id"] = job.business_id
                    if step.step_order == 1:
                        self._log_action("job", job_id, "mcp_tool_discovery", {
                            "business_id": job.business_id,
                            "tool_count": len(available_mcp_tools),
                            "tool_names": [t.get("name") for t in available_mcp_tools[:20]],
                        })

                # Hybrid A2A: peer context for async_a2a — agents can call each other; no tools/credentials shared
                collaboration_hint = _get_workflow_collaboration_hint_from_job(job)
                if collaboration_hint == "async_a2a":
                    peer_agents = self._get_peer_agents_for_step(workflow_steps, step, agent)
                    if peer_agents:
                        input_data["peer_agents"] = peer_agents
                        if step.step_order == 1:
                            self._log_action("job", job_id, "peer_a2a_context", {
                                "peer_count": len(peer_agents),
                                "peer_ids": [p["agent_id"] for p in peer_agents],
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
                output_data = None
                artifact_ref = None
                contract = _parse_output_contract(getattr(job, "output_contract", None))
                write_mode = (getattr(job, "write_execution_mode", None) or "platform").strip().lower()
                write_targets: List[Dict[str, Any]] = contract.get("write_targets") if isinstance(contract, dict) else []
                write_policy = _parse_write_policy(contract, len(write_targets) if isinstance(write_targets, list) else 0)
                write_results: List[Dict[str, Any]] = []
                try:
                    output_data = await self._execute_agent(agent, input_data)
                    output_data = normalize_agent_output_for_artifact(output_data)
                    # ui_only: keep results in step.output_data (DB) for the UI only — no S3/local artifact file, no contract writes.
                    if write_mode == "ui_only":
                        output_format = (getattr(job, "output_artifact_format", None) or "jsonl").strip().lower()
                        if output_format not in ("jsonl", "json"):
                            output_format = "jsonl"
                        artifact_ref = {
                            "artifact_id": str(uuid.uuid4()),
                            "storage": "inline",
                            "inline_only": True,
                            "format": output_format,
                            "created_at": datetime.utcnow().isoformat(),
                        }
                    else:
                        artifact_ref = await self._persist_output_artifact(job, step, output_data)
                    if write_mode == "platform" and isinstance(write_targets, list) and write_targets:
                        successful_writes = 0
                        for target in write_targets:
                            if not isinstance(target, dict):
                                continue
                            tool_name = str(target.get("tool_name", "")).strip() or None
                            try:
                                write_result = await self._trigger_platform_write(
                                    business_id=job.business_id,
                                    write_spec=target,
                                    artifact_ref=artifact_ref,
                                    step=step,
                                )
                                write_results.append({
                                    "tool_name": tool_name,
                                    "status": "success",
                                    "result": write_result,
                                })
                                successful_writes += 1
                            except Exception as target_error:
                                write_results.append({
                                    "tool_name": tool_name,
                                    "status": "failed",
                                    "error": str(target_error),
                                })
                                # Policy-controlled behavior: fail immediately or continue collecting target outcomes.
                                if write_policy.get("on_write_error") == "fail_job":
                                    raise
                        if successful_writes < int(write_policy.get("min_successful_targets", 0)):
                            raise ValueError(
                                "Write policy violation: "
                                f"successful_targets={successful_writes} < "
                                f"min_successful_targets={write_policy.get('min_successful_targets')}"
                            )

                    # Update step with output + persisted artifact reference contract
                    step.output_data = json.dumps({
                        "agent_output": output_data,
                        "artifact_ref": artifact_ref,
                        "write_execution_mode": write_mode,
                        "write_policy": write_policy,
                        "write_results": write_results,
                    })
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
                    # Persist error with best-effort context for postmortem debugging.
                    step.output_data = json.dumps({
                        "error": error_msg,
                        "agent_output": output_data,
                        "artifact_ref": artifact_ref,
                        "write_execution_mode": write_mode,
                        "write_policy": write_policy,
                        "write_results": write_results,
                    })
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
            job.execution_token = None
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
            logger.exception("Job execution failed job_id=%s", job_id)
            job.status = JobStatus.FAILED
            job.execution_token = None
            error_message = type(e).__name__
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
        # One model call per round. A workflow with N tool rounds needs N+1 calls (tools then final answer).
        # A fixed `for range(5)` could end right after a tool round with no follow-up call, leaving `content` "".
        max_agent_rounds = 20
        round_idx = 0
        content = ""

        while round_idx < max_agent_rounds:
            round_idx += 1
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
            routing = {t.get("name"): t for t in (available_mcp_tools or []) if t.get("name")}
            for tc in tool_calls:
                tc_id = tc.get("id")
                fn = tc.get("function") or {}
                tool_name = fn.get("name")
                args_raw = fn.get("arguments") or "{}"
                try:
                    args = json.loads(args_raw) if isinstance(args_raw, str) else args_raw
                except (json.JSONDecodeError, TypeError):
                    args = {}
                tool_result = await self._invoke_mcp_tool(business_id, tool_name, args, routing)
                messages.append({"role": "tool", "tool_call_id": tc_id, "content": tool_result})
            logger.info("MCP tool round %s: %s tool call(s), continuing agent loop", round_idx, len(tool_calls))

        if not (content or "").strip():
            for msg in reversed(messages):
                if isinstance(msg, dict) and msg.get("role") == "tool":
                    tr = msg.get("content")
                    if tr:
                        content = (
                            "The model did not return a final assistant message after tools ran. "
                            "Last tool result:\n\n" + str(tr)
                        )
                        break

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
        assigned_doc_ids = input_data.get("allowed_document_ids") or []
        assigned_doc_names = input_data.get("assigned_document_names") or []
        doc_scope_restricted = bool(input_data.get("document_scope_restricted"))
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
            if doc_scope_restricted:
                system_content += "DOCUMENT SCOPE POLICY (strict):\n"
                if assigned_doc_ids:
                    system_content += f"- You are allowed to use ONLY these BRD IDs: {', '.join([str(x) for x in assigned_doc_ids])}\n"
                if assigned_doc_names:
                    system_content += f"- Allowed BRD names: {', '.join([str(x) for x in assigned_doc_names])}\n"
                system_content += "- Do NOT use requirements from any other BRD.\n\n"
        else:
            if has_previous_output and step_order is not None and total_steps is not None and total_steps > 1:
                system_content += "SEQUENTIAL WORKFLOW: You receive the previous agent's output below. Use it as your input and continue the pipeline.\n\n"
            system_content += "TASK: Execute the job from the requirements above and provide your complete, correct answer. Do not ask questions unless something is strictly required and impossible to infer.\n"
        # Hybrid A2A: peer agents (endpoints only; no credentials or tool lists shared)
        peer_agents = input_data.get("peer_agents") or []
        if peer_agents:
            system_content += "\n═══════════════════════════════════════════════════════\n"
            system_content += "PEER AGENTS (optional direct A2A)\n"
            system_content += "═══════════════════════════════════════════════════════\n\n"
            system_content += "You may call these agents directly via the A2A protocol (SendMessage to their endpoint). "
            system_content += "Only endpoint URLs are provided; no API keys or tool information are shared.\n\n"
            for p in peer_agents:
                system_content += f"  - {p.get('name', '')} (step {p.get('step_order', '')}): {p.get('a2a_endpoint', '')}\n"
            system_content += "\n"
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
            retrieval_tools = [
                t
                for t in available_mcp_tools
                if str(t.get("tool_type", "")).lower() in _RETRIEVAL_MCP_TOOL_TYPES
            ]
            if len(retrieval_tools) > 1:
                system_content += (
                    "CRITICAL — Multiple search/retrieval tools are enabled (e.g. a vector store and PageIndex). "
                    "They are different corpora. Use ONLY the tool that matches the assigned task and job wording "
                    "(e.g. Pinecone for the tenant’s vector index). Do not call PageIndex or another retrieval API "
                    "as a substitute for a Pinecone query unless the task explicitly names that source. "
                    "Never present documents from one backend as results for another.\n\n"
                )
            if any(str(t.get("tool_type", "")).lower() in ("postgres", "mysql") for t in available_mcp_tools):
                system_content += (
                    "CRITICAL — PostgreSQL/MySQL platform tools: call with ONLY the arguments allowed by the tool schema: "
                    "`query` (required) and optionally `params`. "
                    "Do NOT send write_mode, operation_type, target, artifact_ref, merge_keys, or idempotency_key with "
                    "these tools — those fields belong to the job output contract (e.g. MinIO/Snowflake artifact writes), "
                    "not to interactive SQL. "
                    "For a row count use only: {\"query\": \"SELECT COUNT(*) FROM your_table\"}.\n\n"
                )
            if any(
                str(t.get("tool_type", "")).lower() in ("s3", "minio", "ceph", "azure_blob", "gcs")
                for t in available_mcp_tools
            ):
                system_content += (
                    "CRITICAL — S3 / MinIO / object storage tools: for action put or write you MUST include "
                    "`body` or `content` with the full file payload as a string in the SAME tool call (e.g. JSONL text). "
                    "Calling put/write with only `key` will fail. For listing or reading, use action list or get.\n\n"
                )
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
    
    def _get_peer_agents_for_step(
        self, workflow_steps: list, current_step: WorkflowStep, current_agent: Agent
    ) -> list:
        """
        For hybrid A2A (async_a2a): return other agents in this workflow that support A2A.
        Only endpoint URL and identity are shared; no API keys, no tool lists, no credentials.
        """
        peer_list = []
        for s in workflow_steps:
            if s.id == current_step.id or s.agent_id == current_agent.id:
                continue
            peer_agent = self.db.query(Agent).filter(Agent.id == s.agent_id).first()
            if not peer_agent or not getattr(peer_agent, "a2a_enabled", False):
                continue
            endpoint = (getattr(peer_agent, "api_endpoint", None) or "").strip()
            if not endpoint:
                continue
            peer_list.append({
                "agent_id": peer_agent.id,
                "name": peer_agent.name or "",
                "a2a_endpoint": endpoint,
                "step_order": s.step_order,
            })
        return peer_list

    async def _get_available_mcp_tools_async(
        self,
        business_id: int,
        platform_tool_ids: Optional[list] = None,
        connection_ids: Optional[list] = None,
    ) -> list:
        """
        Return list of MCP tool descriptors for the business (tenant) for agent context.
        If platform_tool_ids/connection_ids are provided, only those tools are returned
        (per-step or per-job allowlist). Otherwise all active business tools are returned.
        BYO connections: calls tools/list on each server and registers one OpenAI function per remote tool
        (name byo_{connection_id}_{slug}) so tools/call can route read and write operations correctly.
        """
        TOOL_TYPE_DESC = {
            "vector_db": "Vector database (semantic search)",
            "pinecone": "Pinecone vector store",
            "weaviate": "Weaviate vector store",
            "qdrant": "Qdrant vector store",
            "chroma": "Chroma vector store",
            "postgres": "PostgreSQL database",
            "mysql": "MySQL database",
            "sqlserver": "SQL Server database",
            "snowflake": "Snowflake data warehouse",
            "databricks": "Databricks SQL warehouse",
            "bigquery": "Google BigQuery warehouse",
            "elasticsearch": "Elasticsearch search",
            "pageindex": "PageIndex (vectorless document retrieval)",
            "filesystem": "File system access",
            "s3": "AWS S3 storage",
            "minio": "MinIO object storage",
            "ceph": "Ceph object storage",
            "azure_blob": "Azure Blob storage",
            "gcs": "Google Cloud Storage",
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
        # [] must mean "no platform tools" — `if platform_tool_ids` wrongly treated [] as "no filter" (all tools).
        if platform_tool_ids is not None:
            if len(platform_tool_ids) == 0:
                platform = []
            else:
                platform = (
                    platform_query.filter(MCPToolConfig.id.in_(platform_tool_ids))
                    .order_by(MCPToolConfig.name)
                    .all()
                )
        else:
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
        if connection_ids is not None:
            if len(connection_ids) == 0:
                conns = []
            else:
                conns = (
                    conn_query.filter(MCPServerConnection.id.in_(connection_ids))
                    .order_by(MCPServerConnection.name)
                    .all()
                )
        else:
            conns = conn_query.order_by(MCPServerConnection.name).all()
        for c in conns:
            creds = None
            if c.encrypted_credentials:
                try:
                    creds = decrypt_json(c.encrypted_credentials)
                except Exception:
                    creds = None
            base_url = (c.base_url or "").strip().rstrip("/")
            endpoint_path = (c.endpoint_path or "/mcp").strip()
            if not endpoint_path.startswith("/"):
                endpoint_path = "/" + endpoint_path
            remote_tools: list = []
            try:
                res = await mcp_list_tools(
                    base_url=base_url,
                    endpoint_path=endpoint_path,
                    auth_type=c.auth_type or "none",
                    credentials=creds,
                    timeout=20.0,
                )
                remote_tools = res.get("tools") or []
            except Exception as e:
                logger.warning(
                    "BYO MCP tools/list failed for connection id=%s name=%s url=%s: %s",
                    c.id, c.name, base_url, e,
                )
                continue
            used_slugs: set = set()
            for idx, rt in enumerate(remote_tools):
                raw_name = (rt.get("name") or "").strip()
                if not raw_name:
                    continue
                base_slug = _safe_slug(raw_name) or f"tool_{idx}"
                slug = base_slug
                suffix = 0
                while slug in used_slugs:
                    suffix += 1
                    slug = f"{base_slug}_{suffix}"
                used_slugs.add(slug)
                fn_name = f"byo_{c.id}_{slug}"
                desc = (rt.get("description") or raw_name).strip()
                if len(desc) > 1800:
                    desc = desc[:1800] + "…"
                schema = rt.get("inputSchema") or rt.get("input_schema")
                if not isinstance(schema, dict):
                    schema = {"type": "object", "properties": {}}
                tools.append({
                    "name": fn_name,
                    "description": f"[BYO MCP: {c.name}] {desc}",
                    "source": "external",
                    "connection_id": c.id,
                    "external_tool_name": raw_name,
                    "input_schema": schema,
                })
        return tools

    @staticmethod
    def _mcp_tool_result_to_text(result: Dict[str, Any]) -> str:
        content_list = result.get("content") or []
        texts = []
        for part in content_list:
            if isinstance(part, dict) and part.get("type") == "text":
                texts.append(part.get("text", ""))
        return "\n".join(texts) if texts else json.dumps(result)

    async def _invoke_mcp_tool(
        self,
        business_id: int,
        tool_name: str,
        arguments: Dict[str, Any],
        routing: Dict[str, Dict[str, Any]],
    ) -> str:
        """
        Route tool call to platform MCP server or BYO (external) MCP connection.
        BYO tools use names from tools/list (byo_{connection_id}_{slug}) and forward to tools/call on that server.
        """
        meta = routing.get(tool_name) if routing else None
        if not meta:
            return json.dumps({"error": f"Unknown tool {tool_name!r}. Not in this job's MCP tool list."})
        if meta.get("source") == "external":
            conn = self.db.query(MCPServerConnection).filter(
                MCPServerConnection.id == meta["connection_id"],
                MCPServerConnection.user_id == business_id,
                MCPServerConnection.is_active == True,
            ).first()
            if not conn:
                return json.dumps({"error": "MCP connection not found or not allowed for this job."})
            ext_name = (meta.get("external_tool_name") or "").strip()
            if not ext_name:
                return json.dumps({"error": "External MCP tool name missing in registry."})
            creds = decrypt_json(conn.encrypted_credentials) if conn.encrypted_credentials else None
            timeout = float(getattr(settings, "MCP_TOOL_DEFAULT_TIMEOUT_SECONDS", 60.0))
            try:
                result = await mcp_call_tool(
                    base_url=conn.base_url.rstrip("/"),
                    tool_name=ext_name,
                    arguments=arguments or {},
                    endpoint_path=conn.endpoint_path or "/mcp",
                    auth_type=conn.auth_type or "none",
                    credentials=creds,
                    timeout=timeout,
                )
            except Exception as e:
                logger.exception("BYO MCP tools/call failed connection_id=%s tool=%s", meta.get("connection_id"), ext_name)
                return json.dumps({"error": type(e).__name__})
            return self._mcp_tool_result_to_text(result)
        args = arguments if isinstance(arguments, dict) else {}
        if meta.get("source") == "platform":
            args = _sanitize_platform_sql_tool_arguments(str(meta.get("tool_type") or ""), args)
        return await self._call_platform_mcp_tool(business_id, tool_name, args)

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
            logger.exception("Platform MCP tools/call failed tool_name=%s", tool_name)
            return json.dumps({"error": type(e).__name__})
        return self._mcp_tool_result_to_text(result)

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
