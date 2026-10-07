#!/usr/bin/env python3
"""Resume the 2026-10-06 Jev day for keys still unscored in the partial file.

Scores normalized texts with the filler + specific question set. Appends each
new judgment to jev_cache.jsonl. Stops launching calls when this run's input
token cost passes $5, or immediately on HTTP 402. Does not touch
partial-20261006/.
"""
import csv
import json
import os
import subprocess
import sys
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait

import score_jev as sj

HERE = sj.HERE
PARTIAL = os.path.join(HERE, "partial-20261006", "jev_day_20261006.csv")
CACHE_PATH = sj.CACHE_PATH
DAY_CSV = sj.DAY_CSV
IDENT_CSV = sj.IDENT_CSV
SUMMARY_PATH = sj.SUMMARY_PATH
PROGRESS_LOG = sj.PROGRESS_LOG
PID_PATH = os.path.join(HERE, "resume_20261006.pid")
COST_LIMIT = 5.0
PRIOR_COST = 25.631176
WORKERS = 16
PROGRESS_EVERY = 10000
EXPECTED_MESSAGES = 2164450
EXPECTED_UNSCORED = 87036


def other_scorer_running():
    """True when another score_jev.py or resume_20261006.py python is alive."""
    me = os.getpid()
    parent = os.getppid()
    found = []
    for pattern in ("score_jev.py", "resume_20261006.py"):
        try:
            out = subprocess.check_output(["pgrep", "-fl", pattern], text=True)
        except subprocess.CalledProcessError:
            continue
        for line in out.splitlines():
            parts = line.split(None, 1)
            if not parts:
                continue
            try:
                pid = int(parts[0])
            except ValueError:
                continue
            if pid in (me, parent):
                continue
            cmd = parts[1] if len(parts) > 1 else ""
            if "python" not in cmd.lower() and "Python" not in cmd:
                continue
            found.append((pid, pattern))
    return found


