"""Load one deterministic project-context result for all output formats."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path

from . import __version__
from .collect import ProjectContext, collect_project_context
from .git import collect_repository
from .render import render_markdown_with_details, rendered_source_contents

JSON_SCHEMA_VERSION = 1
TOOL_NAME = "context-loader"


@dataclass(frozen=True, slots=True)
class ToolIdentity:
    name: str
    version: str


@dataclass(frozen=True, slots=True)
class RepositoryIdentity:
    requested_path: Path
    canonical_root: Path


@dataclass(frozen=True, slots=True)
class ProjectContextSource:
    ordinal: int
    kind: str
    scope: str
    path: Path
    content_sha256: str
    content: str


@dataclass(frozen=True, slots=True)
class ProjectContextResult:
    schema_version: int
    tool: ToolIdentity
    repository: RepositoryIdentity
    sources: tuple[ProjectContextSource, ...]
    context: str
    context_sha256: str
    warnings: tuple[str, ...]


def _text_sha256(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def source_scope_for_path(path: Path, canonical_root: Path) -> str:
    """Classify a canonical source path without following or reading it."""
    try:
        path.relative_to(canonical_root)
    except ValueError:
        return "global"
    return "repository"


def _source_kind(name: str) -> str:
    if name == "AGENTS.md":
        return "agents"
    if name == "README.md":
        return "readme"
    return "entry_file"


def _sources(
    canonical_root: Path,
    project: ProjectContext,
    included_sections: tuple[str, ...],
) -> tuple[ProjectContextSource, ...]:
    sources: list[ProjectContextSource] = []
    for ordinal, (source, content) in enumerate(
        rendered_source_contents(project, included_sections)
    ):
        path = canonical_root / source.name
        sources.append(
            ProjectContextSource(
                ordinal=ordinal,
                kind=_source_kind(source.name),
                scope=source_scope_for_path(path, canonical_root),
                path=path,
                content_sha256=_text_sha256(content),
                content=content,
            )
        )
    return tuple(sources)


def load_project_context(
    repo: str | os.PathLike[str], *, require_repository_root: bool = False
) -> ProjectContextResult:
    """Collect one repository once and return its deterministic machine-readable result."""
    location, state = collect_repository(
        repo,
        require_canonical_root=require_repository_root,
    )
    project = collect_project_context(state.repository)
    rendered = render_markdown_with_details(state, project)
    context = rendered.output.decode("utf-8")
    return ProjectContextResult(
        schema_version=JSON_SCHEMA_VERSION,
        tool=ToolIdentity(name=TOOL_NAME, version=__version__),
        repository=RepositoryIdentity(
            requested_path=location.requested_path,
            canonical_root=location.canonical_root,
        ),
        sources=_sources(state.repository, project, rendered.included_sections),
        context=context,
        context_sha256=hashlib.sha256(rendered.output).hexdigest(),
        warnings=(),
    )


def render_json(result: ProjectContextResult) -> bytes:
    """Serialize one result as stable UTF-8 JSON followed by exactly one newline."""
    document = {
        "schema_version": result.schema_version,
        "tool": {
            "name": result.tool.name,
            "version": result.tool.version,
        },
        "repository": {
            "requested_path": os.fspath(result.repository.requested_path),
            "canonical_root": os.fspath(result.repository.canonical_root),
        },
        "sources": [
            {
                "ordinal": source.ordinal,
                "kind": source.kind,
                "scope": source.scope,
                "path": os.fspath(source.path),
                "content_sha256": source.content_sha256,
                "content": source.content,
            }
            for source in result.sources
        ],
        "context": result.context,
        "context_sha256": result.context_sha256,
        "warnings": list(result.warnings),
    }
    serialized = json.dumps(
        document,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return f"{serialized}\n".encode()
