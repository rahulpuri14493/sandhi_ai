"""Integration-style coverage for workflow_builder: real DB session, document load, replan, manual workflow."""
import concurrent.futures
import json
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from models.agent import Agent
from models.job import Job, JobStatus, WorkflowStep
from models.user import User, UserRole
from services.payment_processor import PaymentProcessor
from services.workflow_builder import WorkflowBuilder, _normalized_job_output_settings
from core.config import settings


def _seed_business_dev_agents(db_session, n_agents: int = 2):
    uid = uuid.uuid4().hex[:8]
    business = User(email=f"biz-{uid}@t.com", password_hash="h", role=UserRole.BUSINESS)
    dev = User(email=f"dev-{uid}@t.com", password_hash="h", role=UserRole.DEVELOPER)
    db_session.add_all([business, dev])
    db_session.commit()
    db_session.refresh(business)
    db_session.refresh(dev)
    agents = []
    for i in range(n_agents):
        a = Agent(
            developer_id=dev.id,
            name=f"A{i}-{uid}",
            description=f"D{i}",
            price_per_task=1.0,
            price_per_communication=0.1,
            api_endpoint=f"http://example.invalid/{i}",
        )
        db_session.add(a)
        agents.append(a)
    db_session.commit()
    for a in agents:
        db_session.refresh(a)
    return business, dev, agents


def _job(db_session, business_id: int, **kwargs) -> Job:
    defaults = dict(
        business_id=business_id,
        title="t",
        status=JobStatus.DRAFT,
        files=json.dumps([]),
        conversation=json.dumps([]),
        total_cost=2.0,
    )
    defaults.update(kwargs)
    job = Job(**defaults)
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)
    return job


def test_normalized_job_output_settings_invalid_values_use_defaults():
    job = MagicMock()
    job.write_execution_mode = "not_a_mode"
    job.output_artifact_format = "yaml"
    wm, af = _normalized_job_output_settings(job)
    assert wm == "platform"
    assert af == "jsonl"


def test_normalized_job_output_settings_non_string_coercion():
    job = MagicMock()
    job.write_execution_mode = 123
    job.output_artifact_format = None
    wm, af = _normalized_job_output_settings(job)
    assert wm == "platform"
    assert af == "jsonl"


def test_normalized_job_output_settings_valid_agent_json():
    job = MagicMock()
    job.write_execution_mode = " agent "
    job.output_artifact_format = " JSON "
    wm, af = _normalized_job_output_settings(job)
    assert wm == "agent"
    assert af == "json"


@pytest.mark.asyncio
async def test_load_job_documents_empty_when_no_files(db_session):
    business, _dev, _agents = _seed_business_dev_agents(db_session, 1)
    job = _job(db_session, business.id, files=None)
    out = await WorkflowBuilder(db_session).load_job_documents_content_async(job)
    assert out == []


@pytest.mark.asyncio
async def test_load_job_documents_invalid_files_json_returns_empty(db_session):
    business, _dev, _agents = _seed_business_dev_agents(db_session, 1)
    job = _job(db_session, business.id, files="{not valid json")
    out = await WorkflowBuilder(db_session).load_job_documents_content_async(job)
    assert out == []


@pytest.mark.asyncio
async def test_load_job_documents_skips_entry_without_source_metadata(db_session):
    business, _dev, _agents = _seed_business_dev_agents(db_session, 1)
    job = _job(db_session, business.id, files=json.dumps([{"name": "orphan", "type": "x"}]))
    out = await WorkflowBuilder(db_session).load_job_documents_content_async(job)
    assert out == []


@pytest.mark.asyncio
async def test_load_job_documents_skips_empty_file_content(db_session):
    business, _dev, _agents = _seed_business_dev_agents(db_session, 1)
    job = _job(
        db_session,
        business.id,
        files=json.dumps([{"name": "e.txt", "path": "/tmp/x", "type": "text/plain"}]),
    )
    with patch("services.document_analyzer.DocumentAnalyzer") as cls:
        inst = MagicMock()
        inst.read_file_info = AsyncMock(return_value="   \n")
        cls.return_value = inst
        out = await WorkflowBuilder(db_session).load_job_documents_content_async(job)
    assert out == []


