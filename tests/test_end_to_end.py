import json
from pathlib import Path

from migration_validator import api
from migration_validator.models.result import Status
from migration_validator.reporting.json_report import to_json
from migration_validator.reporting.text_report import filter_result, render

NOW = "2026-07-24T11:40:02Z"

FIXTURES = Path(__file__).resolve().parent / "fixtures"
DEVICE_4 = str(FIXTURES / "172.20.20.4.yml")
DEVICE_5 = str(FIXTURES / "172.20.20.5.yml")


def test_full_migration_run_is_green(synthetic_snapshot):
    old = synthetic_snapshot(DEVICE_4, "172.20.20.4", "pre-migration")
    new = synthetic_snapshot(DEVICE_5, "172.20.20.5", "post-migration")

    result = api.evaluate(new, baseline=old, now=NOW)

    assert result.summary["fail"] == 0
    # Ne jen "nic neselhalo" - "clean migration" znamena i zadne trvale
    # varovani. Bez tohohle by check, ktery je vzdy WARN na zdrave sluzbe,
    # prosel tichem stejne jako FAIL.
    assert result.summary["warn"] == 0
    assert result.summary["scopes_matched"] >= 5
    assert json.loads(to_json(result))["schema_version"] == 1


def test_evpn_checks_produce_real_verdicts_on_real_data(synthetic_snapshot):
    """Guards against the EVPN fact-schema silently miskeying into all-SKIP."""
    old = synthetic_snapshot(DEVICE_4, "172.20.20.4", "pre-migration")
    new = synthetic_snapshot(DEVICE_5, "172.20.20.5", "post-migration")

    result = api.evaluate(new, baseline=old, now=NOW)

    all_checks = [check for scope in result.scopes for check in scope.checks]

    for check_id in ("evpn_vpws_status", "evpn_esi_status", "evpn_mac_count"):
        matching = [check for check in all_checks if check.id == check_id]
        assert any(check.status is not Status.SKIP for check in matching), (
            f"expected at least one non-SKIP result for {check_id!r}"
        )


def test_service_without_ipv6_has_no_ipv6_section(synthetic_snapshot):
    """Rozhodnuti R-1 (varianta c) na skutecnych datech, ne na fixture.

    Sluzba bez nakonfigurovane IPv6 nedostane v bloku ani sekci IPv6, ani
    radek o ni. Drive si SKIP z `nd_present`, ktery svou rodinu znackoval
    i u nenakonfigurovane rodiny, prazdnou sekci vynutil.

    Chytit to jde jen tudy: renderer dostane sekci jen tehdy, kdyz nejaky
    check nese family=6, takze rucne slozena fixture bez takoveho checku
    projde vzdycky - at uz je chovani checku jakekoliv.
    """
    new = synthetic_snapshot(DEVICE_5, "172.20.20.5", "post-migration")
    result = api.evaluate(new, now=NOW)

    ipv4_only = [
        scope
        for scope in result.scopes
        if scope.identity.get("ipv4") and not scope.identity.get("ipv6")
    ]
    assert ipv4_only, "fixture nema zadnou sluzbu jen s IPv4 - test by nic neoveril"

    for scope in ipv4_only:
        assert not [check for check in scope.checks if check.family == 6], (
            f"{scope.scope_id} nema IPv6, presto nese check s family=6"
        )

    rendered = render(result, detail=True)
    for scope in ipv4_only:
        block = _block_of(
            rendered, scope.identity["description"], scope.identity["service_type"]
        )
        assert "-- IPv6" not in block


