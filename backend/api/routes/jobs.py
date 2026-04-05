import logging
import asyncio
import io
import json
import zipfile
import random
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, Depends, HTTPException, status, UploadFile, File, Form, Query
from fastapi.responses import FileResponse, Response, StreamingResponse
from sqlalchemy.orm import Session
from pydantic import BaseModel
from db.database import get_db
from models.job import Job, JobStatus, WorkflowStep, JobSchedule, ScheduleStatus, ScheduleExecutionHistory, JobPlannerArtifact
from models.agent import Agent
from models.user import User, UserRole
from schemas.job import (
    JobResponse, WorkflowStepResponse, WorkflowPreview,
    AutoSplitBody, AnswerQuestionBody,
    JobScheduleCreate, JobScheduleUpdate, JobScheduleResponse,
    JobScheduleWithJobResponse, ScheduleExecutionHistoryResponse,
    ScheduleListResponse, ScheduleActionResponse, RerunResponse,
    JobFilterOptions, ScheduleFilterOptions, EnumOption,
    PlannerArtifactListResponse, PlannerArtifactResponse,
    PlannerPipelineBundleResponse,
)
from core.security import get_current_user, get_current_business_user
from core.config import settings
from services.job_scheduler import get_scheduler, reset_job_for_execution, queue_job_execution
from services.task_queue import get_queue_stats
from services.workflow_builder import WorkflowBuilder
from services.tool_splitter import suggest_tool_assignments_for_agents
from services.payment_processor import PaymentProcessor
from services.document_analyzer import DocumentAnalyzer
from services.planner_artifact_cache import get_cached_planner_raw, set_cached_planner_raw
from services.planner_artifact_storage import (
    load_latest_planner_pipeline_payloads,
    persist_brd_analysis_artifact,
    persist_json_planner_artifact,
    read_planner_artifact_bytes,
)
from models.transaction import Transaction, Earnings
from core.external_token import create_job_token, get_share_url
from datetime import datetime, timedelta
from models.communication import AgentCommunication
from models.mcp_server import MCPToolConfig, MCPServerConnection
from services.job_file_storage import (
    persist_file,
    delete_file,
    delete_file_sync,
    download_s3_bytes,
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


def _user_can_access_job(job: Job, current_user: User, db: Session) -> bool:
    if current_user.role == UserRole.BUSINESS:
        return job.business_id == current_user.id
    return (
        db.query(WorkflowStep)
        .join(Agent)
        .filter(WorkflowStep.job_id == job.id, Agent.developer_id == current_user.id)
        .first()
        is not None
    )


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


def _zip_extract_backoff(attempt_idx: int) -> float:
    base = max(0.0, float(getattr(settings, "ZIP_EXTRACT_RETRY_BASE_DELAY_SECONDS", 0.1)))
    cap = max(base, float(getattr(settings, "ZIP_EXTRACT_RETRY_MAX_DELAY_SECONDS", 0.5)))
    jitter = max(0.0, float(getattr(settings, "ZIP_EXTRACT_RETRY_JITTER_SECONDS", 0.05)))
    return min(cap, base * (2 ** max(0, attempt_idx))) + random.uniform(0, jitter)


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
        # S3 flow: persist raw zip in object storage first, then extract from stored object bytes.
        s3_backend = (getattr(settings, "OBJECT_STORAGE_BACKEND", "s3") or "s3").strip().lower() == "s3"
        raw_zip_entry: Optional[dict] = None
        extract_source = content
        if s3_backend:
            try:
                raw_zip_entry = await persist_file(
                    file.filename,
                    content,
                    file.content_type or "application/zip",
                    job_id=job_id,
                )
                extract_source = await asyncio.to_thread(download_s3_bytes, raw_zip_entry)
            except Exception as e:
                if raw_zip_entry:
                    await delete_file(raw_zip_entry)
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail=f"Failed to stage zip file {file.filename} in object storage: {str(e)}",
                ) from e

        # Extract zip and save each allowed file (with transient retry + cleanup)
        attempts = max(1, int(getattr(settings, "ZIP_EXTRACT_RETRY_ATTEMPTS", 3)))
        for attempt in range(attempts):
            staged_entries: List[dict] = []
            try:
                with zipfile.ZipFile(io.BytesIO(extract_source), 'r') as zf:
                    for name in zf.namelist():
                        if name.endswith('/'):
                            continue
                        ext = Path(name).suffix.lower()
                        if ext not in EXTRACTABLE_EXTENSIONS:
                            continue
                        safe_name = Path(name).name or f"file{ext}"
                        with zf.open(name, 'r') as src:
                            data = src.read()
                        staged_entries.append(
                            await persist_file(safe_name, data, "application/octet-stream", job_id=job_id)
                        )
                if raw_zip_entry:
                    await delete_file(raw_zip_entry)
                return staged_entries
            except zipfile.BadZipFile as e:
                for staged in staged_entries:
                    await delete_file(staged)
                if raw_zip_entry:
                    await delete_file(raw_zip_entry)
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Invalid or corrupted zip file: {file.filename}"
                ) from e
            except Exception as e:
                for staged in staged_entries:
                    await delete_file(staged)
                if attempt >= attempts - 1:
                    if raw_zip_entry:
                        await delete_file(raw_zip_entry)
                    raise HTTPException(
                        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                        detail=f"Failed to process zip file {file.filename} after {attempts} attempts: {str(e)}"
                    ) from e
                delay = _zip_extract_backoff(attempt)
                logger.warning(
                    "Transient ZIP extraction error for %s (attempt %s/%s): %s. Retrying in %.2fs",
                    file.filename,
                    attempt + 1,
                    attempts,
                    e,
                    delay,
                )
                await asyncio.sleep(delay)
        return []

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


