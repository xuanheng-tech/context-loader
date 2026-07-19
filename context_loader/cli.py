"""Command-line interface for deterministic Codex project context."""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Sequence

from .git import ContextLoaderError, RepositoryState, collect_repository_state

SCHEMA = "context-loader/v0.1"
MAX_CHANGE_ENTRIES = 100
MAX_CHANGE_MARKDOWN_BYTES = 4 * 1024


class SafeArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        del message
        raise ContextLoaderError("invalid command-line arguments")


def _parser() -> argparse.ArgumentParser:
    parser = SafeArgumentParser(
        prog="codex-project-context",
        description="Render deterministic local Git context as Markdown.",
        allow_abbrev=False,
    )
    parser.add_argument("--repo", required=True, help="absolute Git worktree root")
    return parser


def _display(value: str) -> str:
    rendered: list[str] = []
    for character in value:
        codepoint = ord(character)
        if character == "`":
            rendered.append(r"\x60")
        elif 0xDC80 <= codepoint <= 0xDCFF:
            rendered.append(f"\\x{codepoint - 0xDC00:02x}")
        elif character.isprintable():
            rendered.append(character)
        elif codepoint <= 0xFF:
            rendered.append(f"\\x{codepoint:02x}")
        elif codepoint <= 0xFFFF:
            rendered.append(f"\\u{codepoint:04x}")
        else:
            rendered.append(f"\\U{codepoint:08x}")
    return "".join(rendered)


def _change_lines(state: RepositoryState) -> tuple[list[str], bool]:
    lines: list[str] = []
    used_bytes = 0
    for change in state.changes:
        if len(lines) == MAX_CHANGE_ENTRIES:
            return lines, True
        line = f"- `{change.status} {_display(change.path)}`"
        line_bytes = len(f"{line}\n".encode())
        if used_bytes + line_bytes > MAX_CHANGE_MARKDOWN_BYTES:
            return lines, True
        lines.append(line)
        used_bytes += line_bytes
    return lines, False


def render_markdown(state: RepositoryState) -> str:
    """Render a byte-stable Markdown document without wall-clock fields."""
    lines = [
        "# Codex Project Context",
        "",
        f"- Schema: `{SCHEMA}`",
        f"- Repository: `{_display(os.fspath(state.repository))}`",
        "",
        "## Git State",
        "",
        f"- Branch: `{_display(state.branch)}`",
        f"- HEAD: `{_display(state.head)}`",
        f"- Upstream: `{_display(state.upstream)}`",
        f"- Ahead / behind: `{_display(state.ahead_behind)}`",
        f"- Worktree: `{state.worktree}`",
        "",
        "### Working Tree Changes",
        "",
    ]
    change_lines, truncated = _change_lines(state)
    if change_lines:
        lines.extend(change_lines)
    elif not truncated:
        lines.append("No changes.")
    if truncated:
        lines.append("- Additional changes omitted by the 100-entry / 4-KiB limit.")

    lines.extend(("", "## Recent Commits", ""))
    if state.commits:
        lines.extend(
            f"- `{commit.object_id[:12]}` · {_display(commit.author_date)} · "
            f"{_display(commit.subject)}"
            for commit in state.commits
        )
    else:
        lines.append("No commits.")
    return "\n".join(lines) + "\n"


def main(argv: Sequence[str] | None = None) -> int:
    try:
        arguments = _parser().parse_args(argv)
        output = render_markdown(collect_repository_state(arguments.repo)).encode()
    except ContextLoaderError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
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
