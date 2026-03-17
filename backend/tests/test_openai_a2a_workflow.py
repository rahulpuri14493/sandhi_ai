"""
Integration test: OpenAI-compatible agent and A2A agent communicate in a 2-step workflow.

Step 1: Agent with a2a_enabled=False (OpenAI-compatible) is called via platform adapter → returns content.
Step 2: Agent with a2a_enabled=True (A2A) receives previous_step_output and returns content.

Verifies no break in handoff between the two protocol paths.
"""

import json
import uuid
from unittest.mock import AsyncMock, patch

import pytest

from db.database import get_db
from main import app
from models.user import User, UserRole
from models.job import Job, JobStatus, WorkflowStep
from models.agent import Agent
from services.agent_executor import AgentExecutor


@pytest.fixture
def job_two_agents_openai_then_a2a(db_session):
    """Job with 2 workflow steps: first agent OpenAI-compatible, second agent A2A."""
    unique = uuid.uuid4().hex[:8]
    business = User(
        email=f"business-{unique}@test.com",
        password_hash="hash",
        role=UserRole.BUSINESS,
    )
    db_session.add(business)
    db_session.commit()
    db_session.refresh(business)

    dev = User(
        email=f"dev-{unique}@test.com",
        password_hash="hash",
        role=UserRole.DEVELOPER,
    )
    db_session.add(dev)
    db_session.commit()
    db_session.refresh(dev)

    # Agent 1: OpenAI-compatible (no A2A) — platform will call via adapter
    agent1 = Agent(
        developer_id=dev.id,
        name="OpenAI Agent",
        description="OpenAI-compatible",
        price_per_task=1.0,
        price_per_communication=0.1,
        api_endpoint="https://openai.example.com/v1/chat/completions",
        api_key="sk-test",
        llm_model="gpt-4o-mini",
        a2a_enabled=False,
    )
    db_session.add(agent1)
    db_session.commit()
    db_session.refresh(agent1)

    # Agent 2: A2A native — platform will call directly
    agent2 = Agent(
        developer_id=dev.id,
        name="A2A Agent",
        description="A2A protocol",
        price_per_task=2.0,
        price_per_communication=0.2,
        api_endpoint="https://a2a.example.com/",
        api_key=None,
        a2a_enabled=True,
    )
    db_session.add(agent2)
    db_session.commit()
    db_session.refresh(agent2)

    job = Job(
        business_id=business.id,
        title="OpenAI + A2A workflow test",
        description="Test handoff",
        status=JobStatus.IN_PROGRESS,
        files=json.dumps([]),
        conversation=json.dumps([]),
    )
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)

    step1_input = {
        "job_title": job.title,
        "job_description": job.description,
        "documents": [{"name": "req.txt", "content": "Do step 1."}],
        "conversation": [],
    }
    step1 = WorkflowStep(
        job_id=job.id,
        agent_id=agent1.id,
        step_order=1,
        status="pending",
        input_data=json.dumps(step1_input),
    )
    db_session.add(step1)
    db_session.commit()

    step2_input = {
        "job_title": job.title,
        "job_description": job.description,
        "documents": [],
        "conversation": [],
    }
    step2 = WorkflowStep(
        job_id=job.id,
        agent_id=agent2.id,
        step_order=2,
        status="pending",
        input_data=json.dumps(step2_input),
    )
    db_session.add(step2)
    db_session.commit()

    return job, agent1, agent2, step1, step2, db_session


