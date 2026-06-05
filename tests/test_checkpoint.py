from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from ebook_translator.checkpoint import CheckpointManager
from ebook_translator.models import JobState, Segment, TranslationRecord


def make_state(output_dir: str = "outputs/test") -> JobState:
    now = datetime.now(timezone.utc)
    return JobState(
        job_id="test-job-001",
        input_path="books/test.epub",
        output_dir=output_dir,
        status="running",
        total_segments=10,
        completed_segments=0,
        failed_segments=0,
        created_at=now,
        updated_at=now,
    )


def make_record(segment_id: str, status: str = "completed", attempt: int = 1) -> TranslationRecord:
    return TranslationRecord(
        segment_id=segment_id,
        source_hash="aabbccdd",
        status=status,
        source="Original text",
        translation="譯文",
        model="test-model",
        attempt=attempt,
        created_at=datetime.now(timezone.utc),
    )


def test_save_and_load_state(tmp_path):
    mgr = CheckpointManager(tmp_path)
    state = make_state(str(tmp_path))
    mgr.save_state(state)
    loaded = mgr.load_state()
    assert loaded is not None
    assert loaded.job_id == "test-job-001"
    assert loaded.status == "running"


def test_load_state_returns_none_when_missing(tmp_path):
    mgr = CheckpointManager(tmp_path)
    assert mgr.load_state() is None


def test_append_and_load_translations(tmp_path):
    mgr = CheckpointManager(tmp_path)
    r1 = make_record("0000:0000:aabb")
    r2 = make_record("0000:0001:ccdd")
    mgr.append_translation(r1)
    mgr.append_translation(r2)

    completed = mgr.load_completed_ids()
    assert "0000:0000:aabb" in completed
    assert "0000:0001:ccdd" in completed


def test_failed_records_not_in_completed(tmp_path):
    mgr = CheckpointManager(tmp_path)
    mgr.append_translation(make_record("seg-ok", status="completed"))
    mgr.append_translation(make_record("seg-fail", status="failed"))

    completed = mgr.load_completed_ids()
    assert "seg-ok" in completed
    assert "seg-fail" not in completed


def test_load_failed_ids_returns_attempt_count(tmp_path):
    mgr = CheckpointManager(tmp_path)
    mgr.append_translation(make_record("seg-1", status="failed", attempt=1))
    mgr.append_translation(make_record("seg-1", status="failed", attempt=2))

    failed = mgr.load_failed_ids()
    assert failed["seg-1"] == 2


def test_completed_ids_follow_latest_record(tmp_path):
    mgr = CheckpointManager(tmp_path)
    mgr.append_translation(make_record("seg-1", status="completed", attempt=1))
    mgr.append_translation(make_record("seg-1", status="quality_failed", attempt=2))

    assert "seg-1" not in mgr.load_completed_ids()


def test_failed_ids_follow_latest_record(tmp_path):
    mgr = CheckpointManager(tmp_path)
    mgr.append_translation(make_record("seg-1", status="failed", attempt=1))
    mgr.append_translation(make_record("seg-1", status="completed", attempt=2))

    assert "seg-1" not in mgr.load_failed_ids()


def test_partial_last_line_skipped(tmp_path):
    mgr = CheckpointManager(tmp_path)
    mgr.append_translation(make_record("seg-good"))
    # Simulate partial write
    with open(tmp_path / "translations.jsonl", "a", encoding="utf-8") as f:
        f.write('{"segment_id": "seg-bad", "status": "compl')

    completed = mgr.load_completed_ids()
    assert "seg-good" in completed
    assert "seg-bad" not in completed


def test_atomic_state_write(tmp_path):
    mgr = CheckpointManager(tmp_path)
    state = make_state(str(tmp_path))
    mgr.save_state(state)
    # .tmp file should not remain
    assert not (tmp_path / "state.json.tmp").exists()
    assert (tmp_path / "state.json").exists()


def test_save_and_load_segments(tmp_path):
    mgr = CheckpointManager(tmp_path)
    segs = [
        Segment(
            segment_id="0000:0000:aabb",
            chapter_index=0,
            block_index=0,
            sha1_prefix="aabb",
            source_text="Hello",
            source_html="Hello",
            tag_name="p",
            chapter_href="chap01.xhtml",
        )
    ]
    mgr.save_segments(segs)
    loaded = mgr.load_segments()
    assert len(loaded) == 1
    assert loaded[0].segment_id == "0000:0000:aabb"


def test_load_all_translations_returns_latest(tmp_path):
    mgr = CheckpointManager(tmp_path)
    mgr.append_translation(make_record("seg-1", status="failed", attempt=1))
    mgr.append_translation(make_record("seg-1", status="completed", attempt=2))

    all_tr = mgr.load_all_translations()
    assert all_tr["seg-1"].status == "completed"
