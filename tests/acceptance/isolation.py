"""T8 requirement 2: the oracle must never be reachable from the detector.

Asserts that no module under src/ imports anything from tests/, and that no
module under src/ mentions the sealed store path.
"""

import os
import re


def check_import_isolation(src_root="src"):
    bad_imports, bad_sealed = [], []
    pat_import = re.compile(r"^\s*(?:from|import)\s+tests\b", re.M)
    pat_sealed = re.compile(r"sealed", re.I)
    for dirpath, _, files in os.walk(src_root):
        if "__pycache__" in dirpath:
            continue
        for f in files:
            if not f.endswith(".py"):
                continue
            p = os.path.join(dirpath, f)
            txt = open(p, encoding="utf-8").read()
            if pat_import.search(txt):
                bad_imports.append(p)
            # emit.py legitimately WRITES the sealed store during generation;
            # what matters is that nothing on the inference side READS it. Scoped
            # to those paths. src/decision/ was added on 2026-08-30 and is held
            # to the same rule: it consumes scores and costs, never an outcome.
            inference = (os.path.join(src_root, "detector"),
                         os.path.join(src_root, "decision"),
                         os.path.join(src_root, "runtime"))
            if p.startswith(inference) and pat_sealed.search(txt):
                bad_sealed.append(p)
    return bad_imports, bad_sealed
