from __future__ import annotations

from datetime import datetime, timezone

from bs4 import BeautifulSoup, Tag

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


# ---------------------------------------------------------------------------
# OutputConfig / note mode tests
# ---------------------------------------------------------------------------

def _make_output_config(style="simple", **kwargs):
    from ebook_translator.config import OutputConfig
    return OutputConfig(bilingual_style=style, **kwargs)


def _make_seg_and_record(seg_id="0000:0000:aabb", source="Original.", translation="譯文。"):
    seg = make_segment(seg_id, source)
    record = make_record(seg_id, translation)
    return seg, record, {seg_id: record}


# --- note mode structure ---

def test_note_mode_produces_note_block_class():
    doc = make_doc(SAMPLE_HTML)
    seg, _, translations = _make_seg_and_record()
    cfg = _make_output_config("note")
    rendered = render_bilingual_documents([doc], [seg], translations, output_config=cfg)
    soup = BeautifulSoup(rendered["chap01.xhtml"], "lxml")
    block = soup.find("div", class_="note-block")
    assert block is not None, "note mode must add 'note-block' class"


def test_note_mode_has_source_block_and_translation_block():
    doc = make_doc(SAMPLE_HTML)
    seg, _, translations = _make_seg_and_record()
    cfg = _make_output_config("note")
    rendered = render_bilingual_documents([doc], [seg], translations, output_config=cfg)
    soup = BeautifulSoup(rendered["chap01.xhtml"], "lxml")
    assert soup.find(class_="source-block") is not None
    assert soup.find(class_="translation-block") is not None


def test_note_mode_labels_when_add_translation_label_true():
    doc = make_doc(SAMPLE_HTML)
    seg, _, translations = _make_seg_and_record()
    cfg = _make_output_config("note", add_translation_label=True)
    rendered = render_bilingual_documents([doc], [seg], translations, output_config=cfg)
    soup = BeautifulSoup(rendered["chap01.xhtml"], "lxml")
    labels = soup.find_all(class_="block-label")
    assert len(labels) >= 1
    texts = [lbl.get_text(strip=True) for lbl in labels]
    assert "譯文" in texts


def test_note_mode_no_labels_when_add_translation_label_false():
    doc = make_doc(SAMPLE_HTML)
    seg, _, translations = _make_seg_and_record()
    cfg = _make_output_config("note", add_translation_label=False)
    rendered = render_bilingual_documents([doc], [seg], translations, output_config=cfg)
    soup = BeautifulSoup(rendered["chap01.xhtml"], "lxml")
    assert soup.find(class_="block-label") is None


def test_note_mode_source_hidden_when_show_source_false():
    doc = make_doc(SAMPLE_HTML)
    seg, _, translations = _make_seg_and_record()
    cfg = _make_output_config("note", show_source=False)
    rendered = render_bilingual_documents([doc], [seg], translations, output_config=cfg)
    soup = BeautifulSoup(rendered["chap01.xhtml"], "lxml")
    assert soup.find(class_="source-block") is None
    assert soup.find(class_="translation-block") is not None


def test_note_mode_data_segment_id_preserved():
    doc = make_doc(SAMPLE_HTML)
    seg, _, translations = _make_seg_and_record()
    cfg = _make_output_config("note")
    rendered = render_bilingual_documents([doc], [seg], translations, output_config=cfg)
    soup = BeautifulSoup(rendered["chap01.xhtml"], "lxml")
    block = soup.find("div", attrs={"data-segment-id": seg.segment_id})
    assert block is not None, "data-segment-id must be present in note mode"


def test_note_mode_source_above_translation():
    """source-block must precede translation-block in DOM order."""
    doc = make_doc(SAMPLE_HTML)
    seg, _, translations = _make_seg_and_record()
    cfg = _make_output_config("note")
    rendered = render_bilingual_documents([doc], [seg], translations, output_config=cfg)
    soup = BeautifulSoup(rendered["chap01.xhtml"], "lxml")
    block = soup.find("div", class_="note-block")
    children = [c for c in block.children if isinstance(c, Tag)]
    classes = [c.get("class", []) for c in children]
    flat = [cls for group in classes for cls in group]
    src_pos = flat.index("source-block")
    trg_pos = flat.index("translation-block")
    assert src_pos < trg_pos, "source-block must come before translation-block"


# --- compact mode structure ---

def test_compact_mode_produces_compact_block_class():
    doc = make_doc(SAMPLE_HTML)
    seg, _, translations = _make_seg_and_record()
    cfg = _make_output_config("compact")
    rendered = render_bilingual_documents([doc], [seg], translations, output_config=cfg)
    soup = BeautifulSoup(rendered["chap01.xhtml"], "lxml")
    assert soup.find("div", class_="compact-block") is not None


def test_compact_mode_has_source_and_translation_blocks():
    doc = make_doc(SAMPLE_HTML)
    seg, _, translations = _make_seg_and_record()
    cfg = _make_output_config("compact")
    rendered = render_bilingual_documents([doc], [seg], translations, output_config=cfg)
    soup = BeautifulSoup(rendered["chap01.xhtml"], "lxml")
    assert soup.find(class_="source-block") is not None
    assert soup.find(class_="translation-block") is not None


