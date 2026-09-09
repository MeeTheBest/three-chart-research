"""Public HTTP interface: short requests, one worker, durable stage checkpoints."""

from __future__ import annotations

import asyncio
import os
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from queue import Empty, Queue
from secrets import token_hex
from threading import Event, Lock, Thread
from time import monotonic

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from src.mvp import local_server as research
from src.mvp.report_recovery import recover_report
from src.mvp.persistence import SnapshotRepository
from src.mvp.session_store import EphemeralSessionStore, SessionNotFound, SYSTEMS


def now():
    return datetime.now(UTC)


def failure(exc: Exception) -> dict:
    if isinstance(exc, research.ApiError):
        return {"code": exc.code, "message": exc.message, "details": exc.details}
    return {"code": "analysis_interrupted", "message": "本次分析中断，已完成阶段已保存；可以点击继续分析。"}


class DatabaseAuditLog:
    def __init__(self, url: str):
        self.repository = SnapshotRepository(url, "research_audit")

    def record(self, event: str, payload: dict):
        self.repository.purge_expired(now())
        prefix = payload.get("sessionId", "lookup")
        self.repository.save(f"{prefix}:{token_hex(8)}", {"event": event, "payload": payload}, now() + timedelta(days=1))

    def remove_session(self, session_id: str):
        self.repository.delete_prefix(session_id + ":")

    def purge_expired(self):
        self.repository.purge_expired(now())


class Jobs:
    def __init__(self):
        self.queue: Queue = Queue()
        self.pending: set[tuple[str, str]] = set()
        self.lock = Lock()
        self.stop = Event()
        self.thread = Thread(target=self.run, name="analysis-worker", daemon=True)

    def submit(self, session_id: str, system: str):
        store = research.STATE.store
        store.get_birth_input(session_id)
        if system not in (*SYSTEMS, "integration"):
            raise research.ApiError(404, "unknown_system", "未知分析体系。")
        existing = store.get_integration_analysis(session_id) if system == "integration" else store.get_single_analyses(session_id).get(system)
        if existing:
            previous = store.get_analysis_progress(session_id, system)
            store.set_analysis_progress(session_id, system, {**previous, "state": "completed", "system": system, "label": "报告已生成，请下滑查看。"})
            return {"state": "completed", "system": system}
        if system == "integration" and store.comparison_status(session_id)["status"] != "ready":
            raise research.ApiError(409, "comparison_locked", "请先完成三项单术分析。")
        if not research.qwen_api_key():
            raise research.ApiError(503, "model_not_configured", "模型服务尚未配置，请联系网站管理员。")
        key = (session_id, system)
        with self.lock:
            if key in self.pending:
                return store.get_analysis_progress(session_id, system)
            if len(self.pending) >= 12:
                raise research.ApiError(429, "queue_full", "当前分析排队较多，请稍后再试。")
            # A durable queued record precedes the in-process queue insertion.
            progress = {"system": system, "state": "queued", "label": "已加入分析队列，轮到后会自动开始。"}
            store.set_checkpoint(session_id, "job-" + system, {"requested": True})
            store.set_analysis_progress(session_id, system, progress)
            self.pending.add(key)
            self.queue.put(key)
            return progress

    def run(self):
        while not self.stop.is_set():
            try:
                session_id, system = self.queue.get(timeout=1)
            except Empty:
                continue
            try:
                research.STATE.store.get_birth_input(session_id)
                service = research.AnalysisService()
                if system == "integration":
                    service._comparison(session_id)
                else:
                    service._single(session_id, system)
                previous = research.STATE.store.get_analysis_progress(session_id, system)
                research.STATE.store.set_analysis_progress(session_id, system, {**previous, "state": "completed", "label": "报告已生成，请下滑查看。"})
            except SessionNotFound:
                pass  # The visitor deleted the session; never resurrect it.
            except Exception as exc:
                try:
                    previous = research.STATE.store.get_analysis_progress(session_id, system)
                    diagnostic = research.STATE.store.get_diagnostic(session_id, system)
                    diagnostic.update(status="failed", failure=failure(exc))
                    research.STATE.store.set_diagnostic(session_id, system, diagnostic)
                    saved = research.STATE.store.get_checkpoint(session_id, system)
                    saved["partialReport"] = recover_report(research, session_id, system)
                    research.STATE.store.set_checkpoint(session_id, system, saved)
                    research.STATE.store.set_analysis_progress(session_id, system, {**previous, "state": "failed", "label": failure(exc)["message"], "error": failure(exc)})
                except Exception:
                    # Database failure is visible to HTTP requests; retrying a
                    # model call here could spend twice. The checkpoint remains.
                    pass
            finally:
                with self.lock:
                    self.pending.discard((session_id, system))
                self.queue.task_done()


