"""
Feature-search kernel scaffold.

These are deliberate red discriminators for milestone core.  They exercise the
single internal typed seam ratified by the Feature Spec; no parser, SQLite, or
filesystem helper is made public just to support tests.

Behavior contract: feature-search
"""

from __future__ import annotations

import dataclasses
import importlib
from pathlib import Path
from types import ModuleType

import pytest

from heddle.kernel.project_config import load_project_config
from tests.runtime.search_helpers import snapshot_tree


def _search_module() -> ModuleType:
    try:
        return importlib.import_module("heddle.kernel.knowledge_search")
    except ModuleNotFoundError as error:
        pytest.fail(
            "RED feature-search: heddle.kernel.knowledge_search and its "
            f"declared parser dependency must land in m1 ({error})",
            pytrace=False,
        )


def _make_host(tmp_path: Path) -> Path:
    host = tmp_path / "host"
    (host / "docs" / "features").mkdir(parents=True)
    (host / "docs" / "patterns").mkdir(parents=True)
    (host / ".heddle.yaml").write_text(
        "layout:\n  specs: docs/features\n",
        encoding="utf-8",
    )
    return host


def _write(path: Path, text: str, *, newline: str | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline=newline)


def _search(host: Path, query: str, limit: int = 25):
    module = _search_module()
    return module.search_knowledge(
        load_project_config(host),
        module.SearchRequest(query=query, limit=limit),
    )


def test_ac01_searches_feature_sections_and_whole_patterns(tmp_path: Path) -> None:
    """AC-1: both fields/kinds use safely quoted Porter AND semantics."""
    host = _make_host(tmp_path)
    _write(
        host / "docs" / "features" / "channels.md",
        """---
type: feature-spec
---
# Channels

## Intent titleonly

Resilient channels carry bodyonly failures.

## Literal tokens

OR foo"bar error* and  are ordinary authored text.

## Partial precedent

Resilient alone is insufficient.
""",
    )
    _write(
        host / "docs" / "patterns" / "typed-channel.md",
        """# Intent pattern

Resilient channels use a typed boundary.
""",
    )

    hits = _search(host, "resilient channel")
    module = _search_module()
    assert set(hits) == {
        module.SearchHit(
            kind="feature-spec-section",
            path="docs/features/channels.md",
            anchor="intent-titleonly",
            title="Intent titleonly",
            body="\nResilient channels carry bodyonly failures.\n\n",
        ),
        module.SearchHit(
            kind="pattern",
            path="docs/patterns/typed-channel.md",
            anchor="intent-pattern",
            title="Intent pattern",
            body="\nResilient channels use a typed boundary.\n",
        ),
    }
    assert all(not Path(hit.path).is_absolute() for hit in hits)
    assert "Partial precedent" not in {hit.title for hit in hits}, (
        "FAIL AC-1: natural terms are ANDed; a document missing one term must not match"
    )

    title_hits = _search(host, "titleonly")
    assert [hit.title for hit in title_hits] == ["Intent titleonly"]
    body_hits = _search(host, "bodyonly")
    assert [hit.title for hit in body_hits] == ["Intent titleonly"]

    literal_hit = module.SearchHit(
        kind="feature-spec-section",
        path="docs/features/channels.md",
        anchor="literal-tokens",
        title="Literal tokens",
        body='\nOR foo"bar error* and \ue000 are ordinary authored text.\n\n',
    )
    for query in ("OR", 'foo"bar', "error*", "\ue000"):
        assert _search(host, query) == (literal_hit,), (
            f"FAIL AC-1: {query!r} became executable FTS syntax"
        )
    assert _search(host, "***") == (), (
        "FAIL AC-1: tokenizer-empty punctuation must be a successful no-hit query"
    )


