"""Strict parsers from stage output to structured numbers.

Every parser here raises rather than returning a partial result. A silent
mis-parse would put a wrong number into results.json and from there into a
document, which is exactly the failure mode this pipeline exists to prevent.
If a table moves or a heading is renamed, the pipeline stops.

Raw stage output is archived under results/logs/ regardless, so nothing is lost
when a parser is updated.
"""

import re


class ParseError(RuntimeError):
    pass


def _section(text, start, end=None, what=""):
    i = text.find(start)
    if i < 0:
        raise ParseError(f"section start not found: {start!r} ({what})")
    j = text.find(end, i + len(start)) if end else len(text)
    if end and j < 0:
        j = len(text)
    return text[i:j]


GRADES = ("v=0.00", "v=0.25", "v=0.50", "v=0.75", "v=0.90", "v=1.00")
DETECTORS = ("baseline 1: rolling volume", "baseline 2: rolling decline",
             "baseline 3: combined", "GRAPH: fanout vs overlap")


def detector_sweep(text):
    """The central result: PR AUC, recall, precision, misses, FPs by grade."""
    out = {}
    curve = _section(text, "THE CURVE:", "PR AUC vs OBSERVED", "detector sweep")

    for metric, head in (("pr_auc", "  PR AUC"), ("recall", "  RECALL"),
                         ("precision", "  PRECISION")):
        block = _section(curve, head, "\n\n", metric)
        vals = {}
        for det in DETECTORS:
            m = re.search(re.escape(det) + r"\s+((?:-?\d+\.\d{4}\s*){6})", block)
            if not m:
                raise ParseError(f"{metric}: no row for {det!r}")
            nums = [float(x) for x in m.group(1).split()]
            if len(nums) != 6:
                raise ParseError(f"{metric}/{det}: got {len(nums)} values, want 6")
            vals[det] = dict(zip(GRADES, nums))
        out[metric] = vals

    miss = _section(text, "BURSTS MISSED ENTIRELY", "FALSE POSITIVES", "misses")
    out["bursts_missed"] = {}
    for det in DETECTORS:
        m = re.search(re.escape(det) + r"\s+((?:\d+\s+){5}\d+)", miss)
        if not m:
            raise ParseError(f"bursts_missed: no row for {det!r}")
        out["bursts_missed"][det] = dict(
            zip(GRADES, [int(x) for x in m.group(1).split()]))

    fps = _section(text, "FALSE POSITIVES / OF WHICH", "PR AUC vs OBSERVED", "fps")
    out["false_positives"] = {}
    for det in DETECTORS:
        m = re.search(re.escape(det) + r"\s+((?:\d+/\d+\s*){6})", fps)
        if not m:
            raise ParseError(f"false_positives: no row for {det!r}")
        cells = m.group(1).split()
        out["false_positives"][det] = {
            g: {"total": int(c.split("/")[0]), "in_flash_sale": int(c.split("/")[1])}
            for g, c in zip(GRADES, cells)}

    inv = _section(text, "INVERSION CHECK", "MACHINE READABLE", "inversion")
    out["roc_auc"] = {}
    for det in DETECTORS:
        m = re.search(re.escape(det) + r" ROC\s+((?:-?\d+\.\d{4}\s*){6})", inv)
        if not m:
            raise ParseError(f"roc: no row for {det!r}")
        out["roc_auc"][det] = dict(
            zip(GRADES, [float(x) for x in m.group(1).split()]))

    # Machine-readable block. Latency is what carries the negative result and
    # the case against the volume baseline, so it has to be citable rather than
    # only printed.
    out["latency"] = {}
    for m in re.finditer(r"^LATENCY_ROW ([\d.]+)\|([^|]+)\|([^|]+)\|([^|]+)\|([^|\s]+)",
                        text, re.M):
        g = "v=%.2f" % float(m.group(1))
        det, burst = m.group(2), m.group(3)
        cell = (None if m.group(4) == "NA"
                else {"minutes": float(m.group(4)),
                      "attempts_before_alert": int(m.group(5))})
        out["latency"].setdefault(g, {}).setdefault(det, {})[burst] = cell
    if not out["latency"]:
        raise ParseError("no LATENCY_ROW lines found")

    out["decline_by_attempt"] = {}
    for m in re.finditer(r"^DECLINE_ROW ([\d.]+)\|(\d+)\|([\d.]+)\|(\d+)",
                        text, re.M):
        g = "v=%.2f" % float(m.group(1))
        out["decline_by_attempt"].setdefault(g, {})["k=%s" % m.group(2)] = {
            "decline_rate": float(m.group(3)), "n_attempts": int(m.group(4))}
    if not out["decline_by_attempt"]:
        raise ParseError("no DECLINE_ROW lines found")

    # The 72 headline metrics, in a fixed order, so equality is checkable.
    out["headline_72"] = [
        out[m][d][g] for m in ("pr_auc", "recall", "precision")
        for d in DETECTORS for g in GRADES]
    if len(out["headline_72"]) != 72:
        raise ParseError(f"headline_72 has {len(out['headline_72'])} entries")
    return out


