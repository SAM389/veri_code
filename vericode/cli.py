"""Command-line entry point: `vericode check <paths>`."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from vericode import __version__

app = typer.Typer(
    name="vericode",
    help="Check AI-written Python code for made-up packages, versions and APIs.",
    no_args_is_help=True,
    add_completion=False,
)


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
        list[Path] | None, typer.Argument(help="Files or directories to check.")
    ] = None,
) -> None:
    """Check files or a diff and print findings with a PASS / WARN / BLOCK verdict."""
    # Implemented in Phase 1.
    typer.echo("vericode check: not implemented yet.", err=True)
    raise typer.Exit(code=3)


if __name__ == "__main__":
    app()
