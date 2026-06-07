from __future__ import annotations

import json
import re
from pathlib import Path
from unittest.mock import patch

from typer.testing import CliRunner

from ebook_translator.cli import app
from ebook_translator.providers.base import ProviderError, TranslationResponse
from ebook_translator.safe_mode import count_rate_limit_errors_since
from tests.conftest import MockTranslationProvider, make_sample_epub
from tests.test_start import _book_dir, _records, _write_config


runner = CliRunner()


def _make_books_dir(tmp_path: Path) -> tuple[Path, Path]:
    books_dir = tmp_path / "books"
    books_dir.mkdir()
    book_path = make_sample_epub(books_dir)
    return books_dir, book_path


def test_start_dry_run_batch_size_shows_batch_plan(tmp_path):
    books_dir, book_path = _make_books_dir(tmp_path)
    cfg = _write_config(tmp_path, book_path)

    with patch(
        "ebook_translator.translator.OpenAICompatibleProvider",
        side_effect=AssertionError("provider should not be instantiated"),
    ):
        result = runner.invoke(
            app,
            [
                "start",
                "--config",
                str(cfg),
                "--books-dir",
                str(books_dir),
                "--dry-run",
                "--batch-size",
                "2",
                "--cooldown-seconds",
                "30",
                "--stop-on-rate-limit-count",
                "5",
                "--max-segments",
                "10",
            ],
        )

    assert result.exit_code == 0, result.output
    assert "Large-book safe mode batch plan" in result.output
    assert re.search(r"Total segments: \d+", result.output)
    assert re.search(r"Segments after max-segments: \d+", result.output)
    assert "Batch size: 2" in result.output
    assert re.search(r"Estimated batches: \d+", result.output)
    assert "Cooldown seconds: 30" in result.output
    assert "Stop on rate limit count: 5" in result.output
    assert "Dry run complete" in result.output


def test_batch_size_splits_translation_into_batches(tmp_path):
    books_dir, book_path = _make_books_dir(tmp_path)
    cfg = _write_config(tmp_path, book_path)
    provider = MockTranslationProvider()

    with patch("ebook_translator.translator.OpenAICompatibleProvider", return_value=provider):
        result = runner.invoke(
            app,
            [
                "start",
                "--config",
                str(cfg),
                "--books-dir",
                str(books_dir),
                "--yes",
                "--batch-size",
                "2",
            ],
            env={"FAKE_KEY": "test-key"},
        )

    assert result.exit_code == 0, result.output
    assert provider.call_count == 5
    assert "Starting batch 1" in result.output
    assert "Starting batch 2" in result.output
    assert "Starting batch 3" in result.output
    assert len(_records(_book_dir(tmp_path))) == 5


def test_cooldown_seconds_calls_sleep_between_batches(tmp_path):
    books_dir, book_path = _make_books_dir(tmp_path)
    cfg = _write_config(tmp_path, book_path)
    provider = MockTranslationProvider()

    with (
        patch("ebook_translator.translator.OpenAICompatibleProvider", return_value=provider),
        patch("ebook_translator.cli.time.sleep") as mock_sleep,
    ):
        result = runner.invoke(
            app,
            [
                "start",
                "--config",
                str(cfg),
                "--books-dir",
                str(books_dir),
                "--yes",
                "--batch-size",
                "2",
                "--cooldown-seconds",
                "60",
            ],
            env={"FAKE_KEY": "test-key"},
        )

    assert result.exit_code == 0, result.output
    assert mock_sleep.call_count == 2
    mock_sleep.assert_called_with(60)


def test_stop_on_rate_limit_count_stops_safely(tmp_path):
    books_dir, book_path = _make_books_dir(tmp_path)
    cfg = _write_config(tmp_path, book_path, max_retries=0)
    cfg_text = cfg.read_text(encoding="utf-8").replace(
        "resume:",
        "resume:\n  retry_failed: false",
    )
    cfg.write_text(cfg_text, encoding="utf-8")

    class _RateLimitProvider(MockTranslationProvider):
        async def translate(self, request):
            self.call_count += 1
            raise ProviderError("Rate limit exceeded: openai_error")

    provider = _RateLimitProvider()

    with patch("ebook_translator.translator.OpenAICompatibleProvider", return_value=provider):
        result = runner.invoke(
            app,
            [
                "start",
                "--config",
                str(cfg),
                "--books-dir",
                str(books_dir),
                "--yes",
                "--batch-size",
                "1",
                "--stop-on-rate-limit-count",
                "2",
            ],
            env={"FAKE_KEY": "test-key"},
        )

    assert result.exit_code == 0, result.output
    assert provider.call_count == 2
    assert "Stopped: accumulated 2 rate limit error(s)" in result.output
    assert (_book_dir(tmp_path) / "translated.epub").exists()
    job = _book_dir(tmp_path)
    assert (job / "state.json").exists()
    assert (job / "translations.jsonl").exists()
    assert len(_records(job)) == 2


