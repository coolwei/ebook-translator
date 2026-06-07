from __future__ import annotations

import os
from pathlib import Path

import pytest
import yaml

from ebook_translator.config import load_config


VALID_CONFIG = {
    "input": {"path": "books/example.epub"},
    "provider": {
        "base_url": "https://api.example.com/v1",
        "api_key_env": "TEST_API_KEY",
        "model": "test-model",
    },
}


def write_config(tmp_path: Path, data: dict) -> Path:
    p = tmp_path / "config.yaml"
    p.write_text(yaml.dump(data), encoding="utf-8")
    return p


def test_load_valid_config(tmp_path, monkeypatch):
    monkeypatch.setenv("TEST_API_KEY", "sk-test")
    cfg = load_config(write_config(tmp_path, VALID_CONFIG))
    assert cfg.provider.model == "test-model"
    assert cfg.provider.api_key == "sk-test"
    assert cfg.translation.target_language == "zh-TW"


def test_missing_api_key_raises(tmp_path, monkeypatch):
    monkeypatch.delenv("TEST_API_KEY", raising=False)
    with pytest.raises(ValueError, match="TEST_API_KEY"):
        load_config(write_config(tmp_path, VALID_CONFIG))


def test_missing_required_field_raises(tmp_path, monkeypatch):
    monkeypatch.setenv("TEST_API_KEY", "sk-test")
    bad = {"input": {"path": "books/example.epub"}}  # missing provider
    with pytest.raises(Exception):
        load_config(write_config(tmp_path, bad))


def test_defaults_applied(tmp_path, monkeypatch):
    monkeypatch.setenv("TEST_API_KEY", "sk-test")
    cfg = load_config(write_config(tmp_path, VALID_CONFIG))
    assert cfg.limits.rpm == 30
    assert cfg.limits.concurrency == 2
    assert cfg.resume.max_retries == 3
    assert cfg.quality.max_length_ratio == 3.0
    assert cfg.quality.strict_mode is False
    assert cfg.translation.mode == "segment"
    assert cfg.translation.segments_per_request == 1


def test_provider_url_stored(tmp_path, monkeypatch):
    monkeypatch.setenv("TEST_API_KEY", "sk-test")
    cfg = load_config(write_config(tmp_path, VALID_CONFIG))
    assert cfg.provider.base_url == "https://api.example.com/v1"


def test_fallback_models_default_empty(tmp_path, monkeypatch):
    monkeypatch.setenv("TEST_API_KEY", "sk-test")
    cfg = load_config(write_config(tmp_path, VALID_CONFIG))
    assert cfg.provider.fallback_models == []


def test_logging_defaults(tmp_path, monkeypatch):
    monkeypatch.setenv("TEST_API_KEY", "sk-test")
    cfg = load_config(write_config(tmp_path, VALID_CONFIG))
    assert cfg.logging.enabled is True
    assert str(cfg.logging.file).replace("\\", "/") == "logs/translation.log"
    assert cfg.logging.per_book is True
