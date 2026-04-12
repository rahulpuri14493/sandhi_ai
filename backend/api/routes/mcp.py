"""
MCP (Model Context Protocol) API: connections and tool configs per user.
Credentials stored encrypted; platform talks to MCP server via API (JSON-RPC proxy).
"""
import json
import logging
import uuid
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, status, BackgroundTasks
from sqlalchemy import func
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from typing import List, Optional

from db.database import get_db
from models.user import User
from models.audit_log import AuditLog
from models.mcp_server import MCPServerConnection, MCPToolConfig, MCPToolType, MCPWriteOperation
from schemas.mcp import (
    MCPServerConnectionCreate,
    MCPServerConnectionValidate,
    MCPServerConnectionUpdate,
    MCPServerConnectionResponse,
    MCPToolConfigCreate,
    MCPToolConfigUpdate,
    MCPToolConfigResponse,
    MCPProxyRequest,
    ValidateToolConfigRequest,
    MCPPlatformToolCallRequest,
    MCPPlatformWriteRequest,
    MCPWriteOperationResponse,
)
from core.security import get_current_business_user
from core.encryption import encrypt_json, decrypt_json
from db.database import SessionLocal
from services.http_url_guard import safe_url_host_for_logs
from services.mcp_platform_naming import platform_tool_id_from_mcp_function_name
from services.mcp_config_merge import merge_shallow_config, public_config_preview
from services.mcp_guardrails import (
    MCPGuardrailError,
    get_mcp_guardrails,
    guarded_mcp_jsonrpc,
    guarded_mcp_list_tools,
    infer_mcp_tool_operation_class,
    resolve_mcp_tenant_tier,
)
from api.routes import mcp_oauth as _mcp_oauth

router = APIRouter(prefix="/api/mcp", tags=["mcp"])
router.include_router(_mcp_oauth.router)
logger = logging.getLogger(__name__)


def _platform_tool_name_taken(
    db: Session,
    user_id: int,
    name: str,
    *,
    exclude_tool_id: Optional[int] = None,
) -> bool:
    """True if this business user already has a platform tool with the same name (trimmed, case-insensitive)."""
    norm = (name or "").strip()
    if not norm:
        return False
    q = db.query(MCPToolConfig.id).filter(
        MCPToolConfig.user_id == user_id,
        func.lower(MCPToolConfig.name) == norm.lower(),
    )
    if exclude_tool_id is not None:
        q = q.filter(MCPToolConfig.id != exclude_tool_id)
    return q.first() is not None


def _require_platform_tool_for_user(db: Session, user_id: int, tool_name: str) -> None:
    """Reject tool calls unless the platform tool id in tool_name belongs to this tenant."""
    pid = platform_tool_id_from_mcp_function_name(tool_name)
    if pid is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="tool_name must be a platform tool (e.g. platform_5_MyIndex)",
        )
    t = db.query(MCPToolConfig).filter(
        MCPToolConfig.id == pid,
        MCPToolConfig.user_id == user_id,
        MCPToolConfig.is_active == True,
    ).first()
    if not t:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Platform tool not found for this account",
        )


def _estimate_json_size_bytes(data: dict) -> int:
    try:
        return len(json.dumps(data, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))
    except Exception:
        return 0


def _http_exception_from_mcp_guardrail_error(ge: MCPGuardrailError) -> HTTPException:
    """Map MCPGuardrailError codes to HTTP status for platform HTTP routes."""
    # Detail is passed to RuntimeError.__init__; MCPGuardrailError does not store a .detail attribute.
    payload = {"error": ge.code, "message": str(ge)}
    if ge.code in ("mcp_quota_exceeded", "mcp_rate_limited"):
        return HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=payload)
    if ge.code in ("mcp_circuit_open", "mcp_upstream_unavailable"):
        # 503: dependency temporarily unavailable (breaker open or MCP host unreachable / bad HTTP).
        return HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=payload)
    if ge.code == "mcp_tool_validation_failed":
        return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=payload)
    if ge.code == "mcp_timeout":
        return HTTPException(status_code=status.HTTP_504_GATEWAY_TIMEOUT, detail=payload)
    return HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=payload)


def _op_to_response(op: MCPWriteOperation) -> MCPWriteOperationResponse:
    result_obj = None
    if op.response_payload:
        try:
            result_obj = json.loads(op.response_payload)
        except (TypeError, json.JSONDecodeError):
            result_obj = None
    return MCPWriteOperationResponse(
        operation_id=op.operation_id,
        idempotency_key=op.idempotency_key,
        tool_name=op.tool_name,
        status=op.status,
        result=result_obj,
        error_message=op.error_message,
        created_at=op.created_at,
        started_at=op.started_at,
        completed_at=op.completed_at,
    )


def _is_write_capable_tool_descriptor(tool: dict) -> bool:
    name = str((tool or {}).get("name", "")).lower()
    if any(token in name for token in ("write", "upsert", "merge", "insert", "put")):
        return True
    schema = (tool or {}).get("inputSchema") or (tool or {}).get("input_schema") or {}
    props = schema.get("properties") if isinstance(schema, dict) else {}
    op_type = (props or {}).get("operation_type") if isinstance(props, dict) else None
    if isinstance(op_type, dict):
        enums = [str(x).lower() for x in (op_type.get("enum") or [])]
        if any(x in enums for x in ("insert", "update", "upsert", "merge", "put", "write")):
            return True
    return False


