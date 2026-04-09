import asyncio
import logging
import json
import hashlib
import hmac
import uuid
import time
from datetime import datetime
from typing import Any, Dict, List, Optional
import httpx
from sqlalchemy.orm import Session
from db.database import SessionLocal
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
from services.mcp_platform_naming import platform_tool_id_from_mcp_function_name
from core.artifact_contract import (
    extract_record_rows_from_agent_output,
    normalize_agent_output_for_artifact,
)
from schemas.executor_platform_payload import (
    enrich_executor_payload_trace_only,
    validate_and_enrich_executor_payload,
)
from schemas.sandhi_a2a_task import (
    NextAgentRef,
    ParallelExecutionContext,
    SandhiA2ATaskV1,
    task_envelope_to_dict,
)
from services.agent_tool_compatibility import filter_tools_for_agent, validate_tools_for_agent
from services.a2a_outbound_validation import validate_outbound_a2a_payload
from services.tool_assignment_engine import assign_tools_for_step, infer_task_type
from services.planner_llm import (
    resolve_runtime_planner_transport,
    set_planner_runtime_transport,
    reset_planner_runtime_transport,
)
from services.execution_heartbeat import publish_step_heartbeat
from services.business_job_alerts import send_business_job_alert

logger = logging.getLogger(__name__)


def _extract_token_usage_from_payload(payload: Any) -> Optional[Dict[str, int]]:
    """Best-effort token usage extraction across OpenAI/A2A response shapes."""
    if not isinstance(payload, dict):
        return None

    def _as_nonneg_int(v: Any) -> int:
        try:
            return max(0, int(v))
        except Exception:
            return 0

    candidates: List[Any] = [
        payload.get("usage"),
        payload.get("token_usage"),
        payload.get("usage_metadata"),
    ]
    response_meta = payload.get("response_metadata")
    if isinstance(response_meta, dict):
        candidates.append(response_meta.get("token_usage"))
        candidates.append(response_meta.get("usage"))
    raw_message = payload.get("raw_message")
    if isinstance(raw_message, dict):
        raw_meta = raw_message.get("metadata")
        if isinstance(raw_meta, dict):
            candidates.append(raw_meta.get("token_usage"))
            candidates.append(raw_meta.get("usage"))
    task_obj = payload.get("task")
    if isinstance(task_obj, dict):
        status_obj = task_obj.get("status")
        if isinstance(status_obj, dict):
            status_meta = status_obj.get("metadata")
            if isinstance(status_meta, dict):
                candidates.append(status_meta.get("token_usage"))
                candidates.append(status_meta.get("usage"))
    agent_output = payload.get("agent_output")
    if isinstance(agent_output, dict):
        candidates.extend(
            [
                agent_output.get("usage"),
                agent_output.get("token_usage"),
                agent_output.get("usage_metadata"),
            ]
        )
        ao_meta = agent_output.get("response_metadata")
        if isinstance(ao_meta, dict):
            candidates.append(ao_meta.get("token_usage"))

    for c in candidates:
        if not isinstance(c, dict):
            continue
        prompt = _as_nonneg_int(c.get("prompt_tokens") or c.get("input_tokens"))
        completion = _as_nonneg_int(c.get("completion_tokens") or c.get("output_tokens"))
        total = _as_nonneg_int(c.get("total_tokens"))
        if total == 0:
            total = prompt + completion
        if prompt > 0 or completion > 0 or total > 0:
            return {
                "prompt_tokens": prompt,
                "completion_tokens": completion,
                "total_tokens": total,
            }

    return None


def _estimate_token_usage_from_io(input_payload: Any, output_payload: Any) -> Optional[Dict[str, int]]:
    """
    Fallback token estimate when upstream/provider usage is unavailable.
    Uses a conservative chars/4 heuristic over serialized input and output.
    """
    try:
        in_text = json.dumps(input_payload, default=str, ensure_ascii=False) if input_payload is not None else ""
    except Exception:
        in_text = str(input_payload or "")
    try:
        out_text = json.dumps(output_payload, default=str, ensure_ascii=False) if output_payload is not None else ""
    except Exception:
        out_text = str(output_payload or "")

    prompt_tokens = max(0, int(round(len(in_text) / 4.0))) if in_text else 0
    completion_tokens = max(0, int(round(len(out_text) / 4.0))) if out_text else 0
    total_tokens = prompt_tokens + completion_tokens
    if total_tokens <= 0:
        return None
    return {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
    }


def _load_step_input_json(
    raw: Optional[str],
    *,
    job_id: int,
    step_id: int,
    step_order: int,
) -> Dict[str, Any]:
    """Parse WorkflowStep.input_data; raise ValueError with correlation context on failure."""
    if not raw or not str(raw).strip():
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ValueError(
            f"WorkflowStep input_data is not valid JSON "
            f"(job_id={job_id} workflow_step_id={step_id} step_order={step_order}): {e}"
        ) from e
    if not isinstance(data, dict):
        raise ValueError(
            f"WorkflowStep input_data must be a JSON object, got {type(data).__name__} "
            f"(job_id={job_id} workflow_step_id={step_id} step_order={step_order})"
        )
    return data


def _partition_workflow_waves(workflow_steps: List[WorkflowStep]) -> List[List[WorkflowStep]]:
    """
    Partition ordered workflow steps into execution waves.
    Each wave is a maximal consecutive group where every step after the first has
    depends_on_previous=False, so those steps may run concurrently without sharing
    previous_step_output. The first step in a wave may still depend on the prior wave.
    """
    if not workflow_steps:
        return []
    waves: List[List[WorkflowStep]] = []
    i = 0
    n = len(workflow_steps)
    while i < n:
        wave = [workflow_steps[i]]
        j = i
        while j + 1 < n and not getattr(workflow_steps[j + 1], "depends_on_previous", True):
            j += 1
            wave.append(workflow_steps[j])
        waves.append(wave)
        i = j + 1
    return waves


def _next_workflow_step(
    workflow_steps: List[WorkflowStep], current: WorkflowStep
) -> Optional[WorkflowStep]:
    """Next step by ``step_order`` (linear successor in the workflow DAG we execute)."""
    successors = [s for s in workflow_steps if s.step_order > current.step_order]
    if not successors:
        return None
    return min(successors, key=lambda s: s.step_order)


def _parallel_context_for_step(
    workflow_steps: List[WorkflowStep], step: WorkflowStep
) -> Optional[Dict[str, Any]]:
    waves = _partition_workflow_waves(workflow_steps)
    for wi, wave in enumerate(waves):
        ids = [s.id for s in wave]
        if step.id in ids:
            return {
                "wave_index": wi,
                "parallel_group_id": f"job-wave-{wi}",
                "concurrent_workflow_step_ids": ids,
                "depends_on_previous_wave": wi > 0,
            }
    return None


def _load_step_output_json(step: WorkflowStep) -> Optional[Any]:
    """Parse persisted output_data JSON for resume mode handoff."""
    raw = getattr(step, "output_data", None)
    if not raw:
        return None
    if isinstance(raw, (dict, list)):
        return raw
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except (TypeError, json.JSONDecodeError):
            return None
    return None


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
    """Strip artifact/output-contract keys models sometimes mix into interactive SQL tool calls."""
    tt = (tool_type or "").strip().lower()
    if tt not in ("postgres", "mysql", "sqlserver") or not isinstance(arguments, dict):
        return arguments
    out = {k: v for k, v in arguments.items() if k in ("query", "params")}
    return out


