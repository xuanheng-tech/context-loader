"""Command-line interface for deterministic project context."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from . import __version__
from .application import load_project_context, render_json
from .git import ContextLoaderError


class SafeArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        # Categorize failures using static messages without echoing caller tokens or paths.
        lowered = message.lower()
        if "option supplied more than once" in lowered:
            reason = "invalid command-line arguments: option supplied more than once"
        elif "the following arguments are required" in lowered:
            reason = "invalid command-line arguments: missing required option"
        elif "unrecognized arguments" in lowered:
            reason = "invalid command-line arguments: unrecognized argument"
        elif "invalid choice" in lowered:
            reason = "invalid command-line arguments: invalid option value"
        elif "expected one argument" in lowered:
            reason = "invalid command-line arguments: option requires a value"
        else:
            reason = "invalid command-line arguments"
        raise ContextLoaderError(reason, exit_code=2)


class _OnceAction(argparse.Action):
    def __call__(
        self,
        parser: argparse.ArgumentParser,
        namespace: argparse.Namespace,
        values: str,
        option_string: str | None = None,
    ) -> None:
        seen = f"_seen_{self.dest}"
        if getattr(namespace, seen, False):
            parser.error("option supplied more than once")
        setattr(namespace, seen, True)
        setattr(namespace, self.dest, values)


def _parser() -> argparse.ArgumentParser:
    parser = SafeArgumentParser(
        prog="project-context",
        description="Render deterministic local Git context as Markdown or JSON.",
        allow_abbrev=False,
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument(
        "--repo", required=True, action=_OnceAction, help="absolute Git worktree path"
    )
    parser.add_argument(
        "--focus",
        action=_OnceAction,
        help="optional bounded task focus used for deterministic AGENTS section selection",
    )
    parser.add_argument(
        "--path",
        dest="target_path",
        action=_OnceAction,
        help="optional repository-relative target path used for AGENTS section selection",
    )
    parser.add_argument(
        "--format",
        choices=("markdown", "json", "json-compact"),
        default="markdown",
        action=_OnceAction,
        help=(
            "output format (default: markdown); json-compact omits the "
            "duplicated source bodies (see docs/reference.md, Compact model consumption)"
        ),
    )
    parser.add_argument(
        "--layout",
        choices=("default", "cache-friendly"),
        default="default",
        action=_OnceAction,
        help=(
            "section order in rendered context (default: default); "
            "cache-friendly places stable static sections before dynamic Git state"
        ),
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
            include_nested_context=arguments.format != "markdown",
            layout=arguments.layout,
        )
        if arguments.format == "markdown":
            output = result.context.encode("utf-8")
        elif arguments.format == "json":
            output = render_json(result)
        else:
            output = render_json(result, compact=True)
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