def _normalize_platform_write_arguments(body: MCPPlatformWriteRequest) -> dict:
    return {
        "artifact_ref": body.artifact_ref.model_dump(),
        "target": body.target.model_dump(by_alias=True),
        "operation_type": body.operation_type,
        "write_mode": body.write_mode,
        "merge_keys": body.merge_keys,
        "idempotency_key": body.idempotency_key,
        "options": body.options or {},
    }


async def _invoke_platform_tool_call(
    body: MCPPlatformToolCallRequest,
    user_id: int,
    db: Session,
):
    """
    Shared implementation for platform tool invocation (HTTP route and call_platform_write).
    Uses an explicit Session so callers never pass FastAPI Depends() placeholders by mistake.
    """
    from core.config import settings
    if not settings.PLATFORM_MCP_SERVER_URL or not settings.MCP_INTERNAL_SECRET:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Platform MCP server not configured",
        )
    payload_bytes = _estimate_json_size_bytes(body.arguments or {})
    max_payload = max(1024, int(getattr(settings, "MCP_TOOL_MAX_ARGUMENT_BYTES", 5 * 1024 * 1024)))
    if payload_bytes > max_payload:
        raise HTTPException(
            status_code=413,
            detail=f"Tool arguments exceed max payload size ({payload_bytes} > {max_payload} bytes)",
        )
    _require_platform_tool_for_user(db, user_id, body.tool_name)
    default_timeout = float(getattr(settings, "MCP_TOOL_DEFAULT_TIMEOUT_SECONDS", 60.0))
    timeout = float(body.timeout_seconds or default_timeout)
    max_timeout = float(getattr(settings, "MCP_TOOL_MAX_TIMEOUT_SECONDS", 300.0))
    if timeout <= 0 or timeout > max_timeout:
        raise HTTPException(
            status_code=400,
            detail=f"timeout_seconds must be > 0 and <= {max_timeout}",
        )
    base = settings.PLATFORM_MCP_SERVER_URL.rstrip("/")
    extra_headers = {"X-MCP-Business-Id": str(user_id)}
    guard = get_mcp_guardrails()
    target_key = f"platform:{base}:/mcp:{body.tool_name}"
    operation_class = infer_mcp_tool_operation_class(body.tool_name, body.arguments or {})
    tenant_tier = resolve_mcp_tenant_tier(int(user_id))
    args = body.arguments if isinstance(body.arguments, dict) else {}
    idem_key = str(args.get("idempotency_key") or "")

    async def _platform_tool_execute(bounded_timeout: float):
        # Import inside coroutine so tests can patch services.mcp_client.call_tool.
        from services.mcp_client import call_tool

        return await call_tool(
            base_url=base,
            tool_name=body.tool_name,
            arguments=body.arguments or {},
            endpoint_path="/mcp",
            extra_headers=extra_headers,
            timeout=bounded_timeout,
        )

    try:
        return await guard.call_tool_with_guardrails(
            business_id=int(user_id),
            target_key=target_key,
            timeout_seconds=timeout,
            operation_class=operation_class,
            tool_name=body.tool_name,
            tenant_tier=tenant_tier,
            idempotency_key=idem_key,
            execute_call=_platform_tool_execute,
        )
    except MCPGuardrailError as ge:
        raise _http_exception_from_mcp_guardrail_error(ge) from ge
    except Exception as e:
        logger.error(
            "call_platform_tool failed tool_name=%s err_type=%s",
            body.tool_name,
            type(e).__name__,
        )
        raise HTTPException(
            status_code=502,
            detail=f"Platform tool call failed ({type(e).__name__})",
        )


def _run_platform_write_operation(operation_id: str, user_id: int) -> None:
    from core.config import settings
    from services.async_runner import run_coroutine_sync

    db = SessionLocal()
    try:
        op = db.query(MCPWriteOperation).filter(
            MCPWriteOperation.operation_id == operation_id,
            MCPWriteOperation.user_id == user_id,
        ).first()
        if not op or op.status in ("success", "failure"):
            return
        op.status = "in_progress"
        op.started_at = datetime.utcnow()
        db.commit()

        payload = json.loads(op.request_payload)
        timeout = float(payload.get("timeout_seconds") or getattr(settings, "MCP_TOOL_DEFAULT_TIMEOUT_SECONDS", 60.0))
        base = settings.PLATFORM_MCP_SERVER_URL.rstrip("/")
        extra_headers = {"X-MCP-Business-Id": str(user_id)}
        target_key = f"platform:{base}:/mcp:{op.tool_name}"
        tenant_tier = resolve_mcp_tenant_tier(int(user_id))
        idem_key = str(payload.get("idempotency_key") or op.idempotency_key or "")

        async def _execute_async_write(bounded_timeout: float):
            from services.mcp_client import call_tool

            return await call_tool(
                base_url=base,
                tool_name=op.tool_name,
                arguments=payload["arguments"],
                endpoint_path="/mcp",
                extra_headers=extra_headers,
                timeout=bounded_timeout,
            )

        async def _guarded_write():
            guard = get_mcp_guardrails()
            return await guard.call_tool_with_guardrails(
                business_id=int(user_id),
                target_key=target_key,
                timeout_seconds=timeout,
                operation_class="write_like",
                tool_name=op.tool_name,
                tenant_tier=tenant_tier,
                idempotency_key=idem_key,
                execute_call=_execute_async_write,
            )

        try:
            result = run_coroutine_sync(_guarded_write())
        except MCPGuardrailError as ge:
            op = db.query(MCPWriteOperation).filter(
                MCPWriteOperation.operation_id == operation_id,
                MCPWriteOperation.user_id == user_id,
            ).first()
            if op:
                op.status = "failure"
                op.error_message = f"{ge.code}: {str(ge)[:1950]}"
                op.completed_at = datetime.utcnow()
                db.commit()
            return

        op.status = "success"
        op.response_payload = json.dumps(result)
        op.completed_at = datetime.utcnow()
        db.commit()
    except Exception as e:
        logger.error(
            "Async platform write operation failed operation_id=%s err_type=%s",
            operation_id,
            type(e).__name__,
        )
        try:
            op = db.query(MCPWriteOperation).filter(
                MCPWriteOperation.operation_id == operation_id,
                MCPWriteOperation.user_id == user_id,
            ).first()
            if op:
                op.status = "failure"
                op.error_message = type(e).__name__[:2000]
                op.completed_at = datetime.utcnow()
                db.commit()
        except Exception:
            # Do not bubble background-task DB errors to request thread.
            pass
    finally:
        try:
            db.close()
        except Exception:
            pass