@router.get("/output-contract/template", status_code=status.HTTP_200_OK)
def get_output_contract_template(current_user=Depends(get_current_user)):
    """
    Universal output contract template for artifact-first write execution.

    Use `write_execution_mode` = platform so the job executor pushes the step artifact to each
    `write_targets[]` tool. Use `ui_only` to skip artifact files and contract writes (results only in DB/UI).

    Object stores, Snowflake/BigQuery, SQL Server, Postgres, and MySQL are supported for structured loads.

    Agent-driven SQL (reads and ad-hoc writes) still uses the interactive Postgres/MySQL tools
    with `query` / `params` only; that path is separate from this contract.
    """
    return {
        "version": "1.0",
        "record_schema": {
            "customer_id": "string",
            "decision": "string",
            "confidence": "number",
            "reason_codes": ["string"],
            "evidence_refs": ["string"],
            "processed_at": "datetime",
        },
        "write_policy": {
            "on_write_error": "fail_job",
            "min_successful_targets": 1,
        },
        "write_targets": [
            {
                "tool_name": "platform_1_snowflake_kyc_results",
                "operation_type": "upsert",
                "write_mode": "upsert",
                "merge_keys": ["customer_id"],
                "target": {
                    "database": "BANK",
                    "schema": "RISK",
                    "table": "KYC_AML_DECISIONS",
                },
                "options": {
                    "on_conflict": "update",
                },
            }
        ],
    }


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


def _validate_write_execution_mode(v: Optional[str]) -> str:
    s = (v or "platform").strip().lower()
    if s in ("platform", "agent", "ui_only"):
        return s
    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail="write_execution_mode must be 'platform', 'agent', or 'ui_only'",
    )


def _validate_output_artifact_format(v: Optional[str]) -> str:
    s = (v or "jsonl").strip().lower()
    if s in ("jsonl", "json"):
        return s
    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="output_artifact_format must be 'jsonl' or 'json'")


def _parse_json_form(value: Optional[str]) -> Optional[dict]:
    if value is None:
        return None
    raw = value.strip()
    if not raw:
        return None
    try:
        obj = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid JSON provided in output_contract") from exc
    if not isinstance(obj, dict):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="output_contract must be a JSON object")
    return obj


def _validate_output_contract_policy(contract: Optional[dict]) -> Optional[dict]:
    if contract is None:
        return None
    if not isinstance(contract, dict):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="output_contract must be a JSON object")
    policy = contract.get("write_policy")
    if policy is None:
        return contract
    if not isinstance(policy, dict):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="output_contract.write_policy must be an object")
    on_write_error = policy.get("on_write_error")
    if on_write_error is not None and str(on_write_error).strip().lower() not in ("fail_job", "continue"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="output_contract.write_policy.on_write_error must be 'fail_job' or 'continue'",
        )
    min_successful_targets = policy.get("min_successful_targets")
    if min_successful_targets is not None:
        try:
            v = int(min_successful_targets)
        except (TypeError, ValueError):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="output_contract.write_policy.min_successful_targets must be an integer >= 0",
            )
        if v < 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="output_contract.write_policy.min_successful_targets must be an integer >= 0",
            )
    return contract


def _parse_contract_json(raw: Optional[str]) -> Optional[dict]:
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None
    return data if isinstance(data, dict) else None


def _transition_job_status_if_current(
    db: Session,
    *,
    job_id: int,
    business_id: int,
    from_statuses: List[JobStatus],
    to_status: JobStatus,
    extra_updates: Optional[dict] = None,
) -> bool:
    updates = {"status": to_status}
    if extra_updates:
        updates.update(extra_updates)
    updated = (
        db.query(Job)
        .filter(
            Job.id == job_id,
            Job.business_id == business_id,
            Job.status.in_(from_statuses),
        )
        .update(updates, synchronize_session=False)
    )
    db.commit()
    return bool(updated)


@router.post("", response_model=JobResponse, status_code=status.HTTP_201_CREATED)
async def create_job(
    title: str = Form(...),
    description: Optional[str] = Form(None),
    schedule_timezone: Optional[str] = Form(None),
    schedule_scheduled_at: Optional[str] = Form(None),
    files: Optional[List[UploadFile]] = File(None),
    allowed_platform_tool_ids: Optional[str] = Form(None),
    allowed_connection_ids: Optional[str] = Form(None),
    tool_visibility: Optional[str] = Form(None),
    write_execution_mode: Optional[str] = Form("platform"),
    output_artifact_format: Optional[str] = Form("jsonl"),
    output_contract: Optional[str] = Form(None),
    current_user: User = Depends(get_current_business_user),
    db: Session = Depends(get_db)
):
    """Create a new job with optional file uploads and an optional schedule."""
    file_metadata = []

    platform_ids = _parse_int_list_form(allowed_platform_tool_ids)
    connection_ids = _parse_int_list_form(allowed_connection_ids)
    if (platform_ids and len(platform_ids)) or (connection_ids and len(connection_ids)):
        platform_ids, connection_ids = _validate_allowed_tools(db, current_user.id, platform_ids, connection_ids)

    tv = _validate_tool_visibility(tool_visibility) if tool_visibility else None
    write_mode = _validate_write_execution_mode(write_execution_mode)
    artifact_format = _validate_output_artifact_format(output_artifact_format)
    output_contract_obj = _validate_output_contract_policy(_parse_json_form(output_contract))
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
        write_execution_mode=write_mode,
        output_artifact_format=artifact_format,
        output_contract=json.dumps(output_contract_obj) if output_contract_obj is not None else None,
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

    # Optionally attach a one-time schedule at job creation time.
    # Wrapped in try/except so a schedule validation error (e.g. past date)
    # doesn't prevent the job itself from being created.
    if schedule_scheduled_at is not None:
        try:
            schedule_payload = JobScheduleCreate(
                scheduled_at=schedule_scheduled_at,
                timezone=schedule_timezone or "UTC",
            )
            schedule = JobSchedule(
                job_id=new_job.id,
                status=schedule_payload.status,
                timezone=schedule_payload.timezone,
                scheduled_at=schedule_payload.scheduled_at,
                next_run_time=schedule_payload.scheduled_at if schedule_payload.status == ScheduleStatus.ACTIVE else None,
            )
            db.add(schedule)
            # Creating a schedule puts the job in queue
            new_job.status = JobStatus.IN_QUEUE
            db.commit()
            db.refresh(schedule)

            svc = get_scheduler()
            if svc and schedule.status == ScheduleStatus.ACTIVE:
                svc.add_schedule(schedule.id, scheduled_at=schedule.scheduled_at, timezone=schedule.timezone)
        except Exception as exc:
            db.rollback()
            db.refresh(new_job)  # Restore actual DB state (status stays DRAFT)
            logger.warning("Failed to create inline schedule for job %s: %s", new_job.id, exc)

    # Build response with parsed files (remove paths for security)
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
        "write_execution_mode": new_job.write_execution_mode,
        "output_artifact_format": new_job.output_artifact_format,
        "output_contract": output_contract_obj,
    }
    return JobResponse(**response_data)


