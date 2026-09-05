"""Profile store - profiles/<name>.yml, validace pres load_profile, atomicky zapis."""

import pytest

from migration_validator.config import PING_COUNT_DEFAULT, Profile, CheckConfig
from migration_validator.profiles.store import (
    ProfileStore,
    check_profile_name,
    document_from_profile,
    document_to_yaml,
    empty_document,
    strip_check_defaults,
)


def _doc(**profile):
    return {"profile": {"collectors": None, "service_types": None,
                        "ping_count": None, **profile}, "checks": {}}


def test_ping_count_default_je_pet():
    assert PING_COUNT_DEFAULT == 5


def test_empty_document_ma_plny_tvar():
    assert empty_document() == _doc()


def test_document_to_yaml_vynecha_null_a_prazdne_checks():
    text = document_to_yaml(_doc(collectors=["interfaces", "bgp"]))
    assert text == "profile:\n  collectors:\n  - interfaces\n  - bgp\n"


def test_document_to_yaml_prazdny_dokument_je_prazdny_text():
    assert document_to_yaml(empty_document()) == ""


def test_document_to_yaml_vynecha_prazdne_seznamy():
    # Prazdny seznam znamena v capture "neber nic" - ulozit se smi jen to,
    # co uzivatel opravdu vybral, takze se zahodi stejne jako null.
    text = document_to_yaml(_doc(collectors=[], service_types=[], ping_count=3))
    assert text == "profile:\n  ping_count: 3\n"
    assert document_to_yaml(_doc(collectors=[], service_types=[])) == ""


def test_document_to_yaml_zachova_checks_a_vynecha_null_option():
    doc = _doc(ping_count=3)
    doc["checks"] = {
        "interface_traffic": {"tolerance_percent": -40, "require_nonzero": None},
        "traffic_ceased": {"enabled": None},
    }
    text = document_to_yaml(doc)
    assert text == (
        "profile:\n  ping_count: 3\n"
        "checks:\n  interface_traffic:\n    tolerance_percent: -40\n"
    )


def test_document_to_yaml_je_stabilni():
    doc = _doc(service_types=["IPVPN", "Internet"])
    doc["checks"] = {"bgp_prefix_counts": {"tolerance_percent": -5}}
    assert document_to_yaml(doc) == document_to_yaml(doc)


def test_check_profile_name():
    check_profile_name("core-only_2")
    with pytest.raises(ValueError, match="nevalidni jmeno profilu 'Core Only'"):
        check_profile_name("Core Only")
    with pytest.raises(ValueError, match="nevalidni jmeno profilu '..'"):
        check_profile_name("..")


def test_save_a_load_roundtrip(tmp_path):
    store = ProfileStore(tmp_path / "profiles")
    doc = _doc(collectors=["interfaces"], service_types=["IPVPN"], ping_count=3)
    doc["checks"] = {"interface_optics_levels": {"enabled": False}}
    path = store.save("core-only", doc)
    assert path == tmp_path / "profiles" / "core-only.yml"
    assert path.read_text(encoding="utf-8") == document_to_yaml(doc)
    profile = store.load("core-only")
    assert profile.collectors == ["interfaces"]
    assert profile.service_types == ["IPVPN"]
    assert profile.ping_count == 3
    assert profile.name == "core-only.yml"
    assert not profile.checks.enabled("interface_optics_levels")
    assert store.document("core-only") == doc
    assert store.list() == ["core-only"]
    assert store.exists("core-only") and not store.exists("jiny")


def test_list_ignoruje_cizi_soubory(tmp_path):
    store = ProfileStore(tmp_path)
    store.save("b", empty_document())
    store.save("a", empty_document())
    (tmp_path / "Poznamky.yml").write_text("", encoding="utf-8")
    (tmp_path / ".a.x.tmp").write_text("", encoding="utf-8")
    (tmp_path / "readme.txt").write_text("", encoding="utf-8")
    assert store.list() == ["a", "b"]


def test_save_nevalidni_dokument_nezanecha_temp_a_puvodni_soubor_zustane(tmp_path):
    store = ProfileStore(tmp_path)
    store.save("core-only", _doc(ping_count=3))
    before = store.path("core-only").read_text(encoding="utf-8")
    with pytest.raises(ValueError) as info:
        store.save("core-only", _doc(collectors=["iface"]))
    message = str(info.value)
    assert "neznamy collector 'iface'" in message
    assert message.startswith(str(store.path("core-only")))
    assert ".tmp" not in message
    assert store.path("core-only").read_text(encoding="utf-8") == before
    assert sorted(p.name for p in tmp_path.iterdir()) == ["core-only.yml"]


