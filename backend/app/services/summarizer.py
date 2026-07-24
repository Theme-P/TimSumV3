"""Incremental, context-preserving meeting summarization via the NTC Gateway."""

import requests
import os
import json
import re
import logging
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional

from pymongo.database import Database

from ..models.meeting import MEETING_TYPES, get_meeting_focus_prompt
from .cancellation import JobCancelled
from .summary_pipeline import (
    GROUNDING_RULES,
    SUMMARY_GEMMA_EMPTY_WARNING,
    SUMMARY_GEMMA_PARTIAL_WARNING,
    SUMMARY_USER_WARNING,
    append_user_warning,
    build_token_check,
    chunk_segments,
    estimate_tokens,
    sample_text_windows,
    segments_from_text,
    summarize_agenda_collection,
    summarize_agenda_segments,
    summarize_transcript_incrementally,
    transcript_fallback_text,
)

logger = logging.getLogger(__name__)


@dataclass
class LLMCallResult:
    """Internal model-call outcome; legacy callers still receive plain text."""

    content: str = ""
    timed_out: bool = False
    error_kind: Optional[str] = None
    model: Optional[str] = None
    attempts: int = 0


def _coerce_llm_result(value: Any, *, model: Optional[str] = None) -> LLMCallResult:
    if isinstance(value, LLMCallResult):
        return value
    return LLMCallResult(content=str(value or "").strip(), model=model)


def _run_cancel_check(cancel_check: Optional[Callable[[], None]] = None):
    if cancel_check:
        cancel_check()


def _clean_env_value(value: Optional[str]) -> Optional[str]:
    """Normalize dotenv/docker env values without exposing secrets in logs."""
    if value is None:
        return None
    return value.strip().strip('"').strip("'")


def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(value, maximum))


def _env_float(name: str, default: float, minimum: float, maximum: float) -> float:
    try:
        value = float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(value, maximum))


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes", "y", "on"}:
        return True
    if normalized in {"0", "false", "no", "n", "off"}:
        return False
    return default


# NTC AI Gateway API configuration
DEFAULT_NTC_MODEL = "ict-ollama/gemma4:31b-it-q4_K_M"
DEFAULT_GLM_FALLBACK_MODEL = "ict-ollama/glm4.7flashq4:latest"
DEFAULT_QWEN_FALLBACK_MODEL = "ict-ollama/qwen2.5:72b-instruct-q4_K_M"
DEFAULT_FALLBACK_MODEL_VALUE = DEFAULT_GLM_FALLBACK_MODEL
DEFAULT_FALLBACK_MODELS = [
    model.strip()
    for model in os.getenv("NTC_FALLBACK_MODELS", DEFAULT_FALLBACK_MODEL_VALUE).split(",")
    if model.strip()
] or [DEFAULT_GLM_FALLBACK_MODEL]
LEGACY_PRIMARY_MODELS = {"gpt-4.1"}
LEGACY_FALLBACK_MODEL_ALIASES = {
    "qwen2.5:72b-instruct-q4_K_M": DEFAULT_QWEN_FALLBACK_MODEL,
    "seallms-v3-7b:latest": DEFAULT_GLM_FALLBACK_MODEL,
    "ict-ollama/seallms-v3-7b:latest": DEFAULT_GLM_FALLBACK_MODEL,
}
LEGACY_FALLBACK_MODELS_TO_DROP = {"scb10x/typhoon2.1-gemma3-12b"}
NON_REASONING_MODELS = {
    DEFAULT_NTC_MODEL,
    DEFAULT_GLM_FALLBACK_MODEL,
    DEFAULT_QWEN_FALLBACK_MODEL,
}

NTC_API_KEY = _clean_env_value(os.getenv("NTC_API_KEY"))
NTC_API_URL = _clean_env_value(os.getenv("NTC_API_URL")) or "https://aigateway.ntictsolution.com/v1/chat/completions"
NTC_MODEL = _clean_env_value(os.getenv("NTC_MODEL")) or DEFAULT_NTC_MODEL
NTC_LLM_MAX_RETRIES = _env_int("NTC_LLM_MAX_RETRIES", 1, 0, 3)
NTC_LLM_RETRY_BACKOFF_SECONDS = _env_float("NTC_LLM_RETRY_BACKOFF_SECONDS", 2.0, 0.0, 30.0)
NTC_LLM_MODEL_COOLDOWN_SECONDS = _env_int("NTC_LLM_MODEL_COOLDOWN_SECONDS", 600, 30, 3600)
NTC_LLM_CONNECT_TIMEOUT_SECONDS = _env_int("NTC_LLM_CONNECT_TIMEOUT_SECONDS", 15, 5, 60)
NTC_LLM_STREAM_RESPONSES = _env_bool("NTC_LLM_STREAM_RESPONSES", True)

_MODEL_COOLDOWN_UNTIL: dict[str, float] = {}

def _serialize_mongo_doc(doc: Optional[dict]) -> Optional[dict]:
    if not doc:
        return doc
    doc = dict(doc)
    if "_id" in doc:
        doc["_id"] = str(doc["_id"])
    return doc


def _get_pymongo_db(mongo_service=None) -> Optional[Database]:
    if mongo_service is None:
        return None
    if isinstance(mongo_service, Database):
        return mongo_service
    db = getattr(mongo_service, "db", None)
    return db if isinstance(db, Database) else None


def _fetch_llm_config(mongo_service=None, name: str = "default_fallback") -> Optional[dict]:
    if mongo_service is None:
        return None

    if not isinstance(mongo_service, Database):
        getter = getattr(mongo_service, "get_llm_config", None)
        if callable(getter):
            return getter(name)

    db = _get_pymongo_db(mongo_service)
    if db is None:
        return None
    return _serialize_mongo_doc(db.llm_config.find_one({"name": name}))


def _fetch_meeting_template(mongo_service=None, meeting_type_id: int = 0) -> Optional[dict]:
    if mongo_service is None:
        return None

    if not isinstance(mongo_service, Database):
        getter = getattr(mongo_service, "get_meeting_template", None)
        if callable(getter):
            return getter(meeting_type_id)

    db = _get_pymongo_db(mongo_service)
    if db is None:
        return None
    return _serialize_mongo_doc(db.meeting_template.find_one({"meeting_type_id": meeting_type_id}))


def _sanitize_gateway_error(text: str) -> str:
    sanitized = text or ""
    sanitized = re.sub(r"(Received API Key\s*=\s*)[^,\s]+", r"\1[redacted]", sanitized)
    sanitized = re.sub(r"(Key Hash \(Token\)\s*=\s*)[A-Fa-f0-9]+", r"\1[redacted]", sanitized)
    sanitized = re.sub(r"sk-[A-Za-z0-9._-]+", "sk-[redacted]", sanitized)
    return sanitized[:1000]


def _prompt_requests_json(system_prompt: str, user_prompt: str) -> bool:
    return bool(re.search(r"\bjson\b", f"{system_prompt}\n{user_prompt}", flags=re.IGNORECASE))


def _has_meaningful_json_value(value) -> bool:
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, dict):
        return any(_has_meaningful_json_value(item) for item in value.values())
    if isinstance(value, list):
        return any(_has_meaningful_json_value(item) for item in value)
    return value is not None


def _normalize_model_name(value: Optional[str]) -> str:
    return (_clean_env_value(value) or "").strip()


def _resolve_primary_model(config_model: Optional[str]) -> str:
    model = _normalize_model_name(config_model)
    if not model or model in LEGACY_PRIMARY_MODELS:
        return NTC_MODEL
    return model


def _resolve_fallback_models(value) -> list[str]:
    if not value:
        return DEFAULT_FALLBACK_MODELS
    if isinstance(value, str):
        raw_models = [item.strip() for item in value.split(",") if item.strip()]
    else:
        raw_models = [str(item).strip() for item in value if item and str(item).strip()]

    models: list[str] = []
    for model in raw_models:
        mapped = LEGACY_FALLBACK_MODEL_ALIASES.get(model, model)
        if mapped in LEGACY_FALLBACK_MODELS_TO_DROP:
            continue
        if mapped not in models:
            models.append(mapped)
    return models or DEFAULT_FALLBACK_MODELS

