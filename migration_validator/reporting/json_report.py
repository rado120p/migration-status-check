"""Serializace vysledku do JSON."""

from __future__ import annotations

import json
from pathlib import Path

from migration_validator.models.jsonsafe import encode_nonfinite
from migration_validator.models.result import RunResult


def to_json(result: RunResult, *, indent: int = 2) -> str:
    # encode_nonfinite + allow_nan=False: opticke -inf by jinak vysly jako
    # '-Infinity', coz neni platny JSON a striktni parsery ho odmitnou.
    return json.dumps(
        encode_nonfinite(result.to_dict()),
        indent=indent,
        ensure_ascii=False,
        allow_nan=False,
    )


def write_json(result: RunResult, path: str | Path) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(to_json(result) + "\n", encoding="utf-8")
