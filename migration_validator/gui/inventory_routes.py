"""Vyhledavani v inventari a filtr jmen (spec 2026-09-25, vlna B)."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from migration_validator.gui.audit import record
from migration_validator.gui.authz import Actor, Permission, require
from migration_validator.hostname_filter import FilterStore, is_visible, normalize_patterns
from migration_validator.inventory import InventoryHost, InventorySource

SEARCH_LIMIT = 50


class FilterBody(BaseModel):
    allow: list[str]


def build_inventory_router(inventory: InventorySource | None, filters: FilterStore) -> APIRouter:
    router = APIRouter(prefix="/api/inventory")

    def _hosts() -> tuple[list[InventoryHost], list[str], str | None]:
        """(hosts, warnings, error). Chyba = nectitelny inventar."""
        if inventory is None:
            return [], [], None
        try:
            loaded = inventory.load()
        except OSError as error:
            return [], [], f"inventory {inventory.path}: {error.strerror or error}"
        return loaded.hosts, loaded.warnings, None

    def _state(allow: list[str]) -> dict:
        hosts, warnings, error = _hosts()
        return {
            "allow": allow,
            "visible": sum(1 for h in hosts if is_visible(h.node, allow)),
            "total": len(hosts),
            "warnings": warnings,
            "enabled": inventory is not None,
            "error": error,
        }

    def _load_filter() -> tuple[list[str], str | None]:
        """(allow, error). Chyba = nectitelny nebo neplatny filtr."""
        try:
            return filters.load(), None
        except ValueError as error:
            return [], str(error)
        except OSError as error:
            return [], f"{filters.path}: {error.strerror or error}"

    @router.get("")
    def search(q: str = "", actor: Actor = require(Permission.VIEW)) -> dict:
        hosts, _, error = _hosts()
        if error is None:
            allow, error = _load_filter()
        if error is not None:
            return {"items": [], "total": 0, "enabled": inventory is not None, "error": error}
        needle = q.strip().lower()
        matches = [h for h in hosts if is_visible(h.node, allow) and needle in h.node.lower()]
        return {
            "items": [{"node": h.node, "host": h.host} for h in matches[:SEARCH_LIMIT]],
            "total": len(matches),
            "enabled": inventory is not None,
            "error": None,
        }

    @router.get("/filter")
    def get_filter(actor: Actor = require(Permission.VIEW)) -> dict:
        allow, error = _load_filter()
        state = _state(allow)
        if error is not None:
            state["error"] = error
        return state

    @router.put("/filter")
    def put_filter(body: FilterBody, dry_run: bool = False, actor: Actor = require(Permission.ADMIN)) -> dict:
        try:
            allow = normalize_patterns(body.allow)
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        if not dry_run:
            try:
                filters.save(allow)
            except OSError as error:
                raise HTTPException(
                    status_code=500,
                    detail=f"hostname filter cannot be saved ({filters.path}): {error.strerror or error}",
                ) from error
            record(actor.username, "filter-edit", target="hostname_filter", allow=",".join(allow))
        return _state(allow)

    return router
