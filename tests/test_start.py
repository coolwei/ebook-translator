from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from typer.testing import CliRunner

from ebook_translator.cli import app
from ebook_translator.epub.reader import read_epub
from ebook_translator.providers.base import ProviderError, TranslationResponse
from ebook_translator.segmenter.segmenter import segment_all_documents
from ebook_translator.validator import SIMPLIFIED_CHARS
from tests.conftest import MockTranslationProvider, make_sample_epub


runner = CliRunner()


def _write_config(
    tmp_path: Path,
    book_path: Path | None = None,
    *,
    max_retries: int = 2,
    quality_strict_mode: bool = False,
) -> Path:
    """Write a minimal config.yaml.  ``input.path`` is intentionally omitted —
    the ``start`` command discovers books from ``--books-dir`` / ``--book``,
    and injects the path per book at runtime."""
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        "\n".join(
            [
                "project:",
                f"  output_dir: {(tmp_path / 'outputs').as_posix()}",
                "provider:",
                "  base_url: https://api.example.com/v1",
                "  api_key_env: FAKE_KEY",
                "  model: test-model",
                "limits:",
                "  rpm: 60",
                "  concurrency: 1",
                "resume:",
                f"  max_retries: {max_retries}",
                "cli:",
                "  language: en",
                "quality:",
                f"  strict_mode: {'true' if quality_strict_mode else 'false'}",
            ]
        ),
        encoding="utf-8",
    )
    return cfg


def _make_input_dir(tmp_path: Path) -> tuple[Path, Path]:
    """Create an input/ directory (the new default) containing one sample EPUB."""
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    book_path = make_sample_epub(input_dir)
    return input_dir, book_path


# Keep the old name as an alias so test helpers that still spell out
# --books-dir continue to work without any other changes.
def _make_books_dir(tmp_path: Path) -> tuple[Path, Path]:
    return _make_input_dir(tmp_path)


def _book_dir(tmp_path: Path) -> Path:
    return tmp_path / "outputs" / "test-book"


def _records(job_dir: Path) -> list[dict]:
    path = job_dir / "translations.jsonl"
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def test_start_dry_run_does_not_call_provider(tmp_path):
    books_dir, book_path = _make_books_dir(tmp_path)
    cfg = _write_config(tmp_path, book_path)

    with patch(
        "ebook_translator.translator.OpenAICompatibleProvider",
        side_effect=AssertionError("provider should not be instantiated"),
    ):
        result = runner.invoke(
            app,
            ["start", "--config", str(cfg), "--books-dir", str(books_dir), "--dry-run"],
        )

    assert result.exit_code == 0, result.output
    assert "Dry run complete" in result.output
    assert not (_book_dir(tmp_path) / "translations.jsonl").exists()


def test_start_no_epub_shows_clear_message(tmp_path):
    books_dir = tmp_path / "books"
    books_dir.mkdir()
    cfg = _write_config(tmp_path)

    result = runner.invoke(
        app,
        ["start", "--config", str(cfg), "--books-dir", str(books_dir), "--dry-run"],
    )

    assert result.exit_code == 1
    assert "No EPUB files found" in result.output


def test_start_stops_when_segment_count_exceeds_max_segments(tmp_path):
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
                "--yes",
                "--max-segments",
                "1",
            ],
            env={"FAKE_KEY": "test-key"},
        )

    assert result.exit_code == 0, result.output
    assert "exceeds --max-segments" in result.output
    assert not (_book_dir(tmp_path) / "translations.jsonl").exists()


def test_start_limit_translates_only_n_segments_and_writes_outputs(tmp_path):
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
                "--limit",
                "2",
            ],
            env={"FAKE_KEY": "test-key"},
        )

    assert result.exit_code == 0, result.output
    assert provider.call_count == 2
    job = _book_dir(tmp_path)
    assert len(_records(job)) == 2
    assert (job / "validation_report.json").exists()
    assert (job / "missing_translation_report.json").exists()
    assert (job / "translated.epub").exists()


class _FailThenRecoverProvider(MockTranslationProvider):
    def __init__(self, fail_id: str) -> None:
        super().__init__()
        self.fail_id = fail_id
        self.failed_once = False
        self.seen_ids: list[str] = []

    async def translate(self, request):
        self.call_count += 1
        sid = request.segment.segment_id
        self.seen_ids.append(sid)
        if sid == self.fail_id and not self.failed_once:
            self.failed_once = True
            raise ProviderError("empty message content")
        return TranslationResponse(
            translated_text=f"譯文內容{request.segment.sha1_prefix}",
            model=request.model,
            finish_reason="stop",
            prompt_tokens=1,
            completion_tokens=1,
        )


