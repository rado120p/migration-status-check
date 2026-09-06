"""Testy pro skupiny (bulk single runy) v api.py."""

from datetime import datetime, timezone

import pytest
import yaml

from migration_validator import api

DEVICES = [
    {"node": "PTX1-POP1", "host": "172.20.20.5", "platform": "junos-evo"},
    {"node": "MX2-POP1", "host": "172.20.20.7", "platform": "junos"},
]


def test_group_run_name_je_group_a_node_malymi():
    assert api.group_run_name("pop1", "PTX1-POP1") == "pop1-ptx1-pop1"


def test_create_group_zapise_n_single_runu(tmp_path):
    names = api.create_group("pop1", DEVICES, run_root=tmp_path, profiles_root=tmp_path / "p")
    assert names == ["pop1-ptx1-pop1", "pop1-mx2-pop1"]
    raw = yaml.safe_load((tmp_path / "pop1-ptx1-pop1" / "run.yml").read_text())
    assert raw["kind"] == "single"
    assert raw["group"] == "pop1"
    assert raw["devices"] == {
        "PTX1-POP1": {"host": "172.20.20.5", "platform": "junos-evo", "role": "single"}
    }
    assert "profile" not in raw or raw["profile"] is None


def test_create_group_nevalidni_jmeno_je_radek_none(tmp_path):
    with pytest.raises(api.GroupError) as excinfo:
        api.create_group("Pop 1", DEVICES, run_root=tmp_path, profiles_root=tmp_path / "p")
    assert excinfo.value.rows == [
        (None, "nevalidni jmeno skupiny 'Pop 1' - povolene znaky: a-z 0-9 _ -")
    ]
    assert list(tmp_path.iterdir()) == []


def test_create_group_prazdny_seznam(tmp_path):
    with pytest.raises(api.GroupError) as excinfo:
        api.create_group("pop1", [], run_root=tmp_path, profiles_root=tmp_path / "p")
    assert excinfo.value.rows == [(None, "seznam zarizeni je prazdny")]


def test_create_group_existujici_skupina(tmp_path):
    api.create_group("pop1", DEVICES, run_root=tmp_path, profiles_root=tmp_path / "p")
    with pytest.raises(api.GroupError) as excinfo:
        api.create_group("pop1", DEVICES, run_root=tmp_path, profiles_root=tmp_path / "p")
    assert excinfo.value.rows == [(None, "skupina 'pop1' uz existuje")]


def test_create_group_neexistujici_profil(tmp_path):
    with pytest.raises(api.GroupError) as excinfo:
        api.create_group("pop1", DEVICES, profile="nope", run_root=tmp_path,
                         profiles_root=tmp_path / "p")
    assert excinfo.value.rows[0][0] is None
    assert "profil 'nope' neexistuje" in excinfo.value.rows[0][1]


def test_create_group_radkove_chyby_maji_index_a_nic_se_nezapise(tmp_path):
    (tmp_path / "pop1-ex9").mkdir()
    (tmp_path / "pop1-ex9" / "run.yml").write_text("devices: {}\n", encoding="utf-8")
    devices = [
        {"node": "PTX1", "host": "", "platform": "junos-evo"},
        {"node": "MX2", "host": "10.0.0.2", "platform": "ios"},
        {"node": "ptx1", "host": "10.0.0.3", "platform": "junos"},
        {"node": "EX9", "host": "10.0.0.4", "platform": "junos"},
    ]
    with pytest.raises(api.GroupError) as excinfo:
        api.create_group("pop1", devices, run_root=tmp_path, profiles_root=tmp_path / "p")
    assert excinfo.value.rows == [
        (0, "zarizeni 'PTX1': chybi 'host'"),
        (1, "zarizeni 'MX2': neznama platforma 'ios', ocekavano junos nebo junos-evo"),
        (2, "zarizeni 'ptx1' je uvedeno dvakrat (radek 1)"),
        (3, "run 'pop1-ex9' uz existuje"),
    ]
    assert sorted(p.name for p in tmp_path.iterdir()) == ["pop1-ex9"]


def test_group_runs_a_list_groups(tmp_path):
    api.create_group("pop1", DEVICES, run_root=tmp_path, profiles_root=tmp_path / "p")
    api.create_run("solo", kind="single", devices=[
        {"node": "X", "host": "1.1.1.1", "platform": "junos", "role": "single"}
    ], run_root=tmp_path)
    (tmp_path / "broken").mkdir()
    (tmp_path / "broken" / "run.yml").write_text("[not a mapping", encoding="utf-8")
    assert api.group_runs(tmp_path) == {"pop1": ["pop1-mx2-pop1", "pop1-ptx1-pop1"]}
    groups = api.list_groups(tmp_path)
    assert len(groups) == 1
    assert groups[0]["name"] == "pop1"
    assert groups[0]["runs"] == 2
    assert groups[0]["profile"] is None
    assert groups[0]["created"].endswith("Z")


def test_add_group_devices_dedi_profil_a_odmita_duplicitu(tmp_path):
    from migration_validator.profiles.store import ProfileStore, empty_document
    ProfileStore(tmp_path / "p").save("core-only", empty_document())
    api.create_group("pop1", DEVICES, profile="core-only", run_root=tmp_path,
                     profiles_root=tmp_path / "p")
    names = api.add_group_devices(
        "pop1", [{"node": "EX1", "host": "10.0.0.9", "platform": "junos"}], run_root=tmp_path,
    )
    assert names == ["pop1-ex1"]
    raw = yaml.safe_load((tmp_path / "pop1-ex1" / "run.yml").read_text())
    assert raw["profile"] == "core-only"
    assert raw["group"] == "pop1"
    with pytest.raises(api.GroupError) as excinfo:
        api.add_group_devices(
            "pop1", [{"node": "ex1", "host": "10.0.0.9", "platform": "junos"}], run_root=tmp_path,
        )
    assert excinfo.value.rows == [(0, "run 'pop1-ex1' uz existuje")]


def test_add_group_devices_neznama_skupina(tmp_path):
    with pytest.raises(FileNotFoundError, match="skupina 'pop1' neexistuje"):
        api.add_group_devices("pop1", DEVICES, run_root=tmp_path)


def test_archive_group_presune_vsechny_cleny(tmp_path):
    api.create_group("pop1", DEVICES, run_root=tmp_path, profiles_root=tmp_path / "p")
    now = datetime(2026, 9, 6, 8, 0, 0, tzinfo=timezone.utc)
    archived = api.archive_group("pop1", run_root=tmp_path, now=now)
    assert archived == [
        "pop1-mx2-pop1-20260906T080000Z", "pop1-ptx1-pop1-20260906T080000Z",
    ]
    assert api.group_runs(tmp_path) == {}
    assert (tmp_path / ".archive" / archived[0] / "run.yml").exists()


def test_archive_group_neznama_skupina(tmp_path):
    with pytest.raises(FileNotFoundError, match="skupina 'pop1' neexistuje"):
        api.archive_group("pop1", run_root=tmp_path)
