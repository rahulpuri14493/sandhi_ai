from sqlalchemy.orm import Session
from sqlalchemy import or_
from typing import List, Dict, Any, Optional
import asyncio
import json
from models.job import Job, WorkflowStep
from models.agent import Agent
from models.transaction import Earnings
from models.communication import AgentCommunication
from schemas.job import WorkflowPreview, WorkflowStepResponse
from services.payment_processor import PaymentProcessor
from services.task_splitter import split_job_for_agents


class WorkflowBuilder:
    def __init__(self, db: Session):
        self.db = db
        self.payment_processor = PaymentProcessor(db)
    
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

    def auto_split_workflow(
        self, job_id: int, agent_ids: List[int], workflow_mode: Optional[str] = None
    ) -> WorkflowPreview:
        """Automatically split a job across selected agents. workflow_mode: 'independent' | 'sequential' | None (infer from BRD)."""
        import json
        import asyncio
        
        job = self.db.query(Job).filter(Job.id == job_id).first()
        if not job:
            raise ValueError("Job not found")
        
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
        
        # Parse and read document content
        documents_content = []
        if job.files:
            try:
                from services.document_analyzer import DocumentAnalyzer
                files_data = json.loads(job.files)
                analyzer = DocumentAnalyzer()
                
                # Read content from each document
                for file_info in files_data:
                    file_path = file_info.get("path")
                    if file_path:
                        try:
                            # Use asyncio to call async method
                            content = asyncio.run(analyzer.read_document(file_path))
                            # Validate content was extracted - skip empty documents (they're optional)
                            if not content or not content.strip():
                                print(f"[WARNING] Document {file_info.get('name')} has empty content - skipping (documents are optional)")
                                continue
                            
                            print(f"[DEBUG] Successfully read document: {file_info.get('name')} - Content length: {len(content)} chars")
                            documents_content.append({
                                "name": file_info.get("name", "Unknown"),
                                "type": file_info.get("type", "unknown"),
                                "content": content
                            })
                        except Exception as e:
                            # If document reading fails, skip it (documents are optional)
                            print(f"[WARNING] Failed to read document {file_info.get('name')}: {str(e)} - skipping (documents are optional)")
                            continue
                    else:
                        print(f"[WARNING] Document {file_info.get('name')} has no file path")
            except (json.JSONDecodeError, TypeError, Exception) as e:
                # If document parsing fails, continue without document content (documents are optional)
                print(f"[WARNING] Failed to parse job.files: {str(e)} - Continuing without documents (they are optional)")
        
        # Log document status
        if documents_content:
            print(f"[DEBUG] {len(documents_content)} document(s) will be included as additional information")
        else:
            print(f"[DEBUG] No documents provided - agent will work with job title and description only")
        
        # Prepare base input data with job context, Q&A conversation, and documents
        base_input_data = {
            "job_title": job.title,
            "job_description": job.description,
            "conversation": conversation_data or [],  # Include Q&A conversation
            "documents": documents_content  # Include document content
        }
        
        # Split job into subtasks per agent (generalized, uses first agent's API when available)
        task_assignments = []
        if agents:
            splitter = agents[0] if (agents[0].api_endpoint and (agents[0].api_endpoint or "").strip()) else None
            try:
                if splitter:
                    task_assignments = asyncio.run(
                        split_job_for_agents(
                            job_title=job.title,
                            job_description=job.description or "",
                            documents_content=documents_content,
                            conversation_data=conversation_data,
                            agents=agents,
                            splitter_agent=splitter,
                        )
                    )
            except Exception as e:
                print(f"[WARNING] Task split failed: {e}, using fallback")
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
        print(f"[DEBUG] Workflow mode: {'independent' if steps_independent else 'sequential'} (workflow_mode={workflow_mode!r}, BRD hint used when None)")
        
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
        
        # Create workflow steps - each agent gets assigned task + base context
        steps = []
        for idx, agent in enumerate(agents):
            assigned_task = ""
            for ta in task_assignments:
                if ta.get("agent_index") == idx:
                    assigned_task = ta.get("task", "")
                    break
            if not assigned_task:
                assigned_task = f"Execute the job. You are agent {idx+1} of {len(agents)}. {job.description or job.title}"
            
            # Step 1 has no previous; step 2+ are independent only when steps_independent
            depends_on_previous = not steps_independent if idx >= 1 else True
            
            # Each agent gets base context + their specific task assignment
            step_input_data = {
                **base_input_data,
                "step_order": idx + 1,
                "total_steps": len(agents),
                "agent_name": agent.name,
                "agent_description": agent.description,
                "assigned_task": assigned_task,
                "previous_agents_outputs": []  # Filled by executor when passing previous outputs
            }
            
            # Validate step input data includes documents and conversation
            step_docs = step_input_data.get('documents', [])
            step_conv = step_input_data.get('conversation', [])
            print(f"[DEBUG] Step {idx + 1} for agent '{agent.name}': depends_on_previous={depends_on_previous}")
            print(f"[DEBUG]   - Documents: {len(step_docs)}")
            print(f"[DEBUG]   - Conversation items: {len(step_conv)}")
            
            step = WorkflowStep(
                job_id=job_id,
                agent_id=agent.id,
                step_order=idx + 1,
                input_data=json.dumps(step_input_data),
                status="pending",
                depends_on_previous=depends_on_previous,
            )
            self.db.add(step)
            steps.append(step)
        
        self.db.commit()
        
        # Calculate costs
        preview = self.payment_processor.calculate_job_cost(job_id)
        return preview
    
    def create_manual_workflow(self, job_id: int, workflow_steps: List[Dict[str, Any]]) -> WorkflowPreview:
        """Create a manual workflow from user-specified steps"""
        import json
        import asyncio
        
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
        
        # Parse and read document content
        documents_content = []
        if job.files:
            try:
                from services.document_analyzer import DocumentAnalyzer
                files_data = json.loads(job.files)
                analyzer = DocumentAnalyzer()
                
                # Read content from each document
                for file_info in files_data:
                    file_path = file_info.get("path")
                    if file_path:
                        try:
                            # Use asyncio to call async method
                            content = asyncio.run(analyzer.read_document(file_path))
                            # Validate content was extracted - skip empty documents (they're optional)
                            if not content or not content.strip():
                                print(f"[WARNING] Document {file_info.get('name')} has empty content - skipping (documents are optional)")
                                continue
                            
                            print(f"[DEBUG] Successfully read document: {file_info.get('name')} - Content length: {len(content)} chars")
                            documents_content.append({
                                "name": file_info.get("name", "Unknown"),
                                "type": file_info.get("type", "unknown"),
                                "content": content
                            })
                        except Exception as e:
                            # If document reading fails, skip it (documents are optional)
                            print(f"[WARNING] Failed to read document {file_info.get('name')}: {str(e)} - skipping (documents are optional)")
                            continue
                    else:
                        print(f"[WARNING] Document {file_info.get('name')} has no file path")
            except (json.JSONDecodeError, TypeError, Exception) as e:
                # If document parsing fails, continue without document content (documents are optional)
                print(f"[WARNING] Failed to parse job.files: {str(e)} - Continuing without documents (they are optional)")
        
        # Log document status
        if documents_content:
            print(f"[DEBUG] {len(documents_content)} document(s) will be included as additional information")
        else:
            print(f"[DEBUG] No documents provided - agent will work with job title and description only")
        
        # Prepare base input data with job context, Q&A conversation, and documents
        print(f"[DEBUG] Building manual workflow for job {job_id}")
        print(f"[DEBUG] Job title: {job.title}")
        print(f"[DEBUG] Conversation items: {len(conversation_data) if conversation_data else 0}")
        print(f"[DEBUG] Documents to include: {len(documents_content)}")
        
        base_input_data = {
            "job_title": job.title,
            "job_description": job.description,
            "conversation": conversation_data or [],  # Include Q&A conversation
            "documents": documents_content  # Include document content
        }
        
        # Validate that documents and conversation are included
        if not documents_content:
            print(f"[WARNING] No document content found for job {job_id}")
        else:
            total_content_length = sum(len(doc.get('content', '')) for doc in documents_content)
            print(f"[DEBUG] Total document content length: {total_content_length} characters")
        
        if not conversation_data:
            print(f"[WARNING] No conversation data found for job {job_id}")
        else:
            print(f"[DEBUG] Conversation includes {len([item for item in conversation_data if item.get('type') == 'question'])} questions")
            print(f"[DEBUG] Conversation includes {len([item for item in conversation_data if item.get('type') == 'completion'])} completion messages")
        
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
            print(f"[DEBUG] Manual workflow step {step_order} for agent '{agent.name}':")
            print(f"[DEBUG]   - Documents: {len(step_docs)}")
            print(f"[DEBUG]   - Conversation items: {len(step_conv)}")
            
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
            
            depends_on_previous = step_data.get("depends_on_previous", True)
            if not isinstance(depends_on_previous, bool):
                depends_on_previous = True
            step = WorkflowStep(
                job_id=job_id,
                agent_id=agent_id,
                step_order=step_order,
                input_data=json.dumps(step_input_data),
                status="pending",
                depends_on_previous=depends_on_previous,
            )
            self.db.add(step)
        
        self.db.commit()
        
        # Calculate costs
        preview = self.payment_processor.calculate_job_cost(job_id)
        return preview
