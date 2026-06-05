"""test_cli_report_missing.py — tests for the `report-missing` CLI command.

Requirements verified:
* CLI can be invoked (via typer.testing.CliRunner)
* missing_translation_report.json is written to the job directory
* No provider is called
* translation_failed blocks appear in the report
* Blocks without a segment appear with reason no_segment_extracted
* Fully-translated jobs produce an empty report (nothing missing)
* Missing segments.jsonl → CLI exits with code 1 and helpful message
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from ebook_translator.cli import app
from ebook_translator.models import Segment, TranslationRecord


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

runner = CliRunner()


def _write_config(tmp_path: Path, epub_path: Path) -> Path:
    """Write a minimal config.yaml that points at *epub_path*."""
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(
        f"input:\n  path: {epub_path.as_posix()}\n"
        "provider:\n"
        "  base_url: https://api.example.com/v1\n"
        "  api_key_env: FAKE_KEY\n"
        "  model: test-model\n"
        "cli:\n"
        "  language: en\n",
        encoding="utf-8",
    )
    return cfg_path


def _make_segment(
    segment_id: str,
    source_text: str,
    block_index: int = 0,
    href: str = "chap01.xhtml",
    chapter_index: int = 0,
) -> Segment:
    return Segment(
        segment_id=segment_id,
        chapter_index=chapter_index,
        block_index=block_index,
        sha1_prefix=segment_id.split(":")[-1],
        source_text=source_text,
        source_html=source_text,
        tag_name="p",
        chapter_href=href,
    )


def _make_record(
    segment_id: str,
    *,
    status: str = "completed",
    translation: str = "已翻譯。",
    error: str | None = None,
) -> TranslationRecord:
    return TranslationRecord(
        segment_id=segment_id,
        source_hash=segment_id.split(":")[-1],
        status=status,  # type: ignore[arg-type]
        source="source",
        translation=translation,
        model="mock",
        attempt=1,
        error=error,
        created_at=datetime.now(timezone.utc),
    )


def _write_segments(job_dir: Path, segments: list[Segment]) -> None:
    with open(job_dir / "segments.jsonl", "w", encoding="utf-8") as f:
        for seg in segments:
            f.write(seg.model_dump_json() + "\n")


def _write_translations(job_dir: Path, records: list[TranslationRecord]) -> None:
    with open(job_dir / "translations.jsonl", "w", encoding="utf-8") as f:
        for rec in records:
            f.write(rec.model_dump_json() + "\n")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_report_missing_cli_is_registered():
    """The `report-missing` sub-command must appear in the app's help output."""
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0, result.output
    assert "report-missing" in result.output


def test_report_missing_exits_if_no_segments_jsonl(tmp_path, sample_epub_path):
    """If segments.jsonl is absent, CLI should exit with code 1."""
    job_dir = tmp_path / "job"
    job_dir.mkdir()
    cfg = _write_config(tmp_path, sample_epub_path)

    result = runner.invoke(app, ["report-missing", str(job_dir), "--config", str(cfg)])
    assert result.exit_code == 1
    assert "segments.jsonl" in result.output


def test_report_missing_exits_if_job_dir_not_found(tmp_path, sample_epub_path):
    """If job dir does not exist, CLI should exit with code 1."""
    cfg = _write_config(tmp_path, sample_epub_path)
    result = runner.invoke(app, ["report-missing", str(tmp_path / "nonexistent"), "--config", str(cfg)])
    assert result.exit_code == 1
    assert "not found" in result.output.lower()


def test_report_missing_creates_json_file(tmp_path, sample_epub_path):
    """report-missing must write missing_translation_report.json to the job dir."""
    job_dir = tmp_path / "job"
    job_dir.mkdir()
    cfg = _write_config(tmp_path, sample_epub_path)

    # Provide empty segments/translations so the command can run
    (job_dir / "segments.jsonl").write_text("", encoding="utf-8")
    (job_dir / "translations.jsonl").write_text("", encoding="utf-8")

    result = runner.invoke(app, ["report-missing", str(job_dir), "--config", str(cfg)])
    assert result.exit_code == 0, result.output

    report_path = job_dir / "missing_translation_report.json"
    assert report_path.exists(), "missing_translation_report.json was not created"


def test_report_missing_provider_never_called(tmp_path, sample_epub_path):
    """report-missing must not call any translation provider."""
    from ebook_translator.providers.openai_compatible import OpenAICompatibleProvider

    job_dir = tmp_path / "job"
    job_dir.mkdir()
    cfg = _write_config(tmp_path, sample_epub_path)
    (job_dir / "segments.jsonl").write_text("", encoding="utf-8")
    (job_dir / "translations.jsonl").write_text("", encoding="utf-8")

    with patch.object(OpenAICompatibleProvider, "__init__", side_effect=AssertionError("Provider should not be instantiated")) as mock_init:
        result = runner.invoke(app, ["report-missing", str(job_dir), "--config", str(cfg)])
    # If the provider had been called, the patched __init__ would raise AssertionError
    # and the command would exit with code 1 / traceback.
    assert result.exit_code == 0, result.output


