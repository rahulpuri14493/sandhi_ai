"""Integration tests: executor attaches ``sandhi_a2a_task`` + ``assigned_tools`` before agent call."""

import json
from unittest.mock import AsyncMock, patch

import pytest

from core.config import settings
from models.agent import Agent, AgentStatus, PricingModel
from models.job import Job, JobStatus, WorkflowStep
from models.user import User, UserRole
from services.agent_executor import AgentExecutor


@pytest.mark.asyncio
async def test_single_step_job_envelope_has_parallel_wave_and_no_next_agent(db_session):
    biz = User(email="env-biz@test.com", password_hash="x", role=UserRole.BUSINESS)
    dev = User(email="env-dev@test.com", password_hash="x", role=UserRole.DEVELOPER)
    db_session.add_all([biz, dev])
    db_session.commit()
    db_session.refresh(biz)
    db_session.refresh(dev)
    agent = Agent(
        developer_id=dev.id,
        name="EnvAgent",
        price_per_task=1.0,
        status=AgentStatus.ACTIVE,
        pricing_model=PricingModel.PAY_PER_USE,
        api_endpoint="http://example.invalid/env",
        a2a_enabled=True,
    )
    db_session.add(agent)
    db_session.commit()
    db_session.refresh(agent)
    job = Job(
        business_id=biz.id,
        title="EnvJob",
        status=JobStatus.IN_PROGRESS,
        description="d",
        files=json.dumps([]),
        conversation=json.dumps([]),
        total_cost=1.0,
        write_execution_mode="ui_only",
    )
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)
    step = WorkflowStep(
        job_id=job.id,
        agent_id=agent.id,
        step_order=1,
        status="pending",
        input_data=json.dumps(
            {
                "job_title": job.title,
                "job_description": "",
                "documents": [],
                "conversation": [],
                "assigned_task": "Hello",
                "task_type": "default",
            }
        ),
    )
    db_session.add(step)
    db_session.commit()
    db_session.refresh(step)

    ex = AgentExecutor(db_session)

    async def _capture_agent(_agent, inp):
        task = inp.get("sandhi_a2a_task") or {}
        assert task.get("schema_version") == "sandhi.a2a_task.v1"
        assert "next_agent" in task and task.get("next_agent") is None
        assert isinstance(task.get("assigned_tools"), list)
        par = task.get("parallel")
        assert par is not None
        assert par.get("wave_index") == 0
        assert isinstance(inp.get("assigned_tools"), list)
        return {"content": "ok"}

    with (
        patch.object(ex, "_get_available_mcp_tools_async", return_value=[]),
        patch.object(ex, "_execute_agent", side_effect=_capture_agent),
    ):
        await ex._execute_one_step_core(job.id, step.id, None)


