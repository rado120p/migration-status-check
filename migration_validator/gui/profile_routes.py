"""Routes /api/profiles - tenke obaly nad profiles.store a profiles.catalogue.

Cteni vyzaduje view, zapis admin (spec 3). Router se sklada v create_app,
aby dostal store, run_root a serverovy default profil."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter

from migration_validator.gui.authz import Actor, Permission, require
from migration_validator.profiles.catalogue import build_catalogue
from migration_validator.profiles.store import ProfileStore


def build_profiles_router(
    store: ProfileStore, run_root: Path, profile_path: str | None
) -> APIRouter:
    router = APIRouter(prefix="/api/profiles")

    # Staticke cesty (catalogue, preview) musi byt registrovane pred /{name}.
    @router.get("/catalogue")
    def catalogue(actor: Actor = require(Permission.VIEW)) -> dict:
        return build_catalogue()

    return router
