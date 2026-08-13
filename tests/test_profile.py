"""Profil - nadmnozina dnesniho --config YAML."""

import pytest

from migration_validator.config import Profile, default_profile, load_profile


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