@pytest.mark.asyncio
async def test_load_job_documents_reads_path_and_s3_metadata(db_session):
    business, _dev, _agents = _seed_business_dev_agents(db_session, 1)
    job = _job(
        db_session,
        business.id,
        files=json.dumps(
            [
                {"id": "BRD1", "name": "a.txt", "path": "/tmp/a", "type": "text/plain"},
                {
                    "id": "BRD2",
                    "name": "b.bin",
                    "storage": "s3",
                    "bucket": "b",
                    "key": "k",
                    "type": "application/octet-stream",
                },
            ]
        ),
    )
    with patch("services.document_analyzer.DocumentAnalyzer") as cls:
        inst = MagicMock()
        inst.read_file_info = AsyncMock(side_effect=["alpha body", "beta body"])
        cls.return_value = inst
        out = await WorkflowBuilder(db_session).load_job_documents_content_async(job)
    assert len(out) == 2
    assert out[0]["id"] == "BRD1" and "alpha" in out[0]["content"]
    assert out[1]["id"] == "BRD2" and "beta" in out[1]["content"]


@pytest.mark.asyncio
async def test_load_job_documents_read_failure_skips_doc(db_session):
    business, _dev, _agents = _seed_business_dev_agents(db_session, 1)
    job = _job(
        db_session,
        business.id,
        files=json.dumps([{"name": "bad.txt", "path": "/tmp/bad", "type": "text/plain"}]),
    )
    with patch("services.document_analyzer.DocumentAnalyzer") as cls:
        inst = MagicMock()
        inst.read_file_info = AsyncMock(side_effect=OSError("boom"))
        cls.return_value = inst
        out = await WorkflowBuilder(db_session).load_job_documents_content_async(job)
    assert out == []


def test_load_job_documents_content_sync_wrapper(db_session):
    business, _dev, _agents = _seed_business_dev_agents(db_session, 1)
    job = _job(db_session, business.id, files=None)
    job.files = ""
    db_session.commit()
    out = WorkflowBuilder(db_session).load_job_documents_content(job)
    assert out == []


@pytest.mark.asyncio
async def test_replan_skips_manual_workflow_origin(db_session, monkeypatch):
    monkeypatch.setattr(settings, "AGENT_PLANNER_EXECUTE_REPLAN", True)
    business, _dev, agents = _seed_business_dev_agents(db_session, 2)
    job = _job(db_session, business.id, status=JobStatus.IN_PROGRESS)
    job.workflow_origin = "manual"
    db_session.commit()
    for i, a in enumerate(agents):
        db_session.add(
            WorkflowStep(
                job_id=job.id,
                agent_id=a.id,
                step_order=i + 1,
                input_data=json.dumps({}),
                status="pending",
                depends_on_previous=False,
            )
        )
    db_session.commit()
    builder = WorkflowBuilder(db_session)
    with patch("services.workflow_builder.is_agent_planner_configured", return_value=True):
        with patch.object(builder, "auto_split_workflow_async", new_callable=AsyncMock) as m:
            await builder.replan_workflow_steps_at_execute_async(job.id)
    m.assert_not_called()


@pytest.mark.asyncio
async def test_replan_execute_replan_disabled_no_auto_split(db_session, monkeypatch):
    monkeypatch.setattr(settings, "AGENT_PLANNER_EXECUTE_REPLAN", False)
    business, _dev, agents = _seed_business_dev_agents(db_session, 2)
    job = _job(db_session, business.id, status=JobStatus.IN_PROGRESS)
    for i, a in enumerate(agents):
        db_session.add(
            WorkflowStep(
                job_id=job.id,
                agent_id=a.id,
                step_order=i + 1,
                input_data=json.dumps({"task_type": "search"}),
                status="pending",
                depends_on_previous=i > 0,
            )
        )
    db_session.commit()
    builder = WorkflowBuilder(db_session)
    with patch.object(builder, "auto_split_workflow_async", new_callable=AsyncMock) as m:
        await builder.replan_workflow_steps_at_execute_async(job.id)
    m.assert_not_called()


