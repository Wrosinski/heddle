# Autonomy Addendum

No user is present in this headless phase session.

Apply the decision procedure to every open question:

1. Use the brief's pre-resolved decisions.
2. Use ratified contracts and D-numbered decisions.
3. Use `docs/workflow/engineering-principles.md`.
4. When a principle decides an ordinary reversible choice, record it through
   `heddle decisions record-policy --from-file <path>` and proceed after success.
5. When principles conflict or none applies, choose the most reversible option
   and use that command with basis `conflict` or `none` and `review_flag: "[REVIEW]"`.
6. When the choice is irreversible, touches a ratified contract, or sets
   cross-feature precedent, escalate instead of choosing.
7. When policy cannot decide, escalate by artifact with
   `heddle decisions add --input-json`; never silently proceed.

Preserve actor and authority in the existing rationale. An explicit user answer
and a lead applying a specifically identified standing user grant both use
`heddle decisions resolve` and therefore record `resolution_source: user`; that
field names the resolution path, not who reasoned through the choice. Say which
case occurred. For a delegated resolution, name the lead, the grant and scope it
covers, the choice, and why it is within that authority. Generic autonomy or
this addendum alone is not a standing user grant. When no applicable grant can
be identified, escalate through step 7. Ordinary reversible choices derived
from engineering principles continue through `record-policy`, with the policy
fact and matching journal entry as their authority record.

The policy command accepts a closed `heddle.policy-batch/v1` JSON document with
`schema` and a nonempty `resolutions` array. Each entry supplies `id`, `title`,
`question`, `options`, `choice`, `rationale`, `basis`, `reversibility`, `confidence`,
`scope` and `routes_to`; only `review_flag` is optional. Use nonempty single-line
strings and nonempty option/path arrays, a whitespace-free unique ID, a choice
from the options, `reversibility: "reversible"`, confidence `high`, `medium` or
`low`, and scope `feature` or the current milestone ID. Name the decisive
principle in `basis`. The command owns the matching journal entry and policy fact;
retain the input for an identical retry if publication is interrupted. Read its
help for the complete contract. Session stdout does not record policy decisions.

Autonomy is decision authority, not scope authority: the per-part
authorization record in the spec's Approved MVP block and the
ancillary-tooling rule (the `necessity-anchor.md` prompt partial, spliced
into the gate prompts that review scope) still bind. An autonomous
resolution picks among options for already-authorized work; it never
expands scope. Scope expansion always escalates (step 7).
