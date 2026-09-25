"""Audit stopa GUI: kdo co zmenil. Jen log, zadny soubor navic."""

from __future__ import annotations

import logging
import sys

AUDIT_LOGGER = "migration_validator.gui.audit"
_log = logging.getLogger(AUDIT_LOGGER)
_HANDLER_NAME = "mig-audit"


def record(username: str, action: str, target: str = "", **extra: str) -> None:
    parts = [f"user={username}", f"action={action}"]
    if target:
        parts.append(f"target={target}")
    parts.extend(f"{key}={value}" for key, value in extra.items())
    _log.info(" ".join(parts))


def configure_audit_logging(stream=None) -> None:
    """Vlastni handler - uvicorn root logger nekonfiguruje a INFO by se
    jinak ztratilo; propagate=False aby se ncclient INFO nepridal."""
    for handler in list(_log.handlers):
        if handler.get_name() == _HANDLER_NAME:
            _log.removeHandler(handler)
    handler = logging.StreamHandler(stream or sys.stderr)
    handler.set_name(_HANDLER_NAME)
    handler.setFormatter(logging.Formatter("%(asctime)s audit %(message)s"))
    _log.addHandler(handler)
    _log.setLevel(logging.INFO)
    _log.propagate = False
