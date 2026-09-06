import sys
from datetime import datetime, timedelta, timezone

from src.agent import Agent
from src.automation.runner import format_due_report, run_due_jobs
from src.automation.schedule import ScheduleParseError, parse_schedule, strip_when_phrases
from src.automation.store import (
    AutomationStore,
    automation_status_line,
    default_automations_path,
)
from src.orchestrator import AgentOrchestrator
from src.tools.automation_tool import AutomationTool
import pytest


FIXED = datetime(2026, 9, 6, 15, 0, tzinfo=timezone.utc)


def test_parse_relative_and_recurring():
    rel = parse_schedule("remind me in 10 minutes to stretch", now=FIXED)
    assert rel.next_run_at == FIXED + timedelta(minutes=10)
    assert rel.every_seconds is None

    every = parse_schedule("every 30 minutes", now=FIXED)
    assert every.every_seconds == 1800
    assert every.next_run_at == FIXED + timedelta(minutes=30)

    daily = parse_schedule("daily at 8:00", now=FIXED, tz_name="UTC")
    assert daily.daily_at == "08:00"
    assert daily.next_run_at == datetime(2026, 9, 7, 8, 0, tzinfo=timezone.utc)

    morning = parse_schedule("every morning research the weather", now=FIXED, tz_name="UTC")
    assert morning.daily_at == "08:00"

    iso = parse_schedule("at 2026-09-06T18:00:00Z", now=FIXED)
    assert iso.next_run_at == datetime(2026, 9, 6, 18, 0, tzinfo=timezone.utc)


def test_parse_tomorrow_and_clock():
    afternoon = parse_schedule("at 3:30pm", now=FIXED, tz_name="UTC")
    assert afternoon.next_run_at == datetime(2026, 9, 6, 15, 30, tzinfo=timezone.utc)

    past = parse_schedule("at 9:00", now=FIXED, tz_name="UTC")
    assert past.next_run_at == datetime(2026, 9, 7, 9, 0, tzinfo=timezone.utc)

    tomorrow = parse_schedule("tomorrow at 9am", now=FIXED, tz_name="UTC")
    assert tomorrow.next_run_at == datetime(2026, 9, 7, 9, 0, tzinfo=timezone.utc)


def test_parse_rejects_empty():
    with pytest.raises(ScheduleParseError):
        parse_schedule("", now=FIXED)
    with pytest.raises(ScheduleParseError):
        parse_schedule("please do the thing", now=FIXED)


def test_strip_when_leaves_payload():
    assert "stretch" in strip_when_phrases("remind me in 10 minutes to stretch")
    leftover = strip_when_phrases("every morning research the weather in Austin")
    assert "research" in leftover
    assert "Austin" in leftover


def test_store_roundtrip_and_due(tmp_path):
    path = tmp_path / "automations.json"
    store = AutomationStore(path)
    job = store.add(
        kind="remind",
        title="stretch",
        schedule=parse_schedule("in 5 minutes", now=FIXED),
        message="stretch",
    )
    assert job.id
    later = AutomationStore(path)
    assert later.jobs[0].title == "stretch"
    assert later.due_jobs(FIXED) == []
    due = later.due_jobs(FIXED + timedelta(minutes=6))
    assert len(due) == 1
    later.mark_ran(due[0], "Reminder: stretch", FIXED + timedelta(minutes=6))
    assert later.jobs[0].enabled is False
    assert later.jobs[0].next_run_at is None


def test_recurring_job_advances(tmp_path):
    store = AutomationStore(tmp_path / "automations.json")
    job = store.add(
        kind="run",
        title="AI news",
        schedule=parse_schedule("every 1 hour", now=FIXED),
        prompt="Research the latest AI news",
    )
    store.mark_ran(job, "done", FIXED + timedelta(hours=1))
    assert job.enabled is True
    assert job.next_run_at == (FIXED + timedelta(hours=2)).isoformat()


def test_cancel_and_pause(tmp_path):
    store = AutomationStore(tmp_path / "automations.json")
    store.add(
        kind="remind",
        title="call mom",
        schedule=parse_schedule("in 2 hours", now=FIXED),
        message="call mom",
    )
    ident = store.jobs[0].id
    paused = store.set_enabled(ident, False)
    assert paused[0].enabled is False
    assert store.due_jobs(FIXED + timedelta(hours=3)) == []
    removed = store.cancel("mom")
    assert len(removed) == 1
    assert store.jobs == []


def test_corrupt_file_starts_empty(tmp_path):
    path = tmp_path / "automations.json"
    path.write_text("{not json", encoding="utf-8")
    store = AutomationStore(path)
    assert store.jobs == []
    store.add(
        kind="remind",
        title="ok",
        schedule=parse_schedule("in 1 minute", now=FIXED),
        message="ok",
    )
    reloaded = AutomationStore(path)
    assert reloaded.jobs[0].title == "ok"


