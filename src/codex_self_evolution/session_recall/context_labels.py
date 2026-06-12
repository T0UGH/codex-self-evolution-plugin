from __future__ import annotations

from .models import ParsedSession

LABEL_ORDER = (
    "agent_injected_context",
    "external_web",
    "local_repo_code",
    "third_party_document",
    "user_instruction",
)

_LOCAL_PATH_MARKERS = ("/Users/", "/tmp/", "/private/tmp/", "src/", "tests/", ".py", ".md", ".toml")
_USER_INSTRUCTION_MARKERS = ("以后", "记住", "偏好", "规则", "优先", "不要", "总是", "每次", "prefer", "always", "never")
_AGENT_CONTEXT_MARKERS = ("AGENTS.md", "DeveloperInstructions", "Stable Background", "Recall Policy", "system", "developer")
_EXTERNAL_TOOL_MARKERS = ("web.run", "search_query", "browser", "chrome", "open_url")
_THIRD_PARTY_MARKERS = ("third_party_document", "pdf", "docx", "Google Docs", "external document")


def derive_context_labels(parsed: ParsedSession) -> list[str]:
    """Derive low-sensitive source labels from parsed message metadata and text."""
    labels: set[str] = set()
    repo_root = str(parsed.metadata.get("repo_root") or parsed.cwd or "")
    for message in parsed.messages:
        role = message.role.lower()
        raw_event = message.raw_event_type.lower()
        tool_name = message.tool_name.lower()
        text = message.content
        lowered = text.lower()

        if role in {"system", "developer"} or any(marker.lower() in lowered for marker in _AGENT_CONTEXT_MARKERS):
            labels.add("agent_injected_context")
        if tool_name in {"web.run", "browser", "chrome"} or any(marker in tool_name for marker in ("web", "browser", "chrome")):
            labels.add("external_web")
        if any(marker.lower() in lowered for marker in _EXTERNAL_TOOL_MARKERS) or "http://" in lowered or "https://" in lowered:
            labels.add("external_web")
        if repo_root and repo_root in text:
            labels.add("local_repo_code")
        if any(marker in text for marker in _LOCAL_PATH_MARKERS):
            labels.add("local_repo_code")
        if role == "user" and any(marker.lower() in lowered for marker in _USER_INSTRUCTION_MARKERS):
            labels.add("user_instruction")
        if any(marker.lower() in lowered for marker in _THIRD_PARTY_MARKERS) or raw_event in {"document", "attachment"}:
            labels.add("third_party_document")
    return [label for label in LABEL_ORDER if label in labels]