def test_safe_mode_checkpoint_resume_across_runs(tmp_path):
    books_dir, book_path = _make_books_dir(tmp_path)
    cfg = _write_config(tmp_path, book_path)
    provider1 = MockTranslationProvider()
    provider2 = MockTranslationProvider()

    with patch("ebook_translator.translator.OpenAICompatibleProvider", return_value=provider1):
        result1 = runner.invoke(
            app,
            [
                "start",
                "--config",
                str(cfg),
                "--books-dir",
                str(books_dir),
                "--yes",
                "--batch-size",
                "2",
                "--limit",
                "2",
            ],
            env={"FAKE_KEY": "test-key"},
        )

    assert result1.exit_code == 0, result1.output
    assert provider1.call_count == 2
    state1 = json.loads((_book_dir(tmp_path) / "state.json").read_text(encoding="utf-8"))
    assert state1["completed_segments"] == 2

    with patch("ebook_translator.translator.OpenAICompatibleProvider", return_value=provider2):
        result2 = runner.invoke(
            app,
            [
                "start",
                "--config",
                str(cfg),
                "--books-dir",
                str(books_dir),
                "--yes",
                "--batch-size",
                "2",
            ],
            env={"FAKE_KEY": "test-key"},
        )

    assert result2.exit_code == 0, result2.output
    assert provider2.call_count == 3
    state2 = json.loads((_book_dir(tmp_path) / "state.json").read_text(encoding="utf-8"))
    assert state2["completed_segments"] == 5


def test_post_batch_validate_report_missing_export_are_called(tmp_path):
    books_dir, book_path = _make_books_dir(tmp_path)
    cfg = _write_config(tmp_path, book_path)
    provider = MockTranslationProvider()

    with (
        patch("ebook_translator.translator.OpenAICompatibleProvider", return_value=provider),
        patch("ebook_translator.translator.run_validate") as mock_validate,
        patch("ebook_translator.translator.run_report_missing") as mock_report_missing,
        patch("ebook_translator.translator.run_export") as mock_export,
    ):
        result = runner.invoke(
            app,
            [
                "start",
                "--config",
                str(cfg),
                "--books-dir",
                str(books_dir),
                "--yes",
                "--batch-size",
                "2",
            ],
            env={"FAKE_KEY": "test-key"},
        )

    assert result.exit_code == 0, result.output
    assert mock_validate.call_count == 3
    assert mock_report_missing.call_count == 3
    assert mock_export.call_count == 3


class _RecoverAfterRateLimitProvider(MockTranslationProvider):
    def __init__(self, fail_ids: set[str]) -> None:
        super().__init__()
        self.fail_ids = fail_ids

    async def translate(self, request):
        self.call_count += 1
        sid = request.segment.segment_id
        if sid in self.fail_ids:
            raise ProviderError("Rate limit exceeded: openai_error")
        return TranslationResponse(
            translated_text=f"譯文內容{request.segment.sha1_prefix}",
            model=request.model,
            finish_reason="stop",
            prompt_tokens=1,
            completion_tokens=1,
        )


def test_rate_limit_counter_uses_translation_record_errors(tmp_path):
    books_dir, book_path = _make_books_dir(tmp_path)
    from ebook_translator.epub.reader import read_epub
    from ebook_translator.segmenter.segmenter import segment_all_documents

    _, spine_docs = read_epub(book_path)
    segments = segment_all_documents(spine_docs)
    fail_ids = {segments[0].segment_id, segments[1].segment_id}
    cfg = _write_config(tmp_path, book_path, max_retries=0)
    cfg_text = cfg.read_text(encoding="utf-8").replace(
        "resume:",
        "resume:\n  retry_failed: false",
    )
    cfg.write_text(cfg_text, encoding="utf-8")
    provider = _RecoverAfterRateLimitProvider(fail_ids)

    with patch("ebook_translator.translator.OpenAICompatibleProvider", return_value=provider):
        result = runner.invoke(
            app,
            [
                "start",
                "--config",
                str(cfg),
                "--books-dir",
                str(books_dir),
                "--yes",
                "--batch-size",
                "3",
                "--stop-on-rate-limit-count",
                "2",
            ],
            env={"FAKE_KEY": "test-key"},
        )

    assert result.exit_code == 0, result.output
    assert "Stopped: accumulated 2 rate limit error(s)" in result.output
    latest = {record["segment_id"]: record for record in _records(_book_dir(tmp_path))}
    assert latest[segments[0].segment_id]["error"] == "Rate limit exceeded: openai_error"
    assert latest[segments[1].segment_id]["error"] == "Rate limit exceeded: openai_error"


