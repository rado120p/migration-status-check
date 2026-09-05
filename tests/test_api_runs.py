"""Testy pro api.create_run a api.update_mapping - seam pro GUI."""

import pytest
import yaml

from migration_validator import api

OLD = {"node": "MX1", "host": "10.0.0.1", "platform": "junos", "role": "old"}
NEW = {"node": "PTX1", "host": "10.0.0.2", "platform": "junos-evo", "role": "new"}
SINGLE = {"node": "PTX1", "host": "10.0.0.2", "platform": "junos-evo", "role": "single"}


def test_create_run_zapise_run_yml(tmp_path):
    manifest = api.create_run(
        "mig02",
        kind="migration",
        devices=[OLD, NEW],
        mappings=[("ge-0/0/1", "et-0/0/1")],
        run_root=tmp_path,
    )
    raw = yaml.safe_load((tmp_path / "mig02" / "run.yml").read_text())
    assert raw["devices"]["MX1"]["role"] == "old"
    assert raw["devices"]["PTX1"]["role"] == "new"
    assert raw["interface_mapping"] == [
        {"old": {"node": "MX1", "port": "ge-0/0/1"},
         "new": {"node": "PTX1", "port": "et-0/0/1"}}
    ]
    assert raw["captures"] == []
    assert manifest.devices["MX1"].host == "10.0.0.1"


def test_create_run_bez_mappingu_je_validni(tmp_path):
    api.create_run("seq", kind="migration", devices=[OLD, NEW], run_root=tmp_path)
    raw = yaml.safe_load((tmp_path / "seq" / "run.yml").read_text())
    assert raw["interface_mapping"] == []


def test_create_run_porty_zustavaji_syrove(tmp_path):
    # normalize_port je jen pro nazvy souboru, run.yml nese ge-0/0/2
    api.create_run(
        "raw", kind="migration", devices=[OLD, NEW],
        mappings=[("ge-0/0/2", "ae0")], run_root=tmp_path,
    )
    raw = yaml.safe_load((tmp_path / "raw" / "run.yml").read_text())
    assert raw["interface_mapping"][0]["old"]["port"] == "ge-0/0/2"


def test_create_run_nevalidni_jmeno(tmp_path):
    with pytest.raises(ValueError, match="jmeno"):
        api.create_run("Mig 02!", kind="migration", devices=[OLD, NEW], run_root=tmp_path)


def test_create_run_existujici_adresar(tmp_path):
    (tmp_path / "mig02").mkdir()
    with pytest.raises(ValueError, match="existuje"):
        api.create_run("mig02", kind="migration", devices=[OLD, NEW], run_root=tmp_path)


def test_create_run_duplicitni_old_port(tmp_path):
    with pytest.raises(ValueError, match="sparovan"):
        api.create_run(
            "dup", kind="migration", devices=[OLD, NEW],
            mappings=[("ge-0/0/1", "et-0/0/1"), ("ge-0/0/1", "et-0/0/2")],
            run_root=tmp_path,
        )


def test_create_run_n_na_1_lag_je_povoleny(tmp_path):
    api.create_run(
        "lag", kind="migration", devices=[OLD, NEW],
        mappings=[("ge-0/0/1", "ae0"), ("ge-0/0/2", "ae0")],
        run_root=tmp_path,
    )
    raw = yaml.safe_load((tmp_path / "lag" / "run.yml").read_text())
    assert len(raw["interface_mapping"]) == 2


def test_create_run_chybejici_klic_zarizeni(tmp_path):
    with pytest.raises(ValueError, match="chybi 'host'"):
        api.create_run(
            "bad", kind="migration",
            devices=[{"node": "MX1", "platform": "junos", "role": "old"}, NEW],
            run_root=tmp_path,
        )


from migration_validator.runs.manifest import CaptureRecord, load_manifest


def _run_se_snimkem(tmp_path):
    """Run se dvema pairingy, prvni ma pre snimek -> je zamceny."""
    api.create_run(
        "edit", kind="migration", devices=[OLD, NEW],
        mappings=[("ge-0/0/1", "et-0/0/1"), ("ge-0/0/2", "et-0/0/2")],
        run_root=tmp_path,
    )
    manifest_path = tmp_path / "edit" / "run.yml"
    manifest = load_manifest(manifest_path)
    manifest.record_capture(CaptureRecord(
        phase="pre", device="MX1", port="ge-0/0/1",
        snapshot="snapshot_pre_MX1_ge_0_0_1.json", taken="2026-09-01T00:00:00Z",
    ))
    from migration_validator.runs.manifest import save_manifest
    save_manifest(manifest, manifest_path)
    return manifest_path