def test_report_missing_shows_translation_failed(tmp_path, sample_epub_path):
    """A translation_failed segment must appear in the report with the correct reason."""
    from ebook_translator.epub.reader import read_epub
    from ebook_translator.segmenter.segmenter import segment_all_documents

    _, spine_docs = read_epub(sample_epub_path)
    all_segs = segment_all_documents(spine_docs)
    assert len(all_segs) >= 1, "sample_epub must have at least one segment"

    job_dir = tmp_path / "job"
    job_dir.mkdir()
    cfg = _write_config(tmp_path, sample_epub_path)

    # All segments have a failed translation record
    failed_records = [
        _make_record(seg.segment_id, status="failed", translation="", error="Provider error")
        for seg in all_segs
    ]
    _write_segments(job_dir, all_segs)
    _write_translations(job_dir, failed_records)

    result = runner.invoke(app, ["report-missing", str(job_dir), "--config", str(cfg)])
    assert result.exit_code == 0, result.output

    report_path = job_dir / "missing_translation_report.json"
    data = json.loads(report_path.read_text(encoding="utf-8"))

    assert len(data) == len(all_segs), "All failed segments must appear in the report"
    reasons = {entry["reason"] for entry in data}
    assert "translation_failed" in reasons

    # stdout must contain the summary
    assert "translation_failed" in result.output
    assert "missing_count" in result.output


def test_report_missing_shows_no_segment_extracted(tmp_path, sample_epub_path):
    """Blocks with no matching segment must appear with reason no_segment_extracted."""
    job_dir = tmp_path / "job"
    job_dir.mkdir()
    cfg = _write_config(tmp_path, sample_epub_path)

    # Empty segments.jsonl — simulates that segmenter never ran or ran on wrong doc
    _write_segments(job_dir, [])
    _write_translations(job_dir, [])

    result = runner.invoke(app, ["report-missing", str(job_dir), "--config", str(cfg)])
    assert result.exit_code == 0, result.output

    data = json.loads((job_dir / "missing_translation_report.json").read_text(encoding="utf-8"))
    reasons = {entry["reason"] for entry in data}
    assert "no_segment_extracted" in reasons


def test_report_missing_empty_when_all_translated(tmp_path, sample_epub_path):
    """If every block has a completed translation, the report must be empty."""
    from ebook_translator.epub.reader import read_epub
    from ebook_translator.segmenter.segmenter import segment_all_documents

    _, spine_docs = read_epub(sample_epub_path)
    all_segs = segment_all_documents(spine_docs)

    job_dir = tmp_path / "job"
    job_dir.mkdir()
    cfg = _write_config(tmp_path, sample_epub_path)

    completed_records = [
        _make_record(seg.segment_id, status="completed", translation="已翻譯。")
        for seg in all_segs
    ]
    _write_segments(job_dir, all_segs)
    _write_translations(job_dir, completed_records)

    result = runner.invoke(app, ["report-missing", str(job_dir), "--config", str(cfg)])
    assert result.exit_code == 0, result.output

    data = json.loads((job_dir / "missing_translation_report.json").read_text(encoding="utf-8"))
    assert data == [], f"Expected empty report when all translated; got {data}"
    assert "all blocks translated" in result.output


def test_report_missing_console_shows_totals(tmp_path, sample_epub_path):
    """Console output must include total_checked_blocks and missing_count."""
    from ebook_translator.epub.reader import read_epub
    from ebook_translator.segmenter.segmenter import segment_all_documents

    _, spine_docs = read_epub(sample_epub_path)
    all_segs = segment_all_documents(spine_docs)

    job_dir = tmp_path / "job"
    job_dir.mkdir()
    cfg = _write_config(tmp_path, sample_epub_path)

    # One failed, rest completed
    records = []
    for i, seg in enumerate(all_segs):
        if i == 0:
            records.append(_make_record(seg.segment_id, status="failed", translation="", error="err"))
        else:
            records.append(_make_record(seg.segment_id, status="completed", translation="已翻譯。"))
    _write_segments(job_dir, all_segs)
    _write_translations(job_dir, records)

    result = runner.invoke(app, ["report-missing", str(job_dir), "--config", str(cfg)])
    assert result.exit_code == 0, result.output

    assert "total_checked_blocks" in result.output
    assert "missing_count" in result.output
