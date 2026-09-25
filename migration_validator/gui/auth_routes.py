"""/api/login a /api/logout - jedine /api routy bez require()."""

from __future__ import annotations

import logging
from dataclasses import replace

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from migration_validator.gui import audit
from migration_validator.gui.sessions import SESSION_COOKIE, LoginThrottle, SessionStore
from migration_validator.users import (
    AuthenticationError,
    UserStore,
    dummy_hash,
    hash_password,
    needs_rehash,
    verify_password,
)

INVALID_LOGIN = "invalid username or password"

log = logging.getLogger(__name__)


class LoginBody(BaseModel):
    username: str
    password: str


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _cookie_kwargs() -> dict:
    return {"httponly": True, "secure": True, "samesite": "strict", "path": "/"}


def build_auth_router(users: UserStore, sessions: SessionStore, throttle: LoginThrottle) -> APIRouter:
    router = APIRouter(prefix="/api")

    @router.post("/login")
    def login(body: LoginBody, request: Request) -> JSONResponse:
        ip = _client_ip(request)
        wait = throttle.retry_after(body.username, ip)
        if wait:
            raise HTTPException(status_code=429, detail=f"too many failed logins, try again in {wait} s")
        user = users.get(body.username)
        stored = user.password_hash if user else dummy_hash()
        password_ok = verify_password(body.password[:2048], stored)
        if user is None or not password_ok:
            throttle.failure(body.username, ip)
            audit.record(body.username, "login-failed", ip=ip)
            raise HTTPException(status_code=401, detail=INVALID_LOGIN)
        throttle.success(body.username, ip)
        session_hash = user.password_hash
        if needs_rehash(user.password_hash):
            try:
                current = users.load()
                fresh = current.get(user.username)
                # Mezi get() (pred pomalym verify_password) a timto zapisem
                # mohl CLI ucet smazat nebo zmenit - pisme jen kdyz fresh
                # zaznam porad odpovida heslu, ktere jsme prave overili.
                if fresh is not None and fresh.password_hash == user.password_hash:
                    session_hash = hash_password(body.password)
                    current[user.username] = replace(fresh, password_hash=session_hash)
                    users.save(current)
            except (AuthenticationError, OSError) as error:
                # login still succeeds; rehash retried next time
                log.warning("rehash hesla pro '%s' selhal: %s", user.username, error)
                session_hash = user.password_hash
        # Session musi nest hash, ktery je PRAVE TED aktualni (po pripadnem
        # rehashi) - jinak by prihlaseni-s-rehashem samo sebe hned zneplatnilo
        # (provider by porovnal starou session hash s novym souborem).
        token = sessions.create(user.username, password_hash=session_hash)
        audit.record(user.username, "login", ip=ip)
        response = JSONResponse({"username": user.username, "role": user.role})
        response.set_cookie(SESSION_COOKIE, token, **_cookie_kwargs())
        return response

    @router.post("/logout", status_code=204)
    def logout(request: Request) -> Response:
        token = request.cookies.get(SESSION_COOKIE)
        if token:
            session = sessions.lookup(token)
            sessions.drop(token)
            if session is not None:
                audit.record(session.username, "logout")
        response = Response(status_code=204)
        response.delete_cookie(SESSION_COOKIE, **_cookie_kwargs())
        return response

    return router