def _connection_to_response(c: MCPServerConnection) -> MCPServerConnectionResponse:
    return MCPServerConnectionResponse(
        id=c.id,
        user_id=c.user_id,
        name=c.name,
        base_url=c.base_url,
        endpoint_path=c.endpoint_path or "/mcp",
        auth_type=c.auth_type,
        is_platform_configured=c.is_platform_configured,
        is_active=c.is_active,
        created_at=c.created_at,
        updated_at=c.updated_at,
    )


def _tool_to_response(t: MCPToolConfig) -> MCPToolConfigResponse:
    schema_table_count = None
    if t.schema_metadata:
        try:
            data = json.loads(t.schema_metadata)
            schema_table_count = len(data.get("tables", []))
        except (TypeError, json.JSONDecodeError):
            pass
    return MCPToolConfigResponse(
        id=t.id,
        user_id=t.user_id,
        tool_type=t.tool_type.value,
        name=t.name,
        is_active=t.is_active,
        business_description=getattr(t, "business_description", None) or None,
        schema_metadata=t.schema_metadata,
        schema_table_count=schema_table_count,
        created_at=t.created_at,
        updated_at=t.updated_at,
    )


# --- Server connections (connect existing MCP server) ---


@router.post("/connections/validate")
async def validate_connection(
    body: MCPServerConnectionValidate,
    current_user: User = Depends(get_current_business_user),
    db: Session = Depends(get_db),
):
    """
    Test MCP server connectivity (JSON-RPC initialize) without saving.
    Returns { "valid": true, "message": "..." } or { "valid": false, "message": "..." }.
    """
    base_url = (body.base_url or "").strip().rstrip("/")
    if not base_url:
        return {"valid": False, "message": "Server URL is required"}
    endpoint_path = (body.endpoint_path or "/mcp").strip()
    if not endpoint_path.startswith("/"):
        endpoint_path = "/" + endpoint_path
    auth_type = body.auth_type or "none"
    credentials = body.credentials
    if body.connection_id is not None:
        conn = db.query(MCPServerConnection).filter(
            MCPServerConnection.id == int(body.connection_id),
            MCPServerConnection.user_id == current_user.id,
        ).first()
        if not conn:
            return {"valid": False, "message": "Connection not found"}
        stored_cred: dict = {}
        if conn.encrypted_credentials:
            try:
                raw = decrypt_json(conn.encrypted_credentials)
                if isinstance(raw, dict):
                    stored_cred = raw
            except Exception:
                pass
        credentials = merge_shallow_config(stored_cred, body.credentials or {})
    try:
        await guarded_mcp_jsonrpc(
            business_id=int(current_user.id),
            # Per-tenant ephemeral id (negative) — connection_id=0 would share one breaker across all users.
            connection_id=-(int(current_user.id) + 1),
            base_url=base_url,
            endpoint_path=endpoint_path,
            method="initialize",
            params={
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "sandhi-ai-mcp-validate", "version": "1.0.0"},
            },
            auth_type=auth_type,
            credentials=credentials,
            timeout_seconds=15.0,
            operation_class="read_like",
        )
        return {"valid": True, "message": "MCP server connection successful"}
    except MCPGuardrailError as ge:
        logger.warning(
            "MCP validate guardrail blocked base_url=%s endpoint=%s code=%s",
            base_url,
            endpoint_path,
            ge.code,
        )
        return {
            "valid": False,
            "message": f"MCP server unavailable ({ge.code}). Please verify the server URL, endpoint, and credentials.",
        }
    except Exception:
        logging.exception("MCP server connection validation failed for base_url=%s, endpoint_path=%s", base_url, endpoint_path)
        return {
            "valid": False,
            "message": "Failed to connect to MCP server. Please verify the server URL, endpoint, and credentials.",
        }


