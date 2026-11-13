#!/usr/bin/env python3
"""Build a small demo drill.db from the original questions in sample/sample_questions.py.

Usage:
    python3 make_sample_bank.py        # writes drill.db beside this file (or $DRILL_DB)

Each question goes through the same validation as import_items.py, then its machine check
runs (computed value for arithmetic, truth-table enumeration for logic; see the docstring of
sample_questions.py). Only items whose check passes are stored as verified, so only they are
served; any item that fails is stored unverified and listed. Re-running adds nothing new.
"""
import os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "sample"))

from sample_questions import QUESTIONS, check   # noqa: E402
import drill                                     # noqa: E402
import import_items                              # noqa: E402


def main():
    passed, failed = [], []
    for n, q in enumerate(QUESTIONS, 1):
        row = {k: v for k, v in q.items() if k != "check"}
        row["source"] = "sample"
        clean, errs = import_items.normalize(row, n)
        if errs:
            sys.exit("\n".join(errs))
        (passed if check(q) else failed).append(clean)
    con = drill.db()
    added_v, arch_v = import_items.import_rows(con, passed, verified=True)
    added_u, arch_u = import_items.import_rows(con, failed, verified=False)
    for r in failed:
        print(f"check FAILED, stored unverified: {r['stem'][:70]}")
    n_a = con.execute("SELECT COUNT(*) FROM archetypes").fetchone()[0]
    n_i = con.execute("SELECT COUNT(*) FROM items WHERE verified=1 AND retired=0").fetchone()[0]
    print(f"{drill.DB}: added {added_v + added_u} items ({len(passed)} checked and verified, "
          f"{len(failed)} unverified); {n_a} archetypes, {n_i} verified items")


if __name__ == "__main__":
    main()