@pytest.mark.asyncio
async def test_replan_planner_not_configured_no_auto_split(db_session, monkeypatch):
    monkeypatch.setattr(settings, "AGENT_PLANNER_EXECUTE_REPLAN", True)
    business, _dev, agents = _seed_business_dev_agents(db_session, 2)
    job = _job(db_session, business.id, status=JobStatus.IN_PROGRESS)
    for i, a in enumerate(agents):
        db_session.add(
            WorkflowStep(
                job_id=job.id,
                agent_id=a.id,
                step_order=i + 1,
                input_data=json.dumps({}),
                status="pending",
                depends_on_previous=False,
            )
        )
    db_session.commit()
    builder = WorkflowBuilder(db_session)
    with patch("services.workflow_builder.is_agent_planner_configured", return_value=False):
        with patch.object(builder, "auto_split_workflow_async", new_callable=AsyncMock) as m:
            await builder.replan_workflow_steps_at_execute_async(job.id)
    m.assert_not_called()


@pytest.mark.asyncio
async def test_replan_single_step_no_auto_split(db_session, monkeypatch):
    monkeypatch.setattr(settings, "AGENT_PLANNER_EXECUTE_REPLAN", True)
    business, _dev, agents = _seed_business_dev_agents(db_session, 1)
    job = _job(db_session, business.id, status=JobStatus.IN_PROGRESS)
    db_session.add(
        WorkflowStep(
            job_id=job.id,
            agent_id=agents[0].id,
            step_order=1,
            input_data=json.dumps({}),
            status="pending",
            depends_on_previous=False,
        )
    )
    db_session.commit()
    builder = WorkflowBuilder(db_session)
    with patch("services.workflow_builder.is_agent_planner_configured", return_value=True):
        with patch.object(builder, "auto_split_workflow_async", new_callable=AsyncMock) as m:
            await builder.replan_workflow_steps_at_execute_async(job.id)
    m.assert_not_called()


@pytest.mark.asyncio
async def test_replan_job_missing_raises(db_session, monkeypatch):
    monkeypatch.setattr(settings, "AGENT_PLANNER_EXECUTE_REPLAN", True)
    with patch("services.workflow_builder.is_agent_planner_configured", return_value=True):
        with pytest.raises(ValueError, match="Job not found"):
            await WorkflowBuilder(db_session).replan_workflow_steps_at_execute_async(999_999_001)


@pytest.mark.asyncio
async def test_replan_delegates_auto_split_sequential_mode(db_session, monkeypatch):
    monkeypatch.setattr(settings, "AGENT_PLANNER_EXECUTE_REPLAN", True)
    business, _dev, agents = _seed_business_dev_agents(db_session, 2)
    job = _job(db_session, business.id, tool_visibility="full")
    for i, a in enumerate(agents):
        db_session.add(
            WorkflowStep(
                job_id=job.id,
                agent_id=a.id,
                step_order=i + 1,
                input_data=json.dumps({"task_type": "Analyze"}),
                status="pending",
                allowed_platform_tool_ids=json.dumps([1, 2]),
                allowed_connection_ids="{not-json",
                tool_visibility="names_only",
                depends_on_previous=(i > 0),
            )
        )
    db_session.commit()
    builder = WorkflowBuilder(db_session)
    with patch("services.workflow_builder.is_agent_planner_configured", return_value=True):
        with patch.object(builder, "auto_split_workflow_async", new_callable=AsyncMock) as m:
            await builder.replan_workflow_steps_at_execute_async(job.id)
    m.assert_awaited_once()
    assert m.await_args.args[0] == job.id
    assert m.await_args.args[1] == [agents[0].id, agents[1].id]
    assert m.await_args.kwargs["workflow_mode"] == "sequential"
    assert m.await_args.kwargs["tool_visibility"] == "full"
    st = m.await_args.kwargs["step_tools"]
    assert st[0]["task_type"] == "analyze"
    assert st[0]["allowed_platform_tool_ids"] == [1, 2]
    assert st[0]["allowed_connection_ids"] is None


@pytest.mark.asyncio
async def test_replan_delegates_auto_split_independent_mode(db_session, monkeypatch):
    monkeypatch.setattr(settings, "AGENT_PLANNER_EXECUTE_REPLAN", True)
    business, _dev, agents = _seed_business_dev_agents(db_session, 2)
    job = _job(db_session, business.id)
    for i, a in enumerate(agents):
        db_session.add(
            WorkflowStep(
                job_id=job.id,
                agent_id=a.id,
                step_order=i + 1,
                input_data="{not json",
                status="pending",
                depends_on_previous=False,
            )
        )
    db_session.commit()
    builder = WorkflowBuilder(db_session)
    with patch("services.workflow_builder.is_agent_planner_configured", return_value=True):
        with patch.object(builder, "auto_split_workflow_async", new_callable=AsyncMock) as m:
            await builder.replan_workflow_steps_at_execute_async(job.id)
    assert m.await_args.kwargs["workflow_mode"] == "independent"


