from __future__ import annotations

import json
import re
from dataclasses import dataclass

_FENCE_RE = re.compile(r"^```(?:json)?\s*([\s\S]*?)\s*```\s*$", re.IGNORECASE)


@dataclass
class BatchParseResult:
    translations: dict[str, str]
    missing_ids: list[str]
    parse_error: str | None = None


class BatchParseError(Exception):
    pass


def strip_markdown_fence(text: str) -> str:
    stripped = text.strip()
    m = _FENCE_RE.match(stripped)
    if m:
        return m.group(1).strip()
    return stripped


def parse_batch_response(raw: str, expected_ids: set[str]) -> BatchParseResult:
    """Parse a model JSON-array batch response into per-segment translations."""
    cleaned = strip_markdown_fence(raw)
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        return BatchParseResult(
            translations={},
            missing_ids=sorted(expected_ids),
            parse_error=f"invalid JSON: {exc}",
        )

    if not isinstance(data, list):
        return BatchParseResult(
            translations={},
            missing_ids=sorted(expected_ids),
            parse_error="response is not a JSON array",
        )

    translations: dict[str, str] = {}
    seen: set[str] = set()
    for index, item in enumerate(data):
        if not isinstance(item, dict):
            return BatchParseResult(
                translations={},
                missing_ids=sorted(expected_ids),
                parse_error=f"item {index} is not an object",
            )
        segment_id = item.get("segment_id")
        translation = item.get("translation")
        if not segment_id or translation is None:
            return BatchParseResult(
                translations={},
                missing_ids=sorted(expected_ids),
                parse_error=f"item {index} missing segment_id or translation",
            )
        sid = str(segment_id)
        if sid not in expected_ids:
            return BatchParseResult(
                translations={},
                missing_ids=sorted(expected_ids),
                parse_error=f"unknown segment_id: {sid}",
            )
        if sid in seen:
            return BatchParseResult(
                translations={},
                missing_ids=sorted(expected_ids),
                parse_error=f"duplicate segment_id: {sid}",
            )
        seen.add(sid)
        translations[sid] = str(translation)

    missing = sorted(expected_ids - set(translations))
    return BatchParseResult(
        translations=translations,
        missing_ids=missing,
        parse_error=None if not missing else None,
    )