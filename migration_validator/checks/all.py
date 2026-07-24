"""Import vsech modulu s checky, aby se zaregistrovaly do registry.

Je to samostatny modul (ne __init__.py), aby nevznikl cyklicky import:
checks.ifaces importuje checks.base, takze __init__ nesmi importovat ifaces.
"""

from __future__ import annotations

from migration_validator.checks import bgp, evpn, ifaces, reachability  # noqa: F401

_LOADED = True


def load_all() -> None:
    """Idempotentni - import na urovni modulu uz probehl."""
    assert _LOADED
