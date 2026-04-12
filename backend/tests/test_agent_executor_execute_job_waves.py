"""Corner cases and coverage for AgentExecutor.execute_job wave scheduling and _execute_one_step_core."""
import asyncio
import json
import time
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from models.agent import Agent
from models.job import Job, JobStatus, WorkflowStep
from models.user import User, UserRole
from services import agent_executor as ae
from services.agent_executor import AgentExecutor
from core.config import settings as app_settings
from services.task_splitter import PlannerSplitError
from services.workflow_builder import WorkflowBuilder


def _create_job_with_steps(
    db_session,
    *,
    depends_on_previous_list: list,
    total_cost: float | None = None,
    write_execution_mode: str = "ui_only",
):
    """
    Build a job with len(depends_on_previous_list) steps, one agent per step.
    depends_on_previous_list[i] is stored on WorkflowStep at step_order i+1.
    """
    n = len(depends_on_previous_list)
    assert n >= 1
    unique = uuid.uuid4().hex[:8]
    business = User(
        email=f"biz-{unique}@t.com",
        password_hash="h",
        role=UserRole.BUSINESS,
    )
    dev = User(
        email=f"dev-{unique}@t.com",
        password_hash="h",
        role=UserRole.DEVELOPER,
    )
    db_session.add_all([business, dev])
    db_session.commit()
    db_session.refresh(business)
    db_session.refresh(dev)

    agents = []
    for i in range(n):
        a = Agent(
            developer_id=dev.id,
            name=f"Agent-{i}-{unique}",
            price_per_task=1.0,
            price_per_communication=0.1,
            api_endpoint=f"http://example.invalid/a{i}",
            a2a_enabled=True,
        )
        db_session.add(a)
        agents.append(a)
    db_session.commit()
    for a in agents:
        db_session.refresh(a)

    if total_cost is None:
        total_cost = float(n)

    job = Job(
        business_id=business.id,
        title=f"job-{unique}",
        status=JobStatus.IN_PROGRESS,
        files=json.dumps([]),
        conversation=json.dumps([]),
        total_cost=total_cost,
        write_execution_mode=write_execution_mode,
    )
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)

    base = {"job_title": job.title, "job_description": "", "documents": [], "conversation": []}
    steps = []
    for i in range(n):
        dep = depends_on_previous_list[i]
        st = WorkflowStep(
            job_id=job.id,
            agent_id=agents[i].id,
            step_order=i + 1,
            status="pending",
            input_data=json.dumps(base),
            depends_on_previous=dep,
        )
        db_session.add(st)
        steps.append(st)
    db_session.commit()
    for st in steps:
        db_session.refresh(st)
    return job, steps, agents


@pytest.mark.asyncio
async def test_execute_job_raises_when_job_missing(db_session):
    executor = AgentExecutor(db_session)
    with pytest.raises(ValueError, match="Job not found"):
        await executor.execute_job(9_999_001)


@pytest.mark.asyncio
async def test_execute_job_no_workflow_steps_marks_failed(db_session):
    unique = uuid.uuid4().hex[:8]
    business = User(
        email=f"biz-{unique}@t.com",
        password_hash="h",
        role=UserRole.BUSINESS,
    )
    db_session.add(business)
    db_session.commit()
    db_session.refresh(business)
    job = Job(
        business_id=business.id,
        title="empty",
        status=JobStatus.IN_PROGRESS,
        files=json.dumps([]),
        conversation=json.dumps([]),
        total_cost=0.0,
    )
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)

    executor = AgentExecutor(db_session)
    await executor.execute_job(job.id)

    db_session.refresh(job)
    assert job.status == JobStatus.FAILED
    assert "No workflow steps" in (job.failure_reason or "")


@pytest.mark.asyncio
@pytest.mark.parametrize("guardrail_code", ["mcp_circuit_open", "mcp_quota_exceeded", "mcp_rate_limited"])
async def test_execute_job_multi_step_surfaces_mcp_guardrail_code_in_failure_reason(db_session, guardrail_code):
    """User-visible failure reason should surface guardrail code when MCP path is blocked/fails."""
    job, steps, _ = _create_job_with_steps(db_session, depends_on_previous_list=[True, True])

    async def fake_core(self, job_id, step_id, prev):
        if step_id == steps[0].id:
            raise RuntimeError(guardrail_code)
        return {"ok": True}

    with patch.object(AgentExecutor, "_execute_one_step_core", new=fake_core):
        executor = AgentExecutor(db_session)
        with pytest.raises(RuntimeError):
            await executor.execute_job(job.id)

    db_session.refresh(job)
    assert job.status == JobStatus.FAILED
    assert guardrail_code in (job.failure_reason or "")


@pytest.mark.asyncio
async def test_execute_one_step_core_job_not_found(db_session):
    ex = AgentExecutor(db_session)
    with pytest.raises(ValueError, match="Job not found"):
        await ex._execute_one_step_core(9_999_002, 1, None)


