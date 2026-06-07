from __future__ import annotations

import asyncio
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from .batch_parser import parse_batch_response
from .batch_planner import TranslationBatch, plan_translation_batches
from .checkpoint import CheckpointManager
from .config import AppConfig
from .epub.reader import SpineDocument, read_epub
from .epub.writer import write_bilingual_epub
from .fallback import ModelFallbackState, should_fallback_from_exception, should_fallback_from_quality
from .i18n import get_cli_language, t
from .logging_setup import get_logger, setup_logging
from .models import JobState, Segment, TranslationRecord
from .prompt import (
    build_batch_system_prompt,
    build_batch_user_message,
    build_system_prompt,
    build_user_message,
)
from .providers.base import (
    AuthError,
    ContextLengthError,
    ProviderError,
    TranslationProvider,
)
from .providers.openai_compatible import OpenAICompatibleProvider
from .renderer import build_bilingual_html, render_bilingual_documents
from .scheduler import TranslationScheduler
from .segmenter.segmenter import segment_all_documents
from .translation_log import TranslationLogWriter
from .validator import (
    classify_failure,
    evaluate_quality_gate,
    quality_failed_segment_ids,
    save_validation_report,
    validate_translations,
)


def _is_cacheable_translation(
    segment: Segment,
    record: TranslationRecord,
    config: AppConfig,
) -> bool:
    if record.status != "completed":
        return False
    report = validate_translations([(segment, record)], config.quality)
    return report.warnings == 0 and report.errors == 0


def _slugify(text: str) -> str:
    text = re.sub(r"[^\w\s-]", "", text).strip()
    return re.sub(r"[\s_-]+", "-", text).lower()[:64] or "book"


def _make_job_id(book_name: str) -> str:
    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return f"{ts}-{book_name}"


def _get_book_name(book: object, input_path: Path) -> str:
    try:
        titles = book.get_metadata("DC", "title")  # type: ignore[attr-defined]
        if titles:
            return _slugify(str(titles[0][0]))
    except Exception:
        pass
    return _slugify(input_path.stem)


def _print_fallback_notice(from_model: str, to_model: str, reason: str, lang: str) -> None:
    print(t("model_fallback", lang, from_model=from_model, to_model=to_model, reason=reason))


def _should_use_batch_mode(
    config: AppConfig,
    *,
    failed_only: bool,
    quality_failed_only: bool,
    force: bool,
    force_segments: set[str] | None,
) -> bool:
    if config.translation.mode != "segment_batch":
        return False
    if failed_only or quality_failed_only or force or force_segments:
        return False
    return True


