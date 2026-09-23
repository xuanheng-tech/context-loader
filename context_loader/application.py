"""Load one deterministic project-context result for all output formats."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path

from . import __version__
from .collect import (
    NOT_PRESENT,
    SKIPPED_ENCODING,
    SKIPPED_NOT_REGULAR,
    SKIPPED_SYMLINK,
    SKIPPED_UNREADABLE,
    TRUNCATION_MARKER,
    AgentsSectionAuditEntry,
    AgentsSelectionAudit,
    AgentsSelectionInputError,
    CollectedFile,
    NestedContextPresence,
    ProjectContext,
    collect_project_context,
)
from .git import ContextLoaderError, collect_repository
from .render import render_markdown_with_details, rendered_source_contents

JSON_SCHEMA_VERSION = 1
COMPACT_JSON_SCHEMA_VERSION = 2
TOOL_NAME = "context-loader"

_STATUS_CODE_BY_MESSAGE = {
    NOT_PRESENT: "not_present",
    SKIPPED_SYMLINK: "skipped_symlink",
    SKIPPED_NOT_REGULAR: "skipped_not_regular",
    SKIPPED_ENCODING: "skipped_encoding",
    SKIPPED_UNREADABLE: "skipped_unreadable",
}


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
    selection: AgentsSelectionAudit | None = None


@dataclass(frozen=True, slots=True)
class ContextStatus:
    """One machine-readable skipped, omitted, truncated, or unreadable condition."""

    code: str
    subject_kind: str
    subject: str


@dataclass(frozen=True, slots=True)
class ProjectContextResult:
    schema_version: int
    tool: ToolIdentity
    repository: RepositoryIdentity
    sources: tuple[ProjectContextSource, ...]
    context: str
    context_sha256: str
    warnings: tuple[str, ...]
    statuses: tuple[ContextStatus, ...]
    nested_context: NestedContextPresence


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
                selection=source.selection,
            )
        )
    return tuple(sources)


def _status(code: str, subject_kind: str, subject: str) -> ContextStatus:
    return ContextStatus(code=code, subject_kind=subject_kind, subject=subject)


def _statuses_for_source(source: CollectedFile) -> list[ContextStatus]:
    if source.status is not None:
        code = _STATUS_CODE_BY_MESSAGE.get(source.status, "skipped_unreadable")
        return [_status(code, "source", source.name)]
    statuses: list[ContextStatus] = []
    if source.truncated or (source.selection is not None and source.selection.truncated):
        statuses.append(_status("truncated", "source", source.name))
    if source.selection is not None:
        if source.selection.parse_fallback:
            statuses.append(_status("parse_fallback", "source", source.name))
        if source.selection.source_scan_truncated:
            statuses.append(_status("source_scan_truncated", "source", source.name))
        if source.selection.index_truncated:
            statuses.append(_status("index_truncated", "source", source.name))
    return statuses


def _build_statuses(
    project: ProjectContext,
    *,
    omitted_sections: tuple[str, ...],
    changes_truncated: bool,
    commands_truncated: bool,
    rendered_sources: tuple[ProjectContextSource, ...],
) -> tuple[ContextStatus, ...]:
    statuses: list[ContextStatus] = []
    for source in (project.instructions, project.overview, *project.entry_files):
        statuses.extend(_statuses_for_source(source))

    truncated_sources = {
        status.subject
        for status in statuses
        if status.subject_kind == "source" and status.code == "truncated"
    }
    for source in rendered_sources:
        if TRUNCATION_MARKER not in source.content:
            continue
        name = source.path.name
        if name in truncated_sources:
            continue
        statuses.append(_status("truncated", "source", name))
        truncated_sources.add(name)

    if commands_truncated:
        statuses.append(_status("truncated", "commands", "Declared Commands"))
    if changes_truncated:
        statuses.append(_status("truncated", "changes", "Working Tree Changes"))

    if project.directory_tree.truncated:
        statuses.append(_status("truncated", "tree", "Directory Tree"))
    for entry in project.directory_tree.entries:
        if entry.kind == "unreadable_directory":
            subject = entry.path if entry.path else "."
            statuses.append(_status("unreadable", "tree_entry", subject))

    for title in omitted_sections:
        statuses.append(_status("section_omitted", "section", title))

    if project.nested_context.list_truncated:
        statuses.append(_status("nested_agents_list_truncated", "nested_context", "AGENTS.md"))
    if project.nested_context.scan_truncated:
        statuses.append(_status("nested_agents_scan_truncated", "nested_context", "AGENTS.md"))

    deduped: list[ContextStatus] = []
    seen: set[tuple[str, str, str]] = set()
    for status in statuses:
        key = (status.code, status.subject_kind, status.subject)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(status)
    return tuple(deduped)


def load_project_context(
    repo: str | os.PathLike[str],
    *,
    require_repository_root: bool = False,
    focus: str | None = None,
    path: str | None = None,
) -> ProjectContextResult:
    """Collect one repository once and return its deterministic machine-readable result."""
    location, state = collect_repository(
        repo,
        require_canonical_root=require_repository_root,
    )
    try:
        project = collect_project_context(state.repository, focus=focus, path=path)
    except AgentsSelectionInputError as exc:
        raise ContextLoaderError(str(exc), exit_code=2) from None
    rendered = render_markdown_with_details(state, project)
    context = rendered.output.decode("utf-8")
    sources = _sources(state.repository, project, rendered.included_sections)
    statuses = _build_statuses(
        project,
        omitted_sections=rendered.omitted_sections,
        changes_truncated=rendered.changes_truncated,
        commands_truncated=rendered.commands_truncated,
        rendered_sources=sources,
    )
    return ProjectContextResult(
        schema_version=JSON_SCHEMA_VERSION,
        tool=ToolIdentity(name=TOOL_NAME, version=__version__),
        repository=RepositoryIdentity(
            requested_path=location.requested_path,
            canonical_root=location.canonical_root,
        ),
        sources=sources,
        context=context,
        context_sha256=hashlib.sha256(rendered.output).hexdigest(),
        warnings=(),
        statuses=statuses,
        nested_context=project.nested_context,
    )


def _selection_document(selection: AgentsSelectionAudit) -> dict[str, object]:
    def entry_document(entry: AgentsSectionAuditEntry) -> dict[str, object]:
        return {
            "heading": entry.heading,
            "heading_level": entry.heading_level,
            "reasons": list(entry.reasons),
        }

    return {
        "source": selection.source,
        "selected_sections": [entry_document(entry) for entry in selection.selected_sections],
        "indexed_only_sections": [
            entry_document(entry) for entry in selection.indexed_only_sections
        ],
        "chars_selected": selection.chars_selected,
        "chars_omitted": selection.chars_omitted,
        "truncated": selection.truncated,
        "parse_fallback": selection.parse_fallback,
        "source_scan_truncated": selection.source_scan_truncated,
        "index_truncated": selection.index_truncated,
    }


def _status_document(status: ContextStatus) -> dict[str, object]:
    return {
        "code": status.code,
        "subject": status.subject,
        "subject_kind": status.subject_kind,
    }


def _source_document(source: ProjectContextSource, *, include_content: bool) -> dict[str, object]:
    document: dict[str, object] = {
        "ordinal": source.ordinal,
        "kind": source.kind,
        "scope": source.scope,
        "path": os.fspath(source.path),
        "content_sha256": source.content_sha256,
    }
    if include_content:
        document["content"] = source.content
    if source.selection is not None:
        document["selection"] = _selection_document(source.selection)
    return document


def render_json(result: ProjectContextResult, *, compact: bool = False) -> bytes:
    """Serialize one result as stable UTF-8 JSON followed by exactly one newline.

    ``compact`` projects away the duplicated source bodies only at this
    boundary: statuses and context were computed from the full model at load
    time, so both documents agree on everything except ``sources[*].content``.
    """
    document = {
        "schema_version": (COMPACT_JSON_SCHEMA_VERSION if compact else result.schema_version),
        "tool": {
            "name": result.tool.name,
            "version": result.tool.version,
        },
        "repository": {
            "requested_path": os.fspath(result.repository.requested_path),
            "canonical_root": os.fspath(result.repository.canonical_root),
        },
        "sources": [
            _source_document(source, include_content=not compact) for source in result.sources
        ],
        "statuses": [_status_document(status) for status in result.statuses],
        "nested_context": {
            "files": list(result.nested_context.files),
            "list_truncated": result.nested_context.list_truncated,
            "scan_truncated": result.nested_context.scan_truncated,
        },
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