def test_update_mapping_prida_a_odebere_volny_pairing(tmp_path):
    _run_se_snimkem(tmp_path)
    result = api.update_mapping(
        "edit",
        [("ge-0/0/1", "et-0/0/1"), ("ge-0/0/3", "et-0/0/3")],
        run_root=tmp_path,
    )
    ports = [(m.old.port, m.new.port) for m in result.interface_mapping]
    assert ports == [("ge-0/0/1", "et-0/0/1"), ("ge-0/0/3", "et-0/0/3")]
    # zapsano na disk
    on_disk = load_manifest(tmp_path / "edit" / "run.yml")
    assert len(on_disk.interface_mapping) == 2


def test_update_mapping_zamceny_pairing_nelze_odebrat(tmp_path):
    _run_se_snimkem(tmp_path)
    with pytest.raises(ValueError, match="ma snimky"):
        api.update_mapping("edit", [("ge-0/0/2", "et-0/0/2")], run_root=tmp_path)


def test_update_mapping_zamek_plati_i_pro_novy_endpoint(tmp_path):
    path = _run_se_snimkem(tmp_path)
    manifest = load_manifest(path)
    manifest.record_capture(CaptureRecord(
        phase="post", device="PTX1", port="et-0/0/2",
        snapshot="snapshot_post_PTX1_et_0_0_2.json", taken="2026-09-01T00:00:00Z",
    ))
    from migration_validator.runs.manifest import save_manifest
    save_manifest(manifest, path)
    with pytest.raises(ValueError, match="ma snimky"):
        api.update_mapping("edit", [("ge-0/0/1", "et-0/0/1")], run_root=tmp_path)


def test_update_mapping_neexistujici_run(tmp_path):
    with pytest.raises(ValueError, match="neexistuje"):
        api.update_mapping("neni", [], run_root=tmp_path)


def test_update_mapping_zachova_captures(tmp_path):
    _run_se_snimkem(tmp_path)
    result = api.update_mapping(
        "edit",
        [("ge-0/0/1", "et-0/0/1"), ("ge-0/0/2", "et-0/0/2")],
        run_root=tmp_path,
    )
    assert len(result.captures) == 1


def test_update_mapping_all_snimek_zamyka_pairingy_zarizeni(tmp_path):
    """Celozarizeni snimek (port=None) zamyka vsechny pairingy sveho boxu."""
    api.create_run(
        "alldev", kind="migration", devices=[OLD, NEW],
        mappings=[("ge-0/0/1", "et-0/0/1")],
        run_root=tmp_path,
    )
    manifest_path = tmp_path / "alldev" / "run.yml"
    manifest = load_manifest(manifest_path)
    # Zaznamenij celozarizeni snimek
    manifest.record_capture(CaptureRecord(
        phase="pre", device="MX1", port=None,
        snapshot="snapshot_pre_MX1_all.json", taken="2026-09-01T00:00:00Z",
    ))
    from migration_validator.runs.manifest import save_manifest
    save_manifest(manifest, manifest_path)
    # Pairing zamceny - nelze ho odebrat
    with pytest.raises(ValueError, match="ma snimky"):
        api.update_mapping("alldev", [], run_root=tmp_path)


from datetime import datetime, timezone
from pathlib import Path


def test_archive_run_presune_adresar_do_archive(tmp_path):
    api.create_run("mig02", kind="migration", devices=[OLD, NEW], run_root=tmp_path)
    (tmp_path / "mig02" / "snapshot_pre_MX1_all.json").write_text("{}")
    when = datetime(2026, 9, 4, 10, 30, 0, tzinfo=timezone.utc)
    target = api.archive_run("mig02", run_root=tmp_path, now=when)
    assert target == tmp_path / ".archive" / "mig02-20260904T103000Z"
    assert (target / "run.yml").exists()
    assert (target / "snapshot_pre_MX1_all.json").exists()
    assert not (tmp_path / "mig02").exists()


def test_archive_run_neexistujici_run(tmp_path):
    with pytest.raises(FileNotFoundError, match="run 'nope' neexistuje"):
        api.archive_run("nope", run_root=tmp_path)


def test_archive_run_nevalidni_jmeno(tmp_path):
    with pytest.raises(ValueError, match="nevalidni jmeno runu"):
        api.archive_run("../etc", run_root=tmp_path)


def _archived(tmp_path, name, when):
    api.create_run(name, kind="migration", devices=[OLD, NEW], run_root=tmp_path)
    (tmp_path / name / "snapshot_pre_MX1_all.json").write_text("{}")
    return api.archive_run(name, run_root=tmp_path, now=when)


def test_list_archive_cte_jmeno_cas_a_pocet_snimku(tmp_path):
    _archived(tmp_path, "mig02", datetime(2026, 9, 1, tzinfo=timezone.utc))
    _archived(tmp_path, "mig01", datetime(2026, 8, 1, tzinfo=timezone.utc))
    entries = api.list_archive(tmp_path)
    assert [e.name for e in entries] == ["mig01", "mig02"]
    assert entries[0].archived == datetime(2026, 8, 1, tzinfo=timezone.utc)
    assert entries[0].snapshots == 1
    assert entries[0].path == tmp_path / ".archive" / "mig01-20260801T000000Z"