@pytest.mark.asyncio
async def test_execute_one_step_core_step_not_found(db_session):
    unique = uuid.uuid4().hex[:8]
    business = User(email=f"b2-{unique}@t.com", password_hash="h", role=UserRole.BUSINESS)
    dev = User(email=f"d2-{unique}@t.com", password_hash="h", role=UserRole.DEVELOPER)
    db_session.add_all([business, dev])
    db_session.commit()
    db_session.refresh(business)
    db_session.refresh(dev)
    agent = Agent(
        developer_id=dev.id,
        name="A",
        price_per_task=1.0,
        price_per_communication=0.1,
        api_endpoint="http://example.invalid/a",
        a2a_enabled=True,
    )
    db_session.add(agent)
    db_session.commit()
    db_session.refresh(agent)
    job = Job(
        business_id=business.id,
        title="j",
        status=JobStatus.IN_PROGRESS,
        files=json.dumps([]),
        conversation=json.dumps([]),
        total_cost=1.0,
        write_execution_mode="ui_only",
    )
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)

    ex = AgentExecutor(db_session)
    with pytest.raises(ValueError, match="Workflow step .* not found"):
        await ex._execute_one_step_core(job.id, 9_999_003, None)


def _minimal_job_with_two_independent_steps(db_session):
    job, steps, _agents = _create_job_with_steps(db_session, depends_on_previous_list=[True, False])
    return job, steps[0], steps[1]


@pytest.mark.asyncio
async def test_single_step_job_completes(db_session):
    job, steps, _ = _create_job_with_steps(db_session, depends_on_previous_list=[True])

    async def fake_core(self, job_id, step_id, prev):
        assert prev is None
        return {"done": True}

    with patch.object(AgentExecutor, "_execute_one_step_core", new=fake_core):
        executor = AgentExecutor(db_session)
        await executor.execute_job(job.id)

    db_session.refresh(job)
    assert job.status == JobStatus.COMPLETED


@pytest.mark.asyncio
async def test_resume_skips_completed_step_and_preserves_chain_output(db_session):
    job, steps, _ = _create_job_with_steps(db_session, depends_on_previous_list=[True, True])
    steps[0].status = "completed"
    steps[0].output_data = json.dumps({"from_completed": True})
    db_session.commit()

    calls = []

    async def fake_core(self, job_id, step_id, prev):
        calls.append((step_id, prev))
        return {"executed_step": step_id}

    with patch.object(AgentExecutor, "_execute_one_step_core", new=fake_core):
        executor = AgentExecutor(db_session)
        await executor.execute_job(job.id)

    assert len(calls) == 1
    assert calls[0][0] == steps[1].id
    assert calls[0][1] == {"from_completed": True}
    db_session.refresh(job)
    assert job.status == JobStatus.COMPLETED


@pytest.mark.asyncio
async def test_resume_mixed_wave_runs_only_incomplete_steps_in_wave(db_session):
    # Waves: [step1], [step2, step3]
    job, steps, _ = _create_job_with_steps(db_session, depends_on_previous_list=[True, True, False])
    steps[1].status = "completed"
    steps[1].output_data = json.dumps({"already_done": 2})
    db_session.commit()

    calls = []

    async def fake_core(self, job_id, step_id, prev):
        calls.append(step_id)
        return {"executed_step": step_id}

    with patch.object(AgentExecutor, "_execute_one_step_core", new=fake_core):
        executor = AgentExecutor(db_session)
        await executor.execute_job(job.id)

    # step1 and step3 execute; completed step2 is skipped.
    assert calls == [steps[0].id, steps[2].id]
    db_session.refresh(job)
    assert job.status == JobStatus.COMPLETED


@pytest.mark.asyncio
async def test_execute_job_all_steps_completed_does_not_reexecute(db_session):
    job, steps, _ = _create_job_with_steps(db_session, depends_on_previous_list=[True, True])
    for st in steps:
        st.status = "completed"
        st.output_data = json.dumps({"done": st.id})
    db_session.commit()

    async def should_not_run(self, job_id, step_id, prev):
        raise AssertionError("completed steps should not be re-executed")

    with patch.object(AgentExecutor, "_execute_one_step_core", new=should_not_run):
        executor = AgentExecutor(db_session)
        await executor.execute_job(job.id)

    db_session.refresh(job)
    assert job.status == JobStatus.COMPLETED


@pytest.mark.asyncio
async def test_parallel_wave_invokes_core_concurrently(db_session):
    """Two independent steps in one wave should start both cores before either long sleep ends."""
    job, s1, s2 = _minimal_job_with_two_independent_steps(db_session)
    starts = {}

    async def fake_core(self, job_id, step_id, prev):
        starts[step_id] = time.monotonic()
        await asyncio.sleep(0.15)
        return {"step_id": step_id}

    with patch.object(AgentExecutor, "_execute_one_step_core", new=fake_core):
        executor = AgentExecutor(db_session)
        await executor.execute_job(job.id)

    t1, t2 = starts[s1.id], starts[s2.id]
    assert abs(t1 - t2) < 0.08, "both steps should start close together when parallel"


