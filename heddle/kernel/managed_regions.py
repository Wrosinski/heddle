"""Pure exact-line managed region parsing and replacement, shared with gate inputs."""

from __future__ import annotations

from heddle.kernel.project_config import KernelError

PLAN_STATUS_ID = "plan-status"


def begin_marker(block_id: str) -> str:
    return f"<!-- heddle:begin {block_id} -->"


def end_marker(block_id: str) -> str:
    return f"<!-- heddle:end {block_id} -->"


def _lf_lines(text: str) -> list[str]:
    parts = text.split("\n")
    lines = [part + "\n" for part in parts[:-1]]
    if parts[-1]:
        lines.append(parts[-1])
    return lines


def _line_content(line: str) -> str:
    if line.endswith("\r\n"):
        return line[:-2]
    if line.endswith("\n"):
        return line[:-1]
    return line


def _marker_indexes(text: str, block_id: str) -> tuple[list[str], list[int], list[int]]:
    lines = _lf_lines(text)
    begin = begin_marker(block_id)
    end = end_marker(block_id)
    begin_indexes = [
        index for index, line in enumerate(lines) if _line_content(line) == begin
    ]
    end_indexes = [
        index for index, line in enumerate(lines) if _line_content(line) == end
    ]
    return lines, begin_indexes, end_indexes


def _marker_fault_from_indexes(
    begin_indexes: list[int],
    end_indexes: list[int],
) -> str | None:
    if not begin_indexes and not end_indexes:
        return "absent"
    if not begin_indexes:
        return "end-only"
    if not end_indexes:
        return "begin-only"
    if len(begin_indexes) > 1:
        return "duplicate-begin"
    if len(end_indexes) > 1:
        return "duplicate-end"
    if begin_indexes[0] > end_indexes[0]:
        return "reversed"
    return None


def marker_fault_kind(text: str, block_id: str) -> str | None:
    """Classify one managed marker pair using sync's exact line semantics."""
    _lines, begin_indexes, end_indexes = _marker_indexes(text, block_id)
    return _marker_fault_from_indexes(begin_indexes, end_indexes)


def _marker_fault(block_id: str, fault: str) -> KernelError:
    begin = begin_marker(block_id)
    end = end_marker(block_id)
    if fault == "absent":
        message = f"target has no `{block_id}` managed block"
        hint = f"add the exact pair `{begin}` and `{end}`, then retry"
    elif fault == "begin-only":
        message = f"target has a `{block_id}` BEGIN marker but no END marker"
        hint = f"add the missing END line `{end}`, then retry"
    elif fault == "end-only":
        message = f"target has a `{block_id}` END marker but no BEGIN marker"
        hint = f"add the missing BEGIN line `{begin}`, then retry"
    elif fault == "duplicate-begin":
        message = f"target has a duplicate `{block_id}` BEGIN marker"
        hint = f"remove the duplicate BEGIN line so exactly one `{begin}` remains"
    elif fault == "duplicate-end":
        message = f"target has a duplicate `{block_id}` END marker"
        hint = f"remove the duplicate END line so exactly one `{end}` remains"
    else:
        message = f"target has the `{block_id}` marker pair in reversed order"
        hint = f"put `{begin}` before `{end}` to restore the correct order"
    return KernelError(code="workspace-invalid", message=message, hint=hint)


def replace_managed_region(text: str, block_id: str, body: str) -> str:
    """Replace the bytes strictly between one exact managed-marker pair."""
    lines, begin_indexes, end_indexes = _marker_indexes(text, block_id)
    fault = _marker_fault_from_indexes(begin_indexes, end_indexes)
    if fault is not None:
        raise _marker_fault(block_id, fault)

    begin_index = begin_indexes[0]
    end_index = end_indexes[0]
    return "".join(lines[: begin_index + 1]) + body + "".join(lines[end_index:])


def without_managed_region(text: str, block_id: str) -> str:
    """Exclude one valid native region; malformed or inline markers stay authored."""
    lines, begins, ends = _marker_indexes(text, block_id)
    if _marker_fault_from_indexes(begins, ends) is not None:
        return text
    return "".join(lines[: begins[0]] + lines[ends[0] + 1 :])
