---
name: crash-isolated-worker-result-pipes
area: architecture
status: active
created: 2026-10-06
superseded_by: null
---

# Crash-isolated worker result pipes

**Principle:** [Fail loud](../workflow/engineering-principles.md#inviolables).

**Intent:** a parent that runs several forked workers learns each worker's
outcome, including a death in the middle of sending its result, without one
worker's failure blocking or corrupting the others.

**When to use:** a command forks workers that each return one structured
result to a parent, the parent holds locks or other resources while it waits,
and a worker can die at any point (provider crash, `os._exit`, signal, OOM
kill). Not needed for in-process threads that share memory, or for a single
child whose exit code alone is the result.

**Recipe:**

1. **One one-way pipe per worker.** Before starting each worker, create
   `receiver, sender = context.Pipe(duplex=False)`. Pass only `sender` to that
   worker.
2. **The worker holds the only sending end.** Right after `process.start()`,
   close the parent's copy of `sender` in a `finally`. Once the worker exits,
   for any reason, the pipe has no writer, so the receiver reaches end of file
   instead of waiting forever.
3. **Send exactly one message, then close.** The worker builds its whole
   result first, sends it once, and closes `sender` in a `finally`. If
   sending fails, it tries a short error message instead.
4. **Wait on all receivers together.** Loop over
   `multiprocessing.connection.wait(list(receivers))` and `recv()` from each
   ready connection. On `EOFError` the worker exited without a complete result.
   On any other read error the frame was unreadable. Either way, mark that
   worker failed with its own reason, close its receiver and keep waiting on
   the rest.
5. **Separate collection from ordering.** Results arrive in completion order.
   If they must be recorded in a fixed order, buffer each result and record
   the next one in that order as soon as it is available.
6. **Clean up in `finally`.** Terminate any worker still running, join it and
   close every receiver, including on interruption.
7. **Test with a torn frame.** Make one worker write a partial frame and exit
   (for example, patch the connection's send to write half a length-prefixed
   message, then `os._exit(1)`). Assert that the parent reports that worker
   failed, that the others complete, and that the test cannot hang: guard it
   with an alarm that fails the test rather than raising an `OSError` the code
   under test might catch.

**Anti-patterns / caveats:** a shared `multiprocessing.Queue` looks
equivalent but is not. `Queue.get(timeout=…)` applies the timeout only to
polling. Once part of a frame is readable it blocks reading the rest, and
because the parent also holds a write end of the queue's pipe, a worker dying
mid-frame never produces end of file. The parent hangs with every lock held.
A crash test that kills a worker before it sends anything does not reach this
window. Keep the worker's message small and send it last. Never let the
parent keep a reference to a sending end after start.

**Concrete future-feature scenario:** running several milestone-review scopes
of one gate at the same time, or several independent verification lanes, each
in its own forked worker under a lock the parent holds. Each lane reports
through its own pipe, so a lane killed mid-report fails alone, and the parent
records the others and releases the lock.