@pytest.mark.asyncio
async def test_three_step_parallel_wave_all_receive_none_previous(db_session):
    job, steps, _ = _create_job_with_steps(
        db_session,
        depends_on_previous_list=[True, False, False],
    )
    received = {}

    async def fake_core(self, job_id, step_id, prev):
        received[step_id] = prev
        return {"id": step_id}

    with patch.object(AgentExecutor, "_execute_one_step_core", new=fake_core):
        await AgentExecutor(db_session).execute_job(job.id)

    for st in steps:
        assert received[st.id] is None


@pytest.mark.asyncio
async def test_after_parallel_wave_next_step_gets_last_step_order_output(db_session):
    """
    Wave [1,2] parallel → previous_chain_output = results[-1] = highest step_order in wave.
    Step 3 (depends) must receive step 2's return value, not step 1's.
    """
    job, steps, _ = _create_job_with_steps(
        db_session,
        depends_on_previous_list=[True, False, True],
    )
    s1, s2, s3 = steps

    async def fake_core(self, job_id, step_id, prev):
        if step_id == s1.id:
            assert prev is None
            return {"wave": 1, "step_order": 1}
        if step_id == s2.id:
            assert prev is None
            return {"wave": 1, "step_order": 2}
        if step_id == s3.id:
            assert prev == {"wave": 1, "step_order": 2}
            return {"wave": 2}
        raise AssertionError("unknown step")

    with patch.object(AgentExecutor, "_execute_one_step_core", new=fake_core):
        await AgentExecutor(db_session).execute_job(job.id)

    db_session.refresh(job)
    assert job.status == JobStatus.COMPLETED


@pytest.mark.asyncio
async def test_parallel_disabled_runs_sequential_single_step_waves(db_session):
    job, s1, s2 = _minimal_job_with_two_independent_steps(db_session)
    order = []

    async def fake_core(self, job_id, step_id, prev):
        order.append(step_id)
        await asyncio.sleep(0.02)
        return {"step_id": step_id}

    with patch.object(ae.settings, "WORKFLOW_PARALLEL_INDEPENDENT_STEPS", False):
        with patch.object(AgentExecutor, "_execute_one_step_core", new=fake_core):
            executor = AgentExecutor(db_session)
            await executor.execute_job(job.id)

    assert order == [s1.id, s2.id]


@pytest.mark.asyncio
async def test_invalid_max_parallel_falls_back_to_eight(db_session):
    job, _s1, _s2 = _minimal_job_with_two_independent_steps(db_session)

    async def fake_core(self, job_id, step_id, prev):
        return {"step_id": step_id}

    with patch.object(ae.settings, "WORKFLOW_MAX_PARALLEL_STEPS", "not-an-int"):
        with patch.object(AgentExecutor, "_execute_one_step_core", new=fake_core):
            executor = AgentExecutor(db_session)
            await executor.execute_job(job.id)

    db_session.refresh(job)
    assert job.status == JobStatus.COMPLETED


@pytest.mark.asyncio
async def test_max_parallel_zero_coerces_to_eight_via_or_branch(db_session):
    """`getattr(...) or 8` treats 0 as falsy, so concurrency cap becomes 8."""
    job, _s1, _s2 = _minimal_job_with_two_independent_steps(db_session)

    async def fake_core(self, job_id, step_id, prev):
        return {"ok": True}

    with patch.object(ae.settings, "WORKFLOW_MAX_PARALLEL_STEPS", 0):
        with patch.object(AgentExecutor, "_execute_one_step_core", new=fake_core):
            await AgentExecutor(db_session).execute_job(job.id)

    db_session.refresh(job)
    assert job.status == JobStatus.COMPLETED


@pytest.mark.asyncio
async def test_max_parallel_negative_int_becomes_one(db_session):
    job, _s1, _s2 = _minimal_job_with_two_independent_steps(db_session)

    async def fake_core(self, job_id, step_id, prev):
        return {"ok": True}

    with patch.object(ae.settings, "WORKFLOW_MAX_PARALLEL_STEPS", -5):
        with patch.object(AgentExecutor, "_execute_one_step_core", new=fake_core):
            await AgentExecutor(db_session).execute_job(job.id)

    db_session.refresh(job)
    assert job.status == JobStatus.COMPLETED


@pytest.mark.asyncio
async def test_max_parallel_float_truncates_to_int(db_session):
    job, _s1, _s2 = _minimal_job_with_two_independent_steps(db_session)

    async def fake_core(self, job_id, step_id, prev):
        return {"ok": True}

    with patch.object(ae.settings, "WORKFLOW_MAX_PARALLEL_STEPS", 3.7):
        with patch.object(AgentExecutor, "_execute_one_step_core", new=fake_core):
            await AgentExecutor(db_session).execute_job(job.id)

    db_session.refresh(job)
    assert job.status == JobStatus.COMPLETED


