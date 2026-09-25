"""Ansible INI inventar - jen jmeno uzlu a ansible_host.

Skupiny, :vars a :children se preskakuji; rozsahy (mx[01:10]) ani YAML
inventare se nepodporuji. Varovani nesou cislo radku, GUI je ukaze adminovi.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class InventoryHost:
    node: str
    host: str


@dataclass(frozen=True)
class Inventory:
    hosts: list[InventoryHost]
    warnings: list[str]


def _strip_quotes(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
        return value[1:-1]
    return value


def parse_inventory(text: str) -> Inventory:
    found: dict[str, tuple[str, int]] = {}
    warnings: list[str] = []
    in_host_section = True
    for number, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line or line[0] in "#;":
            continue
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1]
            in_host_section = not (section.endswith(":vars") or section.endswith(":children"))
            continue
        if not in_host_section:
            continue
        tokens = line.split()
        node = tokens[0]
        host = None
        for token in tokens[1:]:
            key, sep, value = token.partition("=")
            if sep and key == "ansible_host":
                host = _strip_quotes(value)
        if not host:
            warnings.append(f"line {number}: {node} has no ansible_host")
            continue
        if node in found:
            first_host, first_line = found[node]
            if first_host != host:
                warnings.append(
                    f"line {number}: {node} duplicates line {first_line} with a different host"
                )
            continue
        found[node] = (host, number)
    hosts = sorted(
        (InventoryHost(node, host) for node, (host, _) in found.items()),
        key=lambda h: h.node.lower(),
    )
    return Inventory(hosts=hosts, warnings=warnings)


class InventorySource:
    """Soubor inventare s cache podle (mtime_ns, size, ino) - zmena souboru
    se projevi bez restartu GUI. ino je nutne navic k (mtime_ns, size):
    atomicka vymena (os.replace) muze dopadnout na stejny mtime_ns i size
    (stejne dlouhy obsah, nebo hruba FS hodina / dva zapisy ve stejnem
    tiku) - novy inode ale pozna vzdy, stejne jako u UserStore."""

    def __init__(self, path: Path):
        self.path = Path(path)
        # Jeden (key, inventory) tuple - atomicka vymena, zadne okno mezi
        # aktualizaci klice a dat.
        self._cache: tuple[tuple[int, int, int] | None, Inventory] | None = None

    def load(self) -> Inventory:
        st = self.path.stat()  # OSError kdyz soubor chybi / neni citelny
        key = (st.st_mtime_ns, st.st_size, st.st_ino)
        cache = self._cache
        if cache is None or cache[0] != key:
            text = self.path.read_text(encoding="utf-8", errors="replace")
            cache = (key, parse_inventory(text))
            self._cache = cache
        return cache[1]
