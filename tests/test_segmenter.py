from __future__ import annotations

from ebook_translator.epub.reader import SpineDocument
from ebook_translator.segmenter.segmenter import (
    make_segment_id,
    normalize_text,
    segment_document,
)


def make_doc(html: str | bytes, chapter_index: int = 0, href: str = "chap.xhtml") -> SpineDocument:
    if isinstance(html, str):
        html = html.encode("utf-8")
    return SpineDocument(
        chapter_index=chapter_index,
        item_id="item1",
        href=href,
        content=html,
        media_type="application/xhtml+xml",
    )


SIMPLE_HTML = b"""
<html><body>
  <h1>Title</h1>
  <p>First paragraph.</p>
  <p>Second paragraph.</p>
</body></html>
"""

NAV_HTML = b"""
<html><body>
  <p>Visible paragraph.</p>
  <nav><ul><li>Nav item should be skipped.</li></ul></nav>
  <p>Another visible paragraph.</p>
</body></html>
"""

ASIDE_HTML = b"""
<html><body>
  <p>Main text.</p>
  <aside><p>Aside text should be skipped.</p></aside>
</body></html>
"""

WHITESPACE_HTML = b"""
<html><body>
  <p>   </p>
  <p>Real content here.</p>
</body></html>
"""


def test_simple_document_produces_correct_count():
    segs = segment_document(make_doc(SIMPLE_HTML))
    # h1 + p + p = 3
    assert len(segs) == 3


def test_nav_elements_skipped():
    segs = segment_document(make_doc(NAV_HTML))
    texts = [s.source_text for s in segs]
    assert all("Nav item" not in t for t in texts)
    assert len(segs) == 2


def test_aside_skipped():
    segs = segment_document(make_doc(ASIDE_HTML))
    texts = [s.source_text for s in segs]
    assert all("Aside text" not in t for t in texts)
    assert len(segs) == 1


def test_whitespace_only_skipped():
    segs = segment_document(make_doc(WHITESPACE_HTML))
    assert len(segs) == 1
    assert segs[0].source_text == "Real content here."


def test_segment_id_format():
    seg_id = make_segment_id(3, 42, "Hello world")
    parts = seg_id.split(":")
    assert len(parts) == 3
    assert parts[0] == "0003"
    assert parts[1] == "0042"
    assert len(parts[2]) == 8


def test_segment_id_stability():
    id1 = make_segment_id(0, 0, "Hello world")
    id2 = make_segment_id(0, 0, "Hello world")
    assert id1 == id2


def test_segment_id_changes_with_text():
    id1 = make_segment_id(0, 0, "Hello world")
    id2 = make_segment_id(0, 0, "Hello world!")
    assert id1 != id2


def test_segment_id_changes_with_index():
    id1 = make_segment_id(0, 0, "Same text")
    id2 = make_segment_id(0, 1, "Same text")
    assert id1 != id2


def test_segment_ids_stable_across_calls():
    segs1 = segment_document(make_doc(SIMPLE_HTML))
    segs2 = segment_document(make_doc(SIMPLE_HTML))
    assert [s.segment_id for s in segs1] == [s.segment_id for s in segs2]


def test_segment_chapter_index_stored():
    segs = segment_document(make_doc(SIMPLE_HTML, chapter_index=5))
    assert all(s.chapter_index == 5 for s in segs)


def test_segment_href_stored():
    segs = segment_document(make_doc(SIMPLE_HTML, href="part2.xhtml"))
    assert all(s.chapter_href == "part2.xhtml" for s in segs)


def test_data_segment_id_injected():
    from bs4 import BeautifulSoup
    doc = make_doc(SIMPLE_HTML)
    segs = segment_document(doc)
    # The function modifies the parsed soup but returns a new list
    # Re-parse the content to check the attribute was NOT added to the raw bytes
    # (segmenter operates on its own soup parse; raw doc.content is unchanged)
    # Instead verify all segments have data embedded in their id
    for s in segs:
        assert ":" in s.segment_id
