from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import threading
from contextlib import asynccontextmanager
from datetime import date
from typing import Any
from urllib.parse import urlparse

from fastapi import Body, FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from . import APPLICATION_ID
from .config import Settings, ensure_runtime_paths, load_settings
from .credentials import InvalidApiKeyError
from .db import Database
from .llm import LlmContractError, LlmUnavailable, build_adapter
from .services import (
    ConflictError, NotFoundError, PortfolioService, ValidationError,
)


LOGGER = logging.getLogger(__name__)
WORKER_MAX_BACKOFF_MULTIPLIER = 30
SHUTDOWN_GRACE_SECONDS = 10.0


class _HashedAssetFiles(StaticFiles):
    """Vite assets carry a content hash in the filename, so they never change
    in place and can be cached forever; index.html (served elsewhere with
    no-cache) is what points browsers at the current hashes."""

    def file_response(self, *args: Any, **kwargs: Any):  # type: ignore[override]
        response = super().file_response(*args, **kwargs)
        response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        return response


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class GroupCreate(StrictModel):
    name: str = Field(min_length=1, max_length=120)
    sort_order: int = 0


class GroupUpdate(StrictModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    sort_order: int | None = None


class ProjectCreate(StrictModel):
    name: str = Field(min_length=1, max_length=240)
    portfolio_group_id: int | None = None


class ProjectUpdate(StrictModel):
    name: str | None = None
    portfolio_group_id: int | None = None
    status: str | None = None
    priority: str | None = None
    owner_text: str | None = None
    next_action: str | None = None
    next_action_due: str | None = None


class NoteCreate(StrictModel):
    title: str = "Manual note"
    text: str = Field(min_length=1)
    is_transcript: bool = False
    meeting_name: str | None = None
    meeting_date: str | None = None


class ChatRequest(StrictModel):
    question: str = Field(min_length=1, max_length=4000)


class ReviewResolution(StrictModel):
    action: str = "apply"
    target_project_id: str | None = None
    action_item_id: int | None = None
    field: str | None = None
    value: str | None = None
    rule: dict[str, Any] | None = None
    description: str | None = None
    assignee_type: str | None = None
    assignee_value: str | None = None
    due_date: str | None = None
    reason: str | None = None


class SourceRemoval(StrictModel):
    reason: str = Field(default="Removed from active project memory by the user.", max_length=500)


class ApiKeyUpdate(StrictModel):
    api_key: SecretStr = Field(min_length=1, max_length=20_000)


class ModelSelection(StrictModel):
    routine_model: str = Field(min_length=1, max_length=256)
    judgment_model: str = Field(min_length=1, max_length=256)


class ActionCreate(StrictModel):
    description: str
    assignee_type: str = "me"
    assignee_value: str = "me"
    due_date: str | None = None
    state: str = "open"
    progress_text: str | None = None


class ActionUpdate(StrictModel):
    description: str | None = None
    assignee_type: str | None = None
    assignee_value: str | None = None
    due_date: str | None = None
    state: str | None = None
    progress_text: str | None = None


class Completion(StrictModel):
    confirmed: bool


class RuleActive(StrictModel):
    active: bool


class ReviewStatus(StrictModel):
    status: str


class ContentSizeLimitMiddleware:
    def __init__(self, app: Any, *, max_bytes: int):
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = {key.decode("latin1").casefold(): value.decode("latin1") for key, value in scope.get("headers", [])}
        try:
            declared = int(headers.get("content-length", "0"))
        except ValueError:
            declared = self.max_bytes + 1
        if declared > self.max_bytes:
            await JSONResponse({"detail": "Request body exceeds the configured size limit"}, status_code=413)(scope, receive, send)
            return
        consumed = 0

        async def limited_receive() -> dict[str, Any]:
            nonlocal consumed
            message = await receive()
            if message.get("type") == "http.request":
                consumed += len(message.get("body", b""))
                if consumed > self.max_bytes:
                    raise HTTPException(status_code=413, detail="Request body exceeds the configured size limit")
            return message

        await self.app(scope, limited_receive, send)


class LoopbackGuard:
    def __init__(self, app: Any, *, port: int, testing: bool):
        self.app = app
        self.port = port
        self.allowed = {"127.0.0.1", f"127.0.0.1:{port}", "localhost", f"localhost:{port}", "[::1]", f"[::1]:{port}"}
        if testing:
            self.allowed.add("testserver")

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] == "http":
            headers = {key.decode("latin1").casefold(): value.decode("latin1") for key, value in scope.get("headers", [])}
            host = headers.get("host", "").casefold()
            if host not in self.allowed:
                await JSONResponse({"detail": "Host is not an allowed loopback origin"}, status_code=400)(scope, receive, send)
                return
            origin = headers.get("origin")
            if origin:
                parsed = urlparse(origin)
                origin_host = (parsed.hostname or "").casefold()
                try:
                    origin_port = parsed.port
                except ValueError:
                    origin_port = None
                if parsed.scheme != "http" or origin_host not in {"127.0.0.1", "localhost", "::1"} or origin_port != self.port:
                    await JSONResponse({"detail": "Origin is not an allowed loopback origin"}, status_code=403)(scope, receive, send)
                    return
            elif scope.get("method", "GET").upper() not in {"GET", "HEAD", "OPTIONS"}:
                requested_with = headers.get("x-requested-with", "")
                if requested_with != "CHIO-Portfolio-Assistant":
                    await JSONResponse({"detail": "State-changing requests require an approved origin or application header"}, status_code=403)(scope, receive, send)
                    return
        await self.app(scope, receive, send)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or load_settings()
    ensure_runtime_paths(settings)
    db = Database(settings.app.database_path)
    db.migrate()
    llm = build_adapter(settings.llm)
    service = PortfolioService(settings, db, llm)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        service.recover_interrupted()
        stop = asyncio.Event()

        async def worker() -> None:
            consecutive_failures = 0
            while not stop.is_set():
                try:
                    await asyncio.to_thread(service.process_pending, manual=False, limit=10)
                    consecutive_failures = 0
                except asyncio.CancelledError:
                    raise
                except Exception:
                    # A single filesystem, OneDrive or SQLite error must not end the
                    # worker: an unguarded raise here leaves every future upload stuck
                    # in 'captured' with no error surfaced anywhere in the interface.
                    consecutive_failures += 1
                    LOGGER.exception(
                        "Background source processing failed (attempt %s); retrying",
                        consecutive_failures,
                    )
                multiplier = min(2 ** consecutive_failures, WORKER_MAX_BACKOFF_MULTIPLIER)
                delay = settings.app.worker_poll_seconds * (multiplier if consecutive_failures else 1)
                try:
                    await asyncio.wait_for(stop.wait(), timeout=delay)
                except asyncio.TimeoutError:
                    pass

        task = None if settings.app.testing else asyncio.create_task(worker(), name="source-worker")
        if not settings.app.testing and settings.llm.adapter == "internal":
            threading.Thread(
                target=service.refresh_llm_model_catalog,
                name="genai-model-catalog-refresh",
                daemon=True,
            ).start()
        yield
        stop.set()
        if task:
            task.cancel()
            # cancel() cannot interrupt work already running inside to_thread, so an
            # in-flight batch of LLM calls would otherwise hold shutdown for as long
            # as its timeouts and retries take.
            with contextlib.suppress(asyncio.CancelledError, asyncio.TimeoutError):
                await asyncio.wait_for(asyncio.shield(task), timeout=SHUTDOWN_GRACE_SECONDS)

    app = FastAPI(title="CHIO Portfolio Assistant", version="1.0.0", lifespan=lifespan)
    app.state.settings = settings
    app.state.db = db
    app.state.service = service
    app.add_middleware(LoopbackGuard, port=settings.app.bind_port, testing=settings.app.testing)
    app.add_middleware(ContentSizeLimitMiddleware, max_bytes=settings.app.max_file_mb * 1024 * 1024 + 64 * 1024)

    @app.exception_handler(NotFoundError)
    async def not_found(_: Request, exc: NotFoundError) -> JSONResponse:
        return JSONResponse({"detail": str(exc)}, status_code=404)

    @app.exception_handler(ConflictError)
    async def conflict(_: Request, exc: ConflictError) -> JSONResponse:
        return JSONResponse({"detail": str(exc)}, status_code=409)

    async def invalid(_: Request, exc: Exception) -> JSONResponse:
        return JSONResponse({"detail": str(exc)}, status_code=422)

    app.add_exception_handler(ValidationError, invalid)
    app.add_exception_handler(LlmContractError, invalid)
    app.add_exception_handler(InvalidApiKeyError, invalid)

    @app.exception_handler(RequestValidationError)
    async def request_invalid(_: Request, exc: RequestValidationError) -> JSONResponse:
        # Two constraints. Pydantic reports the rejected value in "input" and
        # SecretStr does not redact it, so only loc and msg may be echoed.
        # And detail must be a string: every other handler here returns
        # {"detail": str} and the frontend renders detail verbatim — a list
        # would surface as "[object Object]" in the UI.
        parts = []
        for error in exc.errors():
            location = ".".join(
                str(piece) for piece in error.get("loc", ()) if piece != "body"
            )
            message = str(error.get("msg", "Invalid value"))
            parts.append(f"{location}: {message}" if location else message)
        return JSONResponse({"detail": "; ".join(parts)[:2000]}, status_code=422)

    @app.exception_handler(LlmUnavailable)
    async def unavailable(_: Request, exc: LlmUnavailable) -> JSONResponse:
        return JSONResponse({"detail": str(exc)}, status_code=503)

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        return {
            "ok": True,
            "application": APPLICATION_ID,
            "version": "1.0.0",
            "retrieval_mode": db.fts_mode,
        }

    @app.get("/api/configuration")
    def configuration(test_llm: bool = False) -> dict[str, Any]:
        return service.configuration_status(test_llm=test_llm)

    @app.put("/api/llm/credential")
    def save_llm_credential(body: ApiKeyUpdate) -> dict[str, Any]:
        return service.save_llm_api_key(body.api_key.get_secret_value())

    @app.delete("/api/llm/credential")
    def remove_llm_credential() -> dict[str, Any]:
        return service.remove_llm_api_key()

    @app.put("/api/llm/models")
    def save_llm_models(body: ModelSelection) -> dict[str, Any]:
        return service.save_llm_models(body.routine_model, body.judgment_model)

    @app.get("/api/llm/models")
    def llm_models() -> dict[str, Any]:
        return service.refresh_llm_model_catalog()

    @app.post("/api/llm/health")
    def llm_health() -> dict[str, Any]:
        return service.llm_health()

    @app.get("/api/groups")
    def groups() -> list[dict[str, Any]]:
        return service.list_groups()

    @app.post("/api/groups", status_code=201)
    def create_group(body: GroupCreate) -> dict[str, Any]:
        return service.create_group(body.name, body.sort_order)

    @app.patch("/api/groups/{group_id}")
    def update_group(group_id: int, body: GroupUpdate) -> dict[str, Any]:
        return service.update_group(group_id, **body.model_dump(exclude_unset=True))

    @app.get("/api/projects")
    def projects(
        q: str = "", portfolio_group_id: int | None = None,
        assignment_group: str | None = None, status: str | None = None,
        priority: str | None = None, limit: int = Query(250, ge=1, le=250),
    ) -> dict[str, Any]:
        return service.list_projects(
            query=q, portfolio_group_id=portfolio_group_id, assignment_group=assignment_group,
            status=status, priority=priority, limit=limit,
        )

    @app.post("/api/projects", status_code=201)
    def create_project(body: ProjectCreate) -> dict[str, Any]:
        return service.create_project(body.name, portfolio_group_id=body.portfolio_group_id)

    @app.get("/api/projects/{project_id}")
    def project(project_id: str) -> dict[str, Any]:
        return service.get_project(project_id)

    @app.patch("/api/projects/{project_id}")
    def patch_project(project_id: str, body: ProjectUpdate) -> dict[str, Any]:
        return service.update_project(project_id, body.model_dump(exclude_unset=True))

    @app.post("/api/projects/{project_id}/sources", status_code=202)
    def upload_source(
        project_id: str, file: UploadFile | None = File(None), files: list[UploadFile] = File(default=[]),
        relative_paths: str | None = Form(None), meeting_name: str | None = Form(None),
        meeting_date: str | None = Form(None), is_transcript: bool = Form(False),
    ) -> dict[str, Any]:
        uploads = ([file] if file else []) + files
        if not uploads:
            raise HTTPException(status_code=422, detail="At least one file is required")
        try:
            paths = json.loads(relative_paths) if relative_paths else []
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=422, detail="relative_paths must be a JSON array") from exc
        if not isinstance(paths, list) or not all(isinstance(path, str) for path in paths):
            raise HTTPException(status_code=422, detail="relative_paths must be a JSON array of strings")
        source, duplicate = service.capture_sources(
            [(upload.file, upload.filename or "source", paths[index] if index < len(paths) else upload.filename or "source")
             for index, upload in enumerate(uploads)],
            project_id=project_id, meeting_name=meeting_name, meeting_date=meeting_date,
            is_transcript=is_transcript,
        )
        return {"source": source, "duplicate": duplicate}

    @app.post("/api/projects/{project_id}/notes", status_code=202)
    def note(project_id: str, body: NoteCreate) -> dict[str, Any]:
        return service.capture_note(
            project_id, body.text, body.title, is_transcript=body.is_transcript,
            meeting_name=body.meeting_name, meeting_date=body.meeting_date,
        )

    @app.post("/api/intake/multi-project", status_code=202)
    def multi_project_upload(
        file: UploadFile | None = File(None), files: list[UploadFile] = File(default=[]),
        relative_paths: str | None = Form(None), meeting_name: str | None = Form(None),
        meeting_date: str | None = Form(None), is_transcript: bool = Form(False),
    ) -> dict[str, Any]:
        uploads = ([file] if file else []) + files
        if not uploads:
            raise HTTPException(status_code=422, detail="At least one file is required")
        try:
            paths = json.loads(relative_paths) if relative_paths else []
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=422, detail="relative_paths must be a JSON array") from exc
        if not isinstance(paths, list) or not all(isinstance(path, str) for path in paths):
            raise HTTPException(status_code=422, detail="relative_paths must be a JSON array of strings")
        source, duplicate = service.capture_sources(
            [(upload.file, upload.filename or "source", paths[index] if index < len(paths) else upload.filename or "source")
             for index, upload in enumerate(uploads)], project_id=None,
            meeting_name=meeting_name, meeting_date=meeting_date,
            is_transcript=is_transcript, multi_project=True,
        )
        return {"source": source, "duplicate": duplicate}

    @app.post("/api/import/servicenow")
    def import_snow(file: UploadFile = File(...)) -> dict[str, Any]:
        return service.import_snow(file.file, file.filename or "snow-export")

    @app.post("/api/sources/{source_id}/retry")
    def retry_source(source_id: int) -> dict[str, int]:
        return service.retry_source(source_id)

    @app.post("/api/sources/retry-pending")
    def retry_pending() -> dict[str, int]:
        return service.process_pending(manual=True, limit=100)

    @app.get("/api/chunks/{chunk_id}")
    def chunk(chunk_id: int) -> dict[str, Any]:
        return service.get_chunk(chunk_id)

    @app.get("/api/sources/{source_id}/original")
    def original(source_id: int) -> FileResponse:
        path, filename = service.get_source_original(source_id)
        return FileResponse(path, media_type="application/octet-stream", filename=filename, content_disposition_type="attachment")

    @app.get("/api/sources/{source_id}")
    def source_detail(source_id: int) -> dict[str, Any]:
        return service.source_detail(source_id)

    @app.post("/api/projects/{project_id}/sources/{source_id}/remove")
    def remove_source(project_id: str, source_id: int, body: SourceRemoval) -> dict[str, Any]:
        return service.remove_source_from_memory(project_id, source_id, body.reason)

    @app.post("/api/projects/{project_id}/sources/{source_id}/restore")
    def restore_source(project_id: str, source_id: int) -> dict[str, Any]:
        return service.restore_source_to_memory(project_id, source_id)

    @app.get("/api/original-files/{original_file_id}")
    def original_file(original_file_id: int) -> FileResponse:
        path, filename = service.get_original_file(original_file_id)
        return FileResponse(path, media_type="application/octet-stream", filename=filename, content_disposition_type="attachment")

    @app.get("/api/citations/{citation_id}/original")
    def citation_original(citation_id: str) -> FileResponse:
        path, filename = service.get_citation_original(citation_id)
        return FileResponse(path, media_type="application/octet-stream", filename=filename, content_disposition_type="attachment")

    @app.get("/api/projects/{project_id}/knowledge")
    def knowledge(
        project_id: str, review_status: str = "all", category: str = "", q: str = ""
    ) -> list[dict[str, Any]]:
        return service.list_knowledge(project_id, review_status=review_status, category=category, query=q)

    @app.patch("/api/projects/{project_id}/knowledge/{knowledge_id}")
    def review_knowledge(project_id: str, knowledge_id: str, body: ReviewStatus) -> dict[str, Any]:
        return service.review_knowledge(project_id, knowledge_id, body.status)

    @app.get("/api/projects/{project_id}/living-summary")
    def living_summary(project_id: str) -> dict[str, Any]:
        return service.get_living_summary(project_id)

    @app.get("/api/projects/{project_id}/living-summary/versions/{revision}")
    def living_summary_version(project_id: str, revision: int) -> dict[str, Any]:
        return service.get_summary_version(project_id, revision)

    @app.post("/api/projects/{project_id}/living-summary/regenerate")
    def regenerate_summary(project_id: str) -> dict[str, Any]:
        return service.regenerate_living_summary(project_id)

    @app.post("/api/projects/{project_id}/knowledge/rebuild")
    def rebuild_project_knowledge(project_id: str) -> dict[str, Any]:
        return service.rebuild_project_knowledge(project_id)

    @app.patch("/api/projects/{project_id}/living-summary/review")
    def review_summary(project_id: str, body: ReviewStatus) -> dict[str, Any]:
        return service.review_summary(project_id, body.status)

    @app.post("/api/sources/{source_id}/refresh-derived")
    def refresh_source_derivatives(source_id: int) -> dict[str, Any]:
        return service.refresh_source_derivatives(source_id)

    @app.get("/api/search")
    def archive_search(q: str, project_id: str | None = None, limit: int = Query(50, ge=1, le=100)) -> list[dict[str, Any]]:
        return service.search_archive(q, project_id=project_id, limit=limit)

    @app.post("/api/archive/rescan")
    def rescan_archive() -> dict[str, int]:
        return service.rebuild_index()

    @app.post("/api/projects/{project_id}/chat")
    def chat(project_id: str, body: ChatRequest) -> dict[str, Any]:
        return service.ask_project(project_id, body.question)

    @app.get("/api/reviews")
    def reviews(status: str = "open") -> list[dict[str, Any]]:
        return service.list_reviews(status)

    @app.post("/api/reviews/{review_id}/resolve")
    def resolve(review_id: int, body: ReviewResolution) -> dict[str, Any]:
        return service.resolve_review(review_id, body.model_dump(exclude_unset=True))

    @app.get("/api/routing-rules")
    def rules() -> list[dict[str, Any]]:
        return service.list_routing_rules()

    @app.patch("/api/routing-rules/{rule_id}")
    def rule_active(rule_id: int, body: RuleActive) -> dict[str, Any]:
        return service.set_routing_rule_active(rule_id, body.active)

    @app.get("/api/projects/{project_id}/actions")
    def actions(project_id: str) -> list[dict[str, Any]]:
        return service.list_actions(project_id)

    @app.post("/api/projects/{project_id}/actions", status_code=201)
    def create_action(project_id: str, body: ActionCreate) -> dict[str, Any]:
        return service.create_action(project_id, body.model_dump())

    @app.patch("/api/projects/{project_id}/actions/{action_id}")
    def update_action(project_id: str, action_id: int, body: ActionUpdate) -> dict[str, Any]:
        return service.update_action(project_id, action_id, body.model_dump(exclude_unset=True))

    @app.post("/api/projects/{project_id}/actions/{action_id}/complete")
    def complete_action(project_id: str, action_id: int, body: Completion) -> dict[str, Any]:
        return service.complete_action(project_id, action_id, body.confirmed)

    @app.get("/api/daily")
    def daily(run_date: date | None = None) -> dict[str, Any] | None:
        return service.get_daily(run_date)

    @app.post("/api/daily")
    def run_daily(run_date: date | None = Body(default=None, embed=True)) -> dict[str, Any]:
        return service.run_daily(run_date)

    static_dir = settings.static_dir
    if static_dir.exists():
        assets = static_dir / "assets"
        if assets.exists():
            app.mount("/assets", _HashedAssetFiles(directory=assets), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        def frontend(path: str) -> FileResponse:
            if path == "api" or path.startswith("api/"):
                raise HTTPException(status_code=404, detail="API route not found")
            candidate = static_dir / path
            if path and candidate.is_file() and candidate.resolve().is_relative_to(static_dir.resolve()):
                response = FileResponse(candidate)
            else:
                response = FileResponse(static_dir / "index.html")
            # Without a cache policy, browsers cache index.html heuristically
            # and keep running a stale frontend against an upgraded backend.
            # no-cache means revalidate-every-load, which is free on loopback.
            response.headers["Cache-Control"] = "no-cache"
            return response

    return app