jobs: Jobs | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global jobs
    url = os.environ.get("DATABASE_URL")
    if os.environ.get("RENDER") and not url:
        raise RuntimeError("DATABASE_URL must be configured for durable cloud reports")
    if not url:
        storage = research.ROOT / "data" / "local-audit"
        storage.mkdir(parents=True, exist_ok=True)
        url = f"sqlite:///{storage / 'sessions.sqlite3'}"
    repository = SnapshotRepository(url)
    repository.purge_expired(now())
    research.STATE.store = EphemeralSessionStore(repository=repository)
    research.STATE.audit_log = DatabaseAuditLog(url)
    research.STATE.audit_log.purge_expired()
    jobs = Jobs()
    jobs.thread.start()
    for session_id in repository.active_ids(now()):
        for system in (*SYSTEMS, "integration"):
            progress = research.STATE.store.get_analysis_progress(session_id, system)
            if progress.get("state") in {"queued", "running"}:
                try:
                    jobs.submit(session_id, system)
                except research.ApiError:
                    research.STATE.store.set_analysis_progress(session_id, system, {**progress, "state": "failed", "label": "服务已恢复，请点击继续分析。"})

    async def expire_cached_sessions():
        while True:
            await asyncio.sleep(60)
            # No periodic remote queries when idle: let the free DB sleep.
            await asyncio.to_thread(research.STATE.store.delete_expired)

    sweeper = asyncio.create_task(expire_cached_sessions())
    try:
        yield
    finally:
        sweeper.cancel()
        jobs.stop.set()


app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
_rates: dict[str, deque] = defaultdict(deque)
_rate_lock = Lock()


@app.middleware("http")
async def request_limits(request: Request, call_next):
    if request.url.path.startswith("/api/"):
        length = request.headers.get("content-length", "0")
        if not length.isdigit() or int(length) > 20_000:
            return JSONResponse({"error": {"code": "body_too_large", "message": "提交内容过大。"}}, status_code=413)
        if request.method == "POST" and request.url.path == "/api/sessions":
            client = request.client.host if request.client else "unknown"
            with _rate_lock:
                current = monotonic()
                for key in list(_rates):
                    while _rates[key] and _rates[key][0] < current - 3600:
                        _rates[key].popleft()
                    if not _rates[key]:
                        del _rates[key]
                if len(_rates[client]) >= 6:
                    return JSONResponse({"error": {"code": "rate_limited", "message": "每小时最多创建6个会话，请稍后再试。"}}, status_code=429)
                _rates[client].append(current)
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    return response


@app.exception_handler(research.ApiError)
async def api_error(_request, exc):
    return JSONResponse({"error": failure(exc)}, status_code=exc.status)


@app.exception_handler(SessionNotFound)
async def expired_error(_request, _exc):
    return JSONResponse({"error": {"code": "session_not_found", "message": "会话已删除或过期。"}}, status_code=404)


@app.exception_handler(Exception)
async def unexpected_error(_request, _exc):
    return JSONResponse({"error": {"code": "service_unavailable", "message": "服务连接暂时中断，请稍后重试；已保存的报告仍保留。"}}, status_code=503)