def test_start_failed_triggers_retry_failed(tmp_path):
    books_dir, book_path = _make_books_dir(tmp_path)
    _, spine_docs = read_epub(book_path)
    target = segment_all_documents(spine_docs)[0].segment_id
    cfg = _write_config(tmp_path, book_path, max_retries=1)
    provider = _FailThenRecoverProvider(target)

    with patch("ebook_translator.translator.OpenAICompatibleProvider", return_value=provider):
        result = runner.invoke(
            app,
            ["start", "--config", str(cfg), "--books-dir", str(books_dir), "--yes"],
            env={"FAKE_KEY": "test-key"},
        )

    assert result.exit_code == 0, result.output
    assert "Retrying failed segments: 1" in result.output
    assert provider.seen_ids.count(target) == 2
    latest = {record["segment_id"]: record for record in _records(_book_dir(tmp_path))}
    assert latest[target]["status"] == "completed"


class _QualityThenRecoverProvider(MockTranslationProvider):
    def __init__(self, bad_id: str) -> None:
        super().__init__()
        self.bad_id = bad_id
        self.seen_ids: list[str] = []
        self._bad_char = next(iter(SIMPLIFIED_CHARS))

    async def translate(self, request):
        self.call_count += 1
        sid = request.segment.segment_id
        self.seen_ids.append(sid)
        if sid == self.bad_id and self.seen_ids.count(sid) == 1:
            text = f"{self._bad_char}品質失敗"
        else:
            text = f"譯文內容{request.segment.sha1_prefix}"
        return TranslationResponse(
            translated_text=text,
            model=request.model,
            finish_reason="stop",
            prompt_tokens=1,
            completion_tokens=1,
        )


def test_start_quality_failed_triggers_retry_quality_failed(tmp_path):
    books_dir, book_path = _make_books_dir(tmp_path)
    _, spine_docs = read_epub(book_path)
    target = segment_all_documents(spine_docs)[0].segment_id
    cfg = _write_config(tmp_path, book_path, quality_strict_mode=True)
    provider = _QualityThenRecoverProvider(target)

    with patch("ebook_translator.translator.OpenAICompatibleProvider", return_value=provider):
        result = runner.invoke(
            app,
            ["start", "--config", str(cfg), "--books-dir", str(books_dir), "--yes"],
            env={"FAKE_KEY": "test-key"},
        )

    assert result.exit_code == 0, result.output
    assert "Retrying quality-failed segments: 1" in result.output
    assert provider.seen_ids.count(target) == 2
    latest = {record["segment_id"]: record for record in _records(_book_dir(tmp_path))}
    assert latest[target]["status"] == "completed"


# ---------------------------------------------------------------------------
# input/ folder feature tests
# ---------------------------------------------------------------------------


def test_start_creates_input_dir_when_missing(tmp_path):
    """When input/ doesn't exist and no --book is given, create it and exit 0."""
    cfg = _write_config(tmp_path)
    input_dir = tmp_path / "input"
    assert not input_dir.exists()

    result = runner.invoke(
        app,
        ["start", "--config", str(cfg), "--books-dir", str(input_dir)],
    )

    assert result.exit_code == 0, result.output
    assert input_dir.exists(), "input/ dir must be created"
    assert "Drop your .epub files there" in result.output or "放入" in result.output


def test_start_default_books_dir_is_input(tmp_path, monkeypatch):
    """Without --books-dir, the start command looks in input/ by default."""
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    make_sample_epub(input_dir)
    cfg = _write_config(tmp_path)
    provider = MockTranslationProvider()

    # Change CWD so that the default Path("input") resolves to our tmp_path/input.
    monkeypatch.chdir(tmp_path)

    with patch("ebook_translator.translator.OpenAICompatibleProvider", return_value=provider):
        result = runner.invoke(
            app,
            ["start", "--config", str(cfg), "--yes", "--limit", "1"],
            env={"FAKE_KEY": "test-key"},
        )

    assert result.exit_code == 0, result.output
    assert provider.call_count >= 1


def test_start_config_without_input_section_is_valid(tmp_path):
    """A config.yaml with no 'input:' section must load without error (dry-run)."""
    input_dir, _ = _make_input_dir(tmp_path)
    cfg = _write_config(tmp_path)  # no input.path in YAML
    assert "input:" not in cfg.read_text(encoding="utf-8")

    with patch(
        "ebook_translator.translator.OpenAICompatibleProvider",
        side_effect=AssertionError("provider should not be instantiated"),
    ):
        result = runner.invoke(
            app,
            ["start", "--config", str(cfg), "--books-dir", str(input_dir), "--dry-run"],
        )

    assert result.exit_code == 0, result.output
    assert "Dry run complete" in result.output


def test_start_no_epub_in_input_shows_helpful_message(tmp_path):
    """Empty input/ shows a clear error with instructions."""
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    cfg = _write_config(tmp_path)

    result = runner.invoke(
        app,
        ["start", "--config", str(cfg), "--books-dir", str(input_dir), "--dry-run"],
    )

    assert result.exit_code == 1
    assert "No EPUB files found" in result.output
