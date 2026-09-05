"""Profil - nadmnozina dnesniho --config YAML."""

import pytest

from migration_validator.config import Profile, default_profile, load_profile
from migration_validator.models.result import Severity


def write(tmp_path, content, name="profil.yml"):
    path = tmp_path / name
    path.write_text(content, encoding="utf-8")
    return path


def test_stary_checks_only_yaml_se_nacita_beze_zmeny(tmp_path):
    path = write(tmp_path, "checks:\n  bgp_prefix_counts:\n    tolerance_percent: -5\n")
    profile = load_profile(path)
    assert profile.collectors is None
    assert profile.service_types is None
    assert profile.ping_count is None
    assert profile.checks.options("bgp_prefix_counts")["tolerance_percent"] == -5


def test_plny_profil(tmp_path):
    path = write(
        tmp_path,
        "profile:\n"
        "  collectors: [interfaces, bgp]\n"
        "  service_types: [Internet, IPVPN]\n"
        "  ping_count: 3\n"
        "checks:\n"
        "  interface_optics_levels:\n"
        "    enabled: false\n",
        name="core-only.yml",
    )
    profile = load_profile(path)
    assert profile.collectors == ["interfaces", "bgp"]
    assert profile.service_types == ["Internet", "IPVPN"]
    assert profile.ping_count == 3
    assert profile.name == "core-only.yml"
    assert not profile.checks.enabled("interface_optics_levels")


def test_neznamy_klic_v_profile_je_chyba(tmp_path):
    path = write(tmp_path, "profile:\n  servicetypes: [Internet]\n")
    with pytest.raises(ValueError, match="neznamy klic"):
        load_profile(path)


def test_neznamy_collector_je_chyba_s_vypisem(tmp_path):
    path = write(tmp_path, "profile:\n  collectors: [iface]\n")
    with pytest.raises(ValueError, match="neznamy collector 'iface'"):
        load_profile(path)


def test_neznamy_top_level_klic_je_chyba(tmp_path):
    path = write(tmp_path, "cheks:\n  bgp_prefix_counts: {}\n")
    with pytest.raises(ValueError, match="neznamy klic"):
        load_profile(path)


def test_default_profile_je_prazdny():
    profile = default_profile()
    assert profile == Profile(checks=profile.checks)
    assert profile.checks.enabled("bgp_prefix_counts")


# -- sekce checks: typy podle DEFAULTS, severity z enumu, enabled bool -----


def test_checks_number_jako_string_je_chyba_se_jmenem_checku_a_klice(tmp_path):
    path = write(tmp_path, "checks:\n  interface_traffic:\n    tolerance_percent: '-40'\n")
    with pytest.raises(ValueError) as info:
        load_profile(path)
    assert str(info.value) == (
        f"{path}: check 'interface_traffic': volba 'tolerance_percent' "
        "ocekava number, nalezeno str"
    )


def test_checks_boolean_jako_number_je_chyba(tmp_path):
    path = write(tmp_path, "checks:\n  interface_traffic:\n    require_nonzero: 1\n")
    with pytest.raises(ValueError, match="volba 'require_nonzero' ocekava boolean, nalezeno int"):
        load_profile(path)


def test_checks_float_a_int_jsou_oboji_number(tmp_path):
    path = write(
        tmp_path,
        "checks:\n"
        "  interface_traffic:\n    tolerance_percent: -40.5\n"
        "  interface_optics_levels:\n    tolerance_db: 3\n",
    )
    profile = load_profile(path)
    assert profile.checks.options("interface_traffic")["tolerance_percent"] == -40.5
    assert profile.checks.options("interface_optics_levels")["tolerance_db"] == 3


def test_checks_neplatna_severity_je_chyba(tmp_path):
    path = write(tmp_path, "checks:\n  bgp_prefix_counts:\n    severity: warning\n")
    with pytest.raises(ValueError) as info:
        load_profile(path)
    assert str(info.value) == (
        f"{path}: check 'bgp_prefix_counts': severity 'warning' neni platna "
        f"(zname: advisory, critical)"
    )


def test_checks_platna_severity_projde(tmp_path):
    path = write(tmp_path, "checks:\n  bgp_prefix_counts:\n    severity: critical\n")
    profile = load_profile(path)
    assert profile.checks.severity("bgp_prefix_counts", Severity.ADVISORY) == Severity.CRITICAL


def test_checks_enabled_musi_byt_bool(tmp_path):
    path = write(tmp_path, "checks:\n  interface_state:\n    enabled: 'false'\n")
    with pytest.raises(ValueError, match="check 'interface_state': volba 'enabled' ocekava boolean, nalezeno str"):
        load_profile(path)


def test_checks_neznamy_check_a_neznama_volba_nejsou_chyba(tmp_path):
    # GUI je ukaze jako "neznamy check" / read-only k odebrani; loader je
    # nesmi odmitnout, jinak by soubor nesel vycistit bez editoru.
    path = write(
        tmp_path,
        "checks:\n"
        "  old_check:\n    enabled: false\n"
        "  interface_traffic:\n    foo: bar\n",
    )
    profile = load_profile(path)
    assert profile.checks.raw == {
        "old_check": {"enabled": False},
        "interface_traffic": {"foo": "bar"},
    }


def test_checks_prazdny_check_je_prazdny_mapping(tmp_path):
    path = write(tmp_path, "checks:\n  interface_state:\n")
    profile = load_profile(path)
    assert profile.checks.raw == {"interface_state": {}}
    assert profile.checks.enabled("interface_state")


def test_checks_neni_mapping_je_chyba(tmp_path):
    path = write(tmp_path, "checks:\n  - interface_state\n")
    with pytest.raises(ValueError, match="sekce checks: ocekavan mapping, nalezeno list"):
        load_profile(path)
    path = write(tmp_path, "checks:\n  interface_state: [enabled]\n")
    with pytest.raises(ValueError, match="check 'interface_state': ocekavan mapping, nalezeno list"):
        load_profile(path)
