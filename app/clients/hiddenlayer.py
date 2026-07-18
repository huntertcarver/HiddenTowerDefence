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

    async def _token(self) -> str:
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
            self._token_expires_at = time.monotonic() + max(int(payload.get("expires_in", 300)) - 30, 1)
            return self._access_token

    async def scan(self, boundary: str, text: str, model: str | None = None) -> ScanResult:
        """Scan chat-style content, preserving the provider response for auditing."""

        if (
            self._settings.hiddenlayer_client_id is None
            or self._settings.hiddenlayer_client_secret is None
        ):
            return ScanResult(
                boundary=boundary,
                raw={"provider": "not_configured", "simulated": True},
            )
        token = await self._token()
        payload = {
            "metadata": {
                "model": model or self._settings.nvidia_model,
                "requester_id": self._settings.hiddenlayer_requester_id,
            },
            "input": {"messages": [{"role": "user", "content": text}]},
        }
        response = await self._request_with_retry(
            "/detection/v1/interactions",
            payload,
            token,
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
        )

    async def _request_with_retry(
        self, path: str, payload: dict[str, Any], token: str
    ) -> httpx.Response:
        retryable = {408, 409, 429, 500, 502, 503, 504}
        for attempt in range(3):
            response = await self._client.post(
                path,
                json=payload,
                headers={"Authorization": f"Bearer {token}"},
            )
            if response.status_code not in retryable or attempt == 2:
                response.raise_for_status()
                return response
            await asyncio.sleep(0.25 * (2**attempt))
        raise RuntimeError("Unreachable retry state")
