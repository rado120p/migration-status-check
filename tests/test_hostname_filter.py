import pytest
import yaml

from migration_validator.hostname_filter import (
    FilterStore,
    MemoryFilterStore,
    is_visible,
    normalize_patterns,
)


@pytest.mark.parametrize("node, patterns, expected", [
    ("MX-POP1", ["MX-*"], True),
    ("mx-pop1", ["MX-*"], True),          # case-insensitive
    ("SOMEMX-1", ["MX-*"], False),        # whole name, not contains
    ("PTX-POP2", ["MX-*", "PTX-*"], True),
    ("CORE1", ["*POP*"], False),
    ("ACX-POP9", ["*POP*"], True),
    ("ANY", [], True),                    # empty filter = everything
])
def test_is_visible(node, patterns, expected):
    assert is_visible(node, patterns) is expected


def test_normalize_strips_and_dedups():
    assert normalize_patterns([" MX-* ", "PTX-*", "MX-*"]) == ["MX-*", "PTX-*"]


@pytest.mark.parametrize("raw", [[""], ["  "], ["MX-*", ""], [1], [None]])
def test_normalize_rejects_empty_and_non_string(raw):
    with pytest.raises(ValueError):
        normalize_patterns(raw)


def test_store_round_trip(tmp_path):
    store = FilterStore(tmp_path / "config" / "hostname_filter.yml")
    assert store.load() == []
    store.save(["MX-*", "PTX-*"])
    assert yaml.safe_load(store.path.read_text()) == {"allow": ["MX-*", "PTX-*"]}
    assert store.load() == ["MX-*", "PTX-*"]
    assert sorted(p.name for p in store.path.parent.iterdir()) == ["hostname_filter.yml"]


@pytest.mark.parametrize("content", ["allow: MX-*\n", "- MX-*\n", "allow:\n  - ''\n", "allow: [unterminated\n"])
def test_store_malformed_raises(tmp_path, content):
    path = tmp_path / "hostname_filter.yml"
    path.write_text(content)
    with pytest.raises(ValueError, match="hostname_filter.yml"):
        FilterStore(path).load()


def test_store_empty_file(tmp_path):
    path = tmp_path / "hostname_filter.yml"
    path.write_text("")
    assert FilterStore(path).load() == []


def test_memory_filter_store_round_trip_touches_no_disk(tmp_path, monkeypatch):
    # finding #1: create_app(hostname_filter=None) must never touch real
    # config/ - a memory-backed store proves the round trip without a path.
    monkeypatch.chdir(tmp_path)
    store = MemoryFilterStore()
    assert store.load() == []
    store.save(["MX-*", "PTX-*"])
    assert store.load() == ["MX-*", "PTX-*"]
    assert not (tmp_path / "config").exists()


def test_memory_filter_store_has_descriptive_path():
    assert "in-memory" in str(MemoryFilterStore().path).lower()
