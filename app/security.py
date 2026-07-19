from __future__ import annotations

from app.clients.hiddenlayer import HiddenLayerClient
from app.events import EventHub
from app.models import (
    EventType,
    ScanBoundary,
    ScanRecord,
    ScanResult,
    TaintRecord,
    TowerEvent,
)
from app.repositories import Repository


class SecurityScanner:
    def __init__(
        self,
        repository: Repository,
        events: EventHub,
        client: HiddenLayerClient,
    ) -> None:
        self._repository = repository
        self._events = events
        self._client = client

    async def scan(
        self,
        boundary: ScanBoundary,
        source_item_id: str,
        content: str,
        *,
        parent_scan_id: str | None = None,
        simulated_result: ScanResult | None = None,
    ) -> ScanRecord:
        await self._events.publish(
            TowerEvent(
                type=EventType.SCAN_STARTED,
                source_item_id=source_item_id,
                entity_id=source_item_id,
                correlation_id=source_item_id,
                payload={"boundary": boundary.value, "simulated": simulated_result is not None},
            )
        )
        result = simulated_result or await self._client.scan(boundary.value, content)
        record = ScanRecord(
            source_item_id=source_item_id,
            boundary=boundary,
            detected=result.detected,
            threat_level=result.threat_level,
            action=result.action,
            detectors=result.detectors,
            raw=result.raw,
            provider_status=result.provider_status,
            parent_scan_id=parent_scan_id,
        )
        await self._repository.store_scan(record)
        await self._events.publish(
            TowerEvent(
                type=EventType.SCAN_COMPLETED,
                source_item_id=source_item_id,
                entity_id=source_item_id,
                correlation_id=source_item_id,
                payload={
                    "scan_id": record.id,
                    "boundary": boundary.value,
                    "detected": record.detected,
                    "threat_level": record.threat_level,
                    "action": record.action,
                    "detectors": record.detectors,
                    "provider_status": record.provider_status,
                    "simulated": simulated_result is not None,
                },
            )
        )
        if self._is_flagged(record):
            await self._repository.create_taint(
                TaintRecord(
                    source_item_id=source_item_id,
                    reason=f"{boundary.value}:{record.action}:{record.threat_level}",
                )
            )
            await self._events.publish(
                TowerEvent(
                    type=EventType.DETECTION,
                    source_item_id=source_item_id,
                    entity_id=source_item_id,
                    correlation_id=source_item_id,
                    payload={
                        "scan_id": record.id,
                        "boundary": boundary.value,
                        "threat_level": record.threat_level,
                        "action": record.action,
                        "detectors": record.detectors,
                    },
                )
            )
        return record

    @staticmethod
    def _is_flagged(scan: ScanRecord) -> bool:
        return (
            scan.detected
            or scan.action.lower() in {"alert", "block", "error", "review"}
            or scan.threat_level.lower() not in {"none", "low", ""}
        )