@router.post("/{job_id}/analyze-documents", status_code=status.HTTP_200_OK)
async def analyze_documents(
    job_id: int,
    current_user: User = Depends(get_current_business_user),
    db: Session = Depends(get_db)
):
    """Analyze uploaded documents and generate questions. Uses platform planner when configured; else hired agent."""
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
        await persist_brd_analysis_artifact(db, job.id, result)
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
    Generate clarifying questions from each workflow step's assigned agent (their API/A2A),
    using BRD documents and job context. Questions are tagged with workflow_step_id and agent_id.
    Requires each assigned agent to have an api_endpoint. Platform Agent Planner is not used here.
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

    had_endpoint = False
    for step in steps:
        ag = db.query(Agent).filter(Agent.id == step.agent_id).first()
        if ag and (ag.api_endpoint or "").strip():
            had_endpoint = True
            break
    if not had_endpoint:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No assigned agents have an API endpoint. Configure each agent's endpoint so they can ask clarification questions for their task.",
        )

    new_conversation = conversation_history.copy()
    existing_questions = {
        str(item.get("question", "")).strip()
        for item in new_conversation
        if item.get("type") == "question" and item.get("question")
    }
    seen_in_batch = set()
    added_questions = []
    analyzer = DocumentAnalyzer()

    for step in steps:
        agent = db.query(Agent).filter(Agent.id == step.agent_id).first()
        if not agent or not (agent.api_endpoint or "").strip():
            logger.info(
                "Skipping workflow step %s: agent %s has no API endpoint for Q&A",
                step.id,
                step.agent_id,
            )
            continue

        input_data = {}
        if step.input_data:
            try:
                input_data = json.loads(step.input_data)
            except (json.JSONDecodeError, TypeError):
                pass
        only_step = {
            "step_order": step.step_order,
            "agent_name": agent.name if agent else "Agent",
            "assigned_task": input_data.get("assigned_task", ""),
        }

        try:
            result = await analyzer.generate_workflow_clarification_questions(
                job_title=job.title or "",
                job_description=job.description,
                documents_content=documents_content,
                workflow_tasks=[only_step],
                conversation_history=new_conversation,
                agent_api_url=(agent.api_endpoint or "").strip(),
                agent_api_key=(agent.api_key or "").strip() or None,
                agent_llm_model=getattr(agent, "llm_model", None),
                agent_temperature=getattr(agent, "temperature", None),
                use_a2a=bool(getattr(agent, "a2a_enabled", False)),
                only_step=only_step,
            )
        except Exception as e:
            logger.warning(
                "Workflow clarification questions failed for step %s (agent %s): %s",
                step.id,
                step.agent_id,
                e,
            )
            continue

        for q in result.get("questions") or []:
            qs = str(q).strip() if q else ""
            if not qs or qs in existing_questions or qs in seen_in_batch:
                continue
            seen_in_batch.add(qs)
            existing_questions.add(qs)
            added_questions.append(qs)
            new_conversation.append({
                "type": "question",
                "question": qs,
                "answer": None,
                "timestamp": str(datetime.utcnow()),
                "workflow_step_id": step.id,
                "agent_id": agent.id,
                "agent_name": agent.name if agent else None,
            })

    questions = added_questions

    removed_unanswered_questions = 0
    if len(added_questions) == 0:
        # No clarification needed from workflow context. Drop stale unanswered
        # questions so the frontend can proceed to the next step cleanly.
        filtered = []
        for item in new_conversation:
            if item.get("type") == "question":
                ans = str(item.get("answer", "")).strip() if item.get("answer") is not None else ""
                if not ans:
                    removed_unanswered_questions += 1
                    continue
            filtered.append(item)
        new_conversation = filtered

    job.conversation = json.dumps(new_conversation)
    db.commit()

    return {
        "questions": questions,
        "added_questions": added_questions,
        "no_questions_needed": len(added_questions) == 0,
        "removed_unanswered_questions": removed_unanswered_questions,
        "conversation": new_conversation,
    }


