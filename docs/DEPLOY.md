# Deployment runbook

Language: **Python 3.11**, nothing else. No C in this repo; numpy and pyarrow
ship prebuilt wheels, so there is no compiler in the loop.

## Two different things, often confused

| | What it is | Credential |
|---|---|---|
| **You push code** | `git push` from your laptop | HTTPS + a GitHub token in your keychain. Your choice, and HTTPS is fine. |
| **The box gets code** | machine-to-machine, no human present | Nothing. The box pulls from a public repo. |

These are unrelated. Push however you like; it changes nothing below.

## The model: the box deploys itself

```
laptop ──git push (https)──▶ GitHub ──lint + test──▶ green
                                                       │
              EC2 ── every 3 min ──▶ "has main moved, did CI pass?"
                                                       │
                                     yes ──▶ checkout <sha>, uv sync --frozen,
                                             reinstall timers
```

No GitHub secrets. No deploy key. No inbound SSH for the deploy — port 22 stays
locked to your own IP. This is the model Argo CD and Flux use, for the same
reasons.

Two timers on the box:

| Unit | Every | Does |
|---|---|---|
| `factline-deploy.timer` | 3 min | pull a new green commit, if there is one |
| `factline.timer` | Mon–Fri 23:00 UTC | run the pipeline (19:00 ET, after the close) |

---

## 1 · Local: push to GitHub over HTTPS

Create an **empty public** repo named `factline` at github.com/new — public is
the point, interviewers need to be able to read it. Then:

```bash
cd ~/Downloads/factline

git init -b main
git add -A
git commit -m "Day 1: data layer with point-in-time correctness

EDGAR client with token-bucket rate limiting, pydantic validation with
quarantine, parquet + DuckDB lake, and an as-of query that resolves
restatements by filing date. 24 tests, no network dependency."

git remote add origin https://github.com/limian2001/factline.git
git push -u origin main
```

Git will ask for a username and password on first push. The password is **a
Personal Access Token**, not your account password — github.com/settings/tokens,
"Generate new token (classic)", tick `repo`. Store it once:

```bash
git config --global credential.helper osxkeychain
```

Before pushing, confirm the secrets file is not staged:

```bash
git status --porcelain | grep -c '\.env$'    # must print 0
```

---

## 2 · EC2: security group

| Port | Source | Why |
|---|---|---|
| 22 | **your IP only** | you, for operating the box |
| 80 | `0.0.0.0/0` | reports and status page |
| 443 | `0.0.0.0/0` | later, once a domain is attached |

Port 22 needs no public exposure, because nothing deploys inward.

---

## 3 · EC2: bootstrap

SSH in as `ubuntu`, then one command:

```bash
curl -fsSL https://raw.githubusercontent.com/limian2001/factline/main/deploy/bootstrap.sh | bash
```

It installs git, uv and Caddy, sets the clock to UTC, creates the `factline`
service account (no shell, no password — nothing signs in as it), clones the repo
to `/opt/factline`, starts Caddy, and installs the four systemd units.

Then the two things it prints, which are manual because they involve secrets:

```bash
# 3a. the secrets file
sudo -u factline tee /opt/factline/.env >/dev/null <<'EOF'
SEC_USER_AGENT="Mian Li mianmianlife@gmail.com"
SEC_RATE_LIMIT_RPS=8
TIINGO_API_KEY=your_key_here
ANTHROPIC_API_KEY=your_key_here
DATA_DIR=/opt/factline/data
EOF
sudo chmod 600 /opt/factline/.env

# 3b. narrow sudo rights — copy the sudoers block bootstrap.sh printed, then:
sudo visudo -c -f /etc/sudoers.d/factline      # must say "parsed OK"
```

`factline` can run exactly nine commands as root, all of them `systemctl` or
`install` against its own two units. It cannot become root.

Verify the pipeline by hand once before trusting the timer:

```bash
sudo -u factline /opt/factline/deploy/run-pipeline.sh
curl -s localhost/health.json
```

Then start both timers:

```bash
sudo systemctl enable --now factline.timer factline-deploy.timer
systemctl list-timers 'factline*'
```

---

## 4 · The daily loop

```bash
git checkout -b day2-tiingo
# ... work ...
make check                      # lint + 24 tests locally, under a second
git commit -am "Day 2: Tiingo price client + data quality report"
git push -u origin day2-tiingo
# open a PR -> CI runs, nothing deploys
# merge to main -> CI runs, goes green, box picks it up within 3 minutes
```

Watch a deploy land:

```bash
ssh ubuntu@<EC2_HOST> 'journalctl -u factline-deploy.service -f'
```

---

## 5 · Operating it

```bash
# next runs, and whether the last one worked
systemctl list-timers 'factline*'
systemctl status factline.service factline-deploy.service

# pipeline logs
journalctl -u factline.service -n 100 --no-pager

# deploy logs (quiet when there is nothing to do, by design)
journalctl -u factline-deploy.service -n 50 --no-pager

# run the pipeline right now, off-schedule
sudo systemctl start factline.service

# is the data fresh?
curl -s http://<EC2_HOST>/health.json

# roll back: point main at the good commit and let the box follow
git revert <bad-sha> && git push
```

Rollback is a normal commit. There is one code path onto the box and you
exercise it every day, which is the property that makes rollback boring.

---

## 6 · Why pull and not push

The honest tradeoff, worth being able to state in an interview:

**Pull wins here.** No credential is stored in GitHub, so there is nothing to
leak. Port 22 needs no public exposure — push-based deploys require it open to
GitHub's runner IP ranges, which are large and change. And the box verifies CI
status before applying a commit, so a half-tested merge cannot reach production.

**Push would win if** deploys had to be instant, or a deploy needed to run
something GitHub has and the box does not (a build step, a migration against
another service). Neither applies to a daily batch job.

The cost is up to three minutes of lag. For a pipeline that runs once a
weeknight, that is not a cost.

---

## 7 · Known gaps

- **No HTTPS yet.** Port 80 only until a domain points at the box; the TLS block
  is ready to swap into the Caddyfile.
- **No container.** Day 10. `uv.lock` already pins Python dependencies; Docker
  would add reproducible *system* packages, which starts to matter when the box
  stops being a pet.
- **Single instance, no redundancy.** Correct for this workload — a daily batch
  job that can miss a night and catch up, which `Persistent=true` handles.
- **`run-pipeline.sh` does not build reports yet.** It ingests and prints
  coverage; the `build-site` step lands on Day 6.
- **Private repo would need one token.** The status-API call and `git fetch` are
  anonymous today because the repo is public. A private repo needs a read-only
  PAT in a file on the box — still nothing stored in GitHub.
