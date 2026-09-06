"""Fire due automations. Reminders print locally; run jobs reuse Jarvis."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from src.automation.schedule import utc_now
from src.automation.store import AutomationJob, AutomationStore


def run_due_jobs(
    store: AutomationStore,
    *,
    run_prompt: Callable[[str], str] | None = None,
    now: datetime | None = None,
) -> list[str]:
    """Execute every due job and persist the next run / last result.

    ``run`` jobs call ``run_prompt`` (usually the orchestrator). If that
    callback is missing they are left due so ``--run-due`` can pick them up
    with a live LLM. Reminders never need an API key beyond what you already use.
    """
    moment = now or utc_now()
    reports: list[str] = []
    for job in list(store.due_jobs(moment)):
        text, consumed = _execute(job, run_prompt)
        if not consumed:
            continue
        store.mark_ran(job, text, moment)
        reports.append(text)
    return reports


def format_due_report(results: list[str]) -> str:
    if not results:
        return "No automations were due."
    if len(results) == 1:
        return results[0]
    return "Due automations:\n" + "\n".join(f"- {item}" for item in results)


def _execute(
    job: AutomationJob,
    run_prompt: Callable[[str], str] | None,
) -> tuple[str, bool]:
    if job.kind == "remind":
        body = job.message or job.title
        return f"Reminder ({job.id}): {body}", True
    if job.kind == "run":
        prompt = job.prompt or job.message or job.title
        if run_prompt is None:
            return (
                f"Automation ({job.id}) '{job.title}' is due but needs Jarvis "
                "to run the prompt. Use `python main.py --run-due`.",
                False,
            )
        try:
            answer = run_prompt(prompt)
        except Exception as exc:
            answer = f"Automation failed: {exc}"
        return f"Automation ({job.id}) {job.title}: {answer}", True
    return f"Unknown automation kind '{job.kind}' ({job.id}).", True
