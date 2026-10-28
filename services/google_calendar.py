"""Google Calendar REST integration using a user-authorized OAuth access token."""

import hashlib
import logging
from datetime import date, timedelta
from urllib.parse import quote

import httpx

from schemas import EventPlan

logger = logging.getLogger("utsavai.google_calendar")
_GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v3/userinfo"
_GOOGLE_EVENTS_URL = "https://www.googleapis.com/calendar/v3/calendars/primary/events"


def _google_identity(access_token: str, expected_email: str) -> dict[str, str]:
    try:
        response = httpx.get(
            _GOOGLE_USERINFO_URL,
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=10,
        )
    except httpx.RequestError as exc:
        raise RuntimeError("Could not verify the Google Calendar account. Check network access.") from exc
    if response.status_code != 200:
        raise PermissionError("Google Calendar permission expired. Reconnect your Google account.")
    profile = response.json()
    actual_email = profile.get("email", "")
    if (
        not profile.get("sub")
        or not profile.get("email_verified")
        or actual_email.casefold() != expected_email.casefold()
    ):
        raise PermissionError(
            "The Google Calendar account must match the verified email you signed in with."
        )
    return profile


def _stable_event_id(uid: str, event_key: str) -> str:
    return hashlib.sha256(f"{uid}:{event_key}".encode("utf-8")).hexdigest()[:48]


def _calendar_events(plan: EventPlan, uid: str) -> list[dict[str, object]]:
    items: list[tuple[str, str, str, str]] = [
        (
            "event",
            f"UtsavAI: {plan.event.title}",
            plan.event.summary,
            plan.event.date,
        )
    ]
    items.extend(
        (
            task.id,
            f"[{task.category}] {task.task}",
            (
                f"UtsavAI milestone for {plan.event.title}\n"
                f"Category: {task.category}\nOwner: {task.owner}\n"
                f"Depends on: {', '.join(task.depends_on) or 'None'}"
            ),
            task.due_date,
        )
        for task in plan.timeline
    )

    events: list[dict[str, object]] = []
    for key, summary, description, start_date in items:
        current_date = date.fromisoformat(start_date)
        item_key = f"{plan.event.type}:{plan.event.city.casefold()}:{plan.event.title.casefold()}:{key}"
        events.append({
            "id": _stable_event_id(uid, item_key),
            "summary": summary,
            "description": description,
            "location": plan.event.city,
            "start": {"date": current_date.isoformat()},
            "end": {"date": (current_date + timedelta(days=1)).isoformat()},
            "reminders": {
                "useDefault": False,
                "overrides": [{"method": "popup", "minutes": 2880}],
            },
            "extendedProperties": {
                "private": {
                    "utsavaiPlanDate": plan.event.date,
                    "utsavaiTaskId": key,
                }
            },
        })
    return events


def sync_plan_to_google_calendar(
    plan: EventPlan,
    uid: str,
    email: str,
    access_token: str,
) -> dict[str, int]:
    """Create or update an event-day entry and every timeline task, idempotently."""
    _google_identity(access_token, email)
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }
    created = 0
    updated = 0
    errors: list[str] = []
    with httpx.Client(timeout=20) as client:
        for event in _calendar_events(plan, uid):
            event_id = str(event["id"])
            encoded_id = quote(event_id, safe="")
            try:
                response = client.post(_GOOGLE_EVENTS_URL, headers=headers, json=event)
            except httpx.RequestError as exc:
                raise RuntimeError(
                    f"Google Calendar connection failed after {created} created and "
                    f"{updated} updated events."
                ) from exc
            if response.status_code == 409:
                try:
                    response = client.patch(
                        f"{_GOOGLE_EVENTS_URL}/{encoded_id}",
                        headers=headers,
                        json=event,
                    )
                except httpx.RequestError as exc:
                    raise RuntimeError(
                        f"Google Calendar update failed after {created} created and "
                        f"{updated} updated events."
                    ) from exc
                if response.is_success:
                    updated += 1
                    continue
            elif response.is_success:
                created += 1
                continue

            logger.warning(
                "google_calendar_event_sync_failed",
                extra={
                    "event_id": event_id,
                    "status_code": response.status_code,
                },
            )
            errors.append(f"{event.get('summary', 'Calendar event')}: HTTP {response.status_code}")

    if errors:
        raise RuntimeError(
            f"Google Calendar partially synced: {created} created, {updated} updated. "
            + "; ".join(errors[:3])
        )
    return {"created": created, "updated": updated}