def _normalize_llm_config(config: Optional[dict]) -> dict:
    config = config or {}
    return {
        "primary_model": _resolve_primary_model(config.get("primary_model")),
        "fallback_models": _resolve_fallback_models(config.get("fallback_models")),
        "temperature": config.get("temperature", 0.3),
        "max_tokens": config.get("max_tokens", 4000),
    }


def get_llm_config(mongo_service=None) -> dict:
    if mongo_service is not None:
        try:
            config = _fetch_llm_config(mongo_service, "default_fallback")
            if config:
                return _normalize_llm_config(config)
        except Exception as e:
            logger.error(f"Error getting LLM config from DB: {e}")
            
    return _normalize_llm_config({
        "primary_model": NTC_MODEL,
        "fallback_models": DEFAULT_FALLBACK_MODELS,
        "temperature": 0.3,
        "max_tokens": 4000
    })

def _get_template_for_meeting(meeting_type_id: int, mongo_service=None) -> dict:
    """Fetch meeting template from DB or fallback to default."""
    if mongo_service is not None:
        try:
            template = _fetch_meeting_template(mongo_service, meeting_type_id)
            if template:
                return template
        except Exception as e:
            logger.error(f"Error fetching meeting template from DB: {e}")
            
    from ..models.meeting_template import _get_default_system_prompt
    return {
        "system_prompt": _get_default_system_prompt(meeting_type_id),
        "temperature": 0.4,
        "max_tokens": 4000
    }




# ============================================================
# NTC AI Gateway API Helper
# ============================================================

def _extract_response_content(response_data: dict, model: str, glm_text_wrapper: bool) -> str:
    content = response_data["choices"][0]["message"]["content"]
    if model == DEFAULT_GLM_FALLBACK_MODEL:
        try:
            structured_content = json.loads(content)
        except (json.JSONDecodeError, TypeError):
            logger.warning("Rejected unstructured GLM fallback response")
            return ""
        if glm_text_wrapper:
            content = structured_content.get("response", "")
        elif not _has_meaningful_json_value(structured_content):
            logger.warning("Rejected empty GLM JSON fallback response")
            return ""

    usage = response_data.get("usage")
    if isinstance(usage, dict):
        logger.info(
            "LLM usage model=%s prompt_tokens=%s completion_tokens=%s total_tokens=%s",
            model,
            usage.get("prompt_tokens"),
            usage.get("completion_tokens"),
            usage.get("total_tokens"),
        )
    return (content or "").strip()


def _stream_delta_from_data(data: dict) -> str:
    choices = data.get("choices")
    if not isinstance(choices, list) or not choices:
        return ""
    choice = choices[0] if isinstance(choices[0], dict) else {}
    delta = choice.get("delta")
    if isinstance(delta, dict) and delta.get("content") is not None:
        return str(delta.get("content") or "")
    message = choice.get("message")
    if isinstance(message, dict) and message.get("content") is not None:
        return str(message.get("content") or "")
    if choice.get("text") is not None:
        return str(choice.get("text") or "")
    return ""


def _is_read_timeout_exception(exc: BaseException) -> bool:
    if exc.__class__.__name__ == "ReadTimeout":
        return True
    context = getattr(exc, "__context__", None)
    if context is not None and context.__class__.__name__ == "ReadTimeout":
        return True
    return "read timed out" in str(exc).lower()


def _read_streaming_response(
    resp: requests.Response,
    model: str,
    timeout: Optional[int],
) -> tuple[str, bool]:
    parts: list[str] = []
    timed_out = False
    deadline = time.monotonic() + timeout if timeout is not None else None

    try:
        for raw_line in resp.iter_lines(decode_unicode=True):
            now = time.monotonic()
            if deadline is not None and now >= deadline:
                timed_out = True
                break
            if not raw_line:
                continue

            line = raw_line.strip()
            if line.startswith("data:"):
                line = line[5:].strip()
            if not line or line == "[DONE]":
                break

            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                logger.debug("Ignoring non-JSON LLM stream line for model %s", model)
                continue

            delta = _stream_delta_from_data(data)
            if delta:
                parts.append(delta)

            usage = data.get("usage")
            if isinstance(usage, dict):
                logger.info(
                    "LLM usage model=%s prompt_tokens=%s completion_tokens=%s total_tokens=%s",
                    model,
                    usage.get("prompt_tokens"),
                    usage.get("completion_tokens"),
                    usage.get("total_tokens"),
                )
    except requests.exceptions.RequestException as exc:
        if not _is_read_timeout_exception(exc) or not parts:
            raise
        timed_out = True

    content = "".join(parts).strip()
    if timed_out and content:
        logger.warning(
            "LLM streaming model %s reached timeout; returning partial output chars=%s",
            model,
            len(content),
        )
    elif content:
        logger.info("LLM streaming completed model=%s output_chars=%s", model, len(content))
    return content, timed_out


def _streaming_not_supported(status_code: int, error_text: str) -> bool:
    return status_code in {400, 415, 422} and "stream" in error_text.lower()


