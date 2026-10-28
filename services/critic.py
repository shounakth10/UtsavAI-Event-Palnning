"""Deterministic plan checks followed by focused qualitative Gemini review."""

import json
import logging
import re
from datetime import date
from typing import Iterable

from google.genai import types

import config
from prompts import CRITIC_SYSTEM_PROMPT, LANGUAGE_INSTRUCTIONS
from schemas import CriticIssue, CriticReport, EventPlan
from services.budget import normalize_budget
from services.gemini_retry import generate_content_with_retry
from services.timeline import process_timeline

logger = logging.getLogger("utsavai.critic")


def _issue(severity: str, message: str, fix_suggestion: str) -> CriticIssue:
    return CriticIssue(severity=severity, message=message, fix_suggestion=fix_suggestion)


def _parse_iso(value: str, field: str, issues: list[CriticIssue]) -> date | None:
    try:
        parsed = date.fromisoformat(value)
        if len(value) != 10:
            raise ValueError
        return parsed
    except ValueError:
        issues.append(_issue("high", f"{field} is not a valid ISO date.", "Use YYYY-MM-DD."))
        return None


def _has_dependency_cycle(dependencies: dict[str, list[str]]) -> bool:
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(task_id: str) -> bool:
        if task_id in visiting:
            return True
        if task_id in visited:
            return False
        visiting.add(task_id)
        if any(visit(parent_id) for parent_id in dependencies.get(task_id, [])):
            return True
        visiting.remove(task_id)
        visited.add(task_id)
        return False

    return any(visit(task_id) for task_id in dependencies)


def _meaningful_words(value: str) -> set[str]:
    stop_words = {"and", "the", "for", "of", "a", "an", "&"}
    return {
        word for word in re.findall(r"[a-z0-9]+", value.casefold())
        if len(word) > 2 and word not in stop_words
    }


def _vendor_matches(category: str, vendors: Iterable[str]) -> bool:
    category_words = _meaningful_words(category)
    return any(category_words & _meaningful_words(vendor) for vendor in vendors)


def check_plan_programmatically(plan: EventPlan, today: date | None = None) -> CriticReport:
    """Validate arithmetic, timeline bounds, dependencies, and must-have vendors."""
    today = today or date.today()
    issues: list[CriticIssue] = []
    event_date = _parse_iso(plan.event.date, "Event date", issues)

    percentage_total = sum(item.percentage for item in plan.budget)
    if plan.budget and abs(percentage_total - 100.0) > 0.01:
        issues.append(_issue(
            "high",
            f"Budget percentages total {percentage_total:.2f}%, not 100%.",
            "Normalize all category percentages to 100%.",
        ))

    amount_total = sum(item.amount_inr for item in plan.budget)
    if plan.budget and amount_total != plan.event.total_budget_inr:
        issues.append(_issue(
            "high",
            f"Budget amounts total ₹{amount_total}, not the ₹{plan.event.total_budget_inr} event budget.",
            "Recalculate category amounts and balance rounding exactly.",
        ))
    if not plan.budget:
        issues.append(_issue("high", "The plan has no budget categories.", "Add budget categories that total 100%."))

    task_ids = {task.id for task in plan.timeline}
    dependencies: dict[str, list[str]] = {}
    task_dates: dict[str, date] = {}
    for task in plan.timeline:
        task_date = _parse_iso(task.due_date, f"Task '{task.id}' due date", issues)
        if task_date is not None:
            task_dates[task.id] = task_date
            if task_date < today:
                issues.append(_issue(
                    "high",
                    f"Task '{task.task}' is scheduled before today.",
                    "Move the deadline to today or a later date.",
                ))
            if event_date and event_date >= today and task_date > event_date:
                issues.append(_issue(
                    "high",
                    f"Task '{task.task}' is scheduled after the event.",
                    "Schedule the task on or before the event date.",
                ))
        dependencies[task.id] = task.depends_on
        for parent_id in task.depends_on:
            if parent_id not in task_ids or parent_id == task.id:
                issues.append(_issue(
                    "high",
                    f"Task '{task.id}' has an invalid dependency '{parent_id}'.",
                    "Remove missing or self-referential dependencies.",
                ))

    if _has_dependency_cycle({
        task_id: [parent for parent in parent_ids if parent in task_ids]
        for task_id, parent_ids in dependencies.items()
    }):
        issues.append(_issue(
            "high",
            "Timeline dependencies contain a cycle.",
            "Reorder the tasks so dependencies form an acyclic sequence.",
        ))

    for task in plan.timeline:
        child_date = task_dates.get(task.id)
        for parent_id in task.depends_on:
            parent_date = task_dates.get(parent_id)
            if child_date and parent_date and parent_date > child_date:
                issues.append(_issue(
                    "medium",
                    f"Dependency '{parent_id}' is scheduled after '{task.id}'.",
                    "Schedule prerequisites on or before the dependent task.",
                ))

    must_categories = [item.category for item in plan.budget if item.priority == "must"]
    vendor_categories = [vendor.category for vendor in plan.vendors]
    for category in must_categories:
        if not _vendor_matches(category, vendor_categories):
            issues.append(_issue(
                "medium",
                f"Must-have budget category '{category}' has no matching vendor category.",
                "Add a vendor checklist entry for this must-have category.",
            ))

    return CriticReport(passed=not issues, issues=issues)