def _ensure_records_for_platform_write(
    output_data: Any,
    *,
    write_mode: str,
    write_targets: Any,
) -> Any:
    """
    For platform write-target jobs, require tabular rows to avoid writing narrative fallback text.
    Accepts empty records list, but rejects non-tabular prose payloads.
    """
    if write_mode != "platform" or not isinstance(write_targets, list) or not write_targets:
        return output_data
    if isinstance(output_data, dict) and isinstance(output_data.get("records"), list):
        return output_data
    rows = extract_record_rows_from_agent_output(output_data)
    if rows is None:
        raise ValueError(
            "Output contract requires structured tabular output with top-level `records` array; "
            "agent returned non-tabular content."
        )
    return {"records": rows}


def _normalize_placeholder_error_values(output_data: Any) -> Any:
    """
    Normalize low-quality placeholder strings from model summaries to null.
    Example: {"total_users": "Error retrieving data"} -> {"total_users": None}
    """
    if isinstance(output_data, dict):
        return {k: _normalize_placeholder_error_values(v) for k, v in output_data.items()}
    if isinstance(output_data, list):
        return [_normalize_placeholder_error_values(v) for v in output_data]
    if isinstance(output_data, str):
        s = output_data.strip().lower()
        if s in ("error retrieving data", "error retrieving"):
            return None
    return output_data


def _is_sql_programming_error_tool_result(tool_result: str) -> bool:
    t = str(tool_result or "")
    return "ProgrammingError" in t and "Error:" in t


