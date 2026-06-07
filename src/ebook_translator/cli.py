from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

import typer
from dotenv import load_dotenv

app = typer.Typer(
    name="ebook-translator",
    help="Bilingual EPUB translation CLI",
    add_completion=False,
)


def _load_config_or_exit(config: Path, *, require_api_key: bool = True):
    from .config import load_config

    if not require_api_key:
        # For --mock runs we don't need a real key; load YAML without the key check.
        import yaml
        from .config import AppConfig

        try:
            data = yaml.safe_load(config.read_text(encoding="utf-8"))
            return AppConfig.model_validate(data)
        except Exception as exc:
            typer.echo(f"Error loading config: {exc}", err=True)
            raise typer.Exit(1)

    try:
        return load_config(config)
    except Exception as exc:
        typer.echo(f"Error loading config: {exc}", err=True)
        raise typer.Exit(1)


def _collect_start_books(books_dir: Path, book: Path | None) -> list[Path]:
    if book is not None:
        candidates = [book]
        if not book.is_absolute() and not book.exists():
            candidates.append(books_dir / book)
        for candidate in candidates:
            if candidate.exists() and candidate.suffix.lower() == ".epub":
                return [candidate]
        raise FileNotFoundError(f"EPUB not found: {book}")

    if not books_dir.exists():
        return []
    return sorted(
        path for path in books_dir.iterdir()
        if path.is_file() and path.suffix.lower() == ".epub"
    )


def _quality_failed_ids(job_dir: Path, cfg) -> set[str]:
    from .checkpoint import CheckpointManager
    from .validator import quality_failed_segment_ids

    checkpoint = CheckpointManager(job_dir)
    segments = checkpoint.load_segments()
    translations = checkpoint.load_all_translations()
    pairs = [
        (seg, translations[seg.segment_id])
        for seg in segments
        if seg.segment_id in translations
    ]
    return quality_failed_segment_ids(pairs, cfg.quality)


def _print_safe_mode_plan(
    *,
    lang: str,
    segment_count: int,
    effective_segments: int | None,
    batch_size: int,
    cooldown_seconds: int,
    stop_on_rate_limit_count: int,
    stop_on_no_progress_count: int,
) -> None:
    from .i18n import t
    from .safe_mode import estimate_batch_count

    typer.echo(t("safe_mode_plan_title", lang))
    typer.echo(f"  {t('safe_mode_total_segments', lang)}: {segment_count}")
    if effective_segments is None:
        typer.echo(f"  {t('safe_mode_effective_segments', lang)}: 0 (skipped)")
    else:
        typer.echo(f"  {t('safe_mode_effective_segments', lang)}: {effective_segments}")
        typer.echo(
            f"  {t('safe_mode_estimated_batches', lang)}: "
            f"{estimate_batch_count(effective_segments, batch_size)}"
        )
    typer.echo(f"  {t('safe_mode_batch_size', lang)}: {batch_size}")
    typer.echo(f"  {t('safe_mode_cooldown_seconds', lang)}: {cooldown_seconds}")
    threshold = stop_on_rate_limit_count if stop_on_rate_limit_count > 0 else "disabled"
    typer.echo(f"  {t('safe_mode_stop_on_rate_limit', lang)}: {threshold}")
    np_threshold = stop_on_no_progress_count if stop_on_no_progress_count > 0 else "disabled"
    typer.echo(f"  {t('safe_mode_stop_on_no_progress', lang)}: {np_threshold}")


def _run_post_batch_exports_only(job_dir: Path, cfg) -> None:
    from .translator import run_export, run_report_missing, run_validate

    run_validate(job_dir, cfg)
    run_report_missing(job_dir, cfg.input.path, cfg)
    run_export(job_dir, cfg)


