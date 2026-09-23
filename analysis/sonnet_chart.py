#!/usr/bin/env python3
"""sonnet_chart.py - ballots per minute for a set of entries, as an SVG chart.

Reads a tca-schema SQLite file of mb-sonnet-2-votes (the imported public snapshot), counts
well-formed ballots per minute per entry, and draws the candidate set against a contrast entry.
Standard library only, so it runs anywhere. Output is SVG; on macOS convert to PNG with

    qlmanage -t -s 2400 -o . sonnet2-votes-per-minute.svg

  python3 sonnet_chart.py --votes-db votes-snapshot.sqlite --out sonnet2-votes-per-minute.svg
"""
import argparse, collections, json, os, sqlite3

W, H = 1600, 900
L, R, T, B = 110, 40, 90, 120      # margins
COLORS = ["#d7263d", "#f46036", "#f5b700"]  # candidate set
CONTRAST = "#2e86ab"


def esc(s):
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--votes-db", default="votes-snapshot.sqlite")
    p.add_argument("--entries", default="vngalaxy3,dongqn-s2,abigayle")
    p.add_argument("--contrast", default="maragung-flop")
    p.add_argument("--deadline", default="2026-09-18T12:00:00Z")
    p.add_argument("--title", default="sonnet-2 votes room: ballots per minute, Sep 18, 08:30Z to the 12:00Z deadline")
    p.add_argument("--note", default="the three colored lines move together: minute correlation 0.997 to 0.999 | one request-id signature on 96.7% of their ballots, 4,773 shared voters",
                   help="annotation under the legend; split lines with ' | '")
    p.add_argument("--out", default="sonnet2-votes-per-minute.svg")
    a = p.parse_args()
    cl = [e.strip() for e in a.entries.split(",") if e.strip()]
    dl = a.deadline[:19]
    c = sqlite3.connect("file:%s?mode=ro" % a.votes_db, uri=True)
    per = collections.defaultdict(collections.Counter)   # entry -> minute -> n
    first = None
    for ts, did, text in c.execute("SELECT ts, did, text FROM messages WHERE room='mb-sonnet-2-votes' AND text LIKE '%sonnet.ballot.v1%' ORDER BY seq"):
        if not text or text[0] != "{" or (ts or "")[:19] > dl:
            continue
        try:
            j = json.loads(text)
        except Exception:
            continue
        if not isinstance(j, dict) or j.get("type") != "sonnet.ballot.v1" or j.get("voter_did") != did:
            continue
        e = j.get("entry_id")
        if not isinstance(e, str):
            continue
        mn = ts[:16]
        first = mn if first is None or mn < first else first
        per[e][mn] += 1
    c.close()
    if first is None:
        raise SystemExit("no ballots found")
    # minute axis from first captured minute to the deadline minute
    import datetime as dt
    t0 = dt.datetime.strptime(first, "%Y-%m-%dT%H:%M"); t1 = dt.datetime.strptime(dl[:16], "%Y-%m-%dT%H:%M")
    minutes = []
    t = t0
    while t <= t1:
        minutes.append(t.strftime("%Y-%m-%dT%H:%M")); t += dt.timedelta(minutes=1)
    series = {e: [per[e].get(m, 0) for m in minutes] for e in cl + [a.contrast]}
    ymax = max(max(v) for v in series.values()) or 1
    ymax = int((ymax // 100 + 1) * 100)
    pw, ph = W - L - R, H - T - B
    x = lambda i: L + pw * i / max(1, len(minutes) - 1)
    y = lambda v: T + ph - ph * v / ymax

    out = ['<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" viewBox="0 0 %d %d">' % (W, H, W, H),
           '<rect width="%d" height="%d" fill="#ffffff"/>' % (W, H),
           '<style>text{font-family:Helvetica,Arial,sans-serif;fill:#1a1a1a}</style>',
           '<text x="%d" y="44" font-size="30" font-weight="bold">%s</text>' % (L, esc(a.title))]
    # gridlines
    for g in range(0, ymax + 1, 100):
        out.append('<line x1="%d" y1="%.1f" x2="%d" y2="%.1f" stroke="#e6e6e6"/>' % (L, y(g), W - R, y(g)))
        out.append('<text x="%d" y="%.1f" font-size="18" text-anchor="end">%d</text>' % (L - 12, y(g) + 6, g))
    # x ticks every 30 minutes
    for i, m in enumerate(minutes):
        if m[14:16] in ("00", "30"):
            out.append('<line x1="%.1f" y1="%d" x2="%.1f" y2="%d" stroke="#cccccc"/>' % (x(i), T, x(i), T + ph))
            out.append('<text x="%.1f" y="%d" font-size="18" text-anchor="middle">%sZ</text>' % (x(i), T + ph + 30, m[11:16]))
    out.append('<text x="%d" y="%d" font-size="18" text-anchor="middle">UTC, Sep 18 2026</text>' % (L + pw / 2, T + ph + 62))
    out.append('<text x="30" y="%d" font-size="18" transform="rotate(-90 30 %d)" text-anchor="middle">ballots per minute</text>' % (T + ph / 2, T + ph / 2))
    # deadline marker
    out.append('<line x1="%.1f" y1="%d" x2="%.1f" y2="%d" stroke="#1a1a1a" stroke-dasharray="8,6"/>' % (x(len(minutes) - 1), T, x(len(minutes) - 1), T + ph))
    out.append('<text x="%.1f" y="%d" font-size="18" text-anchor="end">deadline</text>' % (x(len(minutes) - 1) - 8, T + 22))

    def path(vals):
        return "M " + " L ".join("%.1f %.1f" % (x(i), y(v)) for i, v in enumerate(vals))
    out.append('<path d="%s" fill="none" stroke="%s" stroke-width="3"/>' % (path(series[a.contrast]), CONTRAST))
    for e, col in zip(cl, COLORS):
        out.append('<path d="%s" fill="none" stroke="%s" stroke-width="2.5" stroke-opacity="0.75"/>' % (path(series[e]), col))
    # legend
    ly = T + 20
    items = [(a.contrast, CONTRAST)] + list(zip(cl, COLORS))
    lx = L + 20
    for name, col in items:
        out.append('<rect x="%d" y="%d" width="28" height="6" fill="%s"/>' % (lx, ly - 4, col))
        out.append('<text x="%d" y="%d" font-size="20">%s  (peak %d/min)</text>' % (lx + 38, ly + 4, esc(name), max(series[name])))
        ly += 30
    ny = ly + 8
    for line in a.note.split(" | "):
        out.append('<text x="%d" y="%d" font-size="18" fill="#444">%s</text>' % (L + 20, ny, esc(line)))
        ny += 26
    out.append('<text x="%d" y="%d" font-size="16" fill="#666">source: signed public snapshot of mb-sonnet-2-votes, github.com/lastbubble2035/tca</text>' % (L, H - 18))
    out.append("</svg>")
    open(a.out, "w", encoding="utf-8").write("\n".join(out))
    print("wrote", a.out, "| minutes", len(minutes), "| peaks", {e: max(v) for e, v in series.items()})


if __name__ == "__main__":
    main()