@router.post("/connections/{connection_id}/certify")
async def certify_connection_for_production(
    connection_id: int,
    current_user: User = Depends(get_current_business_user),
    db: Session = Depends(get_db),
):
    """
    BYO MCP certification probe:
    - initialize works
    - tools/list works
    - at least one write-capable tool is discoverable
    """
    conn = db.query(MCPServerConnection).filter(
        MCPServerConnection.id == connection_id,
        MCPServerConnection.user_id == current_user.id,
        MCPServerConnection.is_active == True,
    ).first()
    if not conn:
        raise HTTPException(status_code=404, detail="Connection not found")
    credentials = decrypt_json(conn.encrypted_credentials) if conn.encrypted_credentials else None
    checks = []
    try:
        await guarded_mcp_jsonrpc(
            business_id=int(current_user.id),
            connection_id=int(conn.id),
            base_url=conn.base_url,
            endpoint_path=conn.endpoint_path or "/mcp",
            method="initialize",
            params={
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "sandhi-ai-mcp-certify", "version": "1.0.0"},
            },
            auth_type=conn.auth_type,
            credentials=credentials,
            timeout_seconds=20.0,
            operation_class="read_like",
        )
        checks.append({"name": "initialize", "passed": True})
    except MCPGuardrailError as ge:
        logger.warning("MCP certify: initialize guardrail connection_id=%s code=%s", conn.id, ge.code)
        checks.append({"name": "initialize", "passed": False, "error": ge.code})
        return {"certified": False, "checks": checks, "recommended_policy": "fix_connection"}
    except Exception as e:
        logger.error(
            "MCP certify: initialize failed connection_id=%s err_type=%s",
            conn.id,
            type(e).__name__,
        )
        checks.append({"name": "initialize", "passed": False, "error": type(e).__name__})
        return {"certified": False, "checks": checks, "recommended_policy": "fix_connection"}

    try:
        tools_result = await guarded_mcp_list_tools(
            business_id=int(current_user.id),
            connection_id=int(conn.id),
            base_url=conn.base_url,
            endpoint_path=conn.endpoint_path or "/mcp",
            auth_type=conn.auth_type,
            credentials=credentials,
            timeout_seconds=20.0,
        )
        tools = tools_result.get("tools", []) if isinstance(tools_result, dict) else []
        checks.append({"name": "tools_list", "passed": True, "tool_count": len(tools)})
    except MCPGuardrailError as ge:
        logger.warning("MCP certify: tools/list guardrail connection_id=%s code=%s", conn.id, ge.code)
        checks.append({"name": "tools_list", "passed": False, "error": ge.code})
        return {"certified": False, "checks": checks, "recommended_policy": "fix_tools_list"}
    except Exception as e:
        logger.error(
            "MCP certify: tools/list failed connection_id=%s err_type=%s",
            conn.id,
            type(e).__name__,
        )
        checks.append({"name": "tools_list", "passed": False, "error": type(e).__name__})
        return {"certified": False, "checks": checks, "recommended_policy": "fix_tools_list"}

    write_capable = [t for t in tools if _is_write_capable_tool_descriptor(t)]
    checks.append({"name": "write_capability", "passed": len(write_capable) > 0, "write_tool_count": len(write_capable)})
    certified = len(write_capable) > 0
    return {
        "certified": certified,
        "checks": checks,
        "recommended_policy": "allow_read_write" if certified else "read_only_until_write_tool_added",
    }


@router.get("/connections", response_model=List[MCPServerConnectionResponse])
def list_connections(
    current_user: User = Depends(get_current_business_user),
    db: Session = Depends(get_db),
):
    """List current user's MCP server connections."""
    rows = db.query(MCPServerConnection).filter(
        MCPServerConnection.user_id == current_user.id
    ).order_by(MCPServerConnection.created_at.desc()).all()
    return [_connection_to_response(r) for r in rows]


@router.post("/connections", response_model=MCPServerConnectionResponse, status_code=status.HTTP_201_CREATED)
def create_connection(
    body: MCPServerConnectionCreate,
    current_user: User = Depends(get_current_business_user),
    db: Session = Depends(get_db),
):
    """Register a new MCP server connection. Credentials are stored encrypted."""
    encrypted = None
    if body.credentials:
        encrypted = encrypt_json(body.credentials)
    endpoint_path = (body.endpoint_path or "/mcp").strip()
    if not endpoint_path.startswith("/"):
        endpoint_path = "/" + endpoint_path
    conn = MCPServerConnection(
        user_id=current_user.id,
        name=body.name,
        base_url=body.base_url.strip().rstrip("/"),
        endpoint_path=endpoint_path,
        auth_type=body.auth_type or "none",
        encrypted_credentials=encrypted,
        is_platform_configured=False,
    )
    db.add(conn)
    db.commit()
    db.refresh(conn)
    return _connection_to_response(conn)


@router.get("/connections/{connection_id}", response_model=MCPServerConnectionResponse)
def get_connection(
    connection_id: int,
    current_user: User = Depends(get_current_business_user),
    db: Session = Depends(get_db),
):
    conn = db.query(MCPServerConnection).filter(
        MCPServerConnection.id == connection_id,
        MCPServerConnection.user_id == current_user.id,
    ).first()
    if not conn:
        raise HTTPException(status_code=404, detail="Connection not found")
    return _connection_to_response(conn)


