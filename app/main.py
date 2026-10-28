"""FastAPI API and static frontend host for UtsavAI."""

import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

import config
import gemini_client
from schemas import ChatRequest, EventPlan, PlanRequest
from schemas import CalendarSyncRequest
from services.auth import AuthenticatedUser, get_current_user
from services.copilot import ask_copilot
from services.critic import check_plan_programmatically
from services.critic import repair_plan_programmatically
from services.budget import normalize_budget
from services.export import generate_ics, generate_pdf, generate_whatsapp_summary
from services.google_calendar import sync_plan_to_google_calendar
from services.notifications import send_plan_email
import smtplib

class JsonLogFormatter(logging.Formatter):
    _standard_fields = set(logging.makeLogRecord({}).__dict__) | {"message", "asctime"}

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": datetime.fromtimestamp(record.created, timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        payload.update({
            key: value
            for key, value in record.__dict__.items()
            if key not in self._standard_fields and not key.startswith("_")
        })
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str, ensure_ascii=False)


root_logger = logging.getLogger()
if not any(getattr(handler, "_utsavai_json", False) for handler in root_logger.handlers):
    json_handler = logging.StreamHandler()
    json_handler.setFormatter(JsonLogFormatter())
    json_handler._utsavai_json = True
    root_logger.addHandler(json_handler)
root_logger.setLevel(logging.INFO)
logger = logging.getLogger("utsavai.http")

limiter = Limiter(key_func=get_remote_address, default_limits=["20/minute"])
app = FastAPI(
    title="UtsavAI - GenAI Event Planning Assistant",
    description="Plan culturally grounded celebrations across India.",
    version="1.1.0",
)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://192.168.1.7:3000",
    ],
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type", "Authorization"],
)

BASE_DIR = Path(__file__).resolve().parent.parent
STATIC_DIR = BASE_DIR / "app" / "static"
FRONTEND_DIR = STATIC_DIR if (STATIC_DIR / "index.html").is_file() else BASE_DIR / "out"


@app.middleware("http")
async def log_request_duration(request: Request, call_next):
    started = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        logger.exception(
            "http_request_failed",
            extra={
                "method": request.method,
                "path": request.url.path,
                "duration_ms": round((time.perf_counter() - started) * 1000),
            },
        )
        raise
    logger.info(
        "http_request_complete",
        extra={
            "method": request.method,
            "path": request.url.path,
            "status_code": response.status_code,
            "duration_ms": round((time.perf_counter() - started) * 1000),
            "client_ip": get_remote_address(request),
        },
    )
    return response


@app.get("/health")
def health_check():
    return {
        "status": "ok",
        "app": "UtsavAI",
        "version": app.version,
        "model": config.GEMINI_MODEL,
        "api_key_configured": bool(config.GEMINI_API_KEY),
    }


@app.post("/api/plan")
@limiter.limit("20/minute")
def create_plan(
    request: Request,
    payload: PlanRequest,
    _user: AuthenticatedUser = Depends(get_current_user),
):
    description = payload.description.strip()
    if not description:
        raise HTTPException(status_code=400, detail="Event description cannot be empty.")
    if len(description) > config.MAX_DESCRIPTION_LENGTH:
        raise HTTPException(
            status_code=400,
            detail=f"Description exceeds maximum limit of {config.MAX_DESCRIPTION_LENGTH} characters.",
        )
    if any(len(answer) > config.MAX_DESCRIPTION_LENGTH for answer in (payload.clarification_answers or {}).values()):
        raise HTTPException(status_code=400, detail="A clarification answer exceeds the maximum length.")

    success, result, error = gemini_client.generate_event_plan(
        description=description,
        language=payload.language,
        clarification_answers=payload.clarification_answers,
    )
    if not success or result is None:
        raise HTTPException(status_code=502, detail=error or "Plan generation failed.")
    if result.needs_clarification:
        return {
            "status": "needs_clarification",
            "questions": result.questions[:2],
        }
    if result.plan is None:
        raise HTTPException(
            status_code=502,
            detail="The planner returned neither a plan nor clarification questions.",
        )
    return {
        "status": "ok",
        "plan": result.plan.model_dump(mode="json"),
        "critic_report": result.critic_report.model_dump(mode="json") if result.critic_report else None,
        "fixed_issues": result.fixed_issues,
    }


