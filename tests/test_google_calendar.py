from datetime import date, timedelta
from types import SimpleNamespace

import pytest

from services import google_calendar
from services.google_calendar import _calendar_events, sync_plan_to_google_calendar


def test_calendar_payload_includes_event_and_one_reminded_entry_per_task(make_plan):
    plan = make_plan()

    events = _calendar_events(plan, "user-1")

    assert len(events) == len(plan.timeline) + 1
    assert events[0]["summary"].startswith("UtsavAI:")
    assert all(event["reminders"]["overrides"][0]["minutes"] == 2880 for event in events)
    assert all(event["end"]["date"] > event["start"]["date"] for event in events)
    assert len({event["id"] for event in events}) == len(events)


def test_calendar_refuses_access_token_for_a_different_account(make_plan, monkeypatch):
    monkeypatch.setattr(
        google_calendar,
        "_google_identity",
        lambda access_token, expected_email: (_ for _ in ()).throw(
            PermissionError("The Google Calendar account must match.")
        ),
    )

    with pytest.raises(PermissionError, match="must match"):
        sync_plan_to_google_calendar(
            make_plan(),
            "user-1",
            "planner@example.com",
            "google-access-token",
        )


def test_calendar_sync_creates_or_updates_all_plan_events(make_plan, monkeypatch):
    plan = make_plan()
    monkeypatch.setattr(google_calendar, "_google_identity", lambda *args: {"sub": "google-user"})

    class FakeResponse:
        def __init__(self, status_code):
            self.status_code = status_code

        @property
        def is_success(self):
            return 200 <= self.status_code < 300

    class FakeHttpClient:
        def __init__(self, timeout):
            self.posts = 0
            self.patches = 0

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def post(self, url, headers, json):
            self.posts += 1
            return FakeResponse(409 if self.posts == 1 else 200)

        def patch(self, url, headers, json):
            self.patches += 1
            return FakeResponse(200)

    client = FakeHttpClient(timeout=20)
    monkeypatch.setattr(google_calendar.httpx, "Client", lambda timeout: client)

    result = sync_plan_to_google_calendar(
        plan,
        "user-1",
        "planner@example.com",
        "google-access-token",
    )

    assert result == {"created": len(plan.timeline), "updated": 1}
    assert client.posts == len(plan.timeline) + 1
    assert client.patches == 1