def _run_post_batch_workflow(
    job_dir: Path,
    cfg,
    lang: str,
    *,
    limit: int | None,
) -> None:
    """Retry failures, validate, report-missing, export after one batch."""
    from .checkpoint import CheckpointManager
    from .i18n import t
    from .translator import run_export, run_report_missing, run_translation, run_validate

    checkpoint = CheckpointManager(job_dir)

    failed_ids = checkpoint.load_failed_ids()
    if failed_ids:
        typer.echo(f"{t('retrying_failed', lang)}: {len(failed_ids)}")
        asyncio.run(run_translation(cfg, limit=limit, failed_only=True, skip_final_export=True))

    qf_ids = _quality_failed_ids(job_dir, cfg)
    if qf_ids:
        typer.echo(f"{t('retrying_quality_failed', lang)}: {len(qf_ids)}")
        asyncio.run(run_translation(cfg, limit=limit, quality_failed_only=True, skip_final_export=True))

    run_validate(job_dir, cfg)
    run_report_missing(job_dir, cfg.input.path, cfg)
    run_export(job_dir, cfg)


def _run_safe_mode_translation(
    cfg,
    job_dir: Path,
    lang: str,
    *,
    segment_count: int,
    batch_size: int,
    cooldown_seconds: int,
    stop_on_rate_limit_count: int,
    stop_on_no_progress_count: int,
    limit: int | None,
) -> None:
    from .i18n import t
    from .safe_mode import (
        batch_limit_for_run,
        count_rate_limit_errors_since,
        pending_segment_count,
        translation_record_line_count,
    )
    from .translator import run_translation

    remaining_limit = limit
    session_rate_limit_count = 0
    no_progress_count = 0
    batch_num = 0

    while True:
        pending = pending_segment_count(job_dir, cfg, segment_count)
        if pending <= 0:
            break

        batch_limit = batch_limit_for_run(batch_size, remaining_limit)
        if batch_limit is None or batch_limit <= 0:
            break

        batch_num += 1
        typer.echo(t("safe_mode_batch_start", lang, batch=batch_num, limit=batch_limit))

        from .checkpoint import CheckpointManager

        checkpoint = CheckpointManager(job_dir)
        completed_before = len(checkpoint.load_completed_ids())
        lines_before = translation_record_line_count(job_dir)
        asyncio.run(
            run_translation(cfg, limit=batch_limit, skip_final_export=True, batch_index=batch_num)
        )
        batch_rate_limits = count_rate_limit_errors_since(job_dir, lines_before)
        session_rate_limit_count += batch_rate_limits
        completed_after = len(checkpoint.load_completed_ids())

        # Track no-progress batches (no new completions).
        if completed_after > completed_before:
            no_progress_count = 0
        else:
            no_progress_count += 1

        rate_limit_stop = (
            stop_on_rate_limit_count > 0
            and session_rate_limit_count >= stop_on_rate_limit_count
        )
        if rate_limit_stop or batch_rate_limits > 0:
            _run_post_batch_exports_only(job_dir, cfg)
        else:
            _run_post_batch_workflow(job_dir, cfg, lang, limit=limit)
        typer.echo(t("safe_mode_batch_done", lang, batch=batch_num))

        if remaining_limit is not None:
            remaining_limit = max(0, remaining_limit - (completed_after - completed_before))

        if rate_limit_stop:
            typer.echo(
                t(
                    "safe_mode_stopped_rate_limit",
                    lang,
                    count=session_rate_limit_count,
                    threshold=stop_on_rate_limit_count,
                ),
                err=True,
            )
            break

        if stop_on_no_progress_count > 0 and no_progress_count >= stop_on_no_progress_count:
            typer.echo(
                t(
                    "safe_mode_stopped_no_progress",
                    lang,
                    count=no_progress_count,
                    threshold=stop_on_no_progress_count,
                ),
                err=True,
            )
            break

        pending = pending_segment_count(job_dir, cfg, segment_count)
        if pending <= 0:
            break
        if batch_limit_for_run(batch_size, remaining_limit) is None:
            break

        if cooldown_seconds > 0:
            typer.echo(t("safe_mode_cooldown", lang, seconds=cooldown_seconds))
            time.sleep(cooldown_seconds)


