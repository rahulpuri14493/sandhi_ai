import logging
import asyncio
import io
import json
import zipfile
from pathlib import Path
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, status, BackgroundTasks, UploadFile, File, Form
from fastapi.responses import FileResponse, Response, StreamingResponse
from sqlalchemy.orm import Session
from pydantic import BaseModel
from db.database import get_db
from models.job import Job, JobStatus, WorkflowStep
from models.agent import Agent
from models.user import User, UserRole
from schemas.job import JobCreate, JobUpdate, JobResponse, WorkflowStepResponse, WorkflowPreview, AutoSplitBody, AnswerQuestionBody
from core.security import get_current_user, get_current_business_user
from core.config import settings
from services.workflow_builder import WorkflowBuilder
from services.payment_processor import PaymentProcessor
from services.agent_executor import AgentExecutor
from services.document_analyzer import DocumentAnalyzer
from models.transaction import Transaction, Earnings
from core.external_token import create_job_token, get_share_url
from datetime import datetime
from models.communication import AgentCommunication
from models.mcp_server import MCPToolConfig, MCPServerConnection
from services.job_file_storage import (
    persist_file,
    delete_file,
    delete_file_sync,
    redact_file_metadata,
    has_readable_source,
    open_local_download_path,
    open_s3_download_stream,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/jobs", tags=["jobs"])


def _validate_allowed_tools(db: Session, business_id: int, platform_ids: Optional[List[int]], connection_ids: Optional[List[int]]):
    """Validate that platform_tool_ids and connection_ids belong to business_id. Returns (platform_ids, connection_ids) as JSON-serializable lists or None."""
    out_platform = [] if platform_ids is not None and len(platform_ids) == 0 else None
    if platform_ids and len(platform_ids):
        valid = db.query(MCPToolConfig.id).filter(
            MCPToolConfig.user_id == business_id,
            MCPToolConfig.id.in_(platform_ids),
            MCPToolConfig.is_active == True,
        ).all()
        valid_set = {r[0] for r in valid}
        invalid = set(platform_ids) - valid_set
        if invalid:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Invalid or unauthorized platform tool ids: {sorted(invalid)}")
        out_platform = list(valid_set)
    out_conn = [] if connection_ids is not None and len(connection_ids) == 0 else None
    if connection_ids and len(connection_ids):
        valid = db.query(MCPServerConnection.id).filter(
            MCPServerConnection.user_id == business_id,
            MCPServerConnection.id.in_(connection_ids),
            MCPServerConnection.is_active == True,
        ).all()
        valid_set = {r[0] for r in valid}
        invalid = set(connection_ids) - valid_set
        if invalid:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Invalid or unauthorized connection ids: {sorted(invalid)}")
        out_conn = list(valid_set)
    return (out_platform, out_conn)


def _get_first_hired_agent_for_job(db: Session, job_id: int) -> Optional[tuple]:
    """Return (api_url, api_key, llm_model, temperature, a2a_enabled) for the first hired agent, or None."""
    first_step = (
        db.query(WorkflowStep)
        .filter(WorkflowStep.job_id == job_id)
        .order_by(WorkflowStep.step_order)
        .first()
    )
    if not first_step:
        return None
    agent = db.query(Agent).filter(Agent.id == first_step.agent_id).first()
    if not agent or not (agent.api_endpoint and (agent.api_endpoint or "").strip()):
        return None
    return (
        agent.api_endpoint.strip(),
        (agent.api_key or "").strip() or None,
        (getattr(agent, "llm_model", None) or None),
        (getattr(agent, "temperature", None)),
        getattr(agent, "a2a_enabled", False),
    )


# Allowed file extensions (including .zip; zip contents are extracted and only allowed types kept)
ALLOWED_EXTENSIONS = {
    '.txt', '.csv', '.doc', '.docx', '.pdf', '.xls', '.xlsx',
    '.json', '.xml', '.md', '.rtf', '.odt', '.ods', '.zip'
}

# Extensions for files we can extract text from (used when unpacking zip; no nested .zip)
EXTRACTABLE_EXTENSIONS = ALLOWED_EXTENSIONS - {'.zip'}


async def _process_one_upload(file: UploadFile, *, job_id: Optional[int] = None) -> List[dict]:
    """
    Process one uploaded file. If it's a .zip, extract and return metadata for each
    allowed file inside. Otherwise save the file and return a single metadata dict.
    """
    file_ext = Path(file.filename).suffix.lower()
    content = await file.read()
    max_file_bytes = max(1, int(getattr(settings, "JOB_UPLOAD_MAX_FILE_BYTES", 104857600)))
    if len(content) > max_file_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"File {file.filename} exceeds max allowed size of {max_file_bytes} bytes",
        )

    if file_ext == '.zip':
        # Extract zip and save each allowed file
        entries = []
        try:
            with zipfile.ZipFile(io.BytesIO(content), 'r') as zf:
                for name in zf.namelist():
                    if name.endswith('/'):
                        continue
                    ext = Path(name).suffix.lower()
                    if ext not in EXTRACTABLE_EXTENSIONS:
                        continue
                    safe_name = Path(name).name or f"file{ext}"
                    with zf.open(name, 'r') as src:
                        data = src.read()
                    entries.append(await persist_file(safe_name, data, "application/octet-stream", job_id=job_id))
        except zipfile.BadZipFile as e:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid or corrupted zip file: {file.filename}"
            ) from e
        return entries

    # Single file (non-zip)
    try:
        entry = await persist_file(file.filename, content, file.content_type or "application/octet-stream", job_id=job_id)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to save file {file.filename}: {str(e)}"
        ) from e
    return [entry]


class AnalyzeDocumentsRequest(BaseModel):
    job_id: int


class UserResponseRequest(BaseModel):
    job_id: int
    answer: str


def _parse_int_list_form(value: Optional[str]) -> Optional[List[int]]:
    """Parse Form JSON string to list of ints."""
    if not value or not value.strip():
        return None
    try:
        out = json.loads(value)
        return [int(x) for x in out] if isinstance(out, list) else None
    except (json.JSONDecodeError, TypeError, ValueError):
        return None