def test_ac02_commonmark_extraction_and_anchor_contract(tmp_path: Path) -> None:
    """AC-2: source maps, inline text, duplicate anchors, CRLF, and H1 shape."""
    host = _make_host(tmp_path)
    spec = host / "docs" / "features" / "truth.md"
    _write(
        spec,
        """---
type: feature-spec
---
# Document title

## Résumé *Café*

truthmarker first body

```markdown
## Phantom heading
truthmarker fenced body
```

## Résumé *Café*

truthmarker second body

### Child `API`

truthmarker child body
""",
        newline="\r\n",
    )
    _write(
        host / "docs" / "features" / "no-sections.md",
        "# Only H1\n\nnosectionmarker\n",
    )
    _write(
        host / "docs" / "patterns" / "rail.md",
        """---
kind: pattern
---
# Typed **Boundary** Rail

**Use when:** patternmarker applies.
""",
    )
    _write(
        host / "docs" / "features" / "frontmatter-scalar.md",
        """---
type: feature-spec
notes: |
  ---
  ## Ghost marker
  ghostmarker remains metadata
---
# Scalar fixture

## Real section

realmarker
""",
    )

    hits = _search(host, "truthmarker")
    module = _search_module()
    assert set(hits) == {
        module.SearchHit(
            kind="feature-spec-section",
            path="docs/features/truth.md",
            anchor="résumé-café",
            title="Résumé Café",
            body=(
                "\ntruthmarker first body\n\n```markdown\n"
                "## Phantom heading\ntruthmarker fenced body\n```\n\n"
            ),
        ),
        module.SearchHit(
            kind="feature-spec-section",
            path="docs/features/truth.md",
            anchor="résumé-café-1",
            title="Résumé Café",
            body="\ntruthmarker second body\n\n",
        ),
        module.SearchHit(
            kind="feature-spec-section",
            path="docs/features/truth.md",
            anchor="child-api",
            title="Child API",
            body="\ntruthmarker child body\n",
        ),
    }
    assert "Phantom heading" not in {hit.title for hit in hits}
    first_duplicate = next(hit for hit in hits if hit.anchor == "résumé-café")
    assert "second body" not in first_duplicate.body, (
        "FAIL AC-2: a section body must end before the next H2/H3"
    )
    assert "\r" not in "".join(hit.body for hit in hits)
    assert _search(host, "nosectionmarker") == ()
    assert _search(host, "ghostmarker") == (), (
        "FAIL AC-2: an indented YAML scalar delimiter escaped frontmatter"
    )
    assert [hit.title for hit in _search(host, "realmarker")] == ["Real section"]

    pattern_hits = _search(host, "patternmarker")
    assert pattern_hits == (
        module.SearchHit(
            kind="pattern",
            path="docs/patterns/rail.md",
            anchor="typed-boundary-rail",
            title="Typed Boundary Rail",
            body="\n**Use when:** patternmarker applies.\n",
        ),
    )