# ---------------------------------------------------------------------------
# Unit tests for count_rate_limit_errors_since JSONL corruption handling
# ---------------------------------------------------------------------------

def _rate_limit_record(segment_id: str = "0000:0001:aabbccdd") -> str:
    return json.dumps({
        "segment_id": segment_id,
        "status": "failed",
        "error": "Rate limit exceeded: openai_error",
    })


def _failed_non_rate_limit_record(segment_id: str = "0000:0002:bbccddee") -> str:
    return json.dumps({
        "segment_id": segment_id,
        "status": "failed",
        "error": "Context length exceeded",
    })


def _completed_record(segment_id: str = "0000:0003:ccddeeff") -> str:
    return json.dumps({
        "segment_id": segment_id,
        "status": "completed",
        "translation": "譯文",
    })


def _write_jsonl(job_dir: Path, lines: list[str]) -> None:
    job_dir.mkdir(parents=True, exist_ok=True)
    (job_dir / "translations.jsonl").write_text(
        "\n".join(lines) + "\n", encoding="utf-8"
    )


def test_count_rate_limit_normal_records(tmp_path):
    _write_jsonl(tmp_path, [
        _rate_limit_record("0:1:aaa"),
        _rate_limit_record("0:2:bbb"),
        _completed_record("0:3:ccc"),
    ])
    assert count_rate_limit_errors_since(tmp_path, 0) == 2


def test_count_rate_limit_corrupt_line_does_not_crash(tmp_path):
    _write_jsonl(tmp_path, [
        _rate_limit_record("0:1:aaa"),
        '{"unterminated string: ',
        _rate_limit_record("0:2:bbb"),
    ])
    assert count_rate_limit_errors_since(tmp_path, 0) == 2


def test_count_rate_limit_corrupt_line_before_valid_records(tmp_path):
    _write_jsonl(tmp_path, [
        '{"bad json',
        _rate_limit_record("0:1:aaa"),
        _rate_limit_record("0:2:bbb"),
    ])
    assert count_rate_limit_errors_since(tmp_path, 0) == 2


def test_count_rate_limit_empty_lines_ignored(tmp_path):
    job_dir = tmp_path
    job_dir.mkdir(parents=True, exist_ok=True)
    (job_dir / "translations.jsonl").write_text(
        _rate_limit_record("0:1:aaa") + "\n\n\n" + _rate_limit_record("0:2:bbb") + "\n",
        encoding="utf-8",
    )
    assert count_rate_limit_errors_since(job_dir, 0) == 2


def test_count_rate_limit_non_rate_limit_failed_not_counted(tmp_path):
    _write_jsonl(tmp_path, [
        _rate_limit_record("0:1:aaa"),
        _failed_non_rate_limit_record("0:2:bbb"),
        _completed_record("0:3:ccc"),
    ])
    assert count_rate_limit_errors_since(tmp_path, 0) == 1


def test_count_rate_limit_since_offset_respected(tmp_path):
    _write_jsonl(tmp_path, [
        _rate_limit_record("0:1:aaa"),
        _rate_limit_record("0:2:bbb"),
        _rate_limit_record("0:3:ccc"),
    ])
    assert count_rate_limit_errors_since(tmp_path, 2) == 1


# ---------------------------------------------------------------------------
# pending_segment_count excludes quality_failed
# ---------------------------------------------------------------------------

def test_pending_segment_count_excludes_quality_failed(tmp_path):
    """quality_failed segments should not be counted as pending."""
    import json
    from ebook_translator.safe_mode import pending_segment_count
    from tests.conftest import make_sample_config, make_sample_epub

    epub_path = make_sample_epub(tmp_path)
    cfg = make_sample_config(tmp_path, epub_path)

    job_dir = tmp_path / "job"
    job_dir.mkdir()

    # Write one segment and one quality_failed translation record.
    (job_dir / "segments.jsonl").write_text(
        json.dumps({
            "segment_id": "0000:0000:aabb",
            "chapter_index": 0,
            "block_index": 0,
            "sha1_prefix": "aabb",
            "source_text": "Hello",
            "source_html": "Hello",
            "tag_name": "p",
            "chapter_href": "chap01.xhtml",
        }) + "\n",
        encoding="utf-8",
    )
    from datetime import datetime, timezone
    (job_dir / "translations.jsonl").write_text(
        json.dumps({
            "segment_id": "0000:0000:aabb",
            "source_hash": "aabb",
            "status": "quality_failed",
            "source": "Hello",
            "translation": "这是简体",
            "model": "test",
            "attempt": 1,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }) + "\n",
        encoding="utf-8",
    )

    # With 1 quality_failed segment, pending count should be 0 (excluded).
    assert pending_segment_count(job_dir, cfg, 1) == 0