async def _do_translate(
    seg: Segment,
    attempt: int,
    scheduler: TranslationScheduler,
    system_prompt: str,
    chapter_titles: dict[str, str],
    recent_completed: list[tuple[Segment, TranslationRecord]],
    config: AppConfig,
    lock: asyncio.Lock,
    *,
    fallback_state: ModelFallbackState | None = None,
    translation_log: TranslationLogWriter | None = None,
    book_name: str = "",
    batch_index: int | None = None,
) -> TranslationRecord:
    """Call the provider for one segment and apply the quality gate.

    Returns a TranslationRecord (completed / quality_failed / failed). Never raises;
    does not append to the checkpoint or mutate job state.
    """
    logger = get_logger()
    chapter_title = chapter_titles.get(seg.chapter_href)
    lang = get_cli_language(config)

    async with lock:
        prev_window = list(recent_completed[-(config.context.previous_segments):])

    from .providers.base import TranslationRequest

    user_msg = build_user_message(seg, prev_window, chapter_title, config.context)
    base_request = TranslationRequest(
        segment=seg,
        system_prompt=system_prompt,
        user_message=user_msg,
        model=config.provider.model,
        max_tokens=config.limits.max_output_tokens,
    )

    def _make_record(
        status: str,
        model: str,
        error: str = "",
        translation: str = "",
        *,
        fallback_from: str | None = None,
        fallback_attempt: int | None = None,
        quality_matches: list | None = None,
        quality_warnings: list[str] | None = None,
        quality_errors: list[str] | None = None,
    ) -> TranslationRecord:
        return TranslationRecord(
            segment_id=seg.segment_id,
            source_hash=seg.sha1_prefix,
            status=status,  # type: ignore[arg-type]
            source=seg.source_html,
            translation=translation,
            model=model,
            attempt=attempt,
            error=error or None,
            fallback_from=fallback_from,
            fallback_attempt=fallback_attempt,
            quality_matches=quality_matches,
            quality_warnings=quality_warnings,
            quality_errors=quality_errors,
            created_at=datetime.now(timezone.utc),
        )

    def _log_attempt(
        *,
        model: str,
        status: str,
        error_type: str = "",
        error_message: str = "",
        fallback_from: str = "",
        fallback_to: str = "",
        duration_ms: int | None = None,
        level: str = "INFO",
    ) -> None:
        if translation_log is None:
            return
        translation_log.log(
            segment_id=seg.segment_id,
            batch_index=batch_index,
            model=model,
            status=status,
            error_type=error_type,
            error_message=error_message,
            fallback_from=fallback_from,
            fallback_to=fallback_to,
            attempt=attempt,
            duration_ms=duration_ms,
            level=level,
        )

    use_fallback = fallback_state is not None and fallback_state.enabled
    models_to_try = fallback_state.models_to_try() if use_fallback else [config.provider.model]

    last_status = "failed"
    last_model = models_to_try[-1]
    last_error = ""
    last_translation = ""
    last_quality_matches: list | None = None

    for model_index, model in enumerate(models_to_try):
        request = TranslationRequest(
            segment=base_request.segment,
            system_prompt=base_request.system_prompt,
            user_message=base_request.user_message,
            model=model,
            max_tokens=base_request.max_tokens,
            temperature=base_request.temperature,
        )
        previous_model = models_to_try[model_index - 1] if model_index > 0 else None
        started = time.monotonic()

        try:
            if use_fallback:
                response = await scheduler.translate_once(request)
            else:
                response = await scheduler.translate(request)

            gate = evaluate_quality_gate(seg, response.translated_text, config.quality)
            q_matches = (
                [m for issue in (gate.errors + gate.warnings) for m in issue.matches]
                or None
            )
            if gate.has_errors:
                checks = "; ".join(i.check for i in gate.errors)
                last_status = "quality_failed"
                last_model = response.model
                last_error = checks
                last_translation = response.translated_text
                last_quality_matches = q_matches
                duration_ms = int((time.monotonic() - started) * 1000)
                _log_attempt(
                    model=response.model,
                    status="quality_failed",
                    error_type="quality_failed",
                    error_message=checks,
                    duration_ms=duration_ms,
                    level="WARNING",
                )
                if use_fallback and should_fallback_from_quality(
                    checks, strict_mode=config.quality.strict_mode
                ):
                    next_model = (
                        models_to_try[model_index + 1]
                        if model_index + 1 < len(models_to_try)
                        else None
                    )
                    if next_model:
                        _print_fallback_notice(response.model, next_model, checks, lang)
                        _log_attempt(
                            model=response.model,
                            status="failed",
                            error_type="quality_failed",
                            error_message=checks,
                            fallback_to=next_model,
                            duration_ms=duration_ms,
                            level="WARNING",
                        )
                        continue
                logger.warning("Quality-failed %s: %s", seg.segment_id, checks)
                return _make_record(
                    "quality_failed",
                    response.model,
                    checks,
                    response.translated_text,
                    quality_matches=q_matches,
                    quality_errors=[i.check for i in gate.errors],
                    quality_warnings=[i.check for i in gate.warnings] or None,
                )

            duration_ms = int((time.monotonic() - started) * 1000)
            if use_fallback:
                await fallback_state.set_current(response.model)
            warn_checks = [i.check for i in gate.warnings]
            if warn_checks:
                logger.warning(
                    "Quality warnings %s: %s", seg.segment_id, "; ".join(warn_checks)
                )
            _log_attempt(
                model=response.model,
                status="completed",
                fallback_from=previous_model or "",
                duration_ms=duration_ms,
            )
            logger.info("Translated %s", seg.segment_id)
            return _make_record(
                "completed",
                response.model,
                translation=response.translated_text,
                fallback_from=previous_model,
                fallback_attempt=model_index if model_index > 0 else None,
                quality_matches=q_matches,
                quality_warnings=warn_checks or None,
            )
        except (AuthError, ContextLengthError) as exc:
            duration_ms = int((time.monotonic() - started) * 1000)
            logger.error("Failed %s (fatal): %s", seg.segment_id, exc)
            _log_attempt(
                model=model,
                status="failed",
                error_type=type(exc).__name__,
                error_message=str(exc),
                duration_ms=duration_ms,
                level="ERROR",
            )
            return _make_record("failed", model, str(exc))
        except Exception as exc:
            duration_ms = int((time.monotonic() - started) * 1000)
            last_status = "failed"
            last_model = model
            last_error = str(exc)
            _log_attempt(
                model=model,
                status="failed",
                error_type=type(exc).__name__,
                error_message=str(exc),
                duration_ms=duration_ms,
                level="WARNING",
            )
            if use_fallback and should_fallback_from_exception(exc):
                next_model = (
                    models_to_try[model_index + 1]
                    if model_index + 1 < len(models_to_try)
                    else None
                )
                if next_model:
                    _print_fallback_notice(model, next_model, str(exc), lang)
                    _log_attempt(
                        model=model,
                        status="failed",
                        error_type=type(exc).__name__,
                        error_message=str(exc),
                        fallback_to=next_model,
                        duration_ms=duration_ms,
                        level="WARNING",
                    )
                    continue
            logger.warning("Failed %s: %s", seg.segment_id, exc)
            return _make_record("failed", model, str(exc))

    logger.warning("Failed %s after all models: %s", seg.segment_id, last_error)
    return _make_record(
        last_status,
        last_model,
        last_error,
        last_translation,
        fallback_from=models_to_try[-2] if len(models_to_try) > 1 else None,
        fallback_attempt=len(models_to_try) - 1 if len(models_to_try) > 1 else None,
        quality_matches=last_quality_matches,
    )


