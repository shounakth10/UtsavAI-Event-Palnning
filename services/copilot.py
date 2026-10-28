"""Function-calling copilot with deterministic, auditable plan mutations."""

import json
import logging
import re
from datetime import date
from typing import Any

from google.genai import types
from pydantic import BaseModel

import config
from gemini_client import get_genai_client
from prompts import get_copilot_system_prompt
from schemas import (
    ChatMessage,
    ChatResponse,
    EventPlan,
    PlanChange,
    TimelineTask,
)
from services.budget import adjust_category_budget, normalize_budget
from services.critic import audit_plan
from services.gemini_retry import generate_content_with_retry
from services.timeline import process_timeline

logger = logging.getLogger("utsavai.copilot")


class CopilotReply(BaseModel):
    reply: str


class VendorScopeUpdate(BaseModel):
    category: str
    quantity_or_scope: str


class IdeaDescriptionUpdate(BaseModel):
    title: str
    description: str


class CopilotRefresh(BaseModel):
    reply: str
    vendor_updates: list[VendorScopeUpdate]
    idea_updates: list[IdeaDescriptionUpdate]


_FUNCTIONS = [
    types.FunctionDeclaration(
        name="update_guest_count",
        description="Set the event guest count to a positive integer.",
        parameters=types.Schema(
            type=types.Type.OBJECT,
            properties={"n": types.Schema(type=types.Type.INTEGER, description="New guest count")},
            required=["n"],
        ),
    ),
    types.FunctionDeclaration(
        name="update_event_date",
        description="Set the event date in YYYY-MM-DD format.",
        parameters=types.Schema(
            type=types.Type.OBJECT,
            properties={"iso_date": types.Schema(type=types.Type.STRING, description="YYYY-MM-DD")},
            required=["iso_date"],
        ),
    ),
    types.FunctionDeclaration(
        name="update_total_budget",
        description="Set the total budget in positive Indian rupees.",
        parameters=types.Schema(
            type=types.Type.OBJECT,
            properties={"amount_inr": types.Schema(type=types.Type.INTEGER, description="Total INR budget")},
            required=["amount_inr"],
        ),
    ),
    types.FunctionDeclaration(
        name="adjust_category_budget",
        description=(
            "Change a named category's current allocation by a relative percentage. "
            "For example, -10 reduces that category's current share by ten percent; "
            "the other category shares are proportionally rebalanced."
        ),
        parameters=types.Schema(
            type=types.Type.OBJECT,
            properties={
                "category": types.Schema(type=types.Type.STRING),
                "delta_percent": types.Schema(type=types.Type.NUMBER),
            },
            required=["category", "delta_percent"],
        ),
    ),
    types.FunctionDeclaration(
        name="add_task",
        description="Add an actionable task scheduled a number of days before the event.",
        parameters=types.Schema(
            type=types.Type.OBJECT,
            properties={
                "task": types.Schema(type=types.Type.STRING),
                "days_before_event": types.Schema(type=types.Type.INTEGER),
            },
            required=["task", "days_before_event"],
        ),
    ),
    types.FunctionDeclaration(
        name="update_preference",
        description=(
            "Update a preference such as theme, decor, cultural_style, tradition, "
            "city, menu, dietary requirement, or attire."
        ),
        parameters=types.Schema(
            type=types.Type.OBJECT,
            properties={
                "key": types.Schema(type=types.Type.STRING),
                "value": types.Schema(type=types.Type.STRING),
            },
            required=["key", "value"],
        ),
    ),
]


def _function_parameters(name: str, args: dict[str, Any], plan: EventPlan) -> str:
    if name == "update_guest_count":
        count = int(args["n"])
        if count <= 0:
            raise ValueError("Guest count must be greater than zero.")
        plan.event.guest_count = count
        return "event.guest_count"
    if name == "update_event_date":
        event_date = str(args["iso_date"])
        parsed_date = date.fromisoformat(event_date)
        if len(event_date) != 10:
            raise ValueError("Event date must use YYYY-MM-DD format.")
        plan.event.date = parsed_date.isoformat()
        return "event.date"
    if name == "update_total_budget":
        amount = int(args["amount_inr"])
        if amount <= 0:
            raise ValueError("Total budget must be greater than zero.")
        plan.event.total_budget_inr = amount
        plan.budget = normalize_budget(plan.budget, amount)
        return "event.total_budget_inr"
    if name == "adjust_category_budget":
        delta = float(args["delta_percent"])
        if not -100 <= delta <= 100:
            raise ValueError("Budget percentage changes must be between -100 and 100.")
        plan.budget = adjust_category_budget(
            plan.budget,
            str(args["category"]),
            delta,
            plan.event.total_budget_inr,
        )
        return f"budget.{args['category']}.percentage"
    if name == "add_task":
        task_text = str(args["task"]).strip()
        days = int(args["days_before_event"])
        if not task_text:
            raise ValueError("Task description cannot be empty.")
        if days < 0:
            raise ValueError("days_before_event cannot be negative.")
        existing_ids = {item.id for item in plan.timeline}
        next_number = 1
        while f"task-{next_number}" in existing_ids:
            next_number += 1
        plan.timeline.append(
            TimelineTask(
                id=f"task-{next_number}",
                task=task_text,
                category="Planning",
                due_date=plan.event.date,
                days_before_event=days,
                depends_on=[],
                status="todo",
                owner="Host",
            )
        )
        return f"timeline.{plan.timeline[-1].id}"
    if name == "update_preference":
        key = str(args["key"]).strip()
        value = str(args["value"]).strip()
        if not key or not value:
            raise ValueError("Preference key and value cannot be empty.")
        normalized_key = re.sub(r"[^a-z]", "", key.casefold())
        if normalized_key in {"theme", "decor", "decoration"}:
            plan.event.theme = value
            return "event.theme"
        if normalized_key in {"culturalstyle", "tradition", "culture"}:
            plan.event.cultural_style = value
            return "event.cultural_style"
        if normalized_key in {"city", "location"}:
            plan.event.city = value
            return "event.city"
        assumption = f"Preference — {key}: {value}"
        if assumption not in plan.assumptions:
            plan.assumptions.append(assumption)
        return "assumptions"
    raise ValueError(f"Unknown copilot tool: {name}")