def _sql_schema_discovery_query(tool_type: str) -> Optional[str]:
    tt = (tool_type or "").strip().lower()
    if tt == "sqlserver":
        return (
            "SELECT TOP 200 TABLE_SCHEMA, TABLE_NAME, COLUMN_NAME, DATA_TYPE "
            "FROM INFORMATION_SCHEMA.COLUMNS "
            "ORDER BY TABLE_SCHEMA, TABLE_NAME, ORDINAL_POSITION"
        )
    if tt == "postgres":
        return (
            "SELECT table_schema, table_name, column_name, data_type "
            "FROM information_schema.columns "
            "WHERE table_schema NOT IN ('information_schema', 'pg_catalog') "
            "ORDER BY table_schema, table_name, ordinal_position "
            "LIMIT 200"
        )
    if tt == "mysql":
        return (
            "SELECT TABLE_SCHEMA, TABLE_NAME, COLUMN_NAME, DATA_TYPE "
            "FROM INFORMATION_SCHEMA.COLUMNS "
            "WHERE TABLE_SCHEMA = DATABASE() "
            "ORDER BY TABLE_SCHEMA, TABLE_NAME, ORDINAL_POSITION "
            "LIMIT 200"
        )
    return None


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
        self._mcp_correlation_job_id: Optional[int] = None
        self._mcp_correlation_step_id: Optional[int] = None
        self._mcp_correlation_trace_id: Optional[str] = None

    def _sandhi_mcp_correlation_headers(self) -> Dict[str, str]:
        """HTTP headers for platform/BYO MCP calls — observability in platform-mcp-server logs."""
        h: Dict[str, str] = {}
        jid = getattr(self, "_mcp_correlation_job_id", None)
        sid = getattr(self, "_mcp_correlation_step_id", None)
        tid = getattr(self, "_mcp_correlation_trace_id", None)
        if jid is not None:
            h["X-Sandhi-Job-Id"] = str(jid)
        if sid is not None:
            h["X-Sandhi-Workflow-Step-Id"] = str(sid)
        if tid:
            h["X-Sandhi-Trace-Id"] = str(tid)
        return h

    def _emit_step_heartbeat(
        self,
        step: WorkflowStep,
        *,
        phase: str,
        reason_code: str,
        message: Optional[str] = None,
        reason_detail: Optional[Dict[str, Any]] = None,
        attempt: Optional[int] = None,
        max_retries: Optional[int] = None,
        meaningful_progress: bool = False,
        commit_db: bool = False,
    ) -> None:
        publish_step_heartbeat(
            db=self.db,
            step=step,
            phase=phase,
            reason_code=reason_code,
            message=message,
            reason_detail=reason_detail,
            trace_id=self._mcp_correlation_trace_id,
            attempt=attempt,
            max_retries=max_retries,
            execution_token=getattr(getattr(step, "job", None), "execution_token", None),
            meaningful_progress=meaningful_progress,
            commit_db=commit_db,
        )

    def _emit_current_step_heartbeat(
        self,
        *,
        phase: str,
        reason_code: str,
        message: Optional[str] = None,
        reason_detail: Optional[Dict[str, Any]] = None,
        meaningful_progress: bool = False,
        commit_db: bool = False,
    ) -> None:
        sid = getattr(self, "_mcp_correlation_step_id", None)
        jid = getattr(self, "_mcp_correlation_job_id", None)
        if sid is None or jid is None:
            return
        try:
            step = (
                self.db.query(WorkflowStep)
                .filter(WorkflowStep.id == int(sid), WorkflowStep.job_id == int(jid))
                .first()
            )
            if not step:
                return
            self._emit_step_heartbeat(
                step,
                phase=phase,
                reason_code=reason_code,
                message=message,
                reason_detail=reason_detail,
                meaningful_progress=meaningful_progress,
                commit_db=commit_db,
            )
        except Exception:
            # Heartbeat path must not break execution.
            return

    def _is_retryable_step_exception(self, exc: BaseException) -> bool:
        if isinstance(exc, asyncio.TimeoutError):
            return True
        if isinstance(exc, httpx.HTTPStatusError):
            status = exc.response.status_code if exc.response is not None else None
            return status in {408, 409, 425, 429, 500, 502, 503, 504}
        if isinstance(exc, httpx.TransportError):
            return True
        msg = str(exc).lower()
        retryable_markers = (
            "timeout",
            "timed out",
            "temporarily unavailable",
            "temporary failure",
            "connection reset",
            "connection refused",
            "service unavailable",
            "too many requests",
            "rate limit",
            "gateway",
            "try again",
            "retryable_error",
        )
        return any(m in msg for m in retryable_markers)

    def _classify_endpoint_error(self, exc: BaseException) -> str:
        if isinstance(exc, asyncio.TimeoutError):
            return "timeout"
        if isinstance(exc, httpx.HTTPStatusError):
            code = exc.response.status_code if exc.response is not None else None
            if code == 429:
                return "throttled"
            if code is not None and 500 <= int(code) <= 599:
                return "upstream_5xx"
            if code is not None and 400 <= int(code) <= 499:
                return "upstream_4xx"
            return "http_error"
        if isinstance(exc, httpx.TransportError):
            return "transport_error"
        msg = str(exc).lower()
        if " 500" in msg or "internal server error" in msg:
            return "upstream_5xx"
        if " 429" in msg or "too many requests" in msg or "rate limit" in msg:
            return "throttled"
        if "timeout" in msg or "timed out" in msg:
            return "timeout"
        return "unknown"

    def _validate_agent_output_guardrails(self, output_data: Any) -> None:
        if output_data is None and getattr(settings, "AGENT_OUTPUT_REQUIRE_NONEMPTY", True):
            raise ValueError("Agent returned empty output")
        if isinstance(output_data, str) and getattr(settings, "AGENT_OUTPUT_REQUIRE_NONEMPTY", True):
            if not output_data.strip():
                raise ValueError("Agent returned empty text output")
        if isinstance(output_data, list) and getattr(settings, "AGENT_OUTPUT_REQUIRE_NONEMPTY", True):
            if len(output_data) == 0:
                raise ValueError("Agent returned empty list output")
        if not isinstance(output_data, dict):
            return

        status_val = str(output_data.get("status") or "").strip().lower()
        if status_val in {"retryable_error", "transient_error", "timeout"}:
            raise RuntimeError(f"retryable_error: status={status_val}")
        if status_val in {"failed", "error", "fatal_error"}:
            raise ValueError(f"fatal agent status={status_val}")

        min_conf = float(getattr(settings, "AGENT_OUTPUT_MIN_CONFIDENCE", 0.0) or 0.0)
        confidence = output_data.get("confidence")
        if isinstance(confidence, (int, float)):
            if float(confidence) < min_conf:
                raise ValueError(
                    f"Low confidence output: confidence={float(confidence):.3f} < min={min_conf:.3f}"
                )

        err_blob = output_data.get("error") or output_data.get("errors")
        has_useful_content = any(
            output_data.get(k) not in (None, "", [], {})
            for k in ("content", "result", "output", "text", "data", "records", "choices")
        )
        if err_blob and not has_useful_content:
            raise ValueError(f"Agent output only contains error: {err_blob}")

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
        extra_headers = {"X-MCP-Business-Id": str(business_id)}
        extra_headers.update(self._sandhi_mcp_correlation_headers())
        return await mcp_call_tool(
            base_url=settings.PLATFORM_MCP_SERVER_URL.rstrip("/"),
            tool_name=tool_name,
            arguments=arguments,
            endpoint_path="/mcp",
            extra_headers=extra_headers,
            timeout=timeout,
        )

    async def _execute_one_step_core(
        self,
        job_id: int,
        step_id: int,
        previous_chain_output: Optional[Any],
    ) -> Any:
        """Execute a single workflow step using this executor's DB session."""
        job = self.db.query(Job).filter(Job.id == job_id).first()
        if not job:
            raise ValueError("Job not found")
        workflow_steps = (
            self.db.query(WorkflowStep)
            .filter(WorkflowStep.job_id == job_id)
            .order_by(WorkflowStep.step_order)
            .all()
        )
        step = self.db.query(WorkflowStep).filter(
            WorkflowStep.id == step_id, WorkflowStep.job_id == job_id
        ).first()
        if not step:
            raise ValueError(f"Workflow step {step_id} not found for job {job_id}")

        self._mcp_correlation_job_id = job_id
        self._mcp_correlation_step_id = step.id
        self._mcp_correlation_trace_id = str(uuid.uuid4())
        logger.info(
            "workflow_step_mcp_context job_id=%s workflow_step_id=%s trace_id=%s",
            job_id,
            step.id,
            self._mcp_correlation_trace_id,
        )

        async def _run_step_body() -> Any:

            step.status = "in_progress"
            step.started_at = datetime.utcnow()
            self.db.commit()
            self._emit_step_heartbeat(
                step,
                phase="starting",
                reason_code="step_started",
                message="Workflow step execution started",
                meaningful_progress=True,
                commit_db=True,
            )

            self._log_action("workflow_step", step.id, "execution_started", {
                "job_id": job_id,
                "agent_id": step.agent_id
            })

            agent = self.db.query(Agent).filter(Agent.id == step.agent_id).first()
            if not agent:
                raise ValueError(f"Agent {step.agent_id} not found")

            depends_on_previous = getattr(step, "depends_on_previous", True)
            if previous_chain_output and depends_on_previous:
                base_input = _load_step_input_json(
                    step.input_data, job_id=job_id, step_id=step.id, step_order=step.step_order
                )
                input_data = {
                    **base_input,
                    "previous_step_output": previous_chain_output
                }
            else:
                input_data = _load_step_input_json(
                    step.input_data, job_id=job_id, step_id=step.id, step_order=step.step_order
                )

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

            job_platform_ids = _parse_allowed_ids(getattr(job, "allowed_platform_tool_ids", None))
            job_conn_ids = _parse_allowed_ids(getattr(job, "allowed_connection_ids", None))
            step_platform_ids = _parse_allowed_ids(getattr(step, "allowed_platform_tool_ids", None))
            step_conn_ids = _parse_allowed_ids(getattr(step, "allowed_connection_ids", None))
            # Step-level empty arrays mean "inherit job scope", not "no tools".
            if isinstance(step_platform_ids, list) and len(step_platform_ids) == 0:
                step_platform_ids = None
            if isinstance(step_conn_ids, list) and len(step_conn_ids) == 0:
                step_conn_ids = None
            effective_platform = step_platform_ids if step_platform_ids is not None else job_platform_ids
            effective_conn = step_conn_ids if step_conn_ids is not None else job_conn_ids
            if job_platform_ids is not None and effective_platform is not None:
                effective_platform = [x for x in effective_platform if x in job_platform_ids]
            if job_conn_ids is not None and effective_conn is not None:
                effective_conn = [x for x in effective_conn if x in job_conn_ids]
            tool_visibility = getattr(step, "tool_visibility", None) or getattr(job, "tool_visibility", None) or "full"
            has_configured_tool_scope = bool((effective_platform is not None and len(effective_platform) > 0) or (effective_conn is not None and len(effective_conn) > 0))
            if tool_visibility == "none" and has_configured_tool_scope:
                raise ValueError(
                    "Invalid tool configuration: step has MCP tool scope but tool_visibility='none'. "
                    "Set step/job tool_visibility to 'names_only' or 'full'."
                )

            available_mcp_tools = await self._get_available_mcp_tools_async(
                job.business_id,
                platform_tool_ids=effective_platform if effective_platform is not None else None,
                connection_ids=effective_conn if effective_conn is not None else None,
            )
            raw_mcp_tools = available_mcp_tools or []
            compat_errors = validate_tools_for_agent(agent, raw_mcp_tools)
            if compat_errors:
                raise ValueError(compat_errors[0])
            available_mcp_tools = filter_tools_for_agent(agent, raw_mcp_tools)
            visible_mcp_tools = _apply_tool_visibility(available_mcp_tools or [], tool_visibility)
            inp_for_assign = dict(input_data)
            if (
                getattr(settings, "TOOL_ASSIGNMENT_ENABLED", True)
                and getattr(settings, "TOOL_ASSIGNMENT_LLM_PICK_TOOLS", True)
                and getattr(settings, "TOOL_ASSIGNMENT_USE_LLM", True)
                and not inp_for_assign.get("llm_suggested_tool_names")
                and visible_mcp_tools
            ):
                try:
                    from services.planner_llm import is_agent_planner_configured
                    from services.tool_assignment_llm import suggest_tool_names_with_llm

                    if is_agent_planner_configured():
                        max_n = int(getattr(settings, "TOOL_ASSIGNMENT_LLM_MAX_TOOLS", 12) or 12)
                        max_n = max(1, min(max_n, len(visible_mcp_tools)))
                        picked = await suggest_tool_names_with_llm(
                            job_title=str(inp_for_assign.get("job_title") or ""),
                            assigned_task=str(inp_for_assign.get("assigned_task") or ""),
                            task_type=infer_task_type(inp_for_assign),
                            tools=visible_mcp_tools,
                            max_names=max_n,
                        )
                        if picked:
                            inp_for_assign["llm_suggested_tool_names"] = picked
                except Exception as e:
                    logger.warning("tool_assignment_llm_pick_failed: %s", e)
            ordered_tools, assigned_meta, assign_src, assign_flagged = assign_tools_for_step(
                input_data=inp_for_assign,
                agent=agent,
                available_mcp_tools=visible_mcp_tools,
            )
            picked_names = inp_for_assign.get("llm_suggested_tool_names")
            if picked_names is not None:
                input_data["llm_suggested_tool_names"] = picked_names
            input_data["assigned_tools"] = [
                m.model_dump(mode="python", exclude_none=True) for m in assigned_meta
            ]
            nxt_step = _next_workflow_step(workflow_steps, step)
            next_ref: Optional[NextAgentRef] = None
            if nxt_step:
                next_agent_row = self.db.query(Agent).filter(Agent.id == nxt_step.agent_id).first()
                endpoint: Optional[str] = None
                if next_agent_row and getattr(next_agent_row, "a2a_enabled", False):
                    endpoint = (next_agent_row.api_endpoint or "").strip() or None
                next_ref = NextAgentRef(
                    agent_id=nxt_step.agent_id,
                    workflow_step_id=nxt_step.id,
                    name=(next_agent_row.name if next_agent_row else None),
                    a2a_endpoint=endpoint,
                    step_order=nxt_step.step_order,
                )
            par_raw = _parallel_context_for_step(workflow_steps, step)
            par_ctx = ParallelExecutionContext(**par_raw) if par_raw else None
            sandhi_task = SandhiA2ATaskV1(
                agent_id=agent.id,
                task_id=f"{job_id}-{step.id}-{self._mcp_correlation_trace_id}",
                payload={
                    "assigned_task": input_data.get("assigned_task"),
                    "job_title": input_data.get("job_title"),
                    "job_id": job_id,
                    "workflow_step_id": step.id,
                    "step_order": step.step_order,
                },
                next_agent=next_ref,
                assigned_tools=assigned_meta,
                parallel=par_ctx,
                task_type=infer_task_type(input_data),
                assignment_source=assign_src,
                assignment_flagged=assign_flagged,
            )
            input_data["sandhi_a2a_task"] = task_envelope_to_dict(sandhi_task)
            if ordered_tools:
                input_data["available_mcp_tools"] = ordered_tools
                input_data["business_id"] = job.business_id
                if step.step_order == 1:
                    self._log_action("job", job_id, "mcp_tool_discovery", {
                        "business_id": job.business_id,
                        "tool_count": len(ordered_tools),
                        "tool_names": [t.get("name") for t in ordered_tools[:20]],
                    })

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

            documents = input_data.get('documents', [])
            conversation = input_data.get('conversation', [])

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

            if documents:
                logger.debug("Found %s document(s) to send to agent", len(documents))
                for i, doc in enumerate(documents):
                    content_length = len(doc.get('content', '')) if doc.get('content') else 0
                    content_preview = doc.get('content', '')[:150] if doc.get('content') else 'EMPTY'
                    logger.debug("Document %s: %s type=%s content_length=%s preview=%s...", i + 1, doc.get('name', 'Unknown'), doc.get('type', 'unknown'), content_length, content_preview)
            else:
                logger.warning("No documents found in input_data")

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

            output_data = None
            token_usage: Optional[Dict[str, int]] = None
            token_usage_source: Optional[str] = None
            mcp_tools_used: List[str] = []
            artifact_ref = None
            guardrail_meta: Dict[str, Any] = {
                "timeout_seconds": float(getattr(settings, "AGENT_STEP_TIMEOUT_SECONDS", 180.0) or 180.0),
                "max_retries": max(1, int(getattr(settings, "AGENT_STEP_MAX_RETRIES", 2) or 2)),
                "attempts_used": 0,
                "retryable_failures": 0,
            }
            contract = _parse_output_contract(getattr(job, "output_contract", None))
            write_mode = (getattr(job, "write_execution_mode", None) or "platform").strip().lower()
            write_targets: List[Dict[str, Any]] = contract.get("write_targets") if isinstance(contract, dict) else []
            write_policy = _parse_write_policy(contract, len(write_targets) if isinstance(write_targets, list) else 0)
            write_results: List[Dict[str, Any]] = []
            input_data["output_contract"] = contract
            input_data["write_execution_mode"] = write_mode
            input_data["write_targets"] = write_targets
            if getattr(settings, "EXECUTOR_PAYLOAD_VALIDATE", True):
                input_data = validate_and_enrich_executor_payload(
                    input_data,
                    job_id=job_id,
                    workflow_step_id=step.id,
                    step_order=step.step_order,
                    agent_id=agent.id,
                    total_steps=len(workflow_steps),
                )
            else:
                input_data = enrich_executor_payload_trace_only(
                    input_data,
                    job_id=job_id,
                    workflow_step_id=step.id,
                    step_order=step.step_order,
                    agent_id=agent.id,
                    total_steps=len(workflow_steps),
                )
            self._emit_step_heartbeat(
                step,
                phase="planning",
                reason_code="payload_ready",
                message="Payload prepared; calling agent",
                commit_db=True,
            )
            try:
                timeout_seconds = float(guardrail_meta["timeout_seconds"])
                max_retries = int(guardrail_meta["max_retries"])
                backoff_base = float(getattr(settings, "AGENT_STEP_RETRY_BACKOFF_SECONDS", 2.0) or 2.0)
                last_exc: Optional[BaseException] = None
                for attempt in range(1, max_retries + 1):
                    guardrail_meta["attempts_used"] = attempt
                    try:
                        self._emit_step_heartbeat(
                            step,
                            phase="calling_agent",
                            reason_code="agent_call_start",
                            message=f"Calling agent (attempt {attempt}/{max_retries})",
                            reason_detail={
                                "kind": "agent_call",
                                "attempt": attempt,
                                "max_retries": max_retries,
                                "timeout_seconds": timeout_seconds,
                            },
                            attempt=attempt,
                            max_retries=max_retries,
                            commit_db=True,
                        )
                        started = time.perf_counter()
                        raw_candidate = await asyncio.wait_for(
                            self._execute_agent(agent, input_data),
                            timeout=timeout_seconds,
                        )
                        elapsed_ms = int((time.perf_counter() - started) * 1000.0)
                        usage_candidate = _extract_token_usage_from_payload(raw_candidate)
                        if usage_candidate:
                            token_usage = usage_candidate
                            token_usage_source = "reported"
                        tools_used_candidate = raw_candidate.get("mcp_tools_used") if isinstance(raw_candidate, dict) else None
                        if isinstance(tools_used_candidate, list):
                            mcp_tools_used = [
                                str(x).strip()
                                for x in tools_used_candidate
                                if isinstance(x, str) and str(x).strip()
                            ]
                        candidate = raw_candidate
                        candidate = normalize_agent_output_for_artifact(candidate)
                        candidate = _normalize_placeholder_error_values(candidate)
                        candidate = _ensure_records_for_platform_write(
                            candidate,
                            write_mode=write_mode,
                            write_targets=write_targets,
                        )
                        self._validate_agent_output_guardrails(candidate)
                        output_data = candidate
                        self._emit_step_heartbeat(
                            step,
                            phase="calling_agent",
                            reason_code="agent_response_received",
                            message=f"Agent response received (attempt {attempt}/{max_retries})",
                            reason_detail={"kind": "agent_call", "attempt": attempt, "max_retries": max_retries, "elapsed_ms": elapsed_ms},
                            attempt=attempt,
                            max_retries=max_retries,
                            meaningful_progress=True,
                            commit_db=True,
                        )
                        break
                    except Exception as exec_err:
                        last_exc = exec_err
                        is_retryable = self._is_retryable_step_exception(exec_err)
                        if is_retryable:
                            guardrail_meta["retryable_failures"] = int(guardrail_meta["retryable_failures"]) + 1
                        if attempt < max_retries and is_retryable:
                            sleep_s = backoff_base * (2 ** (attempt - 1))
                            self._emit_step_heartbeat(
                                step,
                                phase="retry_backoff",
                                reason_code="retryable_error_backoff",
                                message=(
                                    f"Retryable {type(exec_err).__name__}; "
                                    f"backoff {sleep_s:.2f}s before retry"
                                ),
                                reason_detail={
                                    "kind": "backoff",
                                    "error_type": type(exec_err).__name__,
                                    "attempt": attempt,
                                    "max_retries": max_retries,
                                    "sleep_seconds": sleep_s,
                                },
                                attempt=attempt,
                                max_retries=max_retries,
                                commit_db=True,
                            )
                            logger.warning(
                                "step_guardrail_retry job_id=%s step_id=%s step_order=%s attempt=%s/%s sleep_seconds=%.2f reason=%s",
                                job_id,
                                step.id,
                                step.step_order,
                                attempt,
                                max_retries,
                                sleep_s,
                                type(exec_err).__name__,
                            )
                            await asyncio.sleep(sleep_s)
                            continue
                        raise exec_err
                if output_data is None and last_exc is not None:
                    raise last_exc
                if token_usage is None:
                    est = _estimate_token_usage_from_io(input_data, output_data)
                    if est:
                        token_usage = est
                        token_usage_source = "estimated"
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
                    self._emit_step_heartbeat(
                        step,
                        phase="writing_artifact",
                        reason_code="artifact_persist_started",
                        message="Persisting output artifact",
                        commit_db=True,
                    )
                    artifact_ref = await self._persist_output_artifact(job, step, output_data)
                    self._emit_step_heartbeat(
                        step,
                        phase="writing_artifact",
                        reason_code="artifact_persisted",
                        message="Output artifact persisted",
                        meaningful_progress=True,
                        commit_db=True,
                    )
                if write_mode == "platform" and isinstance(write_targets, list) and write_targets:
                    successful_writes = 0
                    for target in write_targets:
                        if not isinstance(target, dict):
                            continue
                        tool_name = str(target.get("tool_name", "")).strip() or None
                        try:
                            self._emit_step_heartbeat(
                                step,
                                phase="writing_artifact",
                                reason_code="platform_write_target_start",
                                message=f"Writing target via {tool_name or 'unknown_tool'}",
                                reason_detail={"tool_name": tool_name},
                                commit_db=True,
                            )
                            write_result = await self._trigger_platform_write(
                                business_id=job.business_id,
                                write_spec=target,
                                artifact_ref=artifact_ref,
                                step=step,
                            )
                            is_error_result = bool(
                                isinstance(write_result, dict) and (
                                    bool(write_result.get("isError"))
                                    or str(write_result.get("status", "")).strip().lower() in ("error", "failed")
                                )
                            )
                            if is_error_result:
                                write_results.append({
                                    "tool_name": tool_name,
                                    "status": "failed",
                                    "error": "platform write target returned isError=true",
                                    "result": write_result,
                                })
                                self._emit_step_heartbeat(
                                    step,
                                    phase="writing_artifact",
                                    reason_code="platform_write_target_error",
                                    message=f"Write target failed via {tool_name or 'unknown_tool'}",
                                    reason_detail={"tool_name": tool_name, "error_type": "platform_write_error"},
                                    commit_db=True,
                                )
                                if write_policy.get("on_write_error") == "fail_job":
                                    raise ValueError(
                                        f"Write target {tool_name or 'unknown_tool'} returned error response"
                                    )
                            else:
                                write_results.append({
                                    "tool_name": tool_name,
                                    "status": "success",
                                    "result": write_result,
                                })
                                successful_writes += 1
                                self._emit_step_heartbeat(
                                    step,
                                    phase="writing_artifact",
                                    reason_code="platform_write_target_success",
                                    message=f"Write target success via {tool_name or 'unknown_tool'}",
                                    reason_detail={"tool_name": tool_name},
                                    meaningful_progress=True,
                                    commit_db=True,
                                )
                        except Exception as target_error:
                            write_results.append({
                                "tool_name": tool_name,
                                "status": "failed",
                                "error": str(target_error),
                            })
                            if write_policy.get("on_write_error") == "fail_job":
                                raise
                    if successful_writes < int(write_policy.get("min_successful_targets", 0)):
                        raise ValueError(
                            "Write policy violation: "
                            f"successful_targets={successful_writes} < "
                            f"min_successful_targets={write_policy.get('min_successful_targets')}"
                        )

                step.output_data = json.dumps({
                    "agent_output": output_data,
                    "token_usage": token_usage,
                    "token_usage_source": token_usage_source,
                    "mcp_tools_used": mcp_tools_used,
                    "artifact_ref": artifact_ref,
                    "write_execution_mode": write_mode,
                    "write_policy": write_policy,
                    "write_results": write_results,
                    "guardrail_meta": guardrail_meta,
                })
                step.status = "completed"
                step.completed_at = datetime.utcnow()
                step.cost = agent.price_per_task
                self._emit_step_heartbeat(
                    step,
                    phase="completed",
                    reason_code="step_completed",
                    message="Workflow step completed",
                    meaningful_progress=True,
                    commit_db=False,
                )
            except Exception as step_error:
                step.status = "failed"
                step.completed_at = datetime.utcnow()
                error_msg = str(step_error)
                if len(error_msg) > 500:
                    error_msg = error_msg[:500] + "..."
                step.output_data = json.dumps({
                    "error": error_msg,
                    "agent_output": output_data,
                    "token_usage": token_usage,
                    "token_usage_source": token_usage_source,
                    "mcp_tools_used": mcp_tools_used,
                    "artifact_ref": artifact_ref,
                    "write_execution_mode": write_mode,
                    "write_policy": write_policy,
                    "write_results": write_results,
                    "guardrail_meta": guardrail_meta,
                })
                self._emit_step_heartbeat(
                    step,
                    phase="failed",
                    reason_code="step_failed",
                    message=f"Workflow step failed: {type(step_error).__name__}",
                    reason_detail={"error_type": type(step_error).__name__},
                    commit_db=False,
                )
                self.db.commit()

                raise Exception(f"Workflow step {step.step_order} failed: {error_msg}") from step_error

            if previous_chain_output is not None and getattr(step, "depends_on_previous", True):
                idx = next((i for i, w in enumerate(workflow_steps) if w.id == step.id), -1)
                if idx > 0:
                    previous_step = workflow_steps[idx - 1]
                    self._log_communication(previous_step, step, previous_chain_output)

            self.db.commit()

            self._log_action("workflow_step", step.id, "execution_completed", {
                "job_id": job_id,
                "agent_id": step.agent_id,
                "output_size": len(str(output_data))
            })
            return output_data

        try:
            return await _run_step_body()
        finally:
            self._mcp_correlation_job_id = None
            self._mcp_correlation_step_id = None
            self._mcp_correlation_trace_id = None

    async def execute_job(self, job_id: int):
        """Execute a job by running all workflow steps"""
        job = self.db.query(Job).filter(Job.id == job_id).first()
        if not job:
            raise ValueError("Job not found")
        pre_steps = (
            self.db.query(WorkflowStep.agent_id)
            .filter(WorkflowStep.job_id == job_id)
            .order_by(WorkflowStep.step_order)
            .all()
        )
        decision = resolve_runtime_planner_transport(
            self.db,
            agent_ids=[int(r[0]) for r in pre_steps if r and r[0] is not None],
        )
        planner_ctx_token = set_planner_runtime_transport(decision)
        try:
            # Execute-time replan before payment so job.total_cost matches the workflow we charge for.
            if getattr(settings, "AGENT_PLANNER_EXECUTE_REPLAN", True):
                from services.planner_llm import is_agent_planner_configured
                from services.task_splitter import PlannerSplitError
                from services.workflow_builder import WorkflowBuilder

                if is_agent_planner_configured():
                    origin = (getattr(job, "workflow_origin", None) or "auto_split").strip().lower()
                    if origin != "manual":
                        try:
                            await WorkflowBuilder(self.db).replan_workflow_steps_at_execute_async(job_id)
                        except PlannerSplitError as e:
                            policy = (
                                getattr(settings, "AGENT_PLANNER_EXECUTE_REPLAN_ON_FAILURE", None) or "fail"
                            ).strip().lower()
                            if policy == "continue":
                                logger.warning(
                                    "Execute-time replan failed for job_id=%s; continuing with built workflow: %s",
                                    job_id,
                                    e,
                                )
                            else:
                                job = self.db.query(Job).filter(Job.id == job_id).first()
                                if job:
                                    job.status = JobStatus.FAILED
                                    job.execution_token = None
                                    msg = (
                                        str(e)
                                        or "Platform task planner failed to refresh workflow before execution."
                                    )
                                    if getattr(e, "last_detail", None):
                                        msg = f"{msg} ({e.last_detail})"
                                    job.failure_reason = msg[:450]
                                    self.db.commit()
                                    try:
                                        send_business_job_alert(
                                            event_type="job_failed",
                                            job_id=int(job.id),
                                            business_id=int(job.business_id),
                                            title=str(job.title or f"Job {job.id}"),
                                            status="failed",
                                            reason=str(job.failure_reason or ""),
                                        )
                                    except Exception:
                                        pass
                                return
                    self.db.expire_all()

            try:
                self.payment_processor.calculate_job_cost(job_id)
            except ValueError:
                logger.warning("calculate_job_cost skipped: job %s missing", job_id)

            self.payment_processor.process_payment(job_id)
            
            # Get workflow steps in order
            workflow_steps = self.db.query(WorkflowStep).filter(
                WorkflowStep.job_id == job_id
            ).order_by(WorkflowStep.step_order).all()
            
            if not workflow_steps:
                job.status = JobStatus.FAILED
                job.execution_token = None
                job.failure_reason = "No workflow steps found for this job"
                self.db.commit()
                try:
                    send_business_job_alert(
                        event_type="job_failed",
                        job_id=int(job.id),
                        business_id=int(job.business_id),
                        title=str(job.title or f"Job {job.id}"),
                        status="failed",
                        reason=str(job.failure_reason or ""),
                    )
                except Exception:
                    pass
                return
            
            if getattr(settings, "WORKFLOW_PARALLEL_INDEPENDENT_STEPS", True):
                waves = _partition_workflow_waves(workflow_steps)
            else:
                waves = [[s] for s in workflow_steps]

            try:
                max_parallel = getattr(settings, "WORKFLOW_MAX_PARALLEL_STEPS", 8) or 8
                try:
                    max_parallel = max(1, int(max_parallel))
                except (TypeError, ValueError):
                    max_parallel = 8
                sem = asyncio.Semaphore(max_parallel)
                previous_chain_output: Optional[Any] = None

                async def _run_step_isolated(st: WorkflowStep, prev: Optional[Any]) -> Any:
                    async with sem:
                        child_db = SessionLocal()
                        try:
                            child_ex = AgentExecutor(child_db)
                            return await child_ex._execute_one_step_core(job_id, st.id, prev)
                        finally:
                            child_db.close()

                for wave in waves:
                    wave_sorted = sorted(wave, key=lambda s: s.step_order)
                    runnable = [st for st in wave_sorted if st.status != "completed"]
                    wave_outputs: Dict[int, Any] = {}
                    for st in wave_sorted:
                        if st.status == "completed":
                            out = _load_step_output_json(st)
                            if out is not None:
                                wave_outputs[st.id] = out
                    if not runnable:
                        last_step = wave_sorted[-1]
                        previous_chain_output = wave_outputs.get(last_step.id, previous_chain_output)
                        self.db.expire_all()
                        continue
                    if len(runnable) == 1:
                        st = runnable[0]
                        result = await self._execute_one_step_core(
                            job_id, st.id, previous_chain_output
                        )
                        wave_outputs[st.id] = result
                    else:
                        async with asyncio.TaskGroup() as tg:
                            task_objs = {
                                st.id: tg.create_task(_run_step_isolated(st, previous_chain_output))
                                for st in runnable
                            }
                        for st in runnable:
                            wave_outputs[st.id] = task_objs[st.id].result()
                    previous_chain_output = wave_outputs.get(wave_sorted[-1].id, previous_chain_output)
                    self.db.expire_all()

                # Mark job as completed
                job.status = JobStatus.COMPLETED
                job.execution_token = None
                job.completed_at = datetime.utcnow()
                self.db.commit()
                try:
                    send_business_job_alert(
                        event_type="job_completed",
                        job_id=int(job.id),
                        business_id=int(job.business_id),
                        title=str(job.title or f"Job {job.id}"),
                        status="completed",
                        stage="done",
                    )
                except Exception:
                    pass
                
                # Distribute earnings
                self.payment_processor.distribute_earnings(job_id)
                
                # Log job completion
                self._log_action("job", job_id, "completed", {
                    "total_cost": job.total_cost
                })
            
            except (Exception, BaseExceptionGroup) as caught:
                # TaskGroup raises ExceptionGroup (not a subclass of Exception); unwrap to one Exception for logging.
                err: BaseException = caught
                if isinstance(err, BaseExceptionGroup):
                    while isinstance(err, BaseExceptionGroup) and err.exceptions:
                        err = err.exceptions[0]
                if not isinstance(err, Exception):
                    raise caught
                e = err
                # Mark job as failed with reason
                logger.exception("Job execution failed job_id=%s", job_id)
                job.status = JobStatus.FAILED
                job.execution_token = None
                detail = str(e).strip()
                error_message = f"{type(e).__name__}: {detail}" if detail else type(e).__name__
                job.failure_reason = error_message[:2000]
                self.db.commit()
                try:
                    send_business_job_alert(
                        event_type="job_failed",
                        job_id=int(job.id),
                        business_id=int(job.business_id),
                        title=str(job.title or f"Job {job.id}"),
                        status="failed",
                        reason=str(job.failure_reason or ""),
                    )
                except Exception:
                    pass

                # Log error
                self._log_action("job", job_id, "failed", {
                    "error": error_message
                })
                raise e
        finally:
            reset_planner_runtime_transport(planner_ctx_token)
    
    async def _execute_agent(self, agent: Agent, input_data: Dict[str, Any]) -> Dict[str, Any]:
        """Execute agent via plugin, A2A protocol, or OpenAI-compatible (via platform adapter). Architecture runs A2A everywhere: native A2A agents or OpenAI endpoints via the adapter."""
        if agent.plugin_config:
            return await self._execute_plugin_agent(agent, input_data)
        validate_outbound_a2a_payload(input_data)
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
        started = time.perf_counter()
        try:
            result = await execute_via_a2a(
                url,
                input_data,
                api_key=api_key,
                blocking=True,
                timeout=120.0,
            )
        except Exception as exc:
            elapsed_ms = int((time.perf_counter() - started) * 1000.0)
            self._emit_current_step_heartbeat(
                phase="calling_agent",
                reason_code="agent_endpoint_error",
                message=f"A2A endpoint failed: {type(exc).__name__}",
                reason_detail={
                    "kind": "agent_call",
                    "endpoint_host": url,
                    "error_type": type(exc).__name__,
                    "error_class": self._classify_endpoint_error(exc),
                    "elapsed_ms": elapsed_ms,
                },
                commit_db=True,
            )
            raise
        elapsed_ms = int((time.perf_counter() - started) * 1000.0)
        self._emit_current_step_heartbeat(
            phase="calling_agent",
            reason_code="agent_endpoint_ok",
            message="A2A endpoint returned response",
            reason_detail={"kind": "agent_call", "endpoint_host": url, "elapsed_ms": elapsed_ms},
            meaningful_progress=True,
            commit_db=True,
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
        usage_totals = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        used_mcp_tools: set[str] = set()
        auto_schema_retry_used = False
        last_tool_name: Optional[str] = None
        same_tool_count: int = 0

        while round_idx < max_agent_rounds:
            round_idx += 1
            self._emit_current_step_heartbeat(
                phase="calling_agent",
                reason_code="adapter_round_start",
                message=f"A2A adapter round {round_idx}/{max_agent_rounds}",
                reason_detail={
                    "kind": "agent_call",
                    "loop": {"name": "adapter_tool_round", "round_idx": round_idx, "max_rounds": max_agent_rounds},
                },
                commit_db=True,
            )
            metadata = {
                "openai_url": url,
                "openai_api_key": api_key or "",
                "openai_model": model,
                "openai_messages": messages,
                # Deterministic tool-use behavior for stable SQL/MCP execution.
                "openai_temperature": 0.0 if openai_tools else (
                    float(getattr(agent, "temperature", 0.0))
                    if getattr(agent, "temperature", None) is not None
                    else 0.2
                ),
            }
            if openai_tools:
                metadata["openai_seed"] = int(getattr(settings, "AGENT_TOOLCALL_OPENAI_SEED", 42) or 42)
            if openai_tools:
                metadata["openai_tools"] = openai_tools
            started = time.perf_counter()
            result = await execute_via_a2a(
                adapter_url,
                input_data,
                api_key=None,
                blocking=True,
                timeout=120.0,
                adapter_metadata=metadata,
            )
            elapsed_ms = int((time.perf_counter() - started) * 1000.0)
            round_usage = _extract_token_usage_from_payload(result)
            if round_usage:
                usage_totals["prompt_tokens"] += int(round_usage.get("prompt_tokens") or 0)
                usage_totals["completion_tokens"] += int(round_usage.get("completion_tokens") or 0)
                usage_totals["total_tokens"] += int(round_usage.get("total_tokens") or 0)
            content = result.get("content") or ""
            tool_calls = result.get("tool_calls")
            if not tool_calls or not business_id:
                self._emit_current_step_heartbeat(
                    phase="calling_agent",
                    reason_code="adapter_round_completed",
                    message="Adapter returned final response (no tool calls)",
                    reason_detail={
                        "kind": "agent_call",
                        "elapsed_ms": elapsed_ms,
                        "loop": {"name": "adapter_tool_round", "round_idx": round_idx, "max_rounds": max_agent_rounds},
                    },
                    meaningful_progress=True,
                    commit_db=True,
                )
                break
            # Append assistant message with tool_calls
            messages.append({"role": "assistant", "content": content or None, "tool_calls": tool_calls})
            routing = {t.get("name"): t for t in (available_mcp_tools or []) if t.get("name")}
            for tc in tool_calls:
                tc_id = tc.get("id")
                fn = tc.get("function") or {}
                tool_name = fn.get("name")
                if tool_name and tool_name == last_tool_name:
                    same_tool_count += 1
                else:
                    same_tool_count = 1
                    last_tool_name = tool_name
                args_raw = fn.get("arguments") or "{}"
                try:
                    args = json.loads(args_raw) if isinstance(args_raw, str) else args_raw
                except (json.JSONDecodeError, TypeError):
                    args = {}
                tool_result = await self._invoke_mcp_tool(business_id, tool_name, args, routing)
                if isinstance(tool_name, str) and tool_name.strip():
                    used_mcp_tools.add(tool_name.strip())
                self._emit_current_step_heartbeat(
                    phase="calling_tool",
                    reason_code="tool_call_loop_observed",
                    message=f"Tool loop call {tool_name}",
                    reason_detail={
                        "kind": "tool_call",
                        "tool_name": tool_name,
                        "same_tool_count": same_tool_count,
                        "loop": {"name": "adapter_tool_round", "round_idx": round_idx, "max_rounds": max_agent_rounds},
                    },
                    commit_db=True,
                )
                tool_meta = routing.get(tool_name) or {}
                tool_result, used_now = await self._maybe_auto_discover_sql_schema_once(
                    business_id=business_id,
                    tool_name=tool_name,
                    tool_meta=tool_meta,
                    tool_result=tool_result,
                    already_used=auto_schema_retry_used,
                )
                auto_schema_retry_used = auto_schema_retry_used or used_now
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

        if usage_totals["total_tokens"] <= 0:
            usage_totals["total_tokens"] = usage_totals["prompt_tokens"] + usage_totals["completion_tokens"]
        out: Dict[str, Any] = {"content": content}
        if usage_totals["total_tokens"] > 0:
            out["token_usage"] = usage_totals
            out["usage"] = usage_totals
        if used_mcp_tools:
            out["mcp_tools_used"] = sorted(used_mcp_tools)
        return out

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

        started = time.perf_counter()
        async with httpx.AsyncClient(**client_kwargs) as client:
            response = await client.post(url, json=payload, headers=headers)
            elapsed_ms = int((time.perf_counter() - started) * 1000.0)

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
                err_class = (
                    "upstream_5xx"
                    if 500 <= int(response.status_code) <= 599
                    else ("throttled" if int(response.status_code) == 429 else "upstream_4xx")
                )
                self._emit_current_step_heartbeat(
                    phase="calling_agent",
                    reason_code="agent_endpoint_http_error",
                    message=f"Agent endpoint HTTP {response.status_code}",
                    reason_detail={
                        "kind": "agent_call",
                        "endpoint_host": url,
                        "http_status": int(response.status_code),
                        "error_class": err_class,
                        "elapsed_ms": elapsed_ms,
                    },
                    commit_db=True,
                )
                raise Exception(
                    f"Agent inference returned {response.status_code} {response.reason_phrase}. Response: {body}"
                )
            self._emit_current_step_heartbeat(
                phase="calling_agent",
                reason_code="agent_endpoint_http_ok",
                message=f"Agent endpoint HTTP {response.status_code}",
                reason_detail={
                    "kind": "agent_call",
                    "endpoint_host": url,
                    "http_status": int(response.status_code),
                    "elapsed_ms": elapsed_ms,
                },
                meaningful_progress=True,
                commit_db=True,
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
            if any(str(t.get("tool_type", "")).lower() in ("postgres", "mysql", "sqlserver") for t in available_mcp_tools):
                system_content += (
                    "CRITICAL — PostgreSQL/MySQL/SQL Server platform tools: call with ONLY the arguments allowed by the tool schema: "
                    "`query` (required) and optionally `params`. "
                    "Do NOT send write_mode, operation_type, target, artifact_ref, merge_keys, or idempotency_key with "
                    "these tools — those fields belong to the job output contract (e.g. MinIO/Snowflake artifact writes), "
                    "not to interactive SQL. "
                    "For SQL Server, use T-SQL syntax (e.g. SELECT TOP 100, schema-qualified [schema].[table], bracketed identifiers like [column_name]); "
                    "do NOT use LIMIT, backticks, or Postgres/MySQL-only syntax. "
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
            sql_tools_without_schema = [
                t for t in available_mcp_tools
                if str(t.get("tool_type", "")).lower() in ("postgres", "mysql", "sqlserver") and not t.get("schema_metadata")
            ]
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
                system_content += (
                    "Then invoke that tool with your SQL so the platform executes it and returns results; do not only show the SQL in your message. "
                    "Report only metrics you can compute from existing tables/columns in the discovered schema. "
                    "If a requested metric is not derivable from available schema, return null (or omit that key) instead of placeholder text like "
                    "'Error retrieving data'.\n\n"
                )
            elif sql_tools_without_schema:
                system_content += (
                    "CRITICAL — SQL schema metadata is not available for one or more SQL tools. "
                    "Before generating analytical insights, first discover schema via tool calls, then run analysis queries. "
                    "Start with INFORMATION_SCHEMA.TABLES and INFORMATION_SCHEMA.COLUMNS for the selected SQL tool. "
                    "Do not guess table/column names.\n\n"
                )
                system_content += (
                    "Example schema discovery (SQL Server): "
                    "{\"query\": \"SELECT TABLE_SCHEMA, TABLE_NAME FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_TYPE='BASE TABLE' ORDER BY TABLE_SCHEMA, TABLE_NAME\"}\n\n"
                )
        write_mode = (input_data.get("write_execution_mode") or "").strip().lower()
        write_targets = input_data.get("write_targets") or []
        if write_mode == "platform" and isinstance(write_targets, list) and write_targets:
            system_content += "═══════════════════════════════════════════════════════\n"
            system_content += "OUTPUT CONTRACT (STRICT)\n"
            system_content += "═══════════════════════════════════════════════════════\n\n"
            system_content += (
                "Your final answer MUST be valid JSON with top-level key `records` containing an array of objects. "
                "Do not return prose, markdown, or `{ \"content\": ... }` for this job. "
                "If a query fails, retry with corrected SQL using schema context. "
                "If no rows match, return `{ \"records\": [] }`.\n\n"
            )
            system_content += (
                "Example valid output:\n"
                "{\"records\":[{\"product_id\":980,\"queried_at\":\"2026-04-01T18:40:00Z\"}]}\n\n"
            )
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
            "chroma": (
                "Chroma vector store (per-user MCP config only; hits include sender/metadata for attribution; "
                "similarity search is not exact email match; Chroma API key for Cloud; OpenAI on tool for self-hosted embed fallback)"
            ),
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
                xh = self._sandhi_mcp_correlation_headers()
                res = await mcp_list_tools(
                    base_url=base_url,
                    endpoint_path=endpoint_path,
                    auth_type=c.auth_type or "none",
                    credentials=creds,
                    timeout=20.0,
                    extra_headers=xh if xh else None,
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
        started = time.perf_counter()
        self._emit_current_step_heartbeat(
            phase="calling_tool",
            reason_code="tool_call_start",
            message=f"Calling tool {tool_name}",
            reason_detail={"kind": "tool_call", "tool_name": tool_name},
            commit_db=True,
        )
        meta = routing.get(tool_name) if routing else None
        if not meta:
            return json.dumps({"error": f"Unknown tool {tool_name!r}. Not in this job's MCP tool list."})
        if meta.get("source") == "platform":
            parsed_id = platform_tool_id_from_mcp_function_name(tool_name)
            if parsed_id is None:
                return json.dumps({"error": "Invalid platform tool name"})
            reg_id = meta.get("platform_tool_id")
            if reg_id is not None and int(reg_id) != parsed_id:
                return json.dumps({"error": "Tool name does not match this job's registered MCP tool"})
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
            xh = self._sandhi_mcp_correlation_headers()
            try:
                logger.info(
                    "byo_mcp_tools_call connection_id=%s tool=%s job_id=%s workflow_step_id=%s trace_id=%s",
                    meta.get("connection_id"),
                    ext_name,
                    xh.get("X-Sandhi-Job-Id", "-"),
                    xh.get("X-Sandhi-Workflow-Step-Id", "-"),
                    xh.get("X-Sandhi-Trace-Id", "-"),
                )
                result = await mcp_call_tool(
                    base_url=conn.base_url.rstrip("/"),
                    tool_name=ext_name,
                    arguments=arguments or {},
                    endpoint_path=conn.endpoint_path or "/mcp",
                    auth_type=conn.auth_type or "none",
                    credentials=creds,
                    timeout=timeout,
                    extra_headers=xh if xh else None,
                )
            except Exception as e:
                elapsed_ms = int((time.perf_counter() - started) * 1000.0)
                logger.exception(
                    "BYO MCP tools/call failed connection_id=%s tool=%s job_id=%s workflow_step_id=%s",
                    meta.get("connection_id"),
                    ext_name,
                    xh.get("X-Sandhi-Job-Id"),
                    xh.get("X-Sandhi-Workflow-Step-Id"),
                )
                self._emit_current_step_heartbeat(
                    phase="calling_tool",
                    reason_code="tool_call_error",
                    message=f"Tool {ext_name} failed: {type(e).__name__}",
                    reason_detail={
                        "kind": "tool_call",
                        "tool_name": ext_name,
                        "error_type": type(e).__name__,
                        "elapsed_ms": elapsed_ms,
                        "tool_source": "external",
                        "connection_id": meta.get("connection_id"),
                    },
                    commit_db=True,
                )
                return json.dumps({"error": type(e).__name__})
            out = self._mcp_tool_result_to_text(result)
            elapsed_ms = int((time.perf_counter() - started) * 1000.0)
            self._emit_current_step_heartbeat(
                phase="calling_tool",
                reason_code="tool_call_result",
                message=f"Tool {ext_name} returned result",
                reason_detail={
                    "kind": "tool_call",
                    "tool_name": ext_name,
                    "elapsed_ms": elapsed_ms,
                    "tool_source": "external",
                    "connection_id": meta.get("connection_id"),
                },
                meaningful_progress=True,
                commit_db=True,
            )
            return out
        args = arguments if isinstance(arguments, dict) else {}
        if meta.get("source") == "platform":
            args = _sanitize_platform_sql_tool_arguments(str(meta.get("tool_type") or ""), args)
        out = await self._call_platform_mcp_tool(business_id, tool_name, args)
        elapsed_ms = int((time.perf_counter() - started) * 1000.0)
        self._emit_current_step_heartbeat(
            phase="calling_tool",
            reason_code="tool_call_result",
            message=f"Tool {tool_name} returned result",
            reason_detail={"kind": "tool_call", "tool_name": tool_name, "elapsed_ms": elapsed_ms, "tool_source": "platform"},
            meaningful_progress=True,
            commit_db=True,
        )
        return out

    async def _maybe_auto_discover_sql_schema_once(
        self,
        *,
        business_id: int,
        tool_name: str,
        tool_meta: Dict[str, Any],
        tool_result: str,
        already_used: bool,
    ) -> tuple[str, bool]:
        """
        Generic one-time fallback:
        on first SQL ProgrammingError from a platform SQL tool, run schema discovery query and
        append discovery output so the model can retry with corrected SQL in the next round.
        """
        if already_used:
            return tool_result, False
        if not isinstance(tool_meta, dict) or tool_meta.get("source") != "platform":
            return tool_result, False
        tool_type = str(tool_meta.get("tool_type") or "").strip().lower()
        if tool_type not in ("sqlserver", "postgres", "mysql"):
            return tool_result, False
        if not _is_sql_programming_error_tool_result(tool_result):
            return tool_result, False
        query = _sql_schema_discovery_query(tool_type)
        if not query:
            return tool_result, False
        try:
            discovery = await self._call_platform_mcp_tool(
                business_id,
                tool_name,
                {"query": query},
            )
        except Exception:
            logger.exception(
                "Auto schema discovery failed tool=%s tool_type=%s",
                tool_name,
                tool_type,
            )
            discovery = "Error: auto schema discovery failed"
        enriched = (
            f"{tool_result}\n\n"
            "[auto_schema_discovery_once]\n"
            f"{discovery}\n\n"
            "Retry with corrected SQL using the discovered schema above."
        )
        return enriched, True

    async def _call_platform_mcp_tool(
        self, business_id: int, tool_name: str, arguments: Dict[str, Any]
    ) -> str:
        """Invoke a platform MCP tool by name; returns result content as string for tool result message."""
        base = (getattr(settings, "PLATFORM_MCP_SERVER_URL", None) or "").strip().rstrip("/")
        if not base:
            return json.dumps({"error": "Platform MCP server not configured"})
        extra_headers = {"X-MCP-Business-Id": str(business_id)}
        extra_headers.update(self._sandhi_mcp_correlation_headers())
        corr = self._sandhi_mcp_correlation_headers()
        try:
            logger.info(
                "platform_mcp_tools_call tool_name=%s business_id=%s job_id=%s workflow_step_id=%s trace_id=%s",
                tool_name,
                business_id,
                corr.get("X-Sandhi-Job-Id", "-"),
                corr.get("X-Sandhi-Workflow-Step-Id", "-"),
                corr.get("X-Sandhi-Trace-Id", "-"),
            )
            result = await mcp_call_tool(
                base_url=base,
                tool_name=tool_name,
                arguments=arguments,
                endpoint_path="/mcp",
                timeout=60.0,
                extra_headers=extra_headers,
            )
        except Exception as e:
            logger.exception(
                "Platform MCP tools/call failed tool_name=%s business_id=%s job_id=%s workflow_step_id=%s",
                tool_name,
                business_id,
                corr.get("X-Sandhi-Job-Id"),
                corr.get("X-Sandhi-Workflow-Step-Id"),
            )
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
