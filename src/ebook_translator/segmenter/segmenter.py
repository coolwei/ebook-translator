from __future__ import annotations

import hashlib
import re

from bs4 import BeautifulSoup, Tag

from ..epub.reader import SpineDocument
from ..models import Segment


TRANSLATABLE_TAGS = {"p", "h1", "h2", "h3", "h4", "h5", "h6", "li", "blockquote", "td", "th", "figcaption"}
SKIP_CONTAINERS = {"nav", "aside", "script", "style", "figure"}


def normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def make_segment_id(chapter_index: int, block_index: int, text: str) -> str:
    normalized = normalize_text(text).lower()
    sha1 = hashlib.sha1(normalized.encode("utf-8")).hexdigest()[:8]
    return f"{chapter_index:04d}:{block_index:04d}:{sha1}"


def _is_inside_skip_container(tag: Tag) -> bool:
    for parent in tag.parents:
        if isinstance(parent, Tag) and parent.name in SKIP_CONTAINERS:
            return True
    return False


def iter_translatable_blocks(soup: BeautifulSoup):
    """Yield (block_index, tag, normalized_text) for each translatable block.

    This is the single source of truth for block ordering, shared by the
    segmenter and the renderer so segment IDs line up deterministically.
    """
    block_index = 0
    for tag in soup.find_all(TRANSLATABLE_TAGS):
        if not isinstance(tag, Tag):
            continue
        if _is_inside_skip_container(tag):
            continue

        text = normalize_text(tag.get_text())
        if not text:
            continue

        # Skip elements that only contain other block-level elements (avoid duplicates)
        if tag.find_all(TRANSLATABLE_TAGS):
            continue

        yield block_index, tag, text
        block_index += 1


def segment_document(doc: SpineDocument) -> list[Segment]:
    soup = BeautifulSoup(doc.content, "lxml")
    segments: list[Segment] = []

    for block_index, tag, text in iter_translatable_blocks(soup):
        segment_id = make_segment_id(doc.chapter_index, block_index, text)
        segments.append(
            Segment(
                segment_id=segment_id,
                chapter_index=doc.chapter_index,
                block_index=block_index,
                sha1_prefix=segment_id.split(":")[-1],
                source_text=text,
                source_html=tag.decode_contents(),
                tag_name=tag.name,
                chapter_href=doc.href,
            )
        )

    return segments


def segment_all_documents(docs: list[SpineDocument]) -> list[Segment]:
    all_segments: list[Segment] = []
    for doc in docs:
        all_segments.extend(segment_document(doc))
    return all_segments
