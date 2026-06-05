from __future__ import annotations

import asyncio
import sys
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


@app.command()
def translate(
    config: Path = typer.Option(..., "--config", "-c", help="Path to config YAML file"),
    mock: bool = typer.Option(False, "--mock", help="Use offline mock provider (no API calls)"),
    limit: int | None = typer.Option(None, "--limit", help="Translate at most N segments this run"),
) -> None:
    """Translate an EPUB using a config file."""
    load_dotenv(encoding="utf-8-sig")  # tolerate a UTF-8 BOM in .env
    from .logging_setup import setup_logging
    from .translator import run_translation

    cfg = _load_config_or_exit(config, require_api_key=not mock)

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
        asyncio.run(run_translation(cfg, provider=provider, limit=limit))
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
) -> None:
    """Run validation checks on a completed translation job."""
    from .translator import run_validate

    if not job.is_dir():
        typer.echo(f"Directory not found: {job}", err=True)
        raise typer.Exit(1)

    run_validate(job)


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
