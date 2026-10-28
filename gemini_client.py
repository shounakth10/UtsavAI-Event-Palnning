"""Google GenAI integration and bounded in-memory plan-response caching."""

import hashlib
import json
import logging
import time
from collections import OrderedDict
from copy import deepcopy
from dataclasses import dataclass
from datetime import date
from threading import Lock
from typing import Optional, Tuple

from google import genai
from google.genai import types

import config
from prompts import get_plan_system_prompt
from schemas import CriticReport, EventPlan, PlanResponse
from services.budget import normalize_budget
from services.critic import audit_plan
from services.gemini_retry import generate_content_with_retry
from services.timeline import process_timeline

logger = logging.getLogger("utsavai.gemini")


@dataclass
class PlanGenerationResult:
    needs_clarification: bool
    questions: list[str]
    plan: EventPlan | None
    critic_report: CriticReport | None = None
    fixed_issues: int = 0


_PLAN_CACHE: OrderedDict[str, PlanGenerationResult] = OrderedDict()
_CACHE_LOCK = Lock()
_CACHE_MAX_ITEMS = 128


def get_genai_client() -> genai.Client:
    """Initialize the official GenAI SDK client."""
    if not config.GEMINI_API_KEY:
        raise ValueError("GEMINI_API_KEY is not configured. Please check .env or .env.local.")
    return genai.Client(api_key=config.GEMINI_API_KEY)


def _cache_key(description: str, language: str, answers: Optional[dict[str, str]]) -> str:
    data = json.dumps(
        {"description": description, "language": language, "answers": answers or {}},
        sort_keys=True,
        ensure_ascii=False,
    )
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def _log_token_usage(operation: str, response: object) -> None:
    metadata = getattr(response, "usage_metadata", None)
    logger.info(
        "gemini_call_complete",
        extra={
            "operation": operation,
            "prompt_tokens": getattr(metadata, "prompt_token_count", None),
            "candidate_tokens": getattr(metadata, "candidates_token_count", None),
            "total_tokens": getattr(metadata, "total_token_count", None),
        },
    )


def generate_event_plan(
    description: str,
    language: str = "en",
    clarification_answers: Optional[dict[str, str]] = None,
) -> Tuple[bool, Optional[PlanGenerationResult], Optional[str]]:
    """Generate, normalize, critique, and cache a plan or clarifying questions."""
    key = _cache_key(description.strip(), language, clarification_answers)
    with _CACHE_LOCK:
        cached = _PLAN_CACHE.get(key)
        if cached is not None:
            _PLAN_CACHE.move_to_end(key)
            logger.info("plan_cache_hit", extra={"cache_key": key[:12]})
            return True, deepcopy(cached), None

    full_prompt = description.strip()
    if clarification_answers:
        qa_text = "\n".join(
            f"Q: {question}\nA: {answer}"
            for question, answer in clarification_answers.items()
            if answer.strip()
        )
        if qa_text:
            full_prompt += f"\n\nAdditional Clarifications Provided:\n{qa_text}"

    client: genai.Client | None = None
    started = time.perf_counter()
    try:
        client = get_genai_client()
        response = generate_content_with_retry(
            client.models.generate_content,
            operation="plan_generation",
            model=config.GEMINI_MODEL,
            contents=full_prompt,
            config=types.GenerateContentConfig(
                system_instruction=get_plan_system_prompt(
                    today_iso=date.today().isoformat(),
                    language=language,
                ),
                response_mime_type="application/json",
                response_schema=PlanResponse,
                temperature=0.4,
            ),
        )
        _log_token_usage("plan_generation", response)
        if not response.text:
            parsed = getattr(response, "parsed", None)
            if isinstance(parsed, PlanResponse):
                result = parsed
            else:
                raise ValueError("Empty response received from Gemini model.")
        else:
            result = PlanResponse.model_validate_json(response.text)

        critic_report: CriticReport | None = None
        fixed_issues = 0
        if not result.needs_clarification and result.plan:
            plan = result.plan
            plan.budget = normalize_budget(plan.budget, plan.event.total_budget_inr)
            plan.timeline, plan.risks = process_timeline(
                plan.timeline, plan.event.date, plan.risks
            )
            plan, critic_report, fixed_issues = audit_plan(
                plan, language=language, client=client
            )
            result.plan = plan

        generated = PlanGenerationResult(
            needs_clarification=result.needs_clarification,
            questions=result.questions,
            plan=result.plan,
            critic_report=critic_report,
            fixed_issues=fixed_issues,
        )

        with _CACHE_LOCK:
            _PLAN_CACHE[key] = deepcopy(generated)
            _PLAN_CACHE.move_to_end(key)
            while len(_PLAN_CACHE) > _CACHE_MAX_ITEMS:
                _PLAN_CACHE.popitem(last=False)

        logger.info(
            "plan_generation_complete",
            extra={
                "duration_ms": round((time.perf_counter() - started) * 1000),
                "cache_key": key[:12],
                "clarification_needed": generated.needs_clarification,
            },
        )
        return True, generated, None
    except Exception as exc:
        last_error = str(exc)
        logger.warning(
            "plan_generation_failed",
            extra={
                "duration_ms": round((time.perf_counter() - started) * 1000),
                "cache_key": key[:12],
                "error": last_error,
            },
        )

    return (
        False,
        None,
        f"Unable to generate a verified plan right now ({last_error}). Please try again.",
    )


def clear_plan_cache() -> None:
    """Clear generated plans from the process-local cache."""
    with _CACHE_LOCK:
        _PLAN_CACHE.clear()
