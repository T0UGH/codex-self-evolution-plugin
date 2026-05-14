from __future__ import annotations

import re


SECRET_PATTERNS = [
    re.compile(r"(?i)(Authorization\s*:\s*Bearer\s+)[A-Za-z0-9._~+/=-]{12,}"),
    re.compile(r"(?i)(api[_-]?key\s*=\s*)['\"]?[A-Za-z0-9._~+/=-]{8,}['\"]?"),
    re.compile(r"(?i)(token\s*=\s*)['\"]?[A-Za-z0-9._~+/=-]{8,}['\"]?"),
    re.compile(r"(?i)(password\s*=\s*)['\"]?[^'\"\s]{6,}['\"]?"),
    re.compile(r"\bsk-[A-Za-z0-9]{8,}\b"),
    re.compile(r"\bAKIA[A-Z0-9]{12,}\b"),
]


def redact_secrets(text: str) -> tuple[str, int]:
    redacted = text
    count = 0
    for pattern in SECRET_PATTERNS:

        def replace(match: re.Match[str]) -> str:
            nonlocal count
            count += 1
            if match.lastindex:
                return f"{match.group(1)}[REDACTED]"
            return "[REDACTED]"

        redacted = pattern.sub(replace, redacted)
    return redacted, count


def contains_secret_like_text(text: str) -> bool:
    return any(pattern.search(text) for pattern in SECRET_PATTERNS)
