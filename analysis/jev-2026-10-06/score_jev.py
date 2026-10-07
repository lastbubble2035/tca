#!/usr/bin/env python3
"""Score Technocore lobby texts with TypeSafe Jev (stdlib only).

One POST per unique text, all three questions together. Exact-text results are
cached in jev_cache.jsonl and reused. copies is the full-day exact-text count.

final_kind is "template" when copies >= 50, otherwise Jev's raw choice (jev_kind).
flag uses jev_kind, not final_kind: nonsense, or coherent < 0.4. The copy-count
override does not clear a low coherent score.

  --sample N    score a uniform random sample of N messages and stop
  --full        score every remaining unique text in the day

Neither mode rewrites jev_results.csv. A bare run does not score anything.
"""
import argparse
import csv
import hashlib
import json
import os
import random
import re
import sys
import threading
import time
import unicodedata
import urllib.error
import urllib.request
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, as_completed, wait

API = "https://api.typesafe.ai/v1/systemone"
MODEL = "jev-latest"
WORKERS = 16
MAX_ATTEMPTS = 6
COST_PER_MILLION = 0.042
TEMPLATE_AT = 50
HERE = os.path.dirname(os.path.abspath(__file__))
RESULTS_CSV = os.path.join(HERE, "jev_results.csv")
CACHE_PATH = os.path.join(HERE, "jev_cache.jsonl")
CACHE_V2_PATH = os.path.join(HERE, "jev_cache_v2.jsonl")
DAY_PATH = os.path.join(HERE, "lobby-day.jsonl")
BEFORE_CSV = os.path.join(HERE, "sample2000.csv")
TOP20_CSV = os.path.join(HERE, "norm-top20.csv")

# Normalization (message body only), in order:
#   1. Unicode NFKC
#   2. Collapse whitespace to one space and strip
#   3. Repeatedly strip one trailing salt until none match:
#        a. middle dot / bullet: \s*[·•]\s*[A-Za-z0-9]{4,6}\s*$
#        b. dagger:             \s*†\d+\s*$
#        c. therefore-sign:     \s*∴\d+\s*$   i.e. \s*\u2234\d+\s*$
#      Then ONCE, after a–c have stopped: a 4-6 alnum token after the
#      last sentence punctuation, unless the token is in SALT_STOP.
#      That last rule does not loop, so it cannot eat the real last word
#      of an unsalted sentence.
#   4. Lowercase
#   5. Collapse whitespace again
SALT_DOT = re.compile(r"\s*[\u00b7\u2022]\s*[A-Za-z0-9]{4,6}\s*$")
SALT_DAGGER = re.compile(r"\s*\u2020\d+\s*$")
SALT_THEREFORE = re.compile(r"\s*\u2234\d+\s*$")
SALT_TAIL = re.compile(r"^(?P<body>.*[.!?])\s+(?P<tail>[A-Za-z0-9]{4,6})$")
SALT_STOP = frozenset({"signed", "today", "lobby", "flop", "node", "nodes"})
WS_RUN = re.compile(r"\s+")

SUBSTANCE_LABELS = ["None", "Some", "Substantive"]
QUESTIONS = {
    "kind": {
        "type": "choice",
        "instructions": "What kind of message is this?",
        "criteria": {
            "original": "Written by a person for this conversation",
            "template": "Boilerplate or a reused scripted line",
            "spam": "Promotion or noise with no content",
            "nonsense": "Grammatical but meaningless word salad",
        },
    },
    "coherent": {
        "type": "noul",
        "instructions": "This sentence makes a meaningful claim or asks a meaningful question",
    },
    "substance": {
        "type": "score",
        "instructions": "How much substance does this message have?",
        "criteria": [
            "None: the message has no informational content",
            "Some: the message has a little content but is thin",
            "Substantive: the message makes a real point with actual content",
        ],
    },
}
QUESTIONS_V2 = {
    "kind": {
        "type": "choice",
        "instructions": "What kind of message is this?",
        "criteria": {
            "original": "Written by a person for this conversation",
            "template": "Boilerplate or a reused scripted line",
            "spam": "Promotion or noise with no content",
            "nonsense": "Grammatical but meaningless word salad",
            "filler": "Technical jargon assembled into a plausible sentence that refers to nothing real",
        },
    },
    "coherent": {
        "type": "noul",
        "instructions": "This sentence makes a meaningful claim or asks a meaningful question",
    },
    "specific": {
        "type": "noul",
        "instructions": "The message refers to a specific real event, number, person, or prior message",
    },
    "substance": QUESTIONS["substance"],
}
FIELDS = [
    "seq", "ts", "did", "text", "copies", "jev_kind", "final_kind",
    "conf", "coherent", "substance", "flag",
]
FIELDS_V2 = [
    "seq", "ts", "did", "text", "norm", "copies", "jev_kind", "final_kind",
    "conf", "coherent", "specific", "substance", "flag",
]
KINDS = ("original", "template", "spam", "nonsense")
KINDS_V2 = ("original", "template", "spam", "nonsense", "filler")


def normalize(text):
    """Return the normalized copy-count key for one message body."""
    s = unicodedata.normalize("NFKC", text or "")
    s = WS_RUN.sub(" ", s).strip()
    while True:
        m = SALT_DOT.search(s)
        if m:
            s = s[:m.start()].rstrip()
            continue
        m = SALT_DAGGER.search(s)
        if m:
            s = s[:m.start()].rstrip()
            continue
        m = SALT_THEREFORE.search(s)
        if m:
            s = s[:m.start()].rstrip()
            continue
        break
    m = SALT_TAIL.match(s)
    if m and m.group("tail").lower() not in SALT_STOP:
        s = m.group("body").rstrip()
    return WS_RUN.sub(" ", s.lower()).strip()


def normalize_trace(text):
    """Same as normalize, plus which salt rules fired and any rule-c tail."""
    s = unicodedata.normalize("NFKC", text or "")
    s = WS_RUN.sub(" ", s).strip()
    dot = dagger = 0
    while True:
        m = SALT_DOT.search(s)
        if m:
            s = s[:m.start()].rstrip()
            dot += 1
            continue
        m = SALT_DAGGER.search(s)
        if m:
            s = s[:m.start()].rstrip()
            dagger += 1
            continue
        m = SALT_THEREFORE.search(s)
        if m:
            s = s[:m.start()].rstrip()
            continue
        break
    tail = None
    m = SALT_TAIL.match(s)
    if m and m.group("tail").lower() not in SALT_STOP:
        tail = m.group("tail")
        s = m.group("body").rstrip()
    norm = WS_RUN.sub(" ", s.lower()).strip()
    return norm, dot, dagger, tail

_print_lock = threading.Lock()
_first_error = False


def redact(text):
    key = os.environ.get("TYPESAFE_API_KEY") or ""
    if key and text and key in text:
        text = text.replace(key, "[REDACTED]")
    return text


