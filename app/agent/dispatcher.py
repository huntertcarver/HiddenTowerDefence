from __future__ import annotations

import hashlib
import json
from typing import Any

from app.agent.tools import ControlledTools
from app.events import EventHub
from app.models import (
    Approval,
    ApprovalStatus,
    EventType,
    Incident,
    QuarantineRecord,
    ScanBoundary,
    ScanResult,
    ToolRequest,
    ToolStatus,
    TowerEvent,
    TrustState,
)
from app.policy import can_execute_tool, state_for_scan
from app.repositories import Repository
from app.security import SecurityScanner


class ToolDispatcher:
    def __init__(
        self,
        repository: Repository,
        events: EventHub,
        scanner: SecurityScanner,
        tools: ControlledTools,
    ) -> None:
        self._repository = repository
        self._events = events
        self._scanner = scanner
        self._tools = tools

    async def request(
        self,
        source_item_id: str,
        name: str,
        arguments: dict[str, Any],
        trust_state: TrustState,
        *,
        argument_scan_override: ScanResult | None = None,
        result_scan_override: ScanResult | None = None,
    ) -> ToolRequest:
        idempotency_key = self._idempotency_key(source_item_id, name, arguments)
        request, created = await self._repository.create_tool_request(
            ToolRequest(
                source_item_id=source_item_id,
                name=name,
                arguments=arguments,
                idempotency_key=idempotency_key,
            )
        )
        if not created:
            return request
        await self._emit(
            EventType.TOOL_REQUESTED,
            request,
            {"tool_request_id": request.id, "name": name},
        )

        argument_scan = await self._scanner.scan(
            ScanBoundary.TOOL_ARGUMENTS,
            source_item_id,
            json.dumps({"name": name, "arguments": arguments}, sort_keys=True),
            simulated_result=argument_scan_override,
        )
        scan_state = state_for_scan(argument_scan)
        await self._escalate_from_scan(
            source_item_id,
            scan_state,
            f"tool_arguments:{argument_scan.action}:{argument_scan.threat_level}",
        )
        effective_state = max(
            trust_state, scan_state, key=self._state_rank
        )
        if effective_state == TrustState.LOCKED:
            blocked = await self._repository.update_tool_request(
                request.id,
                ToolStatus.BLOCKED,
                failure_reason="tool arguments blocked by security policy",
            )
            await self._emit(
                EventType.TOOL_BLOCKED,
                request,
                {"tool_request_id": request.id, "reason": "security_policy"},
            )
            return blocked or request

        tainted = await self._repository.is_tainted(source_item_id)
        if effective_state == TrustState.RESTRICTED and not can_execute_tool(
            effective_state, name
        ):
            deferred = await self._repository.update_tool_request(
                request.id, ToolStatus.DEFERRED
            )
            approval = await self._repository.create_approval(
                Approval(
                    source_item_id=source_item_id,
                    action=name,
                    arguments=arguments,
                    idempotency_key=idempotency_key,
                    tool_request_id=request.id,
                )
            )
            await self._events.publish(
                TowerEvent(
                    type=EventType.APPROVAL_CREATED,
                    source_item_id=source_item_id,
                    entity_id=source_item_id,
                    correlation_id=request.id,
                    payload={
                        "approval_id": approval.id,
                        "tool_request_id": request.id,
                        "action": name,
                        "tainted": tainted,
                    },
                )
            )
            return deferred or request
        return await self._execute(request, result_scan_override=result_scan_override)

    async def approve(self, approval_id: str) -> Approval | None:
        approval = await self._repository.claim_approval_execution(approval_id)
        if approval is None:
            return None
        request = (
            await self._repository.get_tool_request(approval.tool_request_id)
            if approval.tool_request_id
            else None
        )
        if request is None:
            await self._repository.finalize_approval(
                approval_id, ApprovalStatus.FAILED
            )
            return None
        completed = await self._execute(request)
        final_status = (
            ApprovalStatus.APPROVED
            if completed.status == ToolStatus.COMPLETED
            else ApprovalStatus.FAILED
        )
        resolved = await self._repository.finalize_approval(approval_id, final_status)
        if resolved:
            await self._events.publish(
                TowerEvent(
                    type=EventType.APPROVAL_RESOLVED,
                    source_item_id=resolved.source_item_id,
                    entity_id=resolved.source_item_id,
                    correlation_id=request.id,
                    payload={
                        "approval_id": resolved.id,
                        "status": resolved.status.value,
                        "tool_request_id": request.id,
                    },
                )
            )
        return resolved

    async def deny(self, approval_id: str) -> Approval | None:
        approval = await self._repository.resolve_approval(
            approval_id, ApprovalStatus.DENIED
        )
        if approval is None:
            return None
        if approval.tool_request_id:
            await self._repository.update_tool_request(
                approval.tool_request_id, ToolStatus.DENIED
            )
        await self._repository.store_quarantine(
            QuarantineRecord(
                source_item_id=approval.source_item_id,
                reason=f"Operator denied {approval.action}",
                tool_request_id=approval.tool_request_id,
            )
        )
        await self._events.publish(
            TowerEvent(
                type=EventType.APPROVAL_RESOLVED,
                source_item_id=approval.source_item_id,
                entity_id=approval.source_item_id,
                correlation_id=approval.tool_request_id,
                payload={
                    "approval_id": approval.id,
                    "status": approval.status.value,
                    "tool_request_id": approval.tool_request_id,
                },
            )
        )
        return approval

    async def _execute(
        self,
        request: ToolRequest,
        *,
        result_scan_override: ScanResult | None = None,
    ) -> ToolRequest:
        executing = await self._repository.update_tool_request(
            request.id, ToolStatus.EXECUTING
        )
        request = executing or request
        try:
            result = await self._tools.execute(
                request.source_item_id, request.name, request.arguments
            )
            result_scan = await self._scanner.scan(
                ScanBoundary.TOOL_RESULT,
                request.source_item_id,
                json.dumps(result, sort_keys=True),
                simulated_result=result_scan_override,
            )
            result_state = state_for_scan(result_scan)
            await self._escalate_from_scan(
                request.source_item_id,
                result_state,
                f"tool_result:{result_scan.action}:{result_scan.threat_level}",
            )
            if result_state == TrustState.LOCKED:
                blocked = await self._repository.update_tool_request(
                    request.id,
                    ToolStatus.BLOCKED,
                    failure_reason="tool result blocked by security policy",
                )
                await self._emit(
                    EventType.TOOL_BLOCKED,
                    request,
                    {"tool_request_id": request.id, "reason": "result_scan"},
                )
                return blocked or request
            completed = await self._repository.update_tool_request(
                request.id, ToolStatus.COMPLETED, result=result
            )
            await self._emit(
                EventType.TOOL_COMPLETED,
                request,
                {
                    "tool_request_id": request.id,
                    "name": request.name,
                    "result_status": "completed",
                },
            )
            return completed or request
        except (TypeError, ValueError) as error:
            failed = await self._repository.update_tool_request(
                request.id,
                ToolStatus.FAILED,
                failure_reason=f"{type(error).__name__}: invalid controlled tool request",
            )
            await self._emit(
                EventType.TOOL_BLOCKED,
                request,
                {"tool_request_id": request.id, "reason": "invalid_arguments"},
            )
            return failed or request

    async def _escalate_from_scan(
        self, source_item_id: str, state: TrustState, reason: str
    ) -> None:
        if state == TrustState.NORMAL:
            return
        transition = await self._repository.transition_trust_state(
            state, reason, source_item_id
        )
        if transition:
            await self._events.publish(
                TowerEvent(
                    type=EventType.STATE_CHANGED,
                    source_item_id=source_item_id,
                    entity_id=source_item_id,
                    trust_state=state,
                    payload={
                        "from": transition.from_state.value,
                        "to": transition.to_state.value,
                        "reason": reason,
                    },
                )
            )
        if state == TrustState.LOCKED:
            incidents = await self._repository.list_incidents(active_only=True)
            if any(incident.source_item_id == source_item_id for incident in incidents):
                return
            incident = await self._repository.create_incident(
                Incident(
                    source_item_id=source_item_id,
                    severity="High",
                    summary="Tool boundary security policy locked the runtime.",
                )
            )
            await self._events.publish(
                TowerEvent(
                    type=EventType.INCIDENT_CREATED,
                    source_item_id=source_item_id,
                    entity_id=source_item_id,
                    trust_state=TrustState.LOCKED,
                    payload={"incident_id": incident.id, "severity": incident.severity},
                )
            )

    async def _emit(
        self, event_type: EventType, request: ToolRequest, payload: dict[str, Any]
    ) -> None:
        await self._events.publish(
            TowerEvent(
                type=event_type,
                source_item_id=request.source_item_id,
                entity_id=request.source_item_id,
                correlation_id=request.id,
                payload=payload,
            )
        )

    @staticmethod
    def _idempotency_key(
        source_item_id: str, name: str, arguments: dict[str, Any]
    ) -> str:
        content = json.dumps(
            {"source_item_id": source_item_id, "name": name, "arguments": arguments},
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(content.encode()).hexdigest()

    @staticmethod
    def _state_rank(state: TrustState) -> int:
        return {
            TrustState.NORMAL: 0,
            TrustState.RESTRICTED: 1,
            TrustState.LOCKED: 2,
        }[state]