def _record_from_gate(
    seg: Segment,
    *,
    attempt: int,
    model: str,
    translation: str,
    gate,
    fallback_from: str | None = None,
    fallback_attempt: int | None = None,
    error: str = "",
) -> TranslationRecord:
    q_matches = (
        [m for issue in (gate.errors + gate.warnings) for m in issue.matches]
        or None
    )
    if gate.has_errors:
        checks = "; ".join(i.check for i in gate.errors)
        return TranslationRecord(
            segment_id=seg.segment_id,
            source_hash=seg.sha1_prefix,
            status="quality_failed",
            source=seg.source_html,
            translation=translation,
            model=model,
            attempt=attempt,
            error=error or checks,
            fallback_from=fallback_from,
            fallback_attempt=fallback_attempt,
            quality_matches=q_matches,
            quality_errors=[i.check for i in gate.errors],
            quality_warnings=[i.check for i in gate.warnings] or None,
            created_at=datetime.now(timezone.utc),
        )
    warn_checks = [i.check for i in gate.warnings]
    return TranslationRecord(
        segment_id=seg.segment_id,
        source_hash=seg.sha1_prefix,
        status="completed",
        source=seg.source_html,
        translation=translation,
        model=model,
        attempt=attempt,
        fallback_from=fallback_from,
        fallback_attempt=fallback_attempt,
        quality_matches=q_matches,
        quality_warnings=warn_checks or None,
        created_at=datetime.now(timezone.utc),
    )


