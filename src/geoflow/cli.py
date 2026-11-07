import json
import signal
from pathlib import Path
from typing import Annotated

import typer

from geoflow.config import load_config
from geoflow.exceptions import GeoFlowError, PipelineInterrupted
from geoflow.orchestration.pipeline import run_dataset
from geoflow.reporting.logging import configure_logging

app = typer.Typer(no_args_is_help=True, help="GeoFlow geospatial ETL and quality reports.")
ConfigPath = Annotated[Path, typer.Argument(exists=True, dir_okay=False, readable=True)]


@app.command("validate-config")
def validate_config(config: ConfigPath) -> None:
    """Validate YAML and CRS definitions without touching sources or a database."""
    try:
        loaded = load_config(config)
        typer.echo(f"Valid configuration: {len(loaded.datasets)} dataset(s)")
    except GeoFlowError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(2) from None


@app.command()
def run(
    config: ConfigPath, dataset: Annotated[str, typer.Option(help="Exact dataset name")]
) -> None:
    """Run one explicitly selected dataset; logs go to stderr, report path to stdout."""
    configure_logging()

    def interrupted(signum, frame):
        raise PipelineInterrupted("Pipeline interrupted; completed evidence is retained")

    previous = {sig: signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM)}
    for sig in previous:
        signal.signal(sig, interrupted)
    try:
        loaded = load_config(config)
        selected = next((d for d in loaded.datasets if d.name == dataset), None)
        if selected is None:
            raise GeoFlowError("Dataset name is not in this configuration")
        typer.echo(str(run_dataset(selected, loaded.output_dir)))
    except GeoFlowError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(130 if isinstance(exc, PipelineInterrupted) else 1) from None
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)


@app.command()
def report(path: ConfigPath) -> None:
    """Print a run's JSON report without executing ETL."""
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(doc, dict) or not {"run_id", "status", "source_sha256"} <= doc.keys():
            raise ValueError("Not a run report")
        typer.echo(json.dumps(doc, indent=2, ensure_ascii=False))
    except (OSError, ValueError) as exc:
        typer.echo("Cannot read a GeoFlow run report", err=True)
        raise typer.Exit(2) from exc


if __name__ == "__main__":
    app()