@app.command()
def translate(
    config: Path = typer.Option(..., "--config", "-c", help="Path to config YAML file"),
    mock: bool = typer.Option(False, "--mock", help="Use offline mock provider (no API calls)"),
    limit: int | None = typer.Option(None, "--limit", help="Translate at most N segments this run"),
    force: bool = typer.Option(False, "--force", help="Re-translate ALL segments, ignoring completed"),
    force_segment: list[str] | None = typer.Option(
        None,
        "--force-segment",
        help="Re-translate only these segment ids (repeatable or comma-separated)",
    ),
) -> None:
    """Translate an EPUB using a config file."""
    load_dotenv(encoding="utf-8-sig")  # tolerate a UTF-8 BOM in .env
    from .logging_setup import setup_logging
    from .translator import run_translation

    cfg = _load_config_or_exit(config, require_api_key=not mock)

    # Collect force-segment ids (repeated flags and/or comma-separated values).
    force_ids: set[str] = set()
    for item in force_segment or []:
        force_ids.update(s.strip() for s in item.split(",") if s.strip())

    # Temporary logger until output dir is known (translator re-initializes)
    import tempfile
    from pathlib import Path as _P
    _tmp = _P(tempfile.gettempdir()) / "ebook-translator-startup"
    setup_logging(_tmp)

    provider = None
    if mock:
        from .providers.mock import MockProvider
        provider = MockProvider()

    try:
        asyncio.run(run_translation(
            cfg,
            provider=provider,
            limit=limit,
            force=force,
            force_segments=force_ids or None,
        ))
    except KeyboardInterrupt:
        typer.echo("\nInterrupted.", err=True)
        raise typer.Exit(130)
    except Exception as exc:
        typer.echo(f"Translation failed: {exc}", err=True)
        raise typer.Exit(1)


@app.command()
def inspect(
    config: Path = typer.Option(..., "--config", "-c", help="Path to config YAML file"),
) -> None:
    """Read the EPUB and report spine + segment statistics (no translation)."""
    from .translator import run_inspect

    cfg = _load_config_or_exit(config, require_api_key=False)
    try:
        run_inspect(cfg)
    except Exception as exc:
        typer.echo(f"Inspect failed: {exc}", err=True)
        raise typer.Exit(1)


@app.command()
def estimate(
    config: Path = typer.Option(..., "--config", "-c", help="Path to config YAML file"),
) -> None:
    """Estimate translation cost, requests, tokens, and runtime (no API calls)."""
    from .estimate import run_estimate

    # Estimation never calls a provider, so no API key is required.
    cfg = _load_config_or_exit(config, require_api_key=False)
    try:
        run_estimate(cfg)
    except Exception as exc:
        typer.echo(f"Estimate failed: {exc}", err=True)
        raise typer.Exit(1)


