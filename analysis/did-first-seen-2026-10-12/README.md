# Hourly first-seen counts

Counts only. This directory has no identifiers, message text, keys, usernames, or archive paths.

The measurement is the number of distinct senders whose earliest archived timestamp falls in each UTC hour. The hourly file is freeze rowid 114505988, not a later archive.

## Method

Schema: `messages.did` is the sender stored when the venue from-field starts with `did:`. `messages.ts` is the message timestamp, ISO-8601 text with a Z suffix and fractional seconds.

First-seen is `MIN(ts)` per distinct value matching the glob `did:key:*`, among messages inside the ts window with `rowid` <= 114505988. The hour is the UTC hour of that earliest timestamp (`substr(ts, 1, 13)`), written as `hour_utc` with `:00:00Z` appended.

Window: `2026-08-27T00:00:00Z` through `2026-10-13T00:57:28.117985Z`. The last hour is `2026-10-13T00:00:00Z`. The csv has one row per UTC hour in that window, inclusive (1129 data rows). An hour with no archived messages is written as `first_seen=0`. That zero is not an observed quiet hour. A covered hour with zero new matches is also 0, and that zero is observed.

Median rule: hours with archive coverage only (at least one archived message of any sender). Uncovered hours are excluded. For this freeze n=1095, which is odd, so the median is the middle value after sorting ascending, at 1-based position 548. That median is 12850.

Gap estimate: the 34 uncovered hours, if each matched the median covered hour, are 34 × 12850 = 436900. This is an estimate (missing hours × median), not a measured count, and it is not added to the total of 20523034. Peak hour: `2026-09-06T09:00:00Z`, count 131688. Last hour count: 5104.

The figures come from a read-only query of a read-only APFS clone of the live archive. The live database was not queried.

## Reproduce

```
python3 query.py CLONE.sqlite --max-rowid 114505988 --csv did-first-seen-hourly.csv --summary did-first-seen-summary.txt
```

`CLONE.sqlite` is a read-only APFS clone of the live archive. `--max-rowid` keeps the run on this freeze. Omitting it lets the counts move as the archive grows.

## Checksums

`SHA256SUMS` covers `did-first-seen-hourly.csv`, `did-first-seen-summary.txt`, `query.py`, and this README. From this directory:

```
shasum -a 256 -c SHA256SUMS
```
