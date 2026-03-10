from sqlalchemy.orm import Session
from datetime import datetime
from typing import Dict, Any, Optional
import httpx
import json
from models.job import Job, JobStatus, WorkflowStep
from models.agent import Agent
from models.communication import AgentCommunication
from models.audit_log import AuditLog
from services.payment_processor import PaymentProcessor
from services.a2a_client import execute_via_a2a
from core.config import settings


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
                print(f"[DEBUG] ========== Executing Step {step.step_order} ==========")
                print(f"[DEBUG] Agent: {agent.name} (hired endpoint)")
                print(f"[DEBUG] Agent endpoint: {agent.api_endpoint}")
                print(f"[DEBUG] Job Title: {input_data.get('job_title', 'N/A')}")
                print(f"[DEBUG] Job Description: {input_data.get('job_description', 'N/A')[:100]}...")
                
                # Log documents
                if documents:
                    print(f"[DEBUG] ✓ Found {len(documents)} document(s) to send to agent:")
                    for i, doc in enumerate(documents):
                        content_length = len(doc.get('content', '')) if doc.get('content') else 0
                        content_preview = doc.get('content', '')[:150] if doc.get('content') else 'EMPTY'
                        print(f"[DEBUG]   Document {i+1}: {doc.get('name', 'Unknown')}")
                        print(f"[DEBUG]     - Type: {doc.get('type', 'unknown')}")
                        print(f"[DEBUG]     - Content length: {content_length} chars")
                        print(f"[DEBUG]     - Content preview: {content_preview}...")
                else:
                    print(f"[DEBUG] ✗ WARNING: No documents found in input_data!")
                
                # Log conversation
                if conversation:
                    print(f"[DEBUG] ✓ Found {len(conversation)} conversation item(s):")
                    questions = [item for item in conversation if item.get('type') == 'question']
                    answers = [item for item in conversation if item.get('type') == 'question' and item.get('answer')]
                    completions = [item for item in conversation if item.get('type') == 'completion']
                    print(f"[DEBUG]   - Questions: {len(questions)}")
                    print(f"[DEBUG]   - Answered: {len(answers)}")
                    print(f"[DEBUG]   - Completions: {len(completions)}")
                    if completions:
                        print(f"[DEBUG]   - Latest completion: {completions[-1].get('message', 'N/A')[:100]}...")
                else:
                    print(f"[DEBUG] ✗ WARNING: No conversation found in input_data!")
                
                print(f"[DEBUG] =================================================")
                
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
        """
        url = (agent.api_endpoint or "").strip()
        if not url:
            raise ValueError(
                f"A2A-enabled agent must have api_endpoint configured. "
                f"Agent '{agent.name}' (id={agent.id}) has no api_endpoint."
            )
        api_key = (agent.api_key or "").strip() or None
        print(f"[DEBUG] Executing agent '{agent.name}' via A2A: {url}")
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
        Sends formatted OpenAI-style messages so the model gets a proper prompt and
        returns the actual answer instead of echoing the context.
        """
        url = (agent.api_endpoint or "").strip()
        if not url:
            raise ValueError(
                f"Agent must have api_endpoint configured. "
                f"Agent '{agent.name}' (id={agent.id}) has no api_endpoint."
            )
        api_key = (agent.api_key or "").strip() or None
        model = (getattr(agent, "llm_model", None) or "").strip() or "gpt-4o-mini"
        print(f"[DEBUG] Executing agent '{agent.name}' via A2A adapter -> {url}")
        # Build the same formatted messages we use for direct OpenAI API so the model
        # gets system + user messages and returns a real answer, not the context echo.
        payload = self._format_for_openai(agent, input_data)
        result = await execute_via_a2a(
            adapter_url,
            input_data,
            api_key=None,
            blocking=True,
            timeout=120.0,
            adapter_metadata={
                "openai_url": url,
                "openai_api_key": api_key or "",
                "openai_model": model,
                "openai_messages": payload.get("messages", []),
            },
        )
        return result

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
        print(f"[DEBUG] Executing agent '{agent.name}' via hired endpoint: {url}")

        payload_for_log = self._truncate_payload_for_log(payload, max_content_len=1500)
        print(f"[LOG] Request payload: {json.dumps(payload_for_log, indent=2, default=str)}")

        async with httpx.AsyncClient(**client_kwargs) as client:
            response = await client.post(url, json=payload, headers=headers)

            try:
                response_body = response.text
            except Exception:
                response_body = "(unable to read response body)"
            response_for_log = response_body if len(response_body) <= 2000 else response_body[:2000] + "\n... (truncated)"
            print(f"[LOG] Response status: {response.status_code} {response.reason_phrase}")
            print(f"[LOG] Response body: {response_for_log}")

            if response.status_code >= 400:
                body = response_body if len(response_body) <= 800 else response_body[:800] + "..."
                print(f"[ERROR] Agent API returned {response.status_code}: {body}")
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
        if assigned_task and assigned_task.strip():
            system_content += "═══════════════════════════════════════════════════════\n"
            system_content += "CRITICAL: YOUR ASSIGNED TASK (multi-agent workflow)\n"
            system_content += "═══════════════════════════════════════════════════════\n\n"
            if step_order is not None and total_steps is not None and total_steps > 1:
                system_content += f"You are Agent {step_order} of {total_steps}. "
            system_content += "Perform ONLY your assigned subtask below. Do NOT perform work assigned to other agents.\n"
            system_content += "If the documents describe the full workflow, IGNORE the parts that are not your assignment.\n\n"
            system_content += f"YOUR TASK:\n{assigned_task.strip()}\n\n"
        else:
            system_content += "TASK: Execute the job from the requirements above and provide your complete, correct answer. Do not ask questions unless something is strictly required and impossible to infer.\n"
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

            print(f"[DEBUG] Formatting {len(documents)} requirement documents for API (content is inline below)")
            messages.append({
                "role": "user",
                "content": "═══════════════════════════════════════════════════════\n📋 REQUIREMENT DOCUMENTS (text below):\n═══════════════════════════════════════════════════════\n\nThe requirement document text is in the next message(s). Use it to execute the job and provide your correct answer. Do not ask unnecessary questions.\n\nDocument(s) follow:"
            })

            for i, doc in enumerate(documents):
                doc_name = doc.get('name', 'Unknown')
                doc_type = doc.get('type', 'unknown')
                doc_content = (doc.get('content') or '').strip()

                print(f"[DEBUG] Document {i+1}: {doc_name} - Content length: {len(doc_content)} chars")

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
                    print(f"[DEBUG] Document {doc_name}: no extractable content – sent fallback (use job title/description)")

            messages.append({
                "role": "user",
                "content": "═══════════════════════════════════════════════════════\nEND OF REQUIREMENT DOCUMENTS\n═══════════════════════════════════════════════════════\n\nExecute the job based on the requirement documents above and provide your complete, correct answer."
            })
        else:
            print("[DEBUG] No documents provided - agent will work with job title and description only")
        
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
