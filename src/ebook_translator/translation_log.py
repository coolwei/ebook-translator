from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

from .config import LoggingConfig

_SENSITIVE_PATTERNS = (
    re.compile(r"Bearer\s+\S+", re.IGNORECASE),
    re.compile(r"Authorization\s*:\s*\S+", re.IGNORECASE),
    re.compile(r"sk-[A-Za-z0-9_-]{8,}", re.IGNORECASE),
    re.compile(r"api[_-]?key\s*[:=]\s*\S+", re.IGNORECASE),
    re.compile(r"https?://[^\s/]+/v1", re.IGNORECASE),
)


def sanitize_log_text(text: str | None) -> str:
    if not text:
        return ""
    cleaned = text
    for pattern in _SENSITIVE_PATTERNS:
        cleaned = pattern.sub("[redacted]", cleaned)
    return cleaned.strip()


class TranslationLogWriter:
    def __init__(self, paths: list[Path], level: str, book_name: str) -> None:
        self._paths = paths
        self._book_name = book_name
        self._min_level = _level_value(level)
        for path in paths:
            path.parent.mkdir(parents=True, exist_ok=True)

    @classmethod
    def from_config(
        cls,
        config: LoggingConfig,
        *,
        book_name: str,
        output_dir: Path,
    ) -> TranslationLogWriter | None:
        if not config.enabled:
            return None
        paths: list[Path] = [Path(config.file)]
        if config.per_book:
            paths.append(output_dir / "translation.log")
        return cls(paths, config.level, book_name)

    def log(
        self,
        *,
        segment_id: str,
        model: str,
        status: str,
        batch_index: int | None = None,
        request_batch_size: int | None = None,
        batch_parse_error: str = "",
        missing_from_batch_response: str = "",
        error_type: str = "",
        error_message: str = "",
        fallback_from: str = "",
        fallback_to: str = "",
        attempt: int = 1,
        duration_ms: int | None = None,
        level: str = "INFO",
    ) -> None:
        if _level_value(level) < self._min_level:
            return

        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
        parts = [
            f"{ts} [{level.upper()}]",
            f"book={self._book_name}",
            f"segment={segment_id}",
        ]
        if batch_index is not None:
            parts.append(f"batch_index={batch_index}")
        if request_batch_size is not None:
            parts.append(f"request_batch_size={request_batch_size}")
        if batch_parse_error:
            parts.append(f"batch_parse_error={sanitize_log_text(batch_parse_error)}")
        if missing_from_batch_response:
            parts.append(
                f"missing_from_batch_response={sanitize_log_text(missing_from_batch_response)}"
            )
        parts.extend([
            f"model={model}",
            f"status={status}",
            f"attempt={attempt}",
        ])
        if error_type:
            parts.append(f"error_type={sanitize_log_text(error_type)}")
        if error_message:
            parts.append(f"error={sanitize_log_text(error_message)}")
        if fallback_from:
            parts.append(f"fallback_from={fallback_from}")
        if fallback_to:
            parts.append(f"fallback_to={fallback_to}")
        if duration_ms is not None:
            parts.append(f"duration_ms={duration_ms}")

        line = " ".join(parts) + "\n"
        for path in self._paths:
            with open(path, "a", encoding="utf-8") as f:
                f.write(line)


def _level_value(level: str) -> int:
    return {
        "DEBUG": 10,
        "INFO": 20,
        "WARNING": 30,
        "ERROR": 40,
    }.get(level.upper(), 20)