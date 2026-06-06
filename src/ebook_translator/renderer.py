"""renderer.py — inject bilingual blocks into EPUB spine documents.

Supports three output styles controlled by OutputConfig.bilingual_style:

* simple  — original src/trg pair, backward-compatible (default)
* note    — Chinese reading-note mode: source faded, translation as primary
* compact — tighter spacing for long books
"""
from __future__ import annotations

import warnings
from typing import TYPE_CHECKING

from bs4 import BeautifulSoup, Tag, XMLParsedAsHTMLWarning

warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)

from .epub.reader import SpineDocument
from .models import Segment, TranslationRecord

if TYPE_CHECKING:
    from .config import OutputConfig


# ---------------------------------------------------------------------------
# CSS per style
# ---------------------------------------------------------------------------

_CSS_SIMPLE = """\
body {
  line-height: 1.7;
}
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

_CSS_NOTE = """\
body {
  line-height: 1.8;
  font-family: system-ui, -apple-system, BlinkMacSystemFont,
               "Noto Sans TC", "Microsoft JhengHei", sans-serif;
}
.bilingual-block.note-block {
  margin: 1.4em 0 1.8em 0;
  padding: 0.8em 0;
  border-bottom: 1px solid #e5e5e5;
}
.source-block {
  opacity: 0.58;
  font-size: 0.88em;
  line-height: 1.55;
  margin-bottom: 0.45em;
}
.translation-block {
  font-size: 1.02em;
  line-height: 1.85;
  padding-left: 0.85em;
  border-left: 3px solid #888;
}
.block-label {
  font-size: 0.72em;
  letter-spacing: 0.08em;
  opacity: 0.55;
  margin-bottom: 0.25em;
}
.translation-label {
  font-weight: 600;
}
"""

_CSS_COMPACT = """\
body {
  line-height: 1.65;
  font-family: system-ui, -apple-system, BlinkMacSystemFont,
               "Noto Sans TC", "Microsoft JhengHei", sans-serif;
}
.bilingual-block.compact-block {
  margin: 0.6em 0 0.9em 0;
  padding: 0.4em 0;
  border-bottom: 1px solid #ececec;
}
.source-block {
  opacity: 0.55;
  font-size: 0.86em;
  line-height: 1.45;
  margin-bottom: 0.3em;
}
.translation-block {
  font-size: 1em;
  line-height: 1.7;
  padding-left: 0.7em;
  border-left: 2px solid #aaa;
}
.block-label {
  font-size: 0.7em;
  letter-spacing: 0.06em;
  opacity: 0.5;
  margin-bottom: 0.2em;
}
.translation-label {
  font-weight: 600;
}
"""

# Kept for backward-compat with existing code that imports BILINGUAL_CSS
BILINGUAL_CSS = _CSS_SIMPLE


def _get_css(style: str) -> str:
    if style == "note":
        return _CSS_NOTE
    if style == "compact":
        return _CSS_COMPACT
    return _CSS_SIMPLE


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def render_bilingual_documents(
    spine_docs: list[SpineDocument],
    segments: list[Segment],
    translations: dict[str, TranslationRecord],
    output_config: "OutputConfig | None" = None,
) -> dict[str, bytes]:
    """Render bilingual EPUB documents.

    *output_config* selects the visual style (simple / note / compact).
    When omitted or None, falls back to ``simple`` mode for full backward
    compatibility with callers that do not pass config.
    """
    seg_by_pos: dict[tuple[str, int], Segment] = {
        (seg.chapter_href, seg.block_index): seg for seg in segments
    }
    result: dict[str, bytes] = {}
    for doc in spine_docs:
        result[doc.href] = _inject_into_document(doc, seg_by_pos, translations, output_config)
    return result


def build_bilingual_html(
    spine_docs: list[SpineDocument],
    rendered: dict[str, bytes],
    output_config: "OutputConfig | None" = None,
) -> str:
    """Assemble a single-page bilingual HTML preview."""
    style = output_config.bilingual_style if output_config else "simple"
    css = _get_css(style)

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
        f"<style>{css}</style>\n"
        "</head>\n"
        f"<body>\n{joined}\n</body>\n"
        "</html>\n"
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _inject_into_document(
    doc: SpineDocument,
    seg_by_pos: dict[tuple[str, int], Segment],
    translations: dict[str, TranslationRecord],
    output_config: "OutputConfig | None",
) -> bytes:
    from .segmenter.segmenter import iter_translatable_blocks

    style = output_config.bilingual_style if output_config else "simple"
    css = _get_css(style)
    show_source = output_config.show_source if output_config else True
    add_label = output_config.add_translation_label if output_config else False
    src_opacity = output_config.source_opacity if output_config else 0.78

    soup = BeautifulSoup(doc.content, "lxml")

    # Inject CSS into <head>
    head = soup.find("head")
    if isinstance(head, Tag):
        style_tag = soup.new_tag("style")
        style_tag.string = css
        head.append(style_tag)

    blocks = list(iter_translatable_blocks(soup))

    for block_index, tag, _text in blocks:
        seg = seg_by_pos.get((doc.href, block_index))
        if seg is None:
            continue

        record = translations.get(seg.segment_id)
        if record is None or record.status != "completed" or not record.translation.strip():
            # Missing / failed / empty: leave source element untouched (fallback)
            continue

        source_html = tag.decode_contents()

        if style == "note":
            block = _build_note_block(
                soup, seg, tag.name, source_html, record.translation,
                show_source=show_source, add_label=add_label,
            )
        elif style == "compact":
            block = _build_compact_block(
                soup, seg, tag.name, source_html, record.translation,
                show_source=show_source, add_label=add_label,
            )
        else:
            block = _build_simple_block(
                soup, seg, tag.name, source_html, record.translation,
                show_source=show_source,
            )

        tag.replace_with(block)

    return soup.encode(formatter="html5")


def _build_simple_block(
    soup: BeautifulSoup,
    seg: Segment,
    tag_name: str,
    source_html: str,
    translation: str,
    *,
    show_source: bool,
) -> Tag:
    block = soup.new_tag(
        "div",
        attrs={"class": "bilingual-block", "data-segment-id": seg.segment_id},
    )
    if show_source:
        block.append(_make_inner(soup, tag_name, "src", source_html))
    block.append(_make_inner(soup, tag_name, "trg", translation))
    return block


def _build_note_block(
    soup: BeautifulSoup,
    seg: Segment,
    tag_name: str,
    source_html: str,
    translation: str,
    *,
    show_source: bool,
    add_label: bool,
) -> Tag:
    block = soup.new_tag(
        "div",
        attrs={"class": "bilingual-block note-block", "data-segment-id": seg.segment_id},
    )

    if show_source:
        src_div = soup.new_tag("div", attrs={"class": "source-block"})
        if add_label:
            lbl = soup.new_tag("div", attrs={"class": "block-label source-label"})
            lbl.string = "原文"
            src_div.append(lbl)
        src_div.append(_make_inner(soup, tag_name, "src", source_html))
        block.append(src_div)

    trg_div = soup.new_tag("div", attrs={"class": "translation-block"})
    if add_label:
        lbl = soup.new_tag("div", attrs={"class": "block-label translation-label"})
        lbl.string = "譯文"
        trg_div.append(lbl)
    trg_div.append(_make_inner(soup, tag_name, "trg", translation))
    block.append(trg_div)

    return block


def _build_compact_block(
    soup: BeautifulSoup,
    seg: Segment,
    tag_name: str,
    source_html: str,
    translation: str,
    *,
    show_source: bool,
    add_label: bool,
) -> Tag:
    block = soup.new_tag(
        "div",
        attrs={"class": "bilingual-block compact-block", "data-segment-id": seg.segment_id},
    )

    if show_source:
        src_div = soup.new_tag("div", attrs={"class": "source-block"})
        if add_label:
            lbl = soup.new_tag("div", attrs={"class": "block-label source-label"})
            lbl.string = "原文"
            src_div.append(lbl)
        src_div.append(_make_inner(soup, tag_name, "src", source_html))
        block.append(src_div)

    trg_div = soup.new_tag("div", attrs={"class": "translation-block"})
    if add_label:
        lbl = soup.new_tag("div", attrs={"class": "block-label translation-label"})
        lbl.string = "譯文"
        trg_div.append(lbl)
    trg_div.append(_make_inner(soup, tag_name, "trg", translation))
    block.append(trg_div)

    return block


def _make_inner(
    soup: BeautifulSoup, tag_name: str, css_class: str, inner_html: str
) -> Tag:
    """Create a *tag_name* element with *css_class* and parsed *inner_html* children."""
    tag = soup.new_tag(tag_name, attrs={"class": css_class})
    fragment = BeautifulSoup(f"<{tag_name}>{inner_html}</{tag_name}>", "lxml")
    inner = fragment.find(tag_name)
    if isinstance(inner, Tag):
        tag.extend(inner.children)
    else:
        tag.string = inner_html
    return tag
