from datetime import UTC, datetime, timedelta

from app.models import SourceItem, TriageResult
from app.trends import calculate_trends, parse_query


def test_parse_query_extracts_bounded_time_window() -> None:
    topic, window_hours = parse_query(
        "What prompt injection trends emerged over the last 7 days?"
    )
    assert topic == "prompt injection"
    assert window_hours == 168


def test_trends_compare_equal_windows_with_citations() -> None:
    now = datetime(2026, 7, 19, tzinfo=UTC)
    records = [
        (
            SourceItem(
                id="hn:current",
                title="Prompt injection defenses",
                score=20,
                comment_count=5,
                received_at=now - timedelta(hours=2),
            ),
            TriageResult(
                summary="Current",
                category="security",
                priority="high",
                sentiment="positive",
                topics=["prompt injection"],
            ),
        ),
        (
            SourceItem(
                id="hn:previous",
                title="Prompt injection discussion",
                score=4,
                comment_count=1,
                received_at=now - timedelta(days=8),
            ),
            TriageResult(
                summary="Previous",
                category="security",
                priority="normal",
                sentiment="negative",
                topics=["prompt injection"],
            ),
        ),
    ]
    signals = calculate_trends(
        records,
        topic="prompt injection",
        window_hours=168,
        now=now,
    )
    assert len(signals) == 1
    signal = signals[0]
    assert signal.current_mentions == 1
    assert signal.previous_mentions == 1
    assert signal.engagement_delta == 20
    assert signal.sentiment_delta == 1.0
    assert signal.citations == ["hn:current"]
