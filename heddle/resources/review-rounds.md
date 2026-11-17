## Review rounds

A review's first round runs because the confirmed policy selects the role.
Readiness offers a further round only for an unmet minimum, a continuation the
owner authorized or a retained inspection duty; every other round is the lead's
judgement. Under `convergence`, the default mode, no round count ends a review.
Closure ends it, or a stop that asks the owner: no progress for every role, and
no decrease for spec, plan and scaffolding reviews. Under a confirmed
`upper-limit`, a round past the limit needs the owner's allowance.

Open another verification round when the latest round's findings, or a change
since that round, need a reviewer to look again. Weigh what changed, the
severity of the findings it touches and whether rerun proof covers it: a
Critical or Important fix to reviewed content usually needs one, while a Minor
finding, a formatting-only change or a change outside the review's subject does
not. Record the judgement where a later reader finds it: in the reasons of the
dispositions it concerns, or in the session log when no row exists.

The lead may stop at the latest round when the findings that remain are of low
importance. Minor findings never block closure. Each open Critical or Important
finding still closes through a disposition with qualifying evidence and its
reason, whether or not another round runs.

Under `heddle drive`, a session opens a round it judges necessary itself with
`heddle review round-open`, limited to purpose `verification` without a scope
change, and the driver runs it before the stage moves on. Other rounds and scope
changes come from native next actions or the owner.
