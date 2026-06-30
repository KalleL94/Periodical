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
git checkout main
git pull
git tag "$TAG"
git push origin "$TAG"

echo "Released $TAG"
