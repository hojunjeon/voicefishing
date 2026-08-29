from __future__ import annotations

import ipaddress
import os
import time
import uuid
from collections.abc import Mapping
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel

try:
    from .event_log import bind_event_context, get_log_path, log_event
    from .runtime import BaitbotRuntime, ProviderError, ScenarioRiskError, ScenarioStateError
except ImportError:  # Vercel imports the configured entrypoint as a top-level module.
    from event_log import bind_event_context, get_log_path, log_event
    from runtime import BaitbotRuntime, ProviderError, ScenarioRiskError, ScenarioStateError


class ChatRequest(BaseModel):
    message: str
    model: str | None = None
    reasoning: str | None = None
    session_snapshot: dict[str, Any]


class Scenario4CallerRequest(BaseModel):
    scenario_id: Any = None
    mode: Any
    conversation: Any = None
    message: Any = None
    model: str | None = None
    reasoning: str | None = None


class Scenario4HandoffRequest(BaseModel):
    scenario_id: Any = None
    mode: Any
    conversation: Any
    baitbot_turn: Any
    session_snapshot: Any = None
    model: str | None = None
    reasoning: str | None = None


_AUTH_STATUS_VALUES = frozenset(
    {
        "authenticated",
        "ready",
        "unauthenticated",
        "logged_out",
        "pending",
        "starting",
        "unavailable",
        "error",
        "unsupported",
    }
)
_AUTH_FAILURE_DETAIL = "ChatGPT OAuth is unavailable"
_AUTH_LOCAL_DETAIL = "ChatGPT OAuth login is available only from the local host"
_AUTH_CONFLICT_CODES = frozenset({"codex_already_authenticated", "codex_login_in_progress", "codex_api_key_mode"})
_AUTH_PROVIDERS = frozenset({"codex", "codex_cli", "codex_oauth", "injected"})
_AUTH_METHODS = frozenset({"chatgpt_oauth", "synthetic"})


def _safe_auth_status(value: Any) -> dict[str, Any]:
    """Expose only non-secret OAuth state returned by the runtime."""

    source = value if isinstance(value, Mapping) else {}
    authenticated = source.get("authenticated") is True or source.get("logged_in") is True
    raw_status = source.get("status")
    if isinstance(raw_status, str) and raw_status in _AUTH_STATUS_VALUES:
        status = raw_status
    else:
        status = None
    if status in {"authenticated", "ready"}:
        authenticated = True
    has_auth_signal = (
        isinstance(source.get("authenticated"), bool)
        or isinstance(source.get("logged_in"), bool)
        or status in {"authenticated", "ready", "unauthenticated", "logged_out"}
    )
    result: dict[str, Any] = {"authenticated": authenticated} if has_auth_signal else {}
    if status is not None:
        result["status"] = status
    for key in ("pending", "started"):
        if isinstance(source.get(key), bool):
            result[key] = source[key]
    mode = source.get("mode")
    if mode in {None, "chatgpt", "api_key", "synthetic"} and "mode" in source:
        result["mode"] = mode
    if source.get("provider") in _AUTH_PROVIDERS:
        result["provider"] = source["provider"]
    if source.get("auth_method") in _AUTH_METHODS:
        result["auth_method"] = source["auth_method"]
    if source.get("model") == "gpt-5.5":
        result["model"] = "gpt-5.5"
    if source.get("reasoning") == "low":
        result["reasoning"] = "low"
    return result or {"authenticated": False}


def _is_loopback(request: Request) -> bool:
    host = request.client.host if request.client else ""
    try:
        return ipaddress.ip_address(host).is_loopback
    except (TypeError, ValueError):
        return False


def _auth_failure_status(error: BaseException) -> int:
    return 409 if isinstance(error, ProviderError) and str(error).strip().lower() in _AUTH_CONFLICT_CODES else 503


def _safe_log_path() -> str | None:
    try:
        return str(get_log_path())
    except Exception:
        return None


@asynccontextmanager
async def lifespan(_: FastAPI):
    log_event(
        "server.started",
        status="started",
        outcome="success",
        process_id=os.getpid(),
        log_path=_safe_log_path(),
    )
    try:
        yield
    finally:
        close = getattr(runtime, "close", None)
        if callable(close):
            try:
                await runtime.close()
            except Exception:
                log_event(
                    "runtime.close.failed",
                    level="ERROR",
                    status="failed",
                    outcome="failure",
                    process_id=os.getpid(),
                    cause="runtime_close_failed",
                )
        log_event(
            "server.stopped",
            status="stopped",
            outcome="success",
            process_id=os.getpid(),
            log_path=_safe_log_path(),
        )


app = FastAPI(title="Aegis Baitbot Runtime", lifespan=lifespan)
runtime = BaitbotRuntime()
INDEX_PATH = Path(__file__).parent / "static" / "index.html"


