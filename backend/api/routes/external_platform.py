"""
Machine-to-machine platform maintenance (same auth model as external job creation).

Use ``X-API-Key: <EXTERNAL_API_KEY>`` — not end-user JWT.
"""

from fastapi import APIRouter, Depends

from api.routes.external_jobs import _verify_external_api_key
from services.tool_assignment_registry import reload_tool_assignment_registry

router = APIRouter(prefix="/api/external/platform", tags=["external-platform"])


@router.post("/tool-assignment-registry/reload")
async def reload_tool_assignment_registry_endpoint(_: bool = Depends(_verify_external_api_key)):
    """Reload the tool assignment registry JSON from disk (``TOOL_ASSIGNMENT_REGISTRY_PATH`` or default)."""
    reg = reload_tool_assignment_registry()
    return {
        "ok": True,
        "version": reg.version,
        "rules": len(reg.rules),
        "source_path": reg.source_path,
    }