def _tool_name(call: object) -> str:
    return str(getattr(call, "name", ""))


def _tool_args(call: object) -> dict[str, Any]:
    args = getattr(call, "args", {}) or {}
    return dict(args)


def _contents_from_history(history: list[ChatMessage], message: str) -> list[types.Content]:
    contents = [
        types.Content(
            role="user" if item.role == "user" else "model",
            parts=[types.Part.from_text(text=item.content)],
        )
        for item in history
    ]
    contents.append(types.Content(role="user", parts=[types.Part.from_text(text=message)]))
    return contents


def _parse_reply(response: object) -> CopilotReply:
    text = getattr(response, "text", None)
    if text:
        try:
            return CopilotReply.model_validate_json(text)
        except ValueError:
            return CopilotReply(reply=text)
    parsed = getattr(response, "parsed", None)
    if isinstance(parsed, CopilotReply):
        return parsed
    raise ValueError("Copilot returned an empty response.")


def _snapshot(plan: EventPlan, path: str) -> Any:
    value: Any = plan.model_dump(mode="json")
    for part in path.split("."):
        if isinstance(value, dict):
            value = value.get(part)
        elif part.isdigit() and isinstance(value, list):
            value = value[int(part)]
        else:
            return value
    return value


def _tool_value(plan: EventPlan, field: str) -> Any:
    if field.startswith("event."):
        return _snapshot(plan, field)
    if field == "assumptions":
        return plan.assumptions[-1] if plan.assumptions else None
    if field.startswith("budget."):
        requested = field.removeprefix("budget.").removesuffix(".percentage")
        item = next(
            (entry for entry in plan.budget if requested.casefold() in entry.category.casefold()),
            None,
        )
        return (
            {"percentage": item.percentage, "amount_inr": item.amount_inr}
            if item else None
        )
    if field.startswith("timeline."):
        task_id = field.removeprefix("timeline.")
        task = next((entry for entry in plan.timeline if entry.id == task_id), None)
        return task.model_dump(mode="json") if task else None
    return None


def _refresh_text(
    client: object,
    plan: EventPlan,
    language: str,
    conversation: list[types.Content],
    model_content: types.Content,
    function_responses: list[types.Part],
) -> CopilotRefresh:
    refresh_contents = [
        *conversation,
        model_content,
        types.Content(role="tool", parts=function_responses),
        types.Content(
            role="user",
            parts=[
                types.Part.from_text(
                    text=(
                        "The deterministic tool edits have been applied. Refresh only vendor "
                        "quantity_or_scope text and idea descriptions that the changes affect. "
                        "Do not change prices, plan facts, dates, or budget data. Return a short "
                        "reply, and omit unchanged text. Current plan:\n"
                        + plan.model_dump_json(indent=2)
                    )
                )
            ],
        ),
    ]
    response = generate_content_with_retry(
        client.models.generate_content,
        operation="copilot_refresh",
        model=config.GEMINI_MODEL,
        contents=refresh_contents,
        config=types.GenerateContentConfig(
            system_instruction=(
                get_copilot_system_prompt(plan.model_dump_json(indent=2), language)
                + "\nReturn JSON matching CopilotRefresh. Update only affected descriptions."
            ),
            response_mime_type="application/json",
            response_schema=CopilotRefresh,
            temperature=0.2,
        ),
    )
    metadata = getattr(response, "usage_metadata", None)
    logger.info(
        "copilot_refresh_complete",
        extra={
            "prompt_tokens": getattr(metadata, "prompt_token_count", None),
            "candidate_tokens": getattr(metadata, "candidates_token_count", None),
            "total_tokens": getattr(metadata, "total_token_count", None),
        },
    )
    if response.text:
        refreshed = CopilotRefresh.model_validate_json(response.text)
    elif isinstance(getattr(response, "parsed", None), CopilotRefresh):
        refreshed = response.parsed
    else:
        raise ValueError("Copilot could not refresh affected plan text.")

    for update in refreshed.vendor_updates:
        vendor = next(
            (item for item in plan.vendors if item.category == update.category),
            None,
        )
        if vendor:
            vendor.quantity_or_scope = update.quantity_or_scope
    for update in refreshed.idea_updates:
        idea = next((item for item in plan.ideas if item.title == update.title), None)
        if idea:
            idea.description = update.description
    return refreshed


