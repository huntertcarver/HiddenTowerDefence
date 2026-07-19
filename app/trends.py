from __future__ import annotations

import re
from collections import Counter
from datetime import UTC, datetime, timedelta

from app.models import SourceItem, TrendSignal, TriageResult

TIME_RANGE_PATTERN = re.compile(
    r"(?:last|past|over)\s+(\d{1,3})\s*(hour|hours|day|days|week|weeks)",
    re.IGNORECASE,
)
STOPWORDS = {
    "about",
    "developer",
    "developers",
    "emerged",
    "hacker",
    "market",
    "news",
    "over",
    "recent",
    "signals",
    "the",
    "trend",
    "trends",
    "what",
    "which",
}
SENTIMENT_SCORE = {
    "very_negative": -1.0,
    "negative": -0.5,
    "neutral": 0.0,
    "unknown": 0.0,
    "positive": 0.5,
    "very_positive": 1.0,
}


def parse_query(query: str) -> tuple[str | None, int]:
    match = TIME_RANGE_PATTERN.search(query)
    window_hours = 168
    if match:
        amount = min(max(int(match.group(1)), 1), 90)
        unit = match.group(2).lower()
        multiplier = 1 if unit.startswith("hour") else 24 if unit.startswith("day") else 168
        window_hours = min(amount * multiplier, 24 * 90)
    words = re.findall(r"[A-Za-z0-9+#.-]{3,}", TIME_RANGE_PATTERN.sub("", query))
    candidates = [word.lower() for word in words if word.lower() not in STOPWORDS]
    return (" ".join(candidates[:5]) or None, window_hours)


def calculate_trends(
    records: list[tuple[SourceItem, TriageResult]],
    *,
    topic: str | None,
    window_hours: int,
    now: datetime | None = None,
) -> list[TrendSignal]:
    reference = now or datetime.now(UTC)
    current_start = reference - timedelta(hours=window_hours)
    previous_start = current_start - timedelta(hours=window_hours)
    topics = [topic] if topic else _ranked_topics(records)
    signals: list[TrendSignal] = []
    for candidate in topics[:20]:
        current = [
            record
            for record in records
            if current_start <= record[0].received_at <= reference
            and _matches(candidate, *record)
        ]
        previous = [
            record
            for record in records
            if previous_start <= record[0].received_at < current_start
            and _matches(candidate, *record)
        ]
        if not current and not previous:
            continue
        current_engagement = sum(_engagement(item) for item, _ in current)
        previous_engagement = sum(_engagement(item) for item, _ in previous)
        current_sentiment = _average_sentiment(current)
        previous_sentiment = _average_sentiment(previous)
        evidence_count = len(current)
        confidence = "high" if evidence_count >= 8 else "medium" if evidence_count >= 3 else "low"
        signals.append(
            TrendSignal(
                topic=candidate,
                current_mentions=len(current),
                previous_mentions=len(previous),
                mention_delta=len(current) - len(previous),
                current_engagement=current_engagement,
                previous_engagement=previous_engagement,
                engagement_delta=current_engagement - previous_engagement,
                sentiment_delta=round(current_sentiment - previous_sentiment, 3),
                evidence_count=evidence_count,
                confidence=confidence,
                citations=[item.id for item, _ in current],
            )
        )
    return sorted(
        signals,
        key=lambda signal: (
            signal.evidence_count,
            signal.mention_delta,
            signal.engagement_delta,
        ),
        reverse=True,
    )


def _ranked_topics(
    records: list[tuple[SourceItem, TriageResult]],
) -> list[str]:
    counts = Counter(
        topic.strip().lower()
        for _, triage in records
        for topic in triage.topics
        if topic.strip()
    )
    return [topic for topic, _ in counts.most_common()]


def _matches(topic: str, item: SourceItem, triage: TriageResult) -> bool:
    needle = topic.lower()
    values = [
        item.title,
        item.text,
        *triage.topics,
        *triage.entities,
        *triage.companies,
        *triage.products,
        *triage.technologies,
        *triage.repositories,
        *triage.cves,
    ]
    return any(needle in value.lower() for value in values)


def _engagement(item: SourceItem) -> int:
    return max(item.score or 0, 0) + max(item.comment_count or len(item.comments), 0)


def _average_sentiment(records: list[tuple[SourceItem, TriageResult]]) -> float:
    if not records:
        return 0.0
    return sum(
        SENTIMENT_SCORE.get(triage.sentiment.lower().replace(" ", "_"), 0.0)
        for _, triage in records
    ) / len(records)
