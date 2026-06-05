"""Generate a small example EPUB for smoke testing.

Usage:
    python scripts/make_example_epub.py [output_path]

Default output: books/example.epub
"""
from __future__ import annotations

import sys
from pathlib import Path

from ebooklib import epub


def build(output_path: Path) -> None:
    book = epub.EpubBook()
    book.set_identifier("example-book-001")
    book.set_title("Example Book")
    book.set_language("en")
    book.add_author("Jane Doe")

    ch1 = epub.EpubHtml(title="Chapter 1", file_name="chap01.xhtml", lang="en")
    ch1.set_content(
        b"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml">
<head><title>Chapter 1</title></head>
<body>
  <h1>The Beginning</h1>
  <p>It was a <em>bright</em> cold day in April, and the clocks were striking thirteen.</p>
  <p>Winston Smith walked through the glass doors of Victory Mansions.</p>
  <p>For more details, visit <a href="https://example.com/intro">the introduction</a>.</p>
  <blockquote>War is peace. Freedom is slavery. Ignorance is strength.</blockquote>
</body>
</html>"""
    )

    ch2 = epub.EpubHtml(title="Chapter 2", file_name="chap02.xhtml", lang="en")
    ch2.set_content(
        b"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml">
<head><title>Chapter 2</title></head>
<body>
  <h1>The Middle</h1>
  <p>The <strong>quick</strong> brown fox jumps over the lazy dog.</p>
  <p>   </p>
  <p>Pack my box with five dozen liquor jugs.</p>
  <ul>
    <li>First list item.</li>
    <li>Second list item.</li>
  </ul>
</body>
</html>"""
    )

    ch3 = epub.EpubHtml(title="Chapter 3", file_name="chap03.xhtml", lang="en")
    ch3.set_content(
        b"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml">
<head><title>Chapter 3</title></head>
<body>
  <h1>The End</h1>
  <p>How vexingly quick daft zebras jump.</p>
  <p>Sphinx of black quartz, judge my vow.</p>
</body>
</html>"""
    )

    for ch in (ch1, ch2, ch3):
        book.add_item(ch)

    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    book.spine = ["nav", ch1, ch2, ch3]

    output_path.parent.mkdir(parents=True, exist_ok=True)
    epub.write_epub(str(output_path), book, {})
    print(f"Wrote {output_path}")


if __name__ == "__main__":
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("books/example.epub")
    build(out)