@pytest.mark.asyncio
async def test_parallel_wave_failure_marks_job_failed(db_session):
    job, s1, s2 = _minimal_job_with_two_independent_steps(db_session)

    async def fake_core(self, job_id, step_id, prev):
        if step_id == s2.id:
            raise RuntimeError("step2 failed")
        await asyncio.sleep(0.05)
        return {"ok": True}

    with patch.object(AgentExecutor, "_execute_one_step_core", new=fake_core):
        executor = AgentExecutor(db_session)
        with pytest.raises(RuntimeError, match="step2 failed"):
            await executor.execute_job(job.id)

    db_session.refresh(job)
    assert job.status == JobStatus.FAILED
    assert "step2 failed" in (job.failure_reason or "")


@pytest.mark.asyncio
async def test_sequential_first_step_failure_marks_job_failed(db_session):
    """First wave is a single step (both steps depend) → no TaskGroup; failure is plain Exception."""
    job, steps, _ = _create_job_with_steps(db_session, depends_on_previous_list=[True, True])
    s1, _s2 = steps

    async def fake_core(self, job_id, step_id, prev):
        if step_id == s1.id:
            raise RuntimeError("wave1 failed")
        return {"ok": True}

    with patch.object(AgentExecutor, "_execute_one_step_core", new=fake_core):
        with pytest.raises(RuntimeError, match="wave1 failed"):
            await AgentExecutor(db_session).execute_job(job.id)

    db_session.refresh(job)
    assert job.status == JobStatus.FAILED
    assert "wave1 failed" in (job.failure_reason or "")


@pytest.mark.asyncio
async def test_core_logs_communication_when_step_depends_on_previous(db_session):
    job, steps, _ = _create_job_with_steps(db_session, depends_on_previous_list=[True, True])
    s1, s2 = steps
    ex = AgentExecutor(db_session)

    with (
        patch.object(ex, "_get_available_mcp_tools_async", new_callable=AsyncMock, return_value=[]),
        patch.object(ex, "_execute_agent", new_callable=AsyncMock, return_value={"content": "ok"}),
    ):
        with patch.object(AgentExecutor, "_log_communication") as log_c:
            await ex._execute_one_step_core(job.id, s1.id, None)
        log_c.assert_not_called()

        chain = {"from_step": 1}
        with patch.object(AgentExecutor, "_log_communication") as log_c:
            await ex._execute_one_step_core(job.id, s2.id, chain)
        log_c.assert_called_once()
        args = log_c.call_args[0]
        assert args[0].id == s1.id and args[1].id == s2.id
        assert args[2] == chain


@pytest.mark.asyncio
async def test_core_skips_communication_when_step_independent_even_with_chain(db_session):
    """Parallel semantics: previous_chain may be set but independent steps must not log handoff."""
    job, steps, _ = _create_job_with_steps(db_session, depends_on_previous_list=[True, False])
    _s1, s2 = steps
    ex = AgentExecutor(db_session)

    with (
        patch.object(ex, "_get_available_mcp_tools_async", new_callable=AsyncMock, return_value=[]),
        patch.object(ex, "_execute_agent", new_callable=AsyncMock, return_value={"content": "ok"}),
    ):
        with patch.object(AgentExecutor, "_log_communication") as log_c:
            await ex._execute_one_step_core(job.id, s2.id, {"stale": True})
        log_c.assert_not_called()


@pytest.mark.asyncio
async def test_core_retryable_status_retries_then_succeeds(db_session):
    job, steps, _ = _create_job_with_steps(db_session, depends_on_previous_list=[True])
    s1 = steps[0]
    ex = AgentExecutor(db_session)

    with (
        patch.object(ex, "_get_available_mcp_tools_async", new_callable=AsyncMock, return_value=[]),
        patch.object(
            ex,
            "_execute_agent",
            new_callable=AsyncMock,
            side_effect=[{"status": "retryable_error"}, {"content": "ok"}],
        ) as run_agent,
        patch.object(ae.settings, "AGENT_STEP_TIMEOUT_SECONDS", 2.0),
        patch.object(ae.settings, "AGENT_STEP_MAX_RETRIES", 2),
        patch.object(ae.settings, "AGENT_STEP_RETRY_BACKOFF_SECONDS", 0.0),
    ):
        out = await ex._execute_one_step_core(job.id, s1.id, None)

    assert out == {"content": "ok"}
    assert run_agent.await_count == 2
    db_session.refresh(s1)
    payload = json.loads(s1.output_data or "{}")
    gm = payload.get("guardrail_meta") or {}
    assert gm.get("attempts_used") == 2
    assert gm.get("retryable_failures") == 1


