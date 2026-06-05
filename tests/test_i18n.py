"""test_i18n.py — tests for i18n module and zh-TW/en CLI display mode."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from ebook_translator.config import CliConfig
from ebook_translator.i18n import get_cli_language, t
from tests.conftest import make_sample_config


class TestTranslationFunction:
    """Tests for the t() translation function."""

    def test_zh_tw_translation(self):
        """Test zh-TW translations are returned correctly."""
        assert t("book_name", "zh-TW") == "書籍名稱"
        assert t("input_path", "zh-TW") == "輸入路徑"
        assert t("segment_count", "zh-TW") == "段落數"
        assert t("translation_estimate_title", "zh-TW") == "翻譯估算（未呼叫 API）"
        assert t("missing_translation_report", "zh-TW") == "漏翻報告"
        assert t("missing_count", "zh-TW") == "漏翻數"

    def test_en_returns_english_text(self):
        """Test English mode returns English text."""
        assert t("book_name", "en") == "Book name"
        assert t("input_path", "en") == "Input path"
        assert t("segment_count", "en") == "Segment count"
        assert t("translation_estimate_title", "en") == "Translation Estimate (no API calls were made)"

    def test_unknown_key_returns_key(self):
        """Test unknown key returns the key itself."""
        assert t("unknown_key", "zh-TW") == "unknown_key"
        assert t("unknown_key", "en") == "unknown_key"

    def test_format_kwargs(self):
        """Test format kwargs are applied correctly."""
        result = t("report_saved_to", "zh-TW")
        assert result == "報告已儲存至"
        # Test with actual path
        result = t("report_saved_to", "zh-TW") + " /path/to/report.json"
        assert "/path/to/report.json" in result


class TestGetCliLanguage:
    """Tests for the get_cli_language() function."""

    def test_zh_tw_config(self):
        """Test extraction of zh-TW language from config."""
        config = CliConfig(language="zh-TW")
        assert get_cli_language(config) == "zh-TW"

    def test_en_config(self):
        """Test extraction of en language from config."""
        class FakeConfig:
            cli = CliConfig(language="en")
        assert get_cli_language(FakeConfig()) == "en"

    def test_default_language(self):
        """Test default language is zh-TW."""
        config = CliConfig()
        assert config.language == "zh-TW"

    def test_missing_cli_attribute(self):
        """Test fallback to zh-TW when config has no cli attribute."""
        class NoCliConfig:
            pass
        assert get_cli_language(NoCliConfig()) == "zh-TW"


class TestEstimateI18n:
    """Tests for estimate.py i18n integration."""

    def test_estimate_zh_tw_output(self, tmp_path, sample_epub_path, capsys):
        """Test estimate shows zh-TW output when configured."""
        from ebook_translator.estimate import run_estimate

        cfg = make_sample_config(tmp_path, sample_epub_path)
        cfg.cli.language = "zh-TW"

        run_estimate(cfg)

        captured = capsys.readouterr()
        assert "翻譯估算（未呼叫 API）" in captured.out
        assert "段落數" in captured.out
        assert "報告已儲存至" in captured.out

    def test_estimate_en_output(self, tmp_path, sample_epub_path, capsys):
        """Test estimate shows English output when configured."""
        from ebook_translator.estimate import run_estimate

        cfg = make_sample_config(tmp_path, sample_epub_path)
        cfg.cli.language = "en"

        run_estimate(cfg)

        captured = capsys.readouterr()
        assert "Translation Estimate" in captured.out
        assert "Segment count" in captured.out
        assert "Report saved to" in captured.out


class TestInspectI18n:
    """Tests for translator.py run_inspect i18n integration."""

    def test_inspect_zh_tw_output(self, tmp_path, sample_epub_path, capsys):
        """Test inspect shows zh-TW output when configured."""
        from ebook_translator.translator import run_inspect

        cfg = make_sample_config(tmp_path, sample_epub_path)
        cfg.cli.language = "zh-TW"

        run_inspect(cfg)

        captured = capsys.readouterr()
        assert "書籍名稱" in captured.out
        assert "總段落數" in captured.out

    def test_inspect_en_output(self, tmp_path, sample_epub_path, capsys):
        """Test inspect shows English output when configured."""
        from ebook_translator.translator import run_inspect

        cfg = make_sample_config(tmp_path, sample_epub_path)
        cfg.cli.language = "en"

        run_inspect(cfg)

        captured = capsys.readouterr()
        assert "Book name" in captured.out
        assert "Total segments" in captured.out


class TestReportMissingI18n:
    """Tests for translator.py run_report_missing i18n integration."""

    def test_report_missing_zh_tw_output(self, tmp_path, sample_epub_path, capsys):
        """Test report-missing shows zh-TW output when configured."""
        from ebook_translator.translator import run_report_missing

        cfg = make_sample_config(tmp_path, sample_epub_path)
        cfg.cli.language = "zh-TW"

        # Create minimal job directory with segments
        job_dir = tmp_path / "outputs" / "test-book"
        job_dir.mkdir(parents=True)

        # Create empty segments.jsonl
        (job_dir / "segments.jsonl").write_text("", encoding="utf-8")

        run_report_missing(job_dir, sample_epub_path, cfg)

        captured = capsys.readouterr()
        assert "漏翻報告" in captured.out
        assert "已檢查區塊數" in captured.out
        assert "漏翻數" in captured.out

    def test_report_missing_en_output(self, tmp_path, sample_epub_path, capsys):
        """Test report-missing shows English output when configured."""
        from ebook_translator.translator import run_report_missing

        cfg = make_sample_config(tmp_path, sample_epub_path)
        cfg.cli.language = "en"

        # Create minimal job directory with segments
        job_dir = tmp_path / "outputs" / "test-book"
        job_dir.mkdir(parents=True)

        # Create empty segments.jsonl
        (job_dir / "segments.jsonl").write_text("", encoding="utf-8")

        run_report_missing(job_dir, sample_epub_path, cfg)

        captured = capsys.readouterr()
        assert "Missing translation report" in captured.out
        assert "total_checked_blocks" in captured.out
        assert "missing_count" in captured.out


class TestJsonKeysNotTranslated:
    """Tests that JSON report keys are NOT translated."""

    def test_estimate_report_keys_english(self, tmp_path, sample_epub_path):
        """Test estimate report JSON keys remain in English."""
        from ebook_translator.estimate import run_estimate

        cfg = make_sample_config(tmp_path, sample_epub_path)
        cfg.cli.language = "zh-TW"

        report = run_estimate(cfg)

        # JSON keys should remain in English
        assert "book" in report
        assert "segments" in report
        assert "tokens" in report
        assert "requests" in report
        assert "runtime" in report
        assert "warnings" in report

        # Sub-keys should remain in English
        assert "title" in report["book"]
        assert "input_path" in report["book"]
        assert "count" in report["segments"]
        assert "source_chars" in report["segments"]


class TestValidateI18n:
    """Tests for translator.py run_validate i18n integration."""

    def _setup_job_dir(self, tmp_path, sample_epub_path):
        """Create a job directory with translated segments for validation."""
        from ebook_translator.epub.reader import read_epub
        from ebook_translator.segmenter.segmenter import segment_all_documents
        from ebook_translator.models import TranslationRecord
        from datetime import datetime, timezone

        _, spine_docs = read_epub(sample_epub_path)
        all_segs = segment_all_documents(spine_docs)

        job_dir = tmp_path / "outputs" / "test-book"
        job_dir.mkdir(parents=True)

        # Write segments
        with open(job_dir / "segments.jsonl", "w", encoding="utf-8") as f:
            for seg in all_segs:
                f.write(seg.model_dump_json() + "\n")

        # Write completed translations
        with open(job_dir / "translations.jsonl", "w", encoding="utf-8") as f:
            for seg in all_segs:
                rec = TranslationRecord(
                    segment_id=seg.segment_id,
                    source_hash=seg.sha1_prefix,
                    status="completed",
                    source=seg.source_text,
                    translation="這是已翻譯的內容。",
                    model="mock",
                    attempt=1,
                    created_at=datetime.now(timezone.utc),
                )
                f.write(rec.model_dump_json() + "\n")

        return job_dir

    def test_validate_zh_tw_output(self, tmp_path, sample_epub_path, capsys):
        """Test validate shows zh-TW output when configured."""
        from ebook_translator.translator import run_validate

        cfg = make_sample_config(tmp_path, sample_epub_path)
        cfg.cli.language = "zh-TW"

        job_dir = self._setup_job_dir(tmp_path, sample_epub_path)

        run_validate(job_dir, cfg)

        captured = capsys.readouterr()
        assert "驗證" in captured.out
        assert "已檢查" in captured.out
        assert "報告已儲存至" in captured.out

    def test_validate_en_output(self, tmp_path, sample_epub_path, capsys):
        """Test validate shows English output when configured."""
        from ebook_translator.translator import run_validate

        cfg = make_sample_config(tmp_path, sample_epub_path)
        cfg.cli.language = "en"

        job_dir = self._setup_job_dir(tmp_path, sample_epub_path)

        run_validate(job_dir, cfg)

        captured = capsys.readouterr()
        assert "Validation" in captured.out
        assert "checked" in captured.out
        assert "Report saved to" in captured.out

    def test_validate_json_key_not_translated(self, tmp_path, sample_epub_path):
        """Test validation_report.json keys remain in English."""
        import json
        from ebook_translator.translator import run_validate

        cfg = make_sample_config(tmp_path, sample_epub_path)
        cfg.cli.language = "zh-TW"

        job_dir = self._setup_job_dir(tmp_path, sample_epub_path)

        run_validate(job_dir, cfg)

        report_path = job_dir / "validation_report.json"
        assert report_path.exists()

        report_data = json.loads(report_path.read_text(encoding="utf-8"))
        # JSON keys should remain in English
        assert "total_checked" in report_data
        assert "issues" in report_data
        assert "passed" in report_data
        assert "warnings" in report_data
        assert "errors" in report_data
