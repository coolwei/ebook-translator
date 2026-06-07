"""Tests for the refresh-quality-status CLI command.

This command re-runs the current quality gate on stored quality_failed
translations and, if they now pass, appends a new 'completed' record
(append-only, no API calls).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from ebook_translator.checkpoint import CheckpointManager
from ebook_translator.cli import app
from ebook_translator.config import QualityConfig
from ebook_translator.models import Segment, TranslationRecord
from ebook_translator.validator import validate_translations

runner = CliRunner()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _seg(segment_id: str, source_text: str = "Hello world.") -> Segment:
    sha1 = segment_id.split(":")[-1]
    return Segment(
        segment_id=segment_id,
        chapter_index=0,
        block_index=int(segment_id.split(":")[1]) if ":" in segment_id else 0,
        sha1_prefix=sha1,
        source_text=source_text,
        source_html=source_text,
        tag_name="p",
        chapter_href="chap01.xhtml",
    )


def _qf_record(
    segment_id: str,
    translation: str,
    error: str = "simplified_chinese",
    attempt: int = 1,
) -> TranslationRecord:
    sha1 = segment_id.split(":")[-1]
    return TranslationRecord(
        segment_id=segment_id,
        source_hash=sha1,
        status="quality_failed",
        source="Hello world.",
        translation=translation,
        model="test-model",
        attempt=attempt,
        error=error,
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )


def _setup_job(
    tmp_path: Path,
    segments: list[Segment],
    records: list[TranslationRecord],
) -> Path:
    job_dir = tmp_path / "job"
    checkpoint = CheckpointManager(job_dir)
    checkpoint.save_segments(segments)
    for rec in records:
        checkpoint.append_translation(rec)
    return job_dir


# ---------------------------------------------------------------------------
# Core repair behaviour
# ---------------------------------------------------------------------------


def test_refresh_repairs_false_positive(tmp_path):
    """A quality_failed record whose issue is now a false positive should become completed."""
    # 外勤 contains 勤, which maps to itself in SIMPLIFIED_TO_TRADITIONAL → not flagged.
    seg = _seg("0000:0000:aa")
    rec = _qf_record("0000:0000:aa", "外勤辦公室")
    job_dir = _setup_job(tmp_path, [seg], [rec])

    result = runner.invoke(app, ["refresh-quality-status", str(job_dir), "0000:0000:aa"])

    assert result.exit_code == 0, result.output
    assert "repaired" in result.output or "completed" in result.output

    checkpoint = CheckpointManager(job_dir)
    latest = checkpoint.load_all_translations()["0000:0000:aa"]
    assert latest.status == "completed"
    assert latest.repaired_from_status == "quality_failed"
    assert latest.repair_reason == "quality_recheck_passed"
    assert latest.translation == "外勤辦公室"
    assert latest.attempt == 2  # old attempt was 1


def test_refresh_no_repair_when_still_failing(tmp_path):
    """A genuinely simplified record must not be upgraded to completed."""
    # 美国 contains 国 → 國, which IS a genuine simplified char.
    seg = _seg("0000:0000:aa")
    rec = _qf_record("0000:0000:aa", "美国軍隊")
    job_dir = _setup_job(tmp_path, [seg], [rec])

    result = runner.invoke(app, ["refresh-quality-status", str(job_dir), "0000:0000:aa"])

    assert result.exit_code == 0, result.output
    assert "still failing" in result.output

    checkpoint = CheckpointManager(job_dir)
    latest = checkpoint.load_all_translations()["0000:0000:aa"]
    assert latest.status == "quality_failed", "Must not be upgraded"


def test_refresh_still_failing_shows_match_details(tmp_path):
    """When still failing, match details (char, position, suggestion) should be shown."""
    seg = _seg("0000:0000:aa")
    rec = _qf_record("0000:0000:aa", "美国軍隊")  # 国 at position 1
    job_dir = _setup_job(tmp_path, [seg], [rec])

    result = runner.invoke(app, ["refresh-quality-status", str(job_dir), "0000:0000:aa"])

    assert "国" in result.output
    assert "國" in result.output  # suggestion


def test_refresh_append_only_original_record_preserved(tmp_path):
    """After repair, the original quality_failed record must still be in the JSONL."""
    seg = _seg("0000:0000:aa")
    rec = _qf_record("0000:0000:aa", "外勤辦公室")
    job_dir = _setup_job(tmp_path, [seg], [rec])

    runner.invoke(app, ["refresh-quality-status", str(job_dir), "0000:0000:aa"])

    lines = [
        ln
        for ln in (job_dir / "translations.jsonl").read_text(encoding="utf-8").splitlines()
        if ln.strip()
    ]
    assert len(lines) == 2, "original + repaired = 2 records"
    first = json.loads(lines[0])
    second = json.loads(lines[1])
    assert first["status"] == "quality_failed"
    assert second["status"] == "completed"
    assert second.get("repaired_from_status") == "quality_failed"
    assert second.get("repair_reason") == "quality_recheck_passed"


def test_refresh_already_completed_no_action(tmp_path):
    """Calling refresh on an already-completed segment is harmless."""
    seg = _seg("0000:0000:aa")
    completed_rec = TranslationRecord(
        segment_id="0000:0000:aa",
        source_hash="aa",
        status="completed",
        source="Hello world.",
        translation="你好世界",
        model="test-model",
        attempt=1,
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    job_dir = _setup_job(tmp_path, [seg], [completed_rec])

    result = runner.invoke(app, ["refresh-quality-status", str(job_dir), "0000:0000:aa"])

    assert result.exit_code == 0, result.output
    assert "already completed" in result.output

    # No extra record appended.
    lines = [
        ln
        for ln in (job_dir / "translations.jsonl").read_text(encoding="utf-8").splitlines()
        if ln.strip()
    ]
    assert len(lines) == 1


def test_refresh_all_quality_failed_batch(tmp_path):
    """--all-quality-failed repairs false positives and leaves genuine failures alone."""
    segs = [
        _seg("0000:0000:aa"),
        _seg("0000:0001:bb"),
        _seg("0000:0002:cc"),
    ]
    recs = [
        _qf_record("0000:0000:aa", "外勤辦公室"),  # 勤 is same-form → false positive → repair
        _qf_record("0000:0001:bb", "憲兵部隊"),    # 兵 is same-form → false positive → repair
        _qf_record("0000:0002:cc", "美国軍隊"),    # 国→國, genuine → no repair
    ]
    job_dir = _setup_job(tmp_path, segs, recs)

    result = runner.invoke(
        app, ["refresh-quality-status", str(job_dir), "--all-quality-failed"]
    )

    assert result.exit_code == 0, result.output
    assert "2/3" in result.output  # "Done: 2/3 repaired → completed."

    checkpoint = CheckpointManager(job_dir)
    all_records = checkpoint.load_all_translations()
    assert all_records["0000:0000:aa"].status == "completed"
    assert all_records["0000:0001:bb"].status == "completed"
    assert all_records["0000:0002:cc"].status == "quality_failed"


def test_refresh_all_quality_failed_no_segments(tmp_path):
    """--all-quality-failed with no quality_failed segments prints a helpful message."""
    seg = _seg("0000:0000:aa")
    completed_rec = TranslationRecord(
        segment_id="0000:0000:aa",
        source_hash="aa",
        status="completed",
        source="Hello.",
        translation="你好",
        model="test-model",
        attempt=1,
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    job_dir = _setup_job(tmp_path, [seg], [completed_rec])

    result = runner.invoke(
        app, ["refresh-quality-status", str(job_dir), "--all-quality-failed"]
    )

    assert result.exit_code == 0, result.output
    assert "no quality_failed" in result.output.lower()


# ---------------------------------------------------------------------------
# Error paths
# ---------------------------------------------------------------------------


def test_refresh_error_when_no_target(tmp_path):
    """Neither segment_id nor --all-quality-failed → exit 1 with helpful message."""
    job_dir = tmp_path / "job"
    job_dir.mkdir()
    (job_dir / "segments.jsonl").write_text("", encoding="utf-8")
    (job_dir / "translations.jsonl").write_text("", encoding="utf-8")

    result = runner.invoke(app, ["refresh-quality-status", str(job_dir)])

    assert result.exit_code == 1
    output_combined = result.output
    assert "SEGMENT_ID" in output_combined or "all-quality-failed" in output_combined


def test_refresh_directory_not_found(tmp_path):
    """Non-existent output_dir → exit 1."""
    result = runner.invoke(
        app, ["refresh-quality-status", str(tmp_path / "nonexistent"), "0000:0000:aa"]
    )
    assert result.exit_code == 1
    assert "not found" in result.output.lower()


def test_refresh_segment_not_found(tmp_path):
    """Unknown segment_id exits gracefully (no crash)."""
    job_dir = tmp_path / "job"
    job_dir.mkdir()
    (job_dir / "segments.jsonl").write_text("", encoding="utf-8")
    (job_dir / "translations.jsonl").write_text("", encoding="utf-8")

    result = runner.invoke(
        app, ["refresh-quality-status", str(job_dir), "9999:9999:xx"]
    )
    assert result.exit_code == 0  # doesn't crash; error printed per-segment


# ---------------------------------------------------------------------------
# No API calls guarantee
# ---------------------------------------------------------------------------


def test_refresh_makes_no_api_calls(tmp_path):
    """refresh-quality-status must never instantiate or call any provider."""
    seg = _seg("0000:0000:aa")
    rec = _qf_record("0000:0000:aa", "外勤辦公室")
    job_dir = _setup_job(tmp_path, [seg], [rec])

    with patch(
        "ebook_translator.translator.OpenAICompatibleProvider",
        side_effect=AssertionError("Provider must not be called"),
    ):
        result = runner.invoke(
            app, ["refresh-quality-status", str(job_dir), "0000:0000:aa"]
        )

    assert result.exit_code == 0, result.output


# ---------------------------------------------------------------------------
# Validate integration
# ---------------------------------------------------------------------------


def test_validate_uses_latest_completed_after_repair(tmp_path):
    """After repair, validate_translations should report 0 errors for the repaired segment."""
    seg = _seg("0000:0000:aa")
    rec = _qf_record("0000:0000:aa", "外勤辦公室")
    job_dir = _setup_job(tmp_path, [seg], [rec])

    # Before repair: 1 error (quality_failed status).
    checkpoint = CheckpointManager(job_dir)
    segs = checkpoint.load_segments()
    translations = checkpoint.load_all_translations()
    pairs_before = [(s, translations[s.segment_id]) for s in segs if s.segment_id in translations]
    report_before = validate_translations(pairs_before, QualityConfig(strict_mode=True))
    assert report_before.errors >= 1

    # Run repair.
    runner.invoke(app, ["refresh-quality-status", str(job_dir), "0000:0000:aa"])

    # After repair: 0 errors.
    translations_after = checkpoint.load_all_translations()
    pairs_after = [(s, translations_after[s.segment_id]) for s in segs if s.segment_id in translations_after]
    report_after = validate_translations(pairs_after, QualityConfig(strict_mode=True))
    assert report_after.errors == 0, (
        f"Expected 0 errors after repair, got {report_after.errors}: "
        f"{[i.detail for i in report_after.issues]}"
    )


def test_repaired_record_has_correct_fields(tmp_path):
    """The appended completed record must have the correct metadata fields."""
    seg = _seg("0000:0000:aa")
    rec = _qf_record("0000:0000:aa", "外勤辦公室", attempt=3)
    job_dir = _setup_job(tmp_path, [seg], [rec])

    runner.invoke(app, ["refresh-quality-status", str(job_dir), "0000:0000:aa"])

    checkpoint = CheckpointManager(job_dir)
    latest = checkpoint.load_all_translations()["0000:0000:aa"]

    assert latest.status == "completed"
    assert latest.attempt == 4  # old was 3 → new is 4
    assert latest.model == "test-model"
    assert latest.translation == "外勤辦公室"
    assert latest.repaired_from_status == "quality_failed"
    assert latest.repair_reason == "quality_recheck_passed"
    assert latest.error is None
    assert latest.quality_matches is None
