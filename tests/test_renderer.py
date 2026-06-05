from __future__ import annotations

from datetime import datetime, timezone

from bs4 import BeautifulSoup

from ebook_translator.epub.reader import SpineDocument
from ebook_translator.models import Segment, TranslationRecord
from ebook_translator.renderer import render_bilingual_documents, BILINGUAL_CSS


def make_doc(html: str, href: str = "chap01.xhtml", chapter_index: int = 0) -> SpineDocument:
    return SpineDocument(
        chapter_index=chapter_index,
        item_id="item1",
        href=href,
        content=html.encode("utf-8"),
        media_type="application/xhtml+xml",
    )


def make_segment(seg_id: str, text: str, href: str = "chap01.xhtml") -> Segment:
    return Segment(
        segment_id=seg_id,
        chapter_index=0,
        block_index=0,
        sha1_prefix=seg_id.split(":")[-1],
        source_text=text,
        source_html=text,
        tag_name="p",
        chapter_href=href,
    )


def make_record(seg_id: str, translation: str, status: str = "completed") -> TranslationRecord:
    return TranslationRecord(
        segment_id=seg_id,
        source_hash="aabb",
        status=status,
        source="Original",
        translation=translation,
        model="test-model",
        attempt=1,
        created_at=datetime.now(timezone.utc),
    )


SAMPLE_HTML = (
    '<html><head></head><body>'
    '<p data-segment-id="0000:0000:aabb">Original paragraph.</p>'
    '</body></html>'
)


def test_bilingual_block_created():
    doc = make_doc(SAMPLE_HTML)
    seg = make_segment("0000:0000:aabb", "Original paragraph.")
    record = make_record("0000:0000:aabb", "原始段落。")
    translations = {"0000:0000:aabb": record}

    rendered = render_bilingual_documents([doc], [seg], translations)
    soup = BeautifulSoup(rendered["chap01.xhtml"], "lxml")

    block = soup.find("div", class_="bilingual-block")
    assert block is not None


def test_src_and_trg_present():
    doc = make_doc(SAMPLE_HTML)
    seg = make_segment("0000:0000:aabb", "Original paragraph.")
    record = make_record("0000:0000:aabb", "原始段落。")
    translations = {"0000:0000:aabb": record}

    rendered = render_bilingual_documents([doc], [seg], translations)
    soup = BeautifulSoup(rendered["chap01.xhtml"], "lxml")

    src = soup.find(class_="src")
    trg = soup.find(class_="trg")
    assert src is not None
    assert trg is not None
    assert "原始段落" in trg.get_text()


def test_source_text_in_src():
    doc = make_doc(SAMPLE_HTML)
    seg = make_segment("0000:0000:aabb", "Original paragraph.")
    record = make_record("0000:0000:aabb", "翻譯文字。")
    translations = {"0000:0000:aabb": record}

    rendered = render_bilingual_documents([doc], [seg], translations)
    soup = BeautifulSoup(rendered["chap01.xhtml"], "lxml")

    src = soup.find(class_="src")
    assert "Original paragraph" in src.get_text()


def test_missing_translation_leaves_source_only():
    doc = make_doc(SAMPLE_HTML)
    seg = make_segment("0000:0000:aabb", "Original paragraph.")
    translations: dict = {}  # no translation

    rendered = render_bilingual_documents([doc], [seg], translations)
    soup = BeautifulSoup(rendered["chap01.xhtml"], "lxml")

    # No bilingual block — source element should remain
    block = soup.find("div", class_="bilingual-block")
    assert block is None
    p = soup.find("p")
    assert p is not None


def test_failed_translation_leaves_source_only():
    doc = make_doc(SAMPLE_HTML)
    seg = make_segment("0000:0000:aabb", "Original paragraph.")
    record = make_record("0000:0000:aabb", "", status="failed")
    translations = {"0000:0000:aabb": record}

    rendered = render_bilingual_documents([doc], [seg], translations)
    soup = BeautifulSoup(rendered["chap01.xhtml"], "lxml")

    block = soup.find("div", class_="bilingual-block")
    assert block is None


def test_css_injected_into_head():
    doc = make_doc(SAMPLE_HTML)
    seg = make_segment("0000:0000:aabb", "Original paragraph.")
    record = make_record("0000:0000:aabb", "譯文。")
    translations = {"0000:0000:aabb": record}

    rendered = render_bilingual_documents([doc], [seg], translations)
    soup = BeautifulSoup(rendered["chap01.xhtml"], "lxml")

    style = soup.find("style")
    assert style is not None
    assert "bilingual-block" in style.get_text()


# ---------------------------------------------------------------------------
# Regression: renderer must work on raw content (no pre-injected data-segment-id),
# resolving blocks via the segmenter's own deterministic walk.
# ---------------------------------------------------------------------------

RAW_HTML = (
    '<html><head></head><body>'
    "<h1>Heading</h1>"
    "<p>First paragraph with <em>emphasis</em>.</p>"
    "<p>Second paragraph.</p>"
    "</body></html>"
)


def test_render_from_raw_content_matches_segmenter():
    from ebook_translator.segmenter.segmenter import segment_document

    doc = make_doc(RAW_HTML)
    segments = segment_document(doc)  # no data-segment-id in doc.content
    assert len(segments) == 3

    translations = {
        s.segment_id: TranslationRecord(
            segment_id=s.segment_id,
            source_hash=s.sha1_prefix,
            status="completed",
            source=s.source_html,
            translation=f"譯：{s.source_text}",
            model="mock",
            attempt=1,
            created_at=datetime.now(timezone.utc),
        )
        for s in segments
    }

    rendered = render_bilingual_documents([doc], segments, translations)
    soup = BeautifulSoup(rendered["chap01.xhtml"], "lxml")

    blocks = soup.find_all("div", class_="bilingual-block")
    assert len(blocks) == 3

    # Inline tag preserved in source side
    first = blocks[1]  # the paragraph with <em>
    assert first.find("em") is not None
    # src precedes trg
    inner = first.find_all(class_=["src", "trg"])
    assert inner[0].get("class") == ["src"]
    assert inner[1].get("class") == ["trg"]


def test_render_partial_leaves_untranslated_source_only():
    from ebook_translator.segmenter.segmenter import segment_document

    doc = make_doc(RAW_HTML)
    segments = segment_document(doc)
    # Only translate the first segment
    first = segments[0]
    translations = {
        first.segment_id: TranslationRecord(
            segment_id=first.segment_id,
            source_hash=first.sha1_prefix,
            status="completed",
            source=first.source_html,
            translation="譯：標題",
            model="mock",
            attempt=1,
            created_at=datetime.now(timezone.utc),
        )
    }

    rendered = render_bilingual_documents([doc], segments, translations)
    soup = BeautifulSoup(rendered["chap01.xhtml"], "lxml")

    assert len(soup.find_all("div", class_="bilingual-block")) == 1
    # Untranslated paragraphs remain present (not dropped/corrupted)
    assert "Second paragraph." in soup.get_text()
