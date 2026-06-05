from __future__ import annotations

from datetime import datetime, timezone

from ebook_translator.config import ContextConfig
from ebook_translator.models import Segment, TranslationRecord
from ebook_translator.prompt import build_system_prompt, build_user_message


def make_segment(html: str = "Hello world.") -> Segment:
    return Segment(
        segment_id="0000:0000:aabb",
        chapter_index=0,
        block_index=0,
        sha1_prefix="aabb",
        source_text=html,
        source_html=html,
        tag_name="p",
        chapter_href="chap01.xhtml",
    )


def make_record(translation: str) -> TranslationRecord:
    return TranslationRecord(
        segment_id="0000:0000:zz",
        source_hash="zz",
        status="completed",
        source="prev source",
        translation=translation,
        model="m",
        attempt=1,
        created_at=datetime.now(timezone.utc),
    )


def test_system_prompt_forbids_prefixes_and_simplified():
    sp = build_system_prompt()
    # Forbids bracketed/chapter prefixes
    assert "【" in sp
    assert "prefix" in sp.lower()
    # Forbids Simplified Chinese
    assert "Simplified" in sp
    # Forbids markdown / code fences
    assert "```" in sp or "Markdown" in sp
    # Forbids explanations
    assert "explanation" in sp.lower()
    # Requires Taiwan Traditional Chinese
    assert "Taiwan" in sp


def test_user_message_does_not_inject_translatable_chapter_prefix():
    seg = make_segment("It was a bright cold day.")
    ctx = ContextConfig(include_chapter_title=True, previous_segments=0)
    msg = build_user_message(seg, [], "The Beginning", ctx)

    # The old behaviour put "[Chapter: ...]" as a translatable-looking prefix.
    assert "[Chapter:" not in msg
    # Chapter title must be clearly marked as reference-only, not content to emit.
    assert "reference only" in msg.lower()
    assert "REFERENCE" in msg
    # The text to translate is present and clearly delimited.
    assert "TEXT TO TRANSLATE" in msg
    assert "It was a bright cold day." in msg


def test_user_message_marks_previous_context_as_reference():
    seg = make_segment("Second sentence.")
    ctx = ContextConfig(include_chapter_title=False, previous_segments=2)
    prev = [(make_segment("First sentence."), make_record("第一句。"))]
    msg = build_user_message(seg, prev, None, ctx)

    assert "do not repeat" in msg.lower() or "do not output" in msg.lower()
    assert "Second sentence." in msg


def test_user_message_without_context_is_just_target():
    seg = make_segment("Plain text.")
    ctx = ContextConfig(include_chapter_title=False, previous_segments=0)
    msg = build_user_message(seg, [], None, ctx)
    assert "REFERENCE" not in msg
    assert "Plain text." in msg
