#!/bin/bash
set -e

MODE=$1
if [ -z "$MODE" ]; then
    echo "Usage:"
    echo "  ./release.sh <tag>     Tag main and deploy (e.g. ./release.sh v0.28.0)"
    echo "  ./release.sh --notag   Deploy current main without creating a tag"
    exit 1
fi

# Tag-free deploy: trigger the deploy workflow against main. The displayed app
# version (from changelog.py) is unchanged; this only ships the latest main.
if [ "$MODE" = "--notag" ]; then
    if ! command -v gh >/dev/null 2>&1; then
        echo "Error: gh (GitHub CLI) is required for --notag deploys." >&2
        exit 1
    fi
    echo "Triggering deploy of main (no tag)..."
    gh workflow run deploy.yml --ref main
    echo "Deploy triggered. Follow it with: gh run watch"
    exit 0
fi

# Versioned release: tag main and push the tag, which triggers the deploy.
TAG="$MODE"

# The tag must match the version in the repo. A pull request adds its release
# note to the topmost VERSIONS block while that version is untagged, and only
# bumps the version when the topmost block has already been released. This
# check is what makes that convention hold: it fails both when the version was
# bumped without a release and when a release forgot the bump.
REPO_VERSION=$(grep -m1 '^version = ' pyproject.toml | cut -d'"' -f2)
APP_VERSION=$(grep -m1 '"version":' app/routes/changelog.py | cut -d'"' -f4)
if [ "$TAG" != "v$REPO_VERSION" ] || [ "$REPO_VERSION" != "$APP_VERSION" ]; then
    echo "Error: version mismatch." >&2
    echo "  tag:                    $TAG" >&2
    echo "  pyproject.toml:         v$REPO_VERSION" >&2
    echo "  changelog.py VERSIONS:  v$APP_VERSION  (this one is what the app displays)" >&2
    exit 1
fi
if git rev-parse -q --verify "refs/tags/$TAG" >/dev/null; then
    echo "Error: tag $TAG already exists." >&2
    exit 1
fi

git checkout main
git pull
git tag "$TAG"
git push origin "$TAG"

echo "Released $TAG"
