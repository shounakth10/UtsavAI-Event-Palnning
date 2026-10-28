from datetime import date, timedelta
from types import SimpleNamespace

import pytest

from schemas import BudgetItem, CriticIssue, CriticReport, TimelineTask
from services.budget import adjust_category_budget, normalize_budget
from services.critic import audit_plan, check_plan_programmatically, repair_plan_programmatically
from services.timeline import process_timeline


def budget_item(category: str, percentage: float, amount: int = 0) -> BudgetItem:
    return BudgetItem(
        category=category,
        percentage=percentage,
        amount_inr=amount,
        rationale="Test allocation",
        priority="must",
    )


def timeline_task(task_id: str, days_before: int) -> TimelineTask:
    return TimelineTask(
        id=task_id,
        task=f"Complete {task_id}",
        category="Planning",
        due_date="2026-01-01",
        days_before_event=days_before,
        depends_on=[],
        status="todo",
        owner="Host",
    )


def test_budget_normalization_balances_exact_rupee_total():
    items = [budget_item("Venue", 32.2), budget_item("Food", 41.3), budget_item("Decor", 19.8)]

    normalized = normalize_budget(items, 1_234_567)

    assert sum(item.amount_inr for item in normalized) == 1_234_567
    assert sum(item.percentage for item in normalized) == 100.0


def test_budget_normalization_handles_zero_percentages():
    normalized = normalize_budget(
        [budget_item("Venue", 0), budget_item("Food", 0), budget_item("Decor", 0)],
        101,
    )

    assert sum(item.amount_inr for item in normalized) == 101
    assert sum(item.percentage for item in normalized) == 100.0


def test_category_budget_change_rebalances_other_categories():
    items = [budget_item("Decor", 20), budget_item("Food", 50), budget_item("Venue", 30)]

    changed = adjust_category_budget(items, "decor", -10, 100_000)

    assert changed[0].percentage == pytest.approx(18)
    assert sum(item.percentage for item in changed) == pytest.approx(100)
    assert sum(item.amount_inr for item in changed) == 100_000


def test_timeline_dates_never_fall_before_today():
    today = date(2026, 10, 27)
    event_date = today + timedelta(days=40)
    tasks = [timeline_task("venue", 90), timeline_task("decor", 2)]

    result, _ = process_timeline(tasks, event_date.isoformat(), [], today=today)

    assert all(date.fromisoformat(task.due_date) >= today for task in result)
    assert all(date.fromisoformat(task.due_date) <= event_date for task in result)


def test_timeline_compresses_events_less_than_two_weeks_away():
    today = date(2026, 10, 27)
    event_date = today + timedelta(days=10)
    tasks = [timeline_task("venue", 90), timeline_task("invites", 30), timeline_task("event", 0)]

    result, risks = process_timeline(tasks, event_date.isoformat(), [], today=today)

    assert any("Compressed Timeline" in risk for risk in risks)
    assert all(today <= date.fromisoformat(task.due_date) <= event_date for task in result)


def test_past_event_moves_remaining_tasks_to_today():
    today = date(2026, 10, 27)
    tasks = [timeline_task("venue", 30), timeline_task("event", 0)]

    result, risks = process_timeline(tasks, (today - timedelta(days=1)).isoformat(), [], today=today)

    assert all(task.due_date == today.isoformat() for task in result)
    assert risks and "Urgent Timeline Alert" in risks[0]


def test_invalid_event_date_is_an_explicit_error():
    with pytest.raises(ValueError, match="Invalid event date"):
        process_timeline([timeline_task("venue", 1)], "not-a-date", [])


def test_critic_flags_invalid_dependencies_and_budget(make_plan):
    plan = make_plan()
    plan.budget[0].percentage = 50
    plan.timeline[0].depends_on = ["missing-task"]

    report = check_plan_programmatically(plan)

    assert not report.passed
    assert any("percentages" in issue.message for issue in report.issues)
    assert any("invalid dependency" in issue.message for issue in report.issues)


def test_critic_repairs_budget_and_compressed_timeline(make_plan):
    today = date(2026, 10, 27)
    plan = make_plan(event_date=(today + timedelta(days=8)).isoformat())
    plan.budget[0].percentage = 70
    plan.timeline[0].days_before_event = 90

    repaired = repair_plan_programmatically(plan, today=today)
    report = check_plan_programmatically(repaired, today=today)

    assert sum(item.percentage for item in repaired.budget) == pytest.approx(100)
    assert sum(item.amount_inr for item in repaired.budget) == repaired.event.total_budget_inr
    assert all(
        today <= date.fromisoformat(task.due_date) <= date.fromisoformat(repaired.event.date)
        for task in repaired.timeline
    )
    assert report.passed


def test_critic_qualitative_issue_is_repaired_once(make_plan):
    plan = make_plan()
    issue = CriticIssue(
        severity="medium",
        message="The plan needs a clear guest welcome detail.",
        fix_suggestion="Add one culturally appropriate welcome detail.",
    )

    class FakeModels:
        def __init__(self):
            self.responses = [
                CriticReport(passed=False, issues=[issue]).model_dump_json(),
                plan.model_dump_json(),
                CriticReport(passed=True, issues=[]).model_dump_json(),
            ]

        def generate_content(self, **kwargs):
            return SimpleNamespace(text=self.responses.pop(0), usage_metadata=None)

    fake_client = SimpleNamespace(models=FakeModels())

    _, report, fixed_count = audit_plan(plan, client=fake_client)

    assert report.passed
    assert report.issues == []
    assert fixed_count == 1
    assert fake_client.models.responses == []
