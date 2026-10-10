"""
Review-equivalence identity seams and pre-feature v1 evidence for change impact.

Accessors assert at call time that the reviewed interfaces exist; no rule is
re-implemented here. Activation seams:

- ``heddle.kernel.review_equivalence.equivalence_identity(path, content)``,
  returning ``(rule_id, digest)`` or None, and ``review_equivalent(left,
  right)`` over two ``EvidenceObservation`` values;
- ``heddle.kernel.knowledge_search.markdown_block_ranges(text)``, returning
  half-open zero-based ``(start, end)`` file-line pairs (``Token.map``) of the
  fence, code_block and html_block tokens;
- ``EvidenceObservation.equivalence``, a ``(rule, digest)`` pair or a value
  with ``rule`` and ``digest`` attributes, None when absent;
- ``heddle.kernel.source_manifest.stamp_equivalence(evidence, contents)``,
  which records identity from the observed bytes when a record is written
  (reads build raw identity only);
- ``SourceEvidence.components``, ordered named digests built as
  ``EvidenceComponent(name, digest)`` when that type exists, else as pairs.

The ``V1_*`` constants are ``heddle.source-evidence/v1`` artifact bytes for the
synthetic host files named beside them, captured from the
``heddle.source-evidence/v1`` writer at base commit 50e72d3 before this change.
Tests write them as records made before an upgrade; they must never be
regenerated.
"""

from __future__ import annotations

import hashlib
import importlib
import importlib.util
import json
import sys
from dataclasses import fields, replace
from importlib.metadata import version
from types import ModuleType

IDENTITY_MODULE = "heddle.kernel.review_equivalence"


def identity_api() -> ModuleType:
    assert importlib.util.find_spec(IDENTITY_MODULE) is not None, (
        "FAIL AC-1/AC-2: the review-equivalence identity module "
        f"{IDENTITY_MODULE} is not implemented"
    )
    module = importlib.import_module(IDENTITY_MODULE)
    assert callable(getattr(module, "equivalence_identity", None)), (
        "FAIL AC-1/AC-2: the identity module has no "
        "equivalence_identity(path, content) function"
    )
    assert callable(getattr(module, "review_equivalent", None)), (
        "FAIL AC-1/AC-2: the identity module has no "
        "review_equivalent(left, right) predicate"
    )
    return module


def identity(path: str, content: bytes) -> tuple[str, str] | None:
    value = identity_api().equivalence_identity(path, content)
    if value is None:
        return None
    rule, digest = value
    return rule, digest


def block_ranges_api():
    from heddle.kernel import knowledge_search

    function = getattr(knowledge_search, "markdown_block_ranges", None)
    assert callable(function), (
        "FAIL AC-2: heddle.kernel.knowledge_search has no "
        "markdown_block_ranges(text) function for CommonMark block line ranges"
    )
    return function


def python_rule_id() -> str:
    return f"python-tree/1+py{sys.version_info.major}.{sys.version_info.minor}"


def markdown_rule_id() -> str:
    major, minor = version("markdown-it-py").split(".")[:2]
    return f"markdown-text/1+mdit{major}.{minor}"


def observation(path, content, *, kind="file", executable=False):
    """Observe one path as a written record holds it.

    The production builder records raw identity only; a write stamps the
    equivalence identity from the observed bytes.
    """
    from heddle.kernel.source_manifest import (
        ObservedPath,
        SourceDefinition,
        build_source_evidence,
        stamp_equivalence,
    )

    evidence = build_source_evidence(
        SourceDefinition("milestone-owns", (path,), (path,)),
        (ObservedPath(path, kind, executable, content),),
    )
    return stamp_equivalence(evidence, {path: (content,)}).observations[0]


def recorded_equivalence(row) -> tuple[str, str] | None:
    """Return an observation's recorded ``(rule, digest)``, or None when absent."""
    from heddle.kernel.source_manifest import EvidenceObservation

    assert "equivalence" in {field.name for field in fields(EvidenceObservation)}, (
        "FAIL AC-15: source evidence observations carry no equivalence identity "
        "(heddle.source-evidence/v2 is not implemented)"
    )
    value = row.equivalence
    if value is None:
        return None
    if isinstance(value, tuple):
        rule, digest = value
        return rule, digest
    return value.rule, value.digest


