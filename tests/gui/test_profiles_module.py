"""profile_for_run / profile_usage - reseni profilu per run."""

from pathlib import Path

import pytest

from migration_validator.gui.profiles import (
    profile_for_run,
    profile_usage,
    server_default_profile,
)
from migration_validator.profiles.store import ProfileStore, empty_document
from migration_validator.runs.manifest import RunDevice, RunManifest


def _manifest(profile=None):
    return RunManifest(
        devices={"PTX1": RunDevice(host="10.0.0.2", platform="junos-evo", role="single")},
        kind="single", profile=profile,
    )


def test_bez_profilu_vraci_serverovy_default(tmp_path):
    store = ProfileStore(tmp_path / "profiles")
    profile = profile_for_run(_manifest(), Path("runs/x/run.yml"), store=store, default_path=None)
    assert profile.name == "" and profile.collectors is None


def test_bez_profilu_vraci_soubor_z_profile_flagu(tmp_path):
    default = tmp_path / "core.yml"
    default.write_text("profile:\n  ping_count: 3\n", encoding="utf-8")
    store = ProfileStore(tmp_path / "profiles")
    profile = profile_for_run(
        _manifest(), Path("runs/x/run.yml"), store=store, default_path=str(default)
    )
    assert profile.ping_count == 3 and profile.name == "core.yml"
    assert server_default_profile(str(default)).ping_count == 3
    assert server_default_profile(None).ping_count is None


def test_profil_ze_store(tmp_path):
    store = ProfileStore(tmp_path / "profiles")
    doc = empty_document()
    doc["profile"]["ping_count"] = 2
    store.save("core-only", doc)
    profile = profile_for_run(
        _manifest("core-only"), Path("runs/x/run.yml"), store=store, default_path=None
    )
    assert profile.ping_count == 2 and profile.name == "core-only.yml"


def test_chybejici_profil_je_chyba_bez_fallbacku(tmp_path):
    store = ProfileStore(tmp_path / "profiles")
    with pytest.raises(ValueError) as info:
        profile_for_run(
            _manifest("neni"), Path("runs/x/run.yml"), store=store, default_path=None
        )
    assert str(info.value) == "profil 'neni' neexistuje (runs/x/run.yml)"


def test_profile_usage_pocita_manifesty(tmp_path):
    for run, profile in (("a", "core-only"), ("b", "core-only"), ("c", None), ("d", "jiny")):
        (tmp_path / run).mkdir()
        text = "schema_version: 1\nkind: single\ndevices: {}\n"
        if profile:
            text += f"profile: {profile}\n"
        (tmp_path / run / "run.yml").write_text(text, encoding="utf-8")
    (tmp_path / ".archive").mkdir()
    (tmp_path / ".archive" / "z-20260101T000000Z").mkdir()
    (tmp_path / ".archive" / "z-20260101T000000Z" / "run.yml").write_text(
        "profile: core-only\n", encoding="utf-8"
    )
    (tmp_path / "e").mkdir()  # bez run.yml
    (tmp_path / "f").mkdir()
    (tmp_path / "f" / "run.yml").write_text("profile: [unclosed\n", encoding="utf-8")
    assert profile_usage(tmp_path) == {"core-only": 2, "jiny": 1}
    assert profile_usage(tmp_path / "neexistuje") == {}
