---
name: registry-over-literal-versioning
area: architecture
status: active
created: 2026-06-22
superseded_by: null
---

# Explicit supported-version registry

**Principle:** [Extend through data and one resolver, not parallel paths](../workflow/engineering-principles.md#architecture).

**Intent:** make format compatibility an explicit, implemented contract rather
than equating the newest version with every readable version.

**When to use:** a versioned format has deliberately supported readers for more
than one version. A registry does not justify speculative compatibility code.

**Recipe:**

1. Keep supported versions and the current writer version at one owner. The
   current version must be a member; only register versions with real readers.
2. Decode each supported version with its declared semantics. Never relabel old
   evidence as new or infer support from a lower version number.
3. Refuse unsupported, malformed or foreign formats without mutation. Offer only
   recovery paths the product actually supplies: a compatible runtime, a newer
   reader, or an explicitly implemented converter.
4. Test current and supported older inputs, unsupported older and future inputs,
   malformed versions and non-mutation on refusal. Pin behavior through the
   reader and public error boundary rather than matching source-code spelling.

**Anti-patterns / caveats:** a registry is not a promise to read every historical
version. Do not recommend migration merely because a version is old. Never add a
compatibility alias or converter without a consumer and explicit contract.

**Current Heddle application:** `heddle/contracts/schemas.py` declares v9 and v10
state support. Older and future state refuse; unfinished incompatible work uses
its compatible runtime. No current `heddle feature migrate` command is shipped.

**Concrete future-feature scenario:** a file importer deliberately adds one
older format reader alongside its current format. Both enter the registry with
fixtures; other historical versions remain unsupported with an honest remedy.
