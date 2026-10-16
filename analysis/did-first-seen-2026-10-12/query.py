#!/usr/bin/env python3
"""Hourly first-seen aggregation.

Reads a caller-supplied SQLite database in read-only mode and writes
counts only: one UTC hour per row, plus summary figures. The window is
fixed in this file. The script does not choose a database path of its own
and it refuses the live archive filename.

messages.did is the sender identifier stored when the venue from-field
starts with "did:". messages.ts is an ISO-8601 UTC timestamp with a Z
suffix and fractional seconds. Indexes used on the clone: m_did(did) and
m_ts(ts).

First-seen is MIN(messages.ts) per distinct did matching the glob
did:key:*, among messages inside the fixed ts window. Pass --max-rowid
to also require messages.rowid <= that value. Without that cap the
counts can move as the archive grows. The hour is the UTC hour of that
earliest timestamp:
substr(first_ts, 1, 13), written as hour_utc by appending ":00:00Z".

The window starts at 2026-08-27T00:00:00Z and ends at
2026-10-13T00:57:28.117985Z. The last hour is 2026-10-13T00:00:00Z.
The csv has one row per UTC hour in that window, inclusive. An hour
absent from the aggregation is written as first_seen 0.
"""

import argparse
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

LIVE_FILENAME = "tc-archive.sqlite"

WINDOW_START = datetime(2026, 8, 27, 0, tzinfo=timezone.utc)
WINDOW_END_HOUR = datetime(2026, 10, 13, 0, tzinfo=timezone.utc)
WINDOW_START_TEXT = "2026-08-27T00:00:00Z"
WINDOW_END_TEXT = "2026-10-13T00:57:28.117985Z"

def rowid_clause(max_rowid):
    return "AND rowid <= ?" if max_rowid is not None else ""


def window_params(max_rowid):
    params = [WINDOW_START_TEXT, WINDOW_END_TEXT]
    if max_rowid is not None:
        params.append(int(max_rowid))
    return tuple(params)


def first_seen_sql(max_rowid):
    return f"""
SELECT CASE
         WHEN first_ts GLOB '????-??-??T??:*' THEN substr(first_ts, 1, 13)
         WHEN first_ts IS NULL THEN 'NULL'
         ELSE 'BAD'
       END AS hour,
       COUNT(*) AS n
FROM (
  SELECT MIN(ts) AS first_ts
  FROM messages NOT INDEXED
  WHERE did GLOB 'did:key:*'
    AND ts >= ?
    AND ts <= ?
    {rowid_clause(max_rowid)}
  GROUP BY did
)
GROUP BY hour
"""


def coverage_sql(max_rowid):
    return f"""
SELECT substr(ts, 1, 13) AS hour, COUNT(*) AS n
FROM messages INDEXED BY m_ts
WHERE ts >= ? AND ts <= ?
  {rowid_clause(max_rowid)}
GROUP BY hour
"""


def archive_start_sql(max_rowid):
    return f"""
SELECT ts FROM messages INDEXED BY m_ts
WHERE ts >= ? AND ts <= ?
  {rowid_clause(max_rowid)}
ORDER BY ts ASC
LIMIT 1
"""


def hour_range(start, end):
    cur = start
    while cur <= end:
        yield cur
        cur += timedelta(hours=1)