@pytest.mark.asyncio
async def test_core_low_confidence_fails_without_retry(db_session):
    job, steps, _ = _create_job_with_steps(db_session, depends_on_previous_list=[True])
    s1 = steps[0]
    ex = AgentExecutor(db_session)

    with (
        patch.object(ex, "_get_available_mcp_tools_async", new_callable=AsyncMock, return_value=[]),
        patch.object(ex, "_execute_agent", new_callable=AsyncMock, return_value={"content": "maybe", "confidence": 0.2}) as run_agent,
        patch.object(ae.settings, "AGENT_OUTPUT_MIN_CONFIDENCE", 0.8),
        patch.object(ae.settings, "AGENT_STEP_MAX_RETRIES", 3),
    ):
        with pytest.raises(Exception, match="Low confidence output"):
            await ex._execute_one_step_core(job.id, s1.id, None)

    # Low confidence is a hard quality gate (non-retryable ValueError).
    assert run_agent.await_count == 1
    db_session.refresh(s1)
    assert s1.status == "failed"


@pytest.mark.asyncio
async def test_core_timeout_retries_then_succeeds(db_session):
    job, steps, _ = _create_job_with_steps(db_session, depends_on_previous_list=[True])
    s1 = steps[0]
    ex = AgentExecutor(db_session)
    call_count = {"n": 0}

    async def flaky_execute(_agent, _input):
        call_count["n"] += 1
        if call_count["n"] == 1:
            await asyncio.sleep(0.05)
            return {"content": "late"}
        return {"content": "ok"}

    with (
        patch.object(ex, "_get_available_mcp_tools_async", new_callable=AsyncMock, return_value=[]),
        patch.object(ex, "_execute_agent", new=flaky_execute),
        patch.object(ae.settings, "AGENT_STEP_TIMEOUT_SECONDS", 0.01),
        patch.object(ae.settings, "AGENT_STEP_MAX_RETRIES", 2),
        patch.object(ae.settings, "AGENT_STEP_RETRY_BACKOFF_SECONDS", 0.0),
    ):
        out = await ex._execute_one_step_core(job.id, s1.id, None)

    assert out == {"content": "ok"}
    assert call_count["n"] == 2
    db_session.refresh(s1)
    payload = json.loads(s1.output_data or "{}")
    gm = payload.get("guardrail_meta") or {}
    assert gm.get("attempts_used") == 2
    assert gm.get("retryable_failures") == 1


@pytest.mark.asyncio
async def test_core_max_retries_exhausted_persists_guardrail_meta(db_session):
    """When all attempts hit retryable failures, final step output still includes guardrail telemetry."""
    job, steps, _ = _create_job_with_steps(db_session, depends_on_previous_list=[True])
    s1 = steps[0]
    ex = AgentExecutor(db_session)

    with (
        patch.object(ex, "_get_available_mcp_tools_async", new_callable=AsyncMock, return_value=[]),
        patch.object(ex, "_execute_agent", new_callable=AsyncMock, return_value={"status": "retryable_error"}) as run_agent,
        patch.object(ae.settings, "AGENT_STEP_TIMEOUT_SECONDS", 2.0),
        patch.object(ae.settings, "AGENT_STEP_MAX_RETRIES", 3),
        patch.object(ae.settings, "AGENT_STEP_RETRY_BACKOFF_SECONDS", 0.0),
    ):
        with pytest.raises(Exception, match="retryable_error"):
            await ex._execute_one_step_core(job.id, s1.id, None)

    assert run_agent.await_count == 3
    db_session.refresh(s1)
    assert s1.status == "failed"
    payload = json.loads(s1.output_data or "{}")
    assert "retryable_error" in (payload.get("error") or "")
    gm = payload.get("guardrail_meta") or {}
    assert gm.get("max_retries") == 3
    assert gm.get("attempts_used") == 3
    assert gm.get("retryable_failures") == 3
    assert gm.get("timeout_seconds") == 2.0


@pytest.mark.asyncio
async def test_core_uses_trace_only_enrichment_when_payload_validation_disabled(db_session):
    job, steps, _ = _create_job_with_steps(db_session, depends_on_previous_list=[True])
    s1 = steps[0]
    ex = AgentExecutor(db_session)

    with (
        patch.object(app_settings, "EXECUTOR_PAYLOAD_VALIDATE", False),
        patch.object(ex, "_get_available_mcp_tools_async", new_callable=AsyncMock, return_value=[]),
        patch.object(ex, "_execute_agent", new_callable=AsyncMock, return_value={"content": "ok"}),
    ):
        await ex._execute_one_step_core(job.id, s1.id, None)

    db_session.refresh(s1)
    assert s1.status == "completed"


@pytest.mark.asyncio
async def test_core_invalid_output_artifact_format_defaults_to_jsonl(db_session):
    job, steps, _ = _create_job_with_steps(db_session, depends_on_previous_list=[True])
    job.output_artifact_format = "weird"
    db_session.commit()
    db_session.refresh(job)
    s1 = steps[0]
    ex = AgentExecutor(db_session)

    with (
        patch.object(ex, "_get_available_mcp_tools_async", new_callable=AsyncMock, return_value=[]),
        patch.object(ex, "_execute_agent", new_callable=AsyncMock, return_value={"content": "ok"}),
    ):
        await ex._execute_one_step_core(job.id, s1.id, None)

    db_session.refresh(s1)
    payload = json.loads(s1.output_data or "{}")
    assert (payload.get("artifact_ref") or {}).get("format") == "jsonl"


