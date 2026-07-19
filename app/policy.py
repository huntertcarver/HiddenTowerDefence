from __future__ import annotations

from app.models import ScanResult, TrustState


def state_for_scan(scan: ScanResult, fail_closed: bool = False) -> TrustState:
    """Translate a vendor result to the conservative product policy."""

    severity = scan.threat_level.strip().lower()
    action = scan.action.strip().lower()
    if action == "block" or severity in {"critical", "high"}:
        return TrustState.LOCKED
    if scan.detected or action in {"alert", "review"} or fail_closed:
        return TrustState.RESTRICTED
    return TrustState.NORMAL


def can_execute_tool(state: TrustState, action: str) -> bool:
    if state == TrustState.LOCKED:
        return False
    if state == TrustState.NORMAL:
        return True
    return action in {"mock_web_fetch"}


def can_send_raw_content_to_model(state: TrustState) -> bool:
    return state != TrustState.LOCKED