def note_error(msg):
    """Print the first error verbatim, with the API key removed if it appears."""
    global _first_error
    msg = redact(msg)
    with _print_lock:
        if not _first_error:
            _first_error = True
            print(msg, flush=True)


def peak_index(probabilities, n):
    best_i, best_p = 0, -1.0
    for i in range(n):
        raw = probabilities.get(str(i), probabilities.get(i, 0.0))
        p = float(raw)
        if p > best_p:
            best_i, best_p = i, p
    return best_i


def parse_response(data):
    answers = data["answers"]
    kind_a = answers["kind"]
    coh_a = answers["coherent"]
    sub_a = answers["substance"]
    kind = kind_a["choice"]
    conf = kind_a.get("confidence")
    coherent = float(coh_a["noul"])
    probs = sub_a.get("probabilities") or {}
    if probs:
        substance = SUBSTANCE_LABELS[peak_index(probs, len(SUBSTANCE_LABELS))]
    else:
        score = float(sub_a["score"])
        substance = SUBSTANCE_LABELS[max(0, min(len(SUBSTANCE_LABELS) - 1, int(round(score))))]
    usage = data.get("usage") or {}
    tokens = int(usage.get("input_tokens") or 0)
    model = data.get("model") or ""
    out = {
        "kind": kind,
        "conf": None if conf is None else float(conf),
        "coherent": coherent,
        "substance": substance,
        "input_tokens": tokens,
        "model": model,
    }
    if "specific" in answers:
        out["specific"] = float(answers["specific"]["noul"])
    return out


