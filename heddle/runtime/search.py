"""Native ``heddle search`` handler."""

from __future__ import annotations

import sys
from collections.abc import Mapping
from dataclasses import dataclass

from heddle.contracts import operations as ops
from heddle.contracts.result import ExitCode, HeddleError, HeddleResult
from heddle.kernel.knowledge_search import SearchRequest, search_knowledge
from heddle.kernel.project_config import KernelError, load_project_config_from_cwd
from heddle.runtime.diagnostics import envelope_diagnostics, kernel_error_result
from heddle.runtime.output import emit_envelope

_USAGE = "usage: heddle search <query> [--limit <1..25>] [--titles-only] [--json]"
_EXITS: Mapping[str, ExitCode] = {
    "usage": ExitCode.USAGE,
    "workspace-invalid": ExitCode.FATAL,
    "internal": ExitCode.INTERNAL,
}


@dataclass(frozen=True)
class _ParsedSearch:
    query: str
    limit: int
    titles_only: bool


def run_search(args: list[str], json_mode: bool) -> int:
    parsed, failure = _parse_search(args)
    if failure is not None:
        return _emit(failure, json_mode)
    assert parsed is not None

    from heddle.runtime.application import execute

    return _emit(
        execute(ops.Search(parsed.query, parsed.limit, parsed.titles_only)), json_mode
    )


def search(parsed: ops.Search) -> HeddleResult:
    try:
        config = load_project_config_from_cwd()
    except KernelError as error:
        return kernel_error_result(
            error,
            exit_codes=_EXITS,
            include_feature_switch_action=False,
        )
    diagnostics = envelope_diagnostics(config.diagnostics)
    try:
        hits = search_knowledge(
            config,
            SearchRequest(query=parsed.query, limit=parsed.limit),
        )
    except KernelError as error:
        return kernel_error_result(
            error,
            exit_codes=_EXITS,
            diagnostics=diagnostics,
            include_feature_switch_action=False,
        )

    projected_hits = []
    for hit in hits:
        projected = {
            "kind": hit.kind,
            "path": hit.path,
            "anchor": hit.anchor,
            "title": hit.title,
        }
        if not parsed.titles_only:
            projected["body"] = hit.body
        projected_hits.append(projected)
    result = HeddleResult.success(
        {"query": parsed.query, "hits": projected_hits},
        diagnostics=diagnostics,
    )
    return result


def _parse_search(
    args: list[str],
) -> tuple[_ParsedSearch | None, HeddleResult | None]:
    query: str | None = None
    limit = 5
    titles_only = False
    saw_limit = False
    saw_titles_only = False
    index = 0
    while index < len(args):
        token = args[index]
        if token == "--limit":
            if saw_limit:
                return None, _usage("duplicate command-local flag '--limit'")
            saw_limit = True
            if index + 1 >= len(args) or args[index + 1].startswith("--"):
                return None, _usage("--limit requires an integer value")
            raw_limit = args[index + 1]
            try:
                limit = int(raw_limit)
            except ValueError:
                return None, _usage(
                    f"--limit must be an integer from 1 through 25, got {raw_limit!r}"
                )
            if not 1 <= limit <= 25:
                return None, _usage(
                    f"--limit must be from 1 through 25, got {raw_limit!r}"
                )
            index += 2
            continue
        if token == "--titles-only":
            if saw_titles_only:
                return None, _usage("duplicate command-local flag '--titles-only'")
            saw_titles_only = True
            titles_only = True
            index += 1
            continue
        if token.startswith("-"):
            return None, _usage(f"unrecognized search argument {token!r}")
        if query is not None:
            return None, _usage("search takes exactly one <query> argument")
        query = token
        index += 1

    if query is None:
        return None, _usage("search requires exactly one <query> argument")
    if not query.strip():
        return None, _usage("search query must not be blank")
    return _ParsedSearch(query=query, limit=limit, titles_only=titles_only), None


def _usage(message: str) -> HeddleResult:
    return HeddleResult.failure(
        HeddleError(code="usage", message=message, hint=_USAGE),
        exit_code=ExitCode.USAGE,
    )


def _emit(result: HeddleResult, json_mode: bool) -> int:
    return emit_envelope(result, json_mode, _render_human)


def _render_human(result: HeddleResult) -> None:
    if result.error is not None:
        print(
            f"heddle: error[{result.error.code}]: {result.error.message}",
            file=sys.stderr,
        )
        print(f"  hint: {result.error.hint}", file=sys.stderr)
        for diagnostic in result.diagnostics:
            print(
                f"note: {diagnostic.code}: {diagnostic.message}",
                file=sys.stderr,
            )
        return

    data = result.data or {}
    hits = data.get("hits", [])
    if not hits:
        print("(no hits)")
    elif all("body" not in hit for hit in hits):
        print(
            "\n".join(f"{hit['path']}#{hit['anchor']} — {hit['title']}" for hit in hits)
        )
    else:
        blocks = []
        for hit in hits:
            header = f"{hit['path']}#{hit['anchor']} — {hit['title']}"
            blocks.append(f"{header}\n{hit['body']}".rstrip())
        print("\n---\n".join(blocks))
    for diagnostic in result.diagnostics:
        print(f"note: {diagnostic.code}: {diagnostic.message}")
