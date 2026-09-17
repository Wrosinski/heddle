"""Repository-only pytest execution-band authority."""

from __future__ import annotations

import os
import shlex
from dataclasses import dataclass, field
from pathlib import Path

import pytest

BANDS = ("fast", "routine", "toolchain", "e2e", "live")
PERMISSIONS = ("--allow-e2e", "--allow-live")


@dataclass(frozen=True)
class Invocation:
    targets: tuple[str, ...]
    band: str
    allow_e2e: bool
    allow_live: bool


INVOCATION = pytest.StashKey[Invocation]()


@dataclass(eq=False)
class Proof:
    requested: set[str] = field(default_factory=set)
    excluded: set[str] = field(default_factory=set)
    reports: dict[str, list[pytest.TestReport]] = field(default_factory=dict)
    collection_failed: bool = False
    succeeded: bool = False

    def pytest_collectreport(self, report: pytest.CollectReport) -> None:
        if report.failed or report.skipped:
            self.collection_failed = True

    def pytest_runtest_logreport(self, report: pytest.TestReport) -> None:
        self.reports.setdefault(report.nodeid, []).append(report)

    def counts(self) -> dict[str, int]:
        counts = {
            "requested": len(self.requested),
            "excluded": len(self.excluded),
            "executed": 0,
            "passed": 0,
            "failed": 0,
            "skipped/xfail": 0,
            "incomplete": 0,
            "unexpected": len(self.reports.keys() - self.requested),
        }
        for node in self.requested:
            reports = self.reports.get(node, [])
            if reports:
                counts["executed"] += 1
            if any(report.failed for report in reports):
                counts["failed"] += 1
            elif any(
                report.skipped or hasattr(report, "wasxfail") for report in reports
            ):
                counts["skipped/xfail"] += 1
            elif (
                len(reports) == 3
                and {report.when for report in reports} == {"setup", "call", "teardown"}
                and all(report.passed for report in reports)
            ):
                counts["passed"] += 1
            else:
                counts["incomplete"] += 1
        return counts

    def complete(self) -> bool:
        counts = self.counts()
        return bool(
            self.requested
            and not self.collection_failed
            and not self.excluded
            and not counts["unexpected"]
            and counts["passed"] == counts["requested"]
        )

    @pytest.hookimpl(wrapper=True, tryfirst=True)
    def pytest_sessionfinish(self, session: pytest.Session):
        result = yield
        if session.exitstatus == pytest.ExitCode.OK and not self.complete():
            session.exitstatus = pytest.ExitCode.TESTS_FAILED
        terminal = session.config.pluginmanager.getplugin("terminalreporter")
        if terminal is not None:
            counts = " ".join(
                f"{name}={value}" for name, value in self.counts().items()
            )
            verdict = "PASS" if session.exitstatus == pytest.ExitCode.OK else "FAIL"
            terminal.write_sep("-", f"strict proof {verdict}: {counts}")
        self.succeeded = session.exitstatus == pytest.ExitCode.OK and self.complete()
        return result


PROOF = pytest.StashKey[Proof]()


def pytest_addoption(parser: pytest.Parser) -> None:
    group = parser.getgroup("repository test policy")
    group.addoption("--test-band", choices=BANDS, default="fast")
    group.addoption("--allow-e2e", action="store_true", default=False)
    group.addoption("--allow-live", action="store_true", default=False)
    group.addoption("--strict-proof", action="store_true", default=False)


@pytest.hookimpl(tryfirst=True)
def pytest_cmdline_main(config: pytest.Config) -> None:
    informational = (
        "help",
        "version",
        "markers",
        "showfixtures",
        "show_fixtures_per_test",
    )
    if config.getoption("strict_proof") and any(
        config.getoption(option, default=False) for option in informational
    ):
        raise pytest.UsageError(
            "strict proof requires execution, not an informational pytest command"
        )


def _exact_targets(config: pytest.Config, targets: tuple[str, ...]) -> bool:
    return bool(targets) and all(
        (path := Path(target.split("::", 1)[0])).suffix == ".py"
        and (config.invocation_params.dir / path).is_file()
        for target in targets
    )


