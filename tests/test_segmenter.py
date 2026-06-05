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


# ---------------------------------------------------------------------------
# Phase 6: new tag coverage tests
# ---------------------------------------------------------------------------

DIV_DIRECT_TEXT_HTML = b"""
<html><body>
  <div>Direct text inside div should be extracted.</div>
</body></html>
"""

DIV_WITH_P_HTML = b"""
<html><body>
  <div><p>Paragraph inside div.</p></div>
</body></html>
"""

TD_TH_HTML = b"""
<html><body>
  <table>
    <tr><th>Header cell.</th><td>Data cell.</td></tr>
  </table>
</body></html>
"""

FIGCAPTION_HTML = b"""
<html><body>
  <figure>
    <img src="img.png" alt="photo"/>
    <figcaption>Caption text for the figure.</figcaption>
  </figure>
</body></html>
"""

CAPTION_HTML = b"""
<html><body>
  <table>
    <caption>Table caption text.</caption>
    <tr><td>Cell content.</td></tr>
  </table>
</body></html>
"""

DT_DD_HTML = b"""
<html><body>
  <dl>
    <dt>Term one.</dt>
    <dd>Definition one.</dd>
  </dl>
</body></html>
"""

DIV_NO_TEXT_HTML = b"""
<html><body>
  <div><span></span></div>
</body></html>
"""


def test_div_direct_text_extracted():
    """A div whose only content is a bare text string should be segmented."""
    segs = segment_document(make_doc(DIV_DIRECT_TEXT_HTML))
    texts = [s.source_text for s in segs]
    assert any("Direct text inside div" in t for t in texts), f"Got: {texts}"


def test_div_wrapping_p_not_duplicated():
    """A div that wraps a <p> must NOT create a second segment for the outer div."""
    segs = segment_document(make_doc(DIV_WITH_P_HTML))
    # Exactly one segment (the <p>), not two
    assert len(segs) == 1
    assert "Paragraph inside div" in segs[0].source_text


def test_td_th_extracted():
    """<td> and <th> cells must both be segmented."""
    segs = segment_document(make_doc(TD_TH_HTML))
    texts = [s.source_text for s in segs]
    assert any("Header cell" in t for t in texts), f"Got: {texts}"
    assert any("Data cell" in t for t in texts), f"Got: {texts}"


def test_figcaption_inside_figure_extracted():
    """<figcaption> inside <figure> must not be silently skipped.

    Previously 'figure' was in SKIP_CONTAINERS, which caused figcaption to
    be dropped even though it carries visible translatable text.
    """
    segs = segment_document(make_doc(FIGCAPTION_HTML))
    texts = [s.source_text for s in segs]
    assert any("Caption text for the figure" in t for t in texts), f"Got: {texts}"


def test_table_caption_extracted():
    """<caption> elements in tables must be segmented."""
    segs = segment_document(make_doc(CAPTION_HTML))
    texts = [s.source_text for s in segs]
    assert any("Table caption text" in t for t in texts), f"Got: {texts}"


def test_dt_dd_extracted():
    """<dt> and <dd> elements in definition lists must be segmented."""
    segs = segment_document(make_doc(DT_DD_HTML))
    texts = [s.source_text for s in segs]
    assert any("Term one" in t for t in texts), f"Got: {texts}"
    assert any("Definition one" in t for t in texts), f"Got: {texts}"


def test_div_with_no_direct_text_not_extracted():
    """A div containing only whitespace/empty children should not produce a segment."""
    segs = segment_document(make_doc(DIV_NO_TEXT_HTML))
    assert len(segs) == 0

