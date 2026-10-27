#!/usr/bin/env bash
# Fetch vendored tools that are NOT committed to this repo (no upstream licence to redistribute) at the SHA
# pinned in tools.lock, then apply our patches. Idempotent. Used by the Dockerfile and CI.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

fetch() {  # name dest
    local name="$1" dest="$2" url sha
    read -r url sha < <(awk -v n="$name" '$1==n {print $2, $3}' tools.lock)
    [ -n "${sha:-}" ] || { echo "fetch_vendored: $name missing from tools.lock" >&2; exit 1; }
    if [ -d "$dest/.git" ] && [ "$(git -C "$dest" rev-parse HEAD)" = "$sha" ]; then
        echo "fetch_vendored: $name already at ${sha:0:7}"; return 1
    fi
    rm -rf "$dest"
    git init -q "$dest"
    git -C "$dest" fetch -q --depth 1 "$url" "$sha"
    git -C "$dest" -c advice.detachedHead=false checkout -q FETCH_HEAD
    echo "fetch_vendored: $name @ ${sha:0:7}"
}

if fetch job-automation tools/job-automation; then
    git -C tools/job-automation apply "$ROOT/vendor-patches/job-automation.patch"
    cp vendor-patches/job-automation-hubguard.py tools/job-automation/utils/hubguard.py
    rm -rf tools/job-automation/resume tools/job-automation/*.csv tools/job-automation/applied_jobs.db  # upstream sample data
fi
