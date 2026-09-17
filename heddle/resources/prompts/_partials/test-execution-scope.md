Test execution follows this role's action limits and the user's authorized
scope. Select the narrowest existing tests covering changed behavior, named
contracts and directly affected consumers. Consult the host's test-selection
map when available and check actual fixtures and dependencies; missing mappings
require investigation, not a no-tests assumption or automatic full-suite run.

Start with the host's chosen time budget. Within it, select core behavior,
uncertain, complex or risky interactions, and consequential failures at the
cheapest adequate layer. Keep independently selectable fast feedback and fast
public-boundary acceptance. When required proof exceeds the budget, record a
measured, scoped exception with its cause, owner and review point; do not omit it.

Fast does not establish relevance, and hermetic does not establish low cost.
Inspect setup, subprocesses and verifier dependencies: a narrow wrapper around
a broad suite is a broad execution. Include related slow cases only for their
distinct affected contract; an acceptance label alone is not a selection reason.

Full-repository and broad cross-feature execution requires an explicit request
covering that scope. Phase exit, review, pre-merge, final verification and stored
plan commands do not independently grant it. Both local full-workflow e2e and
real external tests require explicit execution authority and exact targets.
Normally schedule them after implementation or at final verification; earlier
execution needs a specific request. Confirmed prerequisites are not permission.
Existing applicable grants persist; do not request them again.

Use the host's configured runner and native verification scopes. Preserve
required proof: a focused pass cannot replace a broader obligation, and skipped,
excluded or unexecuted required cases are not passing evidence. Report required
pending proof separately from optional unrequested regression. Reuse applicable
evidence under its source/command/environment rules and rerun affected checks
after fixes. This guidance never authorizes actions forbidden to the review role.