def _validate_tool_visibility(v: Optional[str]) -> Optional[str]:
    if v is None or (isinstance(v, str) and not v.strip()):
        return None
    s = (v or "").strip().lower()
    if s in ("full", "names_only", "none"):
        return s
    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="tool_visibility must be 'full', 'names_only', or 'none'")


@router.post("", response_model=JobResponse, status_code=status.HTTP_201_CREATED)
async def create_job(
    title: str = Form(...),
    description: Optional[str] = Form(None),
    files: Optional[List[UploadFile]] = File(None),
    allowed_platform_tool_ids: Optional[str] = Form(None),
    allowed_connection_ids: Optional[str] = Form(None),
    tool_visibility: Optional[str] = Form(None),
    current_user: User = Depends(get_current_business_user),
    db: Session = Depends(get_db)
):
    """Create a new job with optional file uploads and optional tool scope."""
    platform_ids = _parse_int_list_form(allowed_platform_tool_ids)
    connection_ids = _parse_int_list_form(allowed_connection_ids)
    if (platform_ids and len(platform_ids)) or (connection_ids and len(connection_ids)):
        platform_ids, connection_ids = _validate_allowed_tools(db, current_user.id, platform_ids, connection_ids)

    tv = _validate_tool_visibility(tool_visibility) if tool_visibility else None
    new_job = Job(
        business_id=current_user.id,
        title=title,
        description=description,
        status=JobStatus.DRAFT,
        files=None,
        conversation=json.dumps([]),  # Initialize empty conversation
        allowed_platform_tool_ids=json.dumps(platform_ids) if platform_ids is not None else None,
        allowed_connection_ids=json.dumps(connection_ids) if connection_ids is not None else None,
        tool_visibility=tv,
    )
    db.add(new_job)
    db.commit()
    db.refresh(new_job)

    file_metadata = []
    if files:
        try:
            for file in files:
                file_ext = Path(file.filename).suffix.lower()
                if file_ext not in ALLOWED_EXTENSIONS:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail=f"File type {file_ext} not allowed. Allowed types: {', '.join(sorted(ALLOWED_EXTENSIONS))}"
                    )
                file_metadata.extend(await _process_one_upload(file, job_id=new_job.id))
            if file_metadata:
                new_job.files = json.dumps(file_metadata)
                db.commit()
                db.refresh(new_job)
        except Exception:
            # Cleanup any staged file objects and delete the partially created job
            for entry in file_metadata:
                await delete_file(entry)
            db.delete(new_job)
            db.commit()
            raise

    # Build response with parsed files (remove storage internals for security)
    files_for_response = []
    if file_metadata:
        for file_info in file_metadata:
            files_for_response.append(redact_file_metadata(file_info))

    response_data = {
        "id": new_job.id,
        "business_id": new_job.business_id,
        "title": new_job.title,
        "description": new_job.description,
        "status": new_job.status,
        "total_cost": new_job.total_cost,
        "created_at": new_job.created_at,
        "completed_at": new_job.completed_at,
        "workflow_steps": [],
        "files": files_for_response if files_for_response else None,
        "failure_reason": new_job.failure_reason,
        "allowed_platform_tool_ids": platform_ids,
        "allowed_connection_ids": connection_ids,
        "tool_visibility": new_job.tool_visibility,
    }
    return JobResponse(**response_data)


@router.post("/{job_id}/analyze-documents", status_code=status.HTTP_200_OK)
async def analyze_documents(
    job_id: int,
    current_user: User = Depends(get_current_business_user),
    db: Session = Depends(get_db)
):
    """Analyze uploaded documents and generate questions using the hired agent."""
    job = db.query(Job).filter(Job.id == job_id).first()
    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Job not found"
        )
    
    if job.business_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized"
        )
    
    if not job.files:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No documents uploaded for this job"
        )
    
    # Parse files and conversation
    try:
        files_data = json.loads(job.files)
    except (json.JSONDecodeError, TypeError):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid job files data")
    conversation_history = []
    if job.conversation:
        try:
            conversation_history = json.loads(job.conversation)
        except (json.JSONDecodeError, TypeError):
            pass
    
    # Prepare documents for analysis (supports local path and S3-backed metadata)
    documents = [f for f in files_data if has_readable_source(f)]
    if not documents:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No valid document sources found in job files"
        )
    hired = _get_first_hired_agent_for_job(db, job.id)
    if hired:
        agent_url, agent_key, agent_model, agent_temp, use_a2a = hired
    else:
        agent_url = agent_key = agent_model = agent_temp = None
        use_a2a = False
    
    try:
        analyzer = DocumentAnalyzer()
        result = await analyzer.analyze_documents_and_generate_questions(
            documents=documents,
            job_title=job.title,
            job_description=job.description,
            conversation_history=conversation_history,
            agent_api_url=agent_url,
            agent_api_key=agent_key,
            agent_llm_model=agent_model,
            agent_temperature=agent_temp,
            use_a2a=use_a2a,
        )
        
        # Add the new questions to conversation
        
        new_conversation = conversation_history.copy()
        if result.get("analysis"):
            new_conversation.append({
                "type": "analysis",
                "content": result["analysis"],
                "timestamp": str(datetime.utcnow())
            })
        
        # Add questions if any (dedupe by text so we don't repeat the same question)
        existing_questions = {str(item.get("question", "")).strip() for item in new_conversation if item.get("type") == "question" and item.get("question")}
        if result.get("questions"):
            seen_in_batch = set()
            for question in result["questions"]:
                q = str(question).strip() if question else ""
                if not q or q in existing_questions or q in seen_in_batch:
                    continue
                seen_in_batch.add(q)
                existing_questions.add(q)
                new_conversation.append({
                    "type": "question",
                    "question": q,
                    "answer": None,
                    "timestamp": str(datetime.utcnow())
                })
        else:
            # No questions - add completion with solutions and optional workflow hint
            new_conversation.append({
                "type": "completion",
                "message": "Requirements understood. Here are the solutions:",
                "recommendations": result.get("recommendations", []),
                "solutions": result.get("solutions", []),
                "next_steps": result.get("next_steps", []),
                "workflow_collaboration_hint": result.get("workflow_collaboration_hint"),
                "workflow_collaboration_reason": result.get("workflow_collaboration_reason"),
                "timestamp": str(datetime.utcnow())
            })
        
        # Update job conversation
        job.conversation = json.dumps(new_conversation)
        db.commit()
        
        return {
            "analysis": result.get("analysis", ""),
            "questions": result.get("questions", []),
            "recommendations": result.get("recommendations", []),
            "solutions": result.get("solutions", []),
            "next_steps": result.get("next_steps", []),
            "conversation": new_conversation
        }
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to analyze documents: {str(e)}"
        )


