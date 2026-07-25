#!/usr/bin/env bash
# Pull new hardware from upstream and report what needs a human.
#
# Upstream paths are never edited in this fork, so the merge itself is always a
# clean fast-forward. What this adds is the REPORT: of the files upstream just
# changed, which ones do we carry a Danbyte layer for? Those are the only ones
# worth looking at.
#
#   danbyte/bin/sync-upstream.sh --report   # fetch + merge + report
#   danbyte/bin/sync-upstream.sh --dry-run  # report what a merge would bring
set -euo pipefail

MODE="${1:---report}"
BEFORE="$(git rev-parse HEAD)"

git fetch upstream --quiet

if [[ "$MODE" == "--dry-run" ]]; then
  RANGE="HEAD..upstream/master"
else
  # README.md is the one upstream file we own (see README). If upstream touches
  # it the merge will conflict here — keep ours, then cherry-pick anything of
  # theirs that matters.
  git merge upstream/master --no-edit
  RANGE="$BEFORE..HEAD"
fi

changed="$(git diff --name-only "$RANGE" -- device-types/ || true)"
[[ -z "$changed" ]] && { echo "No upstream device-type changes."; exit 0; }

echo "Upstream device-type files changed: $(echo "$changed" | wc -l)"
echo

# Our layer is keyed by the upstream slug, which is the filename stem.
overlapping=0
while IFS= read -r f; do
  slug="$(basename "$f" .yaml)"
  if compgen -G "danbyte/overlays/*/${slug}.y*ml" >/dev/null 2>&1 ||
     compgen -G "danbyte/types/*/${slug}.danbyte.json" >/dev/null 2>&1; then
    echo "  REVIEW  $f  — we carry a Danbyte layer for '$slug'"
    overlapping=$((overlapping + 1))
  fi
done <<< "$changed"

if [[ "$overlapping" -eq 0 ]]; then
  echo "  Nothing we annotate was touched — new hardware only, nothing to review."
fi