def test_paths_honor_env(monkeypatch, tmp_path):
    home = tmp_path / "custom-home"
    monkeypatch.setenv("JARVIS_HOME", str(home))
    monkeypatch.delenv("JARVIS_AUTOMATIONS_PATH", raising=False)
    assert default_automations_path() == home / "automations.json"
    explicit = tmp_path / "elsewhere" / "jobs.json"
    monkeypatch.setenv("JARVIS_AUTOMATIONS_PATH", str(explicit))
    assert default_automations_path() == explicit


def test_tool_schedules_list_and_cancels(tmp_path, monkeypatch):
    monkeypatch.setattr("src.tools.automation_tool.parse_schedule", _fixed_parse)
    tool = AutomationTool(AutomationStore(tmp_path / "automations.json"))
    created = tool.use("remind me in 10 minutes to stretch")
    assert "Scheduled" in created
    assert "stretch" in created
    listed = tool.use("list")
    assert "stretch" in listed
    ident = tool.store.jobs[0].id
    assert "Cancelled" in tool.use({"action": "cancel", "id": ident})
    assert tool.store.jobs == []


def test_tool_infers_run_kind(tmp_path):
    tool = AutomationTool(AutomationStore(tmp_path / "automations.json"))
    text = tool.use("every morning research the weather in Austin")
    assert "Scheduled recurring run" in text
    assert tool.store.jobs[0].kind == "run"
    assert "weather" in tool.store.jobs[0].prompt.lower()


def test_tool_aliases_include_remind():
    assert "remind" in AutomationTool().aliases()


def test_runner_fires_reminder_without_llm(tmp_path):
    store = AutomationStore(tmp_path / "automations.json")
    store.add(
        kind="remind",
        title="stretch",
        schedule=parse_schedule("in 1 minute", now=FIXED),
        message="stretch",
    )
    assert run_due_jobs(store, now=FIXED) == []
    results = run_due_jobs(store, now=FIXED + timedelta(minutes=2))
    assert results == [f"Reminder ({store.jobs[0].id}): stretch"]
    assert store.jobs[0].enabled is False


def test_runner_leaves_run_job_if_no_callback(tmp_path):
    store = AutomationStore(tmp_path / "automations.json")
    store.add(
        kind="run",
        title="news",
        schedule=parse_schedule("in 1 minute", now=FIXED),
        prompt="Research AI news",
    )
    assert run_due_jobs(store, now=FIXED + timedelta(minutes=2)) == []
    assert store.jobs[0].enabled is True
    seen: list[str] = []

    def fake(prompt: str) -> str:
        seen.append(prompt)
        return "summary"

    results = run_due_jobs(store, run_prompt=fake, now=FIXED + timedelta(minutes=2))
    assert seen == ["Research AI news"]
    assert "summary" in results[0]
    assert "No automations were due" in format_due_report([])


def test_orchestrator_drains_due_reminders(tmp_path, monkeypatch):
    store = AutomationStore(tmp_path / "automations.json")
    store.add(
        kind="remind",
        title="water",
        schedule=parse_schedule("in 1 minute", now=FIXED),
        message="drink water",
    )
    orchestrator = AgentOrchestrator([], automation_store=store)
    lines = orchestrator.drain_due_automations(now=FIXED + timedelta(minutes=2))
    assert lines == [f"Reminder ({store.jobs[0].id}): drink water"]


def test_automation_agent_can_schedule(tmp_path, monkeypatch):
    store = AutomationStore(tmp_path / "automations.json")
    agent = Agent(
        Name="Automation Agent",
        Description="Schedules jobs",
        Tools=[AutomationTool(store)],
        Model="gemini-2.0-flash",
    )
    monkeypatch.setattr(
        "src.agent.query_llm",
        lambda prompt, model="gemini-2.0-flash": (
            '{"action": "remind", "args": "in 10 minutes to stretch"}'
        ),
    )
    result = agent.process_input("remind me in 10 minutes to stretch")
    assert "Scheduled" in result
    assert store.jobs[0].kind == "remind"


def test_main_lists_automations_without_api_key(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("JARVIS_AUTOMATIONS_PATH", str(tmp_path / "jobs.json"))
    monkeypatch.setattr(sys, "argv", ["main.py", "--automations"])
    from main import main

    main()
    assert "No automations yet" in capsys.readouterr().out


def test_status_line_empty_and_counts(tmp_path):
    path = tmp_path / "automations.json"
    empty = AutomationStore(path)
    assert "none scheduled" in empty.status_line()
    empty.add(
        kind="remind",
        title="stretch",
        schedule=parse_schedule("in 10 minutes", now=FIXED),
        message="stretch",
    )
    text = automation_status_line(empty)
    assert "1 scheduled" in text
    assert str(path) in text


def _fixed_parse(text, *, now=None, tz_name=None):
    return parse_schedule(text, now=FIXED, tz_name=tz_name)
