from __future__ import annotations

from typing import Any

SENSITIVE_KEY_PARTS = {
    "api_key",
    "apikey",
    "authorization",
    "client_secret",
    "cookie",
    "credential",
    "operator_token",
    "password",
    "secret",
    "token",
}


def sanitize_value(value: Any, *, max_string_length: int = 500) -> Any:
    if isinstance(value, dict):
        sanitized: dict[str, Any] = {}
        for key, nested in value.items():
            normalized = str(key).lower().replace("-", "_")
            if any(part in normalized for part in SENSITIVE_KEY_PARTS):
                sanitized[str(key)] = "[REDACTED]"
            else:
                sanitized[str(key)] = sanitize_value(
                    nested, max_string_length=max_string_length
                )
        return sanitized
    if isinstance(value, list):
        return [
            sanitize_value(item, max_string_length=max_string_length)
            for item in value[:100]
        ]
    if isinstance(value, tuple):
        return [
            sanitize_value(item, max_string_length=max_string_length)
            for item in value[:100]
        ]
    if isinstance(value, str):
        return value[:max_string_length]
    if isinstance(value, (bool, int, float)) or value is None:
        return value
    return str(value)[:max_string_length]