@router.get("/planner/status")
def get_agent_planner_status(current_user: User = Depends(get_current_user)):
    """Platform Agent Planner configuration (Issue #62). No secrets returned."""
    from services.planner_llm import get_planner_public_meta, is_agent_planner_configured

    return {
        "configured": is_agent_planner_configured(),
        **get_planner_public_meta(),
    }


@router.get("/{job_id}/planner-artifacts", response_model=PlannerArtifactListResponse)
def list_job_planner_artifacts(
    job_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """List Postgres pointers to planner JSON in object storage (business job owner or assigned developer)."""
    job = db.query(Job).filter(Job.id == job_id).first()
    if not job:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    if not _user_can_access_job(job, current_user, db):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized")
    rows = (
        db.query(JobPlannerArtifact)
        .filter(JobPlannerArtifact.job_id == job_id)
        .order_by(JobPlannerArtifact.created_at.desc())
        .all()
    )
    return PlannerArtifactListResponse(
        items=[PlannerArtifactResponse.model_validate(r, from_attributes=True) for r in rows]
    )


@router.get("/{job_id}/planner-artifacts/{artifact_id}/raw")
async def download_job_planner_artifact_raw(
    job_id: int,
    artifact_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Return stored planner JSON (application/json). Business job owner or assigned developer only."""
    job = db.query(Job).filter(Job.id == job_id).first()
    if not job:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    if not _user_can_access_job(job, current_user, db):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized")
    row = (
        db.query(JobPlannerArtifact)
        .filter(JobPlannerArtifact.id == artifact_id, JobPlannerArtifact.job_id == job_id)
        .first()
    )
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Artifact not found")
    cached = await asyncio.to_thread(get_cached_planner_raw, job_id, artifact_id)
    if cached is not None:
        return Response(content=cached, media_type="application/json")
    try:
        data = await asyncio.to_thread(read_planner_artifact_bytes, row)
    except Exception as e:
        logger.warning("Failed to read planner artifact id=%s job_id=%s: %s", artifact_id, job_id, e)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Could not read artifact from storage",
        )
    await asyncio.to_thread(set_cached_planner_raw, job_id, artifact_id, data)
    return Response(content=data, media_type="application/json")


@router.get("/{job_id}/planner-pipeline", response_model=PlannerPipelineBundleResponse)
def get_job_planner_pipeline(
    job_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Read-only composed view: latest brd_analysis, task_split, and tool_suggestion JSON per job.
    Same auth as planner-artifacts; does not change storage or execution.
    """
    job = db.query(Job).filter(Job.id == job_id).first()
    if not job:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    if not _user_can_access_job(job, current_user, db):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized")
    payloads, row_ids = load_latest_planner_pipeline_payloads(db, job_id)
    return PlannerPipelineBundleResponse(
        job_id=job_id,
        brd_analysis=payloads.get("brd_analysis"),
        task_split=payloads.get("task_split"),
        tool_suggestion=payloads.get("tool_suggestion"),
        artifact_ids={
            "brd_analysis": row_ids.get("brd_analysis"),
            "task_split": row_ids.get("task_split"),
            "tool_suggestion": row_ids.get("tool_suggestion"),
        },
    )


@router.get("/filter-options", response_model=JobFilterOptions)
def get_job_filter_options(
    current_user: User = Depends(get_current_user),
):
    """Return all available filter/sort values for the job list endpoint.

    Frontend uses this to populate filter dropdowns dynamically — no
    hard-coded enum values needed on the client side.
    """
    statuses = [
        EnumOption(value=s.value, label=s.value.replace("_", " ").title())
        for s in JobStatus
    ]
    sort_options = [
        EnumOption(value="newest", label="Newest First"),
        EnumOption(value="oldest", label="Oldest First"),
    ]
    return JobFilterOptions(statuses=statuses, sort_options=sort_options)


@router.get("/queue/stats", status_code=status.HTTP_200_OK)
def get_runtime_queue_stats(
    current_user: User = Depends(get_current_user),
):
    """
    Queue runtime stats for production operations.
    Useful to validate Redis/Celery usage and parallel processing behavior.
    """
    return get_queue_stats()


@router.get("", response_model=List[JobResponse])
def list_jobs(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    job_status: Optional[str] = None,
    sort: Optional[str] = "newest",
):
    if current_user.role == UserRole.BUSINESS:
        query = db.query(Job).filter(Job.business_id == current_user.id)
    else:
        # Developers can see jobs where their agents are used
        query = db.query(Job).join(WorkflowStep).join(Agent).filter(
            Agent.developer_id == current_user.id
        )

    if job_status is not None:
        query = query.filter(Job.status == job_status)

    if sort == "oldest":
        query = query.order_by(Job.created_at.asc())
    else:
        query = query.order_by(Job.created_at.desc())

    jobs = query.distinct().all()
    
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
            "write_execution_mode": getattr(job, "write_execution_mode", "platform"),
            "output_artifact_format": getattr(job, "output_artifact_format", "jsonl"),
            "output_contract": _parse_contract_json(getattr(job, "output_contract", None)),
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
        "write_execution_mode": getattr(job, "write_execution_mode", "platform"),
        "output_artifact_format": getattr(job, "output_artifact_format", "jsonl"),
        "output_contract": _parse_contract_json(getattr(job, "output_contract", None)),
    }
    return JobResponse(**job_dict)


@router.put("/{job_id}", response_model=JobResponse)
async def update_job(
    job_id: int,
    title: Optional[str] = Form(None),
    description: Optional[str] = Form(None),
    new_status: Optional[str] = Form(None, alias="status"),
    files: Optional[List[UploadFile]] = File(None),
    allowed_platform_tool_ids: Optional[str] = Form(None),
    allowed_connection_ids: Optional[str] = Form(None),
    tool_visibility: Optional[str] = Form(None),
    write_execution_mode: Optional[str] = Form(None),
    output_artifact_format: Optional[str] = Form(None),
    output_contract: Optional[str] = Form(None),
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
    if job.status != JobStatus.DRAFT and new_status is None:
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
    if write_execution_mode is not None:
        job.write_execution_mode = _validate_write_execution_mode(write_execution_mode)
    if output_artifact_format is not None:
        job.output_artifact_format = _validate_output_artifact_format(output_artifact_format)
    if output_contract is not None:
        contract_obj = _validate_output_contract_policy(_parse_json_form(output_contract))
        job.output_contract = json.dumps(contract_obj) if contract_obj is not None else None

    # Update basic fields
    if title is not None:
        job.title = title
    if description is not None:
        job.description = description
    if new_status is not None:
        try:
            job.status = JobStatus(new_status)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid status: {new_status}"
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
            # New BRD invalidates existing workflow execution context. Clear old
            # workflow steps/communications so the user must rebuild workflow
            # from the latest requirements before executing.
            existing_steps = db.query(WorkflowStep).filter(WorkflowStep.job_id == job.id).all()
            for step in existing_steps:
                db.query(AgentCommunication).filter(
                    (AgentCommunication.from_workflow_step_id == step.id) |
                    (AgentCommunication.to_workflow_step_id == step.id)
                ).delete()
                db.delete(step)
            job.status = JobStatus.DRAFT
            job.total_cost = 0.0
            job.completed_at = None
            job.failure_reason = None
            old_files_to_cleanup = old_files
            logger.info(
                "BRD overwrite staged for job_id=%s business_id=%s old_files=%s new_files=%s cleared_steps=%s",
                job.id,
                current_user.id,
                len(old_files),
                len(staged_new_files),
                len(existing_steps),
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
            await persist_brd_analysis_artifact(db, job.id, result)
            db.commit()
            db.refresh(job)
            
            {
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
        "write_execution_mode": getattr(job, "write_execution_mode", "platform"),
        "output_artifact_format": getattr(job, "output_artifact_format", "jsonl"),
        "output_contract": _parse_contract_json(getattr(job, "output_contract", None)),
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
async def auto_split_workflow(
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
                "task_type": getattr(st, "task_type", None),
            }
            for st in body.step_tools
        ]
    tv = body.tool_visibility
    if tv is not None and tv not in ("full", "names_only", "none"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="tool_visibility must be 'full', 'names_only', or 'none'")
    if tv is not None:
        job.tool_visibility = _validate_tool_visibility(tv)
    if body.write_execution_mode is not None:
        job.write_execution_mode = _validate_write_execution_mode(body.write_execution_mode)
    if body.output_artifact_format is not None:
        job.output_artifact_format = _validate_output_artifact_format(body.output_artifact_format)
    if body.output_contract is not None:
        contract_obj = _validate_output_contract_policy(body.output_contract)
        job.output_contract = json.dumps(contract_obj) if contract_obj is not None else None
    db.commit()
    workflow_builder = WorkflowBuilder(db)
    preview = await workflow_builder.auto_split_workflow_async(
        job_id, body.agent_ids, workflow_mode=workflow_mode, step_tools=step_tools, tool_visibility=tv
    )
    return preview


class SuggestWorkflowToolsBody(BaseModel):
    """Agents in workflow order (same as auto-split). LLM: platform planner if configured, else first agent's endpoint."""
    agent_ids: List[int]


@router.get("/{job_id}/suggest-workflow-tools", include_in_schema=True)
def suggest_workflow_tools_get_hint(job_id: int):
    """
    Opening this URL in a browser sends GET; the real API is POST-only.
    Returns 405 so clients see a clear hint instead of a generic 404.
    """
    raise HTTPException(
        status_code=status.HTTP_405_METHOD_NOT_ALLOWED,
        detail='Method Not Allowed: use POST with JSON body {"agent_ids": [agent ids in workflow order]}.',
        headers={"Allow": "POST"},
    )


@router.post("/{job_id}/suggest-workflow-tools")
async def suggest_workflow_tools(
    job_id: int,
    body: SuggestWorkflowToolsBody,
    current_user: User = Depends(get_current_business_user),
    db: Session = Depends(get_db),
):
    """
    BRD-aware suggestion: which platform MCP tools to assign to each agent step (read vs write heuristics),
    plus an output_contract stub with write_targets using platform_{id}_* tool names.

    Uses the same document and Q&A context as auto-split. First selected agent must expose an OpenAI-compatible
    API (same as task splitter); otherwise a deterministic fallback split is returned.
    """
    job = db.query(Job).filter(Job.id == job_id).first()
    if not job:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    if job.business_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized")

    if not body.agent_ids:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="agent_ids is required")

    q = db.query(MCPToolConfig).filter(
        MCPToolConfig.user_id == current_user.id,
        MCPToolConfig.is_active == True,
    )
    if getattr(job, "allowed_platform_tool_ids", None):
        try:
            parsed = json.loads(job.allowed_platform_tool_ids)
            if isinstance(parsed, list) and len(parsed) > 0:
                q = q.filter(MCPToolConfig.id.in_([int(x) for x in parsed]))
        except (TypeError, ValueError, json.JSONDecodeError):
            pass
    platform_tools = q.order_by(MCPToolConfig.created_at.desc()).all()
    if not platform_tools:
        return {
            "step_suggestions": [],
            "output_contract_stub": None,
            "fallback_used": True,
            "detail": "No platform tools configured for this job (check job allowed tools or create MCP tools).",
        }

    rows = db.query(Agent).filter(Agent.id.in_(body.agent_ids)).all()
    by_id = {a.id: a for a in rows}
    agents_ordered = []
    for aid in body.agent_ids:
        if aid not in by_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Agent id {aid} not found",
            )
        agents_ordered.append(by_id[aid])

    conversation_data = None
    if job.conversation:
        try:
            conversation_data = json.loads(job.conversation)
        except (json.JSONDecodeError, TypeError):
            pass

    wb = WorkflowBuilder(db)
    documents_content = await wb.load_job_documents_content_async(job)
    splitter_agent = agents_ordered[0]

    llm_audit: Dict[str, Any] = {}
    result = await suggest_tool_assignments_for_agents(
        job_title=job.title or "",
        job_description=job.description or "",
        documents_content=documents_content,
        conversation_data=conversation_data,
        agents=agents_ordered,
        platform_tools=platform_tools,
        splitter_agent=splitter_agent,
        llm_audit=llm_audit,
    )
    if llm_audit.get("raw_llm_response"):
        aid = await persist_json_planner_artifact(
            db,
            job_id,
            "tool_suggestion",
            {
                "raw_llm_response": llm_audit["raw_llm_response"],
                "source": llm_audit.get("source"),
                "job_title": job.title or "",
                "job_description": job.description or "",
                "parsed_result": {
                    "step_suggestions": result.get("step_suggestions"),
                    "output_contract_stub": result.get("output_contract_stub"),
                    "fallback_used": result.get("fallback_used"),
                },
            },
        )
        if aid is not None:
            db.commit()
    return result


@router.post("/{job_id}/workflow/manual", response_model=WorkflowPreview)
async def manual_workflow(
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
    preview = await workflow_builder.create_manual_workflow_async(job_id, workflow_steps)
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
        "failure_reason": job.failure_reason,
        "write_execution_mode": getattr(job, "write_execution_mode", "platform"),
        "output_artifact_format": getattr(job, "output_artifact_format", "jsonl"),
        "output_contract": _parse_contract_json(getattr(job, "output_contract", None)),
    }
    return JobResponse(**job_dict)


@router.post("/{job_id}/execute", response_model=JobResponse)
def execute_job(
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
    
    if job.status != JobStatus.PENDING_APPROVAL:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Job must be approved before execution"
        )
    
    execution_token = uuid.uuid4().hex
    transitioned = _transition_job_status_if_current(
        db,
        job_id=job.id,
        business_id=current_user.id,
        from_statuses=[JobStatus.PENDING_APPROVAL],
        to_status=JobStatus.IN_PROGRESS,
        extra_updates={"execution_token": execution_token},
    )
    if not transitioned:
        db.refresh(job)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Job must be approved before execution",
        )
    db.refresh(job)
    
    # Queue async execution (Redis/Celery) with strict mode option.
    try:
        queue_job_execution(
            job_id=job_id,
            history_id=None,
            execution_token=execution_token,
            strict=bool(getattr(settings, "JOB_EXECUTION_STRICT_QUEUE", False)),
        )
    except Exception as exc:
        job.status = JobStatus.PENDING_APPROVAL
        job.execution_token = None
        job.failure_reason = f"Failed to enqueue execution: {str(exc)[:200]}"
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Failed to enqueue job execution. Please try again.",
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
        "write_execution_mode": getattr(job, "write_execution_mode", "platform"),
        "output_artifact_format": getattr(job, "output_artifact_format", "jsonl"),
        "output_contract": _parse_contract_json(getattr(job, "output_contract", None)),
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
        "write_execution_mode": getattr(job, "write_execution_mode", "platform"),
        "output_artifact_format": getattr(job, "output_artifact_format", "jsonl"),
        "output_contract": _parse_contract_json(getattr(job, "output_contract", None)),
    }

    # Compute schedule-aware fields for frontend UX
    schedule = db.query(JobSchedule).filter(JobSchedule.job_id == job.id).first()
    if schedule:
        job_dict["scheduled_at"] = schedule.scheduled_at
        if job.status == JobStatus.IN_PROGRESS and schedule.last_run_time:
            elapsed = datetime.utcnow() - schedule.last_run_time
            if elapsed > timedelta(hours=settings.STUCK_JOB_THRESHOLD_HOURS):
                job_dict["show_cancel_option"] = True

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


