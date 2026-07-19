from app.models import ScanResult, TrustState
from app.policy import can_execute_tool, can_send_raw_content_to_model, state_for_scan


def test_clean_scan_remains_normal() -> None:
    assert state_for_scan(ScanResult(boundary="ingest")) == TrustState.NORMAL


def test_alert_requires_restriction() -> None:
    scan = ScanResult(boundary="ingest", detected=True, threat_level="Medium", action="Alert")
    assert state_for_scan(scan) == TrustState.RESTRICTED


def test_high_finding_locks_the_agent() -> None:
    scan = ScanResult(boundary="ingest", detected=True, threat_level="High", action="Alert")
    assert state_for_scan(scan) == TrustState.LOCKED
    assert not can_send_raw_content_to_model(TrustState.LOCKED)
    assert not can_execute_tool(TrustState.LOCKED, "save_brief")