@app.command()
def start(
    config: Path = typer.Option(Path("config.yaml"), "--config", "-c", help="Path to config YAML file"),
    books_dir: Path = typer.Option(Path("input"), "--books-dir", help="Directory containing EPUB files (default: input/)"),
    book: Path | None = typer.Option(None, "--book", help="Translate only this EPUB file"),
    limit: int | None = typer.Option(None, "--limit", help="Translate at most N pending segments per step"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Only run inspect + estimate; no API calls"),
    yes: bool = typer.Option(False, "--yes", help="Skip confirmation and start translating"),
    max_segments: int = typer.Option(300, "--max-segments", help="Stop a book if segment count exceeds this"),
    skip_confirm: bool = typer.Option(False, "--skip-confirm", help="Alias for --yes"),
    batch_size: int | None = typer.Option(
        None,
        "--batch-size",
        help="Process at most N pending segments per batch (large-book safe mode)",
    ),
    cooldown_seconds: int = typer.Option(
        0,
        "--cooldown-seconds",
        help="Wait N seconds between batches (0 = no wait)",
    ),
    stop_on_rate_limit_count: int = typer.Option(
        0,
        "--stop-on-rate-limit-count",
        help="Stop after N rate-limit errors in this run (0 = disabled)",
    ),
    stop_on_no_progress_count: int = typer.Option(
        2,
        "--stop-on-no-progress-count",
        help="Stop safe mode after N consecutive batches with no new completions (0 = disabled)",
    ),
) -> None:
    """Run the full translation workflow for EPUBs in input/."""
    load_dotenv(encoding="utf-8-sig")
    from .checkpoint import CheckpointManager
    from .estimate import run_estimate
    from .i18n import t, get_cli_language
    from .safe_mode import compute_effective_segments
    from .translator import (
        run_export,
        run_inspect,
        run_report_missing,
        run_translation,
        run_validate,
    )

    # Auto-create the books directory when it does not exist and no explicit
    # --book path was given. Show a friendly message instead of an error.
    if not books_dir.exists() and book is None:
        books_dir.mkdir(parents=True, exist_ok=True)
        typer.echo(
            f"已建立 {books_dir}/ 資料夾。請把 .epub 檔案放入後重新執行。\n"
            f"  Folder created: {books_dir}/\n"
            f"  Drop your .epub files there, then run again.",
        )
        raise typer.Exit(0)

    try:
        books = _collect_start_books(books_dir, book)
    except Exception as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1)

    if not books:
        typer.echo(
            f"No EPUB files found in {books_dir}/.\n"
            f"  Drop .epub files there, or use --book <path>, or --books-dir <dir>.",
            err=True,
        )
        raise typer.Exit(1)

    cfg_base = _load_config_or_exit(config, require_api_key=not dry_run)
    auto_yes = yes or skip_confirm
    lang = get_cli_language(cfg_base)

    for epub_path in books:
        cfg = cfg_base.model_copy(deep=True)
        cfg.input.path = epub_path

        typer.echo("=" * 60)
        typer.echo(f"{t('book', lang)}: {epub_path}")
        typer.echo("=" * 60)

        run_inspect(cfg)
        estimate_report = run_estimate(cfg)
        segment_count = int(estimate_report["segments"]["count"])
        book_name = estimate_report["book"]["book_name"]
        job_dir = cfg.project.output_dir / book_name

        if segment_count > max_segments:
            typer.echo(
                f"{t('skipping', lang)} {epub_path}: segment count {segment_count} exceeds --max-segments {max_segments}.",
                err=True,
            )
            continue

        effective_segments = compute_effective_segments(segment_count, max_segments, limit)
        safe_mode = batch_size is not None and batch_size > 0

        if dry_run:
            if safe_mode:
                _print_safe_mode_plan(
                    lang=lang,
                    segment_count=segment_count,
                    effective_segments=effective_segments,
                    batch_size=batch_size,
                    cooldown_seconds=cooldown_seconds,
                    stop_on_rate_limit_count=stop_on_rate_limit_count,
                    stop_on_no_progress_count=stop_on_no_progress_count,
                )
            typer.echo(t("dry_run_complete", lang))
            continue

        if not auto_yes:
            tokens = estimate_report["tokens"]["estimated_total_tokens"]
            requests = estimate_report["requests"]["estimated_requests_with_retry"]
            runtime = estimate_report["runtime"]["minimum_minutes_with_retry"]
            typer.echo(
                f"{t('estimate_summary', lang)}: segments={segment_count}, "
                f"estimated_total_tokens={tokens}, "
                f"estimated_requests_with_retry={requests}, "
                f"estimated_runtime_with_retry={runtime} min"
            )
            if not typer.confirm(t("start_translating", lang), default=False):
                typer.echo(f"{t('skipped', lang)} {epub_path}")
                continue

        if safe_mode:
            _run_safe_mode_translation(
                cfg,
                job_dir,
                lang,
                segment_count=segment_count,
                batch_size=batch_size,
                cooldown_seconds=cooldown_seconds,
                stop_on_rate_limit_count=stop_on_rate_limit_count,
                stop_on_no_progress_count=stop_on_no_progress_count,
                limit=limit,
            )
        else:
            asyncio.run(run_translation(cfg, limit=limit))

            checkpoint = CheckpointManager(job_dir)
            failed_ids = checkpoint.load_failed_ids()
            if failed_ids:
                typer.echo(f"{t('retrying_failed', lang)}: {len(failed_ids)}")
                asyncio.run(run_translation(cfg, limit=limit, failed_only=True))

            qf_ids = _quality_failed_ids(job_dir, cfg)
            if qf_ids:
                typer.echo(f"{t('retrying_quality_failed', lang)}: {len(qf_ids)}")
                asyncio.run(run_translation(cfg, limit=limit, quality_failed_only=True))

            run_validate(job_dir, cfg)
            run_report_missing(job_dir, cfg.input.path, cfg)
            run_export(job_dir, cfg)