@app.get("/api/health")
def health():
    return {"status": "ok", "model": research.MODEL, "modelConfigured": bool(research.qwen_api_key()), "storage": "persistent", "frozenResultHours": 24}


@app.get("/api/source")
def source_repository():
    url = os.environ.get("SOURCE_REPOSITORY_URL")
    if not url:
        raise research.ApiError(503, "source_not_configured", "源码仓库地址尚未配置。")
    return RedirectResponse(url, status_code=307)


@app.post("/api/sessions", status_code=201)
def create_session(data: dict):
    service = research.AnalysisService()
    birth, resolution = service._birth(data)
    research.STATE.store.repository.purge_expired(now())
    session_id = research.STATE.store.create(birth)
    case_id = research.STATE.new_case(session_id)
    research.STATE.audit_log.record("session_created", service._birth_audit_payload(session_id, case_id, birth, resolution))
    return {"sessionId": session_id, "caseId": case_id, "activeExpiresInMinutes": 30, "frozenResultRetentionHours": 24}


@app.get("/api/sessions/{session_id}")
def get_session(session_id: str):
    return {**research.STATE.store.session_summary(session_id), "sessionId": session_id, "caseId": research.STATE.case_id(session_id)}


@app.delete("/api/sessions/{session_id}")
def delete_session(session_id: str):
    research.STATE.close(session_id)
    return {"deleted": True}


@app.get("/api/sessions/{session_id}/comparison")
def comparison_status(session_id: str):
    return research.STATE.store.comparison_status(session_id)


@app.get("/api/sessions/{session_id}/analyses/{system}/progress")
def progress(session_id: str, system: str):
    if system not in (*SYSTEMS, "integration"):
        raise research.ApiError(404, "unknown_system", "未知分析体系。")
    return research.STATE.store.get_analysis_progress(session_id, system)


@app.get("/api/sessions/{session_id}/analyses/{system}/diagnostic")
def diagnostic(session_id: str, system: str):
    return research.STATE.store.get_diagnostic(session_id, system)


@app.get("/api/sessions/{session_id}/analyses/{system}")
def get_analysis(session_id: str, system: str):
    store = research.STATE.store
    if system == "integration":
        analysis = store.get_integration_analysis(session_id)
    elif system in SYSTEMS:
        analysis = store.get_single_analyses(session_id).get(system)
    else:
        raise research.ApiError(404, "unknown_system", "未知分析体系。")
    if analysis is None:
        raise research.ApiError(404, "analysis_not_found", "该报告尚未生成。")
    return {"analysis": analysis, "comparison": store.comparison_status(session_id)}


@app.get("/api/sessions/{session_id}/analyses/{system}/partial")
def partial_report(session_id: str, system: str):
    if system not in (*SYSTEMS, "integration"):
        raise research.ApiError(404, "unknown_system", "未知分析体系。")
    store = research.STATE.store
    diagnostic = store.get_diagnostic(session_id, system)
    if diagnostic.get("status") != "failed":
        raise research.ApiError(404, "partial_not_found", "当前没有未完成报告。")
    saved = store.get_checkpoint(session_id, system)
    return saved.get("partialReport") or recover_report(research, session_id, system)


@app.post("/api/sessions/{session_id}/analyses/{system}", status_code=202)
def start_analysis(session_id: str, system: str):
    return jobs.submit(session_id, system)


@app.post("/api/sessions/{session_id}/comparison", status_code=202)
def start_comparison(session_id: str):
    return jobs.submit(session_id, "integration")


@app.api_route("/api/{path:path}", methods=["GET", "POST", "DELETE"])
def unknown_route(path: str):
    raise research.ApiError(404, "not_found", "接口不存在。")


# Serve only the built frontend. Never mount source, Skill packs, or data.
frontend = Path(os.environ.get("FRONTEND_DIR", str(research.ROOT / "mvp-web" / "dist" / "client")))
if frontend.is_dir():
    app.mount("/", StaticFiles(directory=frontend, html=True), name="website")
