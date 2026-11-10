Threat-model calibration. This system is a single-user local
development tool; the workspace, repository, and filesystem are
operator-trusted inputs. Do not apply an adversarial-filesystem or
hostile-multi-tenant threat model (symlink planting, TOCTOU races against
the operator, resource exhaustion from operator-owned files) unless the
Feature Spec names that threat explicitly. Every Critical or Important
finding that alleges a vulnerability, tampering vector, or adversarial
misuse must name its threat actor and check that actor against the spec's
trust model. A finding whose actor or failure mode the trust model
excludes is not actionable above the gate's lowest tier: route it to
Observation (IGNORE) where the gate offers that lane, otherwise record it
as Minor — citing the trust-model statement it conflicts with instead of
escalating severity, and adding a note recommending AC expansion when the
gap is real but out of model. Full skepticism stays on every failure mode
the spec names; only the routing of out-of-model gaps changes.
