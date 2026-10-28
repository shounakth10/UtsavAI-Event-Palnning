import pytest

from services import notifications
from services.notifications import send_plan_email


def test_email_is_sent_to_the_verified_identity_supplied_by_auth(make_plan, monkeypatch):
    sent = {}

    class FakeSMTP:
        def __init__(self, host, port, timeout):
            sent["connection"] = (host, port, timeout)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def ehlo(self):
            pass

        def starttls(self):
            pass

        def login(self, username, password):
            sent["login"] = (username, password)

        def send_message(self, message):
            sent["to"] = message["To"]
            sent["from"] = message["From"]
            sent["body"] = message.get_content()

    monkeypatch.setattr(notifications.config, "SMTP_HOST", "smtp.example.com")
    monkeypatch.setattr(notifications.config, "SMTP_PORT", 587)
    monkeypatch.setattr(notifications.config, "SMTP_USERNAME", "sender")
    monkeypatch.setattr(notifications.config, "SMTP_PASSWORD", "test-password")
    monkeypatch.setattr(notifications.config, "SMTP_FROM_EMAIL", "events@example.com")
    monkeypatch.setattr(notifications.config, "SMTP_USE_SSL", False)
    monkeypatch.setattr(notifications.smtplib, "SMTP", FakeSMTP)

    send_plan_email("planner@example.com", make_plan())

    assert sent["to"] == "planner@example.com"
    assert sent["from"] == "events@example.com"
    assert "UtsavAI" in sent["body"]


def test_email_requires_smtp_configuration(make_plan, monkeypatch):
    monkeypatch.setattr(notifications.config, "SMTP_HOST", "")

    with pytest.raises(RuntimeError, match="SMTP_HOST"):
        send_plan_email("planner@example.com", make_plan())
