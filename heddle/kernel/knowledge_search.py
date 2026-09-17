"""Disposable knowledge-plane search over Feature Specs and Heddle patterns."""

from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from markdown_it import MarkdownIt
from markdown_it.token import Token

from heddle.kernel.knowledge import (
    PATTERNS_ROOT,
    heading_anchor,
    read_markdown,
)
from heddle.kernel.project_config import KernelError, ProjectConfig

SearchKind = Literal["feature-spec-section", "pattern"]


@dataclass(frozen=True)
class SearchRequest:
    query: str
    limit: int


@dataclass(frozen=True)
class SearchHit:
    kind: SearchKind
    path: str
    anchor: str
    title: str
    body: str


@dataclass(frozen=True)
class _SearchDocument:
    kind: SearchKind
    path: str
    anchor: str
    title: str
    body: str


_MARKDOWN = MarkdownIt("commonmark")
_CREATE_SEARCH_TABLE = """
CREATE VIRTUAL TABLE search_documents USING fts5(
  title,
  body,
  kind UNINDEXED,
  path UNINDEXED,
  anchor UNINDEXED,
  tokenize = 'porter unicode61 remove_diacritics 1'
)
"""


def search_knowledge(
    config: ProjectConfig,
    request: SearchRequest,
) -> tuple[SearchHit, ...]:
    """Rebuild and query the complete current-worktree corpus in memory."""
    if not request.query.strip():
        raise KernelError(
            code="usage",
            message="search query must not be blank",
            hint="provide one non-blank natural-language query",
        )
    if not 1 <= request.limit <= 25:
        raise KernelError(
            code="usage",
            message=f"search limit must be from 1 through 25, got {request.limit}",
            hint="pass a result limit from 1 through 25",
        )

    documents = _load_documents(config)
    connection = sqlite3.connect(":memory:")
    try:
        try:
            connection.execute(_CREATE_SEARCH_TABLE)
        except sqlite3.OperationalError as error:
            raise KernelError(
                code="internal",
                message="this Python SQLite runtime does not provide FTS5",
                hint="install a Python build whose SQLite includes FTS5",
            ) from error
        connection.executemany(
            """
            INSERT INTO search_documents(title, body, kind, path, anchor)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                (
                    document.title,
                    document.body,
                    document.kind,
                    document.path,
                    document.anchor,
                )
                for document in documents
            ),
        )
        expression = " AND ".join(
            f'"{term.replace(chr(34), chr(34) * 2)}"' for term in request.query.split()
        )
        rows = connection.execute(
            """
            SELECT kind, path, anchor, title, body,
                   bm25(search_documents, 1.0, 1.0) AS rank
            FROM search_documents
            WHERE search_documents MATCH ?
            ORDER BY rank ASC, path ASC, anchor ASC
            LIMIT ?
            """,
            (expression, request.limit),
        ).fetchall()
        return tuple(
            SearchHit(
                kind=row[0],
                path=row[1],
                anchor=row[2],
                title=row[3],
                body=row[4],
            )
            for row in rows
        )
    finally:
        connection.close()


def _load_documents(config: ProjectConfig) -> tuple[_SearchDocument, ...]:
    specs_root = config.root / config.layout.specs
    patterns_root = config.root / PATTERNS_ROOT
    spec_paths = _corpus_paths(config.root, specs_root, required=True)
    pattern_paths = _corpus_paths(config.root, patterns_root, required=False)

    documents: list[_SearchDocument] = []
    for path in spec_paths:
        documents.extend(_feature_spec_documents(config.root, path))
    for path in pattern_paths:
        documents.append(_pattern_document(config.root, path))
    return tuple(documents)


def _corpus_paths(
    project_root: Path,
    corpus_root: Path,
    *,
    required: bool,
) -> tuple[Path, ...]:
    label = _display_path(project_root, corpus_root)
    if corpus_root.is_symlink():
        raise _corpus_error(label, "must not be a symlink")
    if not corpus_root.exists():
        if required:
            raise _corpus_error(label, "required corpus directory is missing")
        return ()
    if not corpus_root.is_dir():
        raise _corpus_error(label, "corpus root is not a directory")
    if not _is_contained(project_root, corpus_root):
        raise _corpus_error(label, "corpus root escapes the project")
    if not os.access(corpus_root, os.R_OK | os.X_OK):
        raise _corpus_error(label, "corpus root is unreadable")

    try:
        entries = sorted(corpus_root.rglob("*"), key=lambda path: path.as_posix())
    except OSError as error:
        raise _corpus_error(label, f"corpus root is unreadable: {error}") from error

    documents: list[Path] = []
    for entry in entries:
        relative = entry.relative_to(corpus_root)
        if any(part.startswith("_") for part in relative.parts):
            continue
        entry_label = _display_path(project_root, entry)
        if entry.is_symlink():
            raise _corpus_error(entry_label, "corpus entry must not be a symlink")
        if not _is_contained(project_root, entry):
            raise _corpus_error(entry_label, "corpus entry escapes the project")
        if entry.is_dir():
            if not os.access(entry, os.R_OK | os.X_OK):
                raise _corpus_error(entry_label, "corpus directory is unreadable")
            continue
        if entry.suffix != ".md":
            continue
        if not entry.is_file():
            raise _corpus_error(entry_label, "Markdown corpus entry is not a file")
        if not os.access(entry, os.R_OK):
            raise _corpus_error(entry_label, "Markdown corpus entry is unreadable")
        documents.append(entry)
    return tuple(documents)


def _feature_spec_documents(
    project_root: Path,
    path: Path,
) -> tuple[_SearchDocument, ...]:
    relative_path = path.relative_to(project_root).as_posix()
    document = read_markdown(path, display_path=relative_path)
    tokens = _MARKDOWN.parse(document.body)
    lines = document.body.splitlines(keepends=True)
    headings = _headings(tokens, levels={"h2", "h3"})
    anchors: dict[str, int] = {}
    results: list[_SearchDocument] = []

    for index, (token_index, _start, end, title) in enumerate(headings):
        del token_index
        next_start = headings[index + 1][1] if index + 1 < len(headings) else len(lines)
        base_anchor = heading_anchor(title)
        occurrence = anchors.get(base_anchor, 0)
        anchors[base_anchor] = occurrence + 1
        anchor = base_anchor if occurrence == 0 else f"{base_anchor}-{occurrence}"
        results.append(
            _SearchDocument(
                kind="feature-spec-section",
                path=relative_path,
                anchor=anchor,
                title=title,
                body="".join(lines[end:next_start]),
            )
        )
    return tuple(results)


def _pattern_document(project_root: Path, path: Path) -> _SearchDocument:
    relative_path = path.relative_to(project_root).as_posix()
    document = read_markdown(path, display_path=relative_path)
    tokens = _MARKDOWN.parse(document.body)
    headings = _headings(tokens, levels={"h1"})
    if len(headings) != 1:
        raise _corpus_error(
            relative_path,
            "pattern must contain exactly one CommonMark H1",
        )
    token_index, _start, end, title = headings[0]
    if token_index != 0:
        raise _corpus_error(
            relative_path,
            "pattern H1 must be the first non-frontmatter content block",
        )
    lines = document.body.splitlines(keepends=True)
    return _SearchDocument(
        kind="pattern",
        path=relative_path,
        anchor=heading_anchor(title),
        title=title,
        body="".join(lines[end:]),
    )


def _headings(
    tokens: list[Token],
    *,
    levels: set[str],
) -> list[tuple[int, int, int, str]]:
    headings: list[tuple[int, int, int, str]] = []
    for index, token in enumerate(tokens):
        if token.type != "heading_open" or token.tag not in levels:
            continue
        if token.map is None or index + 1 >= len(tokens):
            continue
        inline = tokens[index + 1]
        headings.append((index, token.map[0], token.map[1], _inline_text(inline)))
    return headings


def _inline_text(token: Token) -> str:
    if token.children is None:
        return token.content
    return "".join(
        child.content
        for child in token.children
        if child.type in {"text", "code_inline", "html_inline", "image"}
    ).strip()


def _is_contained(project_root: Path, path: Path) -> bool:
    try:
        path.resolve().relative_to(project_root.resolve())
    except (OSError, ValueError):
        return False
    return True


def _display_path(project_root: Path, path: Path) -> str:
    try:
        return path.relative_to(project_root).as_posix()
    except ValueError:
        return str(path)


def _corpus_error(path: str, detail: str) -> KernelError:
    return KernelError(
        code="workspace-invalid",
        message=f"invalid search corpus at {path}: {detail}",
        hint=f"repair or remove the offending corpus path {path}",
    )