@app.command()
def resume(
    job: Path = typer.Argument(..., help="Path to job output directory (contains state.json)"),
    config: Path = typer.Option(..., "--config", "-c", help="Path to config YAML file"),
) -> None:
    """Resume a previously interrupted translation job."""
    load_dotenv(encoding="utf-8-sig")  # tolerate a UTF-8 BOM in .env
    from .config import load_config
    from .logging_setup import setup_logging
    from .translator import run_translation

    if not (job / "state.json").exists():
        typer.echo(f"No state.json found in {job}", err=True)
        raise typer.Exit(1)

    try:
        cfg = load_config(config)
    except Exception as exc:
        typer.echo(f"Error loading config: {exc}", err=True)
        raise typer.Exit(1)

    # Override output dir so the pipeline resumes into the same directory
    import json
    state_data = json.loads((job / "state.json").read_text(encoding="utf-8"))
    cfg.project.output_dir = job.parent
    cfg.input.path = Path(state_data["input_path"])

    setup_logging(job)

    try:
        asyncio.run(run_translation(cfg))
    except KeyboardInterrupt:
        typer.echo("\nInterrupted.", err=True)
        raise typer.Exit(130)
    except Exception as exc:
        typer.echo(f"Resume failed: {exc}", err=True)
        raise typer.Exit(1)


@app.command(name="retry-failed")
def retry_failed(
    job: Path = typer.Argument(..., help="Path to job output directory (contains state.json)"),
    config: Path = typer.Option(..., "--config", "-c", help="Path to config YAML file"),
    limit: int | None = typer.Option(None, "--limit", help="Retry at most N failed segments"),
) -> None:
    """Retry only the segments that previously failed (never re-translates completed ones)."""
    load_dotenv(encoding="utf-8-sig")  # tolerate a UTF-8 BOM in .env
    from .config import load_config
    from .logging_setup import setup_logging
    from .translator import run_translation

    if not (job / "state.json").exists():
        typer.echo(f"No state.json found in {job}", err=True)
        raise typer.Exit(1)

    try:
        cfg = load_config(config)
    except Exception as exc:
        typer.echo(f"Error loading config: {exc}", err=True)
        raise typer.Exit(1)

    import json
    state_data = json.loads((job / "state.json").read_text(encoding="utf-8"))
    cfg.project.output_dir = job.parent
    cfg.input.path = Path(state_data["input_path"])

    setup_logging(job)

    try:
        asyncio.run(run_translation(cfg, limit=limit, failed_only=True))
    except KeyboardInterrupt:
        typer.echo("\nInterrupted.", err=True)
        raise typer.Exit(130)
    except Exception as exc:
        typer.echo(f"Retry failed: {exc}", err=True)
        raise typer.Exit(1)


@app.command(name="retry-quality-failed")
def retry_quality_failed(
    job: Path = typer.Argument(..., help="Path to job output directory (contains state.json)"),
    config: Path = typer.Option(..., "--config", "-c", help="Path to config YAML file"),
    limit: int | None = typer.Option(None, "--limit", help="Retry at most N quality-failed segments"),
) -> None:
    """Re-translate completed segments that fail a quality check (simplified Chinese,
    added prefix, markdown fence, explanation prefix). Never touches clean completed
    segments or hard-failed segments (use retry-failed for those)."""
    load_dotenv(encoding="utf-8-sig")  # tolerate a UTF-8 BOM in .env
    from .config import load_config
    from .logging_setup import setup_logging
    from .translator import run_translation

    if not (job / "state.json").exists():
        typer.echo(f"No state.json found in {job}", err=True)
        raise typer.Exit(1)

    try:
        cfg = load_config(config)
    except Exception as exc:
        typer.echo(f"Error loading config: {exc}", err=True)
        raise typer.Exit(1)

    import json
    state_data = json.loads((job / "state.json").read_text(encoding="utf-8"))
    cfg.project.output_dir = job.parent
    cfg.input.path = Path(state_data["input_path"])

    setup_logging(job)

    try:
        asyncio.run(run_translation(cfg, limit=limit, quality_failed_only=True))
    except KeyboardInterrupt:
        typer.echo("\nInterrupted.", err=True)
        raise typer.Exit(130)
    except Exception as exc:
        typer.echo(f"Retry quality-failed: {exc}", err=True)
        raise typer.Exit(1)


