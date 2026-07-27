"""Nacitani nahraneho RPC XML pro testy collectoru."""

from __future__ import annotations

from pathlib import Path

import pytest
from lxml import etree

FIXTURE_ROOT = Path(__file__).resolve().parents[1] / "fixtures" / "rpc"


@pytest.fixture
def rpc_fixture():
    def load(platform: str, name: str) -> etree._Element:
        path = FIXTURE_ROOT / platform / f"{name}.xml"
        if not path.exists():
            pytest.skip(f"chybi fixture {path} - nahraj ji pres 'mig-validate record'")
        return etree.parse(str(path)).getroot()

    return load