# ---------------------------------------------------------------------------
# Job Schedule — one schedule per job (singular endpoints)
#
# Workflow:
#   1. User creates a schedule → job status moves to IN_QUEUE.
#   2. Before scheduled time (IN_QUEUE): user can edit the schedule.
#   3. At scheduled time: job moves to IN_PROGRESS. No user actions.
#   4. After the job runs:
#      (a) Success (COMPLETED): no further action items.
#      (b) Failure (FAILED) or cancellation (CANCELLED): frontend shows
#          "Schedule Again" (PUT /schedule) or "Run Now" (POST /rerun).
# ---------------------------------------------------------------------------

@router.get("/schedules/filter-options", response_model=ScheduleFilterOptions)
def get_schedule_filter_options(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Return all available filter/sort values for the schedule list endpoint.

    Requires authentication. Results are scoped to jobs owned by the current
    user. Includes the user's jobs (id + title) for the job dropdown, schedule
    statuses, job statuses, and sort options — so the frontend never
    hard-codes enum values.
    """
    schedule_statuses = [
        EnumOption(value=s.value, label=s.value.replace("_", " ").title())
        for s in ScheduleStatus
    ]
    job_statuses = [
        EnumOption(value=s.value, label=s.value.replace("_", " ").title())
        for s in JobStatus
    ]
    sort_options = [
        EnumOption(value="newest", label="Newest First"),
        EnumOption(value="oldest", label="Oldest First"),
    ]
    # User's jobs for the job_id filter dropdown
    user_jobs = (
        db.query(Job.id, Job.title)
        .filter(Job.business_id == current_user.id)
        .order_by(Job.created_at.desc())
        .all()
    )
    jobs = [{"id": j.id, "title": j.title} for j in user_jobs]

    return ScheduleFilterOptions(
        schedule_statuses=schedule_statuses,
        job_statuses=job_statuses,
        sort_options=sort_options,
        jobs=jobs,
    )


@router.get("/schedules/all", response_model=ScheduleListResponse)
def list_all_schedules(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    job_id: Optional[int] = None,
    sort: Optional[str] = "newest",
    schedule_status: Optional[str] = None,
    job_status: Optional[str] = None,
    limit: int = Query(10, ge=1, le=100, description="Records per page"),
    offset: int = Query(0, ge=0, description="Pagination offset"),
):
    """List all schedules across all jobs owned by the current user.

    Supports filtering by job_id, schedule_status, job_status, and sorting
    by scheduled_at (newest/oldest). Paginated — default 10 per page.
    """
    query = (
        db.query(JobSchedule, Job.title, Job.status)
        .join(Job, Job.id == JobSchedule.job_id)
        .filter(Job.business_id == current_user.id)
    )
    if job_id is not None:
        query = query.filter(JobSchedule.job_id == job_id)
    if schedule_status is not None:
        query = query.filter(JobSchedule.status == schedule_status)
    if job_status is not None:
        query = query.filter(Job.status == job_status)

    if sort == "oldest":
        query = query.order_by(JobSchedule.scheduled_at.asc())
    else:
        query = query.order_by(JobSchedule.scheduled_at.desc())

    total = query.count()
    rows = query.offset(offset).limit(limit).all()
    results = []
    for schedule, job_title, js in rows:
        resp = JobScheduleResponse.model_validate(schedule, from_attributes=True)
        data = resp.model_dump()
        data["job_title"] = job_title
        data["job_status"] = js.value if hasattr(js, "value") else str(js)
        results.append(JobScheduleWithJobResponse(**data))
    return ScheduleListResponse(items=results, total=total, limit=limit, offset=offset)


@router.post("/{job_id}/schedule", response_model=ScheduleActionResponse, status_code=status.HTTP_201_CREATED)
def create_job_schedule(
    job_id: int,
    payload: JobScheduleCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Create a one-time schedule for a job. One schedule per job — returns 400 if one already exists.

    The schedule fires at scheduled_at (in the given timezone). After execution,
    the schedule is deactivated. If the job fails, the user can reschedule via PUT.
    Creating a schedule transitions the job to IN_QUEUE.
    """
    job = db.query(Job).filter(Job.id == job_id, Job.business_id == current_user.id).first()
    if not job:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found or you do not have access")

    # Block schedule creation while job is actively executing
    if job.status == JobStatus.IN_PROGRESS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot create schedule while job is in progress — wait for execution to complete",
        )

    existing = db.query(JobSchedule).filter(JobSchedule.job_id == job_id).first()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Schedule already exists for this job — use PUT to update it",
        )

    schedule = JobSchedule(
        job_id=job_id,
        status=payload.status,
        timezone=payload.timezone,
        scheduled_at=payload.scheduled_at,
        next_run_time=payload.scheduled_at if payload.status == ScheduleStatus.ACTIVE else None,
    )
    db.add(schedule)
    # Creating a schedule puts the job in queue (waiting for scheduled time)
    job.status = JobStatus.IN_QUEUE
    db.commit()
    db.refresh(schedule)

    if schedule.status == ScheduleStatus.ACTIVE:
        svc = get_scheduler()
        if svc:
            svc.add_schedule(schedule.id, scheduled_at=schedule.scheduled_at, timezone=schedule.timezone)

    return ScheduleActionResponse(
        message="Schedule created successfully",
        data=JobScheduleResponse.model_validate(schedule),
    )