def ask_copilot(
    message: str,
    plan: EventPlan,
    language: str = "en",
    history: list[ChatMessage] | None = None,
) -> tuple[bool, ChatResponse | None, str | None]:
    """Answer a question or apply deterministic edits requested through Gemini tools."""
    try:
        client = get_genai_client()
        updated_plan = plan.model_copy(deep=True)
        conversation = _contents_from_history(history or [], message)
        first_response = generate_content_with_retry(
            client.models.generate_content,
            operation="copilot_tool_call",
            model=config.GEMINI_MODEL,
            contents=conversation,
            config=types.GenerateContentConfig(
                system_instruction=get_copilot_system_prompt(
                    updated_plan.model_dump_json(indent=2), language
                ),
                tools=[types.Tool(function_declarations=_FUNCTIONS)],
                temperature=0.2,
            ),
        )
        metadata = getattr(first_response, "usage_metadata", None)
        logger.info(
            "copilot_tool_call_complete",
            extra={
                "prompt_tokens": getattr(metadata, "prompt_token_count", None),
                "candidate_tokens": getattr(metadata, "candidates_token_count", None),
                "total_tokens": getattr(metadata, "total_token_count", None),
            },
        )
        calls = list(getattr(first_response, "function_calls", None) or [])
        if not calls:
            reply = _parse_reply(first_response)
            return True, ChatResponse(reply=reply.reply), None

        model_content = first_response.candidates[0].content
        function_responses: list[types.Part] = []
        for call in calls:
            name = _tool_name(call)
            args = _tool_args(call)
            field = _function_parameters(name, args, updated_plan)
            function_responses.append(
                types.Part.from_function_response(
                    name=name,
                    response={
                        "ok": True,
                        "field": field,
                        "value": _tool_value(updated_plan, field),
                    },
                )
            )

        updated_plan.timeline, updated_plan.risks = process_timeline(
            updated_plan.timeline, updated_plan.event.date, updated_plan.risks
        )
        refreshed = _refresh_text(
            client,
            updated_plan,
            language,
            conversation,
            model_content,
            function_responses,
        )
        updated_plan, report, fixed_count = audit_plan(
            updated_plan, language=language, client=client
        )
        changes = _describe_changes(plan, updated_plan)
        return True, ChatResponse(
            reply=refreshed.reply,
            updated_plan=updated_plan,
            changes=changes,
            critic_report=report,
            fixed_issues=fixed_count,
        ), None
    except Exception as exc:
        return False, None, f"Copilot could not process the request: {exc}"


def _describe_changes(before: EventPlan, after: EventPlan) -> list[PlanChange]:
    changes: list[PlanChange] = []
    for field in (
        "guest_count",
        "date",
        "total_budget_inr",
        "theme",
        "cultural_style",
        "city",
    ):
        old_value = getattr(before.event, field)
        new_value = getattr(after.event, field)
        if old_value != new_value:
            changes.append(
                PlanChange(
                    field=f"event.{field}",
                    before=old_value,
                    after=new_value,
                )
            )

    before_budget = {item.category: item for item in before.budget}
    after_budget = {item.category: item for item in after.budget}
    for category in sorted(after_budget.keys() | before_budget.keys()):
        old_item = before_budget.get(category)
        new_item = after_budget.get(category)
        old_value = (
            {"percentage": old_item.percentage, "amount_inr": old_item.amount_inr}
            if old_item else None
        )
        new_value = (
            {"percentage": new_item.percentage, "amount_inr": new_item.amount_inr}
            if new_item else None
        )
        if old_value != new_value:
            changes.append(PlanChange(
                field=f"budget.{category}",
                before=old_value,
                after=new_value,
            ))

    before_tasks = {task.id: task for task in before.timeline}
    after_tasks = {task.id: task for task in after.timeline}
    for task_id in sorted(after_tasks.keys() | before_tasks.keys()):
        old_task = before_tasks.get(task_id)
        new_task = after_tasks.get(task_id)
        old_value = (
            {
                "task": old_task.task,
                "due_date": old_task.due_date,
                "days_before_event": old_task.days_before_event,
            }
            if old_task else None
        )
        new_value = (
            {
                "task": new_task.task,
                "due_date": new_task.due_date,
                "days_before_event": new_task.days_before_event,
            }
            if new_task else None
        )
        if old_value != new_value:
            changes.append(PlanChange(
                field=f"timeline.{task_id}",
                before=old_value,
                after=new_value,
            ))

    if before.assumptions != after.assumptions:
        changes.append(PlanChange(
            field="assumptions",
            before=before.assumptions,
            after=after.assumptions,
        ))
    return changes