def _call_ntc_gateway(
    system_prompt: str,
    user_prompt: str,
    temperature: float = 0.3,
    max_tokens: int = 4000,
    timeout: Optional[int] = 120,
    model_name: str = None,
    cooldown_on_read_timeout: bool = True,
    cooldown_on_api_error: bool = True,
    attempt_timeout_provider: Optional[Callable[[str, int, Optional[int]], Optional[int]]] = None,
) -> LLMCallResult:
    """Call the NTC gateway with bounded retries and per-model cooldown."""
    if not NTC_API_KEY:
        logger.error("NTC_API_KEY not set")
        return LLMCallResult(error_kind="configuration_error")

    model = model_name or NTC_MODEL
    now = time.monotonic()
    cooldown_until = _MODEL_COOLDOWN_UNTIL.get(model, 0)
    if cooldown_until > now:
        logger.warning(
            "Skipping model %s during cooldown (%ss remaining)",
            model,
            max(1, round(cooldown_until - now)),
        )
        return LLMCallResult(error_kind="model_cooldown", model=model)

    headers = {
        "Authorization": f"Bearer {NTC_API_KEY}",
        "Content-Type": "application/json",
    }
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]
    glm_text_wrapper = False
    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if model in NON_REASONING_MODELS:
        payload.update({"think": False, "reasoning_effort": "none"})
    if model == DEFAULT_GLM_FALLBACK_MODEL:
        if _prompt_requests_json(system_prompt, user_prompt):
            payload["response_format"] = {"type": "json_object"}
        else:
            glm_text_wrapper = True
            messages[0]["content"] += (
                "\nReturn only a JSON object whose response field contains the final answer. "
                "Do not include analysis or reasoning."
            )
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "final_response",
                    "strict": True,
                    "schema": {
                        "type": "object",
                        "properties": {"response": {"type": "string"}},
                        "required": ["response"],
                        "additionalProperties": False,
                    },
                },
            }

    request_tokens = estimate_tokens(system_prompt) + estimate_tokens(user_prompt)
    attempts = NTC_LLM_MAX_RETRIES + 1
    retriable_statuses = {408, 425, 429, 500, 502, 503, 504}
    stream_response = NTC_LLM_STREAM_RESPONSES and model != DEFAULT_GLM_FALLBACK_MODEL
    last_error_kind: Optional[str] = None
    attempts_made = 0

    def timeout_for_attempt(attempt_number: int) -> Optional[int]:
        if attempt_timeout_provider is None:
            return timeout
        provided = attempt_timeout_provider(model, attempt_number, timeout)
        if timeout is None:
            return provided
        if provided is None:
            return timeout
        return max(1, min(int(timeout), int(provided)))

    def wait_before_retry(attempt_number: int) -> bool:
        delay = NTC_LLM_RETRY_BACKOFF_SECONDS * (2 ** (attempt_number - 1))
        if attempt_timeout_provider is not None:
            next_timeout = timeout_for_attempt(attempt_number + 1)
            if next_timeout is not None and next_timeout <= delay:
                logger.warning(
                    "Skipping model %s retry because remaining request budget is %ss",
                    model,
                    next_timeout,
                )
                return False
        logger.warning("Retrying model %s in %.1fs", model, delay)
        time.sleep(delay)
        return True

    for attempt in range(1, attempts + 1):
        effective_timeout = timeout_for_attempt(attempt)
        attempts_made = attempt
        read_timeout_label = "unlimited" if effective_timeout is None else f"{effective_timeout}s"
        logger.info(
            "LLM request model=%s attempt=%s/%s estimated_input_tokens=%s "
            "max_output_tokens=%s read_timeout=%s stream=%s",
            model,
            attempt,
            attempts,
            request_tokens,
            max_tokens,
            read_timeout_label,
            stream_response,
        )
        request_payload = dict(payload)
        request_headers = dict(headers)
        request_streaming = stream_response
        if request_streaming:
            request_payload["stream"] = True
            request_headers["Accept"] = "text/event-stream"
        try:
            resp = requests.post(
                NTC_API_URL,
                headers=request_headers,
                json=request_payload,
                timeout=(NTC_LLM_CONNECT_TIMEOUT_SECONDS, effective_timeout),
                stream=request_streaming,
            )
        except requests.exceptions.RequestException as exc:
            logger.error(
                "NTC AI Gateway request failed for model %s (attempt %s/%s): %s",
                model,
                attempt,
                attempts,
                exc,
            )
            is_read_timeout = _is_read_timeout_exception(exc)
            if is_read_timeout:
                last_error_kind = "model_timeout"
                if cooldown_on_read_timeout:
                    logger.warning(
                        "Model %s exceeded the read timeout; entering cooldown without duplicate retry",
                        model,
                    )
                    _MODEL_COOLDOWN_UNTIL[model] = time.monotonic() + NTC_LLM_MODEL_COOLDOWN_SECONDS
                else:
                    logger.warning(
                        "Primary model %s exceeded the read timeout; next pipeline stage may retry it",
                        model,
                    )
                return LLMCallResult(
                    timed_out=True,
                    error_kind=last_error_kind,
                    model=model,
                    attempts=attempts_made,
                )
            last_error_kind = "request_error"
            if attempt < attempts and wait_before_retry(attempt):
                continue
            _MODEL_COOLDOWN_UNTIL[model] = time.monotonic() + NTC_LLM_MODEL_COOLDOWN_SECONDS
            return LLMCallResult(
                error_kind=last_error_kind,
                model=model,
                attempts=attempts_made,
            )

        if resp.status_code >= 400 and request_streaming:
            error_text = _sanitize_gateway_error(resp.text) or resp.reason
            if _streaming_not_supported(resp.status_code, error_text):
                logger.warning(
                    "NTC AI Gateway did not accept streaming for model %s; retrying this attempt without stream",
                    model,
                )
                request_streaming = False
                effective_timeout = timeout_for_attempt(attempt)
                try:
                    resp.close()
                    resp = requests.post(
                        NTC_API_URL,
                        headers=headers,
                        json=payload,
                        timeout=(NTC_LLM_CONNECT_TIMEOUT_SECONDS, effective_timeout),
                    )
                except requests.exceptions.RequestException as exc:
                    logger.error(
                        "NTC AI Gateway request failed for model %s (attempt %s/%s): %s",
                        model,
                        attempt,
                        attempts,
                        exc,
                    )
                    is_read_timeout = _is_read_timeout_exception(exc)
                    if is_read_timeout:
                        last_error_kind = "model_timeout"
                        if cooldown_on_read_timeout:
                            logger.warning(
                                "Model %s exceeded the read timeout; entering cooldown without duplicate retry",
                                model,
                            )
                            _MODEL_COOLDOWN_UNTIL[model] = time.monotonic() + NTC_LLM_MODEL_COOLDOWN_SECONDS
                        else:
                            logger.warning(
                                "Primary model %s exceeded the read timeout; next pipeline stage may retry it",
                                model,
                            )
                        return LLMCallResult(
                            timed_out=True,
                            error_kind=last_error_kind,
                            model=model,
                            attempts=attempts_made,
                        )
                    last_error_kind = "request_error"
                    if attempt < attempts and wait_before_retry(attempt):
                        continue
                    _MODEL_COOLDOWN_UNTIL[model] = time.monotonic() + NTC_LLM_MODEL_COOLDOWN_SECONDS
                    return LLMCallResult(
                        error_kind=last_error_kind,
                        model=model,
                        attempts=attempts_made,
                    )

        if resp.status_code >= 400:
            error_text = _sanitize_gateway_error(resp.text) or resp.reason
            model_not_found = "model" in error_text.lower() and "not found" in error_text.lower()
            retriable = resp.status_code in retriable_statuses and not model_not_found
            logger.error(
                "NTC AI Gateway API error for model %s (%s, attempt %s/%s): %s",
                model,
                resp.status_code,
                attempt,
                attempts,
                error_text,
            )
            last_error_kind = "model_not_found" if model_not_found else "api_error"
            if retriable and attempt < attempts and wait_before_retry(attempt):
                continue
            if model_not_found or (retriable and cooldown_on_api_error):
                _MODEL_COOLDOWN_UNTIL[model] = time.monotonic() + NTC_LLM_MODEL_COOLDOWN_SECONDS
            return LLMCallResult(
                error_kind=last_error_kind,
                model=model,
                attempts=attempts_made,
            )

        if request_streaming:
            try:
                result, stream_timed_out = _read_streaming_response(resp, model, effective_timeout)
            except requests.exceptions.RequestException as exc:
                partial = ""
                logger.error(
                    "NTC AI Gateway stream failed for model %s (attempt %s/%s): %s",
                    model,
                    attempt,
                    attempts,
                    exc,
                )
                is_read_timeout = _is_read_timeout_exception(exc)
                if is_read_timeout:
                    last_error_kind = "model_timeout"
                    if cooldown_on_read_timeout:
                        logger.warning(
                            "Model %s exceeded the streaming read timeout; entering cooldown",
                            model,
                        )
                        _MODEL_COOLDOWN_UNTIL[model] = time.monotonic() + NTC_LLM_MODEL_COOLDOWN_SECONDS
                    else:
                        logger.warning(
                            "Primary model %s exceeded the streaming read timeout",
                            model,
                        )
                    return LLMCallResult(
                        content=partial,
                        timed_out=True,
                        error_kind=last_error_kind,
                        model=model,
                        attempts=attempts_made,
                    )
                last_error_kind = "stream_error"
                if attempt < attempts and wait_before_retry(attempt):
                    continue
                _MODEL_COOLDOWN_UNTIL[model] = time.monotonic() + NTC_LLM_MODEL_COOLDOWN_SECONDS
                return LLMCallResult(
                    error_kind=last_error_kind,
                    model=model,
                    attempts=attempts_made,
                )
            finally:
                resp.close()

            if result:
                _MODEL_COOLDOWN_UNTIL.pop(model, None)
                return LLMCallResult(
                    content=result,
                    timed_out=stream_timed_out,
                    error_kind="model_timeout" if stream_timed_out else None,
                    model=model,
                    attempts=attempts_made,
                )
            if stream_timed_out:
                return LLMCallResult(
                    timed_out=True,
                    error_kind="model_timeout",
                    model=model,
                    attempts=attempts_made,
                )
            logger.warning("LLM stream returned no content for model %s", model)
            return LLMCallResult(
                error_kind="empty_response",
                model=model,
                attempts=attempts_made,
            )

        try:
            response_data = resp.json()
            result = _extract_response_content(response_data, model, glm_text_wrapper)
            if result:
                _MODEL_COOLDOWN_UNTIL.pop(model, None)
            return LLMCallResult(
                content=result,
                error_kind=None if result else "empty_response",
                model=model,
                attempts=attempts_made,
            )
        except (AttributeError, KeyError, IndexError, TypeError, ValueError) as e:
            logger.error("NTC AI Gateway response parse error for model %s: %s", model, e)
            return LLMCallResult(
                error_kind="invalid_response",
                model=model,
                attempts=attempts_made,
            )

    return LLMCallResult(
        error_kind=last_error_kind or "unknown_error",
        model=model,
        attempts=attempts_made,
    )