async def _do_translate_batch(
    batch: TranslationBatch,
    attempt_map: dict[str, int],
    scheduler: TranslationScheduler,
    config: AppConfig,
    *,
    fallback_state: ModelFallbackState | None = None,
    translation_log: TranslationLogWriter | None = None,
) -> list[TranslationRecord]:
    """Translate multiple segments in one provider request."""
    from .providers.base import TranslationRequest

    logger = get_logger()
    lang = get_cli_language(config)
    segments = batch.segments
    if not segments:
        return []

    system_prompt = build_batch_system_prompt()
    user_message = build_batch_user_message(segments)
    expected_ids = {seg.segment_id for seg in segments}
    batch_size = len(segments)

    use_fallback = fallback_state is not None and fallback_state.enabled
    models_to_try = fallback_state.models_to_try() if use_fallback else [config.provider.model]

    def _log_seg(
        seg: Segment,
        *,
        model: str,
        status: str,
        attempt: int,
        error_type: str = "",
        error_message: str = "",
        fallback_from: str = "",
        fallback_to: str = "",
        duration_ms: int | None = None,
        batch_parse_error: str = "",
        missing_from_batch_response: str = "",
        level: str = "INFO",
    ) -> None:
        if translation_log is None:
            return
        translation_log.log(
            segment_id=seg.segment_id,
            batch_index=batch.batch_index,
            request_batch_size=batch_size,
            batch_parse_error=batch_parse_error,
            missing_from_batch_response=missing_from_batch_response,
            model=model,
            status=status,
            error_type=error_type,
            error_message=error_message,
            fallback_from=fallback_from,
            fallback_to=fallback_to,
            attempt=attempt,
            duration_ms=duration_ms,
            level=level,
        )

    last_error = ""
    for model_index, model in enumerate(models_to_try):
        previous_model = models_to_try[model_index - 1] if model_index > 0 else None
        request = TranslationRequest(
            segment=segments[0],
            system_prompt=system_prompt,
            user_message=user_message,
            model=model,
            max_tokens=config.limits.max_output_tokens,
        )
        started = time.monotonic()
        try:
            if use_fallback:
                response = await scheduler.translate_once(request)
            else:
                response = await scheduler.translate(request)

            parsed = parse_batch_response(response.translated_text, expected_ids)
            duration_ms = int((time.monotonic() - started) * 1000)

            if parsed.parse_error or parsed.missing_ids:
                reason = parsed.parse_error or "missing segment translations"
                last_error = reason
                for seg in segments:
                    attempt = attempt_map.get(seg.segment_id, 1)
                    _log_seg(
                        seg,
                        model=response.model,
                        status="failed",
                        attempt=attempt,
                        error_type="batch_parse_error",
                        error_message=reason,
                        batch_parse_error=parsed.parse_error or "",
                        missing_from_batch_response=",".join(parsed.missing_ids),
                        duration_ms=duration_ms,
                        level="WARNING",
                    )
                if use_fallback and should_fallback_from_exception(ProviderError(reason)):
                    next_model = (
                        models_to_try[model_index + 1]
                        if model_index + 1 < len(models_to_try)
                        else None
                    )
                    if next_model:
                        _print_fallback_notice(response.model, next_model, reason, lang)
                        continue
                return [
                    TranslationRecord(
                        segment_id=seg.segment_id,
                        source_hash=seg.sha1_prefix,
                        status="failed",
                        source=seg.source_html,
                        translation=parsed.translations.get(seg.segment_id, ""),
                        model=response.model,
                        attempt=attempt_map.get(seg.segment_id, 1),
                        error=reason,
                        created_at=datetime.now(timezone.utc),
                    )
                    for seg in segments
                ]

            records: list[TranslationRecord] = []
            for seg in segments:
                attempt = attempt_map.get(seg.segment_id, 1)
                translation = parsed.translations[seg.segment_id]
                gate = evaluate_quality_gate(seg, translation, config.quality)
                record = _record_from_gate(
                    seg,
                    attempt=attempt,
                    model=response.model,
                    translation=translation,
                    gate=gate,
                    fallback_from=previous_model,
                    fallback_attempt=model_index if model_index > 0 else None,
                )
                records.append(record)
                _log_seg(
                    seg,
                    model=response.model,
                    status=record.status,
                    attempt=attempt,
                    error_type="quality_failed" if record.status == "quality_failed" else "",
                    error_message=record.error or "",
                    fallback_from=previous_model or "",
                    duration_ms=duration_ms,
                    level="WARNING" if record.status == "quality_failed" else "INFO",
                )

            if use_fallback:
                await fallback_state.set_current(response.model)
            return records

        except (AuthError, ContextLengthError) as exc:
            duration_ms = int((time.monotonic() - started) * 1000)
            for seg in segments:
                attempt = attempt_map.get(seg.segment_id, 1)
                _log_seg(
                    seg,
                    model=model,
                    status="failed",
                    attempt=attempt,
                    error_type=type(exc).__name__,
                    error_message=str(exc),
                    duration_ms=duration_ms,
                    level="ERROR",
                )
            return [
                TranslationRecord(
                    segment_id=seg.segment_id,
                    source_hash=seg.sha1_prefix,
                    status="failed",
                    source=seg.source_html,
                    translation="",
                    model=model,
                    attempt=attempt_map.get(seg.segment_id, 1),
                    error=str(exc),
                    created_at=datetime.now(timezone.utc),
                )
                for seg in segments
            ]
        except Exception as exc:
            duration_ms = int((time.monotonic() - started) * 1000)
            last_error = str(exc)
            for seg in segments:
                attempt = attempt_map.get(seg.segment_id, 1)
                _log_seg(
                    seg,
                    model=model,
                    status="failed",
                    attempt=attempt,
                    error_type=type(exc).__name__,
                    error_message=str(exc),
                    duration_ms=duration_ms,
                    level="WARNING",
                )
            if use_fallback and should_fallback_from_exception(exc):
                next_model = (
                    models_to_try[model_index + 1]
                    if model_index + 1 < len(models_to_try)
                    else None
                )
                if next_model:
                    _print_fallback_notice(model, next_model, str(exc), lang)
                    continue
            return [
                TranslationRecord(
                    segment_id=seg.segment_id,
                    source_hash=seg.sha1_prefix,
                    status="failed",
                    source=seg.source_html,
                    translation="",
                    model=model,
                    attempt=attempt_map.get(seg.segment_id, 1),
                    error=str(exc),
                    created_at=datetime.now(timezone.utc),
                )
                for seg in segments
            ]

    logger.warning(
        "Batch %d failed after all models: %s", batch.batch_index, last_error
    )
    return [
        TranslationRecord(
            segment_id=seg.segment_id,
            source_hash=seg.sha1_prefix,
            status="failed",
            source=seg.source_html,
            translation="",
            model=models_to_try[-1],
            attempt=attempt_map.get(seg.segment_id, 1),
            error=last_error or "batch failed",
            created_at=datetime.now(timezone.utc),
        )
        for seg in segments
    ]


