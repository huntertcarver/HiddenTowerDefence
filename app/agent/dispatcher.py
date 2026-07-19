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
    ProcessingStatus,
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
            if request.status == ToolStatus.DEFERRED:
                return await self._defer_with_approval(
                    request, await self._repository.is_tainted(source_item_id)
                )
            if request.status in {
                ToolStatus.COMPLETED,
                ToolStatus.BLOCKED,
                ToolStatus.DENIED,
                ToolStatus.FAILED,
                ToolStatus.EXECUTING,
            }:
                return request
        if created:
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
            return await self._defer_with_approval(request, tainted)
        return await self._execute(
            request,
            expected_status=ToolStatus.REQUESTED,
            result_scan_override=result_scan_override,
        )

    async def _defer_with_approval(
        self, request: ToolRequest, tainted: bool
    ) -> ToolRequest:
        existing = await self._repository.get_approval_for_tool_request(request.id)
        result = await self._repository.defer_tool_request_with_approval(
            request.id,
            Approval(
                id=request.id,
                source_item_id=request.source_item_id,
                action=request.name,
                arguments=request.arguments,
                idempotency_key=request.idempotency_key,
                tool_request_id=request.id,
            ),
        )
        if result is None:
            return await self._repository.get_tool_request(request.id) or request
        deferred, approval = result
        if existing is not None:
            return deferred
        await self._events.publish(
            TowerEvent(
                type=EventType.APPROVAL_CREATED,
                source_item_id=request.source_item_id,
                entity_id=request.source_item_id,
                correlation_id=request.id,
                payload={
                    "approval_id": approval.id,
                    "tool_request_id": request.id,
                    "action": request.name,
                    "tainted": tainted,
                },
            )
        )
        return deferred

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
        completed = await self._execute(
            request, expected_status=ToolStatus.DEFERRED
        )
        final_status = (
            ApprovalStatus.APPROVED
            if completed.status == ToolStatus.COMPLETED
            else ApprovalStatus.FAILED
        )
        resolved = await self._repository.finalize_approval(approval_id, final_status)
        await self._repository.update_source_status(
            approval.source_item_id,
            self.processing_status(completed.status),
            completed.failure_reason,
        )
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
                id=approval.tool_request_id or approval.id,
                source_item_id=approval.source_item_id,
                reason=f"Operator denied {approval.action}",
                tool_request_id=approval.tool_request_id,
            )
        )
        await self._repository.update_source_status(
            approval.source_item_id, ProcessingStatus.BLOCKED
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
        expected_status: ToolStatus,
        result_scan_override: ScanResult | None = None,
    ) -> ToolRequest:
        executing = await self._repository.claim_tool_request_execution(
            request.id, expected_status
        )
        if executing is None:
            return await self._repository.get_tool_request(request.id) or request
        request = executing
        try:
            result = await self._tools.execute(
                request.source_item_id,
                request.name,
                request.arguments,
                operation_id=request.id,
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
        except Exception as error:
            failed = await self._repository.update_tool_request(
                request.id,
                ToolStatus.FAILED,
                failure_reason=(
                    f"{type(error).__name__}: controlled tool execution failed"
                ),
            )
            await self._emit(
                EventType.TOOL_BLOCKED,
                request,
                {"tool_request_id": request.id, "reason": "execution_failed"},
            )
            return failed or request

    @staticmethod
    def processing_status(status: ToolStatus) -> ProcessingStatus:
        if status == ToolStatus.COMPLETED:
            return ProcessingStatus.COMPLETED
        if status in {ToolStatus.BLOCKED, ToolStatus.DENIED}:
            return ProcessingStatus.BLOCKED
        if status == ToolStatus.FAILED:
            return ProcessingStatus.FAILED
        return ProcessingStatus.PROCESSING

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