def score_text(text, key, questions=None):
    body = json.dumps(
        {"state": text, "model": MODEL, "questions": questions or QUESTIONS},
        ensure_ascii=False,
    ).encode("utf-8")
    delay = 1.0
    last = "unknown error"
    for attempt in range(1, MAX_ATTEMPTS + 1):
        req = urllib.request.Request(
            API,
            data=body,
            method="POST",
            headers={
                "Authorization": "Bearer " + key,
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=90) as resp:
                raw = resp.read().decode("utf-8")
            return parse_response(json.loads(raw)), attempt
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8", "replace")
            last = f"HTTP {e.code}: {err_body}"
            note_error(last)
            if e.code in (401, 402, 403):
                raise RuntimeError(redact(last))
            retry_after = e.headers.get("Retry-After") if e.headers else None
            if attempt >= MAX_ATTEMPTS:
                break
            wait = delay
            if retry_after:
                try:
                    wait = max(wait, float(retry_after))
                except ValueError:
                    pass
            time.sleep(wait)
            delay *= 2
        except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as e:
            last = f"{type(e).__name__}: {e}"
            note_error(last)
            if attempt >= MAX_ATTEMPTS:
                break
            time.sleep(delay)
            delay *= 2
    raise RuntimeError(redact(last))


class Cache:
    """Unique-text judgments. key_field is 'text' (v1, raw) or 'norm' (v2)."""

    def __init__(self, path, key_field="text"):
        self.path = path
        self.key_field = key_field
        self.data = {}
        self.lock = threading.Lock()
        self._fh = None

    def load(self):
        if not os.path.exists(self.path):
            return
        with open(self.path, encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                rec = json.loads(line)
                self.data[rec[self.key_field]] = rec

    def add(self, rec):
        line = json.dumps(rec, ensure_ascii=False) + "\n"
        with self.lock:
            self.data[rec[self.key_field]] = rec
            if self._fh is None:
                self._fh = open(self.path, "a", encoding="utf-8")
            self._fh.write(line)
            self._fh.flush()

    def close(self):
        if self._fh is not None:
            self._fh.close()
            self._fh = None


def seed_from_results(cache, results_csv):
    """Copy unique texts already scored in jev_results.csv into the cache."""
    if not os.path.exists(results_csv):
        return 0
    csv.field_size_limit(10_000_000)
    added = 0
    seen = set()
    with open(results_csv, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            text = row["text"]
            if text in seen or text in cache.data:
                seen.add(text)
                continue
            seen.add(text)
            conf_raw = row.get("conf") or ""
            conf = float(conf_raw) if conf_raw != "" else None
            jev_kind = row.get("jev_kind") or row["kind"]
            rec = {
                "text": text,
                "jev_kind": jev_kind,
                "conf": conf,
                "coherent": float(row["coherent"]),
                "substance": row["substance"],
                "review": None if conf is None else conf < 0.6,
                "input_tokens": None,
                "model": "jev-1.13.0",
            }
            cache.add(rec)
            added += 1
    return added


def fmt_num(value):
    if value is None or value == "":
        return ""
    return f"{float(value):.6f}".rstrip("0").rstrip(".")


def final_kind_of(jev_kind, copies):
    if copies >= TEMPLATE_AT:
        return "template"
    return jev_kind


def is_flagged(jev_kind, coherent):
    return jev_kind == "nonsense" or float(coherent) < 0.4


def annotate(row, rec, copies):
    jev_kind = rec["jev_kind"]
    coherent = float(rec["coherent"])
    conf = rec["conf"]
    return {
        "seq": row.get("seq"),
        "ts": row.get("ts") or "",
        "did": row.get("did") or "",
        "text": row.get("text") or "",
        "copies": int(copies),
        "jev_kind": jev_kind,
        "final_kind": final_kind_of(jev_kind, copies),
        "conf": fmt_num(conf),
        "coherent": fmt_num(coherent),
        "substance": rec["substance"],
        "flag": "true" if is_flagged(jev_kind, coherent) else "false",
    }


def count_messages(path):
    n = 0
    with open(path, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                n += 1
    return n


def load_sample(path, n, seed, total):
    """Uniform sample of message indices. Order is Random(seed).sample order."""
    rng = random.Random(seed)
    chosen = rng.sample(range(total), n)
    slots = {idx: pos for pos, idx in enumerate(chosen)}
    picked = [None] * n
    i = 0
    with open(path, encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            if i in slots:
                picked[slots[i]] = json.loads(line)
            i += 1
            if i % 500000 == 0:
                print(f"read {i}", flush=True)
    if any(row is None for row in picked):
        raise RuntimeError("sample missed a message index")
    return picked


def full_day_copies(path, texts):
    need = set(texts)
    counts = {t: 0 for t in need}
    i = 0
    with open(path, encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            i += 1
            text = json.loads(line).get("text") or ""
            if text in need:
                counts[text] += 1
            if i % 500000 == 0:
                print(f"counted {i}", flush=True)
    return counts


def score_many(texts, key, cache):
    calls = 0
    tokens = 0
    model = ""
    errors = []
    done = 0
    lock = threading.Lock()

    def work(text):
        parsed, ncalls = score_text(text, key)
        return text, parsed, ncalls

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futures = [pool.submit(work, text) for text in texts]
        for fut in as_completed(futures):
            try:
                text, parsed, ncalls = fut.result()
            except Exception as e:
                errors.append(redact(str(e)))
                continue
            conf = parsed["conf"]
            cache.add({
                "text": text,
                "jev_kind": parsed["kind"],
                "conf": conf,
                "coherent": parsed["coherent"],
                "substance": parsed["substance"],
                "review": None if conf is None else conf < 0.6,
                "input_tokens": parsed["input_tokens"],
                "model": parsed["model"],
            })
            with lock:
                calls += ncalls
                tokens += parsed["input_tokens"]
                if parsed["model"]:
                    model = parsed["model"]
                done += 1
                if done % 50 == 0 or done == len(texts):
                    print(f"scored {done}/{len(texts)}", flush=True)
    return calls, tokens, model, errors, done


def unique_in_order(rows):
    texts = []
    seen = set()
    for row in rows:
        text = row.get("text") or ""
        if text not in seen:
            seen.add(text)
            texts.append(text)
    return texts


def write_csv(path, annotated_rows):
    tmp = path + ".partial"
    with open(tmp, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="ignore")
        w.writeheader()
        for row in annotated_rows:
            w.writerow(row)
    os.replace(tmp, path)


def pct_line(label, counts, n, kinds=KINDS):
    lines = []
    seen = set()
    for name in list(kinds) + sorted(k for k in counts if k not in kinds):
        if name in seen:
            continue
        seen.add(name)
        c = counts.get(name, 0)
        lines.append("%s_%s: %d (%.2f%%)" % (label, name, c, (100.0 * c / n) if n else 0.0))
    return lines


def print_summary(rows, unique_n, cached_n, new_n, calls, tokens, model):
    n = len(rows)
    final_counts = {}
    jev_counts = {}
    flagged_rows = 0
    dids = set()
    flagged_dids = set()
    for row in rows:
        final_counts[row["final_kind"]] = final_counts.get(row["final_kind"], 0) + 1
        jev_counts[row["jev_kind"]] = jev_counts.get(row["jev_kind"], 0) + 1
        did = row["did"]
        if did:
            dids.add(did)
        if row["flag"] == "true":
            flagged_rows += 1
            if did:
                flagged_dids.add(did)
    print("--- sample summary ---")
    print("selection: %d messages drawn uniformly with random.Random(42)" % n)
    print("copies: full-day exact-text count from lobby-day.jsonl")
    print("final_kind: template when copies >= 50, else jev_kind")
    print("flag: jev_kind == nonsense OR coherent < 0.4; the template override does not clear a low coherent score")
    if model:
        print("model: %s" % model)
    print("messages: %d" % n)
    print("unique_texts: %d" % unique_n)
    print("already_cached: %d" % cached_n)
    print("newly_called: %d" % new_n)
    print("identities: %d" % len(dids))
    for line in pct_line("final_kind", final_counts, n):
        print(line)
    for line in pct_line("jev_kind", jev_counts, n):
        print(line)
    print("flagged: %d (%.2f%%)" % (flagged_rows, (100.0 * flagged_rows / n) if n else 0.0))
    print("flagged_identities: %d" % len(flagged_dids))
    print("api_calls: %d" % calls)
    print("input_tokens: %d" % tokens)
    print("cost_usd: %.6f" % (tokens / 1_000_000 * COST_PER_MILLION))


def did_prefix(did):
    marker = "did:key:"
    if did.startswith(marker):
        return did[len(marker):len(marker) + 12]
    return (did or "")[:12]


def clip_text(text):
    return (text or "").replace("\n", " ").replace("\r", " ").replace("|", "/").replace("\t", " ")[:120]


def show_table(title, pool, k, seed):
    print("--- %s ---" % title)
    if not pool:
        print("(none)")
        return
    if len(pool) < k:
        picked = list(pool)
        print("only %d rows; showing all" % len(pool))
    else:
        picked = random.Random(seed).sample(pool, k)
    print("| seq | did prefix | text | copies | jev_kind | final_kind | conf | coherent | substance | flag |")
    print("|---|---|---|---|---|---|---|---|---|---|")
    for row in picked:
        print("| %s | %s | %s | %s | %s | %s | %s | %s | %s | %s |" % (
            row["seq"], did_prefix(row["did"]), clip_text(row["text"]), row["copies"],
            row["jev_kind"], row["final_kind"], row["conf"], row["coherent"],
            row["substance"], row["flag"],
        ))


def refuse_results_rewrite(path):
    if os.path.abspath(path) == os.path.abspath(RESULTS_CSV):
        sys.exit("refusing to rewrite jev_results.csv")


def prepare_cache(cache_path, results_csv):
    cache = Cache(cache_path)
    cache.load()
    added = seed_from_results(cache, results_csv)
    print(f"cache: {len(cache.data)} texts ({added} seeded from jev_results.csv)", flush=True)
    return cache


def cmd_sample(args, key):
    refuse_results_rewrite(args.output)
    cache = prepare_cache(args.cache, args.results)
    print("counting messages", flush=True)
    total = count_messages(args.input)
    print(f"day_messages: {total}", flush=True)
    if args.sample > total:
        cache.close()
        sys.exit(f"sample {args.sample} exceeds {total} messages")
    print(f"drawing {args.sample} messages with random.Random({args.seed})", flush=True)
    picked = load_sample(args.input, args.sample, args.seed, total)
    texts = unique_in_order(picked)
    todo = [t for t in texts if t not in cache.data]
    cached_n = len(texts) - len(todo)
    print(f"unique_texts: {len(texts)}; already_cached: {cached_n}; to_call: {len(todo)}", flush=True)
    if cached_n == 0 and os.path.exists(args.results):
        cache.close()
        sys.exit("no sampled text matched the existing scores; refusing to call")
    print("counting full-day copies", flush=True)
    copies = full_day_copies(args.input, texts)
    missing = [t for t in texts if copies.get(t, 0) < 1]
    if missing:
        cache.close()
        sys.exit(f"full-day copy count missed {len(missing)} sampled texts")
    calls = tokens = 0
    model = ""
    if todo:
        if not key:
            cache.close()
            print("key missing")
            return 2
        calls, tokens, model, errors, done = score_many(todo, key, cache)
        if errors or done != len(todo):
            cache.close()
            print(f"scoring incomplete: {done}/{len(todo)} ok, {len(errors)} failed", flush=True)
            return 1
    else:
        model = next((rec.get("model") or "" for rec in cache.data.values() if rec.get("model")), "")
    annotated = [annotate(row, cache.data[row["text"]], copies[row["text"]]) for row in picked]
    write_csv(args.output, annotated)
    cache.close()
    print_summary(annotated, len(texts), cached_n, len(todo), calls, tokens, model)
    print("csv: %s" % args.output)
    differ = [r for r in annotated if r["final_kind"] != r["jev_kind"]]
    originals = [r for r in annotated if r["jev_kind"] == "original" and int(r["copies"]) < 50]
    show_table("final_kind differs from jev_kind", differ, 10, 42)
    show_table("jev_kind original and copies < 50", originals, 10, 42)
    print("stopped before the full day")
    return 0


def cmd_full(args, key):
    """Score every unique text not already cached, then write one row per message.

    Not invoked unless --full is passed. Refuses to overwrite jev_results.csv.
    """
    refuse_results_rewrite(args.output)
    if not key:
        print("key missing")
        return 2
    cache = prepare_cache(args.cache, args.results)
    print("FULL DAY: counting copies and unique texts", flush=True)
    copies = {}
    texts = []
    rows_n = 0
    with open(args.input, encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            row = json.loads(line)
            text = row.get("text") or ""
            if not text.strip():
                continue
            rows_n += 1
            if text not in copies:
                copies[text] = 0
                texts.append(text)
            copies[text] += 1
    todo = [t for t in texts if t not in cache.data]
    print(f"messages: {rows_n}; unique: {len(texts)}; to_call: {len(todo)}", flush=True)
    calls = tokens = 0
    model = ""
    if todo:
        calls, tokens, model, errors, done = score_many(todo, key, cache)
        if errors or done != len(todo):
            cache.close()
            print(f"scoring incomplete: {done}/{len(todo)} ok, {len(errors)} failed", flush=True)
            return 1
    print("writing day csv", flush=True)
    tmp = args.output + ".partial"
    final_counts = {}
    flagged_rows = 0
    dids = set()
    flagged_dids = set()
    with open(tmp, "w", encoding="utf-8", newline="") as out, open(args.input, encoding="utf-8") as src:
        w = csv.DictWriter(out, fieldnames=FIELDS, extrasaction="ignore")
        w.writeheader()
        for line in src:
            if not line.strip():
                continue
            row = json.loads(line)
            text = row.get("text") or ""
            if text not in copies:
                continue
            annotated = annotate(row, cache.data[text], copies[text])
            w.writerow(annotated)
            final_counts[annotated["final_kind"]] = final_counts.get(annotated["final_kind"], 0) + 1
            did = annotated["did"]
            if did:
                dids.add(did)
            if annotated["flag"] == "true":
                flagged_rows += 1
                if did:
                    flagged_dids.add(did)
    os.replace(tmp, args.output)
    cache.close()
    print("--- full day summary ---")
    if model:
        print("model: %s" % model)
    print("messages: %d" % rows_n)
    print("unique_texts: %d" % len(texts))
    print("api_calls: %d" % calls)
    print("input_tokens: %d" % tokens)
    print("cost_usd: %.6f" % (tokens / 1_000_000 * COST_PER_MILLION))
    print("identities: %d" % len(dids))
    for line in pct_line("final_kind", final_counts, rows_n):
        print(line)
    print("flagged: %d (%.2f%%)" % (flagged_rows, (100.0 * flagged_rows / rows_n) if rows_n else 0.0))
    print("flagged_identities: %d" % len(flagged_dids))
    print("csv: %s" % args.output)
    return 0


def cmd_norm_report(args):
    """Local collapse stats. No API calls."""
    print("normalizing the day", flush=True)
    raw_seen = set()
    copies = {}
    variants = {}
    dot_n = dagger_n = tail_n = tail_alpha = 0
    therefore_n = 0
    therefore_re = re.compile(r"\s*\u2234\s*\d+\s*$")
    examples = {}
    tail_examples = []
    i = 0
    with open(args.input, encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            text = json.loads(line).get("text") or ""
            i += 1
            norm, dot, dagger, tail = normalize_trace(text)
            if dot:
                dot_n += 1
            if dagger:
                dagger_n += 1
            if tail:
                tail_n += 1
                if tail.isalpha():
                    tail_alpha += 1
                    if len(tail_examples) < 25:
                        tail_examples.append((tail, text[:160], norm[:160]))
            collapsed = unicodedata.normalize("NFKC", text or "")
            collapsed = WS_RUN.sub(" ", collapsed).strip()
            if therefore_re.search(collapsed):
                therefore_n += 1
            h = hashlib.sha256(text.encode("utf-8")).digest()
            if h not in raw_seen:
                raw_seen.add(h)
                variants[norm] = variants.get(norm, 0) + 1
            copies[norm] = copies.get(norm, 0) + 1
            if "v2xm1" in text and "v2xm1" not in examples:
                examples["v2xm1"] = (text, norm)
            if "ar6ns" in text and "ar6ns" not in examples:
                examples["ar6ns"] = (text, norm)
            if "\u2020" in text and "dagger" not in examples:
                examples["dagger"] = (text, norm)
            if dot and "v2xm1" not in text and "ar6ns" not in text and "dot_other" not in examples:
                examples["dot_other"] = (text, norm)
            if tail and "tail" not in examples:
                examples["tail"] = (text, norm)
            if "\u2234" in text and "therefore" not in examples:
                examples["therefore"] = (text, norm)
            if text != text.lower() and not dot and not dagger and not tail and "case" not in examples:
                examples["case"] = (text, norm)
            if i % 500000 == 0:
                print(f"normalized {i}", flush=True)
    raw_unique = len(raw_seen)
    norm_unique = len(copies)
    collapsed_raw = sum(v for v in variants.values() if v >= 2)
    reduction = raw_unique - norm_unique
    top = sorted(copies.items(), key=lambda kv: (-kv[1], kv[0]))[:20]
    with open(TOP20_CSV, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["copies", "variants", "norm"])
        for norm, n in top:
            w.writerow([n, variants.get(norm, 0), norm])
    print("--- normalization ---")
    print("messages: %d" % i)
    print("raw_unique: %d" % raw_unique)
    print("norm_unique: %d" % norm_unique)
    print("raw_texts_that_collapsed: %d" % collapsed_raw)
    print("reduction: %d" % reduction)
    print("salt_dot_messages: %d" % dot_n)
    print("salt_dagger_messages: %d" % dagger_n)
    print("salt_tail_messages: %d" % tail_n)
    print("salt_tail_alpha_messages: %d" % tail_alpha)
    print("therefore_salt_messages: %d" % therefore_n)
    print("--- examples ---")
    for name in ("v2xm1", "ar6ns", "dagger", "dot_other", "tail", "therefore", "case"):
        if name not in examples:
            print("%s: (not seen)" % name)
            continue
        raw, norm = examples[name]
        print("EX %s" % name)
        print("  before: %s" % raw[:200].replace("\n", " "))
        print("  after:  %s" % norm[:200])
    print("--- rule-c alpha tails (up to 25) ---")
    for tail, raw, norm in tail_examples:
        print("TAIL %s | %s => %s" % (tail, raw.replace("\n", " "), norm))
    print("--- top 20 ---")
    for norm, n in top:
        shown = norm[:140]
        print("%8d  var %6d  %s" % (n, variants.get(norm, 0), shown))
    print("top20: %s" % TOP20_CSV)
    return 0


def score_many_v2(pairs, key, cache):
    """pairs are (norm, raw_example). State sent to Jev is the normalized text."""
    calls = 0
    tokens = 0
    model = ""
    errors = []
    done = 0
    lock = threading.Lock()

    def work(item):
        norm, raw = item
        parsed, ncalls = score_text(norm, key, QUESTIONS_V2)
        return norm, raw, parsed, ncalls

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futures = [pool.submit(work, item) for item in pairs]
        for fut in as_completed(futures):
            try:
                norm, raw, parsed, ncalls = fut.result()
            except Exception as e:
                errors.append(redact(str(e)))
                continue
            conf = parsed["conf"]
            cache.add({
                "norm": norm,
                "raw_example": raw,
                "jev_kind": parsed["kind"],
                "conf": conf,
                "coherent": parsed["coherent"],
                "specific": parsed["specific"],
                "substance": parsed["substance"],
                "review": None if conf is None else conf < 0.6,
                "input_tokens": parsed["input_tokens"],
                "model": parsed["model"],
            })
            with lock:
                calls += ncalls
                tokens += parsed["input_tokens"]
                if parsed["model"]:
                    model = parsed["model"]
                done += 1
                if done % 50 == 0 or done == len(pairs):
                    print(f"scored {done}/{len(pairs)}", flush=True)
    return calls, tokens, model, errors, done


def annotate_v2(row, rec, copies):
    jev_kind = rec["jev_kind"]
    coherent = float(rec["coherent"])
    return {
        "seq": row.get("seq"),
        "ts": row.get("ts") or "",
        "did": row.get("did") or "",
        "text": row.get("text") or "",
        "norm": rec["norm"],
        "copies": int(copies),
        "jev_kind": jev_kind,
        "final_kind": final_kind_of(jev_kind, copies),
        "conf": fmt_num(rec["conf"]),
        "coherent": fmt_num(coherent),
        "specific": fmt_num(rec["specific"]),
        "substance": rec["substance"],
        "flag": "true" if is_flagged(jev_kind, coherent) else "false",
    }


def full_day_norm_copies(path, norms):
    need = set(norms)
    counts = {n: 0 for n in need}
    i = 0
    with open(path, encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            i += 1
            text = json.loads(line).get("text") or ""
            norm = normalize(text)
            if norm in need:
                counts[norm] += 1
            if i % 500000 == 0:
                print(f"counted {i}", flush=True)
    return counts


def cmd_rescore_v2(args, key):
    """Rescore the seed-42 sample under the v2 questions. Does not touch the old files."""
    refuse_results_rewrite(args.output)
    if os.path.abspath(args.output) == os.path.abspath(BEFORE_CSV):
        sys.exit("refusing to rewrite sample2000.csv")
    if os.path.abspath(args.cache) == os.path.abspath(CACHE_PATH):
        sys.exit("refusing to write v2 scores into jev_cache.jsonl")
    print("counting messages", flush=True)
    total = count_messages(args.input)
    print(f"day_messages: {total}", flush=True)
    if args.sample > total:
        sys.exit(f"sample {args.sample} exceeds {total} messages")
    picked = load_sample(args.input, args.sample, args.seed, total)
    before = {}
    before_order = []
    with open(BEFORE_CSV, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            before[row["seq"]] = row["final_kind"]
            before_order.append(row["seq"])
    got = [str(row.get("seq")) for row in picked]
    if set(got) != set(before_order) or len(got) != len(before_order):
        sys.exit("sample indices do not match sample2000.csv; refusing to score")
    norms = []
    seen = set()
    raw_for = {}
    for row in picked:
        norm = normalize(row.get("text") or "")
        if norm not in seen:
            seen.add(norm)
            norms.append(norm)
            raw_for[norm] = row.get("text") or ""
    cache = Cache(args.cache, key_field="norm")
    cache.load()
    todo = [(n, raw_for[n]) for n in norms if n not in cache.data]
    print(f"unique_norms: {len(norms)}; already_in_v2_cache: {len(norms) - len(todo)}; to_call: {len(todo)}", flush=True)
    if len(todo) > args.sample:
        cache.close()
        sys.exit("refusing to call more texts than the sample size")
    print("counting normalized full-day copies", flush=True)
    copies = full_day_norm_copies(args.input, norms)
    missing = [n for n in norms if copies.get(n, 0) < 1]
    if missing:
        cache.close()
        sys.exit(f"normalized copy count missed {len(missing)} keys")
    calls = tokens = 0
    model = ""
    if todo:
        if not key:
            cache.close()
            print("key missing")
            return 2
        calls, tokens, model, errors, done = score_many_v2(todo, key, cache)
        if errors or done != len(todo):
            cache.close()
            print(f"scoring incomplete: {done}/{len(todo)} ok, {len(errors)} failed", flush=True)
            return 1
    else:
        model = next((rec.get("model") or "" for rec in cache.data.values() if rec.get("model")), "")
    annotated = []
    for row in picked:
        norm = normalize(row.get("text") or "")
        annotated.append(annotate_v2(row, cache.data[norm], copies[norm]))
    tmp = args.output + ".partial"
    with open(tmp, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS_V2, extrasaction="ignore")
        w.writeheader()
        for row in annotated:
            w.writerow(row)
    os.replace(tmp, args.output)
    cache.close()

    before_counts = {}
    for seq in before_order:
        k = before[seq]
        before_counts[k] = before_counts.get(k, 0) + 1
    after_final = {}
    after_jev = {}
    flagged_rows = 0
    dids = set()
    flagged_dids = set()
    specific_sum = 0.0
    moved = []
    for row in annotated:
        after_final[row["final_kind"]] = after_final.get(row["final_kind"], 0) + 1
        after_jev[row["jev_kind"]] = after_jev.get(row["jev_kind"], 0) + 1
        specific_sum += float(row["specific"])
        did = row["did"]
        if did:
            dids.add(did)
        if row["flag"] == "true":
            flagged_rows += 1
            if did:
                flagged_dids.add(did)
        old = before.get(str(row["seq"]))
        if old != row["final_kind"]:
            moved.append((old, row))
    n = len(annotated)
    print("--- before vs after final_kind ---")
    print("messages: %d" % n)
    print("flag: jev_kind == nonsense OR coherent < 0.4; filler does not set flag")
    print("final_kind: template when normalized copies >= 50, else jev_kind")
    print("state scored: normalized text, one call per normalized key in the sample")
    if model:
        print("model: %s" % model)
    for name in KINDS_V2:
        b = before_counts.get(name, 0)
        a = after_final.get(name, 0)
        print("final %-10s before %5d (%5.2f%%)  after %5d (%5.2f%%)" % (
            name, b, 100.0 * b / n, a, 100.0 * a / n))
    for line in pct_line("jev_kind", after_jev, n, KINDS_V2):
        print(line)
    print("flagged: %d (%.2f%%)" % (flagged_rows, 100.0 * flagged_rows / n))
    print("mean_specific: %.4f" % (specific_sum / n))
    print("identities: %d" % len(dids))
    print("flagged_identities: %d" % len(flagged_dids))
    print("unique_norms: %d" % len(norms))
    print("api_calls: %d" % calls)
    print("input_tokens: %d" % tokens)
    print("cost_usd: %.6f" % (tokens / 1_000_000 * COST_PER_MILLION))
    print("moved: %d" % len(moved))
    print("csv: %s" % args.output)
    pool = moved
    print("--- moved final_kind ---")
    if not pool:
        print("(none)")
    else:
        if len(pool) < 10:
            picked_m = list(pool)
            print("only %d rows; showing all" % len(pool))
        else:
            picked_m = random.Random(42).sample(pool, 10)
        print("| seq | did prefix | text | copies | before | jev_kind | final_kind | coherent | specific |")
        print("|---|---|---|---|---|---|---|---|---|")
        for old, row in picked_m:
            print("| %s | %s | %s | %s | %s | %s | %s | %s | %s |" % (
                row["seq"], did_prefix(row["did"]), clip_text(row["text"]), row["copies"],
                old, row["jev_kind"], row["final_kind"], row["coherent"], row["specific"],
            ))
    print("stopped before the full day")
    return 0


DAY_CSV = os.path.join(HERE, "jev_day_20261006.csv")
IDENT_CSV = os.path.join(HERE, "identities_20261006.csv")
SUMMARY_PATH = os.path.join(HERE, "summary_20261006.txt")
PROGRESS_LOG = os.path.join(HERE, "progress_20261006.log")
COST_LIMIT = 40.0
PROGRESS_EVERY = 10000


def is_reusable_score(rec):
    kind = rec.get("jev_kind")
    if kind is None or kind == "":
        return False
    spec = rec.get("specific")
    return isinstance(spec, (int, float)) and not isinstance(spec, bool)


def cache_norm_key(rec):
    raw = rec.get("norm")
    if raw is None:
        raw = rec.get("text") or ""
    return normalize(raw)


def flag_cell(jev_kind, coherent):
    """Empty coherent is not a flag. Filler does not set the flag."""
    if jev_kind == "nonsense":
        return "true"
    if coherent is None or coherent == "":
        return "false"
    try:
        return "true" if float(coherent) < 0.4 else "false"
    except (TypeError, ValueError):
        return "false"


class DayProgress:
    def __init__(self, total, fh, t0):
        self.total = total
        self.done = 0
        self.calls = 0
        self.tokens = 0
        self.fh = fh
        self.t0 = t0
        self.next_mark = PROGRESS_EVERY
        self.last_line = ""

    def emit(self, tag):
        elapsed = max(time.monotonic() - self.t0, 1e-6)
        rate = self.done / elapsed if self.done else 0.0
        remaining = max(self.total - self.done, 0)
        eta = (remaining / rate) if rate else 0.0
        cost = self.tokens / 1_000_000 * COST_PER_MILLION
        line = (
            "%s texts_done=%d texts_remaining=%d api_calls=%d input_tokens=%d cost_usd=%.6f eta_s=%.0f"
            % (tag, self.done, remaining, self.calls, self.tokens, cost, eta)
        )
        self.last_line = line
        print(line, flush=True)
        self.fh.write(line + "\n")
        self.fh.flush()

    def tick(self):
        self.done += 1
        if self.done >= self.next_mark:
            self.emit("progress")
            self.next_mark += PROGRESS_EVERY


def last_prefixed_line(path, prefix):
    last = None
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        for line in f:
            if line.startswith(prefix):
                last = line.strip()
    return last


def progress_fields(line):
    out = {}
    for part in line.split():
        if "=" not in part:
            continue
        k, v = part.split("=", 1)
        if k == "cost_usd":
            out[k] = float(v)
        else:
            try:
                out[k] = int(v)
            except ValueError:
                out[k] = v
    return out


def cmd_run_day(args, key):
    """Full 2026-10-06 day. Skips copies>=50, reuses new-question cache lines, stops above $40."""
    finalize = bool(getattr(args, "finalize", False))
    if not key and not finalize:
        print("key missing")
        return 2
    log_fh = open(PROGRESS_LOG, "a", encoding="utf-8")
    cache_fh = open(CACHE_PATH, "a", encoding="utf-8")

    def log(line):
        print(line, flush=True)
        log_fh.write(line + "\n")
        log_fh.flush()

    def append_cache(rec):
        cache_fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        cache_fh.flush()

    print("loading resume cache", flush=True)
    reusable = {}
    ignored_old = 0
    if os.path.exists(CACHE_PATH):
        with open(CACHE_PATH, encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    ignored_old += 1
                    continue
                if not is_reusable_score(rec):
                    ignored_old += 1
                    continue
                reusable[cache_norm_key(rec)] = rec
    appended_v2 = 0
    v2_collapsed = 0
    if os.path.exists(CACHE_V2_PATH):
        with open(CACHE_V2_PATH, encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                rec = json.loads(line)
                if not is_reusable_score(rec):
                    continue
                new_key = cache_norm_key(rec)
                if new_key in reusable:
                    v2_collapsed += 1
                    continue
                stored = {
                    "norm": new_key,
                    "jev_kind": rec["jev_kind"],
                    "conf": rec.get("conf"),
                    "coherent": rec.get("coherent"),
                    "specific": rec.get("specific"),
                    "substance": rec.get("substance"),
                    "input_tokens": rec.get("input_tokens"),
                    "model": rec.get("model") or "",
                }
                append_cache(stored)
                reusable[new_key] = stored
                appended_v2 += 1
    print(f"cache reusable={len(reusable)} ignored_old_lines={ignored_old} appended_v2={appended_v2}", flush=True)

    print("recounting copies with therefore-salt", flush=True)
    copies = {}
    messages_n = 0
    with open(args.input, encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            text = json.loads(line).get("text") or ""
            norm = normalize(text)
            copies[norm] = copies.get(norm, 0) + 1
            messages_n += 1
            if messages_n % 500000 == 0:
                print(f"recounted {messages_n}", flush=True)
    unique_n = len(copies)
    skipped = 0
    reused_below = 0
    reused_on_skip = 0
    todo = []
    v2_keys = set(reusable)
    for norm, c in copies.items():
        if c >= 50:
            skipped += 1
            if norm in v2_keys:
                reused_on_skip += 1
        elif norm in v2_keys:
            reused_below += 1
        else:
            todo.append(norm)
    reused_v2 = reused_below + reused_on_skip
    calls = 0
    tokens = 0
    failures = 0
    cost_stop = False
    fatal = False
    billing_stop = False
    model = ""
    scored_progress_line = None
    t0 = time.monotonic()
    progress = DayProgress(unique_n, log_fh, t0)

    if finalize:
        start_line = last_prefixed_line(PROGRESS_LOG, "start ")
        prog_line = last_prefixed_line(PROGRESS_LOG, "progress ")
        if not start_line or not prog_line:
            print("finalize: missing start or progress line in the log")
            return 1
        start_stats = progress_fields(start_line)
        prog_stats = progress_fields(prog_line)
        if unique_n != start_stats["unique"] or skipped != start_stats["skipped_copies_ge_50"]:
            print(
                "finalize: recount mismatch unique=%d skipped=%d log_unique=%s log_skipped=%s"
                % (unique_n, skipped, start_stats.get("unique"), start_stats.get("skipped_copies_ge_50"))
            )
            return 1
        reused_v2 = start_stats["reused_v2"]
        reused_below = start_stats["reused_below_50"]
        reused_on_skip = start_stats["cache_filled_skips"]
        ignored_old = start_stats["ignored_old_cache_lines"]
        appended_v2 = start_stats["appended_v2"]
        v2_norms = set()
        if os.path.exists(CACHE_V2_PATH):
            with open(CACHE_V2_PATH, encoding="utf-8") as f:
                for line in f:
                    if not line.strip():
                        continue
                    rec = json.loads(line)
                    if is_reusable_score(rec):
                        v2_norms.add(cache_norm_key(rec))
        billed_rows = 0
        billed_tokens = 0
        missing_tokens = 0
        for norm, rec in reusable.items():
            if norm in v2_norms:
                continue
            billed_rows += 1
            raw_tokens = rec.get("input_tokens")
            if raw_tokens is None:
                missing_tokens += 1
            else:
                billed_tokens += int(raw_tokens)
        logged_successes = prog_stats["texts_done"] - (start_stats["skipped_copies_ge_50"] + start_stats["reused_below_50"])
        retry_extra = prog_stats["api_calls"] - logged_successes
        if billed_rows < logged_successes or missing_tokens:
            print(
                "finalize: cache billing rows=%d missing_tokens=%d logged_successes=%d; keeping log totals"
                % (billed_rows, missing_tokens, logged_successes)
            )
            calls = prog_stats["api_calls"]
            tokens = prog_stats["input_tokens"]
        else:
            calls = billed_rows + max(retry_extra, 0)
            tokens = billed_tokens
            print(
                "finalize: billed_rows=%d billed_tokens=%d logged_calls=%d logged_tokens=%d retry_extra=%d"
                % (billed_rows, billed_tokens, prog_stats["api_calls"], prog_stats["input_tokens"], retry_extra)
            )
        billing_stop = True
        for rec in reusable.values():
            if rec.get("model"):
                model = rec["model"]
                break
        progress.done = prog_stats["texts_done"]
        progress.calls = calls
        progress.tokens = tokens
        scored_progress_line = prog_line
        progress.last_line = prog_line
        print(start_line, flush=True)
        print("finalize: HTTP 402 billing stop, writing partial outputs, no API calls", flush=True)
        stop_line = "stop " + prog_line.split(" ", 1)[1] + " reason=HTTP_402_no_credits"
        log(stop_line)
    else:
        start = (
            "start unique=%d to_call=%d skipped_copies_ge_50=%d reused_v2=%d "
            "reused_below_50=%d cache_filled_skips=%d ignored_old_cache_lines=%d appended_v2=%d"
            % (unique_n, len(todo), skipped, reused_v2, reused_below, reused_on_skip, ignored_old, appended_v2)
        )
        log(start)
        for norm, c in copies.items():
            if c >= 50 or norm in v2_keys:
                progress.tick()

    todo_iter = iter(todo)
    pending = {}

    def submit_one(pool):
        nonlocal cost_stop
        if cost_stop or fatal or billing_stop:
            return False
        cost_now = tokens / 1_000_000 * COST_PER_MILLION
        if cost_now > COST_LIMIT:
            cost_stop = True
            return False
        try:
            norm = next(todo_iter)
        except StopIteration:
            return False
        pending[pool.submit(score_text, norm, key, QUESTIONS_V2)] = norm
        return True

    if todo and not finalize:
        with ThreadPoolExecutor(max_workers=WORKERS) as pool:
            for _ in range(WORKERS):
                if not submit_one(pool):
                    break
            while pending:
                done_set, _ = wait(set(pending), return_when=FIRST_COMPLETED)
                for fut in done_set:
                    norm = pending.pop(fut)
                    try:
                        parsed, ncalls = fut.result()
                    except Exception as e:
                        failures += 1
                        msg = redact(str(e))
                        if "HTTP 402" in msg:
                            billing_stop = True
                        elif "HTTP 401" in msg or "HTTP 403" in msg:
                            fatal = True
                        continue
                    stored = {
                        "norm": norm,
                        "jev_kind": parsed["kind"],
                        "conf": parsed["conf"],
                        "coherent": parsed["coherent"],
                        "specific": parsed["specific"],
                        "substance": parsed["substance"],
                        "input_tokens": parsed["input_tokens"],
                        "model": parsed["model"],
                    }
                    append_cache(stored)
                    reusable[norm] = stored
                    calls += ncalls
                    tokens += parsed["input_tokens"]
                    if parsed["model"]:
                        model = parsed["model"]
                    progress.calls = calls
                    progress.tokens = tokens
                    progress.tick()
                    if tokens / 1_000_000 * COST_PER_MILLION > COST_LIMIT:
                        cost_stop = True
                if not cost_stop and not fatal and not billing_stop:
                    while len(pending) < WORKERS:
                        if not submit_one(pool):
                            break
    if not finalize:
        progress.calls = calls
        progress.tokens = tokens
        end_tag = "stop" if (cost_stop or fatal or billing_stop) else "end"
        progress.emit(end_tag)

    print("writing day csv", flush=True)
    kind_counts = {}
    ident = {}
    written = 0
    tmp_day = DAY_CSV + ".partial"
    with open(tmp_day, "w", encoding="utf-8", newline="") as out, open(args.input, encoding="utf-8") as src:
        w = csv.DictWriter(out, fieldnames=FIELDS_V2, extrasaction="ignore")
        w.writeheader()
        for line in src:
            if not line.strip():
                continue
            msg = json.loads(line)
            text = msg.get("text") or ""
            norm = normalize(text)
            c = copies.get(norm, 0)
            rec = reusable.get(norm)
            below_unscored = c < 50 and rec is None
            if below_unscored:
                jev_kind = conf = coherent = specific = substance = ""
                final = ""
            else:
                if rec:
                    jev_kind = rec.get("jev_kind") or ""
                    conf = rec.get("conf")
                    coherent = rec.get("coherent")
                    specific = rec.get("specific")
                    substance = rec.get("substance") or ""
                else:
                    jev_kind = conf = coherent = specific = substance = ""
                final = "template" if c >= 50 else jev_kind
            flag = flag_cell(jev_kind, coherent)
            bucket = final if final else "unscored"
            kind_counts[bucket] = kind_counts.get(bucket, 0) + 1
            did = msg.get("did") or ""
            if did:
                st = ident.get(did)
                if st is None:
                    st = [0, 0, 0, 0]
                    ident[did] = st
                st[0] += 1
                if final == "original":
                    st[1] += 1
                elif final == "template":
                    st[2] += 1
                elif final == "filler":
                    st[3] += 1
            w.writerow({
                "seq": msg.get("seq"),
                "ts": msg.get("ts") or "",
                "did": did,
                "text": text,
                "norm": norm,
                "copies": c,
                "jev_kind": jev_kind,
                "final_kind": final,
                "conf": fmt_num(conf) if conf not in ("", None) else "",
                "coherent": fmt_num(coherent) if coherent not in ("", None) else "",
                "specific": fmt_num(specific) if specific not in ("", None) else "",
                "substance": substance if substance is not None else "",
                "flag": flag,
            })
            written += 1
            if written % 500000 == 0:
                print(f"wrote {written}", flush=True)
    os.replace(tmp_day, DAY_CSV)

    ever = two_plus = 0
    with open(IDENT_CSV, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["did", "messages", "pct_original", "pct_template", "pct_filler", "ever_original", "n_original"])
        for did, (msgs, n_orig, n_temp, n_fill) in ident.items():
            if n_orig >= 1:
                ever += 1
            if n_orig >= 2:
                two_plus += 1
            w.writerow([
                did, msgs,
                "%.2f" % (100.0 * n_orig / msgs),
                "%.2f" % (100.0 * n_temp / msgs),
                "%.2f" % (100.0 * n_fill / msgs),
                "yes" if n_orig >= 1 else "no",
                n_orig,
            ])
    n_ident = len(ident)
    cost = tokens / 1_000_000 * COST_PER_MILLION
    status = "PARTIAL" if (cost_stop or fatal or failures or billing_stop) else "complete"
    if cost_stop or billing_stop:
        status = "PARTIAL"
    lines = []
    lines.append("day: 2026-10-06")
    lines.append("status: %s" % status)
    lines.append("cost_stop: %s" % ("yes" if cost_stop else "no"))
    lines.append("billing_stop: %s" % ("yes" if billing_stop else "no"))
    lines.append("fatal_auth: %s" % ("yes" if fatal else "no"))
    if billing_stop:
        lines.append("day_csv_partial: yes")
        lines.append("stop_reason: HTTP 402 billing_error, no TypeSafe API credits; unscored keys left blank")
    lines.append("messages: %d" % written)
    lines.append("norm_unique: %d" % unique_n)
    lines.append("skipped_copies_ge_50: %d" % skipped)
    lines.append("reused_v2: %d" % reused_v2)
    lines.append("reused_below_50: %d" % reused_below)
    lines.append("cache_filled_skips: %d" % reused_on_skip)
    lines.append("ignored_old_cache_lines: %d" % ignored_old)
    lines.append("appended_v2: %d" % appended_v2)
    lines.append("api_calls: %d" % calls)
    lines.append("input_tokens: %d" % tokens)
    lines.append("cost_usd: %.6f" % cost)
    lines.append("failures: %d" % failures)
    if model:
        lines.append("model: %s" % model)
    for name in ("original", "template", "spam", "nonsense", "filler", "unscored"):
        c = kind_counts.get(name, 0)
        pct = (100.0 * c / written) if written else 0.0
        lines.append("final_kind_%s: %d (%.2f%%)" % (name, c, pct))
    lines.append("identities: %d" % n_ident)
    lines.append("ever_original: %d (%.2f%%)" % (ever, (100.0 * ever / n_ident) if n_ident else 0.0))
    lines.append("original_2plus: %d (%.2f%%)" % (two_plus, (100.0 * two_plus / n_ident) if n_ident else 0.0))
    lines.append("day_csv: %s" % DAY_CSV)
    lines.append("identities_csv: %s" % IDENT_CSV)
    lines.append("last_progress: %s" % (scored_progress_line or progress.last_line))
    text_out = "\n".join(lines) + "\n"
    with open(SUMMARY_PATH, "w", encoding="utf-8") as f:
        f.write(text_out)
    print(text_out, flush=True)
    cache_fh.close()
    log_fh.close()
    if cost_stop:
        print("PARTIAL: stopped because cost passed $40. Not resuming.", flush=True)
        return 0
    if billing_stop:
        print("PARTIAL: stopped because the API returned HTTP 402 (no credits). Cost did not pass $40. Not resuming.", flush=True)
        print("day csv is partial: jev_kind and final_kind are empty when that normalized key was neither skipped nor scored.", flush=True)
        return 0
    if fatal:
        print("PARTIAL: stopped on authentication error.", flush=True)
        return 1
    return 0 if failures == 0 else 1


def main():
    ap = argparse.ArgumentParser(description="Score lobby texts with TypeSafe Jev")
    ap.add_argument("--input", default=DAY_PATH)
    ap.add_argument("--cache", default=CACHE_PATH)
    ap.add_argument("--results", default=RESULTS_CSV, help="existing unique scores to seed the cache; never rewritten")
    ap.add_argument("--output", default=None)
    ap.add_argument("--sample", type=int, default=None, help="uniform sample of N messages, then stop")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--full", action="store_true", help="score the rest of the day")
    ap.add_argument("--norm-report", action="store_true", help="count normalized copies; no API")
    ap.add_argument("--rescore-v2", action="store_true", help="rescore the seed sample with v2 questions, then stop")
    ap.add_argument("--run-day", action="store_true", help="score the full 2026-10-06 lobby day")
    ap.add_argument("--finalize", action="store_true", help="write partial day outputs from the cache; no API calls")
    args = ap.parse_args()
    modes = sum(bool(x) for x in (args.full, args.sample is not None, args.norm_report, args.rescore_v2, args.run_day, args.finalize))
    if modes != 1:
        ap.print_help()
        print("\npass exactly one of --run-day, --finalize, --norm-report, --rescore-v2, --sample N, or --full")
        return 2
    if args.norm_report:
        return cmd_norm_report(args)
    if args.finalize:
        return cmd_run_day(args, "")
    key = os.environ.get("TYPESAFE_API_KEY") or ""
    if args.run_day:
        return cmd_run_day(args, key)
    if args.rescore_v2:
        if args.sample is None:
            args.sample = 2000
        if args.output is None:
            args.output = os.path.join(HERE, "sample2000-v2.csv")
        if args.cache == CACHE_PATH:
            args.cache = CACHE_V2_PATH
        return cmd_rescore_v2(args, key)
    if args.sample is not None:
        if args.output is None:
            args.output = os.path.join(HERE, "sample2000.csv" if args.sample == 2000 else f"sample{args.sample}.csv")
        return cmd_sample(args, key)
    if args.output is None:
        args.output = os.path.join(HERE, "jev_day.csv")
    return cmd_full(args, key)


if __name__ == "__main__":
    sys.exit(main())