# --- simple mode unchanged ---

def test_simple_mode_still_uses_src_trg_classes():
    doc = make_doc(SAMPLE_HTML)
    seg, _, translations = _make_seg_and_record()
    cfg = _make_output_config("simple")
    rendered = render_bilingual_documents([doc], [seg], translations, output_config=cfg)
    soup = BeautifulSoup(rendered["chap01.xhtml"], "lxml")
    assert soup.find(class_="src") is not None
    assert soup.find(class_="trg") is not None
    assert soup.find("div", class_="bilingual-block") is not None


def test_simple_mode_backward_compat_no_output_config():
    """Omitting output_config must behave identically to simple mode."""
    doc = make_doc(SAMPLE_HTML)
    seg, _, translations = _make_seg_and_record()
    rendered_none = render_bilingual_documents([doc], [seg], translations)
    rendered_simple = render_bilingual_documents(
        [doc], [seg], translations, output_config=_make_output_config("simple")
    )
    soup_none = BeautifulSoup(rendered_none["chap01.xhtml"], "lxml")
    soup_simple = BeautifulSoup(rendered_simple["chap01.xhtml"], "lxml")
    assert soup_none.find(class_="src") is not None
    assert soup_simple.find(class_="src") is not None


# --- CSS injection per style ---

def test_note_mode_css_injected():
    doc = make_doc(SAMPLE_HTML)
    seg, _, translations = _make_seg_and_record()
    cfg = _make_output_config("note")
    rendered = render_bilingual_documents([doc], [seg], translations, output_config=cfg)
    soup = BeautifulSoup(rendered["chap01.xhtml"], "lxml")
    style = soup.find("style")
    assert style is not None
    assert "note-block" in style.get_text()
    assert "translation-block" in style.get_text()


def test_compact_mode_css_injected():
    doc = make_doc(SAMPLE_HTML)
    seg, _, translations = _make_seg_and_record()
    cfg = _make_output_config("compact")
    rendered = render_bilingual_documents([doc], [seg], translations, output_config=cfg)
    soup = BeautifulSoup(rendered["chap01.xhtml"], "lxml")
    style = soup.find("style")
    assert style is not None
    assert "compact-block" in style.get_text()


def test_build_bilingual_html_note_css():
    from ebook_translator.renderer import build_bilingual_html
    doc = make_doc(SAMPLE_HTML)
    seg, _, translations = _make_seg_and_record()
    cfg = _make_output_config("note")
    rendered = render_bilingual_documents([doc], [seg], translations, output_config=cfg)
    html = build_bilingual_html([doc], rendered, output_config=cfg)
    assert "note-block" in html
    assert "translation-block" in html


# --- inline tag preservation in note mode ---

def test_note_mode_inline_tags_preserved():
    from ebook_translator.segmenter.segmenter import segment_document

    html = (
        "<html><head></head><body>"
        "<p>Text with <em>emphasis</em> and <strong>bold</strong>.</p>"
        "</body></html>"
    )
    doc = make_doc(html)
    segs = segment_document(doc)
    assert len(segs) == 1
    seg = segs[0]
    translations = {
        seg.segment_id: TranslationRecord(
            segment_id=seg.segment_id,
            source_hash=seg.sha1_prefix,
            status="completed",
            source=seg.source_html,
            translation="帶有<em>強調</em>和<strong>粗體</strong>的文字。",
            model="mock",
            attempt=1,
            created_at=datetime.now(timezone.utc),
        )
    }
    cfg = _make_output_config("note")
    rendered = render_bilingual_documents([doc], segs, translations, output_config=cfg)
    soup = BeautifulSoup(rendered["chap01.xhtml"], "lxml")
    # em and strong in both source and translation sides
    assert soup.find("em") is not None
    assert soup.find("strong") is not None


# --- failed translation fallback in note mode ---

def test_note_mode_failed_translation_leaves_source_only():
    doc = make_doc(SAMPLE_HTML)
    seg, _, _ = _make_seg_and_record()
    record = make_record(seg.segment_id, "", status="failed")
    translations = {seg.segment_id: record}
    cfg = _make_output_config("note")
    rendered = render_bilingual_documents([doc], [seg], translations, output_config=cfg)
    soup = BeautifulSoup(rendered["chap01.xhtml"], "lxml")
    # No bilingual block — original <p> stays
    assert soup.find("div", class_="note-block") is None
    assert soup.find("p") is not None


def test_note_mode_missing_translation_leaves_source_only():
    doc = make_doc(SAMPLE_HTML)
    seg, _, _ = _make_seg_and_record()
    cfg = _make_output_config("note")
    rendered = render_bilingual_documents([doc], [seg], {}, output_config=cfg)
    soup = BeautifulSoup(rendered["chap01.xhtml"], "lxml")
    assert soup.find("div", class_="note-block") is None
    assert soup.find("p") is not None

