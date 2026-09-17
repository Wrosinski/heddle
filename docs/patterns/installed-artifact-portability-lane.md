---
name: installed-artifact-portability-lane
area: testing
status: active
created: 2026-07-29
superseded_by: null
---

# Installed-artifact portability lane

**Intent:** prove that a packaged application works from its installed artifact
on a host unlike the source checkout, using independent observations that fail
if the application reads checkout assets, reaches the network, or merely
self-reports the child behavior the test is meant to verify.

**When to use:** an installer, bootstrap, migration, or adoption feature whose
purpose includes "works from the wheel/package without the repository." Not
for ordinary unit tests or a package whose supported runtime explicitly
requires its checkout.

**Recipe:**

1. **Build once from a controlled source tree.** Produce the wheel or other
   immutable artifact before entering the test host. Reuse that exact artifact
   across scenarios so packaging differences cannot explain behavioral
   differences.
2. **Install without source or network fallback.** Create an isolated
   environment, install the artifact with dependency resolution disabled, and
   add only the already-pinned dependency closure. Assert imports resolve from
   the installed location.
3. **Use a deliberately unlike host.** Create a fresh Git repository with
   non-default layouts, nested invocation directories, and scenario-specific
   pre-existing host files. Do not copy the engine checkout into it.
4. **Scrub ambient inputs.** Build an explicit environment allowlist. Remove
   source-root variables, agent-session variables, credentials, proxy settings,
   and other paths that could make a missing packaged asset appear present.
5. **Audit the installed command interval.** Install an audit hook in the
   isolated interpreter that rejects reads under the source checkout and
   network events. Prove the hook is live with positive controls before
   trusting the absence of violations.
6. **Observe subprocesses independently.** Put a deterministic executable shim
   on `PATH` that captures the actual child argv, relevant environment, and
   canned result. Compare that capture with the product's recorded launch
   vector; never use the product log as its own oracle.
7. **Read expected packaged bytes from the installation.** Resource comparisons
   resolve from `site_packages` (or the equivalent installed root), not from
   repository constants or checkout paths.
8. **Sweep registered surfaces through the public entrypoint.** Derive the
   complete public surface set from the installed runtime's authoritative
   registry and invoke every member through the installed command or API. An
   internal parser, resolver, or expansion helper is not proof that production
   routing works; exact-set compare the exercised members with the registry.
9. **Bound the whole installed journey.** Measure from the first installed
   command through the last, assert topology as well as bytes on no-write
   paths, and record an explicit N/A rationale when a deterministic boundary
   substitute makes live execution inapplicable.

**Anti-patterns / caveats:** importing production modules from the checkout
inside an "installed" test; letting `pip` or the command reach the network;
building a host whose layout mirrors the repository; asserting only the
product-authored log; testing a helper while skipping the installed public
entrypoint; exercising a hand-picked subset that can drift from the registry;
monkeypatching across a subprocess boundary; timing only the final command; or
calling live proof "covered" without stating why the substitute exercises the
same boundary. Audit hooks are process-local, so the installed interpreter
must load the hook for every relevant child.

**Concrete future-feature scenario:** a future Heddle migration command ships a
wheel that adopts a legacy repository. Its acceptance lane installs one wheel,
uses a host with a non-default plans directory and legacy files, forbids
checkout reads/network, drives the migration through installed console scripts,
and captures the real converter subprocess through a PATH shim.
