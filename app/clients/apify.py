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
        run = await self.start_actor(self._settings.apify_actor_id, limit)
        terminal = await self.poll_run(run["id"])
        if terminal["status"] != "SUCCEEDED":
            raise RuntimeError(f"Apify run ended with {terminal['status']}")
        items = await self.fetch_dataset(terminal["defaultDatasetId"], limit)
        return [self.normalize(item) for item in items]

    async def start_actor(self, actor_id: str, limit: int) -> dict[str, Any]:
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
        response = await self._request(
            "POST",
            f"/acts/{actor_id.replace('/', '~')}/runs",
            params={"timeout": 120},
            json=payload,
        )
        return response.json()["data"]

    async def get_run(self, run_id: str) -> dict[str, Any]:
        response = await self._request("GET", f"/actor-runs/{run_id}")
        return response.json()["data"]

    async def poll_run(self, run_id: str) -> dict[str, Any]:
        terminal_states = {"SUCCEEDED", "FAILED", "TIMED-OUT", "ABORTED"}
        for _ in range(50):
            run = await self.get_run(run_id)
            if run["status"] in terminal_states:
                return run
            await asyncio.sleep(3)
        raise TimeoutError(f"Apify run {run_id} did not finish in time")

    async def fetch_dataset(self, dataset_id: str, limit: int) -> list[dict[str, Any]]:
        response = await self._request(
            "GET",
            f"/datasets/{dataset_id}/items",
            params={"clean": "true", "limit": min(max(limit, 1), 20)},
        )
        payload = response.json()
        if not isinstance(payload, list):
            raise ValueError("Apify dataset response was not a list")
        return [item for item in payload if isinstance(item, dict)]

    async def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        retryable = {408, 409, 429, 500, 502, 503, 504}
        for attempt in range(3):
            response = await self._client.request(
                method,
                path,
                headers=self._headers(),
                **kwargs,
            )
            if response.status_code not in retryable or attempt == 2:
                response.raise_for_status()
                return response
            retry_after = response.headers.get("retry-after")
            delay = (
                float(retry_after)
                if retry_after and retry_after.isdigit()
                else 0.5 * 2**attempt
            )
            await asyncio.sleep(min(delay, 5.0))
        raise RuntimeError("Unreachable retry state")

    @staticmethod
    def normalize(item: dict[str, Any]) -> SourceItem:
        comments = (
            item.get("top_comments")
            or item.get("comments")
            or item.get("children")
            or []
        )
        if isinstance(comments, dict):
            comments = list(comments.values())
        if not isinstance(comments, list):
            comments = []
        comment_text = [
            str(
                comment.get("text")
                or comment.get("content")
                or comment.get("comment")
                or ""
            )
            if isinstance(comment, dict)
            else str(comment)
            for comment in comments
        ]
        item_id = item.get("id") or item.get("item_id") or item.get("story_id")
        if item_id is None:
            raise ValueError("Apify item did not contain an HN id")
        raw_score = item.get("score")
        if raw_score is None:
            raw_score = item.get("points")
        raw_comment_count = item.get("num_comments")
        if raw_comment_count is None:
            raw_comment_count = item.get("comment_count")
        return SourceItem(
            id=f"hn:{item_id}",
            title=str(item.get("title") or item.get("story_title") or "Untitled Hacker News item"),
            text=str(item.get("text") or item.get("story_text") or item.get("description") or ""),
            url=item.get("url") or item.get("hn_url") or item.get("story_url"),
            author=item.get("author") or item.get("user"),
            score=int(raw_score) if raw_score is not None else None,
            comment_count=int(raw_comment_count) if raw_comment_count is not None else None,
            comments=[text for text in comment_text if text],
        )
