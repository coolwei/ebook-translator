from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, model_validator


class ProjectConfig(BaseModel):
    name: str = "ebook-translator"
    output_dir: Path = Path("outputs")
    jobs_dir: Path = Path("jobs")


class InputConfig(BaseModel):
    path: Path


class TranslationConfig(BaseModel):
    source_language: str = "auto"
    target_language: str = "zh-TW"
    bilingual_mode: str = "original_above_translation"
    preserve_html_tags: bool = True
    preserve_footnotes: bool = True
    skip_existing: bool = True


class ProviderConfig(BaseModel):
    type: str = "openai_compatible"
    base_url: str
    api_key_env: str
    model: str
    timeout_seconds: int = 120
    api_key: str = Field(default="", exclude=True)

    @model_validator(mode="after")
    def resolve_api_key(self) -> "ProviderConfig":
        if not self.api_key:
            key = os.environ.get(self.api_key_env, "")
            object.__setattr__(self, "api_key", key)
        return self


class LimitsConfig(BaseModel):
    rpm: int = 30
    concurrency: int = 2
    max_context_tokens: int = 32000
    max_output_tokens: int = 4000
    max_chars_per_chunk: int = 6000


class ResumeConfig(BaseModel):
    enabled: bool = True
    checkpoint_every_segments: int = 1
    retry_failed: bool = True
    max_retries: int = 3


class LoggingConfig(BaseModel):
    level: str = "info"
    save_prompt: bool = False
    save_response: bool = True


class QualityConfig(BaseModel):
    validate_empty_translation: bool = True
    validate_untranslated_ratio: bool = True
    validate_html_integrity: bool = True
    max_length_ratio: float = 3.0
    # Translation-quality guards (Phase 2.5)
    validate_simplified_chinese: bool = True
    validate_added_prefix: bool = True
    validate_markdown_fence: bool = True
    validate_explanation_prefix: bool = True
    # Untranslated-text detection (Phase 6)
    validate_untranslated_text: bool = True
    untranslated_ascii_threshold: float = 0.75


class ContextConfig(BaseModel):
    include_chapter_title: bool = True
    previous_segments: int = 2
    include_glossary: bool = False
    include_style_guide: bool = False


class AppConfig(BaseModel):
    project: ProjectConfig = ProjectConfig()
    input: InputConfig
    translation: TranslationConfig = TranslationConfig()
    provider: ProviderConfig
    limits: LimitsConfig = LimitsConfig()
    resume: ResumeConfig = ResumeConfig()
    logging: LoggingConfig = LoggingConfig()
    quality: QualityConfig = QualityConfig()
    context: ContextConfig = ContextConfig()


def load_config(path: Path) -> AppConfig:
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    config = AppConfig.model_validate(data)
    if not config.provider.api_key:
        raise ValueError(
            f"Environment variable '{config.provider.api_key_env}' is not set. "
            "Please set it to your API key."
        )
    return config
