"""Routes /api/profiles - tenke obaly nad profiles.store a profiles.catalogue.

Cteni vyzaduje view, zapis admin (spec 3). Router se sklada v create_app,
aby dostal store, run_root a serverovy default profil.

Mapovani chyb: nevalidni jmeno / nevalidni dokument (ValueError z loaderu)
-> 422 s hlaskou beze zmeny; chybejici profil -> 404; existujici pri POST a
pouzivany pri DELETE -> 409; I/O chyba store -> 500 s hlaskou OS."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel

from migration_validator.gui.authz import Actor, Permission, require
from migration_validator.gui.profiles import profile_usage, server_default_profile
from migration_validator.profiles.catalogue import build_catalogue
from migration_validator.profiles.store import (
    ProfileStore,
    check_profile_name,
    document_from_profile,
    document_to_yaml,
)


class ProfileBody(BaseModel):
    name: str
    document: dict[str, Any]


class DocumentBody(BaseModel):
    document: dict[str, Any]


def build_profiles_router(
    store: ProfileStore, run_root: Path, profile_path: str | None
) -> APIRouter:
    router = APIRouter(prefix="/api/profiles")

    def _name_or_422(name: str) -> None:
        try:
            check_profile_name(name)
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    def _document_or_error(name: str) -> dict[str, Any]:
        _name_or_422(name)
        try:
            return store.document(name)
        except FileNotFoundError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        except OSError as error:
            raise HTTPException(status_code=500, detail=str(error)) from error

    def _save_or_error(name: str, document: dict[str, Any]) -> dict[str, Any]:
        try:
            store.save(name, document)
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        except OSError as error:
            raise HTTPException(status_code=500, detail=str(error)) from error
        return {"name": name, "document": _document_or_error(name)}

    # Staticke cesty (catalogue, preview) musi byt registrovane pred /{name}.
    @router.get("/catalogue")
    def catalogue(actor: Actor = require(Permission.VIEW)) -> dict:
        return build_catalogue()

    @router.post("/preview")
    def preview(body: DocumentBody, actor: Actor = require(Permission.ADMIN)) -> dict:
        return {"yaml": document_to_yaml(body.document)}

    @router.get("")
    def list_profiles(actor: Actor = require(Permission.VIEW)) -> dict:
        try:
            default = server_default_profile(profile_path)
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        usage = profile_usage(run_root)
        return {
            "default": Path(profile_path).name if profile_path else None,
            "default_document": document_from_profile(default),
            "profiles": [
                {"name": name, "used_by": usage.get(name, 0)} for name in store.list()
            ],
        }

    @router.post("", status_code=201)
    def create_profile(body: ProfileBody, actor: Actor = require(Permission.ADMIN)) -> dict:
        _name_or_422(body.name)
        if store.exists(body.name):
            raise HTTPException(status_code=409, detail=f"profil '{body.name}' uz existuje")
        return _save_or_error(body.name, body.document)

    @router.get("/{name}")
    def get_profile(name: str, actor: Actor = require(Permission.VIEW)) -> dict:
        return {"name": name, "document": _document_or_error(name)}

    @router.put("/{name}")
    def update_profile(
        name: str, body: DocumentBody, actor: Actor = require(Permission.ADMIN)
    ) -> dict:
        _name_or_422(name)
        if not store.exists(name):
            raise HTTPException(
                status_code=404, detail=f"profil '{name}' neexistuje ({store.path(name)})"
            )
        return _save_or_error(name, body.document)

    @router.delete("/{name}", status_code=204)
    def delete_profile(name: str, actor: Actor = require(Permission.ADMIN)) -> Response:
        _name_or_422(name)
        if not store.exists(name):
            raise HTTPException(
                status_code=404, detail=f"profil '{name}' neexistuje ({store.path(name)})"
            )
        used = profile_usage(run_root).get(name, 0)
        if used:
            raise HTTPException(status_code=409, detail=f"profil pouziva {used} runu")
        try:
            store.delete(name)
        except OSError as error:
            raise HTTPException(status_code=500, detail=str(error)) from error
        return Response(status_code=204)

    return router
