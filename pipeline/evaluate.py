"""One command that regenerates every number in the project from seed.

    python -m pipeline.evaluate            full run
    python -m pipeline.evaluate --verify   run the numeric stages twice, compare
    python -m pipeline.evaluate --reuse    skip generation, reuse data on disk

Writes:

    results/results.json   every number, machine readable. DETERMINISTIC.
    results/run_meta.json  commit, seeds, config, timings. NOT deterministic.
    results/summary.md     the same numbers, readable
    results/pr_auc_vs_decline.png
    results/logs/*.txt     raw stage output, archived

**The split between results.json and run_meta.json is the point.** Wall times and
timestamps change every run, so mixing them into the numbers file would make
byte-identity impossible to check. Provenance and timing live in run_meta.json;
results.json contains only numbers that must be reproducible, and `--verify`
asserts it is byte identical across two runs.

**Determinism note.** OMP_NUM_THREADS is pinned to 1 before anything imports
numpy or sklearn. Multi-threaded float reduction reorders summation, which can
change the last bits of a gradient-boosting score and break byte-identity for
reasons that have nothing to do with our code. Pinning it is a pipeline setting,
not a model change, and it is recorded in run_meta.json.
"""

import os

# Must precede any numpy or sklearn import in this process or its children.
for _v in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS",
           "NUMEXPR_NUM_THREADS"):
    os.environ[_v] = "1"
os.environ["PYTHONHASHSEED"] = "0"

import argparse
import hashlib
import json
import subprocess
import sys
import time

from pipeline import parsers

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RESULTS = os.path.join(ROOT, "results")
LOGS = os.path.join(RESULTS, "logs")

SEED = 42
DAYS = 30
ACTORS = 40000
GRADES = (0.00, 0.25, 0.50, 0.75, 0.90, 1.00)
NL = chr(10)


def sh(cmd, log_name, timings):
    """Run a stage, archive its output, time it."""
    os.makedirs(LOGS, exist_ok=True)
    t0 = time.time()
    print(f"  [{log_name}] {' '.join(cmd[2:])[:70]} ...", flush=True)
    p = subprocess.run([sys.executable, "-u"] + cmd[1:], cwd=ROOT,
                       capture_output=True, text=True, env=dict(os.environ))
    dt = time.time() - t0
    out = p.stdout + p.stderr
    with open(os.path.join(LOGS, log_name + ".txt"), "w", encoding="utf-8",
              newline="\n") as fh:
        fh.write(out)
    timings[log_name] = round(dt, 1)
    print(f"      {dt/60:.1f} min, exit {p.returncode}", flush=True)
    if p.returncode != 0:
        # Acceptance exits non-zero on a failing test, which is expected and is
        # itself a recorded result. Only a crash should stop the pipeline.
        if "Traceback" in out:
            raise RuntimeError(f"stage {log_name} crashed:\n{out[-2500:]}")
    return out


def generate(timings):
    print("\n== 1/6 generate datasets from seed ==", flush=True)
    sh(["python", "-m", "src.generator.sweep", "--seed", str(SEED),
        "--days", str(DAYS), "--actors", str(ACTORS), "--out", "data/evasive"],
       "01_generate", timings)
    # data/sample is the v=0.00 dataset; keep both paths populated without
    # regenerating, since T7 already proves the two are byte identical.
    import shutil
    for f in ("events.jsonl", "sealed.jsonl", "manifest.json"):
        shutil.copyfile(os.path.join(ROOT, "data", "evasive", "v000", f),
                        os.path.join(ROOT, "data", "sample", f))


