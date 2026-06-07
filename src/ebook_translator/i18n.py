"""i18n.py — Internationalization support for CLI output.

Provides translation dictionary and helper functions for zh-TW/en CLI display.
"""
from __future__ import annotations

from typing import Any

# Translation dictionary: zh-TW keys → zh-TW values
_ZH_TW: dict[str, str] = {
    # estimate.py
    "translation_estimate_title": "翻譯估算（未呼叫 API）",
    "book_title": "書籍標題",
    "input_path": "輸入路徑",
    "chapter_count": "章節數",
    "segment_count": "段落數",
    "source_char_count": "原文字元數",
    "largest_segment_chars": "最長段落字元數",
    "average_segment_chars": "平均段落字元數",
    "est_input_tokens": "預估輸入 tokens",
    "est_output_tokens": "預估輸出 tokens",
    "est_total_tokens": "預估總 tokens",
    "est_requests": "預估請求數",
    "configured_rpm": "設定 RPM",
    "configured_concurrency": "設定並行數",
    "est_min_runtime": "預估最短時間",
    "retry_overhead": "重試預估增量",
    "est_requests_with_retry": "預估含重試請求數",
    "est_runtime_with_retry": "預估含重試時間",
    "segments_over_max_chars": "超過字元上限段落",
    "warnings": "警告",
    "report_saved_to": "報告已儲存至",

    # translator.py — inspect
    "book_name": "書籍名稱",
    "spine_docs": "章節文件",
    "total_segments": "總段落數",
    "segments": "段落",

    # translator.py — validate
    "validation": "驗證",
    "checked": "已檢查",
    "validation_warnings": "警告",
    "validation_errors": "錯誤",
    "report_saved": "報告已儲存至",

    # translator.py — export
    "exported": "已匯出",

    # translator.py — report-missing
    "missing_translation_report": "漏翻報告",
    "total_checked_blocks": "已檢查區塊數",
    "missing_count": "漏翻數",
    "breakdown_by_reason": "原因統計",
    "all_blocks_translated": "所有區塊皆已翻譯，無漏翻",

    # cli.py — start
    "book": "書籍",
    "skipping": "跳過",
    "dry_run_complete": "模擬完成，未呼叫 API",
    "estimate_summary": "估算摘要",
    "start_translating": "是否開始翻譯？",
    "skipped": "已跳過",
    "retrying_failed": "重試失敗段落",
    "retrying_quality_failed": "重試品質不合格段落",
    "safe_mode_plan_title": "大書安全模式批次計畫",
    "safe_mode_total_segments": "總段落數",
    "safe_mode_effective_segments": "max-segments 生效後段落數",
    "safe_mode_batch_size": "每批段落數",
    "safe_mode_estimated_batches": "預估批次數",
    "safe_mode_cooldown_seconds": "批次間冷卻秒數",
    "safe_mode_stop_on_rate_limit": "rate limit 停止門檻",
    "safe_mode_batch_start": "開始第 {batch} 批（最多 {limit} 段）",
    "safe_mode_batch_done": "第 {batch} 批完成",
    "safe_mode_cooldown": "批次間冷卻 {seconds} 秒…",
    "safe_mode_stopped_rate_limit": "已停止：累積 rate limit 錯誤達 {count} 次（門檻 {threshold}）",
    "safe_mode_stop_on_no_progress": "無進展停止門檻（批次數）",
    "safe_mode_stopped_no_progress": "已停止：連續 {count} 批無新完成段落（門檻 {threshold}）。請用 explain-quality 或 patch-segment 排查。",

    # cli.py — max-segments safety limit
    "max_segments_skip_book": "已跳過書籍：{book_path}",
    "max_segments_skip_reason": "原因：段落數超過安全上限",
    "max_segments_skip_segment_count": "目前段落數：{count}",
    "max_segments_skip_limit": "目前上限：{limit}",
    "max_segments_skip_howto": "處理方式：",
    "max_segments_skip_raise_all": "  1. 若確認要翻譯這本書，請提高上限：",
    "max_segments_skip_cmd": "     {cmd}",
    "max_segments_skip_raise_book": "  2. 或指定單本：",
    "max_segments_no_books_started": "沒有任何書籍開始翻譯。",
    "max_segments_scanned_summary": "已掃描 {scanned} 本書，{skipped} 本因段落數超過 --max-segments 被跳過。",
    "max_segments_all_skipped_reason": "跳過原因：段落數超過安全上限。",
    "max_segments_suggest_translate": "若要翻譯，請執行：",

    # cli.py — common
    "no_segments_found": "找不到段落。請先執行翻譯。",
    "no_job_state": "找不到工作狀態。",
    "no_epub_found": "找不到 EPUB 檔案。",
    "no_segments_jsonl": "找不到 segments.jsonl。請先執行翻譯。",
    "directory_not_found": "找不到目錄：",
    "interrupted": "已中斷。",
    "done": "完成",

    # Validation summary labels
    "status": "狀態",
    "completed": "已完成",
    "failed": "失敗",

    # translator.py — model fallback
    "model_fallback": "模型 fallback: {from_model} → {to_model}，原因: {reason}",
}

