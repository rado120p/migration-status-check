"""In-memory evidence bezicich captures.

Zaznamy zijou jen v pameti procesu - restart serveru ztrati progress
pohled, ale nikdy data: snapshot bud v run.yml pristal, nebo ne;
overview se nacita z disku."""

from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable


class DeviceBusy(Exception):
    """Na zarizeni uz bezi capture - PyEZ session se nesdili."""


ACTIVE_STATES = ("queued", "running")


@dataclass
class CaptureTask:
    id: str
    run: str
    device: str
    port: str | None
    phase: str
    state: str = "queued"  # queued | running | done | failed
    steps: list[dict] = field(default_factory=list)
    error: str | None = None
    failed_collectors: dict = field(default_factory=dict)
    warnings: list = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "run": self.run, "device": self.device,
            "port": self.port, "phase": self.phase, "state": self.state,
            "steps": self.steps, "error": self.error,
            "failed_collectors": self.failed_collectors,
            "warnings": self.warnings,
        }


class CaptureManager:
    """Pool omezuje pocet soucasne bezicich captures (settings.yml
    connection.capture_pool). Task ceka jako `queued`, dokud se neuvolni
    slot; busy kontrola zarizeni i runu pocita queued i running."""

    def __init__(self, pool: int = 10) -> None:
        self._tasks: dict[str, CaptureTask] = {}
        self._lock = threading.Lock()
        self._slots = threading.Semaphore(pool)

    def get(self, task_id: str) -> CaptureTask | None:
        with self._lock:
            return self._tasks.get(task_id)

    def busy_run(self, run: str) -> bool:
        """True, dokud na runu ceka nebo bezi aspon jeden capture."""
        return self.active_task(run) is not None

    def active_task(self, run: str) -> CaptureTask | None:
        with self._lock:
            for task in self._tasks.values():
                if task.run == run and task.state in ACTIVE_STATES:
                    return task
        return None

    def last_finished(self, run: str) -> CaptureTask | None:
        """Posledni dokonceny (done/failed) task runu - insertion order slovniku."""
        with self._lock:
            for task in reversed(list(self._tasks.values())):
                if task.run == run and task.state in ("done", "failed"):
                    return task
        return None

    def start(
        self,
        fn: Callable[[Callable], Any],
        *,
        run: str,
        device: str,
        port: str | None,
        phase: str,
    ) -> CaptureTask:
        with self._lock:
            for task in self._tasks.values():
                if task.device == device and task.state in ACTIVE_STATES:
                    raise DeviceBusy(
                        f"na zarizeni {device} uz bezi capture ({task.id})"
                    )
            task = CaptureTask(
                id=uuid.uuid4().hex[:12], run=run, device=device,
                port=port, phase=phase,
            )
            self._tasks[task.id] = task

        def on_progress(step: str, status: str, message: str | None) -> None:
            with self._lock:
                if status == "start":
                    task.steps.append(
                        {"collector": step, "status": "running", "message": message}
                    )
                else:
                    for entry in reversed(task.steps):
                        if entry["collector"] == step:
                            entry["status"] = status
                            entry["message"] = message
                            break

        def worker() -> None:
            with self._slots:
                with self._lock:
                    task.state = "running"
                try:
                    outcome = fn(on_progress)
                    failed = getattr(outcome, "failed_collectors", None)
                    if failed:
                        task.failed_collectors = failed
                    warns = getattr(outcome, "warnings", None)
                    if warns:
                        task.warnings = warns
                    task.state = "done"
                except Exception as error:  # noqa: BLE001 - stav musi byt failed vzdy
                    task.state = "failed"
                    task.error = str(error)

        threading.Thread(target=worker, daemon=True).start()
        return task
