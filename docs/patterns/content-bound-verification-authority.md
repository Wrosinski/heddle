---
name: content-bound-verification-authority
area: architecture
status: active
created: 2026-09-05
superseded_by: null
---

# Content-bound verification authority

**Intent:** make a passing verification record prove which command and relevant
content actually passed, so later drift cannot silently reuse stale authority.

**When to use:** a test, benchmark, deployment check, migration rehearsal, or
other command authorizes a later transition and the checked inputs may change
between execution and use. It is unnecessary for disposable observations that
grant no authority.

**Recipe:**

1. Define the relevant source set explicitly and normalize it independently of
   checkout location. Include path membership, file kind, executable mode, and
   bytes where those properties affect behavior.
2. Bind the exact stored command and normalized source-set definition into the
   fact. Git revision and dirty state are useful diagnostics, but relevant
   content identity is the freshness authority.
3. Observe the same source set immediately before and after the real command.
   Run the command outside state locks; only a zero exit with equal before and
   after identities can create a passing authoritative fact.
4. Record failed, unstable, publication-failed, and conflicted attempts
   truthfully without letting them authorize the transition.
5. Centralize current freshness assessment. Every writer, read surface,
   transition, validator, and autonomous driver consumes the same reasoned
   result for command, source-set, content, legacy, and stability drift.
6. Re-evaluate freshness at the boundary that consumes it. Do not trust a
   status snapshot captured earlier than the transition.
7. If the claim must survive ephemeral-workspace cleanup, publish a compact,
   semantically validated, create-only receipt and read it back before cleanup.
   Once terminal acceptance exists, that durable receipt is the authority; do
   not route the completed workflow back through deleted ephemeral facts.
8. Report provenance separately from command adequacy, semantic quality,
   operational reliability, and tool behavior. A perfectly bound weak test is
   still a weak test.

**Anti-patterns / caveats:** binding only Git `HEAD`; hashing after the command
without a before observation; accepting exit zero without source stability;
hashing the whole repository when only declared inputs matter; reconstructing
identity later in a validator; duplicating freshness predicates; treating raw
logs as authority; or claiming provenance green implies sufficient coverage.
Source-set resolution and reads need containment, race resistance, and explicit
resource ceilings.

**Concrete future-feature scenario:** a future `heddle deploy qualify` command
binds its deployment test command and normalized service/config inputs, observes
them on both sides of execution, records any unstable attempt, and publishes a
compact receipt before the qualification workspace is removed. A config edit
then makes the old fact stale even if the branch name and Git commit metadata
look unchanged.
