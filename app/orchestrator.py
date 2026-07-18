from __future__ import annotations

from app.clients.hiddenlayer import HiddenLayerClient
from app.clients.nemotron import NemotronClient
from app.config import Settings
from app.events import EventHub
from app.models import (
    Approval,
    EventType,
    Incident,
    ScanResult,
    SourceItem,
    TowerEvent,
    TrustState,
)
from app.policy import can_send_raw_content_to_model, state_for_scan
from app.repositories import SQLiteRepository


class Orchestrator:
    def __init__(
        self,
        settings: Settings,
        repository: SQLiteRepository,
        events: EventHub,
        hiddenlayer: HiddenLayerClient,
        nemotron: NemotronClient,
    ) -> None:
        self._settings = settings
        self._repository = repository
        self._events = events
        self._hiddenlayer = hiddenlayer
        self._nemotron = nemotron

    async def process(self, item: SourceItem, intake_override: ScanResult | None = None) -> bool:
        if not await self._repository.store_source_item(item):
            return False
        await self._emit(
            EventType.CONTENT_RECEIVED,
            item.id,
            {"title": item.title, "simulated": item.simulated},
        )
        intake = intake_override or await self._scan("ingest", item.id, self._source_text(item))
        if intake_override:
            await self._emit(
                EventType.SCAN_COMPLETED,
                item.id,
                {
                    "boundary": intake.boundary,
                    "detected": intake.detected,
                    "threat_level": intake.threat_level,
                    "action": intake.action,
                    "simulated": True,
                },
            )
        fail_closed = self._settings.hiddenlayer_fail_closed and intake.action == "Error"
        state = state_for_scan(intake, fail_closed)
        await self._transition(item.id, state, intake)
        if state == TrustState.LOCKED:
            await self._repository.create_incident(
                Incident(
                    source_item_id=item.id,
                    severity=intake.threat_level,
                    summary="High-risk content was blocked before model processing.",
                )
            )
            await self._emit(EventType.INCIDENT_CREATED, item.id, {"severity": intake.threat_level})
            return True

        prompt = self._prompt_text(item)
        prompt_scan = await self._scan("prompt", item.id, prompt)
        state = max(state, state_for_scan(prompt_scan), key=self._state_rank)
        await self._transition(item.id, state, prompt_scan)
        if not can_send_raw_content_to_model(state):
            return True

        await self._emit(EventType.MODEL_STARTED, item.id)
        triage = await self._nemotron.triage(item)
        response_scan = await self._scan("response", item.id, triage.model_dump_json())
        state = max(state, state_for_scan(response_scan), key=self._state_rank)
        await self._transition(item.id, state, response_scan)
        await self._repository.store_triage(item.id, triage)
        await self._emit(
            EventType.MODEL_COMPLETED,
            item.id,
            {"summary": triage.summary, "priority": triage.priority, "topics": triage.topics},
        )

        if state == TrustState.RESTRICTED:
            approval = await self._repository.create_approval(
                Approval(source_item_id=item.id, action=triage.recommended_action)
            )
            await self._emit(
                EventType.APPROVAL_CREATED,
                item.id,
                {"approval_id": approval.id, "action": approval.action},
            )
        return True

    async def _scan(self, boundary: str, item_id: str, content: str) -> ScanResult:
        await self._emit(EventType.SCAN_STARTED, item_id, {"boundary": boundary})
        scan = await self._hiddenlayer.scan(boundary, content)
        await self._emit(
            EventType.SCAN_COMPLETED,
            item_id,
            {
                "boundary": boundary,
                "detected": scan.detected,
                "threat_level": scan.threat_level,
                "action": scan.action,
            },
        )
        if scan.detected:
            await self._emit(
                EventType.DETECTION,
                item_id,
                {"boundary": boundary, "scan": scan.model_dump()},
            )
        return scan

    async def _transition(self, item_id: str, state: TrustState, scan: ScanResult) -> None:
        current = await self._repository.get_trust_state()
        if current == state:
            return
        await self._repository.set_trust_state(state)
        await self._emit(
            EventType.STATE_CHANGED,
            item_id,
            {"from": current.value, "to": state.value, "reason": scan.action},
            state,
        )

    async def _emit(
        self,
        event_type: EventType,
        source_item_id: str | None = None,
        payload: dict[str, object] | None = None,
        trust_state: TrustState | None = None,
    ) -> None:
        await self._events.publish(
            TowerEvent(
                type=event_type,
                source_item_id=source_item_id,
                trust_state=trust_state,
                payload=payload or {},
            )
        )

    @staticmethod
    def _source_text(item: SourceItem) -> str:
        return "\n".join([item.title, item.text, *item.comments])

    @staticmethod
    def _prompt_text(item: SourceItem) -> str:
        return f"Summarize: {item.title}\n{item.text}"

    @staticmethod
    def _state_rank(state: TrustState) -> int:
        return {TrustState.NORMAL: 0, TrustState.RESTRICTED: 1, TrustState.LOCKED: 2}[state]
