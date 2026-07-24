import pytest

from migration_validator.models.result import (
    Finding,
    Outcome,
    Severity,
    Status,
    derive_status,
)


@pytest.mark.parametrize(
    "outcome,severity,expected",
    [
        (Outcome.OK, Severity.CRITICAL, Status.PASS),
        (Outcome.OK, Severity.ADVISORY, Status.PASS),
        (Outcome.DEGRADED, Severity.CRITICAL, Status.WARN),
        (Outcome.DEGRADED, Severity.ADVISORY, Status.WARN),
        (Outcome.BROKEN, Severity.CRITICAL, Status.FAIL),
        (Outcome.BROKEN, Severity.ADVISORY, Status.WARN),
        (Outcome.SKIP, Severity.CRITICAL, Status.SKIP),
        (Outcome.SKIP, Severity.ADVISORY, Status.SKIP),
    ],
)
def test_derive_status(outcome, severity, expected):
    assert derive_status(outcome, severity) is expected


def test_degraded_is_warn_even_when_critical():
    """Castecny uspech je vzdy WARN - pravidlo je v jednom miste, ne v checcich."""
    assert derive_status(Outcome.DEGRADED, Severity.CRITICAL) is Status.WARN


def test_status_worst_ranks_skip_above_pass():
    assert Status.worst([Status.PASS, Status.SKIP]) is Status.SKIP
    assert Status.worst([Status.PASS, Status.WARN, Status.SKIP]) is Status.WARN
    assert Status.worst([Status.WARN, Status.FAIL]) is Status.FAIL
    assert Status.worst([]) is Status.SKIP


def test_finding_defaults():
    finding = Finding(outcome=Outcome.OK, message="vse ok")
    assert finding.label is None
    assert finding.details == {}
