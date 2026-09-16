"""Parquet data lake with DuckDB as the query engine.

Chosen over Postgres on purpose: this is a single-machine analytical workload over
a few million rows. Parquet plus DuckDB gives columnar scans, real SQL and zero
operational surface. Postgres would add a service to run, back up and connect to,
and would buy nothing here. If the universe ever grew past one machine the answer
would be object storage plus the same DuckDB queries, not a different database.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import date
from pathlib import Path
from typing import Any

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq
import structlog

from factline.models.edgar import FactRow, QuarantinedRecord

log = structlog.get_logger(__name__)

FACT_SCHEMA = pa.schema(
    [
        ("cik", pa.int64()),
        ("ticker", pa.string()),
        ("taxonomy", pa.string()),
        ("tag", pa.string()),
        ("unit", pa.string()),
        ("val", pa.float64()),
        ("start", pa.date32()),
        ("end", pa.date32()),
        ("filed", pa.date32()),
        ("accn", pa.string()),
        ("form", pa.string()),
        ("fy", pa.int32()),
        ("fp", pa.string()),
        ("frame", pa.string()),
        ("is_forward_looking", pa.bool_()),
    ]
)


class FactLake:
    """Reads and writes the fact tables. One parquet file per ticker."""

    def __init__(self, clean_dir: Path, quarantine_dir: Path) -> None:
        self.clean_dir = clean_dir
        self.quarantine_dir = quarantine_dir
        self.facts_dir = clean_dir / "facts"
        self.facts_dir.mkdir(parents=True, exist_ok=True)
        self.quarantine_dir.mkdir(parents=True, exist_ok=True)

    # ----------------------------------------------------------------- write

    def write_facts(self, ticker: str, rows: Sequence[FactRow]) -> Path:
        """Overwrite this ticker's partition. Idempotent by construction:
        re-running the pipeline produces the same file, never appends duplicates."""
        if not rows:
            raise ValueError(f"refusing to write an empty fact partition for {ticker}")

        table = pa.Table.from_pylist([r.model_dump() for r in rows], schema=FACT_SCHEMA)
        path = self.facts_dir / f"ticker={ticker.upper()}.parquet"
        tmp = path.with_suffix(".tmp")
        pq.write_table(table, tmp, compression="zstd")
        tmp.replace(path)
        log.info("lake.facts_written", ticker=ticker, rows=len(rows), path=str(path))
        return path

    def write_quarantine(self, ticker: str, records: Iterable[QuarantinedRecord]) -> Path | None:
        records = list(records)
        if not records:
            return None
        import json

        path = self.quarantine_dir / f"{ticker.upper()}.jsonl"
        with path.open("w") as fh:
            for rec in records:
                fh.write(json.dumps(rec.model_dump(mode="json")) + "\n")
        log.warning("lake.quarantined", ticker=ticker, count=len(records))
        return path

    # ----------------------------------------------------------------- query

    def connect(self) -> duckdb.DuckDBPyConnection:
        con = duckdb.connect(":memory:")
        glob = str(self.facts_dir / "*.parquet")
        con.execute(
            f"CREATE VIEW facts AS SELECT * FROM read_parquet('{glob}', union_by_name=true)"
        )
        return con

    def sql(self, query: str, params: list[Any] | None = None) -> list[tuple[Any, ...]]:
        con = self.connect()
        try:
            return con.execute(query, params or []).fetchall()
        finally:
            con.close()

    def facts_as_of(
        self,
        ticker: str,
        as_of: date,
        *,
        tag: str | None = None,
        taxonomy: str = "us-gaap",
    ) -> list[tuple[Any, ...]]:
        """Every fact that was PUBLIC on `as_of`, newest filing wins.

        This is the single most important query in the project. Two things make it
        point-in-time correct:

          1. `filed <= as_of` -- a number the market could not have seen on that
             date cannot appear in an as-of-that-date analysis.
          2. dedupe on (tag, unit, start, end) keeping the latest `filed` -- when a
             company restates a period, the restatement is a NEW filing. Before its
             filing date the original number is the correct one to use.

        Getting this wrong is what makes most amateur backtests meaningless.
        """
        con = self.connect()
        try:
            clauses = ["ticker = ?", "taxonomy = ?", "filed <= ?"]
            params: list[Any] = [ticker.upper(), taxonomy, as_of]
            if tag is not None:
                clauses.append("tag = ?")
                params.append(tag)
            where = " AND ".join(clauses)

            return con.execute(
                f"""
                WITH visible AS (
                    SELECT *,
                           ROW_NUMBER() OVER (
                               PARTITION BY tag, unit, start, "end"
                               ORDER BY filed DESC, accn DESC
                           ) AS revision_rank
                    FROM facts
                    WHERE {where}
                )
                SELECT tag, unit, start, "end", val, filed, accn, form
                FROM visible
                WHERE revision_rank = 1
                ORDER BY tag, "end" DESC
                """,
                params,
            ).fetchall()
        finally:
            con.close()

    def coverage(self) -> list[tuple[Any, ...]]:
        """Per-ticker row counts and date span — the basis of the quality report."""
        return self.sql(
            """
            SELECT ticker,
                   COUNT(*)                AS rows,
                   COUNT(DISTINCT tag)     AS tags,
                   MIN("end")              AS earliest_period,
                   MAX("end")              AS latest_period,
                   MAX(filed)              AS latest_filing
            FROM facts
            GROUP BY ticker
            ORDER BY ticker
            """
        )
