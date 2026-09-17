---
name: tiered-resource-resolution
area: architecture
status: active
created: 2026-06-14
superseded_by: null
---

# Tiered resource resolution with a reported source

**Principle:** [Extend through data and one resolver, not parallel paths](../workflow/engineering-principles.md#architecture).

**Intent:** resolve replaceable resources through explicit host override,
generated projection and packaged default, while reporting the selected source.

**When to use:** a host can override a resource that also has an installed default.
A single canonical asset does not need an override mechanism.

**Recipe:**

1. Put precedence in one resolver that returns the path and source tag:
   `override`, `generated`, or `packaged`.
2. Ship a usable default for supported assets inside the installed package.
   A missing required asset is a typed failure naming the searched locations;
   a placeholder is not a functioning default.
3. Surface the selected source in diagnostics so the host can explain what ran.
4. If a subsystem owns a different packaged namespace, reuse the host-prefix
   resolver and select its actual packaged asset at that subsystem's owner.
   Do not introduce a second precedence rule or shadow a real template with a
   minimal fallback.
5. Verify host override, generated and installed fallback behavior, plus the
   missing-resource case. Installed tests must resolve outside the source checkout.

**Current Heddle application:** `heddle/kernel/resources.py` owns host precedence
and briefing fallback. Gate prompt preparation uses the same host-prefix lookup
and its packaged prompt directory. `layout.prompts` is not this resolution root.
`heddle init` does not generate a full prompt tree or install skills in a host.

**Anti-patterns / caveats:** silent first-hit lookup, placeholder defaults,
checkout-dependent packaged paths, or claiming a generation command exists
because the resolver knows a generated directory.

**Concrete future-feature scenario:** a new replaceable briefing ships with a
real installed default and becomes visible to diagnostics without changing host
precedence or requiring a copied host prompt corpus.
