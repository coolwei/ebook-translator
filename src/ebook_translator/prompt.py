from __future__ import annotations

from .config import ContextConfig
from .models import Segment, TranslationRecord


SYSTEM_PROMPT = (
    "You are a professional ebook translator.\n"
    "Translate the provided text into Traditional Chinese used in Taiwan.\n"
    "Preserve meaning, tone, paragraph structure, terminology, names, numbers, "
    "URLs, code, and inline HTML tags.\n"
    "Do not summarize. Do not omit content. Do not add explanations.\n"
    "Return only the translated result."
)


def build_system_prompt() -> str:
    return SYSTEM_PROMPT


def build_user_message(
    segment: Segment,
    previous_translations: list[tuple[Segment, TranslationRecord]],
    chapter_title: str | None,
    context_config: ContextConfig,
) -> str:
    parts: list[str] = []

    if context_config.include_chapter_title and chapter_title:
        parts.append(f"[Chapter: {chapter_title}]")

    if context_config.previous_segments > 0 and previous_translations:
        window = previous_translations[-context_config.previous_segments :]
        context_lines: list[str] = []
        for prev_seg, prev_rec in window:
            context_lines.append(f"Source: {prev_seg.source_html}")
            context_lines.append(f"Translation: {prev_rec.translation}")
        if context_lines:
            parts.append("[Previous context]\n" + "\n".join(context_lines))

    parts.append(segment.source_html)
    return "\n\n".join(parts)
