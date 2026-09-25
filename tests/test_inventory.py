import os

import pytest

from migration_validator.inventory import Inventory, InventoryHost, InventorySource, parse_inventory


def test_basic_lines():
    inv = parse_inventory("MX-POP1 ansible_host=172.20.20.4\nPTX-POP1 ansible_host=172.20.20.5\n")
    assert inv.hosts == [InventoryHost("MX-POP1", "172.20.20.4"), InventoryHost("PTX-POP1", "172.20.20.5")]
    assert inv.warnings == []


def test_comments_groups_vars_and_children_skipped():
    text = (
        "# comment\n; other comment\n\n"
        "[pop1]\nMX-POP1 ansible_host=10.0.0.1 ansible_user=x\n"
        "[pop1:vars]\nansible_host=9.9.9.9\nntp=1.1.1.1\n"
        "[all:children]\npop1\n"
        "[pop2]\nPTX-POP2   ansible_host='10.0.0.2'\n"
    )
    inv = parse_inventory(text)
    assert inv.hosts == [InventoryHost("MX-POP1", "10.0.0.1"), InventoryHost("PTX-POP2", "10.0.0.2")]
    assert inv.warnings == []


def test_quotes_crlf_and_trailing_space():
    inv = parse_inventory('A1 ansible_host="10.0.0.1"\r\nB1 ansible_host=10.0.0.2   \r\n')
    assert [h.host for h in inv.hosts] == ["10.0.0.1", "10.0.0.2"]


def test_missing_ansible_host_warns():
    inv = parse_inventory("MX-POP1 ansible_host=10.0.0.1\nLONELY\nNOIP ansible_user=x\n")
    assert [h.node for h in inv.hosts] == ["MX-POP1"]
    assert inv.warnings == ["line 2: LONELY has no ansible_host", "line 3: NOIP has no ansible_host"]


def test_section_header_with_trailing_comment_is_recognised():
    # finding #10a: "[mx] # routers" / "[x:vars] ; note" must still be
    # recognised as a header - before the fix, endswith("]") failed and the
    # ":vars" body below was parsed as host lines.
    text = (
        "[mx] # routers\n"
        "MX-POP1 ansible_host=10.0.0.1\n"
        "[mx:vars] ; note\n"
        "ansible_host=9.9.9.9\n"
        "ntp=1.1.1.1\n"
    )
    inv = parse_inventory(text)
    assert inv.hosts == [InventoryHost("MX-POP1", "10.0.0.1")]
    assert inv.warnings == []


def test_bare_name_with_host_defined_elsewhere_does_not_warn():
    # finding #10b: a bare name that gets its ansible_host from a different
    # group-membership line elsewhere in the file must not warn, but a name
    # that never gets a host anywhere still must.
    text = (
        "[pop1]\n"
        "MX-POP1 ansible_host=10.0.0.1\n"
        "[pop2]\n"
        "MX-POP1\n"
        "GHOST\n"
    )
    inv = parse_inventory(text)
    assert inv.hosts == [InventoryHost("MX-POP1", "10.0.0.1")]
    assert inv.warnings == ["line 5: GHOST has no ansible_host"]


def test_bare_name_before_its_host_definition_does_not_warn():
    text = "MX-POP1\n[pop1]\nMX-POP1 ansible_host=10.0.0.1\n"
    inv = parse_inventory(text)
    assert inv.hosts == [InventoryHost("MX-POP1", "10.0.0.1")]
    assert inv.warnings == []


def test_duplicates():
    inv = parse_inventory(
        "A1 ansible_host=10.0.0.1\nA1 ansible_host=10.0.0.1\nA1 ansible_host=10.0.0.9\n"
    )
    assert inv.hosts == [InventoryHost("A1", "10.0.0.1")]
    assert inv.warnings == ["line 3: A1 duplicates line 1 with a different host"]


def test_hosts_sorted_by_name():
    inv = parse_inventory("b ansible_host=2\nA ansible_host=1\nc ansible_host=3\n")
    assert [h.node for h in inv.hosts] == ["A", "b", "c"]


def test_source_reloads_on_change(tmp_path):
    path = tmp_path / "hosts"
    path.write_text("A1 ansible_host=10.0.0.1\n")
    source = InventorySource(path)
    assert [h.node for h in source.load().hosts] == ["A1"]
    replacement = tmp_path / "hosts.new"
    replacement.write_text("B1 ansible_host=10.0.0.2\n")
    os.replace(replacement, path)  # atomic swap, as Ansible tooling does
    st = path.stat()
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000))
    assert [h.node for h in source.load().hosts] == ["B1"]


def test_source_missing_file_raises(tmp_path):
    with pytest.raises(OSError):
        InventorySource(tmp_path / "nope").load()


def test_source_detects_atomic_swap_with_same_mtime_and_size(tmp_path):
    # finding #3: an atomic os.replace() that lands on the same mtime_ns and
    # the same byte size (same-length replacement content, or a coarse FS
    # clock / two writes in one tick) must still be picked up - key on inode
    # too, as UserStore already does.
    path = tmp_path / "hosts"
    path.write_text("A1 ansible_host=10.0.0.1\n")
    source = InventorySource(path)
    assert [h.node for h in source.load().hosts] == ["A1"]

    st = path.stat()
    replacement = tmp_path / "hosts.new"
    replacement.write_text("A1 ansible_host=10.0.0.9\n")  # same length, new inode
    os.utime(replacement, ns=(st.st_atime_ns, st.st_mtime_ns))
    os.replace(replacement, path)
    assert path.stat().st_mtime_ns == st.st_mtime_ns
    assert path.stat().st_size == st.st_size

    assert [h.host for h in source.load().hosts] == ["10.0.0.9"]