@app.command()
def validate(
    job: Path = typer.Argument(..., help="Path to job output directory"),
    config: Path | None = typer.Option(None, "--config", "-c", help="Path to config YAML file (optional for language setting)"),
) -> None:
    """Run validation checks on a completed translation job."""
    from .translator import run_validate

    if not job.is_dir():
        typer.echo(f"Directory not found: {job}", err=True)
        raise typer.Exit(1)

    # Load config if provided (for language setting); otherwise use defaults
    cfg = None
    if config is not None:
        try:
            cfg = _load_config_or_exit(config, require_api_key=False)
        except Exception:
            pass  # Ignore config errors; validate doesn't need API key

    run_validate(job, cfg)


@app.command()
def export(
    job: Path = typer.Argument(..., help="Path to job output directory"),
    config: Path = typer.Option(..., "--config", "-c", help="Path to config YAML file"),
) -> None:
    """Re-export bilingual EPUB and HTML from completed job."""
    load_dotenv(encoding="utf-8-sig")  # tolerate a UTF-8 BOM in .env
    from .translator import run_export

    # Export only re-renders from the checkpoint; no API key needed.
    cfg = _load_config_or_exit(config, require_api_key=False)
    run_export(job, cfg)


@app.command(name="explain-quality")
def explain_quality(
    output_dir: Path = typer.Argument(..., help="Path to job output directory"),
    segment_id: str = typer.Argument(..., help="Segment ID to inspect"),
    config: Path | None = typer.Option(None, "--config", "-c", help="Optional config file for quality settings"),
) -> None:
    """Show detailed quality issue information for a specific segment.

    Loads the segment source and latest translation from the job directory,
    re-runs quality checks, and displays matched characters with positions
    and Traditional Chinese suggestions.
    """
    from .checkpoint import CheckpointManager
    from .config import QualityConfig
    from .validator import check_quality

    if not output_dir.is_dir():
        typer.echo(f"Directory not found: {output_dir}", err=True)
        raise typer.Exit(1)

    checkpoint = CheckpointManager(output_dir)
    segments = checkpoint.load_segments()
    seg = next((s for s in segments if s.segment_id == segment_id), None)
    if seg is None:
        typer.echo(f"Segment not found in segments.jsonl: {segment_id}", err=True)
        raise typer.Exit(1)

    all_translations = checkpoint.load_all_translations()
    record = all_translations.get(segment_id)

    typer.echo(f"segment_id  : {seg.segment_id}")
    typer.echo(f"source      : {seg.source_text[:300]}")

    if record is None:
        typer.echo("No translation record found.")
        return

    typer.echo(f"translation : {record.translation[:300]}")
    typer.echo(f"status      : {record.status}")
    typer.echo(f"model       : {record.model}")
    typer.echo(f"attempt     : {record.attempt}")
    typer.echo(f"error       : {record.error}")

    # Re-run quality checks to get live match details.
    quality_cfg: QualityConfig
    if config is not None:
        try:
            cfg_obj = _load_config_or_exit(config, require_api_key=False)
            quality_cfg = cfg_obj.quality
        except Exception:
            quality_cfg = QualityConfig()
    else:
        quality_cfg = QualityConfig()

    issues = check_quality(seg, record.translation, quality_cfg)

    if not issues:
        typer.echo("quality issues: (none)")
    else:
        typer.echo("quality issues:")
        for issue in issues:
            typer.echo(f"  check     : {issue.check}")
            typer.echo(f"  severity  : {issue.severity}")
            typer.echo(f"  detail    : {issue.detail}")
            if issue.matches:
                typer.echo("  matches   :")
                for m in issue.matches:
                    sug = m.get("suggestion")
                    typer.echo(
                        f"    text={m['text']!r}  pos={m['position']}  suggestion={sug!r}"
                    )
            typer.echo("")

    # Also show stored matches from translation time if available.
    if record.quality_matches:
        typer.echo("stored matches (recorded at translation time):")
        for m in record.quality_matches:
            sug = m.get("suggestion")
            typer.echo(f"  text={m['text']!r}  pos={m['position']}  suggestion={sug!r}")


