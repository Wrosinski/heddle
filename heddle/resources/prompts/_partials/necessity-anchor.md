Necessity is calibrated symmetrically with completeness: an unnecessary
component, bespoke tooling where a proven tool suffices, or a speculative
capability nothing requires is an Important finding — classified IMPLEMENT
when a spec/plan edit removes it, REPORT when it is genuinely a scope
decision ("Core first; scope is earned"). Hard edges:

- A justification by end-to-end runnability/testability counts only when it
  names the concrete test or entrypoint that needs the component; an unnamed
  "needed for testability" does not count.
- A component that traces to no acceptance criterion and no named
  runnability/testability need is converge-blocking, not advisory: raise it
  and keep it open until the component traces or moves to Deferred Scope.
- Borderline items default into Deferred Scope — the burden of proof is on
  inclusion, per the ratified "Core first; scope is earned"
  (`docs/workflow/engineering-principles.md`).
- Ancillary tooling requires recorded owner authorization. The taxonomy:
  verification/proof ceremonies beyond ordinary tests; transition control
  planes beyond a minimal one-time script; report/reviewer/feedback
  artifacts without a named consumer; portability, tamper-resistance, or
  hostile-local defenses without a named threat actor in the spec's trust
  model; attribution/telemetry substrates serving only such tooling;
  generated artifacts without a named external consumer; and guards
  protecting any of the above. A component matching the taxonomy must point
  to an explicit owner authorization recorded in the spec (a Decision Log
  entry or the Approved MVP block); absent that record, flag it as a REPORT
  finding with Deferred Scope as the default disposition — the owner
  decides, never an auto-applied inclusion.