def test_ac03_query_limits_and_total_order(tmp_path: Path) -> None:
    """AC-3 kernel half: Porter matching, explicit bounds, and total order."""
    host = _make_host(tmp_path)
    for index in range(30):
        _write(
            host / "docs" / "features" / f"{index:02d}.md",
            "# Fixture\n\n## Rank signal\n\nrank signal\n",
        )
    _write(
        host / "docs" / "features" / "porter.md",
        "# Fixture\n\n## Language\n\nErrors and résumé handling.\n",
    )
    _write(
        host / "docs" / "features" / "relevance-a-weak.md",
        "# Fixture\n\n## Ranking\n\nrelevance\n",
    )
    _write(
        host / "docs" / "features" / "relevance-z-strong.md",
        "# Fixture\n\n## Ranking\n\n"
        "relevance relevance relevance relevance relevance relevance\n",
    )
    _write(
        host / "docs" / "features" / "fieldone-a-body.md",
        "# Fixture\n\n## Plain\n\nfieldone\n",
    )
    _write(
        host / "docs" / "features" / "fieldone-z-title.md",
        "# Fixture\n\n## Fieldone\n\nplain\n",
    )
    _write(
        host / "docs" / "features" / "fieldtwo-a-title.md",
        "# Fixture\n\n## Fieldtwo\n\nplain\n",
    )
    _write(
        host / "docs" / "features" / "fieldtwo-z-body.md",
        "# Fixture\n\n## Plain\n\nfieldtwo\n",
    )

    limited = _search(host, "rank signal", limit=25)
    repeated = _search(host, "rank signal", limit=25)
    assert limited == repeated
    assert len(limited) == 25
    assert [hit.path for hit in limited] == sorted(hit.path for hit in limited)
    assert _search(host, "rank signal", limit=1) == limited[:1]
    assert [hit.title for hit in _search(host, "error")] == ["Language"]
    assert [hit.title for hit in _search(host, "resume")] == ["Language"]

    module = _search_module()
    assert _search(host, "relevance") == (
        module.SearchHit(
            kind="feature-spec-section",
            path="docs/features/relevance-z-strong.md",
            anchor="ranking",
            title="Ranking",
            body=("\nrelevance relevance relevance relevance relevance relevance\n"),
        ),
        module.SearchHit(
            kind="feature-spec-section",
            path="docs/features/relevance-a-weak.md",
            anchor="ranking",
            title="Ranking",
            body="\nrelevance\n",
        ),
    )
    assert _search(host, "relevance", limit=1)[0].path.endswith("z-strong.md")
    assert _search(host, "fieldone") == (
        module.SearchHit(
            kind="feature-spec-section",
            path="docs/features/fieldone-a-body.md",
            anchor="plain",
            title="Plain",
            body="\nfieldone\n",
        ),
        module.SearchHit(
            kind="feature-spec-section",
            path="docs/features/fieldone-z-title.md",
            anchor="fieldone",
            title="Fieldone",
            body="\nplain\n",
        ),
    )
    assert _search(host, "fieldtwo") == (
        module.SearchHit(
            kind="feature-spec-section",
            path="docs/features/fieldtwo-a-title.md",
            anchor="fieldtwo",
            title="Fieldtwo",
            body="\nplain\n",
        ),
        module.SearchHit(
            kind="feature-spec-section",
            path="docs/features/fieldtwo-z-body.md",
            anchor="plain",
            title="Plain",
            body="\nfieldtwo\n",
        ),
    )

    hit_fields = {field.name for field in dataclasses.fields(limited[0])}
    assert hit_fields == {"kind", "path", "anchor", "title", "body"}, (
        "FAIL AC-3: rank scores and recency are private ordering inputs, "
        "not SearchHit fields"
    )


def test_ac05_in_memory_search_is_fresh_and_writes_nothing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC-5: each call rereads current Markdown and leaves no artifact."""
    host = _make_host(tmp_path)
    source = host / "docs" / "features" / "fresh.md"
    _write(source, "# Fixture\n\n## Freshness\n\noldmarker\n")

    module = _search_module()
    real_connect = module.sqlite3.connect
    connection_targets: list[str] = []

    def traced_connect(target: str, *args, **kwargs):
        connection_targets.append(target)
        return real_connect(target, *args, **kwargs)

    monkeypatch.setattr(module.sqlite3, "connect", traced_connect)
    before_first = snapshot_tree(host)
    assert _search(host, "oldmarker") == (
        module.SearchHit(
            kind="feature-spec-section",
            path="docs/features/fresh.md",
            anchor="freshness",
            title="Freshness",
            body="\noldmarker\n",
        ),
    )
    assert snapshot_tree(host) == before_first

    _write(source, "# Fixture\n\n## Freshness\n\nnewmarker\n")
    before_second = snapshot_tree(host)
    assert _search(host, "oldmarker") == ()
    assert _search(host, "newmarker") == (
        module.SearchHit(
            kind="feature-spec-section",
            path="docs/features/fresh.md",
            anchor="freshness",
            title="Freshness",
            body="\nnewmarker\n",
        ),
    )
    assert snapshot_tree(host) == before_second, (
        "FAIL AC-5: search created or modified a host artifact"
    )
    assert connection_targets and set(connection_targets) == {":memory:"}, (
        f"FAIL AC-5: search opened a persistent SQLite target: {connection_targets}"
    )
