import json

from migration_validator import api
from migration_validator.models.result import Status
from migration_validator.reporting.json_report import to_json
from migration_validator.reporting.text_report import filter_result, render

NOW = "2026-07-24T11:40:02Z"


def test_full_migration_run_is_green(synthetic_snapshot):
    old = synthetic_snapshot("172.20.20.4.yml", "172.20.20.4", "pre-migration")
    new = synthetic_snapshot("172.20.20.5.yml", "172.20.20.5", "post-migration")

    result = api.evaluate(new, baseline=old, now=NOW)

    assert result.summary["fail"] == 0
    assert result.summary["scopes_matched"] >= 5
    assert json.loads(to_json(result))["schema_version"] == 1


def test_traffic_drop_on_new_device_is_detected(synthetic_snapshot):
    old = synthetic_snapshot("172.20.20.4.yml", "172.20.20.4", "pre-migration", pps=400)
    new = synthetic_snapshot("172.20.20.5.yml", "172.20.20.5", "post-migration", pps=50)

    result = api.evaluate(new, baseline=old, now=NOW)

    traffic = [
        check
        for scope in result.scopes
        for check in scope.checks
        if check.id == "interface_traffic"
    ]
    assert any(check.status is Status.WARN for check in traffic)


def test_new_elan_service_shows_up_as_unmatched(synthetic_snapshot):
    old = synthetic_snapshot("172.20.20.4.yml", "172.20.20.4", "pre-migration")
    new = synthetic_snapshot("172.20.20.5.yml", "172.20.20.5", "post-migration")

    result = api.evaluate(new, baseline=old, now=NOW)

    subject_ids = {item["scope_id"] for item in result.unmatched["subject"]}
    assert any("EVPN-VLAN-AWARE-INTERNET" in scope_id for scope_id in subject_ids)


def test_management_interfaces_never_appear(synthetic_snapshot):
    new = synthetic_snapshot("172.20.20.5.yml", "172.20.20.5", "post-migration")

    result = api.evaluate(new, now=NOW)
    rendered = render(result)

    assert "fxp0" not in rendered
    assert "mgmt" not in rendered


def test_single_snapshot_validation_skips_comparison_checks(synthetic_snapshot):
    old = synthetic_snapshot("172.20.20.4.yml", "172.20.20.4", "pre-migration")

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
    old = synthetic_snapshot("172.20.20.4.yml", "172.20.20.4", "pre-migration")
    new = synthetic_snapshot("172.20.20.5.yml", "172.20.20.5", "post-migration")

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