@router.post("/{job_id}/answer-question", status_code=status.HTTP_200_OK)
async def answer_question(
    job_id: int,
    body: AnswerQuestionBody,
    current_user: User = Depends(get_current_business_user),
    db: Session = Depends(get_db)
):
    """Submit user's answer and get follow-up questions or recommendations."""
    answer = body.get_answer()
    if not answer:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Missing 'answer' in request body (or legacy 'question' with the user's answer text)",
        )
    job = db.query(Job).filter(Job.id == job_id).first()
    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Job not found"
        )
    
    if job.business_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized"
        )
    
    if not job.files:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No documents uploaded for this job"
        )
    
    # Parse conversation
    conversation_history = json.loads(job.conversation) if job.conversation else []
    
    # Question texts already answered (so we skip duplicate question items, same as frontend)
    answered_texts = {
        str(item.get("question", "")).strip()
        for item in conversation_history
        if item.get("type") == "question" and item.get("answer") and str(item.get("answer", "")).strip()
    }
    # Find the FIRST unanswered question whose text we haven't already answered (matches UI)
    first_question_idx = None
    for i in range(len(conversation_history)):
        item = conversation_history[i]
        if item.get("type") != "question" or item.get("answer"):
            continue
        qtext = str(item.get("question", "")).strip()
        if qtext and qtext not in answered_texts:
            first_question_idx = i
            break

    if first_question_idx is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No unanswered question found"
        )

    # Update conversation with answer
    conversation_history[first_question_idx]["answer"] = answer
    conversation_history[first_question_idx]["answered_at"] = str(datetime.utcnow())
    
    files_data = json.loads(job.files)
    documents = [f for f in files_data if has_readable_source(f)]
    hired = _get_first_hired_agent_for_job(db, job.id)
    if hired:
        agent_url, agent_key, agent_model, agent_temp, use_a2a = hired
    else:
        agent_url = agent_key = agent_model = agent_temp = None
        use_a2a = False

    try:
        analyzer = DocumentAnalyzer()
        result = await analyzer.process_user_response(
            user_answer=answer,
            documents=documents,
            job_title=job.title,
            job_description=job.description,
            conversation_history=conversation_history,
            agent_api_url=agent_url,
            agent_api_key=agent_key,
            agent_llm_model=agent_model,
            agent_temperature=agent_temp,
            use_a2a=use_a2a,
        )
        
        # Add new questions if any (dedupe: skip if already in conversation or already in this batch)
        existing_questions = {str(item.get("question", "")).strip() for item in conversation_history if item.get("type") == "question" and item.get("question")}
        if result.get("questions"):
            seen_in_batch = set()
            for question in result["questions"]:
                q = str(question).strip() if question else ""
                if not q or q in existing_questions or q in seen_in_batch:
                    continue
                seen_in_batch.add(q)
                existing_questions.add(q)
                conversation_history.append({
                    "type": "question",
                    "question": q,
                    "answer": None,
                    "timestamp": str(datetime.utcnow())
                })
        else:
            # No more questions - add completion message with solutions and optional workflow hint
            completion_item = {
                "type": "completion",
                "message": "Requirements understood. Here are the solutions and recommendations:",
                "recommendations": result.get("recommendations", []),
                "solutions": result.get("solutions", []),
                "next_steps": result.get("next_steps", []),
                "workflow_collaboration_hint": result.get("workflow_collaboration_hint"),
                "workflow_collaboration_reason": result.get("workflow_collaboration_reason"),
                "timestamp": str(datetime.utcnow())
            }
            conversation_history.append(completion_item)
        
        # Add analysis only if not duplicate (avoid same analysis block repeated after every answer)
        if result.get("analysis"):
            new_analysis = (result["analysis"] or "").strip()
            last_analysis = None
            for item in reversed(conversation_history):
                if item.get("type") == "analysis" and item.get("content"):
                    last_analysis = (item.get("content") or "").strip()
                    break
            if new_analysis and new_analysis != last_analysis:
                conversation_history.append({
                    "type": "analysis",
                    "content": result["analysis"],
                    "timestamp": str(datetime.utcnow())
                })
        
        # Update job conversation
        job.conversation = json.dumps(conversation_history)
        db.commit()
        
        return {
            "analysis": result.get("analysis", ""),
            "questions": result.get("questions", []),
            "recommendations": result.get("recommendations", []),
            "conversation": conversation_history,
            "completed": len(result.get("questions", [])) == 0
        }
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to process answer: {str(e)}"
        )


