from datetime import date, timedelta
from types import SimpleNamespace

import pytest
from google.genai import types

from schemas import CriticReport
from services import copilot
from services.copilot import CopilotRefresh, _function_parameters


def test_guest_count_tool_updates_plan_without_llm_math(make_plan):
    plan = make_plan()

    field = _function_parameters("update_guest_count", {"n": 450}, plan)

    assert field == "event.guest_count"
    assert plan.event.guest_count == 450


def test_event_date_tool_recalculates_timeline(make_plan):
    plan = make_plan()
    new_date = (date.today() + timedelta(days=80)).isoformat()

    field = _function_parameters("update_event_date", {"iso_date": new_date}, plan)

    assert field == "event.date"
    assert plan.event.date == new_date


def test_total_budget_tool_rebalances_all_amounts(make_plan):
    plan = make_plan()

    _function_parameters("update_total_budget", {"amount_inr": 400_000}, plan)

    assert plan.event.total_budget_inr == 400_000
    assert sum(item.amount_inr for item in plan.budget) == 400_000


def test_decor_cut_tool_changes_share_deterministically(make_plan):
    plan = make_plan()
    decor = next(item for item in plan.budget if "decor" in item.category.casefold())
    old_share = decor.percentage

    field = _function_parameters(
        "adjust_category_budget",
        {"category": decor.category, "delta_percent": -10},
        plan,
    )

    assert field.endswith(".percentage")
    assert decor.percentage == pytest.approx(old_share * 0.9)
    assert sum(item.amount_inr for item in plan.budget) == plan.event.total_budget_inr


def test_add_task_tool_creates_unique_actionable_task(make_plan):
    plan = make_plan()

    field = _function_parameters(
        "add_task",
        {"task": "Confirm the mandap schedule", "days_before_event": 5},
        plan,
    )

    assert field.startswith("timeline.task-")
    assert plan.timeline[-1].task == "Confirm the mandap schedule"


def test_preference_tool_updates_event_theme(make_plan):
    plan = make_plan()

    _function_parameters("update_preference", {"key": "decor", "value": "marigold and ivory"}, plan)

    assert plan.event.theme == "marigold and ivory"


def test_tool_rejects_non_positive_guests(make_plan):
    with pytest.raises(ValueError, match="Guest count"):
        _function_parameters("update_guest_count", {"n": 0}, make_plan())


@pytest.mark.parametrize(
    "message,function_name,args,expected_field",
    [
        ("Increase guests to 450", "update_guest_count", {"n": 450}, "event.guest_count"),
        (
            "Move the event to January 20",
            "update_event_date",
            {"iso_date": (date(date.today().year + 1, 1, 20)).isoformat()},
            "event.date",
        ),
        (
            "Cut decor budget by 10%",
            "adjust_category_budget",
            {"category": "decor", "delta_percent": -10},
            "budget.Decor & Lighting",
        ),
    ],
)
def test_chat_function_call_applies_requested_edits(
    message,
    function_name,
    args,
    expected_field,
    make_plan,
    monkeypatch,
):
    class FakeModels:
        def __init__(self):
            self.calls = 0

        def generate_content(self, **kwargs):
            self.calls += 1
            if self.calls == 1:
                function_part = types.Part.from_function_call(name=function_name, args=args)
                return SimpleNamespace(
                    function_calls=[SimpleNamespace(name=function_name, args=args)],
                    candidates=[
                        SimpleNamespace(
                            content=types.Content(role="model", parts=[function_part])
                        )
                    ],
                    usage_metadata=None,
                )
            return SimpleNamespace(
                    text=CopilotRefresh(
                        reply="Done.",
                        vendor_updates=[],
                        idea_updates=[],
                    ).model_dump_json(),
                    usage_metadata=None,
                )

    client = SimpleNamespace(models=FakeModels())
    monkeypatch.setattr(copilot, "get_genai_client", lambda: client)
    monkeypatch.setattr(
        copilot,
        "audit_plan",
        lambda value, language="en", client=None: (
            value,
            CriticReport(passed=True, issues=[]),
            0,
        ),
    )
    plan = make_plan()

    success, result, error = copilot.ask_copilot(message, plan)

    assert success, error
    assert result is not None
    assert result.updated_plan is not None
    assert any(change.field.startswith(expected_field) for change in result.changes)
    assert client.models.calls == 2
    if function_name == "update_guest_count":
        assert result.updated_plan.event.guest_count == 450
    elif function_name == "update_event_date":
        assert result.updated_plan.event.date == args["iso_date"]
    else:
        decor = next(item for item in result.updated_plan.budget if "decor" in item.category.casefold())
        assert decor.percentage < next(item for item in plan.budget if item.category == decor.category).percentage