def acceptance(text):
    """T1 to T8 verdicts and details."""
    rows = {}
    for line in text.splitlines():
        m = re.match(r"\s*\[(PASS|FAIL)\]\s+(\S.*?)\s{2,}(.*)$", line)
        if m:
            rows[m.group(2).strip()] = {"pass": m.group(1) == "PASS",
                                        "detail": m.group(3).strip()}
    if not rows:
        raise ParseError("no acceptance verdicts found")
    return rows


def cost_model(text):
    """Money-optimal against F1-optimal, and the frozen-threshold excess."""
    out = {"operating_points": {}, "frozen_excess": {}}

    op = _section(text, "MONEY-OPTIMAL AGAINST F1-OPTIMAL",
                  "THE PRICE OF NOT ADAPTING", "operating points")
    for det in DETECTORS:
        m = re.search(re.escape(det)
                      + r"\s+([\d.]+)\s+([\d.]+)\s+([\d,]+)\s+([\d,]+)\s+([\d,]+)\s+([\d.]+)%",
                      op)
        if not m:
            raise ParseError(f"operating point row missing for {det!r}")
        out["operating_points"][det] = {
            "f1_threshold": float(m.group(1)),
            "money_threshold": float(m.group(2)),
            "f1_cost_rupees": float(m.group(3).replace(",", "")),
            "money_cost_rupees": float(m.group(4).replace(",", "")),
            "gap_rupees": float(m.group(5).replace(",", "")),
            "gap_pct": float(m.group(6)),
        }

    price = _section(text, "THE PRICE OF NOT ADAPTING", "WORKED EXAMPLE", "frozen")
    for det in DETECTORS:
        blk = _section(price, "  " + det + "\n", "\n\n  ", det)
        rows = {}
        for line in blk.splitlines():
            m = re.match(r"\s+(\d\.\d\d)\s+([\d.]+)%\s+([\d.]+)%\s+([\d.]+)\s+"
                         r"([\d.]+)\s+([\d,]+)\s+([\d,]+)\s+([\d,-]+)\s+([\d.-]+)%",
                         line)
            if m:
                rows["v=" + m.group(1)] = {
                    "attack_decline_pct": float(m.group(2)),
                    "p_authorize_pct": float(m.group(3)),
                    "frozen_threshold": float(m.group(4)),
                    "best_threshold": float(m.group(5)),
                    "frozen_cost_rupees": float(m.group(6).replace(",", "")),
                    "best_cost_rupees": float(m.group(7).replace(",", "")),
                    "excess_rupees": float(m.group(8).replace(",", "")),
                    "excess_pct": float(m.group(9)),
                }
        if len(rows) != 6:
            raise ParseError(f"frozen-threshold rows for {det!r}: got {len(rows)}")
        out["frozen_excess"][det] = rows

    m = re.search(r"implied ratio.*?:\s*([\d.]+)x", text, re.S)
    if not m:
        raise ParseError("implied decline ratio not found")
    out["implied_decline_ratio"] = float(m.group(1))
    return out


