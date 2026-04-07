"""
Persist Agent Planner / BRD analysis JSON to object storage (or local uploads) and record a Postgres pointer.

Read path is GET object by bucket/key from DB row (Issue #62). Optional Redis cache for raw bytes
is applied in the download route (planner_artifact_cache).
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

from sqlalchemy.orm import Session

from core.config import settings
from models.job import JobPlannerArtifact
from services.job_file_storage import download_s3_bytes, persist_file
from services.planner_llm import is_agent_planner_configured

logger = logging.getLogger(__name__)

GENERIC_PLANNER_ARTIFACT_TYPES = frozenset({"task_split", "tool_suggestion"})

# Latest-one-of-each for composed API (read model).
PLANNER_PIPELINE_ARTIFACT_TYPES: Tuple[str, ...] = ("brd_analysis", "task_split", "tool_suggestion")


def attach_planner_meta(payload: Dict[str, Any], artifact_type: str) -> Dict[str, Any]:
    """
    Add provenance envelope under planner_meta without removing existing keys (backward compatible).
    """
    model = (getattr(settings, "AGENT_PLANNER_MODEL", None) or "").strip() or "unspecified"
    if not is_agent_planner_configured():
        model = "planner_disabled"
    out = dict(payload)
    out["planner_meta"] = {
        "schema_version": "planner_artifact.v1",
        "artifact_type": artifact_type,
        "planner_model": model,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    return out


def load_latest_planner_pipeline_payloads(
    db: Session, job_id: int
) -> Tuple[Dict[str, Optional[Dict[str, Any]]], Dict[str, Optional[int]]]:
    """
    Sync: for each pipeline artifact type, load the newest row by created_at and parse JSON.
    Returns (payloads_by_type, artifact_row_ids_by_type).
    """
    payloads: Dict[str, Optional[Dict[str, Any]]] = {k: None for k in PLANNER_PIPELINE_ARTIFACT_TYPES}
    row_ids: Dict[str, Optional[int]] = {k: None for k in PLANNER_PIPELINE_ARTIFACT_TYPES}
    for at in PLANNER_PIPELINE_ARTIFACT_TYPES:
        row = (
            db.query(JobPlannerArtifact)
            .filter(JobPlannerArtifact.job_id == job_id, JobPlannerArtifact.artifact_type == at)
            .order_by(JobPlannerArtifact.created_at.desc(), JobPlannerArtifact.id.desc())
            .first()
        )
        if not row:
            continue
        row_ids[at] = int(row.id)
        try:
            raw = read_planner_artifact_bytes(row)
            payloads[at] = json.loads(raw.decode("utf-8"))
        except Exception as exc:
            logger.warning(
                "planner_pipeline_read_fail job_id=%s artifact_type=%s id=%s error=%s",
                job_id,
                at,
                row.id,
                type(exc).__name__,
            )
            payloads[at] = None
    return payloads, row_ids


def _should_persist_brd_payload(result: Dict[str, Any]) -> bool:
    """Skip extraction-only responses with no LLM output."""
    analysis = (result.get("analysis") or "").strip()
    if not analysis:
        return False
    if "Document text extracted for job" in analysis and "Select and assign agents" in analysis:
        return False
    return bool(result.get("raw_response") or result.get("questions") or result.get("recommendations"))


async def _persist_planner_artifact_row(
    db: Session,
    job_id: int,
    artifact_type: str,
    payload_bytes: bytes,
) -> Optional[int]:
    """Write bytes via persist_file, insert job_planner_artifacts row. Returns row id or None. Does not commit."""
    try:
        name = f"planner-{artifact_type}-{uuid.uuid4().hex[:10]}.json"
        meta = await persist_file(name, payload_bytes, "application/json", job_id=job_id)
        storage = (meta.get("storage") or "local").strip().lower()
        if storage == "s3":
            bucket = (meta.get("bucket") or "").strip() or None
            key = (meta.get("key") or "").strip()
            if not key:
                logger.warning(
                    "artifact_write_fail job_id=%s artifact_type=%s reason=missing_s3_key",
                    job_id,
                    artifact_type,
                )
                return None
            object_key = key
        else:
            bucket = None
            object_key = (meta.get("path") or "").strip()
            if not object_key:
                logger.warning(
                    "artifact_write_fail job_id=%s artifact_type=%s reason=missing_local_path",
                    job_id,
                    artifact_type,
                )
                return None
            storage = "local"

        row = JobPlannerArtifact(
            job_id=job_id,
            artifact_type=artifact_type,
            storage=storage,
            bucket=bucket,
            object_key=object_key,
            byte_size=len(payload_bytes),
        )
        db.add(row)
        db.flush()
        logger.info(
            "artifact_write_ok job_id=%s artifact_type=%s artifact_id=%s byte_size=%s",
            job_id,
            artifact_type,
            row.id,
            len(payload_bytes),
        )
        return int(row.id)
    except Exception as exc:
        logger.warning(
            "artifact_write_fail job_id=%s artifact_type=%s error=%s",
            job_id,
            artifact_type,
            exc,
        )
        return None


async def persist_json_planner_artifact(
    db: Session,
    job_id: int,
    artifact_type: str,
    payload: Dict[str, Any],
) -> Optional[int]:
    """
    Persist arbitrary JSON audit payload (task_split, tool_suggestion). Does not commit.
    """
    if artifact_type not in GENERIC_PLANNER_ARTIFACT_TYPES:
        logger.warning("persist_json_planner_artifact: unsupported artifact_type=%s", artifact_type)
        return None
    try:
        wrapped = attach_planner_meta(payload, artifact_type)
        payload_bytes = json.dumps(wrapped, default=str).encode("utf-8")
    except (TypeError, ValueError) as exc:
        logger.warning(
            "artifact_write_fail job_id=%s artifact_type=%s reason=json_encode error=%s",
            job_id,
            artifact_type,
            exc,
        )
        return None
    return await _persist_planner_artifact_row(db, job_id, artifact_type, payload_bytes)


async def persist_brd_analysis_artifact(db: Session, job_id: int, result: Dict[str, Any]) -> Optional[int]:
    """
    Write full analysis result JSON via persist_file (S3/MinIO or local), insert job_planner_artifacts row.
    Returns new row id or None if skipped/failed. Does not commit.
    """
    if not _should_persist_brd_payload(result):
        return None
    try:
        wrapped = attach_planner_meta(result, "brd_analysis")
        payload_bytes = json.dumps(wrapped, default=str).encode("utf-8")
    except (TypeError, ValueError) as exc:
        logger.warning(
            "artifact_write_fail job_id=%s artifact_type=brd_analysis reason=json_encode error=%s",
            job_id,
            exc,
        )
        return None
    return await _persist_planner_artifact_row(db, job_id, "brd_analysis", payload_bytes)


def read_planner_artifact_bytes(row: JobPlannerArtifact) -> bytes:
    """Load JSON bytes from S3/MinIO or local filesystem (sync; call via asyncio.to_thread in routes)."""
    if (row.storage or "").lower() == "s3":
        return download_s3_bytes(
            {"storage": "s3", "bucket": row.bucket or "", "key": row.object_key}
        )
    from pathlib import Path

    return Path(row.object_key).read_bytes()
