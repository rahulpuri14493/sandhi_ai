import logging
import asyncio
import json
from typing import List, Dict, Any, Optional
from sqlalchemy.orm import Session
from sqlalchemy import or_
from models.job import Job, WorkflowStep
from models.agent import Agent
from models.transaction import Earnings
from models.communication import AgentCommunication
from schemas.job import WorkflowPreview, WorkflowStepResponse
from services.payment_processor import PaymentProcessor
from services.task_splitter import split_job_for_agents

logger = logging.getLogger(__name__)


def _normalized_job_output_settings(job: Job) -> tuple[str, str]:
    write_mode_raw = getattr(job, "write_execution_mode", "platform")
    write_mode = write_mode_raw if isinstance(write_mode_raw, str) else "platform"
    write_mode = (write_mode or "platform").strip().lower()
    if write_mode not in ("platform", "agent"):
        write_mode = "platform"

    artifact_format_raw = getattr(job, "output_artifact_format", "jsonl")
    artifact_format = artifact_format_raw if isinstance(artifact_format_raw, str) else "jsonl"
    artifact_format = (artifact_format or "jsonl").strip().lower()
    if artifact_format not in ("jsonl", "json"):
        artifact_format = "jsonl"
    return write_mode, artifact_format


class WorkflowBuilder:
    def __init__(self, db: Session):
        self.db = db
        self.payment_processor = PaymentProcessor(db)

    async def load_job_documents_content_async(self, job: Job) -> List[Dict[str, Any]]:
        """Read BRD / job file text for task splitting and tool suggestion (same as auto-split)."""
        documents_content: List[Dict[str, Any]] = []
        if not job.files:
            return documents_content
        try:
            from services.document_analyzer import DocumentAnalyzer

            files_data = json.loads(job.files)
            analyzer = DocumentAnalyzer()
            for file_info in files_data:
                if file_info.get("path") or (
                    file_info.get("storage") == "s3" and file_info.get("bucket") and file_info.get("key")
                ):
                    try:
                        content = await analyzer.read_file_info(file_info)
                        if not content or not content.strip():
                            logger.warning(
                                "Document %s has empty content - skipping (documents are optional)",
                                file_info.get("name"),
                            )
                            continue
                        doc_id = str(file_info.get("id") or f"BRD{len(documents_content) + 1}")
                        documents_content.append(
                            {
                                "id": doc_id,
                                "name": file_info.get("name", "Unknown"),
                                "type": file_info.get("type", "unknown"),
                                "content": content,
                            }
                        )
                    except Exception as e:
                        logger.warning(
                            "Failed to read document %s: %s - skipping (documents are optional)",
                            file_info.get("name"),
                            e,
                        )
                        continue
                else:
                    logger.warning("Document %s has no readable source metadata", file_info.get("name"))
        except (json.JSONDecodeError, TypeError, Exception) as e:
            logger.warning("Failed to parse job.files: %s - Continuing without documents", e)
        return documents_content

    def load_job_documents_content(self, job: Job) -> List[Dict[str, Any]]:
        """Sync wrapper for tests and non-async code paths (runs the async loader in a new event loop)."""
        return asyncio.run(self.load_job_documents_content_async(job))

    def _get_workflow_collaboration_hint(self, job: Job) -> Optional[str]:
        """Get workflow_collaboration_hint from job conversation (BRD/analyze-documents). Returns 'sequential', 'async_a2a', or None."""
        if not job.conversation:
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

    async def auto_split_workflow_async(
        self,
        job_id: int,
        agent_ids: List[int],
        workflow_mode: Optional[str] = None,
        step_tools: Optional[List[Dict[str, Any]]] = None,
        tool_visibility: Optional[str] = None,
    ) -> WorkflowPreview:
        """Automatically split a job across selected agents. workflow_mode: 'independent' | 'sequential' | None.
        step_tools: optional list of {agent_index, allowed_platform_tool_ids, allowed_connection_ids, tool_visibility} per step.
        tool_visibility: job-level full | names_only | none (restricts what tool info agents see; credentials never shared)."""
        job = self.db.query(Job).filter(Job.id == job_id).first()
        if not job:
            raise ValueError("Job not found")
        if tool_visibility is not None:
            job.tool_visibility = tool_visibility
            self.db.commit()

        # Get agents
        agents = self.db.query(Agent).filter(Agent.id.in_(agent_ids)).all()
        if len(agents) != len(agent_ids):
            raise ValueError("Some agents not found")
        
        # Parse conversation data from job
        conversation_data = None
        if job.conversation:
            try:
                conversation_data = json.loads(job.conversation)
            except (json.JSONDecodeError, TypeError):
                pass

        documents_content = await self.load_job_documents_content_async(job)

        # Log document status
        if documents_content:
            logger.debug("%s document(s) will be included as additional information", len(documents_content))
        else:
            logger.debug("No documents provided - agent will work with job title and description only")
        
        # Prepare base input data with job context, Q&A conversation, and documents
        write_mode, artifact_format = _normalized_job_output_settings(job)
        base_input_data = {
            "job_title": job.title,
            "job_description": job.description,
            "conversation": conversation_data or [],  # Include Q&A conversation
            "documents": documents_content,  # Include document content
            "write_execution_mode": write_mode,
            "output_artifact_format": artifact_format,
        }
        
        # Split job into subtasks per agent (generalized, uses first agent's API when available)
        task_assignments = []
        if agents:
            splitter = agents[0] if (agents[0].api_endpoint and (agents[0].api_endpoint or "").strip()) else None
            try:
                if splitter:
                    task_assignments = await split_job_for_agents(
                        job_title=job.title,
                        job_description=job.description or "",
                        documents_content=documents_content,
                        conversation_data=conversation_data,
                        agents=agents,
                        splitter_agent=splitter,
                    )
            except Exception as e:
                logger.warning("Task split failed: %s, using fallback", e)
            if not task_assignments:
                task_assignments = [
                    {"agent_index": i, "task": f"Execute the job. You are agent {i+1} of {len(agents)}. {job.description or job.title}"}
                    for i in range(len(agents))
                ]
        
        # Independent vs sequential: from request override or BRD/conversation hint
        if workflow_mode == "independent":
            steps_independent = True
        elif workflow_mode == "sequential":
            steps_independent = False
        else:
            hint = self._get_workflow_collaboration_hint(job)
            steps_independent = hint == "async_a2a"
        logger.debug("Workflow mode: %s (workflow_mode=%r, BRD hint used when None)", 'independent' if steps_independent else 'sequential', workflow_mode)
        
        # Unlink earnings and delete dependent rows, then clear existing workflow steps
        step_ids = [s.id for s in self.db.query(WorkflowStep.id).filter(WorkflowStep.job_id == job_id).all()]
        if step_ids:
            self.db.query(Earnings).filter(Earnings.workflow_step_id.in_(step_ids)).update(
                {Earnings.workflow_step_id: None}, synchronize_session=False
            )
            comm_filter = or_(
                AgentCommunication.from_workflow_step_id.in_(step_ids),
                AgentCommunication.to_workflow_step_id.in_(step_ids),
            )
            comm_ids = [r.id for r in self.db.query(AgentCommunication.id).filter(comm_filter).all()]
            if comm_ids:
                self.db.query(Earnings).filter(Earnings.communication_id.in_(comm_ids)).update(
                    {Earnings.communication_id: None}, synchronize_session=False
                )
                self.db.query(AgentCommunication).filter(comm_filter).delete(synchronize_session=False)
        self.db.query(WorkflowStep).filter(WorkflowStep.job_id == job_id).delete(synchronize_session=False)
        
        # When no step_tools provided (e.g. "From BRD/document"): assign job-level tools to every step so selection is saved and visible when job is opened
        job_platform_ids = None
        job_conn_ids = None
        if not step_tools:
            if getattr(job, "allowed_platform_tool_ids", None):
                try:
                    parsed = json.loads(job.allowed_platform_tool_ids)
                    if isinstance(parsed, list) and len(parsed) > 0:
                        job_platform_ids = [int(x) for x in parsed if x is not None]
                except (TypeError, ValueError, json.JSONDecodeError):
                    pass
            if getattr(job, "allowed_connection_ids", None):
                try:
                    parsed = json.loads(job.allowed_connection_ids)
                    if isinstance(parsed, list) and len(parsed) > 0:
                        job_conn_ids = [int(x) for x in parsed if x is not None]
                except (TypeError, ValueError, json.JSONDecodeError):
                    pass
        
        # Create workflow steps - each agent gets assigned task + base context
        steps = []
        for idx, agent in enumerate(agents):
            assigned_task = ""
            assigned_document_ids = None
            for ta in task_assignments:
                if ta.get("agent_index") == idx:
                    assigned_task = ta.get("task", "")
                    ad = ta.get("assigned_document_ids")
                    if isinstance(ad, list):
                        assigned_document_ids = [str(x) for x in ad if str(x).strip()]
                    break
            if not assigned_task:
                assigned_task = f"Execute the job. You are agent {idx+1} of {len(agents)}. {job.description or job.title}"
            
            # Step 1 never receives previous output; step 2+ depend on previous only in sequential mode
            depends_on_previous = False if idx == 0 else (not steps_independent)
            
            # Each agent gets base context + their specific task assignment
            docs_for_step = documents_content
            document_scope_restricted = False
            assigned_document_names = []
            if assigned_document_ids:
                allowed_set = set(assigned_document_ids)
                filtered_docs = [d for d in documents_content if str(d.get("id")) in allowed_set]
                if filtered_docs:
                    docs_for_step = filtered_docs
                    document_scope_restricted = True
                    assigned_document_names = [str(d.get("name", "")) for d in filtered_docs if d.get("name")]

            step_input_data = {
                **base_input_data,
                "step_order": idx + 1,
                "total_steps": len(agents),
                "agent_name": agent.name,
                "agent_description": agent.description,
                "assigned_task": assigned_task,
                "documents": docs_for_step,
                "allowed_document_ids": assigned_document_ids,
                "assigned_document_names": assigned_document_names,
                "document_scope_restricted": document_scope_restricted,
                "previous_agents_outputs": []  # Filled by executor when passing previous outputs
            }
            
            # Validate step input data includes documents and conversation
            step_docs = step_input_data.get('documents', [])
            step_conv = step_input_data.get('conversation', [])
            logger.debug("Step %s for agent '%s': depends_on_previous=%s documents=%s conversation_items=%s", idx + 1, agent.name, depends_on_previous, len(step_docs), len(step_conv))
            
            step_platform = step_conn = step_tool_visibility = None
            if step_tools:
                for st in step_tools:
                    if st.get("agent_index") == idx:
                        step_platform = st.get("allowed_platform_tool_ids")
                        step_conn = st.get("allowed_connection_ids")
                        step_tool_visibility = st.get("tool_visibility")
                        break
            else:
                # Auto-assign job-level tools to this step (From BRD/document: tools selection by system, saved for completed job view)
                if job_platform_ids is not None:
                    step_platform = job_platform_ids
                if job_conn_ids is not None:
                    step_conn = job_conn_ids
            step_visibility = step_tool_visibility if step_tool_visibility is not None else (tool_visibility or getattr(job, "tool_visibility", None))
            step = WorkflowStep(
                job_id=job_id,
                agent_id=agent.id,
                step_order=idx + 1,
                input_data=json.dumps(step_input_data),
                status="pending",
                depends_on_previous=depends_on_previous,
                allowed_platform_tool_ids=json.dumps(step_platform) if step_platform else None,
                allowed_connection_ids=json.dumps(step_conn) if step_conn else None,
                tool_visibility=step_visibility,
            )
            self.db.add(step)
            steps.append(step)
        
        self.db.commit()
        
        # Calculate costs
        preview = self.payment_processor.calculate_job_cost(job_id)
        return preview

    def auto_split_workflow(
        self,
        job_id: int,
        agent_ids: List[int],
        workflow_mode: Optional[str] = None,
        step_tools: Optional[List[Dict[str, Any]]] = None,
        tool_visibility: Optional[str] = None,
    ) -> WorkflowPreview:
        """Sync wrapper for tests and scripts (uses asyncio.run). Prefer auto_split_workflow_async from async routes."""
        return asyncio.run(
            self.auto_split_workflow_async(
                job_id,
                agent_ids,
                workflow_mode=workflow_mode,
                step_tools=step_tools,
                tool_visibility=tool_visibility,
            )
        )
    
    async def create_manual_workflow_async(self, job_id: int, workflow_steps: List[Dict[str, Any]]) -> WorkflowPreview:
        """Create a manual workflow from user-specified steps"""
        job = self.db.query(Job).filter(Job.id == job_id).first()
        if not job:
            raise ValueError("Job not found")
        
        # Parse conversation data from job
        conversation_data = None
        if job.conversation:
            try:
                conversation_data = json.loads(job.conversation)
            except (json.JSONDecodeError, TypeError):
                pass

        documents_content = await self.load_job_documents_content_async(job)

        # Log document status
        if documents_content:
            logger.debug("%s document(s) will be included as additional information", len(documents_content))
        else:
            logger.debug("No documents provided - agent will work with job title and description only")
        
        # Prepare base input data with job context, Q&A conversation, and documents
        logger.debug("Building manual workflow for job %s title=%s conversation_items=%s documents=%s", job_id, job.title, len(conversation_data) if conversation_data else 0, len(documents_content))
        
        write_mode, artifact_format = _normalized_job_output_settings(job)
        base_input_data = {
            "job_title": job.title,
            "job_description": job.description,
            "conversation": conversation_data or [],  # Include Q&A conversation
            "documents": documents_content,  # Include document content
            "write_execution_mode": write_mode,
            "output_artifact_format": artifact_format,
        }
        
        # Validate that documents and conversation are included
        if not documents_content:
            logger.warning("No document content found for job %s", job_id)
        else:
            total_content_length = sum(len(doc.get('content', '')) for doc in documents_content)
            logger.debug("Total document content length: %s characters", total_content_length)
        
        if not conversation_data:
            logger.warning("No conversation data found for job %s", job_id)
        else:
            logger.debug("Conversation includes %s questions and %s completion messages", len([item for item in conversation_data if item.get('type') == 'question']), len([item for item in conversation_data if item.get('type') == 'completion']))
        
        # Unlink earnings and delete dependent rows, then clear existing workflow steps
        step_ids = [s.id for s in self.db.query(WorkflowStep.id).filter(WorkflowStep.job_id == job_id).all()]
        if step_ids:
            self.db.query(Earnings).filter(Earnings.workflow_step_id.in_(step_ids)).update(
                {Earnings.workflow_step_id: None}, synchronize_session=False
            )
            comm_filter = or_(
                AgentCommunication.from_workflow_step_id.in_(step_ids),
                AgentCommunication.to_workflow_step_id.in_(step_ids),
            )
            comm_ids = [r.id for r in self.db.query(AgentCommunication.id).filter(comm_filter).all()]
            if comm_ids:
                self.db.query(Earnings).filter(Earnings.communication_id.in_(comm_ids)).update(
                    {Earnings.communication_id: None}, synchronize_session=False
                )
                self.db.query(AgentCommunication).filter(comm_filter).delete(synchronize_session=False)
        self.db.query(WorkflowStep).filter(WorkflowStep.job_id == job_id).delete(synchronize_session=False)
        
        # Create workflow steps
        for step_data in workflow_steps:
            agent_id = step_data.get("agent_id")
            step_order = step_data.get("step_order")
            custom_input_data = step_data.get("input_data")
            
            # Verify agent exists
            agent = self.db.query(Agent).filter(Agent.id == agent_id).first()
            if not agent:
                raise ValueError(f"Agent {agent_id} not found")
            
            # Merge base input data (job context + conversation + documents) with custom input data
            step_input_data = {
                **base_input_data,
                "step_order": step_order,
                "agent_name": agent.name,
                "agent_description": agent.description
            }
            
            # Validate step input data includes documents and conversation
            step_docs = step_input_data.get('documents', [])
            step_conv = step_input_data.get('conversation', [])
            logger.debug("Manual workflow step %s for agent '%s': documents=%s conversation_items=%s", step_order, agent.name, len(step_docs), len(step_conv))
            
            # If custom input data is provided, merge it
            if custom_input_data:
                if isinstance(custom_input_data, dict):
                    step_input_data.update(custom_input_data)
                else:
                    try:
                        custom_data = json.loads(custom_input_data) if isinstance(custom_input_data, str) else custom_input_data
                        if isinstance(custom_data, dict):
                            step_input_data.update(custom_data)
                    except (json.JSONDecodeError, TypeError):
                        step_input_data["custom_input"] = custom_input_data
            
            # Step 1 never receives previous output; default step 2+ to True (sequential) unless overridden
            depends_on_previous = step_data.get("depends_on_previous")
            if not isinstance(depends_on_previous, bool):
                depends_on_previous = step_order != 1
            step_platform = step_data.get("allowed_platform_tool_ids")
            step_conn = step_data.get("allowed_connection_ids")
            step_tool_visibility = step_data.get("tool_visibility")
            step = WorkflowStep(
                job_id=job_id,
                agent_id=agent_id,
                step_order=step_order,
                input_data=json.dumps(step_input_data),
                status="pending",
                depends_on_previous=depends_on_previous,
                allowed_platform_tool_ids=json.dumps(step_platform) if step_platform else None,
                allowed_connection_ids=json.dumps(step_conn) if step_conn else None,
                tool_visibility=step_tool_visibility,
            )
            self.db.add(step)
        
        self.db.commit()
        
        # Calculate costs
        preview = self.payment_processor.calculate_job_cost(job_id)
        return preview

    def create_manual_workflow(self, job_id: int, workflow_steps: List[Dict[str, Any]]) -> WorkflowPreview:
        """Sync wrapper for tests and scripts. Prefer create_manual_workflow_async from async routes."""
        return asyncio.run(self.create_manual_workflow_async(job_id, workflow_steps))