def streaming(text):
    """Equivalence, memory, throughput, latency."""
    out = {"equivalence": {}, "memory": [], "latency": {}}
    eq = _section(text, "EQUIVALENCE:", "alerts on", "equivalence")
    for det in DETECTORS:
        m = re.search(re.escape(det) + r"\s+(\d+)\s+(\d+)\s+([\d.eg+-]+)\s", eq)
        if not m:
            raise ParseError(f"equivalence row missing for {det!r}")
        out["equivalence"][det] = {"events": int(m.group(1)),
                                   "mismatches": int(m.group(2)),
                                   "max_abs_diff": float(m.group(3))}
    m = re.search(r"batch\s+(\d+)\s*\n\s*stream\s+(\d+)\s*\n\s*identical.*?:\s*(\w+)",
                  text)
    if not m:
        raise ParseError("alert comparison not found")
    out["alerts"] = {"batch": int(m.group(1)), "stream": int(m.group(2)),
                     "identical": m.group(3) == "True"}

    # The memory profile is opt in and normally absent: it re-streams the file
    # five times and is a one-off demonstration, not a correctness check. When
    # present it is parsed; when absent that is expected, not an error. It has
    # its own artifact, results/memory_profile.json, written by `make
    # memory-profile`.
    if "BOUNDED STATE" in text:
        mem = _section(text, "BOUNDED STATE", "THROUGHPUT", "memory")
        for line in mem.splitlines():
            m = re.match(r"\s+(\d+)\s+([\d.]+)\s+(\d+)\s+(\d+)\s+([\d.]+)\s*$",
                         line)
            if m:
                out["memory"].append({"events": int(m.group(1)),
                                      "peak_kib": float(m.group(2)),
                                      "max_window_events": int(m.group(3)),
                                      "alerts": int(m.group(4))})
        if len(out["memory"]) < 3:
            raise ParseError("memory profile present but rows missing")
    else:
        out.pop("memory")

    m = re.search(r"([\d,]+) events in ([\d.]+)s = ([\d,]+) events/sec", text)
    if not m:
        raise ParseError("throughput line not found")
    out["throughput_events_per_sec"] = float(m.group(3).replace(",", ""))
    out["throughput_ms_per_event"] = float(
        re.search(r"([\d.]+) ms per event", text).group(1))

    lat = _section(text, "DETECTION LATENCY PER BURST, STREAMING PATH",
                   "Replay path", "latency")
    for line in lat.splitlines():
        m = re.match(r"\s+(b\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+([\d.]+)m\s+(\d+)", line)
        if m:
            out["latency"][m.group(1)] = {"events": int(m.group(2)),
                                          "latency_minutes": float(m.group(5)),
                                          "attempts_before_alert": int(m.group(6))}
    if not out["latency"]:
        raise ParseError("streaming latency rows not found")

    m = re.search(r"streaming reproduces batch exactly:\s*(\w+)", text)
    out["exact"] = bool(m and m.group(1) == "True")
    return out


def ring(text):
    """Ring detector, patched population only, which is the quoted result."""
    blk = _section(text, "COUNTERFACTUAL: HOUSEHOLDS SHARE AN ADDRESS",
                   "SCALE INVARIANCE", "ring")
    out = {}
    for name in ("pincode baseline (peers on pincode)",
                 "stage 1 only: conjunction",
                 "RING DETECTOR: conj + drop addr"):
        m = re.search(re.escape(name)
                      + r"\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)\s+(\d+)\s+(\d+)",
                      blk)
        if not m:
            raise ParseError(f"ring row missing for {name!r}")
        out[name] = {"precision": float(m.group(1)), "recall": float(m.group(2)),
                     "f1": float(m.group(3)), "pr_auc": float(m.group(4)),
                     "tp": int(m.group(5)), "fp": int(m.group(6))}
    # The real operating point, so the artifact carries precision and the number
    # of accounts flagged rather than only a recall level.
    out["operating_point"] = {}
    for m2 in re.finditer(r"^RING_OP ([^|]+)\|([^|]+)\|([^|]+)\|([^|]+)\|"
                          r"([^|]+)\|([^|]+)\|([^|\s]+)", text, re.M):
        if m2.group(2) == "NA":
            out["operating_point"][m2.group(1)] = None
            continue
        out["operating_point"][m2.group(1)] = {
            "recall": float(m2.group(2)), "precision": float(m2.group(3)),
            "threshold": float(m2.group(4)), "flagged": int(m2.group(5)),
            "tp": int(m2.group(6)), "fp": int(m2.group(7))}
    if not out["operating_point"]:
        raise ParseError("no RING_OP lines found")

    out["conjunction_counts"] = {}
    for m2 in re.finditer(r"^CONJ_ROW (\w+)\|(\d+)\|(\d+)\|(\d+)", text, re.M):
        out["conjunction_counts"][m2.group(1)] = {
            "total": int(m2.group(2)), "benign": int(m2.group(3)),
            "ring": int(m2.group(4))}
    if len(out["conjunction_counts"]) != 2:
        raise ParseError("expected pre_fix and post_fix CONJ_ROW lines")

    m = re.search(r"RING DETECTOR: conj \+ drop addr\s+((?:[\d.]+\s+){3}[\d.]+)\s*\n",
                  _section(blk, "RECALL AT FIXED PRECISION", None, "ring recall"))
    if m:
        vals = [float(x) for x in m.group(1).split()]
        out["recall_at_precision"] = dict(zip(("0.30", "0.50", "0.70", "0.90"), vals))
    return out
