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
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RESULTS = os.path.join(ROOT, "results", "results.json")

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


def main():
    ap = argparse.ArgumentParser(description="Cite a number from results.json")
    ap.add_argument("key", nargs="?")
    ap.add_argument("--list", action="store_true", help="every available key")
    ap.add_argument("--render", metavar="FILE", help="substitute {{key}} in FILE")
    args = ap.parse_args()
    data = load()
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