@pytest.mark.asyncio
async def test_core_platform_write_json_artifact_skips_non_dict_targets(db_session):
    job, steps, _ = _create_job_with_steps(
        db_session,
        depends_on_previous_list=[True],
        write_execution_mode="platform",
    )
    job.output_artifact_format = "json"
    job.output_contract = json.dumps(
        {
            "write_targets": [
                "skip-me",
                {
                    "tool_name": "platform_1_postgres",
                    "operation_type": "upsert",
                    "target": {"schema": "public", "table": "events"},
                },
            ],
            "write_policy": {"on_write_error": "continue", "min_successful_targets": 0},
        }
    )
    db_session.commit()
    db_session.refresh(job)
    s1 = steps[0]
    ex = AgentExecutor(db_session)

    async def fake_persist(j, st, output_data):
        assert st.id == s1.id
        return {
            "artifact_id": "art-1",
            "storage": "local",
            "bucket": None,
            "key": "/tmp/out.json",
            "format": "json",
            "size_bytes": 12,
            "created_at": "2026-01-01T00:00:00Z",
        }

    trigger = AsyncMock(return_value={"inserted": 1})

    with (
        patch.object(ex, "_get_available_mcp_tools_async", new_callable=AsyncMock, return_value=[]),
        patch.object(
            ex,
            "_execute_agent",
            new_callable=AsyncMock,
            return_value={"records": [{"id": 1}]},
        ),
        patch.object(ex, "_persist_output_artifact", new_callable=AsyncMock, side_effect=fake_persist),
        patch.object(ex, "_trigger_platform_write", new=trigger),
    ):
        await ex._execute_one_step_core(job.id, s1.id, None)

    trigger.assert_awaited_once()
    db_session.refresh(s1)
    assert s1.status == "completed"
    payload = json.loads(s1.output_data or "{}")
    wr = payload.get("write_results") or []
    assert len(wr) == 1
    assert wr[0].get("status") == "success"


@pytest.mark.asyncio
async def test_core_platform_write_records_failed_target_when_mcp_raises(db_session):
    job, steps, _ = _create_job_with_steps(
        db_session,
        depends_on_previous_list=[True],
        write_execution_mode="platform",
    )
    job.output_contract = json.dumps(
        {
            "write_targets": [{"tool_name": "platform_1_postgres", "target": {"schema": "public", "table": "t"}}],
            "write_policy": {"on_write_error": "continue", "min_successful_targets": 0},
        }
    )
    db_session.commit()
    s1 = steps[0]
    ex = AgentExecutor(db_session)

    async def fake_persist(j, st, output_data):
        return {"artifact_id": "a1", "storage": "local", "key": "k", "format": "jsonl"}

    with (
        patch.object(ex, "_get_available_mcp_tools_async", new_callable=AsyncMock, return_value=[]),
        patch.object(
            ex,
            "_execute_agent",
            new_callable=AsyncMock,
            return_value={"records": [{"id": 1}]},
        ),
        patch.object(ex, "_persist_output_artifact", new_callable=AsyncMock, side_effect=fake_persist),
        patch.object(ex, "_trigger_platform_write", new_callable=AsyncMock, side_effect=RuntimeError("mcp down")),
    ):
        await ex._execute_one_step_core(job.id, s1.id, None)

    db_session.refresh(s1)
    assert s1.status == "completed"
    payload = json.loads(s1.output_data or "{}")
    wr = payload.get("write_results") or []
    assert len(wr) == 1
    assert wr[0].get("status") == "failed"
    assert "mcp down" in (wr[0].get("error") or "")


@pytest.mark.asyncio
async def test_core_platform_write_fails_job_when_min_successful_not_met(db_session):
    job, steps, _ = _create_job_with_steps(
        db_session,
        depends_on_previous_list=[True],
        write_execution_mode="platform",
    )
    job.output_contract = json.dumps(
        {
            "write_targets": [{"tool_name": "platform_1_postgres", "target": {"schema": "public", "table": "t"}}],
            "write_policy": {"on_write_error": "continue", "min_successful_targets": 1},
        }
    )
    db_session.commit()
    s1 = steps[0]
    ex = AgentExecutor(db_session)

    async def fake_persist(j, st, output_data):
        return {"artifact_id": "a1", "storage": "local", "key": "k", "format": "jsonl"}

    with (
        patch.object(ex, "_get_available_mcp_tools_async", new_callable=AsyncMock, return_value=[]),
        patch.object(
            ex,
            "_execute_agent",
            new_callable=AsyncMock,
            return_value={"records": [{"id": 1}]},
        ),
        patch.object(ex, "_persist_output_artifact", new_callable=AsyncMock, side_effect=fake_persist),
        patch.object(ex, "_trigger_platform_write", new_callable=AsyncMock, side_effect=RuntimeError("mcp down")),
    ):
        with pytest.raises(Exception, match="Write policy violation"):
            await ex._execute_one_step_core(job.id, s1.id, None)


