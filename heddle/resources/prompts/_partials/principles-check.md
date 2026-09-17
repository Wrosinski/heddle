Engineering preference is governed by the ratified
`docs/workflow/engineering-principles.md`. Verify each proposed addition or
suggested change against the principles and name the decisive principle in
the finding. The ones this review most often turns on:

- "Core first; scope is earned" — the smallest end-to-end slice ships
  first; additions carry the burden of proof and default to named, deferred
  follow-ups.
- "Simplicity first; the contract sets the robustness floor" — the contract
  defines the robustness that is required; beyond it, between two
  contract-meeting options the simpler wins.
- "Reversibility beats optimality" — take the option cheapest to undo;
  spend design effort where change is expensive.
- "Structural beats policed" — make bad states impossible at the defining
  contract rather than guarding downstream.
- "Proven tools beat bespoke machinery" — a production-grade existing
  solution beats building from scratch.
- Architecture: apply "One owner per authority" to policy and state ownership,
  "Dependencies point inward, not back out" to boundary crossings, and
  "Extend through data and one resolver, not parallel paths" to extension seams.
  Use the ratified definitions and concrete consumers; these names are pointers,
  not an independent architecture standard.
- Tiebreak: when the principles are silent or tie, prefer the option that
  builds less now.
