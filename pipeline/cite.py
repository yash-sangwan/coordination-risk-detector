"""Make "no number typed by hand" enforceable.

    python -m pipeline.cite --list
    python -m pipeline.cite detector_sweep.pr_auc."GRAPH: fanout vs overlap"."v=1.00"
    python -m pipeline.cite --render draft.md > final.md

A document is written with `{{key}}` placeholders and rendered through this
module. An unknown key is a hard error, so a figure cannot be invented, and a
key whose value changed is picked up automatically on the next render. The rule
is enforced by the renderer failing, not by anyone remembering it.
"""

import argparse
import io
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RESULTS = os.path.join(ROOT, "results", "results.json")

# Every generated document, as (template, output). Adding a pair here is all it
# takes for --render-all and --check to cover it.
GENERATED = (
    ("README.template.md", "README.md"),
    ("docs/architecture.template.md", "docs/architecture.md"),
)

_KEY = re.compile(r'"([^"]+)"|([^.\[\]"]+)')


def load(path=RESULTS):
    if not os.path.exists(path):
        raise SystemExit(f"{path} not found. Run: python -m pipeline.evaluate")
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def get(key, data=None):
    """Dotted lookup. Quoted segments allow dots and spaces inside a key."""
    data = load() if data is None else data
    cur = data
    for m in _KEY.finditer(key):
        seg = m.group(1) if m.group(1) is not None else m.group(2)
        if isinstance(cur, list):
            cur = cur[int(seg)]
        else:
            if seg not in cur:
                raise KeyError(
                    f"{key!r}: no such key at {seg!r}. "
                    f"Available: {sorted(cur)[:12] if isinstance(cur, dict) else type(cur)}")
            cur = cur[seg]
    return cur


def flatten(data, prefix=""):
    out = {}
    if isinstance(data, dict):
        for k, v in data.items():
            kk = f'{prefix}."{k}"' if ("." in k or " " in k) else f"{prefix}.{k}"
            out.update(flatten(v, kk.lstrip(".")))
    elif isinstance(data, list):
        for i, v in enumerate(data):
            out.update(flatten(v, f"{prefix}[{i}]"))
    else:
        out[prefix] = data
    return out


PLACEHOLDER = re.compile(r"\{\{([^}]+)\}\}")


def render(text, data=None):
    """Substitute every {{key}}. Unknown keys raise, they do not pass through."""
    data = load() if data is None else data
    missing = []

    def sub(m):
        key = m.group(1).strip()
        fmt = None
        if "|" in key:
            key, fmt = [x.strip() for x in key.split("|", 1)]
        try:
            val = get(key, data)
        except (KeyError, IndexError, ValueError) as exc:
            missing.append(f"{key}  ({exc})")
            return m.group(0)
        return format(val, fmt) if fmt else str(val)

    out = PLACEHOLDER.sub(sub, text)
    if missing:
        raise SystemExit("unknown keys, refusing to render:\n  "
                         + "\n  ".join(missing))
    return out


def render_all(data=None):
    """Re-render every registered pair. Returns the paths written."""
    data = load() if data is None else data
    written = []
    for tmpl, out in GENERATED:
        text = render(io.open(os.path.join(ROOT, tmpl), encoding="utf-8").read(), data)
        io.open(os.path.join(ROOT, out), "w", encoding="utf-8",
                newline="\n").write(text)
        written.append(out)
    return written


def check(data=None):
    """Which generated files no longer match what their template produces.

    Returns [(output, template, first differing line number)]. It cannot tell a
    hand edit apart from a file left stale by a newer artifact: both show up as
    a difference, and both are fixed the same way, by re-rendering. What it does
    guarantee is that neither goes unnoticed.
    """
    data = load() if data is None else data
    bad = []
    for tmpl, out in GENERATED:
        expected = render(io.open(os.path.join(ROOT, tmpl), encoding="utf-8").read(),
                          data)
        path = os.path.join(ROOT, out)
        actual = io.open(path, encoding="utf-8").read() if os.path.exists(path) else ""
        if actual == expected:
            continue
        a, b = actual.split("\n"), expected.split("\n")
        line = next((i + 1 for i in range(max(len(a), len(b)))
                     if (a[i] if i < len(a) else None)
                     != (b[i] if i < len(b) else None)), 1)
        bad.append((out, tmpl, line))
    return bad


def main():
    ap = argparse.ArgumentParser(description="Cite a number from results.json")
    ap.add_argument("key", nargs="?")
    ap.add_argument("--list", action="store_true", help="every available key")
    ap.add_argument("--render", metavar="FILE", help="substitute {{key}} in FILE")
    ap.add_argument("--render-all", action="store_true",
                    help="re-render every generated document in place")
    ap.add_argument("--check", action="store_true",
                    help="fail if any generated document no longer matches its "
                         "template")
    args = ap.parse_args()
    data = load()
    if args.render_all:
        for out in render_all(data):
            print("rendered", out)
        return 0
    if args.check:
        bad = check(data)
        if not bad:
            print("all %d generated documents match their templates"
                  % len(GENERATED))
            return 0
        print("GENERATED FILES DO NOT MATCH THEIR TEMPLATES")
        for out, tmpl, line in bad:
            print("  %s differs from a render of %s, first at line %d"
                  % (out, tmpl, line))
        print("")
        print("Either the file was edited by hand, which the next render would")
        print("discard, or the artifact changed and it was never re-rendered.")
        print("Both are fixed the same way:")
        print("  python -m pipeline.cite --render-all")
        return 1
    if args.list:
        for k, v in sorted(flatten(data).items()):
            print(f"{k} = {v}")
        return 0
    if args.render:
        sys.stdout.write(render(open(args.render, encoding="utf-8").read(), data))
        return 0
    if not args.key:
        ap.error("give a key, --list, or --render")
    print(get(args.key, data))
    return 0


if __name__ == "__main__":
    sys.exit(main())