@app.post("/api/chat")
@limiter.limit("20/minute")
def copilot_chat(
    request: Request,
    payload: ChatRequest,
    _user: AuthenticatedUser = Depends(get_current_user),
):
    message = payload.message.strip()
    if not message:
        raise HTTPException(status_code=400, detail="Message cannot be empty.")
    if len(message) > config.MAX_DESCRIPTION_LENGTH:
        raise HTTPException(status_code=400, detail="Message exceeds the maximum length.")

    success, response, error = ask_copilot(
        message=message,
        plan=payload.plan,
        language=payload.language,
        history=payload.history,
    )
    if not success or response is None:
        raise HTTPException(status_code=502, detail=error or "Copilot could not process the request.")
    return {
        "status": "ok",
        "reply": response.reply,
        "updated_plan": response.updated_plan.model_dump(mode="json") if response.updated_plan else None,
        "changes": [change.model_dump(mode="json") for change in response.changes],
        "critic_report": response.critic_report.model_dump(mode="json") if response.critic_report else None,
        "fixed_issues": response.fixed_issues,
    }


@app.post("/api/export/pdf")
@limiter.limit("20/minute")
def export_pdf(
    request: Request,
    plan: EventPlan,
    _user: AuthenticatedUser = Depends(get_current_user),
):
    pdf_content = generate_pdf(plan)
    return Response(
        content=pdf_content,
        media_type="application/pdf",
        headers={"Content-Disposition": 'attachment; filename="utsavai-event-plan.pdf"'},
    )


@app.post("/api/export/ics")
@limiter.limit("20/minute")
def export_calendar(
    request: Request,
    plan: EventPlan,
    _user: AuthenticatedUser = Depends(get_current_user),
):
    ics_content = generate_ics(plan)
    filename = f"utsavai_{plan.event.type}_{plan.event.date}.ics"
    return Response(
        content=ics_content,
        media_type="text/calendar; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.post("/api/export/whatsapp")
@limiter.limit("20/minute")
def export_whatsapp(
    request: Request,
    plan: EventPlan,
    _user: AuthenticatedUser = Depends(get_current_user),
):
    return {"status": "ok", "text": generate_whatsapp_summary(plan)}


@app.post("/api/email/plan")
@limiter.limit("5/minute")
def email_plan_to_signed_in_user(
    request: Request,
    plan: EventPlan,
    user: AuthenticatedUser = Depends(get_current_user),
):
    try:
        send_plan_email(user.email, plan)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except (smtplib.SMTPException, OSError) as exc:
        logger.exception("plan_email_delivery_failed", extra={"uid": user.uid})
        raise HTTPException(
            status_code=502,
            detail="Email delivery failed. Check the SMTP configuration and try again.",
        ) from exc
    logger.info("plan_email_delivered", extra={"uid": user.uid})
    return {"status": "ok", "sent": True, "recipient": user.email}


@app.post("/api/calendar/sync")
@limiter.limit("5/minute")
def sync_google_calendar(
    request: Request,
    payload: CalendarSyncRequest,
    user: AuthenticatedUser = Depends(get_current_user),
):
    try:
        result = sync_plan_to_google_calendar(
            payload.plan,
            user.uid,
            user.email,
            payload.google_access_token.get_secret_value(),
        )
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except (RuntimeError, OSError) as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    logger.info(
        "google_calendar_sync_complete",
        extra={
            "uid": user.uid,
            "created_count": result["created"],
            "updated_count": result["updated"],
        },
    )
    return {"status": "ok", **result}


@app.get("/api/demo")
@limiter.limit("20/minute")
def get_demo_plan(request: Request):
    demo_file = BASE_DIR / "demo_plan.json"
    if not demo_file.is_file():
        raise HTTPException(status_code=404, detail="Demo plan not found.")
    with demo_file.open("r", encoding="utf-8") as source:
        plan = EventPlan.model_validate(json.load(source))
    plan.budget = normalize_budget(plan.budget, plan.event.total_budget_inr)
    plan = repair_plan_programmatically(plan)
    report = check_plan_programmatically(plan)
    return {
        "status": "ok",
        "plan": plan.model_dump(mode="json"),
        "critic_report": report.model_dump(mode="json"),
        "fixed_issues": 0,
    }


if STATIC_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/")
def serve_index():
    index_file = FRONTEND_DIR / "index.html"
    if index_file.is_file():
        return FileResponse(str(index_file))
    return {
        "message": "Frontend isn't built. Use `npm run dev` for the UI or build the static frontend."
    }


if FRONTEND_DIR.is_dir():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")
