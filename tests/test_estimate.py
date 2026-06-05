from __future__ import annotations

import json
import math
from pathlib import Path
from unittest.mock import patch

import pytest

from ebook_translator.estimate import (
    OUTPUT_TOKEN_RATIO,
    RETRY_OVERHEAD_RATIO,
    build_estimate_report,
    estimate_input_tokens,
    estimate_output_tokens,
    estimate_tokens,
    requests_with_retry,
    run_estimate,
    runtime_minutes,
)
from ebook_translator.epub.reader import read_epub
from ebook_translator.segmenter.segmenter import segment_all_documents
from tests.conftest import make_sample_config


# ---------------------------------------------------------------------------
# Pure helper functions
# ---------------------------------------------------------------------------

def test_estimate_tokens_mixed():
    assert estimate_tokens("a" * 30, "mixed") == 10  # 30 / 3


def test_estimate_tokens_english_and_cjk():
    assert estimate_tokens("a" * 40, "english") == 10  # 40 / 4
    assert estimate_tokens("字" * 30, "cjk") == 20      # 30 / 1.5


def test_estimate_input_tokens_div_3():
    assert estimate_input_tokens(30, "mixed") == 10
    assert estimate_input_tokens(100, "mixed") == 33  # int(100/3)


def test_estimate_output_tokens_ratio():
    assert estimate_output_tokens(10) == round(10 * OUTPUT_TOKEN_RATIO)  # 8
    assert estimate_output_tokens(100) == 80


def test_requests_with_retry_ceil():
    assert requests_with_retry(100) == 110
    assert requests_with_retry(5) == math.ceil(5 * (1 + RETRY_OVERHEAD_RATIO))  # ceil(5.5)=6
    assert requests_with_retry(0) == 0


def test_runtime_minutes_by_rpm():
    assert runtime_minutes(100, 10) == 10.0
    assert runtime_minutes(110, 10) == 11.0
    assert runtime_minutes(5, 0) == 0.0  # guard against div-by-zero


# ---------------------------------------------------------------------------
# Report building against the sample EPUB
# ---------------------------------------------------------------------------

def test_segment_count_and_source_chars_correct(tmp_path, sample_epub_path):
    cfg = make_sample_config(tmp_path, sample_epub_path)
    _, spine = read_epub(cfg.input.path)
    segs = segment_all_documents(spine)
    expected_count = len(segs)
    expected_chars = sum(len(s.source_text) for s in segs)

    report = build_estimate_report(cfg)
    assert report["segments"]["count"] == expected_count
    assert report["segments"]["source_chars"] == expected_chars


def test_token_estimate_matches_formula(tmp_path, sample_epub_path):
    cfg = make_sample_config(tmp_path, sample_epub_path)
    report = build_estimate_report(cfg)
    chars = report["segments"]["source_chars"]
    assert report["tokens"]["estimated_input_tokens"] == int(chars / 3)
    inp = report["tokens"]["estimated_input_tokens"]
    assert report["tokens"]["estimated_output_tokens"] == round(inp * OUTPUT_TOKEN_RATIO)
    assert report["tokens"]["estimated_total_tokens"] == inp + report["tokens"]["estimated_output_tokens"]
    assert report["tokens"]["estimation_method"] == "rough_mixed_chars_div_3"


def test_requests_and_runtime_correct(tmp_path, sample_epub_path):
    cfg = make_sample_config(tmp_path, sample_epub_path)  # rpm=60 in sample config
    report = build_estimate_report(cfg)
    reqs = report["requests"]["estimated_requests"]
    assert reqs == report["segments"]["count"]
    assert report["requests"]["estimated_requests_with_retry"] == math.ceil(reqs * 1.1)
    assert report["requests"]["retry_overhead_ratio"] == 0.1
    rpm = report["runtime"]["configured_rpm"]
    assert report["runtime"]["minimum_minutes"] == round(reqs / rpm, 2)
    assert report["runtime"]["minimum_minutes_with_retry"] == round(
        report["requests"]["estimated_requests_with_retry"] / rpm, 2
    )


def test_max_chars_warning_triggers(tmp_path, sample_epub_path):
    cfg = make_sample_config(tmp_path, sample_epub_path)
    cfg.limits.max_chars_per_chunk = 5  # tiny so every segment exceeds it
    report = build_estimate_report(cfg)
    assert report["segments"]["exceeding_max_chars_count"] > 0
    codes = {w["code"] for w in report["warnings"]}
    assert "split_required" in codes
    split = next(w for w in report["warnings"] if w["code"] == "split_required")
    assert len(split["segment_ids"]) <= 10


def test_no_warning_when_within_limits(tmp_path, sample_epub_path):
    cfg = make_sample_config(tmp_path, sample_epub_path)
    cfg.limits.max_chars_per_chunk = 100000  # nothing exceeds
    report = build_estimate_report(cfg)
    assert report["segments"]["exceeding_max_chars_count"] == 0
    assert all(w["code"] != "split_required" for w in report["warnings"])


# ---------------------------------------------------------------------------
# run_estimate: writes report, prints summary, makes NO provider/API calls
# ---------------------------------------------------------------------------

def test_run_estimate_writes_readable_report(tmp_path, sample_epub_path, capsys):
    cfg = make_sample_config(tmp_path, sample_epub_path)
    cfg.cli.language = "en"  # Use English for this test
    report = run_estimate(cfg)

    book_dir = next(cfg.project.output_dir.iterdir())
    report_path = book_dir / "estimate_report.json"
    assert report_path.exists()
    loaded = json.loads(report_path.read_text(encoding="utf-8"))
    assert loaded == report
    # console summary mentions key fields
    out = capsys.readouterr().out
    assert "Segment count" in out
    assert "Estimated input tokens" in out
    assert "Translation Estimate" in out


def test_estimate_does_not_call_provider(tmp_path, sample_epub_path):
    cfg = make_sample_config(tmp_path, sample_epub_path)
    with patch("ebook_translator.providers.openai_compatible.OpenAICompatibleProvider") as prov:
        run_estimate(cfg)
    prov.assert_not_called()


def test_estimate_makes_no_http_call(tmp_path, sample_epub_path):
    cfg = make_sample_config(tmp_path, sample_epub_path)
    # Any attempt to construct an httpx client would raise here.
    with patch("httpx.AsyncClient", side_effect=AssertionError("no HTTP allowed")):
        run_estimate(cfg)


def test_cli_estimate_runs(tmp_path, sample_epub_path):
    from typer.testing import CliRunner
    from ebook_translator.cli import app

    # Write a config.yaml pointing at the sample EPUB; no API key needed.
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(
        f"""
input:
  path: {sample_epub_path.as_posix()}
provider:
  base_url: "https://example.com/v1"
  api_key_env: "NOT_NEEDED_FOR_ESTIMATE"
  model: "test-model"
project:
  output_dir: {(tmp_path / 'outputs').as_posix()}
limits:
  rpm: 30
  concurrency: 2
cli:
  language: en
""",
        encoding="utf-8",
    )
    runner = CliRunner()
    result = runner.invoke(app, ["estimate", "--config", str(cfg_path)])
    assert result.exit_code == 0, result.output
    assert "Translation Estimate" in result.output
    assert (tmp_path / "outputs").exists()
