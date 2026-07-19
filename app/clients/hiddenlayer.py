from __future__ import annotations

import asyncio
import time
from typing import Any

import httpx

from app.config import Settings
from app.models import ScanResult


class HiddenLayerClient:
    """OAuth-backed client for the stable interaction detection endpoint."""

    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None) -> None:
        self._settings = settings
        self._client = client or httpx.AsyncClient(
            base_url=settings.hiddenlayer_base_url,
            timeout=httpx.Timeout(20.0),
            headers={"User-Agent": "HiddenTowerDefence/0.1", "Accept": "application/json"},
        )
        self._owns_client = client is None
        self._access_token: str | None = None
        self._token_expires_at = 0.0
        self._token_lock = asyncio.Lock()

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def _token(self, *, force_refresh: bool = False) -> str:
        if force_refresh:
            self._access_token = None
            self._token_expires_at = 0.0
        if self._access_token and time.monotonic() < self._token_expires_at:
            return self._access_token
        async with self._token_lock:
            if self._access_token and time.monotonic() < self._token_expires_at:
                return self._access_token
            client_id = self._settings.hiddenlayer_client_id
            client_secret = self._settings.hiddenlayer_client_secret
            if client_id is None or client_secret is None:
                raise RuntimeError("HiddenLayer credentials are not configured")
            response = await self._client.post(
                "/oauth2/token",
                data={
                    "grant_type": "client_credentials",
                    "client_id": client_id.get_secret_value(),
                    "client_secret": client_secret.get_secret_value(),
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            response.raise_for_status()
            payload = response.json()
            self._access_token = payload["access_token"]
            expires_in = max(int(payload.get("expires_in", 300)) - 30, 1)
            self._token_expires_at = time.monotonic() + expires_in
            return self._access_token

    async def scan(self, boundary: str, text: str, model: str | None = None) -> ScanResult:
        """Scan chat-style content, preserving the provider response for auditing."""

        if (
            self._settings.hiddenlayer_client_id is None
            or self._settings.hiddenlayer_client_secret is None
        ):
            fail_closed = (
                self._settings.environment == "production"
                and self._settings.hiddenlayer_fail_closed
            )
            return ScanResult(
                boundary=boundary,
                detected=fail_closed,
                threat_level="Unknown" if fail_closed else "None",
                action="Error" if fail_closed else "Allow",
                provider_status="not_configured",
                raw={"provider": "not_configured", "simulated": True},
            )
        payload = {
            "metadata": {
                "model": model or self._settings.nvidia_model,
                "requester_id": self._settings.hiddenlayer_requester_id,
            },
            "input": {"messages": [{"role": "user", "content": text}]},
        }
        try:
            response = await self._request_with_retry(
                "/detection/v1/interactions",
                payload,
            )
        except (httpx.HTTPError, TimeoutError, ValueError) as error:
            return ScanResult(
                boundary=boundary,
                detected=self._settings.hiddenlayer_fail_closed,
                threat_level="Unknown",
                action="Error",
                provider_status="failed",
                raw={"error_type": type(error).__name__},
            )
        raw = response.json()
        evaluation: dict[str, Any] = raw.get("evaluation") or {}
        detectors = [
            str(analysis.get("detector_name") or analysis.get("name") or analysis.get("detector"))
            for analysis in raw.get("analysis") or []
            if isinstance(analysis, dict)
            and (analysis.get("detected") or analysis.get("has_detection"))
        ]
        return ScanResult(
            boundary=boundary,
            detected=bool(evaluation.get("has_detections")),
            threat_level=str(evaluation.get("threat_level") or "None"),
            action=str(evaluation.get("action") or "Allow"),
            detectors=detectors,
            raw=raw,
            provider_status="completed",
        )

    async def _request_with_retry(self, path: str, payload: dict[str, Any]) -> httpx.Response:
        retryable = {408, 409, 429, 500, 502, 503, 504}
        token_expired = False
        for attempt in range(3):
            token = await self._token(force_refresh=token_expired)
            response = await self._client.post(
                path,
                json=payload,
                headers={"Authorization": f"Bearer {token}"},
            )
            token_expired = response.status_code == 401
            if token_expired and attempt < 2:
                continue
            if response.status_code not in retryable or attempt == 2:
                response.raise_for_status()
                return response
            await asyncio.sleep(0.25 * (2**attempt))
        raise RuntimeError("Unreachable retry state")
