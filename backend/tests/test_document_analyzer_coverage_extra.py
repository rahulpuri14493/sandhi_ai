"""Extra coverage for services.document_analyzer (read paths + read_file_info)."""

import json
import sys
import types

import pytest

from services.document_analyzer import DocumentAnalyzer


@pytest.mark.asyncio
async def test_read_document_markdown_and_xml(tmp_path):
    da = DocumentAnalyzer()
    md = tmp_path / "n.md"
    md.write_text("# Title", encoding="utf-8")
    assert "Title" in await da.read_document(str(md))

    xml = tmp_path / "n.xml"
    xml.write_text("<root><a>1</a></root>", encoding="utf-8")
    assert "<root>" in await da.read_document(str(xml))


@pytest.mark.asyncio
async def test_read_document_rtf(tmp_path):
    da = DocumentAnalyzer()
    rtf = tmp_path / "n.rtf"
    rtf.write_text(r"{\rtf1\ansi test}", encoding="utf-8")
    out = await da.read_document(str(rtf))
    assert "test" in out or r"\rtf" in out


@pytest.mark.asyncio
async def test_read_document_pdf_import_error_message(monkeypatch, tmp_path):
    monkeypatch.setitem(sys.modules, "pypdf", types.ModuleType("pypdf"))
    da = DocumentAnalyzer()
    pdf = tmp_path / "x.pdf"
    pdf.write_bytes(b"%PDF-1.4 minimal")
    out = await da.read_document(str(pdf))
    assert "pypdf" in out.lower()


@pytest.mark.asyncio
async def test_read_document_pdf_success(monkeypatch, tmp_path):
    class FakePage:
        def extract_text(self):
            return "page body"

    class FakeReader:
        def __init__(self, f):
            self.pages = [FakePage(), FakePage()]

    fake_pypdf = types.ModuleType("pypdf")
    fake_pypdf.PdfReader = FakeReader
    monkeypatch.setitem(sys.modules, "pypdf", fake_pypdf)
    da = DocumentAnalyzer()
    pdf = tmp_path / "ok.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    out = await da.read_document(str(pdf))
    assert "Page 1" in out and "page body" in out


@pytest.mark.asyncio
async def test_read_document_docx_via_python_docx(monkeypatch, tmp_path):
    class FakePara:
        def __init__(self, text):
            self.text = text

    class FakeTable:
        rows = []

    class FakeDoc:
        paragraphs = [FakePara("hello"), FakePara("   ")]
        tables = []

    fake_docx = types.ModuleType("docx")
    fake_docx.Document = lambda path: FakeDoc()
    monkeypatch.setitem(sys.modules, "docx", fake_docx)
    da = DocumentAnalyzer()
    p = tmp_path / "f.docx"
    p.write_bytes(b"PK\x03\x04")
    out = await da.read_document(str(p))
    assert "hello" in out


@pytest.mark.asyncio
async def test_read_document_docx_fallback_docx2txt(monkeypatch, tmp_path):
    def boom(path):
        raise RuntimeError("no docx")

    fake_docx = types.ModuleType("docx")
    fake_docx.Document = boom
    monkeypatch.setitem(sys.modules, "docx", fake_docx)
    fake_d2 = types.ModuleType("docx2txt")
    fake_d2.process = lambda path: "from docx2txt"
    monkeypatch.setitem(sys.modules, "docx2txt", fake_d2)
    da = DocumentAnalyzer()
    p = tmp_path / "f.docx"
    p.write_bytes(b"x")
    out = await da.read_document(str(p))
    assert "docx2txt" in out


@pytest.mark.asyncio
async def test_read_document_top_level_open_error(monkeypatch, tmp_path):
    da = DocumentAnalyzer()
    p = tmp_path / "a.txt"
    p.write_text("x", encoding="utf-8")

    def bad_open(*a, **kw):
        raise OSError("disk full")

    monkeypatch.setattr("builtins.open", bad_open)
    out = await da.read_document(str(p))
    assert "Error reading file" in out


@pytest.mark.asyncio
async def test_read_file_info_uses_materialize_and_cleanup(monkeypatch, tmp_path):
    p = tmp_path / "inner.txt"
    p.write_text("materialized-body", encoding="utf-8")

    async def _mat(_fi):
        return str(p)

    monkeypatch.setattr("services.document_analyzer.materialize_to_temp_path", _mat)
    cleaned = {"ok": False}

    def _clean(fi, local):
        cleaned["ok"] = local == str(p)

    monkeypatch.setattr("services.document_analyzer.cleanup_temp_path", _clean)
    da = DocumentAnalyzer()
    body = await da.read_file_info({"storage": "local"})
    assert body == "materialized-body"
    assert cleaned["ok"] is True


