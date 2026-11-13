#!/usr/bin/env python3
"""Load your own multiple-choice questions into the drill bank from CSV or JSON.

Usage:
    python3 import_items.py my_questions.csv --dry-run    # validate only, write nothing
    python3 import_items.py my_questions.json             # import into drill.db (or $DRILL_DB)
    python3 import_items.py my_questions.csv --unverified # import, but hold items back from serving

Fields (CSV header row, or keys of each object in a JSON list):
    section       required  e.g. Math, Logic, Reading. Sections come from your data.
    archetype     required  the mistake pattern the item tests (alias: topic). Items that share
                            section + archetype are drilled together toward one mastery streak.
    difficulty    optional  free text, e.g. Easy / Medium / Hard
    stem          required  the question text
    choices       required  2-8 options: a JSON list, or in CSV one cell separated by " | "
    key           required  the correct option: a letter (A = first), a 1-based number,
                            or the exact text of the correct choice
    explanation   optional  shown after the learner answers (the audit warns when missing)
    pattern       optional  one-line description of the trap, shown while drilling
    target_seconds optional per-item target time for the archetype (default 90)

Re-running an import is safe: an item whose stem already exists in the same archetype is skipped.
"""
import argparse, csv, json, os, sys

import drill

REQUIRED = ("section", "archetype", "stem", "choices", "key")


def load_rows(path):
    if path.lower().endswith(".json"):
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            data = data.get("items", [])
        if not isinstance(data, list):
            raise ValueError("JSON must be a list of objects (or {\"items\": [...]})")
        return data
    with open(path, newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def normalize(row, n):
    """Return (clean dict, list of errors) for one input row (n = 1-based row number)."""
    errs = []
    r = {str(k).strip().lower(): v for k, v in row.items() if k is not None}
    if not r.get("archetype") and r.get("topic"):
        r["archetype"] = r["topic"]
    for k in REQUIRED:
        if r.get(k) in (None, "", []):
            errs.append(f"row {n}: missing {k}")
    if errs:
        return None, errs
    choices = r["choices"]
    if isinstance(choices, str):
        s = choices.strip()
        if s.startswith("["):
            try:
                choices = json.loads(s)
            except json.JSONDecodeError:
                return None, [f"row {n}: choices look like JSON but do not parse"]
        else:
            choices = [c.strip() for c in s.split("|")]
    choices = [str(c).strip() for c in choices]
    if not 2 <= len(choices) <= len(drill.LETTERS):
        errs.append(f"row {n}: {len(choices)} choices (need 2-{len(drill.LETTERS)})")
    if any(not c for c in choices):
        errs.append(f"row {n}: empty choice")
    if len({c.lower() for c in choices}) < len(choices):
        errs.append(f"row {n}: duplicate choices")
    key = str(r["key"]).strip()
    if len(key) == 1 and key.upper() in drill.LETTERS[:len(choices)]:
        letter = key.upper()
    elif key.isdigit() and 1 <= int(key) <= len(choices):
        letter = drill.LETTERS[int(key) - 1]
    elif key in choices:
        letter = drill.LETTERS[choices.index(key)]
    else:
        errs.append(f"row {n}: key {key!r} matches no choice")
        letter = None
    target = r.get("target_seconds")
    try:
        target = int(float(target)) if target not in (None, "") else drill.DEFAULT_TARGET_S
        if target <= 0:
            raise ValueError
    except (TypeError, ValueError):
        errs.append(f"row {n}: target_seconds must be a positive number")
    if errs:
        return None, errs
    return dict(section=str(r["section"]).strip(), archetype=str(r["archetype"]).strip(),
                difficulty=str(r.get("difficulty") or "").strip(), stem=str(r["stem"]).strip(),
                choices=choices, key=letter, explanation=str(r.get("explanation") or "").strip(),
                pattern=str(r.get("pattern") or "").strip(), target=target,
                source=str(r.get("source") or "import").strip()), []


def import_rows(con, rows, verified=True):
    """Insert validated rows; returns (items added, archetypes created)."""
    added = created = 0
    for r in rows:
        ref = f"{r['section']}:{r['archetype']}".lower()
        a = con.execute("SELECT id FROM archetypes WHERE ref=?", (ref,)).fetchone()
        if a:
            aid = a[0]
        else:
            aid = con.execute("""INSERT INTO archetypes(ref, section, name, pattern, difficulty,
                                 target_time_s, priority, status, created) VALUES(?,?,?,?,?,?,?,?,?)""",
                              (ref, r["section"], r["archetype"], r["pattern"] or r["archetype"],
                               r["difficulty"], r["target"], 1.0, "active", drill.now())).lastrowid
            created += 1
        if con.execute("SELECT 1 FROM items WHERE archetype_id=? AND stem=?", (aid, r["stem"])).fetchone():
            continue
        con.execute("""INSERT INTO items(archetype_id, stem, choices, answer, explanation, difficulty,
                       source, verified, created) VALUES(?,?,?,?,?,?,?,?,?)""",
                    (aid, r["stem"], json.dumps(r["choices"]), r["key"], r["explanation"],
                     r["difficulty"], r["source"], int(verified), drill.now()))
        added += 1
    con.commit()
    return added, created


def main(argv=None):
    p = argparse.ArgumentParser(description="Import multiple-choice items from CSV or JSON.")
    p.add_argument("file")
    p.add_argument("--dry-run", action="store_true", help="validate and report; write nothing")
    p.add_argument("--unverified", action="store_true",
                   help="store items as unverified (not served until `drill.py verify`)")
    p.add_argument("--db", help="database path (default: $DRILL_DB or drill.db)")
    a = p.parse_args(argv)
    try:
        raw = load_rows(a.file)
    except (OSError, ValueError, json.JSONDecodeError) as e:
        sys.exit(f"cannot read {a.file}: {e}")
    good, errors = [], []
    for n, row in enumerate(raw, 1):
        if not isinstance(row, dict):
            errors.append(f"row {n}: not an object"); continue
        clean, errs = normalize(row, n)
        errors.extend(errs)
        if clean:
            good.append(clean)
    for e in errors:
        print(e, file=sys.stderr)
    print(f"{len(raw)} rows read, {len(good)} valid, {len(errors)} problems")
    if errors:
        sys.exit("nothing imported: fix the problems above first")
    if a.dry_run:
        print("dry run: nothing written")
        return
    con = drill.db(a.db)
    added, created = import_rows(con, good, verified=not a.unverified)
    print(f"{a.db or drill.DB}: {added} items added, {created} new archetypes")


if __name__ == "__main__":
    main()
