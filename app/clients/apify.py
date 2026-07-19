from __future__ import annotations

import asyncio
from typing import Any

import httpx

from app.config import Settings
from app.models import SourceItem


class ApifyClient:
    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None) -> None:
        self._settings = settings
        self._client = client or httpx.AsyncClient(
            base_url="https://api.apify.com/v2",
            timeout=httpx.Timeout(30.0),
            headers={"User-Agent": "HiddenTowerDefence/0.1", "Accept": "application/json"},
        )
        self._owns_client = client is None

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    def _headers(self) -> dict[str, str]:
        token = self._settings.apify_api_token
        if token is None:
            raise RuntimeError("Apify token is not configured")
        return {"Authorization": f"Bearer {token.get_secret_value()}"}

    async def fetch_recent(self, limit: int = 10) -> list[SourceItem]:
        run = await self._start_actor(self._settings.apify_actor_id, limit)
        terminal = await self._poll_run(run["id"])
        if terminal["status"] != "SUCCEEDED":
            raise RuntimeError(f"Apify run ended with {terminal['status']}")
        response = await self._client.get(
            f"/datasets/{terminal['defaultDatasetId']}/items",
            params={"clean": "true", "limit": limit},
            headers=self._headers(),
        )
        response.raise_for_status()
        return [self.normalize(item) for item in response.json()]

    async def _start_actor(self, actor_id: str, limit: int) -> dict[str, Any]:
        if actor_id == "gentle_cloud/hacker-news-scraper":
            payload = {
                "mode": "new",
                "max_results": limit,
                "include_comments": True,
                "request_timeout": 30,
            }
        else:
            payload = {
                "category": "new",
                "limit": limit,
                "comment_limit": 10,
                "reply_depth": 1,
                "replies_per_comment": 3,
            }
        response = await self._client.post(
            f"/acts/{actor_id.replace('/', '~')}/runs",
            params={"timeout": 120},
            json=payload,
            headers=self._headers(),
        )
        response.raise_for_status()
        return response.json()["data"]

    async def _poll_run(self, run_id: str) -> dict[str, Any]:
        terminal_states = {"SUCCEEDED", "FAILED", "TIMED-OUT", "ABORTED"}
        for _ in range(50):
            response = await self._client.get(f"/actor-runs/{run_id}", headers=self._headers())
            response.raise_for_status()
            run = response.json()["data"]
            if run["status"] in terminal_states:
                return run
            await asyncio.sleep(3)
        raise TimeoutError(f"Apify run {run_id} did not finish in time")

    @staticmethod
    def normalize(item: dict[str, Any]) -> SourceItem:
        comments = item.get("top_comments") or item.get("comments") or []
        comment_text = [
            str(comment.get("text") or comment.get("content") or "")
            if isinstance(comment, dict)
            else str(comment)
            for comment in comments
        ]
        item_id = item.get("id")
        if item_id is None:
            raise ValueError("Apify item did not contain an HN id")
        return SourceItem(
            id=f"hn:{item_id}",
            title=str(item.get("title") or "Untitled Hacker News item"),
            text=str(item.get("text") or ""),
            url=item.get("url") or item.get("hn_url"),
            author=item.get("author"),
            score=item.get("score") or item.get("points"),
            comment_count=item.get("num_comments") or item.get("comment_count"),
            comments=[text for text in comment_text if text],
        )
