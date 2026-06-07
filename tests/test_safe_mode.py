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