async def _translate_batch_group(
    batch: TranslationBatch,
    attempt_map: dict[str, int],
    scheduler: TranslationScheduler,
    checkpoint: CheckpointManager,
    state: JobState,
    config: AppConfig,
    lock: asyncio.Lock,
    cache: dict[str, tuple[str, str, str]],
    in_flight: dict[str, asyncio.Event],
    use_cache: bool,
    recent_completed: list[tuple[Segment, TranslationRecord]],
    *,
    fallback_state: ModelFallbackState | None = None,
    translation_log: TranslationLogWriter | None = None,
) -> list[TranslationRecord]:
    logger = get_logger()
    cached_records: list[TranslationRecord] = []
    api_segments: list[Segment] = []

    for seg in batch.segments:
        sh = seg.sha1_prefix
        async with lock:
            if use_cache and sh in cache:
                src_id, ctext, cmodel = cache[sh]
                record = TranslationRecord(
                    segment_id=seg.segment_id,
                    source_hash=sh,
                    status="completed",
                    source=seg.source_html,
                    translation=ctext,
                    model=cmodel,
                    attempt=attempt_map.get(seg.segment_id, 1),
                    reused_from_segment_id=src_id,
                    created_at=datetime.now(timezone.utc),
                )
                cached_records.append(record)
                logger.info("Cache hit %s (reused from %s)", seg.segment_id, src_id)
                continue
        api_segments.append(seg)

    if api_segments:
        api_batch = TranslationBatch(segments=api_segments, batch_index=batch.batch_index)
        api_records = await _do_translate_batch(
            api_batch,
            attempt_map,
            scheduler,
            config,
            fallback_state=fallback_state,
            translation_log=translation_log,
        )
    else:
        api_records = []

    records = cached_records + api_records
    for record in records:
        checkpoint.append_translation(record)
        seg = next(s for s in batch.segments if s.segment_id == record.segment_id)
        async with lock:
            if record.status == "completed":
                state.completed_segments += 1
                recent_completed.append((seg, record))
                if use_cache and _is_cacheable_translation(seg, record, config):
                    cache.setdefault(
                        record.source_hash,
                        (record.segment_id, record.translation, record.model),
                    )
            else:
                state.failed_segments += 1
            state.updated_at = datetime.now(timezone.utc)
        checkpoint.save_state(state)

    return records


async def _translate_one(
    seg: Segment,
    attempt: int,
    scheduler: TranslationScheduler,
    checkpoint: CheckpointManager,
    state: JobState,
    system_prompt: str,
    chapter_titles: dict[str, str],
    recent_completed: list[tuple[Segment, TranslationRecord]],
    config: AppConfig,
    lock: asyncio.Lock,
    cache: dict[str, tuple[str, str, str]],
    in_flight: dict[str, asyncio.Event],
    use_cache: bool,
    *,
    fallback_state: ModelFallbackState | None = None,
    translation_log: TranslationLogWriter | None = None,
    book_name: str = "",
    batch_index: int | None = None,
) -> TranslationRecord:
    logger = get_logger()
    sh = seg.sha1_prefix  # source_hash cache key (hash of normalized source text)
    record: TranslationRecord | None = None

    while record is None:
        wait_event: asyncio.Event | None = None
        become_owner = False

        async with lock:
            if use_cache and sh in cache:
                src_id, ctext, cmodel = cache[sh]
                record = TranslationRecord(
                    segment_id=seg.segment_id,
                    source_hash=sh,
                    status="completed",
                    source=seg.source_html,
                    translation=ctext,
                    model=cmodel,
                    attempt=attempt,
                    reused_from_segment_id=src_id,
                    created_at=datetime.now(timezone.utc),
                )
                logger.info("Cache hit %s (reused from %s)", seg.segment_id, src_id)
                break
            if use_cache:
                ev = in_flight.get(sh)
                if ev is None:
                    in_flight[sh] = asyncio.Event()
                    become_owner = True
                else:
                    wait_event = ev
            else:
                become_owner = True

        if wait_event is not None:
            # Another task is translating this source_hash; wait, then re-loop.
            await wait_event.wait()
            continue

        # We own this source_hash (or caching is off): do the real translation.
        translated: TranslationRecord | None = None
        try:
            translated = await _do_translate(
                seg,
                attempt,
                scheduler,
                system_prompt,
                chapter_titles,
                recent_completed,
                config,
                lock,
                fallback_state=fallback_state,
                translation_log=translation_log,
                book_name=book_name,
                batch_index=batch_index,
            )
        finally:
            if use_cache:
                async with lock:
                    if translated is not None and _is_cacheable_translation(seg, translated, config):
                        cache.setdefault(sh, (seg.segment_id, translated.translation, translated.model))
                    ev = in_flight.pop(sh, None)
                    if ev is not None:
                        ev.set()
        record = translated

    checkpoint.append_translation(record)

    async with lock:
        if record.status == "completed":
            state.completed_segments += 1
            recent_completed.append((seg, record))
        else:
            state.failed_segments += 1
        state.updated_at = datetime.now(timezone.utc)
    checkpoint.save_state(state)

    return record