@router.get("/{job_id}/schedule", response_model=JobScheduleResponse)
def get_job_schedule(
    job_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get the schedule for a job. Only the job owner can view it."""
    job = db.query(Job).filter(Job.id == job_id).first()
    if not job:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    if job.business_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized")

    schedule = db.query(JobSchedule).filter(JobSchedule.job_id == job_id).first()
    if not schedule:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No schedule found for this job")

    return schedule


@router.put("/{job_id}/schedule", response_model=ScheduleActionResponse)
def update_job_schedule(
    job_id: int,
    payload: JobScheduleUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Update the schedule for a job.

    Also used as "Schedule Again" after a job failure or cancellation — the frontend
    sends a new scheduled_at and optionally re-enables the schedule (status=active).
    Rescheduling a failed/cancelled job transitions it back to IN_QUEUE.
    """
    schedule = db.query(JobSchedule).join(Job).filter(
        JobSchedule.job_id == job_id,
        Job.business_id == current_user.id,
    ).first()
    if not schedule:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Schedule not found for this job")

    # Block modifications while job is actively executing
    job = db.query(Job).filter(Job.id == job_id).first()
    if job and job.status == JobStatus.IN_PROGRESS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot modify schedule while job is in progress — wait for execution to complete",
        )

    if payload.timezone is not None:
        schedule.timezone = payload.timezone
    if payload.scheduled_at is not None:
        schedule.scheduled_at = payload.scheduled_at
    if payload.status is not None:
        schedule.status = payload.status

    # Recompute next_run_time
    effective_status = payload.status if payload.status is not None else schedule.status
    if effective_status == ScheduleStatus.ACTIVE:
        schedule.next_run_time = schedule.scheduled_at
    else:
        schedule.next_run_time = None

    # Rescheduling a failed/cancelled job puts it back in queue
    if job and effective_status == ScheduleStatus.ACTIVE and job.status in (JobStatus.FAILED, JobStatus.CANCELLED):
        job.status = JobStatus.IN_QUEUE

    db.commit()
    db.refresh(schedule)

    # Sync with APScheduler
    svc = get_scheduler()
    if svc:
        if schedule.status == ScheduleStatus.ACTIVE:
            svc.update_schedule(schedule.id, scheduled_at=schedule.scheduled_at, timezone=schedule.timezone)
        else:
            svc.remove_schedule(schedule.id)

    return ScheduleActionResponse(
        message="Schedule updated successfully",
        data=JobScheduleResponse.model_validate(schedule),
    )