def _direct_exact_request(config: pytest.Config, invocation: Invocation) -> bool:
    return _exact_targets(config, invocation.targets) and all(
        target in invocation.targets for target in config.args
    )


def _node_contains(parent: str, child: str) -> bool:
    if parent == child:
        return True
    # A parameter ID can itself contain brackets and '::'; it is never a parent.
    return "[" not in parent and child.startswith((parent + "::", parent + "["))


def _coalesce_targets(config: pytest.Config) -> None:
    selections = []
    for target in config.args:
        path, _, node = target.partition("::")
        selections.append(
            (Path(os.path.abspath(config.invocation_params.dir / path)), node)
        )
    keep = []
    for index, (path, node) in enumerate(selections):
        covered = any(
            other_path == path
            and (not other_node or _node_contains(other_node, node))
            and (other_node != node or other_index < index)
            for other_index, (other_path, other_node) in enumerate(selections)
            if other_index != index
        )
        if not covered:
            keep.append(config.args[index])
    # Pytest 8 can narrow file+node overlaps before modifyitems. Present the
    # equivalent union without covered descendants to its ordinary collector.
    config.args[:] = keep


def _configure_proof(config: pytest.Config, invocation: Invocation) -> None:
    # Pytest derives its initial conftest cutoff from the configuration/root.
    # These parsed options include inherited inputs, not just direct argv.
    if config.getoption("inifilename") or config.getoption("rootdir"):
        raise pytest.UsageError(
            "strict proof does not support -c/--config-file or --rootdir overrides; "
            "use the normally discovered configuration and exact targets"
        )
    if not _direct_exact_request(config, invocation):
        raise pytest.UsageError(
            "strict proof requires direct exact existing Python files or nodes"
        )
    unsupported = (
        "ignore",
        "ignore_glob",
        "pyargs",
        "collectonly",
        "runxfail",
        "lf",
        "stepwise",
        "doctestmodules",
        "doctestglob",
        "noconftest",
        "confcutdir",
    )
    if any(config.getoption(option, default=False) for option in unsupported):
        raise pytest.UsageError(
            "strict proof does not support collection-only, collection-shaping, "
            "cache-pruning or xfail overrides; use complete exact targets"
        )
    definitions = {
        "python_files": ["test_*.py", "*_test.py"],
        "python_classes": ["Test"],
        "python_functions": ["test"],
    }
    if any(config.getini(name) != expected for name, expected in definitions.items()):
        raise pytest.UsageError(
            "strict proof requires standard Python collection definitions; "
            "remove alternate definitions and use exact targets"
        )
    _coalesce_targets(config)
    proof = Proof()
    config.stash[PROOF] = proof
    config.pluginmanager.register(proof, "repository-strict-proof")


def pytest_configure(config: pytest.Config) -> None:
    inherited = [
        *config.getini("addopts"),
        *shlex.split(os.getenv("PYTEST_ADDOPTS", "")),
    ]
    if any(
        token.startswith("@") or token.split("=", 1)[0] in PERMISSIONS
        for token in inherited
    ):
        raise pytest.UsageError(
            "permission flags and response files in addopts are forbidden; "
            "remove inherited inputs "
            "and request exact targets with direct permission"
        )
    if any(token.startswith("@") for token in config.invocation_params.args):
        raise pytest.UsageError(
            "response files cannot supply direct permission or exact targets; "
            "put options and files/nodes directly in the invocation"
        )
    # Reuse pytest's option grammar to distinguish target operands from values
    # of -k/-m/-o/etc. This isolated private seam avoids a second CLI parser.
    direct = config._parser.parse(list(config.invocation_params.args))
    invocation = Invocation(
        tuple(direct.file_or_dir),
        config.getoption("test_band"),
        direct.allow_e2e,
        direct.allow_live,
    )
    config.stash[INVOCATION] = invocation
    if config.getoption("strict_proof"):
        _configure_proof(config, invocation)
    if config.getoption("collectonly"):
        return
    if invocation.band in ("e2e", "live"):
        if not _direct_exact_request(config, invocation):
            raise pytest.UsageError(
                "execution requires direct exact existing Python files or nodes; "
                "directories, implicit roots and inherited targets are not requests"
            )
        allowed = (
            invocation.allow_e2e if invocation.band == "e2e" else invocation.allow_live
        )
        if not allowed:
            raise pytest.UsageError(
                f"permission required: --test-band {invocation.band} needs direct "
                f"--allow-{invocation.band} and exact targets"
            )


