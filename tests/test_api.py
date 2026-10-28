import os
from datetime import date, timedelta
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import gemini_client
from app.main import app
from schemas import CriticReport, PlanResponse
from services.auth import AuthenticatedUser, get_current_user


@pytest.fixture(autouse=True)
def authenticated_requests():
    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(
        uid="test-user",
        email="planner@example.com",
    )
    yield
    app.dependency_overrides.pop(get_current_user, None)


@pytest.mark.parametrize(
    "origin",
    ["http://localhost:3000", "http://192.168.1.7:3000"],
)
def test_plan_preflight_allows_firebase_authorization_header(origin):
    response = TestClient(app).options(
        "/api/plan",
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "authorization,content-type",
        },
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == origin
    assert "authorization" in response.headers["access-control-allow-headers"].lower()


CASES = [
    ("A tiny-budget wedding in Pune, 20 guests, 2027-01-20, budget ₹20,000.", "en", False),
    ("A wedding in Pune yesterday for 200 guests with a ₹10 lakh budget.", "en", False),
    ("A wedding for 1000 guests in Mumbai on 2027-02-01 with a ₹50 lakh budget.", "en", False),
    ("A wedding in Jaipur on 2027-02-01 for 200 guests.", "en", True),
    ("पुणे में 20 जनवरी 2027 को 200 मेहमानों की शादी, बजट ₹20 लाख।", "hi", False),
    ("Community Diwali celebration for 200 families in Pune on 2027-11-01, ₹8 lakh.", "en", False),
    ("Corporate-style birthday for 80 colleagues in Bengaluru on 2027-03-15, budget ₹3 lakh.", "en", False),
    ("asdf qwer blorp ???", "en", True),
]


class FakeModels:
    def __init__(self, response_text: str):
        self.response_text = response_text
        self.calls = 0

    def generate_content(self, **kwargs):
        self.calls += 1
        return SimpleNamespace(text=self.response_text, usage_metadata=None)


class FakeClient:
    def __init__(self, response_text: str):
        self.models = FakeModels(response_text)


@pytest.mark.parametrize("description,language,needs_clarification", CASES)
def test_plan_api_for_requested_scenarios(
    description,
    language,
    needs_clarification,
    make_plan,
    monkeypatch,
):
    plan = make_plan(
        event_date=(date.today() + timedelta(days=60)).isoformat(),
        guest_count=1000 if "1000" in description else 200,
    )
    expected = PlanResponse(
        needs_clarification=needs_clarification,
        questions=["What is the event date?", "What is the approximate budget?"] if needs_clarification else [],
        plan=None if needs_clarification else plan,
    )
    client = FakeClient(expected.model_dump_json())
    monkeypatch.setattr(gemini_client, "get_genai_client", lambda: client)
    monkeypatch.setattr(
        gemini_client,
        "audit_plan",
        lambda value, language="en", client=None: (
            value,
            CriticReport(passed=True, issues=[]),
            0,
        ),
    )
    gemini_client.clear_plan_cache()

    response = TestClient(app).post(
        "/api/plan",
        json={"description": description, "language": language},
    )

    assert response.status_code == 200
    result = response.json()
    if needs_clarification:
        assert result["status"] == "needs_clarification"
        assert len(result["questions"]) <= 2
    else:
        assert result["status"] == "ok"
        assert result["plan"]["event"]["guest_count"] > 0
        assert result["critic_report"]["passed"]


def test_plan_generation_cache_uses_description_and_language(make_plan, monkeypatch):
    output = PlanResponse(needs_clarification=False, questions=[], plan=make_plan())
    client = FakeClient(output.model_dump_json())
    monkeypatch.setattr(gemini_client, "get_genai_client", lambda: client)
    monkeypatch.setattr(
        gemini_client,
        "audit_plan",
        lambda value, language="en", client=None: (
            value,
            CriticReport(passed=True, issues=[]),
            0,
        ),
    )
    gemini_client.clear_plan_cache()

    first = gemini_client.generate_event_plan("Wedding in Pune", "en")
    second = gemini_client.generate_event_plan("Wedding in Pune", "en")
    different_language = gemini_client.generate_event_plan("Wedding in Pune", "hi")

    assert first[0] and second[0] and different_language[0]
    assert client.models.calls == 2


def test_demo_endpoint_returns_a_verified_wedding_without_gemini():
    response = TestClient(app).get("/api/demo")

    assert response.status_code == 200
    result = response.json()
    assert result["plan"]["event"]["type"] == "wedding"
    assert result["critic_report"]["passed"]


def test_email_api_only_targets_verified_signed_in_email(make_plan, monkeypatch):
    sent_to = []
    monkeypatch.setattr("app.main.send_plan_email", lambda email, plan: sent_to.append(email))

    response = TestClient(app).post("/api/email/plan", json=make_plan().model_dump(mode="json"))

    assert response.status_code == 200
    assert sent_to == ["planner@example.com"]
    assert response.json()["recipient"] == "planner@example.com"


def test_calendar_api_uses_authenticated_user_and_access_token(make_plan, monkeypatch):
    received = {}

    def fake_sync(plan, uid, email, access_token):
        received.update(uid=uid, email=email, access_token=access_token)
        return {"created": 4, "updated": 2}

    monkeypatch.setattr("app.main.sync_plan_to_google_calendar", fake_sync)
    response = TestClient(app).post(
        "/api/calendar/sync",
        json={
            "plan": make_plan().model_dump(mode="json"),
            "google_access_token": "google-oauth-access-token",
        },
    )

    assert response.status_code == 200
    assert received == {
        "uid": "test-user",
        "email": "planner@example.com",
        "access_token": "google-oauth-access-token",
    }


@pytest.mark.skipif(
    os.getenv("UTSAVAI_LIVE_TEST") != "1",
    reason="Set UTSAVAI_LIVE_TEST=1 to run the optional paid Gemini integration test.",
)
def test_optional_live_gemini_integration():
    success, result, error = gemini_client.generate_event_plan(
        "A birthday in Pune in 2027 for 10 guests with a ₹10,000 budget."
    )

    assert success, error
    assert result is not None
