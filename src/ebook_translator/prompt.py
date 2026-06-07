from __future__ import annotations

import json

from .config import ContextConfig
from .models import Segment, TranslationRecord


SYSTEM_PROMPT = (
    "You are a professional ebook translator.\n"
    "Translate the user's text into Traditional Chinese as used in Taiwan (zh-TW).\n"
    "\n"
    "Strict output rules — follow every one:\n"
    "1. Output ONLY the translation itself. No preface, notes, commentary, or "
    "explanations of any kind (for example, never start with 「翻譯如下」, "
    "「譯文：」, 「以下是」, or \"Translation:\").\n"
    "2. Do NOT add chapter titles, headings, numbering, or any bracketed prefix "
    "such as 【…】, ［…］, [...], or （…）. Translate the text as-is.\n"
    "3. Do NOT wrap the output in Markdown, code fences (```), block quotes, or "
    "surrounding quotation marks.\n"
    "4. Use Traditional Chinese characters ONLY. Never output Simplified Chinese "
    "characters.\n"
    "5. Use Taiwan vocabulary, idiom, and phrasing.\n"
    "6. Preserve meaning, tone, paragraph structure, names, numbers, URLs, code, "
    "and inline HTML tags exactly as they appear.\n"
    "7. Do not summarize, omit, or add content.\n"
    "8. Any reference material is for consistency only — never translate, repeat, "
    "or echo it in your output."
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
    reference: list[str] = []

    if context_config.include_chapter_title and chapter_title:
        reference.append(
            f"Chapter title (reference only — do not translate or include): {chapter_title}"
        )

    if context_config.previous_segments > 0 and previous_translations:
        window = previous_translations[-context_config.previous_segments :]
        context_lines: list[str] = []
        for prev_seg, prev_rec in window:
            context_lines.append(f"- {prev_seg.source_html} => {prev_rec.translation}")
        if context_lines:
            reference.append(
                "Previously translated segments (reference only — do not repeat):\n"
                + "\n".join(context_lines)
            )

    if reference:
        parts.append("[REFERENCE — do not output any of this]\n" + "\n\n".join(reference))

    parts.append(
        "[TEXT TO TRANSLATE — output only its Traditional Chinese translation, "
        "nothing else]\n" + segment.source_html
    )
    return "\n\n".join(parts)


BATCH_SYSTEM_PROMPT = (
    SYSTEM_PROMPT
    + "\n\n"
    + "Batch mode rules:\n"
    + "- You will receive a JSON array of segments to translate.\n"
    + "- Return ONLY a JSON array. Each item must include segment_id and translation.\n"
    + "- Do not omit, merge, split, or change any segment_id.\n"
    + "- Do not wrap the JSON in Markdown code fences.\n"
    + "- Do not add explanations."
)


def build_batch_system_prompt() -> str:
    return BATCH_SYSTEM_PROMPT


def build_batch_user_message(segments: list[Segment]) -> str:
    payload = [
        {"segment_id": seg.segment_id, "source": seg.source_html}
        for seg in segments
    ]
    return (
        "請翻譯以下多個段落。\n"
        "必須回傳 JSON array。\n"
        "每個 item 必須包含 segment_id 與 translation。\n"
        "不得省略、合併、拆分、改 ID。\n"
        "不得輸出 markdown code fence。\n"
        "不得輸出解釋。\n\n"
        + json.dumps(payload, ensure_ascii=False, indent=2)
    )