def main():
    ap = argparse.ArgumentParser(description="Reproduce every number from seed")
    ap.add_argument("--reuse", action="store_true",
                    help="skip generation and use the datasets already on disk")
    ap.add_argument("--verify", action="store_true",
                    help="run twice and require byte-identical results.json")
    ap.add_argument("--memory-profile", action="store_true",
                    help="run ONLY the bounded-state memory profile and write "
                         "results/memory_profile.json")
    ap.add_argument("--from-logs", action="store_true",
                    help="rebuild results.json from results/logs/ without "
                         "re-running any stage. For a parser or schema change.")
    args = ap.parse_args()

    os.makedirs(RESULTS, exist_ok=True)
    t_start = time.time()
    timings = {}
    results = {"schema": 1}

    if args.memory_profile:
        return memory_profile()

    if args.from_logs:
        return rebuild_from_logs()

    if not args.reuse:
        generate(timings)
    else:
        print("\n== 1/6 generate: SKIPPED (--reuse) ==", flush=True)

    print("\n== 2/6 acceptance tests T1-T8, all six grades ==", flush=True)
    results["acceptance"] = {}
    for v in GRADES:
        d = "data/evasive/v%03d" % round(v * 100)
        out = sh(["python", "-m", "tests.acceptance.runner", d],
                 "02_acceptance_v%03d" % round(v * 100), timings)
        results["acceptance"]["v=%.2f" % v] = parsers.acceptance(out)

    print("\n== 3/6 four detectors across six grades ==", flush=True)
    out = sh(["python", "-m", "tests.detector.evaluate_sweep", "data/evasive"],
             "03_detector_sweep", timings)
    results["detector_sweep"] = parsers.detector_sweep(out)

    print("\n== 4/6 ring detector ==", flush=True)
    out = sh(["python", "-m", "tests.detector.evaluate_ring", "data/sample"],
             "04_ring", timings)
    results["ring"] = parsers.ring(out)

    print("\n== 5/6 cost model and frozen threshold ==", flush=True)
    out = sh(["python", "-m", "tests.decision.evaluate_cost", "data/evasive"],
             "05_cost", timings)
    results["cost"] = parsers.cost_model(out)

    print("\n== 6/6 streaming equivalence ==", flush=True)
    # Equivalence only. The memory profile is its own target: it re-streams the
    # file five more times, which was 61% of the events this stage touched and
    # about 40% of the whole pipeline, to re-demonstrate something that does not
    # change between runs.
    out = sh(["python", "-m", "tests.runtime.evaluate_stream", "data/sample"],
             "06_streaming", timings)
    results["streaming"] = parsers.streaming(out)
    perf = _split_perf(results)

    # ---------------------------------------------------------------- write
    payload = json.dumps(results, indent=2, sort_keys=True) + "\n"
    with open(os.path.join(RESULTS, "results.json"), "w", encoding="utf-8",
              newline="\n") as fh:
        fh.write(payload)
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()

    total = time.time() - t_start
    meta = {
        "git_commit": _git(["rev-parse", "HEAD"]),
        "git_subject": _git(["log", "-1", "--format=%s"]),
        "git_committed_at": _git(["log", "-1", "--format=%cI"]),
        # A dirty tree means results.json cannot be traced to a commit alone.
        # Recorded rather than warned about, so a stale artifact is detectable.
        "git_dirty": bool(_git(["status", "--porcelain"])),
        "seed": SEED, "days": DAYS, "actors": ACTORS,
        "grades": list(GRADES),
        "python": sys.version.split()[0],
        "packages": _versions(),
        "thread_env": {k: os.environ[k] for k in
                       ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS",
                        "MKL_NUM_THREADS", "PYTHONHASHSEED")},
        "results_sha256": digest,
        "wall_time_seconds": round(total, 1),
        "stage_seconds": timings,
        # Timing measurements. Deliberately here and not in results.json.
        "performance": perf,
    }
    with open(os.path.join(RESULTS, "run_meta.json"), "w", encoding="utf-8",
              newline="\n") as fh:
        fh.write(json.dumps(meta, indent=2, sort_keys=True) + "\n")

    from pipeline.report import write_summary, write_chart
    write_summary(results, meta, os.path.join(RESULTS, "summary.md"))
    write_chart(results, os.path.join(RESULTS, "pr_auc_vs_decline.png"))

    print("\n" + "=" * 78)
    print(f"TOTAL WALL TIME {total/60:.1f} min")
    print("=" * 78)
    for k, v in sorted(timings.items(), key=lambda kv: -kv[1]):
        print(f"  {k:<28} {v/60:>6.1f} min  {v/total*100:>5.1f}%")
    print(f"\n  results.json sha256 {digest}")
    print(f"  git {meta['git_commit'][:12]} {meta['git_subject'][:44]!r}"
          f"  dirty={meta['git_dirty']}")

    if args.verify:
        verify(digest, timings)
    return 0


def memory_profile():
    """The bounded-state demonstration, on its own. Writes its own artifact.

    Split out on 2026-08-30 because it re-streams the file five times to show
    something that is a property of the design rather than of a given run. It is
    not a correctness check; the equivalence check in the main pipeline is, and
    that stays there.
    """
    timings = {}
    print("== memory profile (bounded state demonstration) ==", flush=True)
    out = sh(["python", "-m", "tests.runtime.evaluate_stream", "data/sample",
              "--memory"], "07_memory_profile", timings)
    parsed = parsers.streaming(out)
    payload = json.dumps({"schema": 1, "memory": parsed["memory"],
                          "throughput_note": "timing lives in run_meta.json"},
                         indent=2, sort_keys=True) + NL
    os.makedirs(RESULTS, exist_ok=True)
    with open(os.path.join(RESULTS, "memory_profile.json"), "w",
              encoding="utf-8", newline=NL) as fh:
        fh.write(payload)
    print(f"wrote results/memory_profile.json ({len(parsed['memory'])} rows, "
          f"{timings['07_memory_profile']/60:.1f} min)")
    return 0


