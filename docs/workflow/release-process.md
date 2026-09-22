# Release process

Heddle is released from an annotated Git tag and a GitHub Release that carries
the wheel, the sdist and their checksum list. Heddle is not on PyPI; the
`heddle` name there belongs to an unrelated project, so the install commands in
the README pin a tag. This document owns the release steps, the repository
metadata on GitHub, and what stays manual. The
[publication policy](../../release/public-repository.toml) owns which paths and
artifact contents are public; the [testing strategy](testing-strategy.md) owns
which tests a release must run.

## Versioning

Versions follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html) and
the [changelog](../../CHANGELOG.md) follows Keep a Changelog. Before 1.0, a
minor release may change command surfaces, payload schemas, on-disk record
formats and configuration keys; record every such change under `Changed` or
`Removed`. `heddle help --json` is the authority for the installed version.
The GitHub pre-release flag is reserved for `-alpha`, `-beta` and `-rc`
identifiers; a `0.y.z` release is published as a normal release and marked
latest.

## Prepare the release commit

1. Set `version` in `pyproject.toml` and move the changelog entries into a
   `## [x.y.z] - YYYY-MM-DD` section whose link at the bottom of the file
   points at `https://github.com/Wrosinski/heddle/releases/tag/vx.y.z`.
2. Run pre-commit on the complete tree and the release proof the testing
   strategy requires.
3. From a clean tree, run `scripts/qualify-public-repository.sh`. It archives
   `HEAD`, runs the repository, references, surfaces and tests boundary
   checks, builds the sdist and wheel, rebuilds the wheel from the sdist,
   compares both builds against the manifests under `release/manifests/`,
   installs the rebuilt wheel into a fresh environment, and runs `heddle init`
   and `heddle doctor` against a throwaway host. When a public surface changed
   on purpose, update the manifests in the same change and rerun.
4. Commit as `chore: release vx.y.z`.

## Build the artifacts

Build into the ignored `dist/` directory with the same builder the
qualification script uses, then write the checksum list next to the files:

```bash
.venv/bin/python -m build --no-isolation --sdist --wheel --outdir dist
(cd dist && sha256sum heddle-x.y.z-py3-none-any.whl heddle-x.y.z.tar.gz > SHA256SUMS.txt)
```

`SHA256SUMS.txt` is the authoritative list for the release. The checksums and
the artifacts come from the same build and the same upload, so they guard
against a corrupted download, not against a compromised publisher. The
manifest comparison in the qualification script is the stronger claim; keep it
green.

## Tag and draft

Create an annotated tag on the release commit and push it with the branch:

```bash
git tag -a vx.y.z -m "Heddle vx.y.z"
git push origin main vx.y.z
```

Create the release as a draft so every asset is attached before anything is
announced. `--verify-tag` refuses to create a tag that is not already on the
remote, which keeps the release on the qualified commit:

```bash
gh release create vx.y.z --draft --verify-tag --title "Heddle vx.y.z" \
  --notes-file <notes-file> \
  dist/heddle-x.y.z-py3-none-any.whl dist/heddle-x.y.z.tar.gz dist/SHA256SUMS.txt
```

Write the notes by hand. GitHub's generated notes are built from merged pull
requests and this repository commits to `main` directly, so they would be
empty. The shape used for 0.1.0 is the template:

- One or two paragraphs saying what the release is.
- `## Try it`: the `uvx --from git+https://github.com/Wrosinski/heddle@vx.y.z`
  command, the `uv tool install` command for the attached wheel, and links to
  the tour and the comparison pinned to the tag.
- `## What changed`: prose grouped by what the user will notice, linking the
  changelog section for the item-by-item list.
- `## Known limitations`, including the versioning note above.
- `## Checksums`: the `sha256sum -c SHA256SUMS.txt` command, the provenance
  sentence, and the hashes inside a collapsed `<details>` block.

Before publishing, download the draft's assets and check them against the
list and against `dist/`:

```bash
gh release download vx.y.z --dir <empty-directory>
(cd <empty-directory> && sha256sum -c SHA256SUMS.txt)
```

## Decide immutability, then publish

Release immutability is a repository setting (Settings, Releases, "Enable
release immutability"). It applies only to releases published after it is
enabled, freezes the assets and the tag, and attaches GitHub-signed
attestations to the assets; the title and the notes stay editable. There is no
CLI or API path for the setting. Decide it before publishing: enable it when
the assets are final and the tag will never move, and ship a patch release
rather than replacing an asset afterwards.

Publishing is the event that notifies release watchers and writes the releases
feed, so finish the repository metadata and the notes first. Then:

```bash
gh release edit vx.y.z --draft=false --verify-tag --latest
```

Afterwards confirm that `https://github.com/Wrosinski/heddle/releases/latest`
redirects to the tag, that the changelog link resolves, and that
`uvx --from git+https://github.com/Wrosinski/heddle@vx.y.z heddle help` runs
from a machine without a checkout.

## Repository metadata

GitHub searches only the repository name, description and topics by default,
and renders the description as the page's search snippet and link preview.
Keep the values below current; they are applied with `gh` and are not
derived from any file in the package.

About description:

```text
A local workflow runtime for Claude Code and Codex CLI that records what an agent did and refuses to advance a stage on missing, failed, or stale evidence.
```

```bash
gh repo edit Wrosinski/heddle --description "<the string above>"
```

Topics, applied with the replace-all endpoint so this list is the source of
truth (at most 20, lowercase letters, digits and hyphens):

```bash
gh api -X PUT repos/Wrosinski/heddle/topics --input - <<'JSON'
{"names":["agentic-coding","spec-driven-development","claude-code","codex-cli","coding-agent","ai-coding-agent","workflow-engine","state-machine","agents-md","code-review","human-in-the-loop","agentic-ai","developer-tools","software-engineering","cli","python"]}
JSON
```

Every topic names something Heddle is or does. Do not add topics for
integrations Heddle does not have, for competitor product names, or for the
license, which has its own search qualifier.

The website field stays empty until there is a page that is not this
repository. A social preview image is optional; if one is added, export the
wordmark from `assets/brand/logos/` at 1280 by 640 pixels, under 1 MB, with
one line of text legible at thumbnail size.

## Not automated yet

A tag-triggered workflow could build the artifacts, attest them with
`actions/attest`, and run the same `gh release create` command shown above.
It has not been added because the release path is exercised by hand and a
release workflow cannot be verified before the next tag. If it is added, keep
one command shared by the manual and the CI path, give only the release job
write permissions, pin every action to a full commit SHA, and confirm the
permission set against the current `actions/attest` documentation.
