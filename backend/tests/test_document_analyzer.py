"""Unit tests for DocumentAnalyzer service."""
import asyncio
import pytest
from services.document_analyzer import DocumentAnalyzer


def test_read_document_txt(temp_txt_file):
    """Test reading .txt file content."""
    analyzer = DocumentAnalyzer()
    content = asyncio.run(analyzer.read_document(temp_txt_file))
    assert "Add 2 and 3" in content
    assert "Result should be 5" in content


def test_read_document_json(temp_json_file):
    """Test reading .json file content."""
    analyzer = DocumentAnalyzer()
    content = asyncio.run(analyzer.read_document(temp_json_file))
    assert "task" in content
    assert "add" in content
    assert "2" in content
    assert "3" in content


def test_read_document_nonexistent():
    """Test reading non-existent file returns error message."""
    analyzer = DocumentAnalyzer()
    content = asyncio.run(analyzer.read_document("/nonexistent/path/file.txt"))
    assert "[Error" in content or "error" in content.lower()


@pytest.fixture
def temp_txt_file():
    """Create a temporary .txt file."""
    import tempfile
    from pathlib import Path
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".txt", delete=False, encoding="utf-8"
    ) as f:
        f.write("Sample requirement: Add 2 and 3. Result should be 5.")
        path = f.name
    yield path
    Path(path).unlink(missing_ok=True)


def test_analyze_documents_no_agent_extraction_only(temp_txt_file):
    """When no agent_api_url, returns extraction-only response with empty questions."""
    analyzer = DocumentAnalyzer()
    result = asyncio.run(
        analyzer.analyze_documents_and_generate_questions(
            documents=[{"path": temp_txt_file, "name": "req.txt"}],
            job_title="Test Job",
            job_description="Test description",
            conversation_history=[],
            agent_api_url=None,
            agent_api_key=None,
        )
    )
    assert "questions" in result
    assert result["questions"] == []
    assert "analysis" in result
    assert "Select and assign agents" in result["analysis"] or "extracted" in result["analysis"].lower()


def test_format_conversation_handles_dict_values():
    """_format_conversation should handle dict question/answer without crashing."""
    analyzer = DocumentAnalyzer()
    formatted = analyzer._format_conversation([
        {"question": {"text": "Q1"}, "answer": "A1"},
        {"question": "Q2", "answer": {"value": "A2"}},
    ])
    assert "Q1" in formatted or "A1" in formatted
    assert "Q2" in formatted or "A2" in formatted or "value" in formatted


def test_extract_questions():
    """_extract_questions extracts lines ending with ?."""
    analyzer = DocumentAnalyzer()
    text = "What is 2+3? And what about 4+5?"
    questions = analyzer._extract_questions(text)
    assert len(questions) >= 1
    assert any("?" in q for q in questions)


def test_extract_recommendations():
    """_extract_recommendations extracts bullet/numbered recommendations."""
    analyzer = DocumentAnalyzer()
    text = """
    - We recommend using Python for this task.
    1. Consider adding validation.
    * Solution: Use a calculator.
    """
    recs = analyzer._extract_recommendations(text)
    assert len(recs) <= 5
    assert any("recommend" in r.lower() or "Python" in r or "validation" in r or "calculator" in r for r in recs)