# English translation dictionary: keys → English display text
_EN: dict[str, str] = {
    # estimate.py
    "translation_estimate_title": "Translation Estimate (no API calls were made)",
    "book_title": "Book title",
    "input_path": "Input path",
    "chapter_count": "Chapter count",
    "segment_count": "Segment count",
    "source_char_count": "Source character count",
    "largest_segment_chars": "Largest segment chars",
    "average_segment_chars": "Average segment chars",
    "est_input_tokens": "Estimated input tokens",
    "est_output_tokens": "Estimated output tokens",
    "est_total_tokens": "Estimated total tokens",
    "est_requests": "Estimated requests",
    "configured_rpm": "Configured rpm",
    "configured_concurrency": "Configured concurrency",
    "est_min_runtime": "Est. minimum runtime (rpm)",
    "retry_overhead": "Retry overhead",
    "est_requests_with_retry": "Est. requests with retry",
    "est_runtime_with_retry": "Est. runtime with retry",
    "segments_over_max_chars": "Segments over max_chars",
    "warnings": "Warnings",
    "report_saved_to": "Report saved to",

    # translator.py — inspect
    "book_name": "Book name",
    "spine_docs": "Spine docs",
    "total_segments": "Total segments",
    "segments": "segments",

    # translator.py — validate
    "validation": "Validation",
    "checked": "checked",
    "validation_warnings": "warnings",
    "validation_errors": "errors",
    "report_saved": "Report saved to",

    # translator.py — export
    "exported": "Exported",

    # translator.py — report-missing
    "missing_translation_report": "Missing translation report",
    "total_checked_blocks": "total_checked_blocks",
    "missing_count": "missing_count",
    "breakdown_by_reason": "breakdown by reason",
    "all_blocks_translated": "all blocks translated — nothing missing",

    # cli.py — start
    "book": "Book",
    "skipping": "Skipping",
    "dry_run_complete": "Dry run complete; no API calls were made.",
    "estimate_summary": "Estimate summary",
    "start_translating": "Start translating this book?",
    "skipped": "Skipped",
    "retrying_failed": "Retrying failed segments",
    "retrying_quality_failed": "Retrying quality-failed segments",
    "safe_mode_plan_title": "Large-book safe mode batch plan",
    "safe_mode_total_segments": "Total segments",
    "safe_mode_effective_segments": "Segments after max-segments",
    "safe_mode_batch_size": "Batch size",
    "safe_mode_estimated_batches": "Estimated batches",
    "safe_mode_cooldown_seconds": "Cooldown seconds",
    "safe_mode_stop_on_rate_limit": "Stop on rate limit count",
    "safe_mode_batch_start": "Starting batch {batch} (up to {limit} segments)",
    "safe_mode_batch_done": "Batch {batch} complete",
    "safe_mode_cooldown": "Cooldown between batches: {seconds}s…",
    "safe_mode_stopped_rate_limit": "Stopped: accumulated {count} rate limit error(s) (threshold {threshold})",
    "safe_mode_stop_on_no_progress": "Stop on no-progress batch count",
    "safe_mode_stopped_no_progress": "Stopped: {count} consecutive batch(es) with no new completions (threshold {threshold}). Use 'explain-quality' or 'patch-segment' to investigate.",

    # cli.py — max-segments safety limit
    "max_segments_skip_book": "Skipped book: {book_path}",
    "max_segments_skip_reason": "Reason: segment count exceeds the safety limit",
    "max_segments_skip_segment_count": "Segment count: {count}",
    "max_segments_skip_limit": "Current limit: {limit}",
    "max_segments_skip_howto": "What to do:",
    "max_segments_skip_raise_all": "  1. To translate this book, raise the limit:",
    "max_segments_skip_cmd": "     {cmd}",
    "max_segments_skip_raise_book": "  2. Or target one book:",
    "max_segments_no_books_started": "No books started translation.",
    "max_segments_scanned_summary": "Scanned {scanned} book(s); {skipped} skipped because segment count exceeds --max-segments.",
    "max_segments_all_skipped_reason": "Skip reason: segment count exceeds the safety limit.",
    "max_segments_suggest_translate": "To translate, run:",

    # cli.py — common
    "no_segments_found": "No segments found. Run translate first.",
    "no_job_state": "No job state found.",
    "no_epub_found": "No EPUB files found.",
    "no_segments_jsonl": "No segments.jsonl found. Run 'translate' first.",
    "directory_not_found": "Directory not found:",
    "interrupted": "Interrupted.",
    "done": "Done",

    # Validation summary labels
    "status": "Status",
    "completed": "Completed",
    "failed": "Failed",

    # translator.py — model fallback
    "model_fallback": "Model fallback: {from_model} → {to_model}, reason: {reason}",
}


def t(key: str, lang: str = "zh-TW", **kwargs: Any) -> str:
    """Translate a key to the target language.

    Args:
        key: The translation key (English).
        lang: Target language code ("zh-TW" or "en").
        **kwargs: Format arguments for string interpolation.

    Returns:
        Translated string, or English text if lang is "en".
    """
    if lang == "en":
        translated = _EN.get(key, key)
    else:
        translated = _ZH_TW.get(key, key)
    if kwargs:
        return translated.format(**kwargs)
    return translated


def get_cli_language(config: Any) -> str:
    """Extract CLI language from config object.

    Args:
        config: AppConfig object (or any object with a .cli attribute).

    Returns:
        Language code string, defaults to "zh-TW".
    """
    try:
        return config.cli.language
    except AttributeError:
        return "zh-TW"
