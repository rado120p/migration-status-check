"""Laboratorni bundly (MX1-POP1, PTX1-POP1, nahrano 2026-09-23 planem
raw retention, Task 14): replay musi udelat presne tataz volani jako zivy
capture - zadny miss, zadne volani navic.

Kdyz collector zmeni RPC nebo jeho argumenty, test spadne. Pak se bundly
nahraji z laborky znovu (Task 14 planu 2026-09-23-raw-retention-a-upgrade)
- jinak by upgrade starsich capture tise hlasil 'neni v raw zaznamu'.

authentication-key hodnoty v konfiguraci fixtures jsou nahrazeny literalem
REDACTED.
"""

from pathlib import Path

import pytest

from migration_validator.capture import capture_device
from migration_validator.connection.junos import detect_platform
from migration_validator.models.inventory import load_inventory
from migration_validator.raw.bundle import read_session
from migration_validator.raw.recorder import RecordingDevice, SessionRecording
from migration_validator.raw.replay import ReplayDevice
from migration_validator.runs.services import generate_inventory

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "raw"


def _shape(calls):
    return [
        (call.rpc, call.kwargs, call.filter, call.error["type"] if call.error else None)
        for call in calls
    ]


@pytest.mark.parametrize("platform", ["junos", "junos-evo"])
def test_replay_makes_exactly_the_recorded_calls(platform, tmp_path):
    bundle = FIXTURES / platform
    session = read_session(bundle)
    inventory_session = read_session(bundle / "inventory")

    inventory_recording = SessionRecording()
    inventory_device = RecordingDevice(ReplayDevice(inventory_session), inventory_recording)
    inventory_path = tmp_path / "inventory.yml"
    generate_inventory(
        inventory_device, detect_platform(inventory_device), inventory_path,
        inventory_session.port_filter,
    )
    assert _shape(inventory_recording.calls) == _shape(inventory_session.calls)

    recording = SessionRecording()
    params = session.params
    capture_device(
        RecordingDevice(ReplayDevice(session), recording),
        session.address,
        inventory=load_inventory(inventory_path),
        collector_names=params["collectors"],
        phase=params["phase"],
        ping_count=params["ping_count"],
        now=session.started_at,
        finished_at=session.finished_at,
        service_types=params["service_types"],
        profile_name=params["profile_name"],
    )
    assert _shape(recording.calls) == _shape(session.calls)
    assert all(call.error is None or call.error["type"] != "NotRecorded" for call in recording.calls)
