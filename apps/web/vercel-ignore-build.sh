#!/usr/bin/env bash
# Vercel's `ignoreCommand` for the web app (see vercel.json). Vercel runs it from
# this directory before every build: exit 0 skips the build, anything else builds.
#
# It lives in a file because vercel.json caps `ignoreCommand` at 256 characters,
# and a longer inline command fails the deployment before any build starts.
#
# Skips only when the diff from the last deployed commit touches nothing the web
# app is built from. Anything uncertain (first deploy, a previous commit missing
# from Vercel's shallow clone, a git error) builds.

prev="${VERCEL_GIT_PREVIOUS_SHA:-}"

if [ -z "$prev" ] || ! git cat-file -e "${prev}^{commit}" 2>/dev/null; then
  echo "No reachable previous deployment; building."
  exit 1
fi

if git diff --quiet "$prev" HEAD -- \
  . \
  ../../packages \
  ../../modules \
  ../../package.json \
  ../../pnpm-lock.yaml \
  ../../pnpm-workspace.yaml \
  ../../tsconfig.base.json; then
  echo "Nothing the web app is built from changed since ${prev}; skipping."
  exit 0
fi

# `git diff --quiet` exits 1 for a difference and 128 for an error: both build.
echo "Web app inputs changed since ${prev} (or git could not tell); building."
exit 1