@router.post("/{job_id}/generate-workflow-questions", status_code=status.HTTP_200_OK)
async def generate_workflow_questions(
    job_id: int,
    current_user: User = Depends(get_current_business_user),
    db: Session = Depends(get_db)
):
    """
    Generate clarifying questions for the end user based on the workflow (assigned tasks),
    BRD documents, and job prompt. Use this in the Q&A step after Build Workflow so
    AI agents can get requirements clarified before execution. Appends new questions
    to the job conversation.
    """
    job = db.query(Job).filter(Job.id == job_id).first()
    if not job:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    if job.business_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized")

    steps = (
        db.query(WorkflowStep)
        .filter(WorkflowStep.job_id == job_id)
        .order_by(WorkflowStep.step_order)
        .all()
    )
    if not steps:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Job has no workflow. Build a workflow first, then generate clarification questions.",
        )

    # Build workflow_tasks from steps (assigned_task, agent_name)
    workflow_tasks = []
    for step in steps:
        agent = db.query(Agent).filter(Agent.id == step.agent_id).first()
        agent_name = agent.name if agent else "Agent"
        input_data = {}
        if step.input_data:
            try:
                input_data = json.loads(step.input_data)
            except (json.JSONDecodeError, TypeError):
                pass
        workflow_tasks.append({
            "step_order": step.step_order,
            "agent_name": agent_name,
            "assigned_task": input_data.get("assigned_task", ""),
        })

    # Document content for BRD (read from storage)
    documents_content = []
    if job.files:
        try:
            files_data = json.loads(job.files)
        except (json.JSONDecodeError, TypeError):
            files_data = []
        analyzer = DocumentAnalyzer()
        for f in files_data:
            name = f.get("name", "Unknown")
            if not has_readable_source(f):
                continue
            try:
                content = await analyzer.read_file_info(f)
                documents_content.append({"name": name, "content": content})
            except Exception:
                documents_content.append({"name": name, "content": f"[Could not read {name}]"})

    conversation_history = []
    if job.conversation:
        try:
            conversation_history = json.loads(job.conversation)
        except (json.JSONDecodeError, TypeError):
            pass

    hired = _get_first_hired_agent_for_job(db, job.id)
    if hired:
        agent_url, agent_key, agent_model, agent_temp, use_a2a = hired
    else:
        agent_url = agent_key = agent_model = agent_temp = None
        use_a2a = False

    try:
        analyzer = DocumentAnalyzer()
        result = await analyzer.generate_workflow_clarification_questions(
            job_title=job.title or "",
            job_description=job.description,
            documents_content=documents_content,
            workflow_tasks=workflow_tasks,
            conversation_history=conversation_history,
            agent_api_url=agent_url,
            agent_api_key=agent_key,
            agent_llm_model=agent_model,
            agent_temperature=agent_temp,
            use_a2a=use_a2a,
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to generate workflow questions: {str(e)}",
        )

    questions = result.get("questions") or []
    new_conversation = conversation_history.copy()
    existing_questions = {str(item.get("question", "")).strip() for item in new_conversation if item.get("type") == "question" and item.get("question")}
    seen_in_batch = set()
    for q in questions:
        qs = str(q).strip() if q else ""
        if not qs or qs in existing_questions or qs in seen_in_batch:
            continue
        seen_in_batch.add(qs)
        existing_questions.add(qs)
        new_conversation.append({
            "type": "question",
            "question": qs,
            "answer": None,
            "timestamp": str(datetime.utcnow()),
        })

    job.conversation = json.dumps(new_conversation)
    db.commit()

    return {"questions": questions, "conversation": new_conversation}


@router.get("", response_model=List[JobResponse])
def list_jobs(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    if current_user.role == UserRole.BUSINESS:
        jobs = db.query(Job).filter(Job.business_id == current_user.id).all()
    else:
        # Developers can see jobs where their agents are used
        jobs = db.query(Job).join(WorkflowStep).join(Agent).filter(
            Agent.developer_id == current_user.id
        ).distinct().all()
    
    # Parse files for each job
    result = []
    for job in jobs:
        files_data = None
        if job.files:
            try:
                files_parsed = json.loads(job.files)
                # Remove storage internals for security
                files_data = [redact_file_metadata(f) for f in files_parsed]
            except (json.JSONDecodeError, TypeError):
                pass
        
        # Parse conversation
        conversation_data = None
        if job.conversation:
            try:
                conversation_data = json.loads(job.conversation)
            except (json.JSONDecodeError, TypeError):
                pass
        
        job_platform = job_conn = None
        if getattr(job, "allowed_platform_tool_ids", None):
            try:
                job_platform = json.loads(job.allowed_platform_tool_ids)
            except (json.JSONDecodeError, TypeError):
                pass
        if getattr(job, "allowed_connection_ids", None):
            try:
                job_conn = json.loads(job.allowed_connection_ids)
            except (json.JSONDecodeError, TypeError):
                pass
        job_dict = {
            "id": job.id,
            "business_id": job.business_id,
            "title": job.title,
            "description": job.description,
            "status": job.status,
            "total_cost": job.total_cost,
            "created_at": job.created_at,
            "completed_at": job.completed_at,
            "workflow_steps": [],
            "files": files_data,
            "conversation": conversation_data,
            "failure_reason": job.failure_reason,
            "allowed_platform_tool_ids": job_platform,
            "allowed_connection_ids": job_conn,
            "tool_visibility": getattr(job, "tool_visibility", None),
        }
        result.append(JobResponse(**job_dict))
    return result


@router.get("/{job_id}", response_model=JobResponse)
def get_job(
    job_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    job = db.query(Job).filter(Job.id == job_id).first()
    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Job not found"
        )
    
    # Check authorization
    if current_user.role == UserRole.BUSINESS and job.business_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to view this job"
        )
    
    # Parse files
    files_data = None
    if job.files:
        try:
            files_parsed = json.loads(job.files)
            # Remove storage internals for security
            files_data = [redact_file_metadata(f) for f in files_parsed]
        except (json.JSONDecodeError, TypeError):
            pass
    
    # Parse conversation
    conversation_data = None
    if job.conversation:
        try:
            conversation_data = json.loads(job.conversation)
        except (json.JSONDecodeError, TypeError):
            pass
    
    # Load workflow steps with output data and agent names
    workflow_steps_data = []
    workflow_steps = db.query(WorkflowStep).filter(WorkflowStep.job_id == job_id).order_by(WorkflowStep.step_order).all()
    for step in workflow_steps:
        agent = db.query(Agent).filter(Agent.id == step.agent_id).first()
        agent_name = agent.name if agent else None
        step_platform = None
        step_conn = None
        if getattr(step, "allowed_platform_tool_ids", None):
            try:
                step_platform = json.loads(step.allowed_platform_tool_ids)
            except (json.JSONDecodeError, TypeError):
                pass
        if getattr(step, "allowed_connection_ids", None):
            try:
                step_conn = json.loads(step.allowed_connection_ids)
            except (json.JSONDecodeError, TypeError):
                pass
        workflow_steps_data.append(WorkflowStepResponse(
            id=step.id,
            job_id=step.job_id,
            agent_id=step.agent_id,
            agent_name=agent_name,
            step_order=step.step_order,
            input_data=step.input_data,
            output_data=step.output_data,
            status=step.status,
            cost=step.cost or 0.0,
            started_at=step.started_at,
            completed_at=step.completed_at,
            depends_on_previous=getattr(step, "depends_on_previous", True),
            allowed_platform_tool_ids=step_platform,
            allowed_connection_ids=step_conn,
            tool_visibility=getattr(step, "tool_visibility", None),
        ))

    job_platform = None
    job_conn = None
    if getattr(job, "allowed_platform_tool_ids", None):
        try:
            job_platform = json.loads(job.allowed_platform_tool_ids)
        except (json.JSONDecodeError, TypeError):
            pass
    if getattr(job, "allowed_connection_ids", None):
        try:
            job_conn = json.loads(job.allowed_connection_ids)
        except (json.JSONDecodeError, TypeError):
            pass
    job_dict = {
        "id": job.id,
        "business_id": job.business_id,
        "title": job.title,
        "description": job.description,
        "status": job.status,
        "total_cost": job.total_cost,
        "created_at": job.created_at,
        "completed_at": job.completed_at,
        "workflow_steps": workflow_steps_data,
        "files": files_data,
        "conversation": conversation_data,
        "failure_reason": job.failure_reason,
        "allowed_platform_tool_ids": job_platform,
        "allowed_connection_ids": job_conn,
        "tool_visibility": getattr(job, "tool_visibility", None),
    }
    return JobResponse(**job_dict)