def _call_llm_with_fallback(
    system_prompt: str,
    user_prompt: str,
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
    timeout: Optional[int] = 180,
    mongo_service=None,
    override_config: dict = None,
    primary_only: bool = False,
    cancel_check: Optional[Callable[[], None]] = None,
    return_diagnostics: bool = False,
    attempt_timeout_provider: Optional[Callable[[str, int, Optional[int]], Optional[int]]] = None,
    request_meta: Optional[dict] = None,
) -> str | LLMCallResult:
    """Try primary model, fallback to other models via NTC AI Gateway."""
    _run_cancel_check(cancel_check)
    config = _normalize_llm_config(override_config) if override_config else get_llm_config(mongo_service)
    effective_temperature = temperature if temperature is not None else config["temperature"]
    effective_max_tokens = max_tokens if max_tokens is not None else config["max_tokens"]
    
    # Try primary
    _run_cancel_check(cancel_check)
    logger.info(f"Attempting summary with primary model: {config['primary_model']}")
    primary_result = _coerce_llm_result(_call_ntc_gateway(
        system_prompt,
        user_prompt,
        effective_temperature,
        effective_max_tokens,
        timeout,
        model_name=config["primary_model"],
        cooldown_on_read_timeout=False,
        cooldown_on_api_error=False,
        attempt_timeout_provider=attempt_timeout_provider,
    ), model=config["primary_model"])
    
    if primary_result.content.strip():
        return primary_result if return_diagnostics else primary_result.content

    if primary_only:
        logger.warning("Primary model failed; adaptive summary recovery will split this chunk")
        return primary_result if return_diagnostics else ""
        
    logger.warning("Primary model failed, trying fallback models...")
    last_result = primary_result
    for fallback_model in config["fallback_models"]:
        _run_cancel_check(cancel_check)
        logger.info(f"Attempting summary with fallback model: {fallback_model}")
        # All configured models are served via NTC AI Gateway (OpenAI-compatible)
        result = _coerce_llm_result(_call_ntc_gateway(
            system_prompt,
            user_prompt,
            effective_temperature,
            effective_max_tokens,
            timeout,
            model_name=fallback_model,
            attempt_timeout_provider=attempt_timeout_provider,
        ), model=fallback_model)
        last_result = result
        if result.content.strip():
            logger.info(f"Successfully generated summary with fallback model {fallback_model}")
            return result if return_diagnostics else result.content
            
    logger.error("All models failed.")
    if primary_result.timed_out and not last_result.timed_out:
        last_result.timed_out = True
        last_result.error_kind = last_result.error_kind or primary_result.error_kind
    return last_result if return_diagnostics else ""

def _create_fallback_summary(transcription_text: str) -> str:
    """Create a basic fallback summary when all AI models fail."""
    try:
        lines = transcription_text.strip().split('\n')
        # Filter lines with actual content
        content_lines = [line.strip() for line in lines if line.strip() and len(line.strip()) > 10]
        
        if not content_lines:
            return ""
            
        fallback_summary = f"""สรุปการประชุม (สร้างโดยระบบ Fallback)

ข้อมูลการประชุม:
- ความยาวการประชุม: {len(transcription_text)} ตัวอักษร
- จำนวนประโยคที่มีเนื้อหา: {len(content_lines)} ประโยค

เนื้อหาสำคัญบางส่วน:
{chr(10).join(content_lines[:10])}

หมายเหตุ: นี่เป็นสรุปพื้นฐานที่สร้างโดยระบบเนื่องจากการสรุปด้วย AI ประสบปัญหา 
กรุณาตรวจสอบไฟล์ Transcription เพื่อดูรายละเอียดครบถ้วน"""

        return fallback_summary
    except Exception as e:
        logger.error(f"Error creating fallback summary: {e}")
        return "เกิดข้อผิดพลาดในการสรุปผล กรุณาตรวจสอบไฟล์ถอดเสียง (Transcription)"



# ============================================================
# Speaker Name Detection (from ST)
# ============================================================

def detect_speaker_names(
    transcript_with_speakers: str,
    speakers: list,
    mongo_service=None,
    cancel_check: Optional[Callable[[], None]] = None,
) -> dict:
    """
    Use the configured LLM to detect self-introductions in the transcript
    and map speaker labels to real names.
    Returns: { "คนพูด 1": { "name": "สมชาย", "position": "ผู้จัดการ" }, ... }
    """
    if not NTC_API_KEY:
        print("   ⚠️ No API key, skipping name detection")
        return {}

    transcript_excerpt = sample_text_windows(transcript_with_speakers, max_chars=12000, windows=3)
    speakers_list = ", ".join(speakers)

    system = """คุณคือ AI ที่วิเคราะห์บทสนทนาภาษาไทย อังกฤษ และจีน เพื่อหาการแนะนำตัวของผู้พูด

หน้าที่: อ่านตัวอย่างจากช่วงต้น ช่วงกลาง และช่วงท้าย แล้วหาว่าผู้พูดคนไหนแนะนำตัวเอง
หรือถูกเรียกชื่อ/แนะนำโดยคนอื่น ให้ยืนยันจากข้อความจริงเท่านั้นและห้ามเดาชื่อจากบริบท

ตัวอย่างการแนะนำตัว (ไทย):
- "สวัสดีครับ ผม สมชาย ใจดี ครับ" → name: "สมชาย ใจดี"
- "ดิฉัน สมหญิง รักดี ตำแหน่งผู้จัดการฝ่ายบุคคล" → name: "สมหญิง รักดี", position: "ผู้จัดการฝ่ายบุคคล"

ตัวอย่างการแนะนำตัว (อังกฤษ/ผสม):
- "Hi, I'm John Smith, the project manager" → name: "John Smith", position: "Project Manager"
- "สวัสดีครับ ผม David Lee ครับ เป็น CTO" → name: "David Lee", position: "CTO"

ตัวอย่างการแนะนำตัว (จีน/ผสม):
- "大家好，我是王明，负责市场部" → name: "王明 (หวัง หมิง)", position: "ผู้รับผิดชอบฝ่ายการตลาด"
- "สวัสดีครับ 我叫李华" → name: "李华 (หลี่ หวา)", position: ""

ตอบเป็น JSON เท่านั้น:
{
  "คนพูด 1": {"name": "ชื่อ นามสกุล", "position": "ตำแหน่ง"},
  "คนพูด 2": {"name": "ชื่อ นามสกุล", "position": ""}
}

กฎสำคัญ:
1. ชื่อต้องเว้นวรรคระหว่างชื่อกับนามสกุล
2. ตัดคำนำหน้าทั่วไปออก: นาย, นาง, นางสาว, คุณ (แต่เก็บ ดร., ศ., ผศ. ไว้)
3. position = ตำแหน่ง/บทบาท — แปลเป็นภาษาไทย ถ้าต้นฉบับเป็นภาษาจีนหรือภาษาอื่น
4. ชื่อภาษาจีนให้ใส่คำอ่านภาษาไทยในวงเล็บ เช่น 王明 (หวัง หมิง)
5. ถ้าพบตำแหน่งแต่ไม่พบชื่อ ให้ข้ามไป
6. ถ้าไม่พบการแนะนำตัวเลย ให้ตอบ {}
7. ถ้าชื่อเดียวกันถูกอ้างถึงต่างกัน ให้เลือกค่าที่มีหลักฐานชัดที่สุด ห้ามรวมคนละคนเข้าด้วยกัน
8. ตอบ JSON เท่านั้น ไม่ต้องมีคำอธิบาย"""

    user = f"ผู้พูดที่ตรวจพบ: {speakers_list}\n\nTranscript:\n{transcript_excerpt}"

    _run_cancel_check(cancel_check)
    content = _call_llm_with_fallback(
        system,
        user,
        temperature=0.1,
        max_tokens=700,
        timeout=150,
        mongo_service=mongo_service,
        cancel_check=cancel_check,
    )
    if not content:
        return {}

    try:
        # Clean markdown code blocks
        if content.startswith("```"):
            content = content.split("```")[1]
            if content.startswith("json"):
                content = content[4:]
            content = content.strip()

        detected = json.loads(content)

        # Validate — only keep entries for known speakers
        validated = {}
        for speaker in speakers:
            if speaker in detected and isinstance(detected[speaker], dict):
                name = detected[speaker].get("name", "").strip()
                position = detected[speaker].get("position", "").strip()
                if name:
                    validated[speaker] = {"name": name, "position": position}
        return validated
    except Exception as e:
        print(f"   ⚠️ Name detection failed: {e}")
        return {}


# ============================================================
# Meeting Auto-Classification (LLM-assisted)
# ============================================================

