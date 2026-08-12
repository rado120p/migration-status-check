from migration_validator.models.inventory import ServiceEntry
from migration_validator.models.scope import Selectors


def test_selectors_lag_members_roundtrip():
    selectors = Selectors(interfaces=["ae0"], lag_members=["et-0/0/5", "et-0/0/6"])
    assert Selectors.from_dict(selectors.to_dict()).lag_members == [
        "et-0/0/5", "et-0/0/6"]


def test_selectors_stary_dict_bez_pole_se_nacte():
    assert Selectors.from_dict({"interfaces": ["ae0"]}).lag_members == []


def test_service_entry_lag_members_roundtrip():
    entry = ServiceEntry.from_dict({
        "interface": "ae0", "service_type": "Layer1",
        "lag_members": ["et-0/0/5"],
    })
    assert entry.lag_members == ["et-0/0/5"]
    assert entry.to_dict()["lag_members"] == ["et-0/0/5"]


def test_service_entry_bez_pole_ma_prazdny_seznam():
    entry = ServiceEntry.from_dict({"interface": "ae0", "service_type": "Layer1"})
    assert entry.lag_members == []