@router.put("/{job_id}", response_model=JobResponse)
async def update_job(
    job_id: int,
    title: Optional[str] = Form(None),
    description: Optional[str] = Form(None),
    status: Optional[str] = Form(None),
    files: Optional[List[UploadFile]] = File(None),
    allowed_platform_tool_ids: Optional[str] = Form(None),
    allowed_connection_ids: Optional[str] = Form(None),
    tool_visibility: Optional[str] = Form(None),
    current_user: User = Depends(get_current_business_user),
    db: Session = Depends(get_db)
):
    """Update a job with optional file uploads and optional tool scope."""
    job = db.query(Job).filter(Job.id == job_id).first()
    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Job not found"
        )

    if job.business_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to update this job"
        )

    # Only allow updates to draft jobs or status changes
    if job.status != JobStatus.DRAFT and status is None:
        if title is not None or description is not None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Can only update title and description for draft jobs. Use status update for other jobs."
            )

    # Update allowed tools if provided (each key updates only that field)
    if allowed_platform_tool_ids is not None:
        pids = _parse_int_list_form(allowed_platform_tool_ids)
        if pids:
            pids, _ = _validate_allowed_tools(db, current_user.id, pids, None)
        job.allowed_platform_tool_ids = json.dumps(pids) if pids is not None else None
    if allowed_connection_ids is not None:
        cids = _parse_int_list_form(allowed_connection_ids)
        if cids:
            _, cids = _validate_allowed_tools(db, current_user.id, None, cids)
        job.allowed_connection_ids = json.dumps(cids) if cids is not None else None
    if tool_visibility is not None:
        job.tool_visibility = _validate_tool_visibility(tool_visibility)

    # Update basic fields
    if title is not None:
        job.title = title
    if description is not None:
        job.description = description
    if status is not None:
        try:
            job.status = JobStatus(status)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid status: {status}"
            )
    
    # Handle file uploads (overwrite existing documents with the new upload set)
    new_files_added = False
    old_files_to_cleanup: List[dict] = []
    staged_new_files: List[dict] = []
    if files:
        old_files = []
        if job.files:
            try:
                old_files = json.loads(job.files)
            except (json.JSONDecodeError, TypeError):
                old_files = []
        try:
            for file in files:
                file_ext = Path(file.filename).suffix.lower()
                if file_ext not in ALLOWED_EXTENSIONS:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail=f"File type {file_ext} not allowed. Allowed types: {', '.join(sorted(ALLOWED_EXTENSIONS))}"
                    )
                entries = await _process_one_upload(file, job_id=job.id)
                for e in entries:
                    staged_new_files.append(e)
                if entries:
                    new_files_added = True
        except Exception:
            # Best-effort cleanup of newly staged files so failed overwrite keeps existing data intact
            for staged in staged_new_files:
                await delete_file(staged)
            raise

        # Overwrite: keep only the latest upload set
        job.files = json.dumps(staged_new_files)

        # Reset conversation when new files are uploaded to start fresh Q&A
        if new_files_added:
            job.conversation = json.dumps([])
            old_files_to_cleanup = old_files
            logger.info(
                "BRD overwrite staged for job_id=%s business_id=%s old_files=%s new_files=%s",
                job.id,
                current_user.id,
                len(old_files),
                len(staged_new_files),
            )
    
    try:
        db.commit()
        db.refresh(job)
    except Exception:
        # If DB commit fails after staging new files, remove staged objects/files.
        if staged_new_files:
            for staged in staged_new_files:
                await delete_file(staged)
        raise

    # Cleanup old files only after successful DB commit.
    if old_files_to_cleanup:
        for old in old_files_to_cleanup:
            await delete_file(old)
        logger.info(
            "BRD overwrite finalized for job_id=%s business_id=%s removed_files=%s",
            job.id,
            current_user.id,
            len(old_files_to_cleanup),
        )
    
    # If new files were added, automatically trigger document analysis (extraction only or via first hired agent)
    analysis_result = None
    if new_files_added and job.files:
        try:
            files_data = json.loads(job.files)
            documents = [f for f in files_data if has_readable_source(f)]
            hired = _get_first_hired_agent_for_job(db, job.id)
            if hired:
                agent_url, agent_key = hired[0], hired[1]
                use_a2a = hired[4] if len(hired) > 4 else False
            else:
                agent_url = agent_key = None
                use_a2a = False

            analyzer = DocumentAnalyzer()
            result = await analyzer.analyze_documents_and_generate_questions(
                documents=documents,
                job_title=job.title,
                job_description=job.description,
                conversation_history=[],
                agent_api_url=agent_url,
                agent_api_key=agent_key,
                use_a2a=use_a2a,
            )
            
            # Add analysis and questions to conversation
            new_conversation = []
            if result.get("analysis"):
                new_conversation.append({
                    "type": "analysis",
                    "content": result["analysis"],
                    "timestamp": str(datetime.utcnow())
                })
            for question in result.get("questions", []):
                new_conversation.append({
                    "type": "question",
                    "question": question,
                    "answer": None,
                    "timestamp": str(datetime.utcnow())
                })
            
            job.conversation = json.dumps(new_conversation)
            db.commit()
            db.refresh(job)
            
            analysis_result = {
                "analysis": result.get("analysis", ""),
                "questions": result.get("questions", []),
                "recommendations": result.get("recommendations", [])
            }
        except Exception as e:
            # Don't fail the update if analysis fails, just log it
            logger.warning("Failed to auto-analyze documents: %s", e)
    
    # Parse files and conversation for response
    files_data = None
    if job.files:
        try:
            files_parsed = json.loads(job.files)
            files_data = [redact_file_metadata(f) for f in files_parsed]
        except (json.JSONDecodeError, TypeError):
            pass
    
    conversation_data = None
    if job.conversation:
        try:
            conversation_data = json.loads(job.conversation)
        except (json.JSONDecodeError, TypeError):
            pass
    
    job_platform = None
    job_conn = None
    if getattr(job, "allowed_platform_tool_ids", None):
        try:
            job_platform = json.loads(job.allowed_platform_tool_ids)
        except (json.JSONDecodeError, TypeError):
            pass
    if getattr(job, "allowed_connection_ids", None):
        try:
            job_conn = json.loads(job.allowed_connection_ids)
        except (json.JSONDecodeError, TypeError):
            pass
    job_dict = {
        "id": job.id,
        "business_id": job.business_id,
        "title": job.title,
        "description": job.description,
        "status": job.status,
        "total_cost": job.total_cost,
        "created_at": job.created_at,
        "completed_at": job.completed_at,
        "workflow_steps": [],
        "files": files_data,
        "conversation": conversation_data,
        "failure_reason": getattr(job, "failure_reason", None),
        "allowed_platform_tool_ids": job_platform,
        "allowed_connection_ids": job_conn,
        "tool_visibility": getattr(job, "tool_visibility", None),
    }

    return JobResponse(**job_dict)


