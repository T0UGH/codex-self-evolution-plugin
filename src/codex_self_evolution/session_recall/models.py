from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ParsedMessage:
    session_id: str
    message_uid: str
    message_index: int
    role: str
    content: str
    raw_json: str
    metadata: dict[str, Any] = field(default_factory=dict)
    timestamp: str = ""
    tool_name: str = ""
    raw_event_type: str = ""


@dataclass(frozen=True)
class ParsedSession:
    session_id: str
    session_path: Path
    cwd: str
    messages: list[ParsedMessage]
    metadata: dict[str, Any] = field(default_factory=dict)
