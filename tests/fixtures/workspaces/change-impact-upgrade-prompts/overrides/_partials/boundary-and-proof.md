Apply both principles within this review's lane and at its level: a
specification or plan defines the outcomes and names the proof, tests exercise
them, and code delivers them. They add no review dimension; report what they
reveal under the dimension or category where it belongs.

Account for every state that crosses a boundary. Where a contract depends
on work crossing a boundary between components, owners, processes, runs or
points in time, identify every state that can reach the other side, not only
the intended one. Consider partial, interrupted, repeated, concurrent and late
states, every failure the producer can emit, and every form the input can
take. Each needs a defined outcome: handled, refused with a reason, or placed
out of scope by the contract or an owner decision. One rule may settle a whole
class of states; name a state separately only when its outcome differs. A
reachable state without a defined outcome is a finding.

Proof must be able to fail for the real defect. A test or planned witness
counts only when the code under test, not the check itself, produces the
behavior it checks, and a genuine defect would make it fail. A check that
supplies the value it asserts, injects what production must create, relies on
an authority only tests use, or replaces a dependency with a double more
forgiving than the real one, including a model or service that never makes
mistakes, establishes nothing about the contract. A status the system reports,
such as complete, passing or supported, must not claim more than the state and
proof beneath it.