def _block_of(rendered: str, description: str, service_type: str) -> str:
    """Vyrizne blok jedne sluzby. Bloky oddeluji cary ze samych '='.

    Krajet podle `rendered.split(description)[-1]` nestaci: vratilo by to
    zbytek hlavickoveho radku posledniho vyskytu, tedy nikdy nic, co
    zacina '-- IPv6'. Takova kontrola by nemohla selhat, at se kod chova
    jakkoliv - a hollow test je presne to, co tenhle test opravoval.

    Samotny popis taky nestaci: v laborce nesou dve ruzne sluzby popis
    EVPN-VLAN-AWARE-INTERNET a lisi se az typem (E-LAN vs Internet).
    """
    lines = rendered.splitlines()
    starts = [i for i, line in enumerate(lines) if line and set(line) == {"="}]

    blocks = []
    for index in starts:
        header = lines[index + 1] if index + 1 < len(lines) else ""
        if description not in header or service_type not in header:
            continue
        # Blok ma dve cary '=': nad hlavickou a pod ni. Dalsi blok proto
        # zacina az tou treti - hledat od index+1 by useklo vyrez hned za
        # hlavickou a telo bloku by se vubec nemerilo.
        rest = [i for i in starts if i > index + 2]
        end = rest[0] if rest else lines.index("NESPAROVANO")
        blocks.append("\n".join(lines[index:end]))

    assert len(blocks) == 1, (
        f"ocekavan 1 blok pro {description!r}/{service_type!r}, je jich {len(blocks)}"
    )
    # Bez tohohle by prazdny nebo useknuty vyrez prosel jako "IPv6 tam neni".
    assert " STAV |" in blocks[0], "vyrez neobsahuje telo bloku"
    return blocks[0]


def test_traffic_drop_on_new_device_is_detected(synthetic_snapshot):
    old = synthetic_snapshot(DEVICE_4, "172.20.20.4", "pre-migration", pps=400)
    new = synthetic_snapshot(DEVICE_5, "172.20.20.5", "post-migration", pps=50)

    result = api.evaluate(new, baseline=old, now=NOW)

    traffic = [
        check
        for scope in result.scopes
        for check in scope.checks
        if check.id == "interface_traffic"
    ]
    assert any(check.status is Status.WARN for check in traffic)


def test_new_elan_service_shows_up_as_unmatched(synthetic_snapshot):
    old = synthetic_snapshot(DEVICE_4, "172.20.20.4", "pre-migration")
    new = synthetic_snapshot(DEVICE_5, "172.20.20.5", "post-migration")

    result = api.evaluate(new, baseline=old, now=NOW)

    subject_ids = {item["scope_id"] for item in result.unmatched["subject"]}
    assert any("EVPN-VLAN-AWARE-INTERNET" in scope_id for scope_id in subject_ids)


def test_management_interfaces_never_appear(synthetic_snapshot):
    new = synthetic_snapshot(DEVICE_5, "172.20.20.5", "post-migration")

    result = api.evaluate(new, now=NOW)
    rendered = render(result)

    assert "fxp0" not in rendered
    assert "mgmt" not in rendered


def test_single_snapshot_validation_skips_comparison_checks(synthetic_snapshot):
    old = synthetic_snapshot(DEVICE_4, "172.20.20.4", "pre-migration")

    result = api.evaluate(old, now=NOW)

    prefix_checks = [
        check
        for scope in result.scopes
        for check in scope.checks
        if check.id == "bgp_prefix_counts"
    ]
    assert prefix_checks
    assert all(check.status is Status.SKIP for check in prefix_checks)


def test_render_after_filter_still_shows_unmatched_section(synthetic_snapshot):
    """NESPAROVANO je pojistka: i kdyz filtr smaze vsechny scopy, sekce s
    nezmigrovanymi sluzbami se musi vykreslit dal - to je hlavni bod, proti
    kteremu tenhle task testuje.
    """
    old = synthetic_snapshot(DEVICE_4, "172.20.20.4", "pre-migration")
    new = synthetic_snapshot(DEVICE_5, "172.20.20.5", "post-migration")

    result = api.evaluate(new, baseline=old, now=NOW)
    assert result.unmatched["subject"] or result.unmatched["baseline"]

    filtered = filter_result(result, text="no-such-scope-matches-this-needle")
    assert filtered.scopes == []

    rendered = render(filtered)

    assert "NESPAROVANO" in rendered
    unmatched_labels = {
        item.get("description") or item["scope_id"]
        for side in ("baseline", "subject")
        for item in filtered.unmatched[side]
    }
    assert any(label in rendered for label in unmatched_labels)
