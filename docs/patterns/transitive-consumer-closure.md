---
name: transitive-consumer-closure
area: architecture
status: active
created: 2026-09-03
superseded_by: null
---

# Transitive consumer closure before shared changes

**Intent:** identify every executable surface affected by shared content or
code, so a convenient source file is not mistaken for the complete consumer
set and one successful family cannot hide regressions elsewhere.

**When to use:** a proposed change targets a partial, library, base template,
configuration fragment, generated input, or helper reached by multiple public
or executable leaves. Not needed for a proven local leaf with no incoming
dependency edge.

**Recipe:**

1. **Choose the authoritative leaves.** Enumerate executable or public
   consumers from the runtime registry, command table, build graph, or another
   production-owned source. Do not derive the expected set from the proposal
   record being validated.
2. **Model the real dependency edges.** Parse or resolve template includes,
   partial references, imports, generated inputs, and aliases using the same
   semantics production uses. Record shared files as sources or anchors, not
   as substitutes for their downstream consumers.
3. **Walk reverse edges to closure.** Starting at every changed shared node,
   traverse until registered leaves are reached. Deduplicate cycles and aliases
   deterministically, then compare the derived leaves with the recorded
   consumer set by exact equality.
4. **Partition consumers by behavior.** Assign every affected leaf to exactly
   one materially distinct family and reject omissions, duplicates, and
   unclassified leaves. File location or a common name is not evidence that two
   leaves have the same behavioral risk.
5. **Pin one representative case per family.** Keep a fixed compatibility case
   and rubric for every family, in addition to any candidate-specific case.
   Shared placement is ineligible while a family lacks evidence; retain the
   change locally or inertly instead.
6. **Make registry growth fail loudly.** The test derives the current closure
   from production metadata and exact-set compares it with the record. Adding a
   new registered consumer must fail until its family and case are classified.

**Anti-patterns / caveats:** text-searching only for the candidate wording;
listing the shared partial as its own consumer; validating a recorded set
against itself; stopping after direct references; using one convenient gate to
stand in for several behavior families; or silently ignoring conditional and
generated edges. If production resolution is dynamic and cannot be reproduced,
default to local placement rather than claiming a complete shared census.

**Concrete future-feature scenario:** a shared serialization fragment feeds
four generated reports, two CLI summaries, and an API response through nested
includes. A future edit derives all seven registered leaves transitively,
partitions them by output contract, and runs one fixed compatibility case per
family before promoting the shared change.
