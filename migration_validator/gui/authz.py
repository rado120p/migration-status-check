"""Permission seam GUI - dnes je kazdy anonymni admin.

Budouci model roli (admin / operator / viewer) vymeni jen actor provider
v app.state; routy deklaruji, jake opravneni potrebuji, a nemeni se."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Callable

from fastapi import Depends, HTTPException, Request


class Permission(str, Enum):
    VIEW = "view"
    OPERATE = "operate"
    ADMIN = "admin"


@dataclass(frozen=True)
class Actor:
    role: str


_ROLE_RANK = {"viewer": 0, "operator": 1, "admin": 2}
_NEEDED_RANK = {Permission.VIEW: 0, Permission.OPERATE: 1, Permission.ADMIN: 2}

ActorProvider = Callable[[Request], Actor]


def anonymous_admin(request: Request) -> Actor:
    return Actor(role="admin")


def current_actor(request: Request) -> Actor:
    provider: ActorProvider = getattr(
        request.app.state, "actor_provider", anonymous_admin
    )
    return provider(request)


def allows(actor: Actor, permission: Permission) -> bool:
    return _ROLE_RANK.get(actor.role, -1) >= _NEEDED_RANK[permission]


def require(permission: Permission):
    """Pouziti: `def route(actor: Actor = require(Permission.ADMIN))`."""

    def dependency(actor: Actor = Depends(current_actor)) -> Actor:
        if not allows(actor, permission):
            raise HTTPException(
                status_code=403,
                detail=f"nedostatecne opravneni: vyzaduje {permission.value}",
            )
        return actor

    return Depends(dependency)
