"""Unit tests for job Q&A schema."""

from datetime import datetime

from models.job_qa import QAStatus
from schemas.job_qa import JobQuestionResponse, AnswerQuestionRequest


def test_job_question_schema_from_orm():
    """JobQuestionResponse can validate from ORM object."""
    now = datetime.utcnow()
    # Use a lightweight object instead of SQLAlchemy-mapped instance.
    q = type(
        "Q",
        (),
        {
            "id": 1,
            "job_id": 2,
            "question": "What is the goal?",
            "answer": None,
            "status": QAStatus.PENDING,
            "created_at": now,
            "answered_at": None,
        },
    )()
    out = JobQuestionResponse.model_validate(q, from_attributes=True)
    assert out.id == 1
    assert out.job_id == 2
    assert out.status == QAStatus.PENDING


def test_answer_question_request_schema():
    req = AnswerQuestionRequest(answer="Yes, do X.")
    assert req.answer.startswith("Yes")