@router.delete("/{job_id}")
def delete_job(
    job_id: int,
    current_user: User = Depends(get_current_business_user),
    db: Session = Depends(get_db)
):
    """Delete a job (draft, completed, or failed jobs can be deleted)"""
    job = db.query(Job).filter(Job.id == job_id).first()
    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Job not found"
        )
    
    if job.business_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to delete this job"
        )
    
    # Allow deletion of draft, completed, or failed jobs
    # Prevent deletion of jobs that are in progress or pending approval
    if job.status in [JobStatus.IN_PROGRESS, JobStatus.PENDING_APPROVAL, JobStatus.APPROVED]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot delete a job with status '{job.status}'. Only draft, completed, or failed jobs can be deleted."
        )
    
    # Delete transaction and earnings FIRST (earnings reference agent_communications,
    # so they must be removed before we delete workflow steps and communications)
    
    transaction = db.query(Transaction).filter(Transaction.job_id == job_id).first()
    if transaction:
        db.query(Earnings).filter(Earnings.transaction_id == transaction.id).delete()
        db.delete(transaction)
    
    # Delete associated workflow steps and their communications (no FKs reference these now)
    
    workflow_steps = db.query(WorkflowStep).filter(WorkflowStep.job_id == job_id).all()
    for step in workflow_steps:
        db.query(AgentCommunication).filter(
            (AgentCommunication.from_workflow_step_id == step.id) |
            (AgentCommunication.to_workflow_step_id == step.id)
        ).delete()
        db.delete(step)
    
    # Delete associated files
    if job.files:
        try:
            files_data = json.loads(job.files)
            for file_info in files_data:
                delete_file_sync(file_info)
        except (json.JSONDecodeError, TypeError, Exception):
            pass  # Continue even if file deletion fails
    
    # Now delete the job
    db.delete(job)
    db.commit()
    # Return 204 No Content response
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{job_id}/workflow/auto-split", response_model=WorkflowPreview)
def auto_split_workflow(
    job_id: int,
    body: AutoSplitBody,
    current_user: User = Depends(get_current_business_user),
    db: Session = Depends(get_db)
):
    job = db.query(Job).filter(Job.id == job_id).first()
    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Job not found"
        )
    
    if job.business_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized"
        )
    
    workflow_mode = (body.workflow_mode or "").strip() or None
    if workflow_mode and workflow_mode not in ("independent", "sequential"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="workflow_mode must be 'independent' or 'sequential' when provided",
        )
    step_tools = None
    if body.step_tools:
        step_tools = [
            {
                "agent_index": st.agent_index,
                "allowed_platform_tool_ids": st.allowed_platform_tool_ids,
                "allowed_connection_ids": st.allowed_connection_ids,
                "tool_visibility": getattr(st, "tool_visibility", None),
            }
            for st in body.step_tools
        ]
    tv = body.tool_visibility
    if tv is not None and tv not in ("full", "names_only", "none"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="tool_visibility must be 'full', 'names_only', or 'none'")
    workflow_builder = WorkflowBuilder(db)
    preview = workflow_builder.auto_split_workflow(job_id, body.agent_ids, workflow_mode=workflow_mode, step_tools=step_tools, tool_visibility=tv)
    return preview


@router.post("/{job_id}/workflow/manual", response_model=WorkflowPreview)
def manual_workflow(
    job_id: int,
    workflow_steps: List[dict],
    current_user: User = Depends(get_current_business_user),
    db: Session = Depends(get_db)
):
    job = db.query(Job).filter(Job.id == job_id).first()
    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Job not found"
        )
    
    if job.business_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized"
        )
    
    workflow_builder = WorkflowBuilder(db)
    preview = workflow_builder.create_manual_workflow(job_id, workflow_steps)
    return preview


class StepToolsUpdateBody(BaseModel):
    allowed_platform_tool_ids: Optional[List[int]] = None
    allowed_connection_ids: Optional[List[int]] = None
    tool_visibility: Optional[str] = None  # full | names_only | none


