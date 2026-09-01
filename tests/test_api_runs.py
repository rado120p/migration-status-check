"""Testy pro api.create_run a api.update_mapping - seam pro GUI."""

import pytest
import yaml

from migration_validator import api

OLD = {"node": "MX1", "host": "10.0.0.1", "platform": "junos"}
NEW = {"node": "PTX1", "host": "10.0.0.2", "platform": "junos-evo"}


def test_create_run_zapise_run_yml(tmp_path):
    manifest = api.create_run(
        "mig02",
        old_device=OLD,
        new_device=NEW,
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
    api.create_run("seq", old_device=OLD, new_device=NEW, run_root=tmp_path)
    raw = yaml.safe_load((tmp_path / "seq" / "run.yml").read_text())
    assert raw["interface_mapping"] == []


def test_create_run_porty_zustavaji_syrove(tmp_path):
    # normalize_port je jen pro nazvy souboru, run.yml nese ge-0/0/2
    api.create_run(
        "raw", old_device=OLD, new_device=NEW,
        mappings=[("ge-0/0/2", "ae0")], run_root=tmp_path,
    )
    raw = yaml.safe_load((tmp_path / "raw" / "run.yml").read_text())
    assert raw["interface_mapping"][0]["old"]["port"] == "ge-0/0/2"


def test_create_run_nevalidni_jmeno(tmp_path):
    with pytest.raises(ValueError, match="jmeno"):
        api.create_run("Mig 02!", old_device=OLD, new_device=NEW, run_root=tmp_path)


def test_create_run_existujici_adresar(tmp_path):
    (tmp_path / "mig02").mkdir()
    with pytest.raises(ValueError, match="existuje"):
        api.create_run("mig02", old_device=OLD, new_device=NEW, run_root=tmp_path)


def test_create_run_duplicitni_old_port(tmp_path):
    with pytest.raises(ValueError, match="sparovan"):
        api.create_run(
            "dup", old_device=OLD, new_device=NEW,
            mappings=[("ge-0/0/1", "et-0/0/1"), ("ge-0/0/1", "et-0/0/2")],
            run_root=tmp_path,
        )


def test_create_run_n_na_1_lag_je_povoleny(tmp_path):
    api.create_run(
        "lag", old_device=OLD, new_device=NEW,
        mappings=[("ge-0/0/1", "ae0"), ("ge-0/0/2", "ae0")],
        run_root=tmp_path,
    )
    raw = yaml.safe_load((tmp_path / "lag" / "run.yml").read_text())
    assert len(raw["interface_mapping"]) == 2


def test_create_run_chybejici_klic_zarizeni(tmp_path):
    with pytest.raises(ValueError, match="host"):
        api.create_run(
            "bad", old_device={"node": "MX1", "platform": "junos"},
            new_device=NEW, run_root=tmp_path,
        )


from migration_validator.runs.manifest import CaptureRecord, load_manifest


def _run_se_snimkem(tmp_path):
    """Run se dvema pairingy, prvni ma pre snimek -> je zamceny."""
    api.create_run(
        "edit", old_device=OLD, new_device=NEW,
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
