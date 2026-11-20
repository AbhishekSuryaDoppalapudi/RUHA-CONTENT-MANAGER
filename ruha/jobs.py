"""Persistent, restart-safe job queue with a single sequential worker.

A single worker means only one heavy model (Whisper, LLM, translator) is ever loaded at a
time, which is what a 16 GB machine needs. Jobs live in SQLite, so anything queued or
interrupted survives an application restart.
"""
from __future__ import annotations

import json
import logging
import threading
import time
import traceback
import uuid

from ruha.config import JOB_TYPES
from ruha.models import JobLog, ProcessingJob, Project, utcnow
from ruha.security import redact

log = logging.getLogger("ruha.jobs")

PIPELINE = ["transcription", "reel_generation", "poster_generation", "social_post_generation", "carousel_generation",
            "thumbnail_generation"]


class JobCancelled(Exception):
    pass


# Errors that go away by themselves (a service that is not up yet, a timeout, a busy GPU, too little free memory right
# now) are retried automatically; everything else (bad input, missing setup, a bug) fails at once and waits for a person.
_TEMPORARY_WORDS = ("timed out", "timeout", "out of memory", "cuda error", "temporarily", "connection refused",
                    "connection reset", "resource busy", "gpu is busy", "too many requests", "service unavailable")


def is_temporary(exc: BaseException) -> bool:
    import requests
    from ruha.ai.llm import LLMUnavailable
    from ruha.ai.whisper import ResourceError
    if isinstance(exc, (LLMUnavailable, ResourceError, TimeoutError, ConnectionError,
                        requests.exceptions.Timeout, requests.exceptions.ConnectionError)):
        return True
    if isinstance(exc, (ValueError, TypeError, KeyError, LookupError, PermissionError, FileNotFoundError)):
        return False
    return any(w in str(exc).lower() for w in _TEMPORARY_WORDS) or "OutOfMemory" in type(exc).__name__


class JobContext:
    """Handed to every handler: logging, throttled progress, config snapshot, cancellation."""

    def __init__(self, app, job_id: int, project_id, params: dict):
        self.app, self.job_id, self.project_id, self.params = app, job_id, project_id, params
        self.cfg = app.config.all()
        self._last = 0.0

    def log(self, message: str, level: str = "INFO"):
        message = redact(str(message))
        log.log(getattr(logging, level, logging.INFO), "[job %s] %s", self.job_id, message)
        with self.app.db.session() as s:
            s.add(JobLog(job_id=self.job_id, level=level, message=message[:4000]))

    def progress(self, fraction: float, step: str | None = None, force: bool = False):
        self.check_cancel()                       # never throttled: cancellation must be noticed promptly
        now = time.monotonic()
        if not force and now - self._last < 0.4:
            return
        self._last = now
        with self.app.db.session() as s:
            j = s.get(ProcessingJob, self.job_id)
            if j:
                j.progress = round(max(0.0, min(1.0, fraction)) * 100, 1)
                if step is not None:
                    j.step = step[:200]
        self.check_cancel()

    def check_cancel(self):
        if self.job_id in self.app.queue._cancel_flags:
            raise JobCancelled()

    def cancelled(self) -> bool:
        return self.job_id in self.app.queue._cancel_flags


