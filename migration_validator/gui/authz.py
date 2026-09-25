"""Permission seam GUI - role se cte ze session cookie pres UserStore.

Bez users souboru (testy, vlozene pouziti) je anonymni volajici admin;
`mig-validate gui` tuto vychozi hodnotu nikdy nepouzije - bez uzivatelu
odmitne start. Routy deklaruji, jake opravneni potrebuji, a nemeni se."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Callable

from fastapi import Depends, HTTPException, Request

from migration_validator.gui.sessions import SESSION_COOKIE, SessionStore
from migration_validator.users import UserStore


class Permission(str, Enum):
    VIEW = "view"
    OPERATE = "operate"
    ADMIN = "admin"


@dataclass(frozen=True)
class Actor:
    role: str
    username: str = "anonymous"


_ROLE_RANK = {"viewer": 0, "operator": 1, "admin": 2}
_NEEDED_RANK = {Permission.VIEW: 0, Permission.OPERATE: 1, Permission.ADMIN: 2}

ActorProvider = Callable[[Request], "Actor | None"]


def anonymous_admin(request: Request) -> Actor:
    """Bez users souboru (testy, vlozene pouziti). `mig-validate gui` ho
    nikdy nepouzije - bez uzivatelu odmitne start."""
    return Actor(role="admin")


def session_actor_provider(sessions: SessionStore, users: UserStore) -> ActorProvider:
    """Role se cte z users souboru pri kazdem requestu - CLI zmena role
    nebo smazani uctu plati hned."""

    def provider(request: Request) -> Actor | None:
        token = request.cookies.get(SESSION_COOKIE)
        if not token:
            return None
        session = sessions.lookup(token)
        if session is None:
            return None
        user = users.get(session.username)
        # password_hash mismatch = `user passwd` changed it since login -
        # the old session must not keep working (finding #3).
        if user is None or user.password_hash != session.password_hash:
            sessions.drop(token)
            return None
        return Actor(role=user.role, username=user.username)

    return provider


def current_actor(request: Request) -> Actor | None:
    provider: ActorProvider = getattr(
        request.app.state, "actor_provider", anonymous_admin
    )
    return provider(request)


def allows(actor: Actor, permission: Permission) -> bool:
    return _ROLE_RANK.get(actor.role, -1) >= _NEEDED_RANK[permission]


def permissions_for(role: str) -> list[str]:
    actor = Actor(role=role)
    return [p.value for p in Permission if allows(actor, p)]


def require(permission: Permission):
    """Pouziti: `def route(actor: Actor = require(Permission.ADMIN))`."""

    def dependency(actor: Actor | None = Depends(current_actor)) -> Actor:
        if actor is None:
            raise HTTPException(status_code=401, detail="login required")
        if not allows(actor, permission):
            raise HTTPException(
                status_code=403,
                detail=f"nedostatecne opravneni: vyzaduje {permission.value}",
            )
        return actor

    return Depends(dependency)
