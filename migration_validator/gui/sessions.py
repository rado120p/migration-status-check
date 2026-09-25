"""Prihlasene session GUI - v pameti procesu (restart = vsichni se
prihlasi znovu, rozhodnuto ve specu 2026-09-25)."""

from __future__ import annotations

import math
import secrets
import threading
import time
from dataclasses import dataclass
from typing import Callable

SESSION_COOKIE = "mig_session"
IDLE_TIMEOUT_S = 8 * 3600
ABSOLUTE_TIMEOUT_S = 12 * 3600
LOCKOUT_THRESHOLD = 5
LOCKOUT_S = 300


@dataclass
class _Session:
    username: str
    created_at: float
    last_seen: float
    password_hash: str | None = None


class SessionStore:
    def __init__(
        self,
        clock: Callable[[], float] = time.monotonic,
        idle: float = IDLE_TIMEOUT_S,
        absolute: float = ABSOLUTE_TIMEOUT_S,
    ):
        self._clock = clock
        self._idle = idle
        self._absolute = absolute
        self._sessions: dict[str, _Session] = {}
        self._lock = threading.Lock()

    def _expired(self, session: _Session, now: float) -> bool:
        return now - session.last_seen > self._idle or now - session.created_at > self._absolute

    def create(self, username: str, *, password_hash: str | None = None) -> str:
        token = secrets.token_urlsafe(32)
        now = self._clock()
        with self._lock:
            for key in [k for k, s in self._sessions.items() if self._expired(s, now)]:
                del self._sessions[key]
            self._sessions[token] = _Session(username, now, now, password_hash)
        return token

    def lookup(self, token: str) -> _Session | None:
        """Vraci cely zaznam session (username + password_hash v okamziku
        prihlaseni), aby volajici mohl poznat, ze `user passwd` mezitim
        zmenil heslo a stara session uz neplati."""
        now = self._clock()
        with self._lock:
            session = self._sessions.get(token)
            if session is None:
                return None
            if self._expired(session, now):
                del self._sessions[token]
                return None
            session.last_seen = now
            return session

    def drop(self, token: str) -> None:
        with self._lock:
            self._sessions.pop(token, None)


@dataclass
class _Failures:
    count: int
    last_failure: float
    locked_until: float = 0.0


class LoginThrottle:
    """5 neuspechu za sebou pro (jmeno, IP) -> zamek na 5 minut."""

    def __init__(
        self,
        clock: Callable[[], float] = time.monotonic,
        threshold: int = LOCKOUT_THRESHOLD,
        lockout: float = LOCKOUT_S,
    ):
        self._clock = clock
        self._threshold = threshold
        self._lockout = lockout
        self._entries: dict[tuple[str, str], _Failures] = {}
        self._lock = threading.Lock()

    def retry_after(self, username: str, ip: str) -> int:
        now = self._clock()
        with self._lock:
            entry = self._entries.get((username, ip))
            if entry is None or entry.locked_until <= now:
                return 0
            return math.ceil(entry.locked_until - now)

    def failure(self, username: str, ip: str) -> None:
        now = self._clock()
        with self._lock:
            if len(self._entries) > 10_000:
                # Ochrana pameti proti zaplave ruznych jmen.
                stale = [k for k, e in self._entries.items()
                         if e.locked_until <= now and now - e.last_failure > self._lockout]
                for key in stale:
                    del self._entries[key]
            entry = self._entries.get((username, ip))
            if entry is None or (entry.locked_until and entry.locked_until <= now) \
                    or now - entry.last_failure > self._lockout:
                entry = _Failures(count=0, last_failure=now)
                self._entries[(username, ip)] = entry
            entry.count += 1
            entry.last_failure = now
            if entry.count >= self._threshold:
                entry.locked_until = now + self._lockout
                entry.count = 0

    def success(self, username: str, ip: str) -> None:
        with self._lock:
            self._entries.pop((username, ip), None)