# ---------------------------------------------------------------------------
# --stop-on-no-progress-count stops the safe mode loop
# ---------------------------------------------------------------------------

def test_safe_mode_no_progress_stops(tmp_path):
    """Safe mode should stop after N consecutive batches with no new completions.

    With retry_failed=true (default), failed segments stay in the pending count
    and are re-attempted each batch. If the provider always fails, no new
    completions ever occur and --stop-on-no-progress-count should trigger.
    """
    from ebook_translator.providers.base import ProviderError

    books_dir, book_path = _make_books_dir(tmp_path)
    # max_retries=0 means no scheduler-level retries; the provider always raises.
    # retry_failed stays true (default) so failed segs are counted as pending.
    cfg = _write_config(tmp_path, book_path, max_retries=0)

    class _AlwaysFailProvider(MockTranslationProvider):
        async def translate(self, request):
            self.call_count += 1
            raise ProviderError("Persistent error — cannot fix")

    provider = _AlwaysFailProvider()

    with patch("ebook_translator.translator.OpenAICompatibleProvider", return_value=provider):
        result = runner.invoke(
            app,
            [
                "start",
                "--config", str(cfg),
                "--books-dir", str(books_dir),
                "--yes",
                "--batch-size", "5",
                "--stop-on-no-progress-count", "2",
            ],
            env={"FAKE_KEY": "test-key"},
        )

    assert result.exit_code == 0, result.output
    # Should stop after 2 batches with no new completions.
    output = result.output
    assert "Stopped:" in output, f"Expected 'Stopped:' in output:\n{output}"
    assert "consecutive" in output or "no new completion" in output.lower(), (
        f"Expected no-progress message in output:\n{output}"
    )


# ---------------------------------------------------------------------------
# repair-jsonl CLI
# ---------------------------------------------------------------------------

def test_repair_jsonl_bom_first_line_preserved(tmp_path):
    """A valid JSON first line with a UTF-8 BOM must not be deleted."""
    import json
    from ebook_translator.cli import app as cli_app

    job_dir = tmp_path / "test-job"
    job_dir.mkdir()

    valid_line = json.dumps({"segment_id": "0:0:aaa", "status": "completed", "translation": "譯文"})
    # Write with BOM.
    (job_dir / "translations.jsonl").write_bytes(
        b"\xef\xbb\xbf" + valid_line.encode("utf-8") + b"\n"
    )

    result = runner.invoke(cli_app, ["repair-jsonl", str(job_dir)])
    assert result.exit_code == 0, result.output

    content = (job_dir / "translations.jsonl").read_text(encoding="utf-8")
    kept = [l for l in content.splitlines() if l.strip()]
    assert len(kept) == 1, "BOM first line should be kept"
    assert json.loads(kept[0])["segment_id"] == "0:0:aaa"


def test_repair_jsonl_removes_corrupt_lines(tmp_path):
    """Corrupt JSONL lines must be removed; valid lines must be kept."""
    import json
    from ebook_translator.cli import app as cli_app

    job_dir = tmp_path / "test-job"
    job_dir.mkdir()

    valid1 = json.dumps({"segment_id": "0:0:aaa", "status": "completed"})
    corrupt = '{"unterminated'
    valid2 = json.dumps({"segment_id": "0:1:bbb", "status": "completed"})

    (job_dir / "translations.jsonl").write_text(
        "\n".join([valid1, corrupt, valid2]) + "\n",
        encoding="utf-8",
    )

    result = runner.invoke(cli_app, ["repair-jsonl", str(job_dir)])
    assert result.exit_code == 0, result.output
    assert "1 bad removed" in result.output

    content = (job_dir / "translations.jsonl").read_text(encoding="utf-8")
    kept = [l for l in content.splitlines() if l.strip()]
    assert len(kept) == 2
    assert json.loads(kept[0])["segment_id"] == "0:0:aaa"
    assert json.loads(kept[1])["segment_id"] == "0:1:bbb"


def test_repair_jsonl_creates_backup(tmp_path):
    """repair-jsonl must create a .bak backup before modifying the file."""
    import json
    from ebook_translator.cli import app as cli_app

    job_dir = tmp_path / "test-job"
    job_dir.mkdir()

    (job_dir / "translations.jsonl").write_text(
        json.dumps({"segment_id": "0:0:aaa"}) + "\n",
        encoding="utf-8",
    )

    runner.invoke(cli_app, ["repair-jsonl", str(job_dir)])

    assert (job_dir / "translations.jsonl.bak").exists()