class JobQueue:
    def __init__(self, app):
        self.app = app
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._cancel_flags: set = set()
        self._current: int | None = None
        self.handlers: dict = {}
        from ruha import handlers
        handlers.register(self)

    # ------------------------------ API -----------------------------------
    def enqueue(self, job_type: str, project_id: int | None, params: dict | None = None,
                depends_on: int | None = None) -> int:
        if job_type not in JOB_TYPES:
            raise ValueError(f"Unknown job type '{job_type}'")
        with self.app.db.session() as s:
            if project_id is not None and s.get(Project, project_id) is None:
                raise LookupError("Project not found")
            j = ProcessingJob(uid=str(uuid.uuid4()), project_id=project_id, job_type=job_type,
                              params_json=json.dumps(params or {}, ensure_ascii=False), depends_on_id=depends_on)
            s.add(j)
            s.flush()
            s.add(JobLog(job_id=j.id, level="INFO", message="Queued"))
            jid = j.id
            if project_id is not None:
                self._sync_project(s, project_id)
        self._wake.set()
        return jid

    def enqueue_pipeline(self, project_id: int, steps: list | None = None, translations: list | None = None,
                         language: str | None = None) -> list:
        """Chain jobs so each starts only after the previous one completed."""
        steps = steps or PIPELINE
        ids, root = [], None
        for st in steps:
            # every step needs the transcript, but reels, posters and posts do not need each other: a failure in one
            # must not stop the others, so they depend on the transcription only
            jid = self.enqueue(st, project_id, {"language": language} if language and st == "reel_generation" else {},
                               depends_on=root)
            if st == "transcription" and root is None:
                root = jid
            ids.append(jid)
        for tr in translations or []:
            prev_t = self.enqueue("dubbing" if tr.get("dub") else "translation", project_id, tr, depends_on=ids[0] if ids else None)
            ids.append(prev_t)
        return ids

    def cancel(self, job_id: int) -> bool:
        with self.app.db.session() as s:
            j = s.get(ProcessingJob, job_id)
            if not j or j.status not in ("queued", "running"):
                return False
            if j.status == "queued":
                j.status, j.completed_at = "cancelled", utcnow()
                s.add(JobLog(job_id=j.id, level="WARNING", message="Cancelled before it started"))
                if j.project_id:
                    self._sync_project(s, j.project_id)
                return True
        self._cancel_flags.add(job_id)
        return True

    def retry(self, job_id: int) -> bool:
        with self.app.db.session() as s:
            j = s.get(ProcessingJob, job_id)
            if not j or j.status not in ("failed", "cancelled"):
                return False
            j.status, j.error, j.progress, j.step = "queued", None, 0, ""
            j.started_at = j.completed_at = None
            j.params_json = json.dumps({k: v for k, v in j.params.items() if k not in ("_auto_retries", "_retry_at")},
                                       ensure_ascii=False)
            s.add(JobLog(job_id=j.id, level="INFO", message="Retry requested"))
            if j.project_id:
                self._sync_project(s, j.project_id)
        self._cancel_flags.discard(job_id)
        self._wake.set()
        return True

    def retry_failed(self, project_id: int | None = None) -> int:
        with self.app.db.session() as s:
            q = s.query(ProcessingJob.id).filter(ProcessingJob.status == "failed")
            if project_id:
                q = q.filter(ProcessingJob.project_id == project_id)
            ids = [i for i, in q]
        return sum(1 for i in ids if self.retry(i))

    def set_paused(self, paused: bool):
        self.app.config.set_many({"queue.paused": bool(paused)})
        self._wake.set()

    def paused(self) -> bool:
        return bool(self.app.config.get("queue.paused", False))

    def recover_interrupted(self) -> int:
        """After a crash/restart, jobs left 'running' go back to the queue (handlers are resumable)."""
        with self.app.db.session() as s:
            rows = s.query(ProcessingJob).filter_by(status="running").all()
            for j in rows:
                j.status, j.step = "queued", "Recovered after restart"
                s.add(JobLog(job_id=j.id, level="WARNING",
                             message="Application stopped while this job was running; re-queued. "
                                     "Completed outputs were kept and will be skipped."))
            for pid in {j.project_id for j in rows if j.project_id}:
                self._sync_project(s, pid)
            return len(rows)

    # ------------------------------ worker --------------------------------
    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="ruha-worker", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 10):
        self._stop.set()
        self._wake.set()
        if self._thread:
            self._thread.join(timeout)

    @property
    def current_job(self):
        return self._current

    def _loop(self):
        while not self._stop.is_set():
            try:
                if self.app.maintenance.is_set() or self.paused() or self.run_next() is None:
                    self._wake.wait(1.5)
                    self._wake.clear()
            except Exception:
                log.exception("worker loop error")
                time.sleep(2)

    def _next_runnable(self, s):
        for j in s.query(ProcessingJob).filter_by(status="queued").order_by(ProcessingJob.id):
            if j.depends_on_id:
                dep = s.get(ProcessingJob, j.depends_on_id)
                if dep and dep.status in ("failed", "cancelled"):
                    j.status, j.completed_at = "failed", utcnow()
                    j.error = f"Not run because job #{dep.id} ({dep.job_type}) {dep.status}"
                    s.add(JobLog(job_id=j.id, level="ERROR", message=j.error))
                    continue
                if dep and dep.status != "completed":
                    continue
            if float(j.params.get("_retry_at") or 0) > time.time():
                continue                                # waiting to retry after a temporary error; others go first
            return j
        return None

    def run_next(self) -> int | None:
        """Run the next runnable job synchronously. Returns its id, or None if nothing to do."""
        with self.app.db.session() as s:
            j = self._next_runnable(s)
            if j is None:
                pids = {x for x, in s.query(ProcessingJob.project_id).filter(ProcessingJob.status == "failed").distinct() if x}
                for pid in pids:
                    self._sync_project(s, pid)
                return None
            j.status, j.started_at, j.attempts, j.progress = "running", utcnow(), j.attempts + 1, 0
            s.add(JobLog(job_id=j.id, level="INFO", message=f"Started (attempt {j.attempts})"))
            jid, jtype, pid, params = j.id, j.job_type, j.project_id, j.params
            if pid:
                self._sync_project(s, pid)
        self._current = jid
        ctx = JobContext(self.app, jid, pid, params)
        status, error, result, temporary = "completed", None, {}, False
        try:
            handler = self.handlers[jtype]
            result = handler(self.app, ctx) or {}
        except JobCancelled:
            status, error = "cancelled", "Cancelled by user"
        except Exception as e:      # noqa: BLE001 - every failure must be recorded on the job
            status, error = "failed", redact(f"{type(e).__name__}: {e}")
            temporary = is_temporary(e)
            log.error("job %s failed (%s): %s\n%s", jid, "temporary" if temporary else "permanent", e, traceback.format_exc())
        finally:
            self._cancel_flags.discard(jid)
            self._current = None
        with self.app.db.session() as s:
            j = s.get(ProcessingJob, jid)
            if status == "failed" and temporary:
                done = int(j.params.get("_auto_retries") or 0)
                limit = int(self.app.config.get("jobs.auto_retries", 3))
                if done < limit:                                           # temporary: try again later, automatically
                    delay = float(self.app.config.get("jobs.retry_backoff_s", 20)) * (3 ** done)
                    j.params_json = json.dumps({**j.params, "_auto_retries": done + 1, "_retry_at": time.time() + delay},
                                               ensure_ascii=False)
                    j.status, j.error, j.progress, j.step = "queued", None, 0, f"Waiting to retry ({done + 1} of {limit})"
                    s.add(JobLog(job_id=jid, level="WARNING",
                                 message=f"Temporary problem: {error}. Retrying automatically in {delay:.0f}s "
                                         f"(retry {done + 1} of {limit})"))
                    if pid:
                        self._sync_project(s, pid)
                    return jid
                error = f"{error} (gave up after {limit} automatic retries)"
            j.status, j.error, j.completed_at = status, error, utcnow()
            j.result_json = json.dumps(result, ensure_ascii=False, default=str)
            if status == "completed":
                j.progress, j.step = 100, "Done"
            s.add(JobLog(job_id=jid, level="INFO" if status == "completed" else "ERROR",
                         message="Completed" if status == "completed" else f"{status.title()}: {error}"))
            if pid:
                self._sync_project(s, pid)
        return jid

    def drain(self, limit: int = 100) -> list:
        done = []
        while len(done) < limit:
            jid = self.run_next()
            if jid is None:
                break
            done.append(jid)
        return done

    # ----------------------- project status derivation --------------------
    def _sync_project(self, s, project_id: int):
        p = s.get(Project, project_id)
        if p is None or p.status in ("archived", "published"):
            return
        s.flush()
        latest: dict = {}                                   # a failure is resolved once a newer job of that type ran
        for _id, jt, st in s.query(ProcessingJob.id, ProcessingJob.job_type, ProcessingJob.status) \
                .filter_by(project_id=project_id).order_by(ProcessingJob.id):
            latest[jt] = st
        states = list(latest.values())
        if any(st in ("queued", "running") for st in states):
            p.status = "processing"
        elif states and p.status in ("processing", "failed", "uploaded", "draft", "review_required"):
            if any(st == "failed" for st in states):      # unresolved failure (retry clears it)
                p.status = "failed"
            elif any(st == "completed" for st in states):
                p.status = "review_required"
