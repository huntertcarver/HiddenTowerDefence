from __future__ import annotations

from typing import Any

from app.clients.nemotron import NemotronClient
from app.models import QueryHistory, SourceItem, TriageResult, Watchlist, WatchlistMatch
from app.repositories import Repository
from app.trends import calculate_trends, parse_query


class IntelligenceService:
    SCOPE = "Hacker News developer-community signals"

    def __init__(self, repository: Repository, nemotron: NemotronClient) -> None:
        self._repository = repository
        self._nemotron = nemotron

    async def match_watchlists(
        self, item: SourceItem, triage: TriageResult
    ) -> list[WatchlistMatch]:
        matches: list[WatchlistMatch] = []
        haystack = " ".join(
            [
                item.title,
                item.text,
                *triage.topics,
                *triage.companies,
                *triage.products,
                *triage.technologies,
            ]
        ).lower()
        for watchlist in await self._repository.list_watchlists():
            evidence = self._watchlist_evidence(watchlist, haystack, triage)
            if not evidence or not self._priority_matches(
                triage.priority, watchlist.minimum_priority
            ):
                continue
            if watchlist.sentiment and watchlist.sentiment.lower() != triage.sentiment.lower():
                continue
            engagement = max(item.score or 0, 0) + max(item.comment_count or 0, 0)
            if (
                watchlist.minimum_engagement is not None
                and engagement < watchlist.minimum_engagement
            ):
                continue
            match = WatchlistMatch(
                watchlist_id=watchlist.id,
                source_item_id=item.id,
                evidence=evidence,
                rationale=f"Matched configured terms: {', '.join(evidence)}",
            )
            await self._repository.store_watchlist_match(match)
            matches.append(match)
        return matches

    async def query(self, query: str) -> dict[str, Any]:
        topic, window_hours = parse_query(query)
        records = await self._repository.list_triage_with_sources(limit=500)
        signals = calculate_trends(
            records,
            topic=topic,
            window_hours=window_hours,
        )
        citations = list(
            dict.fromkeys(
                citation for signal in signals for citation in signal.citations
            )
        )
        evidence = {
            "scope": self.SCOPE,
            "topic": topic,
            "window_hours": window_hours,
            "signals": [signal.model_dump(mode="json") for signal in signals],
            "citations": citations,
        }
        insufficient = not signals or sum(signal.evidence_count for signal in signals) < 2
        if insufficient:
            explanation = {
                "answer": (
                    "Evidence is insufficient for a reliable trend conclusion in the "
                    "requested Hacker News developer-community window."
                ),
                "citations": citations,
            }
        else:
            explanation = await self._nemotron.explain_evidence(query, evidence)
        response = {
            **evidence,
            "query": query,
            "evidence_count": len(citations),
            "confidence": self._overall_confidence(signals),
            "insufficient_evidence": insufficient,
            "answer": explanation["answer"],
            "citations": explanation["citations"],
        }
        await self._repository.store_query_history(
            QueryHistory(
                query=query,
                topic=topic,
                window_hours=window_hours,
                evidence_count=len(citations),
                response=response,
            )
        )
        return response

    @staticmethod
    def _watchlist_evidence(
        watchlist: Watchlist, haystack: str, triage: TriageResult
    ) -> list[str]:
        candidates = [
            *watchlist.search_terms,
            *watchlist.companies_products,
            *watchlist.topics,
        ]
        triage_topics = {topic.lower() for topic in triage.topics}
        return sorted(
            {
                candidate
                for candidate in candidates
                if candidate.lower() in haystack or candidate.lower() in triage_topics
            }
        )

    @staticmethod
    def _priority_matches(actual: str, minimum: str) -> bool:
        rank = {"low": 0, "normal": 1, "medium": 2, "high": 3, "critical": 4}
        return rank.get(actual.lower(), 1) >= rank.get(minimum.lower(), 1)

    @staticmethod
    def _overall_confidence(signals: list[Any]) -> str:
        if not signals:
            return "insufficient"
        if any(signal.confidence == "high" for signal in signals):
            return "high"
        if any(signal.confidence == "medium" for signal in signals):
            return "medium"
        return "low"
