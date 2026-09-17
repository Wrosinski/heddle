#!/usr/bin/env bash
# Commit; when pre-commit hooks mutate staged files, re-stage exactly those
# files and retry once. Any other failure propagates unchanged.
set -u
git commit "$@" && exit 0
mapfile -d '' staged < <(git diff --cached --name-only -z)
((${#staged[@]})) || exit 1
mapfile -d '' mutated < <(git diff --name-only -z -- "${staged[@]}")
((${#mutated[@]})) || exit 1
git add -- "${mutated[@]}"
exec git commit "$@"