def with_rule(row, rule: str):
    """Restamp an observation's recorded equivalence under another rule id."""
    recorded = recorded_equivalence(row)
    assert recorded is not None, "fixture observation has no equivalence identity"
    current = row.equivalence
    restamped = (
        (rule, recorded[1])
        if isinstance(current, tuple)
        else replace(current, rule=rule)
    )
    return replace(row, equivalence=restamped)


def without_equivalence(row):
    """The same raw observation as a record made before this feature holds it."""
    recorded_equivalence(row)
    return replace(row, equivalence=None)


def evidence_components(pairs):
    """Build ``SourceEvidence.components`` from ordered ``(name, digest)`` pairs."""
    from heddle.kernel import source_manifest

    assert "components" in {
        field.name for field in fields(source_manifest.SourceEvidence)
    }, "FAIL AC-15: source evidence has no ordered named components list"
    component = getattr(source_manifest, "EvidenceComponent", None)
    if component is None:
        return tuple(pairs)
    return tuple(component(name, digest) for name, digest in pairs)


def v1_reference(content: bytes) -> dict:
    """The state reference that named a frozen v1 artifact."""
    artifact_sha256 = hashlib.sha256(content).hexdigest()
    return {
        "schema": "heddle.source-evidence-ref/v1",
        "artifact": f"verification/source-evidence.{artifact_sha256}.yaml",
        "artifact_sha256": artifact_sha256,
        "source_sha256": json.loads(content)["source_sha256"],
    }


# `src.py` holding b"value = 1\n", owned by milestone m1 of the current host.
V1_MILESTONE_SRC_PY = (
    b'{"definition":{"declaration_paths":["src.py"],"kind":"milestone-owns",'
    b'"paths":["src.py"]},"observations":[{"content_sha256":'
    b'"585c93666fcb046b7b264d3fa73202aa2a38254ae82a4b3ba19e873c2d5a9886",'
    b'"executable":false,"kind":"file","path":"src.py"}],'
    b'"schema":"heddle.source-evidence/v1","source_sha256":'
    b'"29ecd2a1cd19a652f9e02e0fbc68c058c38f961ca1b35e330b6abaad137821fb"}\n'
)
V1_MILESTONE_SRC_PY_SHA256 = (
    "d6f24db39f1b250a6af6e9103fbcea781b0aab3bc98a3f9eff816521a660d779"
)

# `vendored.py` holding b"VALUE = 'x'\n", attributed outside the feature.
V1_OUTSIDE_VENDORED_PY = (
    b'{"definition":{"declaration_paths":["vendored.py"],'
    b'"kind":"outside-feature","paths":["vendored.py"]},'
    b'"observations":[{"content_sha256":'
    b'"babe049d9f4d7b43a625a738a1aaea231699f5b7c5f62c4b39c82efbb842316d",'
    b'"executable":false,"kind":"file","path":"vendored.py"}],'
    b'"schema":"heddle.source-evidence/v1","source_sha256":'
    b'"98e7457fac2e030d263bac4f7167510c4f03007bfc7b0bcc1603b2da76a9cf73"}\n'
)
V1_OUTSIDE_VENDORED_PY_SHA256 = (
    "ef2fcf9864d18ae2e66b4d9ee3c23ba7e37d85421a144702fc8e64af410ea4b7"
)

# The cited origin Markdown file, holding b"Other feature's accepted scope.\n".
V1_OUTSIDE_ORIGIN_MD = (
    b'{"definition":{"declaration_paths":["origin.md"],'
    b'"kind":"outside-feature","paths":["origin.md"]},'
    b'"observations":[{"content_sha256":'
    b'"eaf777f0b04ca37464ac4e44c8633c72c8b6f3353d44a101de9bd6d9c82d4024",'
    b'"executable":false,"kind":"file","path":"origin.md"}],'
    b'"schema":"heddle.source-evidence/v1","source_sha256":'
    b'"34694760eb0e15d1ec66e8bd29faf83f803de67befc5170ce929b7dd84a52cda"}\n'
)
V1_OUTSIDE_ORIGIN_MD_SHA256 = (
    "e2ef43a09152dfaa5529c6c1232728fff08e685256f76861f2d646483949d8ab"
)


def frozen_v1(content: bytes, artifact_sha256: str) -> bytes:
    """Return frozen v1 bytes after checking they are still the recorded bytes."""
    assert hashlib.sha256(content).hexdigest() == artifact_sha256, (
        "frozen pre-feature v1 evidence bytes were edited"
    )
    return content
