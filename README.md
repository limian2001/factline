# factline

US equity research where **every number traces back to a primary SEC filing**.

The system computes financial metrics in Python and lets the language model do
only language. Each figure in a generated report carries the accession number of
the filing it came from, and a validator rejects any report containing a number
that is not in the fact table. The LLM has no arithmetic to get wrong.

```
EDGAR XBRL ──▶ validate ──▶ fact table ──▶ LLM narrative ──▶ citation validator
   (raw/)      (pydantic)    (parquet)      (facts only)      (fails the build)
```

## Why EDGAR and not a fundamentals vendor

A vendor's normalised fundamentals are easier to consume and would break the two
properties this design rests on:

| | EDGAR XBRL | Vendor fundamentals |
|---|---|---|
| Citation to a real document | accession number per fact | none |
| Filing date (point-in-time) | `filed` on every fact | usually collapsed |
| Restatement history | amendments are separate filings | often overwritten |
| Cost | free | $50–300/mo |

Prices are a different story — those are paid, because free price feeds are
unreliable. Fundamentals stay on EDGAR.

## Point-in-time correctness

The metric layer can only see what was public on the as-of date. Two rules:

1. `filed <= as_of` — a number the market had not seen cannot enter the analysis.
2. On a restatement, the revision with the latest `filed` that is still `<= as_of`
   wins. Before an amendment is filed, the **original** figure is the correct one.

This is demonstrable from the CLI:

```console
$ factline as-of AAPL --on 2025-02-15 --tag Revenues
│ 2024-09-29 │ 2024-12-28 │ 124,300,000,000 │ USD │ 2025-01-31 │ 10-Q   │ …
                             ^^^^^^^ original filing

$ factline as-of AAPL --on 2025-06-01 --tag Revenues
│ 2024-09-29 │ 2024-12-28 │ 124,400,000,000 │ USD │ 2025-05-02 │ 10-Q/A │ …
                             ^^^^^^^ after the amendment
```

Skipping this is why most amateur backtests are meaningless.

## Quick start

```bash
cp .env.example .env        # set SEC_USER_AGENT="Your Name you@email.com"
make install
make check                  # ruff + 24 tests, no network
factline ingest             # pulls the 15-name universe from EDGAR
factline coverage
factline as-of AAPL --on 2025-02-15 --tag Revenues
```

`SEC_USER_AGENT` is validated at startup: the SEC returns a bare 403 without a
contact header, and that is the most common first-run failure.

## Design decisions

**Parquet + DuckDB, not Postgres.** Single-machine analytical workload over a few
million rows. Columnar scans and real SQL with no service to run or back up.
Postgres would add operational surface and buy nothing. Past one machine the
answer is object storage with the same DuckDB queries, not a different database.

**Token bucket, not `time.sleep`.** The SEC blocks an IP for ~10 minutes past
10 req/s. A bucket spends accumulated headroom immediately and only blocks once
it is gone — bursty work finishes faster at the same average rate. The clock is
injectable, so the limiter is tested deterministically rather than with sleeps.

**Quarantine, not drop.** A record that fails validation is written to
`quarantine/` with its reason and payload. "We lost 4% of rows and nobody
noticed" is the failure mode that makes a research system untrustworthy.

**raw/ is immutable.** Responses land untouched and dated. `clean/` is derived and
can be deleted and rebuilt with no network access.

**15 names, not 3000.** Enough to surface every structural problem in EDGAR data
— non-calendar fiscal years (4 of 15), restatements, tag drift — without the long
tail. Universe size is a config change, not a rewrite.

## Layout

```
src/factline/
  config.py            settings, with a validator for the SEC contact header
  universe.py          the covered names and their fiscal year ends
  clients/base.py      token bucket, retry with backoff, disk cache
  clients/edgar.py     EDGAR client and companyfacts flattening
  models/edgar.py      pydantic validation; filed >= period end is enforced
  store/lake.py        parquet writes, DuckDB queries, the as-of query
  ingest.py            raw -> validate -> clean + quarantine
  cli.py               ingest / coverage / as-of
```

## Deployment

The box deploys itself. Every three minutes it asks GitHub whether `main` has
moved and whether CI passed on that commit; if both, it checks out that exact
SHA, installs from `uv.lock`, and reinstalls its systemd units. No credentials
are stored in GitHub and no inbound SSH is needed, which is the same pull model
Argo CD and Flux use. Runbook: [docs/DEPLOY.md](docs/DEPLOY.md).

```
laptop ──push──▶ GitHub ──lint + test──▶ green
                                           └──▶ EC2 polls, verifies, deploys
```

## Status

Data and metric foundation complete and tested. Report generation, the eval
harness and the cost ledger are next.

