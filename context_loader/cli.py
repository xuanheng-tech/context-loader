"""Command-line interface for deterministic Codex project context."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from . import __version__
from .application import load_project_context, render_json
from .git import ContextLoaderError


class SafeArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        del message
        raise ContextLoaderError("invalid command-line arguments", exit_code=2)


def _parser() -> argparse.ArgumentParser:
    parser = SafeArgumentParser(
        prog="codex-project-context",
        description="Render deterministic local Git context as Markdown or JSON.",
        allow_abbrev=False,
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("--repo", required=True, help="absolute Git worktree path")
    parser.add_argument(
        "--focus",
        help="optional bounded task focus used for deterministic AGENTS section selection",
    )
    parser.add_argument(
        "--path",
        dest="target_path",
        help="optional repository-relative target path used for AGENTS section selection",
    )
    parser.add_argument(
        "--format",
        choices=("markdown", "json"),
        default="markdown",
        help="output format (default: markdown)",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    try:
        arguments = _parser().parse_args(argv)
        result = load_project_context(
            arguments.repo,
            require_repository_root=arguments.format == "markdown",
            focus=arguments.focus,
            path=arguments.target_path,
        )
        output = (
            result.context.encode("utf-8")
            if arguments.format == "markdown"
            else render_json(result)
        )
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
