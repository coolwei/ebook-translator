from __future__ import annotations

import json
from pathlib import Path

from .models import JobState, Segment, TranslationRecord


class CheckpointManager:
    def __init__(self, output_dir: Path) -> None:
        self.output_dir = output_dir
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self._state_path = output_dir / "state.json"
        self._segments_path = output_dir / "segments.jsonl"
        self._translations_path = output_dir / "translations.jsonl"

    def save_state(self, state: JobState) -> None:
        tmp = self._state_path.with_suffix(".json.tmp")
        tmp.write_text(state.model_dump_json(indent=2), encoding="utf-8")
        tmp.replace(self._state_path)

    def load_state(self) -> JobState | None:
        if not self._state_path.exists():
            return None
        try:
            return JobState.model_validate_json(self._state_path.read_text(encoding="utf-8"))
        except Exception:
            return None

    def append_translation(self, record: TranslationRecord) -> None:
        with open(self._translations_path, "a", encoding="utf-8") as f:
            f.write(record.model_dump_json() + "\n")

    def save_segments(self, segments: list[Segment]) -> None:
        with open(self._segments_path, "w", encoding="utf-8") as f:
            for seg in segments:
                f.write(seg.model_dump_json() + "\n")

    def load_segments(self) -> list[Segment]:
        if not self._segments_path.exists():
            return []
        segments = []
        for line in self._segments_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                segments.append(Segment.model_validate_json(line))
            except Exception:
                continue
        return segments

    def load_completed_ids(self) -> set[str]:
        completed = set()
        if not self._translations_path.exists():
            return completed
        for line in self._translations_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
                if data.get("status") == "completed":
                    completed.add(data["segment_id"])
            except Exception:
                continue
        return completed

    def load_failed_ids(self) -> dict[str, int]:
        failed: dict[str, int] = {}
        if not self._translations_path.exists():
            return failed
        for line in self._translations_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
                if data.get("status") == "failed":
                    sid = data["segment_id"]
                    failed[sid] = max(failed.get(sid, 0), data.get("attempt", 1))
            except Exception:
                continue
        return failed

    def load_all_translations(self) -> dict[str, TranslationRecord]:
        records: dict[str, TranslationRecord] = {}
        if not self._translations_path.exists():
            return records
        for line in self._translations_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                record = TranslationRecord.model_validate_json(line)
                records[record.segment_id] = record
            except Exception:
                continue
        return records
