from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from collections import defaultdict, deque
from dataclasses import dataclass

from fastapi import HTTPException, Request, Response, status

from app.config import Settings

COOKIE_NAME = "hidden_tower_operator"
CSRF_HEADER = "x-csrf-token"


@dataclass(frozen=True)
class OperatorSession:
    expires_at: int
    csrf_token: str
    session_id: str


class RateLimiter:
    def __init__(self, attempts: int, window_seconds: int) -> None:
        self._attempts = attempts
        self._window_seconds = window_seconds
        self._entries: dict[str, deque[float]] = defaultdict(deque)

    def check(self, key: str) -> None:
        now = time.monotonic()
        entries = self._entries[key]
        while entries and entries[0] <= now - self._window_seconds:
            entries.popleft()
        if len(entries) >= self._attempts:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many operator requests",
            )
        entries.append(now)
        if len(self._entries) > 10_000:
            self._entries = defaultdict(
                deque, {entry_key: value for entry_key, value in self._entries.items() if value}
            )


class OperatorSessionManager:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        configured = settings.operator_token
        if settings.environment == "production" and configured is None:
            raise RuntimeError("OPERATOR_TOKEN is required in production")
        self._operator_token = (
            configured.get_secret_value() if configured else "local-operator"
        )
        key_material = (
            configured.get_secret_value().encode()
            if configured
            else secrets.token_bytes(32)
        )
        self._signing_key = hashlib.sha256(
            b"hidden-tower-operator-session:" + key_material
        ).digest()
        self.login_limiter = RateLimiter(
            settings.operator_login_attempts,
            settings.operator_rate_window_seconds,
        )
        self.mutation_limiter = RateLimiter(
            settings.operator_login_attempts * 4,
            settings.operator_rate_window_seconds,
        )

    def login(self, supplied_token: str) -> tuple[str, OperatorSession]:
        if not hmac.compare_digest(supplied_token, self._operator_token):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid operator credentials",
            )
        session = OperatorSession(
            expires_at=int(time.time()) + self._settings.operator_session_seconds,
            csrf_token=secrets.token_urlsafe(24),
            session_id=secrets.token_urlsafe(16),
        )
        payload = {
            "exp": session.expires_at,
            "csrf": session.csrf_token,
            "sid": session.session_id,
        }
        encoded = base64.urlsafe_b64encode(
            json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
        ).decode()
        signature = hmac.new(
            self._signing_key, encoded.encode(), hashlib.sha256
        ).hexdigest()
        return f"{encoded}.{signature}", session

    def verify(self, value: str | None) -> OperatorSession:
        if not value or "." not in value:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Operator session required",
            )
        encoded, signature = value.rsplit(".", 1)
        expected = hmac.new(
            self._signing_key, encoded.encode(), hashlib.sha256
        ).hexdigest()
        if not hmac.compare_digest(signature, expected):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid operator session",
            )
        try:
            payload = json.loads(base64.urlsafe_b64decode(encoded.encode()))
            session = OperatorSession(
                expires_at=int(payload["exp"]),
                csrf_token=str(payload["csrf"]),
                session_id=str(payload["sid"]),
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid operator session",
            ) from error
        if session.expires_at <= int(time.time()):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Operator session expired",
            )
        return session

    def require_mutation(self, request: Request) -> OperatorSession:
        session = self.verify(request.cookies.get(COOKIE_NAME))
        supplied_csrf = request.headers.get(CSRF_HEADER)
        if not supplied_csrf or not hmac.compare_digest(
            supplied_csrf, session.csrf_token
        ):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="CSRF validation failed",
            )
        self.mutation_limiter.check(f"{self._client_key(request)}:{session.session_id}")
        return session

    def set_cookie(self, response: Response, value: str) -> None:
        response.set_cookie(
            COOKIE_NAME,
            value,
            max_age=self._settings.operator_session_seconds,
            httponly=True,
            secure=self._settings.environment == "production",
            samesite="strict",
            path="/",
        )

    @staticmethod
    def clear_cookie(response: Response) -> None:
        response.delete_cookie(COOKIE_NAME, path="/", samesite="strict")

    @staticmethod
    def _client_key(request: Request) -> str:
        return request.client.host if request.client else "unknown"