@pytest.mark.asyncio
async def test_core_platform_write_fail_job_re_raises_from_trigger(db_session):
    job, steps, _ = _create_job_with_steps(
        db_session,
        depends_on_previous_list=[True],
        write_execution_mode="platform",
    )
    job.output_contract = json.dumps(
        {
            "write_targets": [{"tool_name": "platform_1_postgres", "target": {"schema": "public", "table": "t"}}],
            "write_policy": {"on_write_error": "fail_job", "min_successful_targets": 0},
        }
    )
    db_session.commit()
    s1 = steps[0]
    ex = AgentExecutor(db_session)

    async def fake_persist(j, st, output_data):
        return {"artifact_id": "a1", "storage": "local", "key": "k", "format": "jsonl"}

    with (
        patch.object(ex, "_get_available_mcp_tools_async", new_callable=AsyncMock, return_value=[]),
        patch.object(
            ex,
            "_execute_agent",
            new_callable=AsyncMock,
            return_value={"records": [{"id": 1}]},
        ),
        patch.object(ex, "_persist_output_artifact", new_callable=AsyncMock, side_effect=fake_persist),
        patch.object(ex, "_trigger_platform_write", new_callable=AsyncMock, side_effect=RuntimeError("mcp boom")),
    ):
        with pytest.raises(Exception, match="mcp boom"):
            await ex._execute_one_step_core(job.id, s1.id, None)


@pytest.mark.asyncio
async def test_alternating_waves_sequential_then_parallel_then_sequential(db_session):
    """
    Partition T,F,T,F → waves [[s1,s2],[s3,s4]]. Step 3 and 4 both get chain from step 2 output.
    """
    job, steps, _ = _create_job_with_steps(
        db_session,
        depends_on_previous_list=[True, False, True, False],
    )
    s1, s2, s3, s4 = steps

    async def fake_core(self, job_id, step_id, prev):
        if step_id == s1.id:
            assert prev is None
            return {"order": 1}
        if step_id == s2.id:
            assert prev is None
            return {"order": 2}
        if step_id == s3.id:
            assert prev == {"order": 2}
            return {"order": 3}
        if step_id == s4.id:
            assert prev == {"order": 2}
            return {"order": 4}
        raise AssertionError(step_id)

    with patch.object(AgentExecutor, "_execute_one_step_core", new=fake_core):
        await AgentExecutor(db_session).execute_job(job.id)

    db_session.refresh(job)
    assert job.status == JobStatus.COMPLETED


@pytest.mark.asyncio
async def test_semaphore_limits_parallel_starts_when_max_parallel_one(db_session):
    """With cap 1, parallel wave runs cores one-at-a-time (second start after first sleep)."""
    job, steps, _ = _create_job_with_steps(
        db_session,
        depends_on_previous_list=[True, False, False],
    )
    events = []

    async def fake_core(self, job_id, step_id, prev):
        t0 = time.monotonic()
        events.append(("start", step_id, t0))
        await asyncio.sleep(0.07)
        events.append(("end", step_id, time.monotonic()))
        return {"id": step_id}

    with patch.object(ae.settings, "WORKFLOW_MAX_PARALLEL_STEPS", 1):
        with patch.object(AgentExecutor, "_execute_one_step_core", new=fake_core):
            await AgentExecutor(db_session).execute_job(job.id)

    starts = [e for e in events if e[0] == "start"]
    assert len(starts) == 3
    # Second start should not happen until ~one sleep after first start (serialized by semaphore)
    assert starts[1][2] >= starts[0][2] + 0.055
    assert starts[2][2] >= starts[1][2] + 0.055


@pytest.mark.asyncio
async def test_nested_exceptiongroup_unwraps_to_inner_exception(db_session):
    """execute_job handler walks BaseExceptionGroup to the first leaf for failure_reason."""

    class _FakeTaskGroup:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            raise BaseExceptionGroup(
                "multi",
                [ValueError("inner leaf")],
            )

    job, _s1, _s2 = _minimal_job_with_two_independent_steps(db_session)

    async def fake_core(self, job_id, step_id, prev):
        return {"ok": True}

    with patch.object(asyncio, "TaskGroup", _FakeTaskGroup):
        with patch.object(AgentExecutor, "_execute_one_step_core", new=fake_core):
            with pytest.raises(ValueError, match="inner leaf"):
                await AgentExecutor(db_session).execute_job(job.id)

    db_session.refresh(job)
    assert job.status == JobStatus.FAILED
    assert "inner leaf" in (job.failure_reason or "")


