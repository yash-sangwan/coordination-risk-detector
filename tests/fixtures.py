"""The only path to the sealed outcome store.

Spec section 5, T8: the sealed store is readable only through a fixture helper
that lives under tests/. The detector's loader is given the event stream path
and has no code path to the outcome file. Nothing under src/ may import this.
"""

import json
import os


def load_events(path):
    with open(os.path.join(path, "events.jsonl"), encoding="utf-8") as fh:
        return [json.loads(l) for l in fh if l.strip()]


def load_manifest(path):
    with open(os.path.join(path, "manifest.json"), encoding="utf-8") as fh:
        return json.load(fh)


def load_sealed(path):
    """Labels and generative truth. Test fixtures only, never a feature input."""
    with open(os.path.join(path, "sealed.jsonl"), encoding="utf-8") as fh:
        return [json.loads(l) for l in fh if l.strip()]


def labels_by_id(path):
    return {s["id"]: s for s in load_sealed(path)}
