"""Offline translation cost / runtime estimation.

This module never calls a provider or any network API. It reads the EPUB,
segments it, and produces rough cost/runtime estimates plus risk warnings.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

from .config import AppConfig
from .epub.reader import read_epub
from .models import Segment
from .segmenter.segmenter import segment_all_documents


# Token estimation is intentionally pluggable: swap this for a real tokenizer
# (e.g. tiktoken) later without touching the report-building code.
TOKEN_DIVISORS = {
    "english": 4.0,  # English-heavy: ~4 chars/token
    "cjk": 1.5,      # CJK-heavy: ~1.5 chars/token
    "mixed": 3.0,    # Mixed (conservative default)
}
ESTIMATION_METHOD = "rough_mixed_chars_div_3"

OUTPUT_TOKEN_RATIO = 0.8  # rough: output tokens ~= 0.8 * input tokens
RETRY_OVERHEAD_RATIO = 0.1

LONG_RUNTIME_MINUTES = 60
LARGE_BOOK_SEGMENTS = 3000
MAX_REPORTED_SEGMENT_IDS = 10


def estimate_tokens(text: str, method: str = "mixed") -> int:
    """Rough token count for a string. Replace with a real tokenizer later."""
    divisor = TOKEN_DIVISORS.get(method, TOKEN_DIVISORS["mixed"])
    return int(len(text) / divisor)


def estimate_input_tokens(total_chars: int, method: str = "mixed") -> int:
    divisor = TOKEN_DIVISORS.get(method, TOKEN_DIVISORS["mixed"])
    return int(total_chars / divisor)


def estimate_output_tokens(input_tokens: int) -> int:
    return int(round(input_tokens * OUTPUT_TOKEN_RATIO))


def requests_with_retry(estimated_requests: int, ratio: float = RETRY_OVERHEAD_RATIO) -> int:
    # Round away floating-point noise (e.g. 100 * 1.1 == 110.00000000000001)
    # before ceiling so whole multiples stay whole.
    return math.ceil(round(estimated_requests * (1.0 + ratio), 6))


def runtime_minutes(requests: int, rpm: int) -> float:
    if rpm <= 0:
        return 0.0
    return round(requests / rpm, 2)


def _segment_char_count(seg: Segment) -> int:
    return len(seg.source_text)


def build_estimate_report(config: AppConfig) -> dict:
    """Read the EPUB, segment it, and build the estimate report dict.

    Reads a local EPUB file only — no provider/API calls.
    """
    from .translator import _get_book_name  # local import to avoid cycle

    book, spine_docs = read_epub(config.input.path)
    title = config.input.path.stem
    try:
        titles = book.get_metadata("DC", "title")
        if titles:
            title = str(titles[0][0])
    except Exception:
        pass
    book_name = _get_book_name(book, config.input.path)

    segments = segment_all_documents(spine_docs)
    max_chars = config.limits.max_chars_per_chunk

    char_counts = [_segment_char_count(s) for s in segments]
    count = len(segments)
    total_chars = sum(char_counts)
    largest = max(char_counts) if char_counts else 0
    average = round(total_chars / count, 2) if count else 0.0

    exceeding = [s for s in segments if _segment_char_count(s) > max_chars]
    exceeding_ids = [s.segment_id for s in exceeding]

    input_tokens = estimate_input_tokens(total_chars, "mixed")
    output_tokens = estimate_output_tokens(input_tokens)
    total_tokens = input_tokens + output_tokens

    estimated_requests = count  # one request per segment
    with_retry = requests_with_retry(estimated_requests)

    rpm = config.limits.rpm
    concurrency = config.limits.concurrency
    min_minutes = runtime_minutes(estimated_requests, rpm)
    min_minutes_retry = runtime_minutes(with_retry, rpm)

    warnings: list[dict] = []
    if exceeding:
        warnings.append({
            "code": "split_required",
            "message": (
                f"{len(exceeding)} segment(s) exceed max_chars_per_chunk ({max_chars}); "
                "consider splitting before translating."
            ),
            "segment_ids": exceeding_ids[:MAX_REPORTED_SEGMENT_IDS],
        })
    if min_minutes > LONG_RUNTIME_MINUTES:
        warnings.append({
            "code": "long_runtime",
            "message": (
                f"Estimated minimum runtime {min_minutes} min exceeds "
                f"{LONG_RUNTIME_MINUTES} min at rpm={rpm}."
            ),
        })
    if count > LARGE_BOOK_SEGMENTS:
        warnings.append({
            "code": "large_book",
            "message": f"Segment count {count} exceeds {LARGE_BOOK_SEGMENTS}.",
        })

    return {
        "book": {
            "title": title,
            "input_path": str(config.input.path),
            "chapter_count": len(spine_docs),
            "book_name": book_name,
        },
        "segments": {
            "count": count,
            "source_chars": total_chars,
            "largest_segment_chars": largest,
            "average_segment_chars": average,
            "exceeding_max_chars_count": len(exceeding),
            "exceeding_max_chars_segment_ids": exceeding_ids[:MAX_REPORTED_SEGMENT_IDS],
        },
        "tokens": {
            "estimation_method": ESTIMATION_METHOD,
            "estimated_input_tokens": input_tokens,
            "estimated_output_tokens": output_tokens,
            "estimated_total_tokens": total_tokens,
            "output_token_note": "rough estimate: input_tokens * 0.8",
        },
        "requests": {
            "estimated_requests": estimated_requests,
            "retry_overhead_ratio": RETRY_OVERHEAD_RATIO,
            "estimated_requests_with_retry": with_retry,
        },
        "runtime": {
            "configured_rpm": rpm,
            "configured_concurrency": concurrency,
            "minimum_minutes": min_minutes,
            "minimum_minutes_with_retry": min_minutes_retry,
        },
        "warnings": warnings,
    }


def run_estimate(config: AppConfig) -> dict:
    """Build the estimate, print a console summary, and write estimate_report.json."""
    report = build_estimate_report(config)

    output_dir = config.project.output_dir / report["book"]["book_name"]
    output_dir.mkdir(parents=True, exist_ok=True)
    report_path = output_dir / "estimate_report.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    b = report["book"]
    s = report["segments"]
    t = report["tokens"]
    r = report["requests"]
    rt = report["runtime"]

    print("=" * 60)
    print("Translation Estimate (no API calls were made)")
    print("=" * 60)
    print(f"Book title                 : {b['title']}")
    print(f"Input path                 : {b['input_path']}")
    print(f"Chapter count              : {b['chapter_count']}")
    print(f"Segment count              : {s['count']}")
    print(f"Source character count     : {s['source_chars']}")
    print(f"Largest segment chars      : {s['largest_segment_chars']}")
    print(f"Average segment chars      : {s['average_segment_chars']}")
    print(f"Estimated input tokens     : {t['estimated_input_tokens']}  ({t['estimation_method']})")
    print(f"Estimated output tokens    : {t['estimated_output_tokens']}  (rough: input * {OUTPUT_TOKEN_RATIO})")
    print(f"Estimated total tokens     : {t['estimated_total_tokens']}")
    print(f"Estimated requests         : {r['estimated_requests']}")
    print(f"Configured rpm             : {rt['configured_rpm']}")
    print(f"Configured concurrency     : {rt['configured_concurrency']}")
    print(f"Est. minimum runtime (rpm) : {rt['minimum_minutes']} min")
    print(f"Retry overhead             : {int(r['retry_overhead_ratio'] * 100)}%")
    print(f"Est. requests with retry   : {r['estimated_requests_with_retry']}")
    print(f"Est. runtime with retry    : {rt['minimum_minutes_with_retry']} min")
    print(f"Segments over max_chars     : {s['exceeding_max_chars_count']} (limit {config.limits.max_chars_per_chunk})")

    if report["warnings"]:
        print("-" * 60)
        print("Warnings:")
        for w in report["warnings"]:
            print(f"  [{w['code']}] {w['message']}")
            if w.get("segment_ids"):
                print(f"      first ids: {', '.join(w['segment_ids'])}")
    print("=" * 60)
    print(f"Report saved to {report_path}")

    return report
