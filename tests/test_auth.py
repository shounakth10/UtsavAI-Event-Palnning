import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials

from services import auth


def test_firebase_auth_requires_bearer_token():
    with pytest.raises(HTTPException) as error:
        auth.get_current_user(None)

    assert error.value.status_code == 401


def test_firebase_admin_requires_application_default_credentials(monkeypatch):
    class MissingCredentials:
        def get_credential(self):
            raise RuntimeError("no application default credentials")

    def no_default_app():
        raise ValueError("no default app")

    monkeypatch.setattr(auth.config, "FIREBASE_PROJECT_ID", "test-project")
    monkeypatch.setattr(auth.firebase_admin, "get_app", no_default_app)
    monkeypatch.setattr(
        auth.credentials,
        "ApplicationDefault",
        MissingCredentials,
    )
    auth._firebase_app.cache_clear()

    try:
        with pytest.raises(HTTPException) as error:
            auth._firebase_app()
    finally:
        auth._firebase_app.cache_clear()

    assert error.value.status_code == 503
    assert "GOOGLE_APPLICATION_CREDENTIALS" in error.value.detail
    assert "gcloud auth application-default login" in error.value.detail


def test_firebase_auth_requires_verified_email(monkeypatch):
    monkeypatch.setattr(auth, "_firebase_app", lambda: object())
    monkeypatch.setattr(
        auth.firebase_auth,
        "verify_id_token",
        lambda token, app, check_revoked: {
            "uid": "user-1",
            "email": "planner@example.com",
            "email_verified": False,
        },
    )
    credentials = HTTPAuthorizationCredentials(
        scheme="Bearer",
        credentials="firebase-id-token",
    )

    with pytest.raises(HTTPException) as error:
        auth.get_current_user(credentials)

    assert error.value.status_code == 403


def test_firebase_auth_returns_verified_user(monkeypatch):
    monkeypatch.setattr(auth, "_firebase_app", lambda: object())
    monkeypatch.setattr(
        auth.firebase_auth,
        "verify_id_token",
        lambda token, app, check_revoked: {
            "uid": "user-1",
            "email": "planner@example.com",
            "email_verified": True,
        },
    )
    credentials = HTTPAuthorizationCredentials(
        scheme="Bearer",
        credentials="firebase-id-token",
    )

    user = auth.get_current_user(credentials)

    assert user.uid == "user-1"
    assert user.email == "planner@example.com"


@pytest.mark.parametrize(
    "error_type,expected_status,detail",
    [
        (
            auth.firebase_auth.ExpiredIdTokenError,
            401,
            "expired or was revoked",
        ),
        (
            auth.firebase_auth.InvalidIdTokenError,
            401,
            "same Firebase project",
        ),
        (
            auth.firebase_auth.CertificateFetchError,
            503,
            "Firebase could not verify",
        ),
    ],
)
def test_firebase_auth_reports_verification_failure_category(
    monkeypatch,
    error_type,
    expected_status,
    detail,
):
    monkeypatch.setattr(auth, "_firebase_app", lambda: object())

    def reject_token(*args, **kwargs):
        if error_type in (
            auth.firebase_auth.ExpiredIdTokenError,
            auth.firebase_auth.CertificateFetchError,
        ):
            raise error_type("verification failed", Exception("cause"))
        raise error_type("verification failed")

    monkeypatch.setattr(auth.firebase_auth, "verify_id_token", reject_token)
    credentials = HTTPAuthorizationCredentials(
        scheme="Bearer",
        credentials="firebase-id-token",
    )

    with pytest.raises(HTTPException) as error:
        auth.get_current_user(credentials)

    assert error.value.status_code == expected_status
    assert detail in error.value.detail
