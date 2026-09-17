---
name: tiered-resource-resolution
area: architecture
status: active
created: 2026-06-14
superseded_by: null
---

# Tiered resource resolution with a reported source

**Principle:** [Extend through data and one resolver, not parallel paths](../workflow/engineering-principles.md#architecture).

**Intent:** locate a prompt/template/config asset by a fixed precedence
(host override > generated projection > packaged default) and return
*which tier resolved it*, with a shipped packaged floor so the path is
never empty — making the resolution observable (a `doctor`-style command
can name each asset's source) instead of a silent first-hit lookup.

**When to use:** any command that loads replaceable assets a host may
override (gate prompts, stage briefings, slash-command templates, config
fragments). Not for assets with a single canonical location, and not on a
hot path that forbids the extra stat calls.

**Recipe:**

1. One helper `resolve_resource(asset) -> (path, source)` walks the tiers
   in order and returns the resolved path **and** a source tag
   (`override | generated | packaged`). Callers that only need the path
   ignore the tag; diagnostics surface it.
2. Ship a packaged **floor** for every probed asset so the chain always
   resolves — the absence of a floor turns "not found" into an error path
   that is hard to test through the surface. (For example, a single
   `self-review.prompt.md` floor; absent-everywhere reuses the domain's
   existing error code — there was no dedicated `resource-missing` code.)
3. A diagnostics command (`doctor`) reports `(asset, source)` per probed
   asset — the observable half of the precedence bar.
4. **Caveat to encode:** a subsystem with its *own* bundled
   default may need a *narrower* resolver that deliberately skips the new
   packaged tier, or the minimal packaged floor will *shadow* the real
   asset on a bootstrap/self-hosting path. The engine's `build_prompt`
   resolves host-override > generated only (its bundled template is its
   floor); only `kickoff`/`doctor` use the full three-tier resolver. Do
   not unify onto one resolver without checking for this shadowing.

**Anti-patterns / caveats:** a first-hit lookup that does not report the
tier (you cannot answer "where did this come from?"); no packaged floor
(every probe has an untested error branch); unifying every consumer onto
the full resolver and silently shadowing a subsystem's own default
(caveat 4).

**Concrete future-feature scenario:** Heddle ships the canonical
per-(stage, tier) briefing corpus and the gate prompt corpus — both reuse
`resolve_resource` and `doctor`'s source-naming unchanged, and `doctor`'s
probe set expands to cover briefings; installed command templates resolve
through the same chain.
