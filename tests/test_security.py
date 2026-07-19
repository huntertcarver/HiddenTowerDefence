from app.redaction import sanitize_value


def test_event_redaction_removes_nested_secrets_and_bounds_strings() -> None:
    payload = {
        "authorization": "Bearer secret",
        "nested": {
            "client_secret": "hidden",
            "safe": "x" * 600,
        },
        "items": [{"api-key": "hidden"}, "visible"],
    }
    sanitized = sanitize_value(payload)
    assert sanitized["authorization"] == "[REDACTED]"
    assert sanitized["nested"]["client_secret"] == "[REDACTED]"
    assert len(sanitized["nested"]["safe"]) == 500
    assert sanitized["items"][0]["api-key"] == "[REDACTED]"