@router.patch("/connections/{connection_id}", response_model=MCPServerConnectionResponse)
def update_connection(
    connection_id: int,
    body: MCPServerConnectionUpdate,
    current_user: User = Depends(get_current_business_user),
    db: Session = Depends(get_db),
):
    conn = db.query(MCPServerConnection).filter(
        MCPServerConnection.id == connection_id,
        MCPServerConnection.user_id == current_user.id,
    ).first()
    if not conn:
        raise HTTPException(status_code=404, detail="Connection not found")
    if body.name is not None:
        conn.name = body.name
    if body.base_url is not None:
        conn.base_url = body.base_url.strip().rstrip("/")
    if body.endpoint_path is not None:
        ep = body.endpoint_path.strip()
        conn.endpoint_path = ep if ep.startswith("/") else "/" + ep
    if body.auth_type is not None:
        conn.auth_type = body.auth_type
    if body.credentials is not None:
        prev: dict = {}
        if conn.encrypted_credentials:
            try:
                d = decrypt_json(conn.encrypted_credentials)
                if isinstance(d, dict):
                    prev = d
            except Exception:
                pass
        conn.encrypted_credentials = encrypt_json(merge_shallow_config(prev, body.credentials))
    if body.is_active is not None:
        conn.is_active = body.is_active
    db.commit()
    db.refresh(conn)
    return _connection_to_response(conn)


@router.delete("/connections/{connection_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_connection(
    connection_id: int,
    current_user: User = Depends(get_current_business_user),
    db: Session = Depends(get_db),
):
    conn = db.query(MCPServerConnection).filter(
        MCPServerConnection.id == connection_id,
        MCPServerConnection.user_id == current_user.id,
    ).first()
    if not conn:
        raise HTTPException(status_code=404, detail="Connection not found")
    db.delete(conn)
    db.commit()
    return None


# --- Platform tool configs (Vector DB, Postgres, File system) ---

@router.get("/tools", response_model=List[MCPToolConfigResponse])
def list_tools(
    current_user: User = Depends(get_current_business_user),
    db: Session = Depends(get_db),
):
    """List current user's platform MCP tool configs (no credentials in response)."""
    rows = db.query(MCPToolConfig).filter(
        MCPToolConfig.user_id == current_user.id
    ).order_by(MCPToolConfig.created_at.desc()).all()
    return [_tool_to_response(r) for r in rows]


@router.post("/tools/validate")
def validate_tool_config(
    body: ValidateToolConfigRequest,
    current_user: User = Depends(get_current_business_user),
    db: Session = Depends(get_db),
):
    """Validate tool config (test connection) before save. Does not store anything."""
    from services.mcp_validate import validate_tool_config as do_validate
    tool_type_str = (body.tool_type or "").strip().lower()
    try:
        expected_type = MCPToolType(tool_type_str)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid tool_type")
    cfg_in = body.config if isinstance(body.config, dict) else {}
    merged: dict = dict(cfg_in)
    if body.tool_id is not None:
        t = db.query(MCPToolConfig).filter(
            MCPToolConfig.id == int(body.tool_id),
            MCPToolConfig.user_id == current_user.id,
        ).first()
        if not t:
            raise HTTPException(status_code=404, detail="Tool config not found")
        if t.tool_type != expected_type:
            raise HTTPException(status_code=400, detail="tool_type does not match this tool_id")
        stored: dict = {}
        if t.encrypted_config:
            try:
                raw = decrypt_json(t.encrypted_config)
                if isinstance(raw, dict):
                    stored = raw
            except Exception:
                pass
        merged = merge_shallow_config(stored, cfg_in)
    valid, message = do_validate(tool_type_str, merged)
    return {"valid": valid, "message": message}


@router.post("/tools", response_model=MCPToolConfigResponse, status_code=status.HTTP_201_CREATED)
def create_tool(
    body: MCPToolConfigCreate,
    current_user: User = Depends(get_current_business_user),
    db: Session = Depends(get_db),
):
    """Add a platform tool config. Config (credentials) stored encrypted."""
    try:
        tool_type = MCPToolType((body.tool_type or "").strip().lower())
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=(
                "tool_type must be one of: vector_db, pinecone, weaviate, qdrant, chroma, "
                "postgres, mysql, sqlserver, snowflake, databricks, bigquery, elasticsearch, pageindex, "
                "filesystem, s3, minio, ceph, azure_blob, gcs, slack, teams, smtp, github, notion, rest_api"
            ),
        )
    encrypted = encrypt_json(body.config)
    business_description = (body.business_description or "").strip() or None
    if business_description and len(business_description) > 2000:
        business_description = business_description[:2000]
    display_name = (body.name or "").strip()
    if not display_name:
        raise HTTPException(status_code=400, detail="Tool name is required")
    if _platform_tool_name_taken(db, int(current_user.id), display_name):
        raise HTTPException(
            status_code=400,
            detail="A platform tool with this name already exists. Choose a different name.",
        )
    tool = MCPToolConfig(
        user_id=current_user.id,
        tool_type=tool_type,
        name=display_name,
        encrypted_config=encrypted,
        business_description=business_description,
    )
    db.add(tool)
    db.commit()
    db.refresh(tool)
    return _tool_to_response(tool)