@app.command(name="repair-jsonl")
def repair_jsonl(
    output_dir: Path = typer.Argument(..., help="Path to job output directory"),
) -> None:
    """Back up and repair corrupt JSONL lines in translations.jsonl and segments.jsonl.

    Uses utf-8-sig encoding to handle BOM files correctly (the BOM is stripped
    automatically, so a valid JSON first line is not misidentified as corrupt).
    Creates .bak backups before modifying files.
    """
    import json
    import shutil

    if not output_dir.is_dir():
        typer.echo(f"Directory not found: {output_dir}", err=True)
        raise typer.Exit(1)

    for filename in ("translations.jsonl", "segments.jsonl"):
        src = output_dir / filename
        if not src.exists():
            typer.echo(f"  {filename}: not found, skipping")
            continue

        # utf-8-sig strips BOM from the start of the file transparently.
        content = src.read_text(encoding="utf-8-sig")
        lines = content.splitlines()

        valid_lines: list[str] = []
        bad_count = 0
        for line in lines:
            stripped = line.strip()
            if not stripped:
                continue
            try:
                json.loads(stripped)
                valid_lines.append(stripped)
            except json.JSONDecodeError:
                bad_count += 1
                preview = stripped[:80]
                typer.echo(f"  {filename}: removing bad line: {preview!r}")

        bak = output_dir / (filename + ".bak")
        shutil.copy2(src, bak)

        new_content = "\n".join(valid_lines) + ("\n" if valid_lines else "")
        src.write_text(new_content, encoding="utf-8")

        typer.echo(
            f"  {filename}: {len(valid_lines)} valid kept, {bad_count} bad removed"
            f"  (backup: {bak.name})"
        )