def _chat_status_code(result: dict) -> int:
    errors = result.get("errors") or []
    if not errors:
        return 200
    failed_goals = {
        error.split(":", 1)[0].strip().lower()
        for error in errors
        if isinstance(error, str) and ":" in error
    }
    failures = failed_goals & {"responder", "extractor"}
    return 207 if len(failures) == 1 else 502


@app.middleware("http")
async def log_request(request: Request, call_next):
    request_id = f"request_{uuid.uuid4().hex[:12]}"
    started = time.perf_counter()
    with bind_event_context(request_id=request_id):
        log_event(
            "api.request.started",
            status="started",
            method=request.method,
            path=request.url.path,
        )
        try:
            response = await call_next(request)
        except Exception:
            log_event(
                "api.request.failed",
                level="ERROR",
                status="failed",
                status_code=500,
                duration_ms=round((time.perf_counter() - started) * 1000, 2),
                method=request.method,
                path=request.url.path,
                cause="unhandled_exception",
            )
            raise

        duration_ms = round((time.perf_counter() - started) * 1000, 2)
        if response.status_code == 207:
            log_event(
                "api.request.completed",
                level="WARNING",
                status="partial",
                status_code=response.status_code,
                duration_ms=duration_ms,
                method=request.method,
                path=request.url.path,
                details={"outcome": "partial"},
            )
        elif response.status_code >= 400:
            log_event(
                "api.request.failed",
                level="ERROR" if response.status_code >= 500 else "WARNING",
                status="failed",
                status_code=response.status_code,
                duration_ms=duration_ms,
                method=request.method,
                path=request.url.path,
                cause=f"http_{response.status_code}",
            )
        else:
            log_event(
                "api.request.completed",
                status="completed",
                status_code=response.status_code,
                duration_ms=duration_ms,
                method=request.method,
                path=request.url.path,
            )
        return response


@app.get("/", response_class=FileResponse)
async def index() -> FileResponse:
    if not INDEX_PATH.is_file():
        raise HTTPException(status_code=404, detail="static index is not available")
    return FileResponse(INDEX_PATH, media_type="text/html")


@app.get("/favicon.ico", include_in_schema=False)
async def favicon() -> Response:
    return Response(status_code=204)


@app.get("/api/config")
async def config() -> dict:
    return runtime.config()


@app.get("/api/auth/status")
async def auth_status() -> dict[str, Any]:
    try:
        return _safe_auth_status(await runtime.auth_status())
    except Exception as error:
        raise HTTPException(status_code=503, detail=_AUTH_FAILURE_DETAIL) from error


@app.post("/api/auth/login")
async def auth_login(request: Request) -> dict[str, Any]:
    if os.getenv("VERCEL") or os.getenv("VERCEL_ENV"):
        raise HTTPException(status_code=503, detail=_AUTH_FAILURE_DETAIL)
    if request.headers.get("X-Baitbot-Local") != "1" or not _is_loopback(request):
        raise HTTPException(status_code=403, detail=_AUTH_LOCAL_DETAIL)
    try:
        return _safe_auth_status(await runtime.start_auth_login())
    except Exception as error:
        raise HTTPException(status_code=_auth_failure_status(error), detail=_AUTH_FAILURE_DETAIL) from error


@app.post("/api/chat")
async def chat(request: ChatRequest) -> dict:
    try:
        result = await runtime.process(
            request.message,
            model=request.model,
            reasoning=request.reasoning,
            session_snapshot=request.session_snapshot,
        )
        return JSONResponse(content=result, status_code=_chat_status_code(result))
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@app.post("/api/scenario4/caller")
async def scenario4_caller(request: Scenario4CallerRequest) -> dict:
    try:
        return await runtime.scenario4_caller(
            scenario_id=request.scenario_id,
            mode=request.mode,
            conversation=request.conversation,
            message=request.message,
            model=request.model,
            reasoning=request.reasoning,
        )
    except ScenarioStateError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except ProviderError as error:
        raise HTTPException(status_code=502, detail="caller provider is unavailable") from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@app.post("/api/scenario4/handoff")
async def scenario4_handoff(request: Scenario4HandoffRequest) -> JSONResponse:
    try:
        result = await runtime.scenario4_handoff(
            scenario_id=request.scenario_id,
            mode=request.mode,
            conversation=request.conversation,
            session_snapshot=request.session_snapshot,
            baitbot_turn=request.baitbot_turn,
            model=request.model,
            reasoning=request.reasoning,
        )
        return JSONResponse(content=result, status_code=_chat_status_code(result))
    except ScenarioStateError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except ScenarioRiskError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except ProviderError as error:
        raise HTTPException(status_code=502, detail="caller provider is unavailable") from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@app.post("/api/reset")
async def reset() -> JSONResponse:
    return JSONResponse(await runtime.reset())
