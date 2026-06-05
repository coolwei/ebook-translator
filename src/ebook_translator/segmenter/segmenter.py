from __future__ import annotations

import hashlib
import re

from bs4 import BeautifulSoup, NavigableString, Tag

from ..epub.reader import SpineDocument
from ..models import Segment


# Tags whose *own* text content should be translated (leaf-level blocks).
# figcaption is included here; note that 'figure' has been removed from
# SKIP_CONTAINERS so figcaption inside <figure> is no longer silently dropped.
TRANSLATABLE_TAGS = {
    "p", "h1", "h2", "h3", "h4", "h5", "h6",
    "li", "blockquote",
    "td", "th",
    "figcaption",
    "caption",   # <table><caption>
    "dt", "dd",  # definition list terms / descriptions
}

# Containers whose *entire subtree* should be skipped (navigation, code, etc.).
# 'figure' has been intentionally removed so figcaption is reachable.
SKIP_CONTAINERS = {"nav", "aside", "script", "style"}

# Tags that may carry direct visible text children (not already caught by a
# block-level child).  We harvest NavigableString children directly so we don't
# create duplicate segments for text already inside a nested translatable tag.
DIV_LIKE_TAGS = {"div", "section", "article", "main", "header", "footer"}


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


def _has_direct_text(tag: Tag) -> bool:
    """Return True if *tag* contains at least one non-whitespace NavigableString
    that is a direct child (not nested inside another element)."""
    for child in tag.children:
        if isinstance(child, NavigableString) and child.strip():
            return True
    return False


def iter_translatable_blocks(soup: BeautifulSoup):
    """Yield (block_index, tag, normalized_text) for each translatable block.

    This is the single source of truth for block ordering, shared by the
    segmenter and the renderer so segment IDs line up deterministically.

    Two classes of blocks are handled:
    1. Known block tags (TRANSLATABLE_TAGS) — the classic case.
    2. DIV-like containers (DIV_LIKE_TAGS) that carry direct text children
       but whose immediate children are NOT one of the TRANSLATABLE_TAGS.
       This avoids creating duplicate segments when a div simply wraps a <p>.
    """
    block_index = 0
    seen_tags: set[int] = set()  # id() of Tag objects already yielded

    # --- Pass 1: standard translatable block tags ---
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

        seen_tags.add(id(tag))
        yield block_index, tag, text
        block_index += 1

    # --- Pass 2: div-like containers with direct (bare) text children ---
    for tag in soup.find_all(DIV_LIKE_TAGS):
        if not isinstance(tag, Tag):
            continue
        if _is_inside_skip_container(tag):
            continue
        if id(tag) in seen_tags:
            continue
        # Only harvest if the div has at least one direct text child AND
        # does NOT already contain a proper block child (that would be
        # translated in pass 1 and would cause a duplicate).
        if tag.find(TRANSLATABLE_TAGS):
            continue
        if not _has_direct_text(tag):
            continue

        text = normalize_text(tag.get_text())
        if not text:
            continue

        seen_tags.add(id(tag))
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
