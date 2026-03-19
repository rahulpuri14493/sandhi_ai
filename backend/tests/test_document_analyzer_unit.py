"""Unit tests for services/document_analyzer.py (mocked, no network)."""

import json

import pytest

from services.document_analyzer import DocumentAnalyzer


@pytest.mark.asyncio
async def test_read_document_txt_csv_json_and_unsupported(tmp_path):
    da = DocumentAnalyzer()

    p_txt = tmp_path / "a.txt"
    p_txt.write_text("hello", encoding="utf-8")
    assert await da.read_document(str(p_txt)) == "hello"

    p_csv = tmp_path / "a.csv"
    p_csv.write_text("x,y\n1,2\n", encoding="utf-8")
    csv_out = await da.read_document(str(p_csv))
    assert "x,y" in csv_out and "1,2" in csv_out

    p_json = tmp_path / "a.json"
    p_json.write_text(json.dumps({"a": 1}), encoding="utf-8")
    json_out = await da.read_document(str(p_json))
    assert "\"a\": 1" in json_out

    p_bin = tmp_path / "a.bin"
    p_bin.write_bytes(b"\x00\x01")
    unsupported = await da.read_document(str(p_bin))
    assert "Unsupported file type" in unsupported


@pytest.mark.asyncio
async def test_analyze_documents_extraction_only_when_no_agent(tmp_path):
    da = DocumentAnalyzer()
    p_txt = tmp_path / "req.txt"
    p_txt.write_text("Reqs here", encoding="utf-8")

    out = await da.analyze_documents_and_generate_questions(
        documents=[{"path": str(p_txt), "name": "req.txt"}],
        job_title="T",
        job_description="",
        conversation_history=[],
        agent_api_url=None,
    )
    assert "Document text extracted" in out["analysis"]
    assert out["questions"] == []
    assert out["recommendations"] == []


@pytest.mark.asyncio
async def test_analyze_documents_a2a_json_parse(monkeypatch, tmp_path):
    da = DocumentAnalyzer()
    p_txt = tmp_path / "req.txt"
    p_txt.write_text("Reqs here", encoding="utf-8")

    async def fake_execute_via_a2a(url, input_data, **kwargs):
        return {
            "content": json.dumps(
                {
                    "analysis": "A",
                    "questions": ["Q1?"],
                    "recommendations": ["R1"],
                    "solutions": ["S1"],
                    "next_steps": ["N1"],
                    "workflow_collaboration_hint": "sequential",
                    "workflow_collaboration_reason": "pipeline",
                }
            )
        }

    # Patch the imported symbol inside module
    import services.document_analyzer as mod

    monkeypatch.setattr(mod, "execute_via_a2a", fake_execute_via_a2a)

    out = await da.analyze_documents_and_generate_questions(
        documents=[{"path": str(p_txt), "name": "req.txt"}],
        job_title="T",
        job_description=None,
        conversation_history=[],
        agent_api_url="http://agent",
        agent_api_key="k",
        use_a2a=True,
    )
    assert out["analysis"] == "A"
    assert out["questions"] == ["Q1?"]
    assert out["workflow_collaboration_hint"] == "sequential"


@pytest.mark.asyncio
async def test_read_file_info_with_local_path_metadata(tmp_path):
    """read_file_info should accept a metadata dict with a local path and return text content."""
    da = DocumentAnalyzer()
    p = tmp_path / "brd.txt"
    p.write_text("Business requirements document content", encoding="utf-8")

    file_info = {"path": str(p), "name": "brd.txt", "type": "text/plain", "size": p.stat().st_size}
    content = await da.read_file_info(file_info)
    assert "Business requirements document content" in content


@pytest.mark.asyncio
async def test_read_file_info_missing_source_raises():
    """read_file_info should raise for metadata with no readable source."""
    da = DocumentAnalyzer()
    with pytest.raises(ValueError, match="no readable source"):
        await da.read_file_info({"name": "orphan.txt"})


def test_extract_helpers():
    da = DocumentAnalyzer()
    qs = da._extract_questions("One? Two? Three? Four?")
    assert qs == ["One?", "Two?", "Three?"]
    # Lines must have len(cleaned) > 10 to be included
    recs = da._extract_recommendations("- First recommendation here\n1) Second recommendation here\nignore\n")
    assert len(recs) >= 2


@pytest.mark.asyncio
async def test_generate_workflow_clarification_questions_parses_fenced_json(monkeypatch):
    da = DocumentAnalyzer()

    async def fake_execute_via_a2a(url, input_data, **kwargs):
        return {"content": "```json\n{\"questions\": [\"Q?\"]}\n```"}

    import services.document_analyzer as mod

    monkeypatch.setattr(mod, "execute_via_a2a", fake_execute_via_a2a)
    monkeypatch.setattr(mod.settings, "A2A_ADAPTER_URL", "http://adapter")

    out = await da.generate_workflow_clarification_questions(
        job_title="T",
        job_description="D",
        documents_content=[],
        workflow_tasks=[{"step_order": 1, "agent_name": "A", "assigned_task": "Do X"}],
        conversation_history=[],
        agent_api_url="http://openai",
        agent_api_key="k",
        agent_llm_model="m",
        use_a2a=False,
    )
    assert out["questions"] == ["Q?"]