@pytest.mark.asyncio
async def test_generate_workflow_clarification_questions_empty_workflow():
    da = DocumentAnalyzer()
    out = await da.generate_workflow_clarification_questions(
        "Title",
        "Desc",
        [],
        [],
        [],
    )
    assert out == {"questions": []}


@pytest.mark.asyncio
async def test_analyze_documents_empty_list():
    da = DocumentAnalyzer()
    out = await da.analyze_documents_and_generate_questions(
        documents=[],
        job_title="T",
        job_description=None,
        conversation_history=[],
        agent_api_url=None,
    )
    assert "No documents could be read" in out["analysis"]


@pytest.mark.asyncio
async def test_analyze_documents_platform_planner_json(monkeypatch, tmp_path):
    import services.document_analyzer as mod

    monkeypatch.setattr(mod, "is_agent_planner_configured", lambda: True)

    async def fake_planner(messages, **kw):
        return json.dumps(
            {
                "analysis": "Planned",
                "questions": [],
                "recommendations": [],
                "solutions": [],
                "next_steps": [],
                "workflow_collaboration_hint": "async_a2a",
                "workflow_collaboration_reason": "peers",
            }
        )

    monkeypatch.setattr(mod, "planner_chat_completion", fake_planner)
    da = DocumentAnalyzer()
    p = tmp_path / "r.txt"
    p.write_text("req", encoding="utf-8")
    out = await da.analyze_documents_and_generate_questions(
        documents=[{"path": str(p), "name": "r.txt"}],
        job_title="Job",
        job_description="Desc",
        conversation_history=[
            {"type": "question", "question": "Q?", "answer": "A"},
            {"type": "analysis", "content": " prior "},
        ],
    )
    assert out["analysis"] == "Planned"
    assert out["workflow_collaboration_hint"] == "async_a2a"
    assert out["workflow_collaboration_reason"] == "peers"


@pytest.mark.asyncio
async def test_analyze_documents_platform_planner_non_json(monkeypatch, tmp_path):
    import services.document_analyzer as mod

    monkeypatch.setattr(mod, "is_agent_planner_configured", lambda: True)

    async def fake_planner(*a, **kw):
        return "Plain text. Still unclear?"

    monkeypatch.setattr(mod, "planner_chat_completion", fake_planner)
    da = DocumentAnalyzer()
    p = tmp_path / "r.txt"
    p.write_text("x", encoding="utf-8")
    out = await da.analyze_documents_and_generate_questions(
        documents=[{"path": str(p), "name": "r.txt"}],
        job_title="J",
        conversation_history=[],
    )
    assert "Plain text" in out["analysis"]
    assert isinstance(out["questions"], list)


@pytest.mark.asyncio
async def test_platform_planner_failure_propagates(monkeypatch, tmp_path):
    import services.document_analyzer as mod

    monkeypatch.setattr(mod, "is_agent_planner_configured", lambda: True)

    async def boom(*a, **kw):
        raise ValueError("planner down")

    monkeypatch.setattr(mod, "planner_chat_completion", boom)
    da = DocumentAnalyzer()
    p = tmp_path / "r.txt"
    p.write_text("x", encoding="utf-8")
    with pytest.raises(Exception, match="Agent planner analysis failed"):
        await da.analyze_documents_and_generate_questions(
            documents=[{"path": str(p), "name": "r.txt"}],
            job_title="J",
            conversation_history=[],
        )


@pytest.mark.asyncio
async def test_analyze_documents_no_planner_ignores_hired_agent_url(monkeypatch, tmp_path):
    import services.document_analyzer as mod

    monkeypatch.setattr(mod, "is_agent_planner_configured", lambda: False)
    da = DocumentAnalyzer()
    p = tmp_path / "r.txt"
    p.write_text("body", encoding="utf-8")
    out = await da.analyze_documents_and_generate_questions(
        documents=[{"path": str(p), "name": "r.txt"}],
        job_title="J",
        agent_api_url="https://would-be-agent.example/v1",
        agent_api_key="k",
    )
    assert "Configure the platform Agent Planner" in out["analysis"]
    assert out["questions"] == []


@pytest.mark.asyncio
async def test_process_user_response_updates_history_and_delegates(monkeypatch):
    da = DocumentAnalyzer()
    captured = {}

    async def fake_analyze(docs, title, desc, hist, **kw):
        captured["hist"] = hist
        return {"analysis": "done", "questions": []}

    monkeypatch.setattr(da, "analyze_documents_and_generate_questions", fake_analyze)
    hist = [{"type": "question", "question": "Really?"}]
    out = await da.process_user_response(
        "Yes",
        [{"path": "/x", "name": "f.txt"}],
        "Job",
        "",
        hist,
        agent_api_url=None,
    )
    assert out["analysis"] == "done"
    assert any(
        it.get("type") == "question" and it.get("answer") == "Yes" for it in captured["hist"]
    )
