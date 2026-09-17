"""Command-manifest activation contracts for kickoff, doctor, and run-gate."""

from __future__ import annotations


def _command_surface_landed() -> bool:
    from heddle.runtime.contracts import COMMAND_SURFACE

    by_name = {c.name: c for c in COMMAND_SURFACE}
    kickoff = by_name.get("kickoff")
    doctor = by_name.get("doctor")
    return bool(kickoff and kickoff.output_schema) and bool(
        doctor and doctor.output_schema
    )


# Feature landed: a regression that removed the final surface must FAIL here, not
# silently skip the module (review integration; plan write T5 — retire red-phase
# skip sentinels to hard asserts at completion).
assert _command_surface_landed(), (
    "doctor and the current manifest must be active — a regression here "
    "must fail loudly, not skip this module"
)


def _by_name():
    from heddle.runtime.contracts import COMMAND_SURFACE

    return {c.name: c for c in COMMAND_SURFACE}


def test_ac16_kickoff_and_doctor_gain_pinned_output_schemas() -> None:
    # AC-16: kickoff/doctor flip to live with pinned output_schema ids.
    by_name = _by_name()
    assert by_name["kickoff"].output_schema and (
        by_name["kickoff"].output_schema.startswith("heddle.kickoff/")
    ), (
        f"FAIL: kickoff.output_schema must be a pinned heddle.kickoff/* id, "
        f"got {by_name['kickoff'].output_schema!r} (REQ-18)"
    )
    assert by_name["doctor"].output_schema and (
        by_name["doctor"].output_schema.startswith("heddle.doctor/")
    ), (
        f"FAIL: doctor.output_schema must be a pinned heddle.doctor/* id, "
        f"got {by_name['doctor'].output_schema!r} (REQ-18)"
    )


def test_ac16_run_gate_gains_json_and_feature_flags() -> None:
    # AC-16: run-gate gains --json/--feature (the gate envelope additive flags).
    run_gate = _by_name()["run-gate"]
    flag_names = {f.name for f in run_gate.flags}
    assert {"--json", "--feature"} <= flag_names, (
        f"FAIL: run-gate flags {sorted(flag_names)} must include "
        "--json/--feature (REQ-18/AC-6)"
    )


def test_ac16_no_other_manifest_entry_changed() -> None:
    # AC-16: additive only — the reader-pinned schemas stay put and, after validation
    # has
    # landed (validate/migrate output schemas pinned), every future (authoring+) stub
    # keeps output_schema None.
    by_name = _by_name()
    pinned_m2 = {
        "orient": "heddle.orient/v0",
        "status": "heddle.status/v1",
        "feature switch": "heddle.feature-switch/v0",
        "help": "heddle.manifest/v0",
    }
    for name, schema in pinned_m2.items():
        assert by_name[name].output_schema == schema, (
            f"FAIL: {name}.output_schema changed to "
            f"{by_name[name].output_schema!r}; M2 pinned {schema!r} — the flip "
            "must not touch other entries (AC-16)"
        )


def test_ac16_manifest_serializes_the_flip(run_cli, envelope_tools) -> None:
    # AC-16: the change is observable over the wire — `help --json` shows the
    # flipped entries (the manifest is the ratified surface, manifest lockstep).
    code, out, _err = run_cli(["help", "--json"])
    assert code == 0
    commands = {c["name"]: c for c in envelope_tools.parse(out)["data"]["commands"]}
    assert commands["kickoff"]["output_schema"], "FAIL: wire manifest missing "
    assert commands["doctor"]["output_schema"], "FAIL: wire manifest missing "
    rg_flags = {f["name"] for f in commands["run-gate"]["flags"]}
    assert {"--json", "--feature"} <= rg_flags, (
        f"FAIL: wire manifest run-gate flags {sorted(rg_flags)} missing "
        "--json/--feature"
    )