# ---------------------------------------------------------------------------
# Job Rerun (immediate re-execution of a failed or cancelled job)
# ---------------------------------------------------------------------------

@router.post("/{job_id}/rerun", response_model=RerunResponse)
def rerun_job(
    job_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Immediately re-execute a failed or cancelled job ("Run Now" button).

    Only available when job status is FAILED or CANCELLED. Resets all workflow
    steps, transitions to IN_PROGRESS, and triggers execution in a background thread.
    For "Schedule Again", the frontend uses PUT /schedule with a new scheduled_at.
    """
    job = db.query(Job).filter(Job.id == job_id, Job.business_id == current_user.id).first()
    if not job:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found or you do not have access")
    if job.status not in (JobStatus.FAILED, JobStatus.CANCELLED):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Can only rerun failed or cancelled jobs. Current status: {job.status.value}",
        )

    step_count = db.query(WorkflowStep).filter(WorkflowStep.job_id == job.id).count()
    if step_count == 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Job has no workflow steps — add a workflow before rerunning",
        )

    # Create execution history entry if a schedule exists (audit trail)
    history_id = None
    schedule = db.query(JobSchedule).filter(JobSchedule.job_id == job_id).first()
    if schedule:
        history = ScheduleExecutionHistory(
            schedule_id=schedule.id,
            job_id=job.id,
            status="started",
            triggered_by="manual_rerun",
        )
        db.add(history)
        db.flush()
        history_id = history.id

    # Acquire execution claim atomically, then reset steps for a clean run.
    execution_token = uuid.uuid4().hex
    transitioned = _transition_job_status_if_current(
        db,
        job_id=job.id,
        business_id=current_user.id,
        from_statuses=[JobStatus.FAILED, JobStatus.CANCELLED],
        to_status=JobStatus.IN_PROGRESS,
        extra_updates={"execution_token": execution_token},
    )
    if not transitioned:
        db.refresh(job)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Can only rerun failed or cancelled jobs. Current status: {job.status.value}",
        )

    db.refresh(job)
    reset_job_for_execution(db, job)
    db.commit()

    # Spawn execution thread — job is already IN_PROGRESS
    try:
        queue_job_execution(
            job_id=job.id,
            history_id=history_id,
            execution_token=execution_token,
            strict=bool(getattr(settings, "JOB_EXECUTION_STRICT_QUEUE", False)),
        )
    except Exception as exc:
        logger.exception("Failed to enqueue execution for job %s", job.id)
        job.status = JobStatus.FAILED
        job.execution_token = None
        job.failure_reason = f"Failed to start execution: {str(exc)[:200]}"
        if history_id:
            hist = db.query(ScheduleExecutionHistory).filter(ScheduleExecutionHistory.id == history_id).first()
            if hist:
                hist.status = "failed"
                hist.failure_reason = job.failure_reason
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Failed to enqueue job execution: {str(exc)[:200]}",
        )

    return RerunResponse(
        message="Job re-execution started",
        job_id=job.id,
        status=job.status.value,
    )


# ---------------------------------------------------------------------------
# Job Cancel (cancel a long-running in-progress job)
# ---------------------------------------------------------------------------

@router.post("/{job_id}/cancel", response_model=RerunResponse)
def cancel_job(
    job_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Cancel a job that is currently in progress.

    Only available when job status is IN_PROGRESS. Sets the job to CANCELLED
    with a failure reason. The frontend shows this option when the job has
    been running longer than STUCK_JOB_THRESHOLD_HOURS.
    """
    job = db.query(Job).filter(Job.id == job_id, Job.business_id == current_user.id).first()
    if not job:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found or you do not have access")
    if job.status != JobStatus.IN_PROGRESS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Can only cancel in-progress jobs. Current status: {job.status.value}",
        )

    job.status = JobStatus.CANCELLED
    job.execution_token = None
    job.failure_reason = "Cancelled by user"
    db.commit()

    return RerunResponse(
        message="Job cancelled",
        job_id=job.id,
        status=job.status.value,
    )


# ---------------------------------------------------------------------------
# Schedule Execution History
# ---------------------------------------------------------------------------

@router.get("/{job_id}/schedule/history", response_model=List[ScheduleExecutionHistoryResponse])
def get_schedule_history(
    job_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get execution history for a job's schedule (audit log).

    Returns all execution attempts ordered by most recent first.
    Includes started, completed, failed, skipped, and potentially_stuck entries.
    """
    job = db.query(Job).filter(Job.id == job_id).first()
    if not job:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    if job.business_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized")

    history = (
        db.query(ScheduleExecutionHistory)
        .filter(ScheduleExecutionHistory.job_id == job_id)
        .order_by(ScheduleExecutionHistory.started_at.desc())
        .all()
    )
    return history