@pytest.mark.asyncio
async def test_openai_agent_and_a2a_agent_communicate_in_workflow(
    job_two_agents_openai_then_a2a,
):
    """Run 2-step job: OpenAI-compatible (via adapter) → A2A. Assert handoff works."""
    job, agent1, agent2, step1, step2, db_session = job_two_agents_openai_then_a2a
    job_id = job.id

    adapter_url = "http://adapter:8080"
    step1_output = {"content": "Step1-output-from-OpenAI-via-adapter"}
    step2_output = {"content": "Step2-received-handoff"}

    call_log = []

    async def mock_execute_via_a2a(
        url,
        input_data,
        *,
        api_key=None,
        blocking=True,
        timeout=120.0,
        adapter_metadata=None,
    ):
        call_log.append(
            {"url": url, "input_data": input_data, "adapter_metadata": adapter_metadata}
        )
        if adapter_metadata is not None:
            # First call: platform routing to adapter (OpenAI-compatible agent)
            assert "openai_url" in adapter_metadata
            assert adapter_metadata["openai_url"] == agent1.api_endpoint
            return step1_output
        # Second call: direct A2A agent; must receive previous step output
        prev = input_data.get("previous_step_output")
        assert prev is not None, "Step 2 must receive previous_step_output"
        if isinstance(prev, dict) and "content" in prev:
            assert (
                prev["content"] == step1_output["content"]
            ), "Step 2 should see Step 1 content"
        return step2_output

    with patch("services.agent_executor.settings") as mock_settings:
        mock_settings.A2A_ADAPTER_URL = adapter_url
        with patch(
            "services.agent_executor.execute_via_a2a", side_effect=mock_execute_via_a2a
        ):
            executor = AgentExecutor(db=db_session)
            await executor.execute_job(job_id)

    assert len(call_log) == 2

    # First call: to adapter with metadata pointing to agent1
    assert call_log[0]["url"] == adapter_url
    assert call_log[0]["adapter_metadata"] is not None
    assert call_log[0]["adapter_metadata"].get("openai_url") == agent1.api_endpoint

    # Second call: to A2A agent (agent2 endpoint), with previous_step_output
    assert call_log[1]["url"] == agent2.api_endpoint
    assert call_log[1]["adapter_metadata"] is None
    assert call_log[1]["input_data"].get("previous_step_output") == step1_output

    db_session.refresh(step1)
    db_session.refresh(step2)
    assert step1.status == "completed"
    assert step2.status == "completed"
    assert step1_output["content"] in (step1.output_data or "")
    assert step2_output["content"] in (step2.output_data or "")


@pytest.mark.asyncio
async def test_a2a_agent_then_openai_agent_communicate_in_workflow(db_session):
    """Run 2-step job: A2A first → OpenAI-compatible (via adapter) second. Assert handoff works."""
    unique = uuid.uuid4().hex[:8]
    business = User(
        email=f"b-{unique}@test.com", password_hash="h", role=UserRole.BUSINESS
    )
    dev = User(email=f"d-{unique}@test.com", password_hash="h", role=UserRole.DEVELOPER)
    db_session.add_all([business, dev])
    db_session.commit()
    db_session.refresh(business)
    db_session.refresh(dev)

    agent_a2a = Agent(
        developer_id=dev.id,
        name="A2A First",
        price_per_task=1.0,
        price_per_communication=0.1,
        api_endpoint="https://a2a.first/",
        a2a_enabled=True,
    )
    agent_openai = Agent(
        developer_id=dev.id,
        name="OpenAI Second",
        price_per_task=1.0,
        price_per_communication=0.1,
        api_endpoint="https://openai.second/v1/chat/completions",
        a2a_enabled=False,
    )
    db_session.add_all([agent_a2a, agent_openai])
    db_session.commit()
    db_session.refresh(agent_a2a)
    db_session.refresh(agent_openai)

    job = Job(
        business_id=business.id,
        title="A2A then OpenAI",
        status=JobStatus.IN_PROGRESS,
        files=json.dumps([]),
        conversation=json.dumps([]),
    )
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)

    base_input = {
        "job_title": job.title,
        "job_description": "",
        "documents": [],
        "conversation": [],
    }
    s1 = WorkflowStep(
        job_id=job.id,
        agent_id=agent_a2a.id,
        step_order=1,
        status="pending",
        input_data=json.dumps(base_input),
    )
    s2 = WorkflowStep(
        job_id=job.id,
        agent_id=agent_openai.id,
        step_order=2,
        status="pending",
        input_data=json.dumps(base_input),
    )
    db_session.add_all([s1, s2])
    db_session.commit()

    step1_out = {"content": "A2A-step1-output"}
    step2_out = {"content": "OpenAI-step2-received-handoff"}
    call_log = []

    async def mock_execute_via_a2a(
        url,
        input_data,
        *,
        api_key=None,
        blocking=True,
        timeout=120.0,
        adapter_metadata=None,
    ):
        call_log.append(
            {
                "url": url,
                "adapter_metadata": adapter_metadata,
                "has_prev": "previous_step_output" in input_data,
            }
        )
        if adapter_metadata is not None:
            assert input_data.get("previous_step_output") == step1_out
            return step2_out
        return step1_out

    with patch("services.agent_executor.settings") as mock_settings:
        mock_settings.A2A_ADAPTER_URL = "http://adapter:8080"
        with patch(
            "services.agent_executor.execute_via_a2a", side_effect=mock_execute_via_a2a
        ):
            executor = AgentExecutor(db=db_session)
            await executor.execute_job(job.id)

    assert len(call_log) == 2
    assert call_log[0]["adapter_metadata"] is None
    assert call_log[1]["adapter_metadata"] is not None
    assert call_log[1]["has_prev"] is True

    db_session.refresh(s1)
    db_session.refresh(s2)
    assert s1.status == "completed"
    assert s2.status == "completed"
    assert step1_out["content"] in (s1.output_data or "")
    assert step2_out["content"] in (s2.output_data or "")