@router.get("/tools/{tool_id}", response_model=MCPToolConfigResponse)
def get_tool(
    tool_id: int,
    current_user: User = Depends(get_current_business_user),
    db: Session = Depends(get_db),
):
    t = db.query(MCPToolConfig).filter(
        MCPToolConfig.id == tool_id,
        MCPToolConfig.user_id == current_user.id,
    ).first()
    if not t:
        raise HTTPException(status_code=404, detail="Tool config not found")
    base = _tool_to_response(t)
    if not t.encrypted_config:
        return base
    try:
        cfg = decrypt_json(t.encrypted_config)
    except Exception:
        return base
    if not isinstance(cfg, dict):
        return base
    updates: dict = {}
    preview = public_config_preview(cfg)
    if preview:
        updates["config_preview"] = preview
    if t.tool_type == MCPToolType.CHROMA:
        url = cfg.get("url")
        if isinstance(url, str) and url.strip():
            updates["chroma_url_preview"] = url.strip()[:2048]
    if t.tool_type == MCPToolType.WEAVIATE:
        c = cfg.get("weaviate_cluster_name") or cfg.get("cluster_name")
        if isinstance(c, str) and c.strip():
            updates["weaviate_cluster_preview"] = c.strip()[:256]
        idx = cfg.get("index_name") or cfg.get("class_name")
        if isinstance(idx, str) and idx.strip():
            updates["weaviate_class_preview"] = idx.strip()[:512]
    if updates:
        return base.model_copy(update=updates)
    return base


@router.patch("/tools/{tool_id}", response_model=MCPToolConfigResponse)
def update_tool(
    tool_id: int,
    body: MCPToolConfigUpdate,
    current_user: User = Depends(get_current_business_user),
    db: Session = Depends(get_db),
):
    t = db.query(MCPToolConfig).filter(
        MCPToolConfig.id == tool_id,
        MCPToolConfig.user_id == current_user.id,
    ).first()
    if not t:
        raise HTTPException(status_code=404, detail="Tool config not found")
    if body.name is not None:
        display_name = (body.name or "").strip()
        if not display_name:
            raise HTTPException(status_code=400, detail="Tool name cannot be empty")
        if _platform_tool_name_taken(db, int(current_user.id), display_name, exclude_tool_id=tool_id):
            raise HTTPException(
                status_code=400,
                detail="A platform tool with this name already exists. Choose a different name.",
            )
        t.name = display_name
    if body.config is not None:
        # Merge partial updates so omitted keys (e.g. secret fields left blank in UI)
        # are preserved instead of being dropped.
        current_cfg: dict = {}
        if t.encrypted_config:
            try:
                raw = decrypt_json(t.encrypted_config)
            except Exception:
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail=(
                        "Stored tool configuration could not be decrypted. "
                        "Check MCP_ENCRYPTION_KEY matches the key used when the tool was created, or re-create the tool."
                    ),
                )
            if not isinstance(raw, dict):
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="Stored tool configuration is invalid. Re-create the tool.",
                )
            current_cfg = raw
        merged_cfg = merge_shallow_config(current_cfg, body.config)
        t.encrypted_config = encrypt_json(merged_cfg)
    if body.business_description is not None:
        bd = (body.business_description or "").strip() or None
        t.business_description = bd[:2000] if bd and len(bd) > 2000 else bd
    if body.is_active is not None:
        t.is_active = body.is_active
    db.commit()
    db.refresh(t)
    return _tool_to_response(t)


@router.post("/tools/{tool_id}/refresh-schema")
def refresh_tool_schema(
    tool_id: int,
    current_user: User = Depends(get_current_business_user),
    db: Session = Depends(get_db),
):
    """
    Introspect the database for this tool (Postgres/MySQL/SQL Server) and store schema metadata.
    Does not overwrite existing schema on connection failure.
    """
    from services.db_schema_introspection import introspect_sql_tool
    from core.encryption import decrypt_json

    t = db.query(MCPToolConfig).filter(
        MCPToolConfig.id == tool_id,
        MCPToolConfig.user_id == current_user.id,
    ).first()
    if not t:
        raise HTTPException(status_code=404, detail="Tool config not found")
    if t.tool_type not in (MCPToolType.POSTGRES, MCPToolType.MYSQL, MCPToolType.SQLSERVER):
        raise HTTPException(
            status_code=400,
            detail="Schema refresh is only available for PostgreSQL, MySQL, and SQL Server tools",
        )
    config = decrypt_json(t.encrypted_config)
    schema_dict, error = introspect_sql_tool(t.tool_type.value, config)
    if error:
        raise HTTPException(status_code=400, detail=error)
    t.schema_metadata = json.dumps(schema_dict)
    db.commit()
    db.refresh(t)
    table_count = len(schema_dict.get("tables", []))
    return {"success": True, "message": f"Schema refreshed: {table_count} table(s)", "table_count": table_count}


@router.delete("/tools/{tool_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_tool(
    tool_id: int,
    current_user: User = Depends(get_current_business_user),
    db: Session = Depends(get_db),
):
    t = db.query(MCPToolConfig).filter(
        MCPToolConfig.id == tool_id,
        MCPToolConfig.user_id == current_user.id,
    ).first()
    if not t:
        raise HTTPException(status_code=404, detail="Tool config not found")
    db.delete(t)
    db.commit()
    return None


# --- MCP proxy: forward JSON-RPC to user's MCP server ---