async def run_translation(
    config: AppConfig,
    *,
    provider: TranslationProvider | None = None,
    limit: int | None = None,
    failed_only: bool = False,
    quality_failed_only: bool = False,
    force: bool = False,
    force_segments: set[str] | None = None,
    skip_final_export: bool = False,
    batch_index: int | None = None,
) -> None:
    logger = get_logger()

    book, spine_docs = read_epub(config.input.path)
    book_name = _get_book_name(book, config.input.path)
    output_dir = config.project.output_dir / book_name
    setup_logging(output_dir, config.logging.level)
    translation_log = TranslationLogWriter.from_config(
        config.logging,
        book_name=book_name,
        output_dir=output_dir,
    )
    fallback_state = ModelFallbackState(
        config.provider.model,
        config.provider.fallback_models,
    )
    logger = get_logger()

    logger.info("Starting translation job for '%s'", book_name)
    logger.info("Input: %s", config.input.path)
    logger.info("Output: %s", output_dir)

    checkpoint = CheckpointManager(output_dir)
    state = checkpoint.load_state()
    completed_ids = checkpoint.load_completed_ids()

    all_segments = segment_all_documents(spine_docs)
    logger.info("Total segments: %d", len(all_segments))

    if state is None:
        state = JobState(
            job_id=_make_job_id(book_name),
            input_path=str(config.input.path),
            output_dir=str(output_dir),
            status="running",
            total_segments=len(all_segments),
            completed_segments=len(completed_ids),
            failed_segments=0,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        checkpoint.save_segments(all_segments)
    else:
        logger.info("Resuming job %s (%d already completed)", state.job_id, len(completed_ids))
        state.status = "running"
        state.total_segments = len(all_segments)
        state.completed_segments = len(completed_ids)

    failed_ids = checkpoint.load_failed_ids()

    if force:
        # Re-translate every segment, ignoring completed state. Appends new records.
        pending = list(all_segments)
        logger.info("Force mode: re-translating all %d segments", len(pending))
    elif force_segments:
        pending = [seg for seg in all_segments if seg.segment_id in force_segments]
        unknown = force_segments - {seg.segment_id for seg in all_segments}
        if unknown:
            logger.warning("Force-segment ids not found and skipped: %s", ", ".join(sorted(unknown)))
        logger.info("Force-segment mode: re-translating %d segment(s)", len(pending))
    elif quality_failed_only:
        # Retry only completed segments whose latest record fails a quality check.
        all_tr = checkpoint.load_all_translations()
        pairs = [
            (seg, all_tr[seg.segment_id])
            for seg in all_segments
            if seg.segment_id in all_tr
        ]
        qf_ids = quality_failed_segment_ids(pairs, config.quality)
        pending = [seg for seg in all_segments if seg.segment_id in qf_ids]
        logger.info("Quality-failed retry. Segments with quality issues: %d", len(pending))
    elif failed_only:
        # Retry only segments that have a failed record and are not yet completed.
        pending = [
            seg for seg in all_segments
            if seg.segment_id in failed_ids and seg.segment_id not in completed_ids
        ]
        categories: dict[str, int] = {}
        all_tr = checkpoint.load_all_translations()
        for seg in pending:
            rec = all_tr.get(seg.segment_id)
            cat = classify_failure(rec.error if rec else None)
            categories[cat] = categories.get(cat, 0) + 1
        logger.info("Failed-only retry. Failure categories: %s", dict(categories))
    else:
        retry_failed = config.resume.retry_failed
        pending = [
            seg for seg in all_segments
            if seg.segment_id not in completed_ids
            and (retry_failed or seg.segment_id not in failed_ids)
        ]

    if limit is not None:
        pending = pending[:limit]
        logger.info("Limiting this run to %d segments", limit)
    logger.info("Segments to translate: %d", len(pending))

    checkpoint.save_state(state)

    if provider is None:
        provider = OpenAICompatibleProvider(config.provider)
    scheduler = TranslationScheduler(
        provider, config.limits, max_retries=config.resume.max_retries
    )

    # Build chapter title map
    chapter_titles: dict[str, str] = {}
    for doc in spine_docs:
        from bs4 import BeautifulSoup, Tag
        soup = BeautifulSoup(doc.content, "lxml")
        title_tag = soup.find(["h1", "h2", "h3"])
        if isinstance(title_tag, Tag):
            chapter_titles[doc.href] = title_tag.get_text(strip=True)

    system_prompt = build_system_prompt()
    recent_completed: list[tuple[Segment, TranslationRecord]] = []
    lock = asyncio.Lock()

    # Next attempt number per segment = latest record's attempt + 1 (covers both
    # failed retries and quality retries of already-completed segments).
    existing_records = checkpoint.load_all_translations()
    attempt_map = {sid: rec.attempt + 1 for sid, rec in existing_records.items()}

    # Translation cache keyed by source_hash. Disabled for force / force-segment
    # runs so those genuinely re-translate. Seeded only from clean completed
    # records (never failed or quality_failed).
    use_cache = not (force or force_segments)
    cache: dict[str, tuple[str, str, str]] = {}
    in_flight: dict[str, asyncio.Event] = {}
    if use_cache:
        seg_by_id = {seg.segment_id: seg for seg in all_segments}
        for sid, rec in existing_records.items():
            seg = seg_by_id.get(sid)
            if seg is not None and _is_cacheable_translation(seg, rec, config):
                cache.setdefault(rec.source_hash, (rec.segment_id, rec.translation, rec.model))
        logger.info("Cache seeded with %d clean translation(s)", len(cache))

    use_batch = _should_use_batch_mode(
        config,
        failed_only=failed_only,
        quality_failed_only=quality_failed_only,
        force=force,
        force_segments=force_segments,
    )

    if use_batch:
        batches = plan_translation_batches(
            pending,
            segments_per_request=config.translation.segments_per_request,
            max_chars_per_request=config.translation.max_chars_per_request,
            preserve_segment_order=config.translation.preserve_segment_order,
        )
        logger.info(
            "Batch mode: %d segments in %d request(s)",
            len(pending),
            len(batches),
        )
        tasks = [
            _translate_batch_group(
                batch=batch,
                attempt_map=attempt_map,
                scheduler=scheduler,
                checkpoint=checkpoint,
                state=state,
                config=config,
                lock=lock,
                cache=cache,
                in_flight=in_flight,
                use_cache=use_cache,
                recent_completed=recent_completed,
                fallback_state=fallback_state,
                translation_log=translation_log,
            )
            for batch in batches
        ]
    else:
        tasks = [
            _translate_one(
                seg=seg,
                attempt=attempt_map.get(seg.segment_id, 1),
                scheduler=scheduler,
                checkpoint=checkpoint,
                state=state,
                system_prompt=system_prompt,
                chapter_titles=chapter_titles,
                recent_completed=recent_completed,
                config=config,
                lock=lock,
                cache=cache,
                in_flight=in_flight,
                use_cache=use_cache,
                fallback_state=fallback_state,
                translation_log=translation_log,
                book_name=book_name,
                batch_index=batch_index,
            )
            for seg in pending
        ]

    try:
        results = await asyncio.gather(*tasks, return_exceptions=True)
    except KeyboardInterrupt:
        state.status = "interrupted"
        checkpoint.save_state(state)
        logger.warning("Interrupted. Progress saved.")
        await provider.close()
        return

    await provider.close()

    # Load all completed translations for rendering + validation
    all_translations = checkpoint.load_all_translations()

    completed_pairs = [
        (seg, all_translations[seg.segment_id])
        for seg in all_segments
        if seg.segment_id in all_translations
    ]

    if not skip_final_export:
        logger.info("Running validation...")
        report = validate_translations(completed_pairs, config.quality)
        save_validation_report(report, output_dir)
        logger.info(
            "Validation: %d checked, %d warnings, %d errors",
            report.total_checked, report.warnings, report.errors,
        )

        logger.info("Rendering bilingual HTML...")
        rendered = render_bilingual_documents(
            spine_docs, all_segments, all_translations, output_config=config.output
        )

        epub_out = output_dir / "translated.epub"
        write_bilingual_epub(book, rendered, epub_out)
        logger.info("Exported bilingual EPUB: %s", epub_out)

        bilingual_html = build_bilingual_html(spine_docs, rendered, output_config=config.output)
        html_out = output_dir / "bilingual.html"
        html_out.write_text(bilingual_html, encoding="utf-8")
        logger.info("Exported bilingual HTML: %s", html_out)

    completed_final = checkpoint.load_completed_ids()
    # A segment with a failed record that was later completed is no longer failed.
    failed_final = {
        sid for sid in checkpoint.load_failed_ids() if sid not in completed_final
    }
    not_done = sum(1 for seg in all_segments if seg.segment_id not in completed_final)
    state.status = "completed" if not_done == 0 else "interrupted"
    state.completed_segments = len(completed_final)
    state.failed_segments = len(failed_final)
    state.updated_at = datetime.now(timezone.utc)
    checkpoint.save_state(state)

    logger.info(
        "Done. Status: %s | Completed: %d/%d | Failed: %d",
        state.status, state.completed_segments, state.total_segments, state.failed_segments,
    )


def run_validate(output_dir: Path, config: AppConfig | None = None) -> None:
    from .config import QualityConfig
    from .i18n import t, get_cli_language

    checkpoint = CheckpointManager(output_dir)
    segments = checkpoint.load_segments()
    translations = checkpoint.load_all_translations()

    if not segments:
        lang = get_cli_language(config) if config else "zh-TW"
        print(t("no_segments_found", lang), file=sys.stderr)
        return

    pairs = [
        (seg, translations[seg.segment_id])
        for seg in segments
        if seg.segment_id in translations
    ]

    quality_cfg = config.quality if config else QualityConfig()
    report = validate_translations(pairs, quality_cfg)
    save_validation_report(report, output_dir)

    lang = get_cli_language(config) if config else "zh-TW"
    print(
        f"{t('validation', lang)}: {report.total_checked} {t('checked', lang)}, "
        f"{report.warnings} {t('validation_warnings', lang)}, "
        f"{report.errors} {t('validation_errors', lang)}"
    )
    print(f"{t('report_saved', lang)} {output_dir / 'validation_report.json'}")


def run_export(output_dir: Path, config: AppConfig) -> None:
    from .i18n import t, get_cli_language

    checkpoint = CheckpointManager(output_dir)
    state = checkpoint.load_state()
    if not state:
        print(t("no_job_state"), file=sys.stderr)
        return

    lang = get_cli_language(config)

    spine_docs: list[SpineDocument]
    book, spine_docs = read_epub(Path(state.input_path))
    segments = checkpoint.load_segments()
    translations = checkpoint.load_all_translations()

    rendered = render_bilingual_documents(
        spine_docs, segments, translations, output_config=config.output
    )

    epub_out = output_dir / "translated.epub"
    write_bilingual_epub(book, rendered, epub_out)
    print(f"{t('exported', lang)}: {epub_out}")

    bilingual_html = build_bilingual_html(spine_docs, rendered, output_config=config.output)
    html_out = output_dir / "bilingual.html"
    html_out.write_text(bilingual_html, encoding="utf-8")
    print(f"{t('exported', lang)}: {html_out}")


def run_inspect(config: AppConfig) -> None:
    """Read the EPUB and report spine + segment statistics without translating."""
    from .i18n import t, get_cli_language

    book, spine_docs = read_epub(config.input.path)
    book_name = _get_book_name(book, config.input.path)
    lang = get_cli_language(config)

    print(f"{t('book_name', lang):<15s} {book_name}")
    print(f"{t('input_path', lang):<15s} {config.input.path}")
    print(f"{t('spine_docs', lang):<15s} {len(spine_docs)}")

    total_segments = 0
    for doc in spine_docs:
        segs = segment_all_documents([doc])
        total_segments += len(segs)
        print(f"  [{doc.chapter_index:04d}] {doc.href}: {len(segs)} {t('segments', lang)}")

    print(f"{t('total_segments', lang)}: {total_segments}")


def run_report_missing(job_dir: Path, epub_path: Path, config: AppConfig | None = None) -> None:
    """Generate missing_translation_report.json for an existing job.

    Reads the EPUB at *epub_path*, cross-references every translatable block
    against the segments.jsonl / translations.jsonl stored in *job_dir*, and
    writes ``missing_translation_report.json`` to *job_dir*.

    Prints a human-readable summary to stdout.  Never calls any provider.
    """
    from .i18n import t, get_cli_language
    from .missing_report import build_missing_translation_report, save_missing_translation_report

    checkpoint = CheckpointManager(job_dir)
    segments = checkpoint.load_segments()
    translations = checkpoint.load_all_translations()

    _, spine_docs = read_epub(epub_path)

    report = build_missing_translation_report(spine_docs, segments, translations)
    out_path = save_missing_translation_report(report, job_dir)

    # --- count translatable blocks in the original EPUB ---
    from .segmenter.segmenter import iter_translatable_blocks
    from bs4 import BeautifulSoup
    total_blocks = 0
    for doc in spine_docs:
        soup = BeautifulSoup(doc.content, "lxml")
        total_blocks += sum(1 for _ in iter_translatable_blocks(soup))

    missing_count = len(report)

    # tally reasons
    reason_counts: dict[str, int] = {}
    for entry in report:
        r = entry["reason"]
        reason_counts[r] = reason_counts.get(r, 0) + 1

    # --- console output ---
    lang = get_cli_language(config) if config else "zh-TW"
    print(f"{t('missing_translation_report', lang)}: {out_path}")
    print(f"  {t('total_checked_blocks', lang)} : {total_blocks}")
    print(f"  {t('missing_count', lang)}        : {missing_count}")
    if reason_counts:
        print(f"  {t('breakdown_by_reason', lang)}:")
        for reason, count in sorted(reason_counts.items()):
            print(f"    {reason:<30s}: {count}")
    else:
        print(f"  ({t('all_blocks_translated', lang)})")

