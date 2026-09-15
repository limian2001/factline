#!/usr/bin/env bash
# Pull-based deploy. Runs ON the box every few minutes via factline-deploy.timer.
#
# The box asks GitHub "has main moved, and did CI pass on it?" and if so deploys
# itself. Nothing needs to reach in from outside: no inbound SSH, no deploy key,
# no credentials stored in GitHub. This is the same model Argo CD and Flux use.
#
# Exits 0 and silently when there is nothing to do, so the journal stays readable.
set -euo pipefail

REPO_SLUG="limian2001/factline"
APP=/opt/factline
BRANCH=main

cd "$APP"

# A heartbeat that survives log rotation and needs no journal access to read.
mkdir -p "$APP/data"
date -u +%Y-%m-%dT%H:%M:%SZ > "$APP/data/.deploy-last-check"

git fetch --quiet origin "$BRANCH"
local_sha=$(git rev-parse HEAD)
remote_sha=$(git rev-parse "origin/$BRANCH")

if [ "$local_sha" = "$remote_sha" ]; then
  # Logged at syslog debug priority (the <7> prefix), so `journalctl` hides it
  # by default and `journalctl -p debug` shows it. Total silence was worse: it
  # made "the timer is idle" indistinguishable from "the timer is dead".
  echo "<7>deploy.idle up to date at ${local_sha:0:8}"
  exit 0
fi

echo "deploy.candidate local=${local_sha:0:8} remote=${remote_sha:0:8}"

# Only ever deploy a commit CI has gone green on. Without this the box would
# happily deploy a commit whose tests are still running -- or failing.
#
# ci_state.py queries the Checks API. It must not be swapped for the commit
# Status API (/commits/{sha}/status): GitHub Actions writes check runs and never
# writes commit statuses, so that endpoint answers "pending" forever and the
# deploy silently never fires. That was a real bug here.
state=$("$APP/deploy/ci_state.py" "$remote_sha" 2>/dev/null || echo unreachable)

case "$state" in
  success)
    echo "deploy.ci_green ${remote_sha:0:8}"
    ;;
  pending)
    echo "deploy.waiting ci still running on ${remote_sha:0:8}"
    exit 0
    ;;
  none)
    echo "deploy.no_checks no ci runs found for ${remote_sha:0:8}; not deploying" >&2
    exit 0
    ;;
  unreachable)
    echo "deploy.ci_unreachable could not read ci state for ${remote_sha:0:8}" >&2
    exit 0
    ;;
  failed:*)
    echo "deploy.blocked ci red on ${remote_sha:0:8} -> ${state#failed:}" >&2
    exit 0
    ;;
  *)
    echo "deploy.blocked unexpected ci state '$state' on ${remote_sha:0:8}" >&2
    exit 0
    ;;
esac

# Take the pipeline's lock, so a deploy can never swap the code out from under a
# pipeline run that is already in progress.
exec 9>/var/lock/factline.lock
if ! flock --exclusive --wait 1800 9; then
  echo "deploy.abort pipeline still running after 30 min" >&2
  exit 1
fi

git checkout --quiet --force "$remote_sha"
echo "deploy.checked_out $(git rev-parse --short HEAD)"

# --frozen installs exactly what uv.lock pins and fails rather than re-resolving.
# A deploy that quietly picks up a new transitive version is not reproducible,
# which defeats the point of having a lockfile.
uv sync --frozen --no-dev

# Smoke check: the CLI must import and load its config. Catches a bad pin now
# instead of at 23:00 when the pipeline timer fires.
.venv/bin/python -m factline.cli --help > /dev/null
echo "deploy.smoke_ok"

# Unit files come from the repo, so the schedule is version-controlled rather
# than being something someone once typed on the box.
sudo install -m 644 "$APP/deploy/factline.service"        /etc/systemd/system/factline.service
sudo install -m 644 "$APP/deploy/factline.timer"          /etc/systemd/system/factline.timer
sudo install -m 644 "$APP/deploy/factline-deploy.service" /etc/systemd/system/factline-deploy.service
sudo install -m 644 "$APP/deploy/factline-deploy.timer"   /etc/systemd/system/factline-deploy.timer
sudo systemctl daemon-reload
sudo systemctl enable factline.timer factline-deploy.timer
# Deliberately not restarting factline-deploy.timer: this script is running
# under it right now.

echo "deploy.ok $(git rev-parse --short HEAD)"