def test_list_archive_bez_archivu_je_prazdny(tmp_path):
    assert api.list_archive(tmp_path) == []


def test_purge_archive_maze_jen_starsi(tmp_path):
    _archived(tmp_path, "mig01", datetime(2026, 8, 1, tzinfo=timezone.utc))
    keep = _archived(tmp_path, "mig02", datetime(2026, 9, 3, tzinfo=timezone.utc))
    removed = api.purge_archive(
        tmp_path, older_than_days=7,
        now=datetime(2026, 9, 4, tzinfo=timezone.utc),
    )
    assert [e.name for e in removed] == ["mig01"]
    assert not (tmp_path / ".archive" / "mig01-20260801T000000Z").exists()
    assert keep.exists()


# -- kind single / migration ---------------------------------------------------


def test_create_run_zapise_kind_migration(tmp_path):
    api.create_run("mig02", kind="migration", devices=[OLD, NEW], run_root=tmp_path)
    raw = yaml.safe_load((tmp_path / "mig02" / "run.yml").read_text())
    assert raw["kind"] == "migration"
    assert "profile" not in raw


def test_create_run_single(tmp_path):
    manifest = api.create_run("upg01", kind="single", devices=[SINGLE], run_root=tmp_path)
    assert manifest.kind == "single"
    assert manifest.devices["PTX1"].role == "single"
    raw = yaml.safe_load((tmp_path / "upg01" / "run.yml").read_text())
    assert raw["kind"] == "single"
    assert raw["interface_mapping"] == []


def test_create_run_neznamy_kind(tmp_path):
    with pytest.raises(ValueError, match="neznamy kind 'bulk'"):
        api.create_run("b", kind="bulk", devices=[SINGLE], run_root=tmp_path)
    assert not (tmp_path / "b").exists()


def test_create_run_single_se_dvema_zarizenimi(tmp_path):
    with pytest.raises(ValueError, match="run typu single"):
        api.create_run(
            "s", kind="single",
            devices=[SINGLE, {**OLD, "role": "single"}], run_root=tmp_path,
        )
    assert not (tmp_path / "s").exists()


def test_create_run_single_se_spatnou_roli(tmp_path):
    with pytest.raises(ValueError, match="run typu single"):
        api.create_run("s", kind="single", devices=[OLD], run_root=tmp_path)


def test_create_run_single_s_mappingem(tmp_path):
    with pytest.raises(ValueError, match="run typu single nema interface mapping"):
        api.create_run(
            "s", kind="single", devices=[SINGLE],
            mappings=[("ge-0/0/1", "et-0/0/1")], run_root=tmp_path,
        )


def test_create_run_migration_bez_new(tmp_path):
    with pytest.raises(ValueError, match="jedno zarizeni role 'old' a jedno role 'new'"):
        api.create_run("m", kind="migration", devices=[OLD], run_root=tmp_path)


def test_create_run_migration_se_single_roli(tmp_path):
    with pytest.raises(ValueError, match="role 'single'"):
        api.create_run("m", kind="migration", devices=[OLD, SINGLE], run_root=tmp_path)


def test_create_run_duplicitni_node(tmp_path):
    with pytest.raises(ValueError, match="uvedeno dvakrat"):
        api.create_run(
            "d", kind="migration",
            devices=[OLD, {**NEW, "node": "MX1"}], run_root=tmp_path,
        )


def test_create_run_profil_zatim_nelze(tmp_path):
    # do spec 3 (profile store) je kazdy nenulovy profil chyba
    with pytest.raises(ValueError, match="profil 'core-only' neexistuje"):
        api.create_run(
            "p", kind="single", devices=[SINGLE], profile="core-only", run_root=tmp_path,
        )
    assert not (tmp_path / "p").exists()


def test_update_mapping_odmitne_single(tmp_path):
    api.create_run("upg01", kind="single", devices=[SINGLE], run_root=tmp_path)
    with pytest.raises(ValueError, match="run typu single nema interface mapping"):
        api.update_mapping("upg01", [("ge-0/0/1", "et-0/0/1")], run_root=tmp_path)


def test_update_mapping_zachova_kind_profile_group(tmp_path):
    from migration_validator.runs.manifest import load_manifest, save_manifest
    api.create_run("mig02", kind="migration", devices=[OLD, NEW], run_root=tmp_path)
    path = tmp_path / "mig02" / "run.yml"
    manifest = load_manifest(path)
    manifest.profile = "core-only"
    manifest.group = "g1"
    save_manifest(manifest, path)
    api.update_mapping("mig02", [("ge-0/0/1", "et-0/0/1")], run_root=tmp_path)
    reloaded = load_manifest(path)
    assert reloaded.kind == "migration"
    assert reloaded.profile == "core-only"
    assert reloaded.group == "g1"