def scan_partial():
    csv.field_size_limit(10_000_000)
    total = 0
    unscored_messages = 0
    need_copies = {}
    odd_jev_only = 0
    odd_final_only = 0
    with open(PARTIAL, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames != sj.FIELDS_V2:
            sys.exit("partial day header mismatch: %s" % (reader.fieldnames,))
        for row in reader:
            total += 1
            jev_kind = row["jev_kind"]
            final_kind = row["final_kind"]
            if jev_kind == "" and final_kind == "":
                unscored_messages += 1
                norm = row["norm"]
                copies = int(row["copies"])
                prev = need_copies.get(norm)
                if prev is None:
                    need_copies[norm] = copies
                elif prev != copies:
                    sys.exit("norm copies disagree in the partial day file")
            elif jev_kind == "" or final_kind == "":
                if jev_kind == "":
                    odd_final_only += 1
                else:
                    odd_jev_only += 1
            if total % 500000 == 0:
                print("scanned %d" % total, flush=True)
    return total, unscored_messages, need_copies, odd_jev_only, odd_final_only


def load_cached(need):
    """Reusable cache rows whose normalized key is in need. Last line wins."""
    hits = {}
    ignored = 0
    reusable_n = 0
    if not os.path.exists(CACHE_PATH):
        return hits, ignored, reusable_n
    with open(CACHE_PATH, encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                ignored += 1
                continue
            if not sj.is_reusable_score(rec):
                ignored += 1
                continue
            reusable_n += 1
            key = sj.cache_norm_key(rec)
            if key in need:
                hits[key] = rec
    return hits, ignored, reusable_n


def apply_rec(row, rec):
    kind = rec["jev_kind"]
    coherent = rec.get("coherent")
    conf = rec.get("conf")
    specific = rec.get("specific")
    copies = int(row["copies"])
    row["jev_kind"] = kind
    row["conf"] = sj.fmt_num(conf) if conf not in ("", None) else ""
    row["coherent"] = sj.fmt_num(coherent) if coherent not in ("", None) else ""
    row["specific"] = sj.fmt_num(specific) if specific not in ("", None) else ""
    row["substance"] = rec.get("substance") or ""
    row["flag"] = sj.flag_cell(kind, coherent)
    row["final_kind"] = "template" if copies >= sj.TEMPLATE_AT else kind


class RunLog:
    def __init__(self, fh, total):
        self.fh = fh
        self.total = total
        self.done = 0
        self.calls = 0
        self.tokens = 0
        self.t0 = time.monotonic()

    def line(self, tag):
        elapsed = max(time.monotonic() - self.t0, 1e-6)
        rate = self.done / elapsed if self.done else 0.0
        remaining = max(self.total - self.done, 0)
        eta = (remaining / rate) if rate else 0.0
        cost = self.tokens / 1_000_000 * sj.COST_PER_MILLION
        text = (
            "%s texts_done=%d texts_remaining=%d api_calls=%d input_tokens=%d cost_usd=%.6f eta_s=%.0f"
            % (tag, self.done, remaining, self.calls, self.tokens, cost, eta)
        )
        print(text, flush=True)
        self.fh.write(text + "\n")
        self.fh.flush()
        return text

    def tick(self):
        self.done += 1
        if self.done % PROGRESS_EVERY == 0:
            self.line("resume progress")


def score_todo(todo, key, scored, log_fh):
    calls = 0
    tokens = 0
    failures = 0
    cost_stop = False
    billing_stop = False
    fatal = False
    model = ""
    progress = RunLog(log_fh, len(todo))
    cache_fh = open(CACHE_PATH, "a", encoding="utf-8")
    failed = []

    def append_cache(rec):
        cache_fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        cache_fh.flush()

    def accept(norm, parsed, ncalls):
        nonlocal calls, tokens, model
        spec = parsed.get("specific")
        if not isinstance(spec, (int, float)) or isinstance(spec, bool):
            return False
        stored = {
            "norm": norm,
            "jev_kind": parsed["kind"],
            "conf": parsed["conf"],
            "coherent": parsed["coherent"],
            "specific": spec,
            "substance": parsed["substance"],
            "input_tokens": parsed["input_tokens"],
            "model": parsed["model"],
        }
        append_cache(stored)
        scored[norm] = stored
        calls += ncalls
        tokens += int(parsed["input_tokens"] or 0)
        if parsed.get("model"):
            model = parsed["model"]
        progress.calls = calls
        progress.tokens = tokens
        progress.tick()
        return True

    def run_wave(items):
        nonlocal cost_stop, billing_stop, fatal, failures
        wave_failed = []
        if not items or cost_stop or billing_stop or fatal:
            return wave_failed
        todo_iter = iter(items)
        pending = {}

        def submit_one(pool):
            nonlocal cost_stop
            if cost_stop or billing_stop or fatal:
                return False
            if tokens / 1_000_000 * sj.COST_PER_MILLION > COST_LIMIT:
                cost_stop = True
                return False
            try:
                norm = next(todo_iter)
            except StopIteration:
                return False
            pending[pool.submit(sj.score_text, norm, key, sj.QUESTIONS_V2)] = norm
            return True

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
                        msg = sj.redact(str(e))
                        if "HTTP 402" in msg:
                            billing_stop = True
                        elif "HTTP 401" in msg or "HTTP 403" in msg:
                            fatal = True
                        else:
                            wave_failed.append(norm)
                        continue
                    if not accept(norm, parsed, ncalls):
                        failures += 1
                        wave_failed.append(norm)
                        continue
                    if tokens / 1_000_000 * sj.COST_PER_MILLION > COST_LIMIT:
                        cost_stop = True
                if not cost_stop and not billing_stop and not fatal:
                    while len(pending) < WORKERS:
                        if not submit_one(pool):
                            break
        return wave_failed

    failed = run_wave(todo)
    # Transient failures only. 402 is never retried.
    wave = 0
    while failed and not billing_stop and not fatal and not cost_stop and wave < 2:
        wave += 1
        print("retry wave %d keys=%d" % (wave, len(failed)), flush=True)
        failed = run_wave(failed)

    progress.calls = calls
    progress.tokens = tokens
    if cost_stop or billing_stop or fatal or failed or progress.done != len(todo):
        reason = "cost_passed_5" if cost_stop else ""
        if billing_stop:
            reason = "HTTP_402"
        elif fatal:
            reason = "auth"
        elif failed or progress.done != len(todo):
            reason = reason or "incomplete"
        progress.line("resume stop reason=%s" % reason)
    else:
        progress.line("resume end")
    cache_fh.close()
    return {
        "calls": calls,
        "tokens": tokens,
        "failures": failures,
        "cost_stop": cost_stop,
        "billing_stop": billing_stop,
        "fatal": fatal,
        "model": model,
        "scored": progress.done,
        "left": len(failed),
    }


def write_outputs(scored, stats):
    csv.field_size_limit(10_000_000)
    kind_counts = {}
    ident = {}
    written = 0
    empty_final = 0
    tmp_day = DAY_CSV + ".writing"
    with open(tmp_day, "w", encoding="utf-8", newline="") as out, open(
        PARTIAL, newline="", encoding="utf-8"
    ) as src:
        reader = csv.DictReader(src)
        writer = csv.DictWriter(out, fieldnames=sj.FIELDS_V2, extrasaction="ignore")
        writer.writeheader()
        for row in reader:
            if row["final_kind"] == "" and row["jev_kind"] == "":
                copies = int(row["copies"])
                rec = scored.get(row["norm"])
                if rec is not None:
                    apply_rec(row, rec)
                elif copies >= sj.TEMPLATE_AT:
                    row["final_kind"] = "template"
                    row["flag"] = "false"
            final = row["final_kind"]
            if final == "":
                empty_final += 1
                bucket = "unscored"
            else:
                bucket = final
            kind_counts[bucket] = kind_counts.get(bucket, 0) + 1
            did = row["did"]
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
            writer.writerow(row)
            written += 1
            if written % 500000 == 0:
                print("wrote %d" % written, flush=True)
    os.replace(tmp_day, DAY_CSV)

    ever = 0
    two_plus = 0
    tmp_ident = IDENT_CSV + ".writing"
    with open(tmp_ident, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["did", "messages", "pct_original", "pct_template", "pct_filler", "ever_original"])
        for did, (msgs, n_orig, n_temp, n_fill) in ident.items():
            if n_orig >= 1:
                ever += 1
            if n_orig >= 2:
                two_plus += 1
            w.writerow([
                did,
                msgs,
                "%.2f" % (100.0 * n_orig / msgs),
                "%.2f" % (100.0 * n_temp / msgs),
                "%.2f" % (100.0 * n_fill / msgs),
                "yes" if n_orig >= 1 else "no",
            ])
    os.replace(tmp_ident, IDENT_CSV)

    complete = empty_final == 0
    n_ident = len(ident)
    cost = stats["tokens"] / 1_000_000 * sj.COST_PER_MILLION
    lines = ["day: 2026-10-06"]
    if complete:
        lines.append("status: complete")
    else:
        lines.append("status: PARTIAL")
        if stats["billing_stop"]:
            lines.append("stop_reason: HTTP 402")
        elif stats["cost_stop"]:
            lines.append("stop_reason: cost passed $5")
        elif stats["fatal"]:
            lines.append("stop_reason: authentication error")
        else:
            lines.append("stop_reason: unscored messages remain")
        lines.append("unscored_messages: %d" % empty_final)
    lines.append("messages: %d" % written)
    order = ["original", "template", "spam", "nonsense", "filler"]
    seen = set()
    for name in order + sorted(k for k in kind_counts if k not in order and k != "unscored"):
        if name in seen:
            continue
        seen.add(name)
        c = kind_counts.get(name, 0)
        pct = (100.0 * c / written) if written else 0.0
        lines.append("final_kind_%s: %d (%.2f%%)" % (name, c, pct))
    if not complete:
        c = kind_counts.get("unscored", 0)
        pct = (100.0 * c / written) if written else 0.0
        lines.append("final_kind_unscored: %d (%.2f%%)" % (c, pct))
    lines.append("identities: %d" % n_ident)
    lines.append("ever_original: %d (%.2f%%)" % (ever, (100.0 * ever / n_ident) if n_ident else 0.0))
    lines.append("original_2plus: %d (%.2f%%)" % (two_plus, (100.0 * two_plus / n_ident) if n_ident else 0.0))
    lines.append("resume_api_calls: %d" % stats["calls"])
    lines.append("resume_input_tokens: %d" % stats["tokens"])
    lines.append("resume_cost_usd: %.6f" % cost)
    lines.append("prior_run_cost_usd: %.6f" % PRIOR_COST)
    lines.append("resume_failures: %d" % stats["failures"])
    if stats.get("model"):
        lines.append("model: %s" % stats["model"])
    text_out = "\n".join(lines) + "\n"
    with open(SUMMARY_PATH, "w", encoding="utf-8") as f:
        f.write(text_out)
    print(text_out, flush=True)
    if complete:
        print("RESULT complete", flush=True)
    else:
        print("RESULT incomplete empty_final=%d" % empty_final, flush=True)
    return 0 if complete else 1


def main():
    running = other_scorer_running()
    if running:
        print("scorer already running: %s" % (running,))
        return 0
    key = os.environ.get("TYPESAFE_API_KEY") or ""
    if not key:
        print("key missing")
        return 2
    if not os.path.exists(PARTIAL):
        print("partial day file missing")
        return 1

    print("scanning partial day", flush=True)
    total, unscored_messages, need_copies, odd_jev, odd_final = scan_partial()
    print(
        "scan messages=%d unscored_messages=%d unscored_norms=%d odd_jev_only=%d odd_final_only=%d"
        % (total, unscored_messages, len(need_copies), odd_jev, odd_final),
        flush=True,
    )
    if total != EXPECTED_MESSAGES or unscored_messages != EXPECTED_UNSCORED:
        print("scan does not match the partial day counts; not calling")
        return 1

    print("loading cache hits for unscored norms", flush=True)
    hits, ignored, reusable_n = load_cached(set(need_copies))
    skipped_template = sum(1 for n, c in need_copies.items() if c >= sj.TEMPLATE_AT)
    todo = [n for n, c in need_copies.items() if c < sj.TEMPLATE_AT and n not in hits]
    print(
        "cache reusable=%d ignored=%d already_reusable_unscored=%d skipped_copies_ge_50=%d to_call=%d"
        % (reusable_n, ignored, len(hits), skipped_template, len(todo)),
        flush=True,
    )
    if len(todo) > EXPECTED_UNSCORED:
        print("to_call exceeds unscored messages; not calling")
        return 1

    with open(PID_PATH, "w", encoding="utf-8") as f:
        f.write(str(os.getpid()))
    log_fh = open(PROGRESS_LOG, "a", encoding="utf-8")
    try:
        start = (
            "resume start unscored_messages=%d unscored_norms=%d already_reusable=%d "
            "skipped_copies_ge_50=%d to_call=%d"
            % (unscored_messages, len(need_copies), len(hits), skipped_template, len(todo))
        )
        print(start, flush=True)
        log_fh.write(start + "\n")
        log_fh.flush()
        scored = dict(hits)
        stats = {
            "calls": 0,
            "tokens": 0,
            "failures": 0,
            "cost_stop": False,
            "billing_stop": False,
            "fatal": False,
            "model": "",
            "scored": 0,
            "left": 0,
        }
        if todo:
            stats = score_todo(todo, key, scored, log_fh)
        print("writing day outputs", flush=True)
        return write_outputs(scored, stats)
    finally:
        log_fh.close()
        try:
            os.remove(PID_PATH)
        except OSError:
            pass


if __name__ == "__main__":
    sys.exit(main())