@router.patch("/{job_id}/workflow/steps/{step_id}", response_model=WorkflowStepResponse)
def update_workflow_step_tools(
    job_id: int,
    step_id: int,
    body: StepToolsUpdateBody,
    current_user: User = Depends(get_current_business_user),
    db: Session = Depends(get_db)
):
    """Update which tools a workflow step (agent) can use."""
    job = db.query(Job).filter(Job.id == job_id).first()
    if not job:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    if job.business_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized")
    step = db.query(WorkflowStep).filter(WorkflowStep.id == step_id, WorkflowStep.job_id == job_id).first()
    if not step:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Step not found")
    if body.allowed_platform_tool_ids is not None or body.allowed_connection_ids is not None:
        pids, cids = body.allowed_platform_tool_ids, body.allowed_connection_ids
        if (pids and len(pids)) or (cids and len(cids)):
            pids, cids = _validate_allowed_tools(db, current_user.id, pids, cids)
        step.allowed_platform_tool_ids = json.dumps(pids) if pids is not None else None
        step.allowed_connection_ids = json.dumps(cids) if cids is not None else None
    if body.tool_visibility is not None:
        step.tool_visibility = _validate_tool_visibility(body.tool_visibility)
    db.commit()
    db.refresh(step)
    agent = db.query(Agent).filter(Agent.id == step.agent_id).first()
    step_platform = step_conn = None
    if getattr(step, "allowed_platform_tool_ids", None):
        try:
            step_platform = json.loads(step.allowed_platform_tool_ids)
        except (json.JSONDecodeError, TypeError):
            pass
    if getattr(step, "allowed_connection_ids", None):
        try:
            step_conn = json.loads(step.allowed_connection_ids)
        except (json.JSONDecodeError, TypeError):
            pass
    return WorkflowStepResponse(
        id=step.id,
        job_id=step.job_id,
        agent_id=step.agent_id,
        agent_name=agent.name if agent else None,
        step_order=step.step_order,
        input_data=step.input_data,
        output_data=step.output_data,
        status=step.status,
        cost=step.cost or 0.0,
        started_at=step.started_at,
        completed_at=step.completed_at,
        depends_on_previous=getattr(step, "depends_on_previous", True),
        tool_visibility=getattr(step, "tool_visibility", None),
        allowed_platform_tool_ids=step_platform,
        allowed_connection_ids=step_conn,
    )


@router.get("/{job_id}/workflow/preview", response_model=WorkflowPreview)
def preview_workflow(
    job_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    job = db.query(Job).filter(Job.id == job_id).first()
    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Job not found"
        )
    
    payment_processor = PaymentProcessor(db)
    preview = payment_processor.calculate_job_cost(job_id)
    return preview


@router.post("/{job_id}/approve", response_model=JobResponse)
def approve_job(
    job_id: int,
    current_user: User = Depends(get_current_business_user),
    db: Session = Depends(get_db)
):
    job = db.query(Job).filter(Job.id == job_id).first()
    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Job not found"
        )
    
    if job.business_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized"
        )
    
    job.status = JobStatus.PENDING_APPROVAL
    db.commit()
    db.refresh(job)
    
    # Parse files and conversation for response
    files_data = None
    if job.files:
        try:
            files_parsed = json.loads(job.files)
            files_data = [redact_file_metadata(f) for f in files_parsed]
        except (json.JSONDecodeError, TypeError):
            pass
    
    conversation_data = None
    if job.conversation:
        try:
            conversation_data = json.loads(job.conversation)
        except (json.JSONDecodeError, TypeError):
            pass
    
    job_dict = {
        "id": job.id,
        "business_id": job.business_id,
        "title": job.title,
        "description": job.description,
        "status": job.status,
        "total_cost": job.total_cost,
        "created_at": job.created_at,
        "completed_at": job.completed_at,
        "workflow_steps": [],
        "files": files_data,
        "conversation": conversation_data,
        "failure_reason": job.failure_reason
    }
    return JobResponse(**job_dict)


@router.post("/{job_id}/execute", response_model=JobResponse)
def execute_job(
    job_id: int,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_business_user),
    db: Session = Depends(get_db)
):
    job = db.query(Job).filter(Job.id == job_id).first()
    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Job not found"
        )
    
    if job.business_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized"
        )
    
    if job.status != JobStatus.PENDING_APPROVAL:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Job must be approved before execution"
        )
    
    # Mark job as in progress
    job.status = JobStatus.IN_PROGRESS
    db.commit()
    db.refresh(job)
    
    # Trigger async job execution
    def run_job():
        # Create a new event loop for the background task
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            executor = AgentExecutor(db)
            loop.run_until_complete(executor.execute_job(job_id))
        except Exception as e:
            # Job status will be updated to failed by executor
            logger.exception("Job execution failed: %s", e)
        finally:
            loop.close()
    
    background_tasks.add_task(run_job)
    
    # Parse files and conversation for response
    files_data = None
    if job.files:
        try:
            files_parsed = json.loads(job.files)
            files_data = [redact_file_metadata(f) for f in files_parsed]
        except (json.JSONDecodeError, TypeError):
            pass
    
    conversation_data = None
    if job.conversation:
        try:
            conversation_data = json.loads(job.conversation)
        except (json.JSONDecodeError, TypeError):
            pass
    
    job_dict = {
        "id": job.id,
        "business_id": job.business_id,
        "title": job.title,
        "description": job.description,
        "status": job.status,
        "total_cost": job.total_cost,
        "created_at": job.created_at,
        "completed_at": job.completed_at,
        "workflow_steps": [],
        "files": files_data,
        "conversation": conversation_data,
        "failure_reason": job.failure_reason
    }
    return JobResponse(**job_dict)


