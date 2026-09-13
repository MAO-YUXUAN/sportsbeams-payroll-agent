from __future__ import annotations

import threading
import inspect
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any, Callable
from uuid import uuid4


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


@dataclass
class Job:
    id: str
    run_id: str
    status: str = "queued"
    created_at: str = ""
    started_at: str | None = None
    finished_at: str | None = None
    result: dict[str, Any] | None = None
    error: str | None = None
    progress: int = 0
    progress_message: str = "等待开始"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class JobManager:
    """Serial worker for Excel COM operations plus duplicate-run protection."""

    def __init__(self):
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="payroll-excel")
        self._lock = threading.RLock()
        self._jobs: dict[str, Job] = {}
        self._active_runs: set[str] = set()

    def submit(self, run_id: str, operation: Callable[[], dict[str, Any]]) -> Job:
        with self._lock:
            if run_id in self._active_runs:
                raise RuntimeError(f"Run already has an active job: {run_id}")
            job = Job(id=f"job_{uuid4().hex}", run_id=run_id, created_at=_now())
            self._jobs[job.id] = job
            self._active_runs.add(run_id)
        self._executor.submit(self._execute, job.id, operation)
        return job

    def _execute(self, job_id: str, operation: Callable[[], dict[str, Any]]) -> None:
        with self._lock:
            job = self._jobs[job_id]
            job.status = "running"
            job.started_at = _now()
            job.progress = 1
            job.progress_message = "任务已开始"
        def report(progress: int, message: str) -> None:
            with self._lock:
                current = self._jobs[job_id]
                current.progress = max(current.progress, min(99, max(0, int(progress))))
                current.progress_message = str(message)
        try:
            result = operation(report) if len(inspect.signature(operation).parameters) else operation()
        except Exception as error:
            with self._lock:
                job.status = "failed"
                job.error = f"{type(error).__name__}: {error}"
                job.progress_message = "处理失败"
        else:
            with self._lock:
                job.status = "completed"
                job.result = result
                job.progress = 100
                job.progress_message = "处理完成"
        finally:
            with self._lock:
                job.finished_at = _now()
                self._active_runs.discard(job.run_id)

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def is_active(self, run_id: str) -> bool:
        with self._lock:
            return run_id in self._active_runs

    def shutdown(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=False)