CLASSIFICATION_SYSTEM = """คุณคือผู้เชี่ยวชาญในการวิเคราะห์ประเภทการประชุม จากเนื้อหาการประชุมที่ได้รับ กรุณาจำแนกประเภทการประชุม:

MEETING_TYPES:
- shareholder_meeting: ประชุมผู้ถือหุ้น (มีวาระ การลงมติ เงินปันผล)
- board_meeting: ประชุมคณะกรรมการ (การตัดสินใจระดับบริษัท)
- planning_meeting: ประชุมวางแผน (กลยุทธ์ แผนการทำงาน)
- progress_update: รายงานความคืบหน้า (สถานะงาน ปัญหา)
- strategy_meeting: ประชุมเชิงกลยุทธ์ (ทิศทางธุรกิจ)
- incident_review: แก้ไขปัญหา (วิเคราะห์ปัญหา หาแนวทาง)
- client_meeting: ประชุมลูกค้า (นำเสนองาน ตอบข้อซักถาม)
- workshop: เชิงปฏิบัติการ (ฝึกอบรม แลกเปลี่ยนความรู้)
- executive_meeting: ผู้บริหารระดับสูง (การตัดสินใจสำคัญ)
- team_meeting: ทีมงาน (ประสานงาน มอบหมายงาน)
- general_meeting: ทั่วไป

ตอบด้วย JSON:
{
  "meeting_type": "ประเภท",
  "confidence": 0.95,
  "key_indicators": ["คำสำคัญ"],
  "participants_level": "executive/management/team",
  "meeting_tone": "formal/semi-formal/informal"
}

เลือกประเภทหลักเพียงประเภทเดียวจากรายการข้างต้น ใช้หลักฐานที่ปรากฏในตัวอย่างทุกช่วง
ห้ามเดาจากชื่อบุคคลหรือข้อความเปิดประชุมเพียงอย่างเดียว และตอบ JSON เท่านั้น"""

# Keyword fallback mapping
KEYWORD_PATTERNS = {
    "shareholder_meeting": ["ผู้ถือหุ้น", "วาระ", "ลงมติ", "เงินปันผล", "กรรมการ", "องค์ประชุม"],
    "board_meeting": ["คณะกรรมการ", "นโยบาย", "อนุมัติ", "ผู้บริหาร"],
    "planning_meeting": ["แผน", "วางแผน", "กลยุทธ์", "เป้าหมาย", "ไทม์ไลน์"],
    "progress_update": ["ความคืบหน้า", "สถานะ", "รายงาน", "ปัญหา", "อุปสรรค"],
    "client_meeting": ["ลูกค้า", "นำเสนอ", "ข้อเสนอ", "ราคา", "สัญญา"],
    "workshop": ["ฝึกอบรม", "workshop", "เรียนรู้", "ทักษะ", "ความรู้"],
}

# Meeting type ID (int) ↔ classification key (str) mapping
_MEETING_ID_TO_KEY = {
    1: "shareholder_meeting", 2: "board_meeting", 3: "planning_meeting",
    4: "progress_update", 5: "strategy_meeting", 6: "incident_review",
    7: "client_meeting", 8: "workshop", 9: "executive_meeting",
    10: "team_meeting", 11: "general_meeting",
}
_MEETING_KEY_TO_ID = {v: k for k, v in _MEETING_ID_TO_KEY.items()}


def _fallback_classification(transcription: str) -> Dict:
    """Keyword-based classification when the LLM call fails."""
    text_lower = transcription.lower()
    max_score = 0
    detected_type = "general_meeting"

    for mtype, keywords in KEYWORD_PATTERNS.items():
        score = sum(1 for kw in keywords if kw in text_lower)
        if score > max_score:
            max_score = score
            detected_type = mtype

    confidence = min(0.8, max_score / 3.0)
    return {
        "meeting_type": detected_type,
        "confidence": confidence,
        "key_indicators": [kw for kw in KEYWORD_PATTERNS.get(detected_type, []) if kw in text_lower],
        "participants_level": "team",
        "meeting_tone": "semi-formal",
    }


def classify_meeting_type(
    transcription: str,
    mongo_service=None,
    cancel_check: Optional[Callable[[], None]] = None,
) -> Dict:
    """Classify meeting type using the configured LLM with keyword fallback."""
    _run_cancel_check(cancel_check)
    sample = sample_text_windows(transcription, max_chars=12000, windows=3)
    user_msg = f"""วิเคราะห์และจำแนกประเภทการประชุมจากตัวอย่างช่วงต้น กลาง และท้ายต่อไปนี้
พิจารณาภาพรวมทุกช่วง ไม่ให้น้ำหนักเฉพาะช่วงเปิดประชุม และห้ามอนุมานสิ่งที่ไม่มีในตัวอย่าง

{sample}"""

    content = _call_llm_with_fallback(
        CLASSIFICATION_SYSTEM,
        user_msg,
        temperature=0.1,
        max_tokens=500,
        timeout=90,
        mongo_service=mongo_service,
        cancel_check=cancel_check,
    )
    if not content:
        return _fallback_classification(transcription)

    try:
        json_start = content.find('{')
        json_end = content.rfind('}') + 1
        if json_start != -1 and json_end > json_start:
            return json.loads(content[json_start:json_end])
        raise ValueError("No JSON found")
    except Exception:
        return _fallback_classification(transcription)


def resolve_meeting_style(
    transcription: str,
    meeting_type_id: int = 0,
    mongo_service=None,
    cancel_check: Optional[Callable[[], None]] = None,
) -> tuple[int, Dict, str]:
    """Resolve output style/template separately from agenda segmentation."""
    _run_cancel_check(cancel_check)
    if meeting_type_id == 0:
        classification = classify_meeting_type(
            transcription,
            mongo_service=mongo_service,
            cancel_check=cancel_check,
        )
        detected_id = _MEETING_KEY_TO_ID.get(
            classification.get("meeting_type", "general_meeting"), 11
        )
        logger.info(
            "Auto-classified meeting style as: %s -> ID %s",
            classification.get("meeting_type"),
            detected_id,
        )
        return detected_id, classification, "auto"

    return (
        meeting_type_id,
        {
            "meeting_type": _MEETING_ID_TO_KEY.get(meeting_type_id, "general_meeting"),
            "confidence": 1.0,
            "key_indicators": [],
        },
        "manual",
    )


# ============================================================
# Text Chunking (from V3, smart boundary)
# ============================================================

def split_text_into_chunks(text: str, max_tokens: int = 30000) -> list[str]:
    """
    Compatibility chunker for V1 fallback, using the same Thai-aware token budget as V2.
    """
    pseudo_segments = segments_from_text(text)
    structured_chunks = chunk_segments(
        pseudo_segments,
        max_tokens=max_tokens,
        overlap_tokens=0,
    )
    chunks = [chunk["text"] for chunk in structured_chunks]
    logger.info("Split text into %s token-budgeted chunks", len(chunks))
    return chunks


# ============================================================
# Chunk-level Summary
# ============================================================

def _summarize_chunk(chunk: str, chunk_idx: int, total_chunks: int, mongo_service=None) -> str:
    """Summarize a single chunk of transcript."""
    system = f"""คุณคือผู้เชี่ยวชาญในการสรุปการประชุมอย่างละเอียด
รักษาลำดับเหตุการณ์และข้อมูลสำคัญทั้งหมดของช่วงนี้
{GROUNDING_RULES}"""

    user = f"""กรุณาสรุปส่วนการประชุมนี้อย่างละเอียด โดยรักษาข้อมูลสำคัญทั้งหมดไว้:

ส่วนที่ {chunk_idx + 1} จากทั้งหมด {total_chunks} ส่วน

{chunk}

โปรดสรุปให้ครอบคลุม:
- ประเด็นหลักและย่อยที่กล่าวถึงในส่วนนี้
- ตัวเลข วันที่ และข้อมูลเฉพาะเจาะจง
- ชื่อบุคคล ตำแหน่ง และผู้ที่มีส่วนเกี่ยวข้อง
- การตัดสินใจหรือข้อสรุปในส่วนนี้
- การมอบหมายงานหรือ action items

หมายเหตุ: นี่คือเพียงส่วนหนึ่งของการประชุม กรุณาสรุปเฉพาะเนื้อหาในส่วนนี้อย่างครบถ้วน"""

    return _call_llm_with_fallback(
        system,
        user,
        temperature=0.2,
        max_tokens=4000,
        timeout=180,
        mongo_service=mongo_service,
    )


