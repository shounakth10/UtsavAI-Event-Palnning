"""
Timeline Service for UtsavAI.
Calculates strictly verified calendar dates from event date and days_before_event.
Guarantees:
1. No task due date falls in the past (before today).
2. If event date is imminent, proportionally compresses milestones.
3. Automatically appends a prominent risk note when timeline compression occurs.
4. Categorizes tasks into intuitive Indian event planning phases.
"""

from datetime import date, timedelta
from typing import List, Tuple
from schemas import TimelineTask


def categorize_phase(days_before_event: int) -> str:
    """Assigns task to planning phase based on days remaining."""
    if days_before_event >= 90:
        return "3+ Months"
    elif days_before_event >= 30:
        return "1-3 Months"
    elif days_before_event >= 8:
        return "Last Month (2-4 Weeks)"
    elif days_before_event >= 1:
        return "Last Week"
    else:
        return "Event Day & Wrap-up"


def process_timeline(
    tasks: List[TimelineTask],
    event_date_str: str,
    existing_risks: List[str],
    today: date | None = None,
) -> Tuple[List[TimelineTask], List[str]]:
    """
    Computes accurate due dates for all tasks.
    If the event is too close, compresses the timeline and appends a risk note.
    """
    today = today or date.today()
    risks = list(existing_risks)

    try:
        event_date = date.fromisoformat(event_date_str)
    except ValueError as exc:
        raise ValueError(f"Invalid event date: {event_date_str}. Expected YYYY-MM-DD.") from exc

    days_until_event = (event_date - today).days

    if not tasks:
        return tasks, risks

    max_original_days = max(t.days_before_event for t in tasks) if tasks else 0

    # Case A: Event date is today or already passed
    if days_until_event <= 0:
        for t in tasks:
            t.days_before_event = 0
            t.due_date = today.strftime("%Y-%m-%d")
        warning = (
            "Urgent Timeline Alert: The event date is today or has passed. "
            "All remaining tasks are marked for immediate execution."
        )
        if warning not in risks:
            risks.insert(0, warning)
        tasks.sort(key=lambda x: (x.due_date, -x.days_before_event))
        return tasks, risks

    # Case B: Event is close and tasks fall before today
    if max_original_days > days_until_event:
        # Scale proportionally into the available window [today, event_date]
        available_window = max(0, days_until_event - 1)
        for t in tasks:
            ratio = t.days_before_event / max(1, max_original_days)
            scaled_days = int(round(ratio * available_window))
            t.days_before_event = max(0, min(scaled_days, days_until_event))
            computed_date = event_date - timedelta(days=t.days_before_event)
            if computed_date < today:
                computed_date = today
                t.days_before_event = (event_date - today).days
            t.due_date = computed_date.strftime("%Y-%m-%d")

        compression_note = (
            f"Compressed Timeline: Event is in {days_until_event} days. "
            f"Milestones normally scheduled across {max_original_days} days have been accelerated. "
            f"Lock in venue, caterer, and key vendors immediately."
        )
        if compression_note not in risks:
            risks.insert(0, compression_note)

    # Case C: Event is sufficiently far in the future
    else:
        for t in tasks:
            computed_date = event_date - timedelta(days=t.days_before_event)
            if computed_date < today:
                computed_date = today
                t.days_before_event = (event_date - today).days
            t.due_date = computed_date.strftime("%Y-%m-%d")

    # Sort tasks so nearest upcoming tasks appear in logical sequence
    tasks.sort(key=lambda x: (x.due_date, -x.days_before_event))
    return tasks, risks