@router.post("/{job_id}/rerun", response_model=JobResponse)
def rerun_job(
    job_id: int,
    current_user: User = Depends(get_current_business_user),
    db: Session = Depends(get_db)
):
    """Rerun a completed or failed job"""
    job = db.query(Job).filter(Job.id == job_id).first()
    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Job not found"
        )
    
    if job.business_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized"
        )
    
    # Only allow rerunning completed or failed jobs
    if job.status not in [JobStatus.COMPLETED, JobStatus.FAILED]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only completed or failed jobs can be rerun"
        )
    
    # Reset workflow steps - clear output data and reset status
    workflow_steps = db.query(WorkflowStep).filter(WorkflowStep.job_id == job_id).all()
    for step in workflow_steps:
        step.output_data = None
        step.status = "pending"
        step.started_at = None
        step.completed_at = None
        step.cost = 0.0
    
        # Reset job status to pending_approval so it can be executed again
        job.status = JobStatus.PENDING_APPROVAL
        job.completed_at = None
        job.failure_reason = None  # Clear previous failure reason
        db.commit()
    db.refresh(job)
    
    # Parse files and conversation for response
    files_data = None
    if job.files:
        try:
            files_parsed = json.loads(job.files)
            files_data = [redact_file_metadata(f) for f in files_parsed]
        except (json.JSONDecodeError, TypeError):
            pass
    
    conversation_data = None
    if job.conversation:
        try:
            conversation_data = json.loads(job.conversation)
        except (json.JSONDecodeError, TypeError):
            pass
    
    job_dict = {
        "id": job.id,
        "business_id": job.business_id,
        "title": job.title,
        "description": job.description,
        "status": job.status,
        "total_cost": job.total_cost,
        "created_at": job.created_at,
        "completed_at": job.completed_at,
        "workflow_steps": [],
        "files": files_data,
        "conversation": conversation_data,
        "failure_reason": job.failure_reason
    }
    return JobResponse(**job_dict)


@router.get("/{job_id}/share-link", response_model=dict)
def get_job_share_link(
    job_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Get a shareable link for external access to this job (no platform login required)."""
    job = db.query(Job).filter(Job.id == job_id).first()
    if not job:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    if current_user.role == UserRole.BUSINESS and job.business_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized")
    
    return {
        "job_id": job.id,
        "share_url": get_share_url(job.id),
        "token": create_job_token(job.id),
        "expires_in_days": getattr(settings, "EXTERNAL_TOKEN_EXPIRE_DAYS", 7),
    }


@router.get("/{job_id}/status", response_model=JobResponse)
def get_job_status(
    job_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    job = db.query(Job).filter(Job.id == job_id).first()
    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Job not found"
        )
    
    # Parse files and conversation for response
    files_data = None
    if job.files:
        try:
            files_parsed = json.loads(job.files)
            files_data = [redact_file_metadata(f) for f in files_parsed]
        except (json.JSONDecodeError, TypeError):
            pass
    
    conversation_data = None
    if job.conversation:
        try:
            conversation_data = json.loads(job.conversation)
        except (json.JSONDecodeError, TypeError):
            pass
    
    # Load workflow steps with output data and agent names
    workflow_steps_data = []
    workflow_steps = db.query(WorkflowStep).filter(WorkflowStep.job_id == job_id).order_by(WorkflowStep.step_order).all()
    for step in workflow_steps:
        agent = db.query(Agent).filter(Agent.id == step.agent_id).first()
        agent_name = agent.name if agent else None
        step_platform = step_conn = None
        if getattr(step, "allowed_platform_tool_ids", None):
            try:
                step_platform = json.loads(step.allowed_platform_tool_ids)
            except (json.JSONDecodeError, TypeError):
                pass
        if getattr(step, "allowed_connection_ids", None):
            try:
                step_conn = json.loads(step.allowed_connection_ids)
            except (json.JSONDecodeError, TypeError):
                pass
        workflow_steps_data.append(WorkflowStepResponse(
            id=step.id,
            job_id=step.job_id,
            agent_id=step.agent_id,
            agent_name=agent_name,
            step_order=step.step_order,
            input_data=step.input_data,
            output_data=step.output_data,  # Keep as string for frontend to parse
            status=step.status,
            cost=step.cost or 0.0,
            started_at=step.started_at,
            completed_at=step.completed_at,
            depends_on_previous=getattr(step, "depends_on_previous", True),
            allowed_platform_tool_ids=step_platform,
            allowed_connection_ids=step_conn,
            tool_visibility=getattr(step, "tool_visibility", None),
        ))
    
    job_platform = None
    job_conn = None
    if getattr(job, "allowed_platform_tool_ids", None):
        try:
            job_platform = json.loads(job.allowed_platform_tool_ids)
        except (json.JSONDecodeError, TypeError):
            pass
    if getattr(job, "allowed_connection_ids", None):
        try:
            job_conn = json.loads(job.allowed_connection_ids)
        except (json.JSONDecodeError, TypeError):
            pass

    job_dict = {
        "id": job.id,
        "business_id": job.business_id,
        "title": job.title,
        "description": job.description,
        "status": job.status,
        "total_cost": job.total_cost,
        "created_at": job.created_at,
        "completed_at": job.completed_at,
        "workflow_steps": workflow_steps_data,
        "files": files_data,
        "conversation": conversation_data,
        "failure_reason": job.failure_reason,
        "allowed_platform_tool_ids": job_platform,
        "allowed_connection_ids": job_conn,
        "tool_visibility": getattr(job, "tool_visibility", None),
    }
    return JobResponse(**job_dict)


@router.get("/{job_id}/files/{file_id}")
def download_job_file(
    job_id: int,
    file_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Download a file associated with a job"""
    job = db.query(Job).filter(Job.id == job_id).first()
    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Job not found"
        )
    
    # Check authorization
    if current_user.role == UserRole.BUSINESS and job.business_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to access this file"
        )
    
    # Parse file metadata
    if not job.files:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No files found for this job"
        )
    
    files = json.loads(job.files)
    file_info = next((f for f in files if f["id"] == file_id), None)
    
    if not file_info:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="File not found"
        )
    
    if file_info.get("storage") == "s3":
        try:
            body, media_type, content_length = open_s3_download_stream(file_info)
        except Exception:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="File no longer exists on storage"
            )
        headers = {"Content-Disposition": f'attachment; filename="{file_info.get("name", "file")}"'}
        if content_length is not None:
            headers["Content-Length"] = str(content_length)
        return StreamingResponse(
            content=body.iter_chunks(chunk_size=1024 * 1024),
            media_type=media_type,
            headers=headers,
        )

    try:
        file_path = open_local_download_path(file_info)
    except FileNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="File no longer exists on server"
        )
    
    return FileResponse(
        path=file_path,
        filename=file_info["name"],
        media_type=file_info["type"]
    )
