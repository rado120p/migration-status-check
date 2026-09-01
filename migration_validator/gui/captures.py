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


@dataclass
class CaptureTask:
    id: str
    run: str
    device: str
    port: str | None
    phase: str
    state: str = "running"  # running | done | failed
    steps: list[dict] = field(default_factory=list)
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "run": self.run, "device": self.device,
            "port": self.port, "phase": self.phase, "state": self.state,
            "steps": self.steps, "error": self.error,
        }


class CaptureManager:
    def __init__(self) -> None:
        self._tasks: dict[str, CaptureTask] = {}
        self._lock = threading.Lock()

    def get(self, task_id: str) -> CaptureTask | None:
        return self._tasks.get(task_id)

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
                if task.device == device and task.state == "running":
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
            try:
                fn(on_progress)
                task.state = "done"
            except Exception as error:  # noqa: BLE001 - stav musi byt failed vzdy
                task.state = "failed"
                task.error = str(error)

        threading.Thread(target=worker, daemon=True).start()
        return task