@app.command(name="refresh-quality-status")
def refresh_quality_status(
    output_dir: Path = typer.Argument(..., help="Path to job output directory"),
    segment_id: str | None = typer.Argument(
        None, help="Segment ID to refresh (omit when using --all-quality-failed)"
    ),
    config: Path | None = typer.Option(
        None, "--config", "-c", help="Optional config file for quality settings"
    ),
    all_quality_failed: bool = typer.Option(
        False, "--all-quality-failed", help="Refresh all quality_failed segments"
    ),
) -> None:
    """Re-run quality checks on stored translations without any API calls.

    If the current quality gate now passes (e.g. after a false-positive fix), a
    new 'completed' record is appended to translations.jsonl.  The original
    quality_failed record is never modified (append-only).

    Use a single SEGMENT_ID to check one segment, or --all-quality-failed to
    process every segment whose latest record has status quality_failed.
    """
    from datetime import datetime, timezone

    from .checkpoint import CheckpointManager
    from .config import QualityConfig
    from .models import TranslationRecord
    from .validator import evaluate_quality_gate

    if not output_dir.is_dir():
        typer.echo(f"Directory not found: {output_dir}", err=True)
        raise typer.Exit(1)

    if segment_id is None and not all_quality_failed:
        typer.echo(
            "Provide a SEGMENT_ID argument or pass --all-quality-failed.", err=True
        )
        raise typer.Exit(1)

    # Load quality config (falls back to defaults if no config given / load fails).
    quality_cfg: QualityConfig
    if config is not None:
        try:
            cfg_obj = _load_config_or_exit(config, require_api_key=False)
            quality_cfg = cfg_obj.quality
        except Exception:
            quality_cfg = QualityConfig()
    else:
        quality_cfg = QualityConfig()

    checkpoint = CheckpointManager(output_dir)
    segments_list = checkpoint.load_segments()
    all_translations = checkpoint.load_all_translations()

    seg_by_id = {s.segment_id: s for s in segments_list}

    def _refresh_one(sid: str) -> bool:
        """Re-check quality for one segment.  Returns True if repaired."""
        seg = seg_by_id.get(sid)
        if seg is None:
            typer.echo(f"  {sid}: segment not found in segments.jsonl", err=True)
            return False
        record = all_translations.get(sid)
        if record is None:
            typer.echo(f"  {sid}: no translation record found", err=True)
            return False
        if record.status == "completed":
            typer.echo(f"  {sid}: already completed — no action needed")
            return False
        if record.status == "failed":
            typer.echo(
                f"  {sid}: hard failure (use retry-failed to re-translate) — no action"
            )
            return False

        # status == "quality_failed" — use the full severity-aware gate so that
        # warning-only issues (e.g. a single simplified-Chinese char) no longer
        # block repair when strict_mode is False.
        gate = evaluate_quality_gate(seg, record.translation, quality_cfg)
        if not gate.has_errors:
            warn_checks = [i.check for i in gate.warnings]
            new_record = TranslationRecord(
                segment_id=seg.segment_id,
                source_hash=seg.sha1_prefix,
                status="completed",
                source=seg.source_html,
                translation=record.translation,
                model=record.model,
                attempt=record.attempt + 1,
                error=None,
                quality_warnings=warn_checks or None,
                repaired_from_status=record.status,
                repair_reason="quality_recheck_passed",
                created_at=datetime.now(timezone.utc),
            )
            checkpoint.append_translation(new_record)
            warn_note = f" (warnings: {', '.join(warn_checks)})" if warn_checks else ""
            typer.echo(
                f"  ✓ {sid}: repaired → completed (attempt {new_record.attempt}){warn_note}"
            )
            return True
        else:
            checks_str = "; ".join(i.check for i in gate.errors)
            typer.echo(f"  ✗ {sid}: still failing — {checks_str}")
            for issue in gate.errors:
                if issue.matches:
                    for m in issue.matches:
                        sug = m.get("suggestion")
                        typer.echo(
                            f"      text={m['text']!r}  pos={m['position']}  suggestion={sug!r}"
                        )
            return False

    if segment_id is not None:
        _refresh_one(segment_id)
    else:
        qf_ids = checkpoint.load_quality_failed_ids()
        if not qf_ids:
            typer.echo("No quality_failed segments found.")
            return
        typer.echo(f"Refreshing {len(qf_ids)} quality_failed segment(s)...")
        repaired = 0
        for sid in sorted(qf_ids):
            if _refresh_one(sid):
                repaired += 1
        typer.echo(f"Done: {repaired}/{len(qf_ids)} repaired → completed.")


@app.command(name="report-missing")
def report_missing(
    job: Path = typer.Argument(..., help="Path to job output directory (contains state.json / segments.jsonl)"),
    config: Path = typer.Option(..., "--config", "-c", help="Path to config YAML file"),
) -> None:
    """Generate missing_translation_report.json for a job directory.

    Reads the original EPUB (from config.input.path), cross-references every
    translatable block against the job's segments.jsonl / translations.jsonl,
    writes missing_translation_report.json into the job directory, and prints
    a summary table.

    No API key is required — no provider calls are made.
    """
    from .translator import run_report_missing

    if not job.is_dir():
        typer.echo(f"Directory not found: {job}", err=True)
        raise typer.Exit(1)

    if not (job / "segments.jsonl").exists():
        typer.echo(
            f"No segments.jsonl found in {job}. Run 'translate' first.",
            err=True,
        )
        raise typer.Exit(1)

    # No API key needed — report-missing never calls a provider.
    cfg = _load_config_or_exit(config, require_api_key=False)

    try:
        run_report_missing(job, cfg.input.path, cfg)
    except Exception as exc:
        typer.echo(f"report-missing failed: {exc}", err=True)
        raise typer.Exit(1)
