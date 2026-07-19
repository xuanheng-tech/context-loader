"""Command-line interface for deterministic Codex project context."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from . import __version__
from .collect import collect_project_context
from .git import ContextLoaderError, collect_repository_state
from .render import render_markdown


class SafeArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        del message
        raise ContextLoaderError("invalid command-line arguments", exit_code=2)


def _parser() -> argparse.ArgumentParser:
    parser = SafeArgumentParser(
        prog="codex-project-context",
        description="Render deterministic local Git context as Markdown.",
        allow_abbrev=False,
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("--repo", required=True, help="absolute Git worktree root")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    try:
        arguments = _parser().parse_args(argv)
        state = collect_repository_state(arguments.repo)
        output = render_markdown(state, collect_project_context(state.repository))
    except ContextLoaderError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return exc.exit_code
    except Exception:
        print("error: context collection failed", file=sys.stderr)
        return 1
    try:
        sys.stdout.buffer.write(output)
    except Exception:
        print("error: unable to write context output", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
