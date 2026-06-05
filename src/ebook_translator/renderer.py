from __future__ import annotations

import warnings

from bs4 import BeautifulSoup, Tag, XMLParsedAsHTMLWarning

warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)

from .epub.reader import SpineDocument
from .models import Segment, TranslationRecord


BILINGUAL_CSS = """\
.bilingual-block {
  margin-bottom: 1em;
}

.src {
  opacity: 0.78;
}

.trg {
  margin-top: 0.25em;
  font-weight: 400;
}
"""


def render_bilingual_documents(
    spine_docs: list[SpineDocument],
    segments: list[Segment],
    translations: dict[str, TranslationRecord],
) -> dict[str, bytes]:
    # Map (href, block_index) -> Segment so the renderer can resolve the
    # deterministic segment for each translatable block it walks.
    seg_by_pos: dict[tuple[str, int], Segment] = {
        (seg.chapter_href, seg.block_index): seg for seg in segments
    }

    result: dict[str, bytes] = {}
    for doc in spine_docs:
        result[doc.href] = _inject_into_document(doc, seg_by_pos, translations)
    return result


def _inject_into_document(
    doc: SpineDocument,
    seg_by_pos: dict[tuple[str, int], Segment],
    translations: dict[str, TranslationRecord],
) -> bytes:
    from .segmenter.segmenter import iter_translatable_blocks

    soup = BeautifulSoup(doc.content, "lxml")

    # Inject bilingual CSS
    head = soup.find("head")
    if isinstance(head, Tag):
        style_tag = soup.new_tag("style")
        style_tag.string = BILINGUAL_CSS
        head.append(style_tag)

    # Materialize the walk first; replacing tags mid-iteration would disturb it.
    blocks = list(iter_translatable_blocks(soup))

    for block_index, tag, _text in blocks:
        seg = seg_by_pos.get((doc.href, block_index))
        if seg is None:
            continue

        record = translations.get(seg.segment_id)
        if record is None or record.status != "completed" or not record.translation.strip():
            # Missing/failed/empty translation: leave the source element untouched.
            continue

        source_html = tag.decode_contents()
        block = soup.new_tag(
            "div",
            attrs={"class": "bilingual-block", "data-segment-id": seg.segment_id},
        )
        block.append(_make_element_with_inner(soup, tag.name, "src", source_html))
        block.append(_make_element_with_inner(soup, tag.name, "trg", record.translation))
        tag.replace_with(block)

    return soup.encode(formatter="html5")


def _make_element_with_inner(soup: BeautifulSoup, tag_name: str, css_class: str, inner_html: str) -> Tag:
    tag = soup.new_tag(tag_name, attrs={"class": css_class})
    fragment = BeautifulSoup(f"<{tag_name}>{inner_html}</{tag_name}>", "lxml")
    inner = fragment.find(tag_name)
    if isinstance(inner, Tag):
        tag.extend(inner.children)
    else:
        tag.string = inner_html
    return tag


def build_bilingual_html(
    spine_docs: list[SpineDocument],
    rendered: dict[str, bytes],
) -> str:
    chapters: list[str] = []
    for doc in spine_docs:
        html_bytes = rendered.get(doc.href, doc.content)
        soup = BeautifulSoup(html_bytes, "lxml")
        body = soup.find("body")
        if isinstance(body, Tag):
            chapters.append(str(body))
        else:
            chapters.append(html_bytes.decode("utf-8", errors="replace"))

    joined = "\n".join(chapters)
    return (
        "<!DOCTYPE html>\n"
        '<html lang="zh-TW">\n'
        "<head>\n"
        '<meta charset="UTF-8">\n'
        "<title>Bilingual EPUB</title>\n"
        f"<style>{BILINGUAL_CSS}</style>\n"
        "</head>\n"
        f"<body>\n{joined}\n</body>\n"
        "</html>\n"
    )