@router.post("/proxy")
async def mcp_proxy(
    body: MCPProxyRequest,
    current_user: User = Depends(get_current_business_user),
    db: Session = Depends(get_db),
):
    """Forward a JSON-RPC request to the user's MCP server using stored credentials."""
    conn = db.query(MCPServerConnection).filter(
        MCPServerConnection.id == body.connection_id,
        MCPServerConnection.user_id == current_user.id,
        MCPServerConnection.is_active == True,
    ).first()
    if not conn:
        raise HTTPException(status_code=404, detail="Connection not found or inactive")
    credentials = None
    if conn.encrypted_credentials:
        credentials = decrypt_json(conn.encrypted_credentials)
    # Observability: log MCP proxy request
    log_entry = AuditLog(
        entity_type="mcp",
        entity_id=conn.id,
        action="proxy_request",
        details=json.dumps({"method": body.method, "user_id": current_user.id}),
    )
    db.add(log_entry)
    db.commit()
    from core.config import settings as _settings

    proxy_timeout = float(getattr(_settings, "MCP_TOOL_DEFAULT_TIMEOUT_SECONDS", 60.0) or 60.0)
    max_proxy = float(getattr(_settings, "MCP_TOOL_MAX_TIMEOUT_SECONDS", 300.0) or 300.0)
    proxy_timeout = min(max(5.0, proxy_timeout), max(5.0, max_proxy))
    try:
        result = await guarded_mcp_jsonrpc(
            business_id=int(current_user.id),
            connection_id=int(conn.id),
            base_url=conn.base_url,
            endpoint_path=conn.endpoint_path or "/mcp",
            method=body.method,
            params=body.params,
            auth_type=conn.auth_type,
            credentials=credentials,
            timeout_seconds=proxy_timeout,
        )
        return result
    except MCPGuardrailError as ge:
        raise _http_exception_from_mcp_guardrail_error(ge) from ge


# --- Invoke platform MCP tool (for UI or agent-driven invocation) ---

@router.post("/call-platform-tool")
async def call_platform_tool(
    body: MCPPlatformToolCallRequest,
    current_user: User = Depends(get_current_business_user),
    db: Session = Depends(get_db),
):
    """
    Invoke a platform MCP tool by name (e.g. platform_1_MyDB).
    Backend calls the platform MCP server with X-MCP-Business-Id so tools are scoped to the current user.
    """
    return await _invoke_platform_tool_call(body, current_user.id, db)


@router.post("/call-platform-write")
async def call_platform_write(
    body: MCPPlatformWriteRequest,
    current_user: User = Depends(get_current_business_user),
    db: Session = Depends(get_db),
):
    """
    Invoke a platform MCP write-capable tool with a normalized artifact-first contract.
    This endpoint standardizes production writes (upsert/merge/insert/update) and
    enforces payload + timeout guards for high-load stability.
    """
    if body.operation_type in ("upsert", "merge") and not body.merge_keys:
        raise HTTPException(
            status_code=422,
            detail="merge_keys are required when operation_type is upsert or merge",
        )
    arguments = _normalize_platform_write_arguments(body)
    tool_call_body = MCPPlatformToolCallRequest(
        tool_name=body.tool_name,
        arguments=arguments,
        timeout_seconds=body.timeout_seconds,
    )
    return await _invoke_platform_tool_call(tool_call_body, current_user.id, db)


@router.post("/call-platform-write-async", response_model=MCPWriteOperationResponse, status_code=status.HTTP_202_ACCEPTED)
async def call_platform_write_async(
    body: MCPPlatformWriteRequest,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_business_user),
    db: Session = Depends(get_db),
):
    """Submit write request as async operation and return operation_id for polling."""
    from core.config import settings
    if not settings.PLATFORM_MCP_SERVER_URL or not settings.MCP_INTERNAL_SECRET:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Platform MCP server not configured",
        )
    _require_platform_tool_for_user(db, current_user.id, body.tool_name)
    if body.operation_type in ("upsert", "merge") and not body.merge_keys:
        raise HTTPException(status_code=422, detail="merge_keys are required when operation_type is upsert or merge")

    existing = db.query(MCPWriteOperation).filter(
        MCPWriteOperation.user_id == current_user.id,
        MCPWriteOperation.idempotency_key == body.idempotency_key,
    ).first()
    if existing:
        return _op_to_response(existing)

    arguments = _normalize_platform_write_arguments(body)
    payload_bytes = _estimate_json_size_bytes(arguments)
    max_payload = max(1024, int(getattr(settings, "MCP_TOOL_MAX_ARGUMENT_BYTES", 5 * 1024 * 1024)))
    if payload_bytes > max_payload:
        raise HTTPException(status_code=413, detail=f"Tool arguments exceed max payload size ({payload_bytes} > {max_payload} bytes)")

    op_id = f"op_{uuid.uuid4().hex}"
    op = MCPWriteOperation(
        user_id=current_user.id,
        operation_id=op_id,
        idempotency_key=body.idempotency_key,
        tool_name=body.tool_name,
        status="accepted",
        request_payload=json.dumps({
            "arguments": arguments,
            "timeout_seconds": body.timeout_seconds,
        }),
    )
    db.add(op)
    try:
        db.commit()
    except IntegrityError:
        # Idempotency race: another request inserted same (user_id, idempotency_key).
        db.rollback()
        existing = db.query(MCPWriteOperation).filter(
            MCPWriteOperation.user_id == current_user.id,
            MCPWriteOperation.idempotency_key == body.idempotency_key,
        ).first()
        if existing:
            return _op_to_response(existing)
        raise
    db.refresh(op)
    background_tasks.add_task(_run_platform_write_operation, op.operation_id, current_user.id)
    return _op_to_response(op)


@router.get("/operations/{operation_id}", response_model=MCPWriteOperationResponse)
def get_write_operation(
    operation_id: str,
    current_user: User = Depends(get_current_business_user),
    db: Session = Depends(get_db),
):
    op = db.query(MCPWriteOperation).filter(
        MCPWriteOperation.operation_id == operation_id,
        MCPWriteOperation.user_id == current_user.id,
    ).first()
    if not op:
        raise HTTPException(status_code=404, detail="Operation not found")
    return _op_to_response(op)