@pytest.mark.asyncio
async def test_create_manual_workflow_async_job_not_found(db_session):
    with pytest.raises(ValueError, match="Job not found"):
        await WorkflowBuilder(db_session).create_manual_workflow_async(
            999_888_777,
            [{"agent_id": 1, "step_order": 1}],
        )


@pytest.mark.asyncio
async def test_create_manual_workflow_async_agent_not_found(db_session):
    business, _dev, agents = _seed_business_dev_agents(db_session, 1)
    job = _job(db_session, business.id)
    with pytest.raises(ValueError, match="Agent 99999 not found"):
        await WorkflowBuilder(db_session).create_manual_workflow_async(
            job.id,
            [{"agent_id": 99999, "step_order": 1}],
        )


@pytest.mark.asyncio
async def test_create_manual_workflow_merges_invalid_json_string_as_custom_input(db_session):
    business, _dev, agents = _seed_business_dev_agents(db_session, 1)
    job = _job(db_session, business.id, conversation="not json", files="also bad")
    preview = MagicMock()
    with patch.object(PaymentProcessor, "calculate_job_cost", return_value=preview):
        await WorkflowBuilder(db_session).create_manual_workflow_async(
            job.id,
            [
                {
                    "agent_id": agents[0].id,
                    "step_order": 1,
                    "depends_on_previous": False,
                    "input_data": "not valid json {{{",
                }
            ],
        )
    step = db_session.query(WorkflowStep).filter(WorkflowStep.job_id == job.id).one()
    data = json.loads(step.input_data)
    assert data.get("custom_input") == "not valid json {{{"
    db_session.refresh(job)
    assert job.workflow_origin == "manual"


def test_create_manual_workflow_sync_wrapper_delegates_to_async(db_session):
    """Sync wrapper uses asyncio.run; patch run to avoid nested loop under pytest-asyncio."""
    business, _dev, agents = _seed_business_dev_agents(db_session, 1)
    job = _job(db_session, business.id)
    preview = MagicMock()

    def _fake_run(coro):
        if coro is not None and hasattr(coro, "close"):
            coro.close()
        return preview

    with patch("services.workflow_builder.asyncio.run", side_effect=_fake_run) as run_mock:
        out = WorkflowBuilder(db_session).create_manual_workflow(
            job.id,
            [{"agent_id": agents[0].id, "step_order": 1, "depends_on_previous": False}],
        )
    assert out is preview
    run_mock.assert_called_once()


def test_get_workflow_collaboration_hint_non_list_conversation(db_session):
    business, _dev, agents = _seed_business_dev_agents(db_session, 2)
    job = _job(db_session, business.id, conversation=json.dumps({"not": "list"}))
    builder = WorkflowBuilder(db_session)
    assert builder._get_workflow_collaboration_hint(job) is None


@pytest.mark.asyncio
async def test_auto_split_uses_conversation_hint_when_workflow_mode_none(db_session, monkeypatch):
    """When workflow_mode is None, BRD hint async_a2a => independent steps for step 2+."""
    business, _dev, agents = _seed_business_dev_agents(db_session, 2)
    job = _job(
        db_session,
        business.id,
        conversation=json.dumps([{"workflow_collaboration_hint": "async_a2a"}]),
        files=json.dumps([]),
    )
    monkeypatch.setattr(settings, "AGENT_PLANNER_ENABLED", False)
    builder = WorkflowBuilder(db_session)
    with patch("services.workflow_builder.split_job_for_agents", new_callable=AsyncMock) as split_m:
        split_m.return_value = [
            {"agent_index": 0, "task": "T1"},
            {"agent_index": 1, "task": "T2"},
        ]
        with patch.object(builder.payment_processor, "calculate_job_cost", return_value=MagicMock()):
            await builder.auto_split_workflow_async(job.id, [agents[0].id, agents[1].id], workflow_mode=None)
    steps = db_session.query(WorkflowStep).filter(WorkflowStep.job_id == job.id).order_by(WorkflowStep.step_order).all()
    assert len(steps) == 2
    assert steps[0].depends_on_previous is False
    assert steps[1].depends_on_previous is False
    db_session.refresh(job)
    assert job.workflow_origin == "auto_split"
