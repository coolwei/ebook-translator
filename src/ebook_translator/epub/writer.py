from __future__ import annotations

from pathlib import Path

from ebooklib import epub


def write_bilingual_epub(
    original_book: epub.EpubBook,
    rendered: dict[str, bytes],
    output_path: Path,
) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)

    for item in original_book.get_items():
        href = item.get_name()
        if href in rendered:
            item.set_content(rendered[href])

    epub.write_epub(str(output_path), original_book, {})