@pytest.mark.asyncio
async def test_two_step_job_first_step_envelope_points_to_next_agent(db_session):
    biz = User(email="env2-biz@test.com", password_hash="x", role=UserRole.BUSINESS)
    dev = User(email="env2-dev@test.com", password_hash="x", role=UserRole.DEVELOPER)
    db_session.add_all([biz, dev])
    db_session.commit()
    db_session.refresh(biz)
    db_session.refresh(dev)

    a1 = Agent(
        developer_id=dev.id,
        name="A1",
        price_per_task=1.0,
        status=AgentStatus.ACTIVE,
        pricing_model=PricingModel.PAY_PER_USE,
        api_endpoint="http://example.invalid/a1",
        a2a_enabled=True,
    )
    a2 = Agent(
        developer_id=dev.id,
        name="A2",
        price_per_task=1.0,
        status=AgentStatus.ACTIVE,
        pricing_model=PricingModel.PAY_PER_USE,
        api_endpoint="http://example.invalid/a2",
        a2a_enabled=True,
    )
    db_session.add_all([a1, a2])
    db_session.commit()
    db_session.refresh(a1)
    db_session.refresh(a2)

    job = Job(
        business_id=biz.id,
        title="EnvJob2",
        status=JobStatus.IN_PROGRESS,
        files=json.dumps([]),
        conversation=json.dumps([]),
        total_cost=2.0,
        write_execution_mode="ui_only",
    )
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)

    base = {"job_title": job.title, "job_description": "", "documents": [], "conversation": []}
    s1 = WorkflowStep(
        job_id=job.id,
        agent_id=a1.id,
        step_order=1,
        status="pending",
        input_data=json.dumps(base),
        depends_on_previous=True,
    )
    s2 = WorkflowStep(
        job_id=job.id,
        agent_id=a2.id,
        step_order=2,
        status="pending",
        input_data=json.dumps(base),
        depends_on_previous=True,
    )
    db_session.add_all([s1, s2])
    db_session.commit()
    db_session.refresh(s1)
    db_session.refresh(s2)

    ex = AgentExecutor(db_session)

    async def _capture(_agent, inp):
        task = inp.get("sandhi_a2a_task") or {}
        nxt = task.get("next_agent") or {}
        assert isinstance(task.get("assigned_tools"), list)
        assert nxt.get("agent_id") == a2.id
        assert nxt.get("workflow_step_id") == s2.id
        assert nxt.get("a2a_endpoint") == "http://example.invalid/a2"
        return {"content": "ok"}

    with (
        patch.object(ex, "_get_available_mcp_tools_async", return_value=[]),
        patch.object(ex, "_execute_agent", side_effect=_capture),
    ):
        await ex._execute_one_step_core(job.id, s1.id, None)


@pytest.mark.asyncio
async def test_llm_tool_pick_skipped_when_tool_assignment_disabled(db_session):
    """Planner tool-pick must not run when assignment engine is disabled (Codex P2)."""
    biz = User(email="env-pick-gate-biz@test.com", password_hash="x", role=UserRole.BUSINESS)
    dev = User(email="env-pick-gate-dev@test.com", password_hash="x", role=UserRole.DEVELOPER)
    db_session.add_all([biz, dev])
    db_session.commit()
    db_session.refresh(biz)
    db_session.refresh(dev)
    agent = Agent(
        developer_id=dev.id,
        name="PickGateAgent",
        price_per_task=1.0,
        status=AgentStatus.ACTIVE,
        pricing_model=PricingModel.PAY_PER_USE,
        api_endpoint="http://example.invalid/pick",
        a2a_enabled=True,
    )
    db_session.add(agent)
    db_session.commit()
    db_session.refresh(agent)
    job = Job(
        business_id=biz.id,
        title="PickGateJob",
        status=JobStatus.IN_PROGRESS,
        description="d",
        files=json.dumps([]),
        conversation=json.dumps([]),
        total_cost=1.0,
        write_execution_mode="ui_only",
    )
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)
    step = WorkflowStep(
        job_id=job.id,
        agent_id=agent.id,
        step_order=1,
        status="pending",
        input_data=json.dumps(
            {
                "job_title": job.title,
                "job_description": "",
                "documents": [],
                "conversation": [],
                "assigned_task": "Hello",
            }
        ),
    )
    db_session.add(step)
    db_session.commit()
    db_session.refresh(step)

    ex = AgentExecutor(db_session)
    pick = AsyncMock(return_value=["t1"])

    async def _capture(_agent, _inp):
        return {"content": "ok"}

    with (
        patch.object(settings, "TOOL_ASSIGNMENT_ENABLED", False),
        patch.object(settings, "TOOL_ASSIGNMENT_LLM_PICK_TOOLS", True),
        patch.object(settings, "TOOL_ASSIGNMENT_USE_LLM", True),
        patch("services.tool_assignment_llm.suggest_tool_names_with_llm", pick),
        patch.object(
            ex,
            "_get_available_mcp_tools_async",
            return_value=[{"name": "t1", "tool_type": "s3"}],
        ),
        patch.object(ex, "_execute_agent", side_effect=_capture),
    ):
        await ex._execute_one_step_core(job.id, step.id, None)

    pick.assert_not_called()
