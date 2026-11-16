#!/usr/bin/env bash
# Publishes homeassistant/ to github.com/sorenda-app/home-assistant, the way
# claude-code/ goes to sorenda-app/claude-code: one commit per release by
# Onada Nelson, a tag vX.Y.Z and a GitHub release carrying sorenda.zip (what
# the Marketplace installs, hacs.json: zip_release).
#
# Publishes what is committed (HEAD), never the working tree: commit first.
#
#   scripts/publish.sh --dry-run   # build the commit in a temp dir, push nothing
#   scripts/publish.sh             # push, tag and release
set -euo pipefail

REPO="sorenda-app/home-assistant"
AUTHOR_NAME="Onada Nelson"
AUTHOR_EMAIL="onada_nel@sorenda.app"
DESCRIPTION="Home Assistant integration: Sorenda notes, tasks and reminders by voice"

dry_run=false
[[ "${1:-}" == "--dry-run" ]] && dry_run=true

here="$(cd "$(dirname "$0")/.." && pwd)"
monorepo="$(git -C "$here" rev-parse --show-toplevel)"
prefix="$(git -C "$here" rev-parse --show-prefix)"   # homeassistant/
prefix="${prefix%/}"

if [[ -n "$(git -C "$monorepo" status --porcelain -- "$prefix")" ]]; then
  if $dry_run; then
    echo "warning: $prefix has uncommitted changes; the dry run shows HEAD without them" >&2
  else
    echo "error: $prefix has uncommitted changes; commit them first" >&2
    exit 1
  fi
fi

version=$(git -C "$monorepo" show "HEAD:$prefix/custom_components/sorenda/manifest.json" \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["version"])')
tag="v$version"
echo "Publishing Sorenda for Home Assistant $version from $(git -C "$monorepo" rev-parse --short HEAD)"

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

# The public repository: its history if it exists, a new one otherwise.
if gh repo view "$REPO" >/dev/null 2>&1; then
  if gh release view "$tag" --repo "$REPO" >/dev/null 2>&1; then
    echo "error: $REPO already has a release $tag; bump the version in manifest.json" >&2
    exit 1
  fi
  gh repo clone "$REPO" "$work/repo" -- --quiet
else
  echo "$REPO does not exist yet: it will be created (public)."
  git init --quiet --initial-branch=main "$work/repo"
fi

# Replace the whole tree with the committed folder.
find "$work/repo" -mindepth 1 -maxdepth 1 ! -name .git -exec rm -rf {} +
git -C "$monorepo" archive "HEAD:$prefix" | tar -x -C "$work/repo"

(
  cd "$work/repo"
  git add -A
  if git diff --cached --quiet; then
    echo "error: nothing changed since the last publish" >&2
    exit 1
  fi
  GIT_AUTHOR_NAME="$AUTHOR_NAME" GIT_AUTHOR_EMAIL="$AUTHOR_EMAIL" \
  GIT_COMMITTER_NAME="$AUTHOR_NAME" GIT_COMMITTER_EMAIL="$AUTHOR_EMAIL" \
    git commit --quiet -m "Publish Sorenda Home Assistant integration $version"
  git tag "$tag"
  ./scripts/build.sh >/dev/null
)

echo
git -C "$work/repo" log --format='%h %an <%ae> %s' -1
git -C "$work/repo" show --stat --format= HEAD | tail -1
unzip -l "$work/repo/dist/sorenda.zip" | tail -1

if $dry_run; then
  echo
  echo "Dry run: nothing pushed. Files that would be published:"
  git -C "$work/repo" ls-files
  exit 0
fi

if ! gh repo view "$REPO" >/dev/null 2>&1; then
  gh repo create "$REPO" --public --description "$DESCRIPTION"
  git -C "$work/repo" remote add origin "https://github.com/$REPO.git"
fi
git -C "$work/repo" push --quiet origin main "$tag"
gh release create "$tag" "$work/repo/dist/sorenda.zip" --repo "$REPO" \
  --title "$version" --notes "Sorenda for Home Assistant $version. Install through the Marketplace or unpack sorenda.zip into custom_components/sorenda/."
echo "Published: https://github.com/$REPO/releases/tag/$tag"
