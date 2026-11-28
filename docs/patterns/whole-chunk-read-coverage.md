---
name: whole-chunk-read-coverage
area: architecture
status: active
created: 2026-11-28
superseded_by: null
---

# Whole-chunk read coverage

**Intent:** prove that a model agent was given all of its input when the
input is too large to send inline and the agent's tool stream shows only
summaries of its reads, never their content.

**When to use:** an agent CLI silently truncates or rewrites inline input
past some size, so large input must be delivered as files the agent reads
itself. Its event stream records each read's path and parameters but not
the bytes returned. It does not apply when the input fits inline and the
CLI reports truncation, or when the stream returns read content you can
compare directly.

**Recipe:**

1. Freeze the route before launch: split the captured input into chunk
   files at line boundaries, each small enough that one read returns it
   whole, and record each chunk's byte range and digest. Refuse to write a
   chunk whose bytes differ from its frozen digest.
2. Send a short launch message that names only the chunk index file and
   states the reading rule: read every chunk whole, in index order, with the
   one file-read tool, never through shell commands or search tools. Put
   the rest of the instructions inside the chunks, so skipping a chunk also
   skips instructions.
3. Re-verify every transport file against its frozen digest immediately
   before the spawn.
4. After the run, audit the stream. Credit a chunk only for a successful
   read whose parameters are all path parameters, because any unobserved
   range or offset parameter may narrow the read. Withhold credit when a
   write or an executed command targeted the chunk before that read.
5. Re-check every transport digest after the run. A transport file that
   changed during the run withholds all credit, because the credited reads
   may have returned other bytes.
6. Fail the review on any uncredited chunk, reporting the uncovered byte
   ranges. Never pass it as a partial review.
7. Treat read fidelity, the claim that a whole read returns the file's
   bytes unchanged, as a stated assumption. Measure it with a live canary
   (planted recall items in late chunks, plus a positive control), and
   record the code points the tool is known to rewrite as stated limits.

**Anti-patterns / caveats:** crediting a read by path alone, which accepts a
narrowed read; trusting the CLI's success status when it truncates silently;
measuring size in characters when the limit is in bytes; chunking so large
that the tool's own read window splits a chunk; letting the agent read
chunks through a shell, whose output may be capped or lossy; and checking
digests only before the run, which misses a chunk changed under the agent.

**Concrete future-feature scenario:** a fourth reviewer CLI shows only
summaries of its reads and silently cuts long prompts. Its runner reuses the
frozen chunk route, the index-only launch message and the coverage audit,
supplying only that CLI's read-tool name and its path-parameter set.
