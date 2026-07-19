import httpx

from app.clients.apify import ApifyClient
from app.clients.hiddenlayer import HiddenLayerClient
from app.config import Settings


def test_apify_normalizes_primary_and_fallback_shapes() -> None:
    primary = ApifyClient.normalize(
        {
            "id": 123,
            "title": "Primary",
            "top_comments": [{"text": "First"}],
            "points": 9,
            "num_comments": 1,
        }
    )
    fallback = ApifyClient.normalize(
        {
            "story_id": 456,
            "story_title": "Fallback",
            "comments": [{"content": "Second"}],
            "score": 4,
            "comment_count": 1,
        }
    )
    assert primary.id == "hn:123"
    assert primary.comments == ["First"]
    assert fallback.id == "hn:456"
    assert fallback.title == "Fallback"


async def test_hiddenlayer_refreshes_oauth_after_unauthorized(monkeypatch) -> None:
    monkeypatch.setenv("environment", "test")
    monkeypatch.setenv("HiddenLayer_API_ClientID", "client")
    monkeypatch.setenv("HiddenLayer_API_ClientSecret", "secret")
    token_calls = 0
    scan_calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal token_calls, scan_calls
        if request.url.path == "/oauth2/token":
            token_calls += 1
            return httpx.Response(
                200,
                json={"access_token": f"token-{token_calls}", "expires_in": 300},
            )
        scan_calls += 1
        if scan_calls == 1:
            return httpx.Response(401, json={"detail": "expired"})
        return httpx.Response(
            200,
            json={
                "evaluation": {
                    "has_detections": False,
                    "threat_level": "None",
                    "action": "Allow",
                },
                "analysis": [],
            },
        )

    async with httpx.AsyncClient(
        base_url="https://api.hiddenlayer.test",
        transport=httpx.MockTransport(handler),
    ) as http_client:
        client = HiddenLayerClient(
            Settings(hiddenlayer_base_url="https://api.hiddenlayer.test"),
            client=http_client,
        )
        result = await client.scan("ingest", "safe content")

    assert not result.detected
    assert result.action == "Allow"
    assert token_calls == 2
    assert scan_calls == 2