def _eligible(item: pytest.Item, band: str) -> bool:
    marks = {mark.name for mark in item.iter_markers()}
    if band == "fast":
        return not marks.intersection({"slow", "toolchain", "e2e", "live"})
    if band == "routine":
        return not marks.intersection({"toolchain", "e2e", "live"})
    if band == "toolchain":
        return "toolchain" in marks and not marks.intersection({"e2e", "live"})
    if band == "e2e":
        return "e2e" in marks and "live" not in marks
    return "live" in marks


def _requested_node(item: pytest.Item, config: pytest.Config) -> bool:
    for target in config.stash[INVOCATION].targets:
        path, separator, node = target.partition("::")
        # Match pytest's lexical absolute path, retaining a requested symlink.
        target_path = Path(os.path.abspath(config.invocation_params.dir / path))
        if not separator or item.path != target_path:
            continue
        relative = item.nodeid.partition("::")[2]
        if _node_contains(node, relative):
            return True
    return False


def _require_resolved_targets(
    config: pytest.Config, requested: list[pytest.Item]
) -> None:
    by_path: dict[Path, set[str]] = {}
    for item in requested:
        by_path.setdefault(item.path, set()).add(item.nodeid.partition("::")[2])
    for target in config.stash[INVOCATION].targets:
        path, _, node = target.partition("::")
        candidates = by_path.get(
            Path(os.path.abspath(config.invocation_params.dir / path)), set()
        )
        if not candidates or (
            node
            and not any(_node_contains(node, candidate) for candidate in candidates)
        ):
            raise pytest.UsageError(
                f"strict proof exact target resolved no witnesses: {target}"
            )


@pytest.hookimpl(wrapper=True, tryfirst=True)
def pytest_collection_modifyitems(
    session: pytest.Session, config: pytest.Config, items: list[pytest.Item]
):
    proof = config.stash.get(PROOF, None)
    if proof is not None:
        # Overlapping files/functions/parameter targets name unique witnesses,
        # not additional executions of an already requested node.
        unique: dict[str, pytest.Item] = {}
        for item in items:
            unique.setdefault(item.nodeid, item)
        items[:] = unique.values()
        proof.requested = set(unique)
    requested = list(items)
    yield
    if proof is not None:
        _require_resolved_targets(config, requested)
    invocation = config.stash[INVOCATION]
    collect_only = config.getoption("collectonly")
    if not collect_only:
        selected_items = set(items)
        for item in requested:
            external = item.get_closest_marker("live") is not None
            workflow = item.get_closest_marker("e2e") is not None
            selected = item in selected_items and _eligible(item, invocation.band)
            if not (_requested_node(item, config) or selected):
                continue
            if (external or workflow) and (
                not _eligible(item, invocation.band)
                or (external and not invocation.allow_live)
                or (workflow and not invocation.allow_e2e)
            ):
                raise pytest.UsageError(
                    "permission denied before fixtures: "
                    f"{item.nodeid}; choose its band and direct required permission(s)"
                )
    excluded = [item for item in items if not _eligible(item, invocation.band)]
    if excluded:
        config.hook.pytest_deselected(items=excluded)
        excluded_ids = {id(item) for item in excluded}
        items[:] = [item for item in items if id(item) not in excluded_ids]
    if proof is not None:
        proof.excluded = proof.requested - {item.nodeid for item in items}
        if proof.collection_failed or not proof.requested or proof.excluded:
            raise pytest.UsageError(
                "strict proof requires a nonempty complete collection: "
                f"requested={len(proof.requested)} excluded={len(proof.excluded)}; "
                "remove filters/exclusions or author an explicit file/node subset"
            )
