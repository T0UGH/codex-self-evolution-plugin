from __future__ import annotations

import re
from typing import Any


def validate_publishable_skill(item: dict[str, Any]) -> tuple[bool, str | None]:
    action = str(item.get("action") or "").strip().lower()
    if action == "retire":
        return True, None

    title = str(item.get("title") or "").strip()
    description = str(item.get("description") or "").strip()
    content = str(item.get("content") or "").strip()

    if not title:
        return False, "missing_title"
    if not description:
        return False, "missing_description"
    if not content:
        return False, "missing_content"

    description_lower = description.lower()
    if "use" not in description_lower or "when" not in description_lower:
        return False, "weak_description"

    words = [word for word in re.split(r"\s+", content) if word]
    if len(words) < 12:
        return False, "low_signal"
    if not any(marker in content.lower() for marker in ("workflow", "steps", "run ", "inspect", "verify", "check")):
        return False, "low_signal"

    return True, None