def _consolidate_summaries(
    chunk_summaries: list[str],
    classification: Dict,
    meeting_type_id: int,
    custom_prompt: str = "",
    mongo_service=None,
) -> str:
    """Consolidate multiple chunk summaries into one final summary."""
    meeting_type = classification.get("meeting_type", "general_meeting")
    confidence = classification.get("confidence", 0.5)
    key_indicators = classification.get("key_indicators", [])

    # Map classification key to Thai name
    type_id = _MEETING_KEY_TO_ID.get(meeting_type, meeting_type_id or 11)
    info = MEETING_TYPES.get(type_id, MEETING_TYPES[11])
    focus_prompt = get_meeting_focus_prompt(type_id)

    combined = "\n\n---\n\n".join(
        f"=== สรุปส่วนที่ {i+1} ===\n{s}" for i, s in enumerate(chunk_summaries)
    )

    system = f"""คุณคือผู้เชี่ยวชาญวิเคราะห์และสรุปการประชุม

**ประเภทการประชุม:** {info['thai']} ({info['name']})
**โครงสร้างการสรุป:** {info['structure']}

{focus_prompt}

คุณกำลังสร้างสรุปขั้นสุดท้ายจากการประชุมยาว กรุณาให้ความสำคัญกับความครบถ้วนและการไม่สูญหายของข้อมูลสำคัญ
{GROUNDING_RULES}"""

    if custom_prompt:
        system += f"\n\n**คำสั่งเพิ่มเติมจากผู้ใช้:**\n{custom_prompt}"

    user = f"""กรุณาสร้างสรุปการประชุมฉบับสมบูรณ์จากสรุปส่วนต่างๆ ต่อไปนี้:

ข้อมูลบริบท:
- ประเภท: {info['thai']} (ความเชื่อมั่น: {confidence:.0%})
- คำสำคัญ: {', '.join(key_indicators)}

{combined}

กรุณาสร้างสรุปที่:
1. เริ่มต้นด้วยหัวข้อ "สรุป{info['thai']}"
2. ครอบคลุมเนื้อหาจากทุกส่วน ไม่ให้สูญหาย
3. จัดเรียงตามลำดับเหมาะสม ไม่ซ้ำซ้อน
4. ยาวและละเอียด ประมาณ 3-5 หน้า A4
5. ใช้ bullet points และหัวข้อย่อย"""

    result = _call_llm_with_fallback(
        system, user, 
        temperature=0.3, 
        max_tokens=6000,
        timeout=180,
        mongo_service=mongo_service
    )
    if not result:
        # Fallback: join summaries
        header = f"สรุป{info['thai']}\n{'=' * 50}\n\n"
        return header + "\n\n".join(chunk_summaries)
    return result


# ============================================================
# Main Summarization Entry Points
# ============================================================

def get_meeting_type_prompt(meeting_type_id: int) -> str:
    """Get the prompt instruction for a specific meeting type."""
    if meeting_type_id == 0:
        types_table = "\n".join([
            f"| {info['name']} | {info['structure']} |"
            for num, info in MEETING_TYPES.items() if num > 0
        ])
        return f"""**ขั้นตอน:**
1. วิเคราะห์ข้อมูลผู้พูดเพื่อระบุบทบาท (ประธาน/ผู้นำเสนอ/ผู้เข้าร่วม)
2. วิเคราะห์เนื้อหาเพื่อระบุประเภทการประชุม
3. สรุปตามโครงสร้างที่เหมาะสม

**ประเภทการประชุม:**
| ประเภท | โครงสร้าง |
|--------|----------|
{types_table}"""
    else:
        info = MEETING_TYPES.get(meeting_type_id, MEETING_TYPES[11])
        focus = get_meeting_focus_prompt(meeting_type_id)
        return f"""**ประเภทการประชุม:** {info['thai']} ({info['name']})
**โครงสร้างการสรุป:** {info['structure']}

{focus}

สรุปเนื้อหาตามโครงสร้างข้างต้น โดยเน้นความละเอียดในประเด็นหัวใจหลัก"""


def summarize_with_diarization(
    transcript_with_speakers: str,
    speaker_summary: dict,
    meeting_type_id: int = 0,
    language: str = "Thai",
    custom_prompt: str = "",
    mongo_service=None,
    segments: Optional[list[dict]] = None,
    return_metadata: bool = False,
    resolved_meeting_style: Optional[tuple[int, Dict, str]] = None,
    cancel_check: Optional[Callable[[], None]] = None,
):
    """
    Summarize transcription with speaker diarization data.
    Uses the incremental chunk-and-reduce pipeline for every transcript length.
    """
    _run_cancel_check(cancel_check)
    if not NTC_API_KEY:
        source_segments = segments or segments_from_text(transcript_with_speakers)
        result = append_user_warning(transcript_fallback_text(source_segments))
        metadata = {
            "version": "incremental",
            "degraded": True,
            "error": "NTC_API_KEY is not configured",
            "user_warning": SUMMARY_USER_WARNING,
        }
        return (result, metadata) if return_metadata else result

    if resolved_meeting_style:
        effective_type_id, classification, meeting_style_source = resolved_meeting_style
    else:
        effective_type_id, classification, meeting_style_source = resolve_meeting_style(
            transcript_with_speakers,
            meeting_type_id,
            mongo_service=mongo_service,
            cancel_check=cancel_check,
        )

    if custom_prompt:
        logger.info(f"Custom prompt provided ({len(custom_prompt)} chars)")

    template_data = _get_template_for_meeting(effective_type_id, mongo_service=mongo_service)

    def llm_call(system_prompt, user_prompt, **kwargs):
        _run_cancel_check(cancel_check)
        return _call_llm_with_fallback(
            system_prompt,
            user_prompt,
            mongo_service=mongo_service,
            cancel_check=cancel_check,
            **kwargs,
        )

    logger.info(
        "Using incremental summary pipeline for %s estimated input tokens",
        estimate_tokens(transcript_with_speakers),
    )
    try:
        _run_cancel_check(cancel_check)
        result, metadata = summarize_transcript_incrementally(
            transcript=transcript_with_speakers,
            segments=segments,
            meeting_type_id=effective_type_id,
            template_prompt=template_data["system_prompt"],
            custom_prompt=custom_prompt,
            llm_call=llm_call,
        )
    except Exception as exc:
        if isinstance(exc, JobCancelled):
            raise
        logger.exception("Incremental summary pipeline failed; preserving transcript as degraded output")
        source_segments = segments or segments_from_text(transcript_with_speakers)
        result = append_user_warning(transcript_fallback_text(source_segments))
        metadata = {
            "version": "incremental",
            "degraded": True,
            "error": exc.__class__.__name__,
            "user_warning": SUMMARY_USER_WARNING,
        }

    if not result:
        source_segments = segments or segments_from_text(transcript_with_speakers)
        result = append_user_warning(transcript_fallback_text(source_segments))
        metadata = {
            **metadata,
            "version": "incremental",
            "degraded": True,
            "user_warning": SUMMARY_USER_WARNING,
        }
    metadata.update({
        "meeting_style_id": effective_type_id,
        "meeting_style_source": meeting_style_source,
        "meeting_style_key": classification.get("meeting_type", "general_meeting"),
    })
    return (result, metadata) if return_metadata else result


