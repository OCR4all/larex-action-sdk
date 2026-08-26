#!/usr/bin/env bash
set -euo pipefail

usage() {
  echo "Usage: $0 VERSION" >&2
  echo "Example: $0 0.13.0" >&2
  exit 2
}

[[ $# -eq 1 ]] || usage
VERSION="$1"

if [[ ! "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+([.-]?(a|b|rc|post|dev)[.-]?[0-9]+)?$ ]]; then
  echo "Invalid PEP 440 release version: $VERSION" >&2
  exit 1
fi

command -v uv >/dev/null || { echo "uv is required" >&2; exit 1; }
command -v gh >/dev/null || { echo "GitHub CLI (gh) is required" >&2; exit 1; }
gh auth status >/dev/null || { echo "Authenticate gh before releasing" >&2; exit 1; }

[[ "$(git branch --show-current)" == "main" ]] || {
  echo "Releases must be created from the main branch" >&2
  exit 1
}
if ! git diff --quiet || ! git diff --cached --quiet; then
  echo "Working tree has tracked changes; commit or stash them first" >&2
  exit 1
fi

TAG="v${VERSION}"
if git show-ref --tags --verify --quiet "refs/tags/${TAG}" || \
  git ls-remote --exit-code --tags origin "refs/tags/${TAG}" >/dev/null 2>&1; then
  echo "Tag already exists: ${TAG}" >&2
  exit 1
fi

echo "Updating project version to ${VERSION}"
uv version --no-sync "$VERSION"

echo "Running checks"
uv sync --locked --all-extras
uv run ruff format --check .
uv run ruff check .
uv run pyright
uv run pytest
uv build

git add pyproject.toml uv.lock
if git diff --cached --quiet; then
  echo "No version changes to commit; continuing with the existing project version"
else
  git commit -m "chore(release): prepare ${TAG}"
fi

echo "Pushing main and ${TAG}"
git push origin HEAD:main
git tag -a "$TAG" -m "Release ${TAG}"
git push origin "$TAG"

echo "Creating GitHub release ${TAG}"
release_args=("$TAG" --title "$TAG" --generate-notes)
if [[ "$VERSION" =~ (a|b|rc|dev) ]]; then
  release_args+=(--prerelease)
fi
gh release create "${release_args[@]}"
echo "Released ${TAG}"