def test_save_neznamy_klic_v_profile_je_chyba(tmp_path):
    store = ProfileStore(tmp_path)
    doc = _doc()
    doc["profile"]["servicetypes"] = ["IPVPN"]
    with pytest.raises(ValueError, match="neznamy klic servicetypes"):
        store.save("x", doc)
    assert list(tmp_path.iterdir()) == []


def test_save_odmitne_spatne_jmeno_bez_zapisu(tmp_path):
    store = ProfileStore(tmp_path)
    with pytest.raises(ValueError, match="nevalidni jmeno profilu"):
        store.save("Core Only", empty_document())
    assert not tmp_path.exists() or list(tmp_path.iterdir()) == []


def test_load_neexistujici_profil(tmp_path):
    store = ProfileStore(tmp_path)
    with pytest.raises(FileNotFoundError, match="profil 'neni' neexistuje"):
        store.load("neni")


def test_delete(tmp_path):
    store = ProfileStore(tmp_path)
    store.save("a", empty_document())
    store.delete("a")
    assert store.list() == []
    with pytest.raises(FileNotFoundError, match="profil 'a' neexistuje"):
        store.delete("a")


def test_document_from_profile():
    profile = Profile(
        checks=CheckConfig({"bgp_prefix_counts": {"tolerance_percent": -5}}),
        collectors=["bgp"], service_types=None, ping_count=None, name="x.yml",
    )
    doc = document_from_profile(profile)
    assert doc == {
        "profile": {"collectors": ["bgp"], "service_types": None, "ping_count": None},
        "checks": {"bgp_prefix_counts": {"tolerance_percent": -5}},
    }
    assert doc["checks"] is not profile.checks.raw


# -- strip_check_defaults: soubor nese jen to, co se lisi od defaultu ------

def test_strip_vyhodi_hodnoty_rovne_defaultu_a_prazdny_check():
    checks = {
        "interface_traffic": {"tolerance_percent": -60, "require_nonzero": True,
                              "severity": "advisory", "enabled": True},
        "bgp_prefix_counts": {"tolerance_percent": -5},
    }
    assert strip_check_defaults(checks) == {"bgp_prefix_counts": {"tolerance_percent": -5}}


def test_strip_enabled_false_zustava_jen_u_defaultne_zapnutych():
    assert strip_check_defaults({"interface_state": {"enabled": False}}) == {
        "interface_state": {"enabled": False},
    }
    # traffic_ceased je defaultne vypnuty: enabled: true je override,
    # enabled: false ne.
    assert strip_check_defaults({"traffic_ceased": {"enabled": True}}) == {
        "traffic_ceased": {"enabled": True},
    }
    assert strip_check_defaults({"traffic_ceased": {"enabled": False}}) == {}


def test_strip_severity_jina_nez_default_zustava():
    assert strip_check_defaults({"bgp_prefix_counts": {"severity": "critical"}}) == {
        "bgp_prefix_counts": {"severity": "critical"},
    }
    assert strip_check_defaults({"interface_state": {"severity": "critical"}}) == {}


def test_strip_necha_neznamy_check_a_neznamou_volbu():
    checks = {"old_check": {"enabled": False}, "interface_traffic": {"foo": 1}}
    assert strip_check_defaults(checks) == checks


def test_strip_necha_hodnotu_spatneho_typu_loaderu():
    # "-60" se rovna defaultu jen na pohled - stripnout ji by loaderu
    # sebralo chybu, kterou ma uzivatel videt.
    checks = {"interface_traffic": {"tolerance_percent": "-60", "require_nonzero": 1}}
    assert strip_check_defaults(checks) == checks


def test_document_to_yaml_pouziva_strip():
    doc = _doc()
    doc["checks"] = {
        "interface_traffic": {"tolerance_percent": -60, "require_nonzero": True},
        "interface_optics_levels": {"enabled": False, "tolerance_db": 2.0},
    }
    assert document_to_yaml(doc) == "checks:\n  interface_optics_levels:\n    enabled: false\n"