def repair_plan_programmatically(plan: EventPlan, today: date | None = None) -> EventPlan:
    """Repair arithmetic, dates, and simple dependency-order errors once."""
    today = today or date.today()
    plan.budget = normalize_budget(plan.budget, plan.event.total_budget_inr)
    plan.timeline, plan.risks = process_timeline(
        plan.timeline, plan.event.date, plan.risks, today=today
    )

    known_ids = {task.id for task in plan.timeline}
    for task in plan.timeline:
        task.depends_on = [
            parent_id for parent_id in task.depends_on
            if parent_id in known_ids and parent_id != task.id
        ]

    by_id = {task.id: task for task in plan.timeline}
    for _ in range(len(plan.timeline)):
        changed = False
        for task in plan.timeline:
            for parent_id in task.depends_on:
                parent = by_id[parent_id]
                if task.days_before_event > parent.days_before_event:
                    task.days_before_event = parent.days_before_event
                    changed = True
        if not changed:
            break

    plan.timeline, plan.risks = process_timeline(
        plan.timeline, plan.event.date, plan.risks, today=today
    )
    return plan


def _parse_critic_response(response: object) -> CriticReport:
    text = getattr(response, "text", None)
    if not text:
        parsed = getattr(response, "parsed", None)
        if isinstance(parsed, CriticReport):
            return parsed
        raise ValueError("The plan critic returned an empty response.")
    return CriticReport.model_validate(json.loads(text))


def _qualitative_review(plan: EventPlan, language: str, client: object) -> CriticReport:
    response = generate_content_with_retry(
        client.models.generate_content,
        operation="critic_review",
        model=config.GEMINI_MODEL,
        contents=plan.model_dump_json(indent=2),
        config=types.GenerateContentConfig(
            system_instruction=(
                CRITIC_SYSTEM_PROMPT
                + "\n"
                + LANGUAGE_INSTRUCTIONS.get(language, LANGUAGE_INSTRUCTIONS["en"])
            ),
            response_mime_type="application/json",
            response_schema=CriticReport,
            temperature=0.1,
        ),
    )
    metadata = getattr(response, "usage_metadata", None)
    logger.info(
        "critic_review_complete",
        extra={
            "prompt_tokens": getattr(metadata, "prompt_token_count", None),
            "candidate_tokens": getattr(metadata, "candidates_token_count", None),
            "total_tokens": getattr(metadata, "total_token_count", None),
        },
    )
    return _parse_critic_response(response)


def _repair_qualitative_issues(
    plan: EventPlan,
    issues: list[CriticIssue],
    language: str,
    client: object,
) -> EventPlan:
    response = generate_content_with_retry(
        client.models.generate_content,
        operation="critic_repair",
        model=config.GEMINI_MODEL,
        contents=(
            "Repair this event plan to address each review issue. Preserve all user-provided "
            "event facts, budget totals, and enum values. Do not invent vendor identities.\n"
            f"Issues: {json.dumps([issue.model_dump() for issue in issues], ensure_ascii=False)}\n"
            f"Plan: {plan.model_dump_json(indent=2)}"
        ),
        config=types.GenerateContentConfig(
            system_instruction=(
                "Return a complete corrected EventPlan as JSON. "
                + LANGUAGE_INSTRUCTIONS.get(language, LANGUAGE_INSTRUCTIONS["en"])
            ),
            response_mime_type="application/json",
            response_schema=EventPlan,
            temperature=0.1,
        ),
    )
    text = getattr(response, "text", None)
    if text:
        return EventPlan.model_validate(json.loads(text))
    parsed = getattr(response, "parsed", None)
    if isinstance(parsed, EventPlan):
        return parsed
    raise ValueError("The plan repair call returned an empty response.")


def audit_plan(
    plan: EventPlan,
    language: str = "en",
    client: object | None = None,
    today: date | None = None,
) -> tuple[EventPlan, CriticReport, int]:
    """Run deterministic checks, repair once, then critique and optionally repair once."""
    initial_programmatic_report = check_plan_programmatically(plan, today=today)
    repaired = repair_plan_programmatically(plan, today=today)
    programmatic_report = check_plan_programmatically(repaired, today=today)

    if client is None:
        from gemini_client import get_genai_client

        client = get_genai_client()

    qualitative_report = _qualitative_review(repaired, language, client)
    initially_reported_issue_count = (
        len(initial_programmatic_report.issues) + len(qualitative_report.issues)
    )
    if qualitative_report.issues:
        repaired = _repair_qualitative_issues(
            repaired, qualitative_report.issues, language, client
        )
        repaired = repair_plan_programmatically(repaired, today=today)
        programmatic_report = check_plan_programmatically(repaired, today=today)
        qualitative_report = _qualitative_review(repaired, language, client)

    final_issues = programmatic_report.issues + qualitative_report.issues
    fixed_count = max(0, initially_reported_issue_count - len(final_issues))
    return repaired, CriticReport(passed=not final_issues, issues=final_issues), fixed_count
