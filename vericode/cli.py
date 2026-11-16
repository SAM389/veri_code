"""Command-line entry point: `vericode check <paths>`."""

from __future__ import annotations

import asyncio
import json
import sys
from enum import StrEnum
from pathlib import Path
from typing import Annotated

import typer

from vericode import __version__
from vericode.engine import run_check
from vericode.verdict import Report, Severity, sort_findings

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_INTERNAL_ERROR = 3

app = typer.Typer(
    name="vericode",
    help="Check AI-written Python code for made-up packages, versions and APIs.",
    no_args_is_help=True,
    add_completion=False,
)


class OutputFormat(StrEnum):
    text = "text"
    json = "json"


class FailOn(StrEnum):
    block = "block"
    warn = "warn"


_COLORS = {Severity.BLOCK: typer.colors.RED, Severity.WARN: typer.colors.YELLOW}


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"vericode {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: Annotated[
        bool,
        typer.Option(
            "--version", callback=_version_callback, is_eager=True, help="Show version and exit."
        ),
    ] = False,
) -> None:
    """VeriCode: deterministic checks first, the LLM last."""


@app.command()
def check(
    paths: Annotated[
        list[Path] | None,
        typer.Argument(help="Files or directories to check. Defaults to the root directory."),
    ] = None,
    diff: Annotated[
        str | None,
        typer.Option(
            "--diff", help="Check a unified diff instead: a patch file path, or '-' for stdin."
        ),
    ] = None,
    root: Annotated[
        Path,
        typer.Option(help="Project root. Paths in output and in the diff are relative to it."),
    ] = Path("."),
    output: Annotated[
        OutputFormat, typer.Option("--format", help="Output format.")
    ] = OutputFormat.text,
    fail_on: Annotated[
        FailOn, typer.Option(help="Exit with code 1 when the verdict reaches this level.")
    ] = FailOn.block,
    no_cache: Annotated[
        bool, typer.Option("--no-cache", help="Ignore and don't update the PyPI cache.")
    ] = False,
) -> None:
    """Check files or a diff and print findings with a PASS / WARN / BLOCK verdict.

    Exit codes: 0 = below --fail-on, 1 = verdict reached --fail-on, 3 = internal error.
    """
    try:
        diff_text = None
        if diff is not None:
            diff_text = sys.stdin.read() if diff == "-" else Path(diff).read_text(encoding="utf-8")
        report = asyncio.run(run_check(root, paths, diff_text, use_cache=not no_cache))
    except Exception as e:  # fail open, but say so loudly
        typer.secho(f"vericode: internal error: {type(e).__name__}: {e}", err=True, fg="red")
        raise typer.Exit(EXIT_INTERNAL_ERROR) from e

    if output is OutputFormat.json:
        typer.echo(json.dumps(report.to_json(), indent=2))
    else:
        _print_text(report)

    threshold = Severity.BLOCK if fail_on is FailOn.block else Severity.WARN
    raise typer.Exit(EXIT_FAILED if report.verdict.rank >= threshold.rank else EXIT_OK)


def _print_text(report: Report) -> None:
    for f in sort_findings(report.findings):
        where = f.file or "-"
        if f.line:
            where += f":{f.line}"
        subject = f.package or f.symbol or ""
        typer.secho(f"{f.severity:<5}", fg=_COLORS.get(f.severity), bold=True, nl=False)
        typer.echo(f" {where}  {f.check}  {subject + ': ' if subject else ''}{f.reason}")
        if f.fix_hint:
            typer.echo(f"      -> {f.fix_hint}")
    if report.findings:
        typer.echo("")
    v = report.verdict
    typer.secho(f"Verdict: {v}", fg=_COLORS.get(v, typer.colors.GREEN), bold=True, nl=False)
    typer.echo(
        f"  ({report.count(Severity.BLOCK)} block, {report.count(Severity.WARN)} warn"
        f" | {report.files_checked} files, {report.packages_checked} packages"
        f" | {report.duration_s:.2f}s)"
    )


if __name__ == "__main__":
    app()
