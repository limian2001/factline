#!/usr/bin/env python3
"""Print the CI verdict for one commit, as a single word on stdout.

    success      every check finished green (or was skipped)
    pending      at least one check is still queued or running
    none         no checks exist for this commit at all
    failed:...   at least one check finished red; details after the colon
    unreachable  GitHub could not be reached, or returned something unparseable

Uses the **Checks API**, not the commit Status API. That distinction is the whole
reason this file exists: GitHub Actions writes *check runs* and never writes
commit statuses, so ``/commits/{sha}/status`` reports ``pending`` forever for an
Actions-only repo -- its documented behaviour when no statuses exist. A deploy
gated on that endpoint silently never fires.

Shared by pull-deploy.sh and status.sh so the verdict is defined exactly once.
Standard library only: this runs on the box before any venv is guaranteed.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

GREEN = frozenset({"success", "skipped", "neutral"})
API = "https://api.github.com/repos/{slug}/commits/{sha}/check-runs"
TIMEOUT = 20


def classify(doc: dict) -> str:
    """Turn a check-runs payload into a verdict. Pure, so it is unit-tested."""
    runs = doc.get("check_runs")
    if runs is None:
        return "unreachable"
    if not runs:
        return "none"
    if any(run.get("status") != "completed" for run in runs):
        return "pending"

    bad = [run for run in runs if run.get("conclusion") not in GREEN]
    if bad:
        detail = ",".join(str(run.get("name")) + "=" + str(run.get("conclusion")) for run in bad)
        return "failed:" + detail
    return "success"


def verdict(slug: str, sha: str) -> str:
    request = urllib.request.Request(
        API.format(slug=slug, sha=sha),
        headers={
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "factline-deploy",
        },
    )

    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            doc = json.load(response)
    except (urllib.error.URLError, urllib.error.HTTPError, OSError, ValueError):
        # Includes rate limiting (403) and a not-yet-visible commit (404).
        # Never guess green from a failed lookup.
        return "unreachable"

    return classify(doc)


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: ci_state.py <git-sha>", file=sys.stderr)
        return 2
    slug = os.environ.get("FACTLINE_REPO_SLUG", "limian2001/factline")
    print(verdict(slug, sys.argv[1]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