def rebuild_from_logs():
    """Re-parse archived stage output. Changes no measurement, only the schema."""
    def rd(name):
        with open(os.path.join(LOGS, name + ".txt"), encoding="utf-8") as fh:
            return fh.read()

    results = {"schema": 1, "acceptance": {}}
    for v in GRADES:
        results["acceptance"]["v=%.2f" % v] = parsers.acceptance(
            rd("02_acceptance_v%03d" % round(v * 100)))
    results["detector_sweep"] = parsers.detector_sweep(rd("03_detector_sweep"))
    results["ring"] = parsers.ring(rd("04_ring"))
    results["cost"] = parsers.cost_model(rd("05_cost"))
    results["streaming"] = parsers.streaming(rd("06_streaming"))
    perf = _split_perf(results)

    # The archived log predates the split and still carries the memory profile.
    # Move it to its own artifact so a rebuild produces exactly what a fresh run
    # now produces. This relocates a measurement; it does not recompute one.
    memory = results["streaming"].pop("memory", None)
    if memory is not None:
        with open(os.path.join(RESULTS, "memory_profile.json"), "w",
                  encoding="utf-8", newline=NL) as fh:
            fh.write(json.dumps({"schema": 1, "memory": memory,
                                 "throughput_note":
                                     "timing lives in run_meta.json"},
                                indent=2, sort_keys=True) + NL)
        print(f"moved {len(memory)} memory rows to results/memory_profile.json")

    payload = json.dumps(results, indent=2, sort_keys=True) + NL
    with open(os.path.join(RESULTS, "results.json"), "w", encoding="utf-8",
              newline=NL) as fh:
        fh.write(payload)
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()

    meta_path = os.path.join(RESULTS, "run_meta.json")
    meta = json.load(open(meta_path, encoding="utf-8"))
    meta["results_sha256"] = digest
    meta["performance"] = perf
    meta["rebuilt_from_logs"] = True
    with open(meta_path, "w", encoding="utf-8", newline=NL) as fh:
        fh.write(json.dumps(meta, indent=2, sort_keys=True) + NL)

    from pipeline.report import write_summary, write_chart
    write_summary(results, meta, os.path.join(RESULTS, "summary.md"))
    write_chart(results, os.path.join(RESULTS, "pr_auc_vs_decline.png"))
    print(f"rebuilt from logs. results.json sha256 {digest}")
    return 0


def verify(first_digest, timings):
    """Run the numeric stages again and require byte-identical results.json."""
    print("\n" + "=" * 78)
    print("DETERMINISM CHECK: second run, comparing results.json byte for byte")
    print("=" * 78)
    print("  Generation is skipped on the second pass because T7 already proves")
    print("  it byte identical, and repeating it doubles the wall time.")
    p = subprocess.run([sys.executable, "-u", "-m", "pipeline.evaluate", "--reuse"],
                       cwd=ROOT, capture_output=True, text=True,
                       env=dict(os.environ))
    if p.returncode != 0:
        print(p.stdout[-3000:], p.stderr[-3000:])
        raise SystemExit("second run failed")
    payload = open(os.path.join(RESULTS, "results.json"), encoding="utf-8").read()
    second = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    print(f"  run 1 sha256 {first_digest}")
    print(f"  run 2 sha256 {second}")
    if first_digest != second:
        raise SystemExit(
            "NOT REPRODUCIBLE: results.json differs between two runs on the "
            "same seed. This is more important than the pipeline. Diff the two "
            "results.json files and find which key moved before doing anything "
            "else.")
    print("  IDENTICAL. Every number reproduces from seed.")


# Keys that are TIMING measurements rather than results. They vary with machine
# load and must not sit in the deterministic file.
#
# Found by the determinism check on 2026-08-30, which is what it is for: two full
# runs agreed on 630 of 632 leaf values and disagreed on exactly these two, at
# 453 vs 553 events/sec. Everything computed from the data reproduced; the only
# thing that moved was a stopwatch reading that should never have been in
# results.json in the first place.
PERF_KEYS = (("streaming", "throughput_events_per_sec"),
             ("streaming", "throughput_ms_per_event"))


def _split_perf(results):
    """Move timing measurements out of results into a perf dict for run_meta."""
    perf = {}
    for section, key in PERF_KEYS:
        if section in results and key in results[section]:
            perf[f"{section}.{key}"] = results[section].pop(key)
    return perf


def _git(args):
    try:
        return subprocess.run(["git"] + list(args), cwd=ROOT,
                              capture_output=True, text=True).stdout.strip()
    except Exception:
        return "unavailable"


def _versions():
    import numpy, scipy, sklearn, matplotlib
    return {"numpy": numpy.__version__, "scipy": scipy.__version__,
            "scikit-learn": sklearn.__version__,
            "matplotlib": matplotlib.__version__}


if __name__ == "__main__":
    sys.exit(main())