def _summarize_standard(
    transcript_with_speakers: str,
    speaker_summary: dict,
    meeting_type_id: int,
    classification: Dict,
    custom_prompt: str = "",
    mongo_service=None,
) -> str:
    """Standard single-call summary for shorter transcripts."""
    speakers_time = speaker_summary.get('speaking_time', {})
    speakers_words = speaker_summary.get('word_count', {})
    total_time = sum(speakers_time.values()) if speakers_time else 1

    speaker_info_lines = []
    for speaker, time_sec in sorted(speakers_time.items(), key=lambda x: -x[1]):
        pct = (time_sec / total_time * 100) if total_time > 0 else 0
        words = speakers_words.get(speaker, 0)
        mins = int(time_sec // 60)
        secs = int(time_sec % 60)
        speaker_info_lines.append(f"- {speaker}: {mins}:{secs:02d} ({pct:.1f}%), {words} คำ")

    speaker_info = "\n".join(speaker_info_lines)
    num_speakers = len(speakers_time)
    effective_type_id = meeting_type_id or _MEETING_KEY_TO_ID.get(
        classification.get("meeting_type", "general_meeting"), 11
    )
    template_data = _get_template_for_meeting(effective_type_id, mongo_service=mongo_service)
    system = f"{template_data['system_prompt']}\n\n{GROUNDING_RULES}"
    
    # Replace placeholder if present, otherwise append
    if "{custom_prompt}" in system:
        if custom_prompt:
            system = system.replace("{custom_prompt}", f"**คำสั่งเพิ่มเติมจากผู้ใช้:**\n{custom_prompt}")
        else:
            system = system.replace("{custom_prompt}", "")
    elif custom_prompt:
        system += f"\n\n**คำสั่งเพิ่มเติมจากผู้ใช้:**\n{custom_prompt}"
        
    system = system.replace("{num_speakers}", str(num_speakers))

    user = f"""**ข้อมูลผู้พูด:**
{speaker_info}

**เนื้อหาการประชุม:**
{transcript_with_speakers}"""

    result = _call_llm_with_fallback(
        system, 
        user, 
        temperature=template_data.get("temperature", 0.4), 
        max_tokens=template_data.get("max_tokens", 4000), 
        timeout=180,
        mongo_service=mongo_service
    )
    if not result:
        logger.warning("All models failed, using basic fallback summary.")
        return _create_fallback_summary(transcript_with_speakers)
    return result


def _summarize_hierarchical(
    transcript_with_speakers: str,
    speaker_summary: dict,
    meeting_type_id: int,
    classification: Dict,
    custom_prompt: str = "",
    mongo_service=None,
) -> str:
    """Hierarchical multi-stage summary for long transcripts."""
    logger.info("Starting hierarchical summarization")

    # Step 1: Split into chunks
    chunks = split_text_into_chunks(transcript_with_speakers, max_tokens=12000)
    logger.info(f"Split into {len(chunks)} chunks")

    # Step 2: Summarize each chunk
    chunk_summaries = []
    for i, chunk in enumerate(chunks):
        logger.info(f"Summarizing chunk {i+1}/{len(chunks)} ({len(chunk)} chars)")
        summary = _summarize_chunk(chunk, i, len(chunks), mongo_service=mongo_service)
        if summary:
            chunk_summaries.append(summary)
            logger.info(f"Chunk {i+1} done ({len(summary)} chars)")
        else:
            logger.warning(f"Chunk {i+1} returned empty")

    if not chunk_summaries:
        return "Error: No chunk summaries generated"

    # Step 3: Consolidate into final summary
    logger.info(f"Consolidating {len(chunk_summaries)} chunk summaries")
    final = _consolidate_summaries(
        chunk_summaries,
        classification,
        meeting_type_id,
        custom_prompt,
        mongo_service=mongo_service,
    )

    logger.info(f"Hierarchical summary complete ({len(final)} chars)")
    return final


# ============================================================
# Agenda-aware Summarization (Feature 19)
# ============================================================

def _summarize_single_agenda(
    agenda_transcript: str,
    agenda_title: str,
    agenda_number: int,
    total_agendas: int,
    meeting_type_id: int,
    custom_prompt: str = "",
    mongo_service=None,
) -> dict:
    """
    Summarize a single agenda section.

    Returns:
        {"summary": str, "decisions": list[str], "action_items": list[str]}
    """
    template_data = _get_template_for_meeting(meeting_type_id, mongo_service=mongo_service)
    thai_name = template_data.get('thai_name', 'ทั่วไป')

    system = f"""คุณคือผู้เชี่ยวชาญในการสรุปการประชุม กรุณาสรุปเนื้อหาเฉพาะวาระนี้อย่างละเอียด

**ประเภทการประชุม:** {thai_name}
**วาระที่กำลังสรุป:** {agenda_title} (วาระที่ {agenda_number}/{total_agendas})

ตอบเป็น JSON เท่านั้น:
```json
{{
  "summary": "สรุปเนื้อหาวาระนี้อย่างละเอียด ใช้ bullet points",
  "decisions": ["มติหรือข้อตกลง (ถ้ามี)"],
  "action_items": ["การมอบหมายงาน: [ผู้รับมอบหมาย] — [งาน] (ถ้ามี)"]
}}
```

**กฎ:**
- ใช้เฉพาะข้อเท็จจริงในเนื้อหาวาระนี้ และรักษาลำดับเหตุการณ์
- สรุปเป็นภาษาไทยเสมอ ไม่ว่า transcript จะเป็นภาษาอะไร (ไทย/อังกฤษ/จีน/ผสม)
- คงคำศัพท์เฉพาะทาง ชื่อเฉพาะ และคำย่อภาษาอังกฤษไว้ตามเดิม
- ถ้ามีการพูดภาษาจีนหรือภาษาอื่น ให้แปลเป็นภาษาไทยแล้วใส่คำต้นฉบับในวงเล็บ
- ใช้ bullet points ใน summary
- ระบุชื่อผู้พูดเมื่อกล่าวถึงการสั่งงาน/ความเห็น
- ถ้าไม่มีมติหรือ action items ให้ตอบ list ว่าง []
- แยกข้อเสนอออกจากมติที่ยืนยันแล้ว และเก็บเรื่องที่ยังไม่มีข้อสรุป
- ตอบ JSON เท่านั้น ไม่ต้องมีข้อความอื่น"""

    if custom_prompt:
        system += f"\n\n**คำสั่งเพิ่มเติมจากผู้ใช้:**\n{custom_prompt}"

    user = f"""**วาระ:** {agenda_title}

**เนื้อหา:**
{agenda_transcript}"""

    content = _call_llm_with_fallback(
        system, user, 
        temperature=template_data.get("temperature", 0.4) if template_data else 0.2, 
        max_tokens=3000, 
        timeout=180,
        mongo_service=mongo_service
    )

    if not content:
        return {"summary": "", "decisions": [], "action_items": []}

    try:
        # Strip markdown code fences
        cleaned = content.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.split("```")[1]
            if cleaned.startswith("json"):
                cleaned = cleaned[4:]
            cleaned = cleaned.strip()

        result = json.loads(cleaned)
        return {
            "summary": result.get("summary", ""),
            "decisions": result.get("decisions", []) if isinstance(result.get("decisions"), list) else [],
            "action_items": result.get("action_items", []) if isinstance(result.get("action_items"), list) else [],
        }
    except (json.JSONDecodeError, TypeError) as exc:
        logger.warning(f"Failed to parse agenda summary JSON, using raw text: {exc}")
        return {"summary": content, "decisions": [], "action_items": []}


def _generate_executive_summary(
    agenda_summaries: list[dict],
    meeting_type_id: int,
    custom_prompt: str = "",
    mongo_service=None,
) -> str:
    """
    Generate an executive summary from per-agenda summaries.

    Args:
        agenda_summaries: List of {"agenda_number", "title", "summary", "decisions", "action_items"}
    """
    info = MEETING_TYPES.get(meeting_type_id, MEETING_TYPES.get(11, MEETING_TYPES[0]))
    template_data = _get_template_for_meeting(meeting_type_id, mongo_service=mongo_service)
    thai_name = template_data.get("thai_name") or info.get("thai", "ทั่วไป")

    # Build combined input
    combined_parts: list[str] = []
    for agenda in agenda_summaries:
        part = f"=== วาระที่ {agenda['agenda_number']}: {agenda['title']} ===\n"
        part += agenda.get("summary", "")
        if agenda.get("decisions"):
            part += "\nมติ: " + "; ".join(agenda["decisions"])
        if agenda.get("action_items"):
            part += "\nงานมอบหมาย: " + "; ".join(agenda["action_items"])
        combined_parts.append(part)

    combined = "\n\n".join(combined_parts)

    system = f"""คุณคือผู้เชี่ยวชาญในการสรุปการประชุม
กรุณาสร้าง **สรุปภาพรวมการประชุม (Executive Summary)** จากสรุปแต่ละวาระด้านล่าง

**ประเภทการประชุม:** {thai_name}

**รูปแบบ:**
1. เริ่มด้วย "สรุปภาพรวมการประชุม"
2. สรุปใจความสำคัญจากทุกวาระอย่างกระชับ
3. รวบรวมมติที่ประชุมทั้งหมด
4. รวบรวมงานมอบหมายทั้งหมด
5. ใช้ bullet points
6. รักษาลำดับวาระ ตัวเลข มติ งานมอบหมาย ความเห็นต่าง และเรื่องที่ยังไม่จบ
7. {GROUNDING_RULES}"""

    if custom_prompt:
        system += f"\n\n**คำสั่งเพิ่มเติมจากผู้ใช้:**\n{custom_prompt}"

    user = f"""จำนวนวาระทั้งหมด: {len(agenda_summaries)} วาระ

{combined}

กรุณาสร้างสรุปภาพรวม"""

    result = _call_llm_with_fallback(
        system, user, 
        temperature=template_data.get("temperature", 0.2),
        max_tokens=template_data.get("max_tokens", 4000), 
        timeout=180,
        mongo_service=mongo_service
    )
    if not result:
        # Fallback: combine agenda summaries as plain text
        header = f"สรุปภาพรวม{thai_name}\n{'=' * 50}\n\n"
        return header + combined

    return result


def _summarize_with_agendas_incrementally(
    segments: list[dict],
    agendas: list[dict],
    meeting_type_id: int,
    custom_prompt: str,
    mongo_service=None,
    resolved_meeting_style: Optional[tuple[int, Dict, str]] = None,
    cancel_check: Optional[Callable[[], None]] = None,
) -> tuple[str, list[dict], dict]:
    _run_cancel_check(cancel_check)
    transcript = "\n".join(
        f"[{seg.get('speaker', '?')}]: {str(seg.get('text') or '').strip()}"
        for seg in segments
        if str(seg.get("text") or "").strip()
    )
    input_tokens = estimate_tokens(transcript)
    overall_chunks = chunk_segments(segments)
    token_check = build_token_check(input_tokens, len(overall_chunks))
    logger.info(
        "WhisperX transcript token check (agenda flow): estimated_tokens=%s "
        "max_tokens_per_chunk=%s exceeds_max=%s chunks=%s",
        token_check["estimated_tokens"],
        token_check["max_tokens_per_chunk"],
        token_check["exceeds_max_tokens"],
        len(overall_chunks),
    )
    if resolved_meeting_style:
        effective_type_id, classification, meeting_style_source = resolved_meeting_style
    else:
        effective_type_id, classification, meeting_style_source = resolve_meeting_style(
            transcript,
            meeting_type_id,
            mongo_service=mongo_service,
            cancel_check=cancel_check,
        )

    template_data = _get_template_for_meeting(effective_type_id, mongo_service=mongo_service)

    def llm_call(system_prompt, user_prompt, **kwargs):
        _run_cancel_check(cancel_check)
        return _call_llm_with_fallback(
            system_prompt,
            user_prompt,
            mongo_service=mongo_service,
            cancel_check=cancel_check,
            **kwargs,
        )

    enriched: list[dict] = []
    records: list[dict] = []
    agenda_metadata: list[dict] = []
    total = len(agendas)
    for agenda in agendas:
        _run_cancel_check(cancel_check)
        start_idx = agenda["start_segment_idx"]
        end_idx = agenda["end_segment_idx"]
        agenda_segments = [
            {**segment, "_source_index": start_idx + offset}
            for offset, segment in enumerate(segments[start_idx:end_idx + 1])
        ]
        result, record, metadata = summarize_agenda_segments(
            segments=agenda_segments,
            agenda_title=agenda["title"],
            agenda_number=agenda["agenda_number"],
            total_agendas=total,
            custom_prompt=custom_prompt,
            llm_call=llm_call,
            template_prompt=template_data["system_prompt"],
        )
        _run_cancel_check(cancel_check)
        enriched.append({
            **agenda,
            "summary": result["summary"],
            "decisions": result["decisions"],
            "action_items": result["action_items"],
        })
        records.append(record)
        agenda_metadata.append(metadata)

    if records and all(record.get("failed_chunks") for record in records):
        _run_cancel_check(cancel_check)
        metadata = {
            "version": "incremental-agenda",
            "meeting_style_id": effective_type_id,
            "meeting_style_source": meeting_style_source,
            "meeting_style_key": classification.get("meeting_type", "general_meeting"),
            "agenda_count": len(records),
            "token_check": token_check,
            "coverage_complete": False,
            "covered_segments": 0,
            "total_segments": sum(
                1 for segment in segments if str(segment.get("text") or "").strip()
            ),
            "failed_chunks": sorted({
                chunk_number
                for record in records
                for chunk_number in record.get("failed_chunks", [])
            }),
            "extraction_complete": False,
            "degraded": True,
            "user_warning": SUMMARY_GEMMA_EMPTY_WARNING,
            "fallback_strategy": "transcript_fallback_no_gemma_chunks_completed",
            "agendas": agenda_metadata,
            "recovery_attempted": False,
            "recovery_succeeded": False,
            "recovery_skipped": "fast_summary_no_extra_llm_call",
        }
        fallback = transcript_fallback_text(segments)
        return append_user_warning(fallback, SUMMARY_GEMMA_EMPTY_WARNING), enriched, metadata

    _run_cancel_check(cancel_check)
    executive_summary, metadata = summarize_agenda_collection(
        records=records,
        meeting_type_id=effective_type_id,
        template_prompt=template_data["system_prompt"],
        custom_prompt=custom_prompt,
        llm_call=llm_call,
        input_tokens=input_tokens,
    )
    expected_ids = {
        index for index, segment in enumerate(segments)
        if str(segment.get("text") or "").strip()
    }
    covered_ids = {
        source_id
        for record in records
        for source_id in record.get("coverage", [])
    }
    coverage_degraded = not expected_ids.issubset(covered_ids)
    pipeline_degraded = bool(metadata.get("degraded"))
    metadata.update({
        "meeting_style_id": effective_type_id,
        "meeting_style_source": meeting_style_source,
        "meeting_style_key": classification.get("meeting_type", "general_meeting"),
        "coverage_complete": expected_ids.issubset(covered_ids),
        "covered_segments": len(expected_ids & covered_ids),
        "total_segments": len(expected_ids),
        "token_check": token_check,
        "agendas": agenda_metadata,
        "degraded": coverage_degraded or pipeline_degraded,
    })
    if metadata["degraded"]:
        metadata["user_warning"] = metadata.get("user_warning") or SUMMARY_GEMMA_PARTIAL_WARNING
        metadata["fallback_strategy"] = metadata.get("fallback_strategy") or "gemma_partial_summary"
        executive_summary = append_user_warning(executive_summary, metadata["user_warning"])
    return executive_summary, enriched, metadata


def summarize_with_agendas(
    segments: list[dict],
    agendas: list[dict],
    meeting_type_id: int = 0,
    custom_prompt: str = "",
    mongo_service=None,
    return_metadata: bool = False,
    resolved_meeting_style: Optional[tuple[int, Dict, str]] = None,
    cancel_check: Optional[Callable[[], None]] = None,
):
    """
    Summarize meeting with agenda-aware approach.

    Produces per-agenda summaries + an executive summary.

    Args:
        segments: Transcript segments (with 'text', 'speaker', 'start', 'end')
        agendas: List of agenda dicts from detect_agendas()
        meeting_type_id: Meeting type (0=auto, 1-11=specific)
        custom_prompt: Optional user instruction

    Returns:
        (executive_summary: str, enriched_agendas: list[dict])
        Each enriched agenda has added: "summary", "decisions", "action_items"
    """
    try:
        _run_cancel_check(cancel_check)
        result = _summarize_with_agendas_incrementally(
            segments,
            agendas,
            meeting_type_id,
            custom_prompt,
            mongo_service=mongo_service,
            resolved_meeting_style=resolved_meeting_style,
            cancel_check=cancel_check,
        )
    except Exception as exc:
        if isinstance(exc, JobCancelled):
            raise
        logger.exception("Incremental agenda summary failed; preserving transcript as degraded output")
        result = (
            append_user_warning(transcript_fallback_text(segments)),
            [{**agenda, "summary": "", "decisions": [], "action_items": []} for agenda in agendas],
            {
                "version": "incremental-agenda",
                "agenda_count": len(agendas),
                "degraded": True,
                "error": exc.__class__.__name__,
                "user_warning": SUMMARY_USER_WARNING,
            },
        )
    if return_metadata:
        return result
    return result[0], result[1]
