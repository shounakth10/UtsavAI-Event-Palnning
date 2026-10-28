"""Firebase ID-token verification for authenticated API endpoints."""

import logging
import os
from functools import lru_cache

import firebase_admin
from firebase_admin import auth as firebase_auth
from firebase_admin import credentials
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel

import config

logger = logging.getLogger("utsavai.auth")
_bearer = HTTPBearer(auto_error=False)


class AuthenticatedUser(BaseModel):
    uid: str
    email: str


@lru_cache(maxsize=1)
def _firebase_app():
    if not config.FIREBASE_PROJECT_ID:
        raise HTTPException(
            status_code=503,
            detail="Firebase Authentication is not configured. Set FIREBASE_PROJECT_ID.",
        )
    try:
        try:
            app = firebase_admin.get_app()
        except ValueError:
            credential_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "")
            credential = (
                credentials.Certificate(credential_path)
                if credential_path
                else credentials.ApplicationDefault()
            )
            if not credential_path:
                credential.get_credential()
            app = firebase_admin.initialize_app(
                credential,
                options={"projectId": config.FIREBASE_PROJECT_ID},
            )
        return app
    except Exception as exc:
        logger.exception("firebase_admin_initialization_failed")
        raise HTTPException(
            status_code=503,
            detail=(
                "Firebase Admin credentials are unavailable or invalid. Set "
                "GOOGLE_APPLICATION_CREDENTIALS to a service-account JSON file, or "
                "configure Google Application Default Credentials with "
                "`gcloud auth application-default login`."
            ),
        ) from exc


def get_current_user(
    authorization: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> AuthenticatedUser:
    """Verify the Firebase ID token and require a verified account email."""
    if authorization is None or authorization.scheme.casefold() != "bearer":
        raise HTTPException(status_code=401, detail="Sign in to use this feature.")
    app = _firebase_app()
    try:
        decoded = firebase_auth.verify_id_token(
            authorization.credentials,
            app=app,
            check_revoked=True,
        )
    except (firebase_auth.ExpiredIdTokenError, firebase_auth.RevokedIdTokenError) as exc:
        logger.info(
            "firebase_id_token_rejected",
            extra={"error_type": type(exc).__name__},
        )
        raise HTTPException(
            status_code=401,
            detail="Your sign-in token expired or was revoked. Sign out and sign in again.",
        ) from exc
    except firebase_auth.InvalidIdTokenError as exc:
        logger.warning(
            "firebase_id_token_invalid",
            extra={"error_type": type(exc).__name__},
        )
        raise HTTPException(
            status_code=401,
            detail=(
                "Firebase rejected this sign-in token. Confirm the frontend and backend "
                "use the same Firebase project, then sign out and sign in again."
            ),
        ) from exc
    except Exception as exc:
        logger.exception(
            "firebase_id_token_verification_failed",
            extra={"error_type": type(exc).__name__},
        )
        raise HTTPException(
            status_code=503,
            detail="Firebase could not verify the sign-in token. Check the backend logs and Firebase Admin credentials.",
        ) from exc

    email = decoded.get("email")
    if not decoded.get("uid") or not email or decoded.get("email_verified") is not True:
        raise HTTPException(
            status_code=403,
            detail="Verify your account email before using this feature.",
        )
    return AuthenticatedUser(uid=decoded["uid"], email=email)
