from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import ebooklib
from ebooklib import epub


SKIP_MEDIA_TYPES = {
    "application/x-dtbncx+xml",
    "application/oebps-package+xml",
}

SKIP_TYPES = {
    ebooklib.ITEM_NAVIGATION,
    ebooklib.ITEM_STYLE,
    ebooklib.ITEM_IMAGE,
    ebooklib.ITEM_FONT,
    ebooklib.ITEM_SMIL,
    ebooklib.ITEM_AUDIO,
    ebooklib.ITEM_VIDEO,
    ebooklib.ITEM_COVER,
    ebooklib.ITEM_SCRIPT,
}


@dataclass
class SpineDocument:
    chapter_index: int
    item_id: str
    href: str
    content: bytes
    media_type: str


def read_epub(path: Path) -> tuple[epub.EpubBook, list[SpineDocument]]:
    book = epub.read_epub(str(path), options={"ignore_ncx": True})
    docs: list[SpineDocument] = []
    chapter_index = 0

    for item_id, linear in book.spine:
        item = book.get_item_with_id(item_id)
        if item is None:
            continue
        # EpubNav reports ITEM_DOCUMENT in ebooklib 0.20, so skip it by instance.
        if isinstance(item, epub.EpubNav):
            continue
        if item.get_type() in SKIP_TYPES:
            continue
        if item.media_type in SKIP_MEDIA_TYPES:
            continue
        if item.media_type not in ("application/xhtml+xml", "text/html"):
            continue

        docs.append(
            SpineDocument(
                chapter_index=chapter_index,
                item_id=item.get_id(),
                href=item.get_name(),
                content=item.get_content(),
                media_type=item.media_type,
            )
        )
        chapter_index += 1

    return book, docs