def fmt_hour(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def median_even_odd(values):
    """Median of covered hours only.

    n odd: middle value after sorting ascending, 1-based position n//2+1.
    n even: arithmetic mean of the two central values.
    Uncovered hours are not included in values.
    """
    s = sorted(values)
    n = len(s)
    if n == 0:
        raise ValueError("no covered hours; median undefined")
    if n % 2 == 1:
        mid = s[n // 2]
        return {
            "n": n,
            "parity": "odd",
            "median": mid,
            "lo": mid,
            "hi": mid,
            "position": (n // 2) + 1,
        }
    lo = s[n // 2 - 1]
    hi = s[n // 2]
    return {
        "n": n,
        "parity": "even",
        "median": (lo + hi) / 2,
        "lo": lo,
        "hi": hi,
        "position": (n // 2, n // 2 + 1),
    }


def gap_estimate(uncovered_count, median):
    """Uncovered hours times the covered-hour median.

    An estimate, not a measured count. Not added to the total.
    """
    return uncovered_count * median


def hourly_rows(first_by_hour):
    """Dense UTC grid for the fixed window. Missing hours are 0."""
    rows = []
    for dt in hour_range(WINDOW_START, WINDOW_END_HOUR):
        hour = fmt_hour(dt)
        rows.append((hour, int(first_by_hour.get(hour, 0))))
    return rows


def parse_args(argv):
    parser = argparse.ArgumentParser(
        description="Hourly first-seen counts from a read-only SQLite clone."
    )
    parser.add_argument("db", nargs="?", help="SQLite clone path")
    parser.add_argument("--db", dest="db_flag", help="SQLite clone path")
    parser.add_argument("--csv", required=True, help="Hourly CSV output path")
    parser.add_argument("--summary", required=True, help="Summary figures output path")
    parser.add_argument(
        "--max-rowid",
        type=int,
        default=None,
        help=(
            "Include only messages with rowid <= this value. "
            "Required for a reproducible run. If omitted, counts can move "
            "as the archive grows."
        ),
    )
    args = parser.parse_args(argv)
    if bool(args.db) == bool(args.db_flag) and args.db:
        parser.error("pass the database path once")
    db = args.db or args.db_flag
    if not db:
        parser.error("database path is required")
    if args.max_rowid is not None and args.max_rowid < 0:
        parser.error("--max-rowid must be >= 0")
    return db, args.csv, args.summary, args.max_rowid


def refuse_live(path):
    if path.name == LIVE_FILENAME:
        raise SystemExit(
            "refusing a database path whose filename is the live archive; pass a clone"
        )
    resolved = path.resolve()
    if resolved.name == LIVE_FILENAME:
        raise SystemExit(
            "refusing a database path whose filename is the live archive; pass a clone"
        )
    return resolved


def open_readonly(db):
    resolved = refuse_live(Path(db))
    if not resolved.is_file():
        raise SystemExit("database path is not a file")
    uri = resolved.as_uri() + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.execute("PRAGMA query_only = ON")
    return conn


def fmt_num(value):
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def load_first_seen(conn, max_rowid):
    counts = {}
    bad = 0
    outside = 0
    last = fmt_hour(WINDOW_END_HOUR)
    for hour, n in conn.execute(first_seen_sql(max_rowid), window_params(max_rowid)):
        n = int(n)
        if hour in ("BAD", "NULL"):
            bad += n
            continue
        full = hour + ":00:00Z"
        if full < WINDOW_START_TEXT or full > last:
            outside += n
            continue
        counts[full] = n
    if bad:
        raise SystemExit("first-seen timestamps included unusable values")
    return counts, outside


def load_coverage(conn, max_rowid):
    covered = set()
    for hour, n in conn.execute(coverage_sql(max_rowid), window_params(max_rowid)):
        if int(n) > 0 and isinstance(hour, str) and len(hour) == 13:
            covered.add(hour + ":00:00Z")
    return covered


def archive_start(conn, max_rowid):
    row = conn.execute(archive_start_sql(max_rowid), window_params(max_rowid)).fetchone()
    if row is None or row[0] is None:
        raise SystemExit("archive start is missing")
    return row[0]


def write_csv(path, rows):
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write("hour_utc,first_seen\n")
        for hour, count in rows:
            handle.write(f"{hour},{count}\n")


def write_summary(path, archive_started, rows, covered, outside, max_rowid):
    covered_counts = [count for hour, count in rows if hour in covered]
    stats = median_even_odd(covered_counts)
    total = sum(count for _, count in rows)
    peak_hour, peak_count = max(rows, key=lambda item: item[1])
    last_hour = fmt_hour(WINDOW_END_HOUR)
    last_count = dict(rows)[last_hour]
    uncovered = len(rows) - len(covered_counts)
    estimate = gap_estimate(uncovered, stats["median"])
    position = stats["position"]
    if isinstance(position, tuple):
        position_text = " and ".join(str(item) for item in position)
    else:
        position_text = str(position)
    if max_rowid is None:
        freeze_line = "rowid cap: omitted. Counts can move as the archive grows."
    else:
        freeze_line = f"freeze rowid: {int(max_rowid)}."
    lines = [
        f"window: {WINDOW_START_TEXT} through {WINDOW_END_TEXT} "
        f"(last hour {last_hour}).",
        freeze_line,
        f"archive start: {archive_started}.",
        "total distinct did:keys whose first-seen hour falls inside the window: "
        f"{total}.",
        f"peak hour: {peak_hour} count={peak_count}.",
        f"last hour count: {last_hour} count={last_count}.",
        f"median hourly first-seen count: {fmt_num(stats['median'])}.",
        f"median rule: n={stats['n']} is {stats['parity']}. "
        "Median is the middle value after sorting ascending, "
        f"at 1-based position {position_text}. "
        f"Central values: {fmt_num(stats['lo'])} and {fmt_num(stats['hi'])}.",
        f"coverage: {stats['n']} hours have at least one archived message; "
        f"{uncovered} hours have none.",
        "gap estimate: "
        f"{uncovered} × {fmt_num(stats['median'])} = {fmt_num(estimate)}. "
        "This is an estimate (missing hours × median), not a measured count, "
        "and it is not added to the total.",
        f"first-seen hours outside the fixed window: {outside}.",
        "source: read-only query of a read-only APFS clone of the live archive. "
        "The live database was not queried.",
        "",
    ]
    Path(path).write_text("\n".join(lines), encoding="utf-8")


def main(argv=None):
    db, csv_path, summary_path, max_rowid = parse_args(argv)
    if max_rowid is None:
        print(
            "warning: --max-rowid omitted; counts can move as the archive grows",
            file=sys.stderr,
        )
    conn = open_readonly(db)
    try:
        started = archive_start(conn, max_rowid)
        counts, outside = load_first_seen(conn, max_rowid)
        covered = load_coverage(conn, max_rowid)
        rows = hourly_rows(counts)
        write_csv(csv_path, rows)
        write_summary(summary_path, started, rows, covered, outside, max_rowid)
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
