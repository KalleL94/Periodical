#!/bin/bash
set -e

TAG=$1
if [ -z "$TAG" ]; then
    echo "Usage: ./release.sh <tag>  (e.g. ./release.sh v0.12.1)"
    exit 1
fi

git checkout main
git pull
git tag "$TAG"
git push origin "$TAG"

echo "Released $TAG"