@pytest.mark.asyncio
async def test_execute_job_planner_replan_failure_marks_job_failed(db_session, monkeypatch):
    """When execute-time replan is enabled and planner fails, job is FAILED without running steps."""
    monkeypatch.setattr(app_settings, "AGENT_PLANNER_EXECUTE_REPLAN_ON_FAILURE", "fail")
    job, _steps, _ = _create_job_with_steps(db_session, depends_on_previous_list=[False, False])
    executor = AgentExecutor(db_session)
    calc_mock = MagicMock()
    pay_mock = MagicMock()
    with patch("services.planner_llm.is_agent_planner_configured", return_value=True):
        with patch.object(
            WorkflowBuilder,
            "replan_workflow_steps_at_execute_async",
            new=AsyncMock(side_effect=PlannerSplitError("simulated planner failure", last_detail="invalid JSON")),
        ):
            with patch.object(executor.payment_processor, "calculate_job_cost", calc_mock):
                with patch.object(executor.payment_processor, "process_payment", pay_mock):
                    await executor.execute_job(job.id)
    db_session.refresh(job)
    assert job.status == JobStatus.FAILED
    assert "simulated planner failure" in (job.failure_reason or "")
    calc_mock.assert_not_called()
    pay_mock.assert_not_called()


@pytest.mark.asyncio
async def test_execute_job_planner_replan_failure_continue_runs_built_workflow(db_session, monkeypatch):
    """AGENT_PLANNER_EXECUTE_REPLAN_ON_FAILURE=continue keeps the pre-built workflow and charges after failed replan."""
    monkeypatch.setattr(app_settings, "AGENT_PLANNER_EXECUTE_REPLAN_ON_FAILURE", "continue")
    job, _steps, _ = _create_job_with_steps(db_session, depends_on_previous_list=[False, False])
    executor = AgentExecutor(db_session)
    fake_tx = MagicMock()
    fake_tx.id = 42

    async def fake_core(self, job_id, step_id, prev):
        return {"ok": True}

    with patch("services.planner_llm.is_agent_planner_configured", return_value=True):
        with patch.object(
            WorkflowBuilder,
            "replan_workflow_steps_at_execute_async",
            new=AsyncMock(side_effect=PlannerSplitError("planner down")),
        ):
            with patch.object(executor.payment_processor, "calculate_job_cost", lambda jid: None):
                with patch.object(executor.payment_processor, "process_payment", return_value=fake_tx):
                    with patch.object(executor.payment_processor, "distribute_earnings", lambda jid: None):
                        with patch.object(AgentExecutor, "_execute_one_step_core", new=fake_core):
                            await executor.execute_job(job.id)
    db_session.refresh(job)
    assert job.status == JobStatus.COMPLETED


@pytest.mark.asyncio
async def test_execute_job_skips_replan_for_manual_workflow_origin(db_session):
    """Manual workflows must not be overwritten by execute-time replan."""
    job, _steps, _ = _create_job_with_steps(db_session, depends_on_previous_list=[False, False])
    job.workflow_origin = "manual"
    db_session.commit()
    db_session.refresh(job)
    executor = AgentExecutor(db_session)

    async def fake_core(self, job_id, step_id, prev):
        return {"ok": True}

    replan_mock = AsyncMock()
    fake_tx = MagicMock()
    fake_tx.id = 43
    with patch("services.planner_llm.is_agent_planner_configured", return_value=True):
        with patch.object(WorkflowBuilder, "replan_workflow_steps_at_execute_async", replan_mock):
            with patch.object(executor.payment_processor, "calculate_job_cost", lambda jid: None):
                with patch.object(executor.payment_processor, "process_payment", return_value=fake_tx):
                    with patch.object(executor.payment_processor, "distribute_earnings", lambda jid: None):
                        with patch.object(AgentExecutor, "_execute_one_step_core", new=fake_core):
                            await executor.execute_job(job.id)
    replan_mock.assert_not_called()
    db_session.refresh(job)
    assert job.status == JobStatus.COMPLETED


@pytest.mark.asyncio
async def test_execute_job_calculates_cost_before_payment(db_session):
    with patch("services.planner_llm.is_agent_planner_configured", return_value=False):
        job, _steps, _ = _create_job_with_steps(db_session, depends_on_previous_list=[True])
        executor = AgentExecutor(db_session)
        order: list = []

        def calc(jid):
            order.append("calc")

        fake_tx = MagicMock()
        fake_tx.id = 44

        def pay(jid):
            order.append("pay")
            return fake_tx

        async def fake_core(self, job_id, step_id, prev):
            return {"ok": True}

        with patch.object(executor.payment_processor, "calculate_job_cost", side_effect=calc):
            with patch.object(executor.payment_processor, "process_payment", side_effect=pay):
                with patch.object(executor.payment_processor, "distribute_earnings", lambda jid: None):
                    with patch.object(AgentExecutor, "_execute_one_step_core", new=fake_core):
                        await executor.execute_job(job.id)
        assert order == ["calc", "pay"]
