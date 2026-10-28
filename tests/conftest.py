import json
from datetime import date, timedelta
from pathlib import Path

import pytest

from schemas import EventPlan


@pytest.fixture
def make_plan():
    def factory(event_date: str | None = None, guest_count: int = 25) -> EventPlan:
        path = Path(__file__).resolve().parents[1] / "demo_plan.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        data["event"]["date"] = event_date or (date.today() + timedelta(days=45)).isoformat()
        data["event"]["guest_count"] = guest_count
        data["timeline"] = [
            {
                **task,
                "due_date": data["event"]["date"],
                "days_before_event": 0,
            }
            for task in data["timeline"]
        ]
        return EventPlan.model_validate(data)

    return factory