# --- Tool registry (for agent orchestration: discover available MCP tools) ---

@router.get("/registry")
async def get_registry(
    current_user: User = Depends(get_current_business_user),
    db: Session = Depends(get_db),
):
    """
    Return MCP tool registry for the current business user, split so the UI can show:
    - platform_tools: internal platform MCP server tools (Vector DB, Postgres, File system)
    - connection_tools: per external MCP connection, the tools exposed by that server (tools/list).
    Also returns combined "tools" for backward compatibility.
    """
    platform_tools = db.query(MCPToolConfig).filter(
        MCPToolConfig.user_id == current_user.id,
        MCPToolConfig.is_active == True,
    ).order_by(MCPToolConfig.name).all()
    connections = db.query(MCPServerConnection).filter(
        MCPServerConnection.user_id == current_user.id,
        MCPServerConnection.is_active == True,
    ).all()

    # Build platform registry entries (no credentials)
    platform_entries = []
    for t in platform_tools:
        platform_entries.append({
            "source": "platform",
            "id": t.id,
            "name": _registry_tool_name(t.id, t.name),
            "tool_type": t.tool_type.value,
            "description": _registry_description(t.tool_type.value, t.name),
            "access_mode": _registry_access_mode(t.tool_type.value),
        })

    # For each connection, fetch tools from that MCP server (tools/list)
    connection_tools = []
    for c in connections:
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
        tools_list = []
        error_msg = None
        try:
            result = await guarded_mcp_list_tools(
                business_id=int(current_user.id),
                connection_id=int(c.id),
                base_url=base_url,
                endpoint_path=endpoint_path,
                auth_type=c.auth_type or "none",
                credentials=creds,
                timeout_seconds=15.0,
            )
            for tool in (result.get("tools") or []):
                tools_list.append({
                    "name": tool.get("name") or "",
                    "description": (tool.get("description") or "")[:500],
                })
        except MCPGuardrailError as ge:
            logger.warning(
                "Registry tools/list guardrail connection=%s base=%s code=%s",
                c.id,
                base_url,
                ge.code,
            )
            error_msg = f"Failed to list tools ({ge.code})."
        except Exception as e:
            logger.warning(
                "Failed to list tools for connection id=%s name=%s host=%s (%s)",
                c.id,
                c.name,
                safe_url_host_for_logs(base_url),
                type(e).__name__,
            )
            error_msg = "Failed to list tools for this connection."
        connection_tools.append({
            "connection_id": c.id,
            "name": c.name,
            "base_url": c.base_url,
            "tools": tools_list,
            "error": error_msg,
        })

    # Combined list for backward compatibility (platform + one entry per connection)
    tools = list(platform_entries)
    for c in connections:
        tools.append({
            "source": "external",
            "connection_id": c.id,
            "name": c.name,
            "base_url": c.base_url,
        })
    return {
        "tools": tools,
        "platform_tools": platform_entries,
        "connection_tools": connection_tools,
        "platform_tool_count": len(platform_entries),
    }


# Backward-compatible alias: some clients may POST to /registry
@router.post("/registry")
async def post_registry(
    current_user: User = Depends(get_current_business_user),
    db: Session = Depends(get_db),
):
    return await get_registry(current_user=current_user, db=db)


def _registry_tool_name(tool_id: int, name: str) -> str:
    safe = "".join(c if c.isalnum() or c in "_-" else "_" for c in (name or "").strip())[:50]
    return f"platform_{tool_id}_{safe}" if safe else f"platform_{tool_id}"


# Tool types whose interactive platform MCP execution is read/search only (no writes in tools/call).
_READ_ONLY_PLATFORM_TOOL_TYPES = frozenset({
    "vector_db",
    "pinecone",
    "weaviate",
    "qdrant",
    "chroma",
    "elasticsearch",
    "pageindex",
    "github",
    "notion",
})


def _registry_access_mode(tool_type: str) -> str:
    """Registry UI: read_only vs read_write from actual platform MCP execute paths."""
    tt = (tool_type or "").strip().lower()
    if tt in _READ_ONLY_PLATFORM_TOOL_TYPES:
        return "read_only"
    return "read_write"


def _registry_description(tool_type: str, name: str) -> str:
    d = {
        "vector_db": "Vector database",
        "pinecone": "Pinecone",
        "weaviate": "Weaviate",
        "qdrant": "Qdrant",
        "chroma": "Chroma",
        "postgres": "PostgreSQL (SELECT + DML/DDL)",
        "mysql": "MySQL (SELECT + DML/DDL)",
        "sqlserver": "SQL Server",
        "snowflake": "Snowflake",
        "databricks": "Databricks",
        "bigquery": "BigQuery",
        "elasticsearch": "Elasticsearch",
        "pageindex": "PageIndex",
        "filesystem": "File system",
        "s3": "AWS S3",
        "minio": "MinIO",
        "ceph": "Ceph",
        "azure_blob": "Azure Blob",
        "gcs": "Google Cloud Storage",
        "slack": "Slack (read + write)",
        "teams": "Microsoft Teams / Graph (read + write)",
        "smtp": "SMTP email (read + write)",
        "github": "GitHub",
        "notion": "Notion",
        "rest_api": "REST API",
    }
    return f"{d.get(tool_type, tool_type)}: {name}"
