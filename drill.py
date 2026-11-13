#!/usr/bin/env python3
"""Adaptive drill engine: archetype-based practice for any multiple-choice subject.

Items are grouped into *archetypes*: one archetype is one mistake pattern (for example
"applies two percent changes additively"), carrying several different questions that test
it. The engine drills an archetype until it is mastered (STREAK_N straight answers that are
correct AND inside the target time, across distinct items), then schedules decay checks so
mastery has to hold up days and weeks later.

Python standard library only (sqlite3 + http.server). One file for the engine and the JSON
API, one HTML file for the browser UI.

Commands:
  serve [--host H] [--port P]    run the local web app (default 127.0.0.1:8780)
  status                         mastery table, one row per archetype
  progress [--hours H]           windowed analytics (attempts, accuracy, pace, misses)
  audit                          check the bank: keys, choices, duplicates, thin archetypes
  pace [SET_ID] [--list]         pacing report for a finished timed set
  show --archetype ID            one archetype and its items
  verify ITEM_ID [ITEM_ID ...]   mark items verified so they can be served
                                 (use --archetype ID to verify a whole archetype)

Data lives in drill.db beside this file, or wherever $DRILL_DB points.
Load questions with import_items.py or build the demo bank with make_sample_bank.py.
"""

import argparse, json, os, random, re, sqlite3, sys, traceback
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
DB = os.environ.get("DRILL_DB", os.path.join(HERE, "drill.db"))
UI = os.path.join(HERE, "drill_ui.html")

STREAK_N = 3            # consecutive correct + on-time answers that master an archetype
MIN_DISTINCT = 3        # ...spread over this many distinct items (capped at what is banked)
COOLDOWN_HOURS = 48     # an item already seen cannot come back inside this window
RECHECK_DAYS = (3, 7, 21)   # decay checks after mastery; a miss reopens the archetype
DEFAULT_TARGET_S = 90   # per-item target time when the bank does not give one
CAP_FACTOR = 1.5        # timed sets: an item is "over cap" past CAP_FACTOR x its target
SINKHOLE_GRACE_S = 30   # ...and a sinkhole when it is this far over cap AND answered wrong

# Burn rule: an item answered wrong has had its explanation shown, so a later correct answer
# could be recall rather than skill. Missed items leave every serving path for good; write
# fresh items for the archetype instead. Derived from attempts, so it needs no extra column.
BURNED = "(SELECT bt.item_id FROM attempts bt WHERE bt.is_correct=0)"
SERVABLE = f"i.verified=1 AND i.retired=0 AND i.id NOT IN {BURNED}"

FAVICON_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">
<rect width="64" height="64" rx="14" fill="#5b8def"/>
<circle cx="18" cy="32" r="6" fill="#fff"/><circle cx="32" cy="32" r="6" fill="#fff" opacity=".75"/>
<circle cx="46" cy="32" r="6" fill="#fff" opacity=".45"/></svg>"""

LETTERS = "ABCDEFGH"


# ---------------------------------------------------------------- database

def db(path=None):
    path = path or DB
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    con.executescript("""
    CREATE TABLE IF NOT EXISTS archetypes(
      id INTEGER PRIMARY KEY,
      ref TEXT UNIQUE,                  -- stable key from the importer: "section:archetype"
      section TEXT, name TEXT,
      pattern TEXT,                     -- the mistake pattern this archetype trains against
      difficulty TEXT,
      target_time_s INTEGER,
      priority REAL DEFAULT 0,
      status TEXT DEFAULT 'active',     -- active | mastered | retired
      mastered_at TEXT, recheck_stage INTEGER DEFAULT 0, created TEXT);
    CREATE TABLE IF NOT EXISTS items(
      id INTEGER PRIMARY KEY,
      archetype_id INTEGER REFERENCES archetypes(id),
      stem TEXT, choices TEXT,          -- JSON list of choice texts
      answer TEXT,                      -- key letter, A = first choice
      explanation TEXT, difficulty TEXT, source TEXT,
      verified INTEGER DEFAULT 0, retired INTEGER DEFAULT 0, created TEXT);
    CREATE TABLE IF NOT EXISTS attempts(
      id INTEGER PRIMARY KEY,
      item_id INTEGER REFERENCES items(id), archetype_id INTEGER,
      ts TEXT, given TEXT, is_correct INTEGER, seconds REAL,
      mode TEXT DEFAULT 'drill');       -- drill | recheck | set
    CREATE TABLE IF NOT EXISTS sets(
      id INTEGER PRIMARY KEY, started TEXT, section TEXT, n INTEGER, clock_s INTEGER,
      item_ids TEXT, perms TEXT, answers TEXT,
      flags INTEGER DEFAULT 0, changes INTEGER DEFAULT 0, finished TEXT, correct INTEGER);
    CREATE INDEX IF NOT EXISTS attempts_arch ON attempts(archetype_id, ts);
    CREATE INDEX IF NOT EXISTS attempts_item ON attempts(item_id);
    """)
    return con


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def num_set(text):
    return set(re.findall(r"\d+(?:\.\d+)?", text or ""))


def too_similar(new_stem, old_stems):
    """True if a stem reuses most of the numbers of another stem (a lazy clone)."""
    a = num_set(new_stem)
    for old in old_stems:
        b = num_set(old)
        if len(a) >= 3 and len(b) >= 3 and len(a & b) / len(a | b) > 0.6:
            return True
    return False


# ---------------------------------------------------------------- mastery

def _trailing(con, arch_id):
    """Rows of the current trailing run of correct + on-time attempts, newest first."""
    out = []
    for r in con.execute("""SELECT a.is_correct, a.seconds, a.item_id, ar.target_time_s
                            FROM attempts a JOIN archetypes ar ON ar.id=a.archetype_id
                            WHERE a.archetype_id=? ORDER BY a.ts DESC, a.id DESC""", (arch_id,)):
        if r["is_correct"] and r["seconds"] <= (r["target_time_s"] or DEFAULT_TARGET_S):
            out.append(r)
        else:
            break
    return out


def streak(con, arch_id):
    """(length of the trailing correct + on-time run, distinct items in it)."""
    run = _trailing(con, arch_id)
    return len(run), len({r["item_id"] for r in run})


def banked(con, arch_id):
    return con.execute("SELECT COUNT(*) FROM items WHERE archetype_id=? AND verified=1 AND retired=0",
                       (arch_id,)).fetchone()[0]


def live(con, arch_id):
    return con.execute(f"SELECT COUNT(*) FROM items i WHERE i.archetype_id=? AND {SERVABLE}",
                       (arch_id,)).fetchone()[0]


def need(con, arch_id):
    """Streak length and distinct-item count this archetype needs. Both are capped at the
    number of live items (verified, not retired, not burned), so a small archetype is
    mastered by one clean pass over what is left; the audit flags it as thin."""
    b = max(1, live(con, arch_id))
    return min(STREAK_N, b), min(MIN_DISTINCT, b)


def mastered(con, arch_id):
    s, d = streak(con, arch_id)
    ns, nd = need(con, arch_id)
    return s >= ns and d >= nd


def due_recheck(con, section=None):
    """Oldest mastered archetype whose next decay check is due, or None."""
    where, params = ["ar.status='mastered'", "ar.recheck_stage < ?"], [len(RECHECK_DAYS)]
    if section:
        where.append("ar.section=?"); params.append(section)
    for a in con.execute(f"""SELECT ar.*, julianday('now') - julianday(ar.mastered_at) age
            FROM archetypes ar WHERE {' AND '.join(where)}
              AND EXISTS (SELECT 1 FROM items i WHERE i.archetype_id=ar.id AND {SERVABLE})
            ORDER BY ar.mastered_at""", params):
        if a["age"] is not None and a["age"] >= RECHECK_DAYS[a["recheck_stage"]]:
            return a
    return None


# ---------------------------------------------------------------- serving

def _shuffled(choices):
    perm = list(range(len(choices)))
    random.shuffle(perm)
    return [choices[p] for p in perm], perm


def _pick_item(con, arch_id, ignore_cooldown=False):
    """Least-attempted servable item of an archetype that is not in the current streak and
    has not been seen inside the cooldown window."""
    used = {r["item_id"] for r in _trailing(con, arch_id)}
    ph = ",".join("?" * len(used)) or "0"
    cool = "" if ignore_cooldown else """AND i.id NOT IN (SELECT t.item_id FROM attempts t
            WHERE t.archetype_id=? AND julianday(t.ts) >= julianday('now', ?))"""
    params = [arch_id, *used] + ([] if ignore_cooldown else [arch_id, f"-{COOLDOWN_HOURS} hours"])
    return con.execute(f"""SELECT i.*, (SELECT COUNT(*) FROM attempts t WHERE t.item_id=i.id) n
        FROM items i WHERE i.archetype_id=? AND {SERVABLE} AND i.id NOT IN ({ph}) {cool}
        ORDER BY n, i.id""", params).fetchone()


def _serve(con, a, item, mode):
    choices, perm = _shuffled(json.loads(item["choices"]))
    s, _ = streak(con, a["id"])
    ns, nd = need(con, a["id"])
    return dict(item_id=item["id"], arch_id=a["id"], archetype=a["name"], section=a["section"],
                pattern=a["pattern"], difficulty=item["difficulty"] or a["difficulty"],
                stem=item["stem"], choices=choices, perm=perm, target=a["target_time_s"],
                streak=s, need=ns, mode=mode)


def pick_next(con, section=None, archetype=None):
    """The next drill item.
    1. A due decay check on a mastered archetype comes first (unless one archetype is pinned).
    2. Otherwise active archetypes in priority order, with win-rotate interleaving: an
       archetype whose latest attempt was correct goes to the back, so a streak continues
       after other material instead of massed back-to-back; a miss keeps practice immediate.
    """
    if not archetype:
        a = due_recheck(con, section)
        if a is not None:
            item = _pick_item(con, a["id"], ignore_cooldown=True)
            if item is not None:
                return _serve(con, a, item, "recheck")
    where, params = ["ar.status='active'"], []
    if archetype:
        where, params = ["ar.id=?", "ar.status<>'retired'"], [archetype]
    if section:
        where.append("ar.section=?"); params.append(section)
    arcs = con.execute(f"""SELECT ar.* FROM archetypes ar WHERE {' AND '.join(where)}
          AND EXISTS (SELECT 1 FROM items i WHERE i.archetype_id=ar.id AND {SERVABLE})
        ORDER BY ar.priority DESC, ar.id""", params).fetchall()
    last = con.execute("SELECT archetype_id, is_correct FROM attempts ORDER BY ts DESC, id DESC LIMIT 1").fetchone()
    if last and last["is_correct"] and len(arcs) > 1:
        arcs = [a for a in arcs if a["id"] != last["archetype_id"]] + \
               [a for a in arcs if a["id"] == last["archetype_id"]]
    for a in arcs:
        if a["status"] == "active" and mastered(con, a["id"]):
            con.execute("UPDATE archetypes SET status='mastered', mastered_at=?, recheck_stage=0 WHERE id=?",
                        (now(), a["id"]))
            con.commit()
            if not archetype:
                continue
        item = _pick_item(con, a["id"])
        if item is not None:
            return _serve(con, a, item, "drill")
    return dict(done=True, message="Nothing due: every open archetype is mastered, cooling down, "
                                   "or out of fresh verified items.")


def _unperm(given, perm):
    """Map a displayed letter back to the bank's letter."""
    if not given:
        return None
    idx = LETTERS.index(given.upper())
    if perm:
        idx = perm[idx]
    return LETTERS[idx]


def record_answer(con, item_id, given, seconds, perm=None, mode="drill"):
    item = con.execute("SELECT i.*, ar.target_time_s, ar.status, ar.recheck_stage FROM items i "
                       "JOIN archetypes ar ON ar.id=i.archetype_id WHERE i.id=?", (item_id,)).fetchone()
    if item is None:
        raise ValueError(f"no item {item_id}")
    bank_letter = _unperm(given, perm)
    ok = int(bank_letter == item["answer"])
    target = item["target_time_s"] or DEFAULT_TARGET_S
    con.execute("INSERT INTO attempts(item_id, archetype_id, ts, given, is_correct, seconds, mode) "
                "VALUES(?,?,?,?,?,?,?)", (item_id, item["archetype_id"], now(), bank_letter, ok,
                                          float(seconds or 0), mode))
    aid, became = item["archetype_id"], None
    if item["status"] == "mastered":
        if ok and seconds <= target:
            con.execute("UPDATE archetypes SET recheck_stage=recheck_stage+1 WHERE id=?", (aid,))
        else:
            con.execute("UPDATE archetypes SET status='active', mastered_at=NULL, recheck_stage=0 "
                        "WHERE id=?", (aid,))
            became = "reopened"
    elif item["status"] == "active" and mastered(con, aid):
        con.execute("UPDATE archetypes SET status='mastered', mastered_at=?, recheck_stage=0 WHERE id=?",
                    (now(), aid))
        became = "mastered"
    con.commit()
    key_idx = LETTERS.index(item["answer"])
    shown_key = LETTERS[perm.index(key_idx)] if perm else item["answer"]
    s, _ = streak(con, aid)
    return dict(correct=bool(ok), on_time=seconds <= target, key=shown_key,
                explanation=item["explanation"] or "", streak=s, need=need(con, aid)[0],
                archetype_status=became, target=target)


# ---------------------------------------------------------------- timed sets

def assemble_set(con, section=None, n=10):
    """n servable items, round-robin across archetypes so one pattern never dominates."""
    where, params = [SERVABLE], []
    if section:
        where.append("ar.section=?"); params.append(section)
    rows = con.execute(f"""SELECT i.*, ar.target_time_s, ar.name,
            (SELECT COUNT(*) FROM attempts t WHERE t.item_id=i.id) seen
        FROM items i JOIN archetypes ar ON ar.id=i.archetype_id
        WHERE {' AND '.join(where)} AND ar.status<>'retired'""", params).fetchall()
    by_arch = {}
    for r in sorted(rows, key=lambda r: (r["seen"], random.random())):
        by_arch.setdefault(r["archetype_id"], []).append(r)
    queues = list(by_arch.values())
    random.shuffle(queues)
    picked = []
    while len(picked) < n and any(queues):
        for q in queues:
            if q and len(picked) < n:
                picked.append(q.pop(0))
    if not picked:
        return None
    clock = sum(r["target_time_s"] or DEFAULT_TARGET_S for r in picked)
    served, perms = [], []
    for r in picked:
        choices, perm = _shuffled(json.loads(r["choices"]))
        perms.append(perm)
        served.append(dict(item_id=r["id"], stem=r["stem"], choices=choices,
                           target=r["target_time_s"] or DEFAULT_TARGET_S))
    sid = con.execute("INSERT INTO sets(started, section, n, clock_s, item_ids, perms) VALUES(?,?,?,?,?,?)",
                      (now(), section or "", len(picked), clock,
                       json.dumps([r["id"] for r in picked]), json.dumps(perms))).lastrowid
    con.commit()
    return dict(set_id=sid, clock_s=clock, items=served)


def finish_set(con, set_id, answers, flags=0, changes=0):
    """answers: [{item_id, given (displayed letter or null), seconds}] in served order."""
    s = con.execute("SELECT * FROM sets WHERE id=?", (set_id,)).fetchone()
    if s is None:
        raise ValueError(f"no set {set_id}")
    if s["finished"]:
        raise ValueError(f"set {set_id} already finished")
    ids, perms = json.loads(s["item_ids"]), json.loads(s["perms"])
    got = {int(a["item_id"]): a for a in answers}
    stored, results = [], []
    for iid, perm in zip(ids, perms):
        a = got.get(iid, {})
        given, secs = a.get("given"), float(a.get("seconds") or 0)
        if given:
            r = record_answer(con, iid, given, secs, perm, mode="set")
        else:   # unanswered at time-out: counts as wrong, but the item is not burned
            item = con.execute("SELECT answer, explanation FROM items WHERE id=?", (iid,)).fetchone()
            r = dict(correct=False, key=LETTERS[perm.index(LETTERS.index(item["answer"]))],
                     explanation=item["explanation"] or "")
        stored.append(dict(item_id=iid, given=_unperm(given, perm), seconds=secs, correct=r["correct"]))
        results.append(dict(item_id=iid, given=given, key=r["key"], correct=r["correct"],
                            seconds=secs, explanation=r["explanation"]))
    ncorrect = sum(r["correct"] for r in results)
    con.execute("UPDATE sets SET answers=?, flags=?, changes=?, finished=?, correct=? WHERE id=?",
                (json.dumps(stored), int(flags), int(changes), now(), ncorrect, set_id))
    con.commit()
    return dict(set_id=set_id, correct=ncorrect, n=len(ids), results=results,
                pace=pace_report(con, set_id))


def pace_report(con, set_id):
    """Grade a finished set on pacing, not score:
    checkpoints  cumulative time at the 1/3 and 2/3 marks against the even-pace plan;
    caps         items over CAP_FACTOR x target, and sinkholes (far over cap AND wrong);
    endgame      time left entering the last three items against what they need.
    Time over cap that bought a right answer is spent, not lost, so it is listed but does
    not fail the set on its own."""
    s = con.execute("SELECT * FROM sets WHERE id=?", (set_id,)).fetchone()
    if s is None or not s["finished"]:
        return None
    rows = json.loads(s["answers"])
    targets = {r["id"]: r["target_time_s"] or DEFAULT_TARGET_S for r in con.execute(
        f"""SELECT i.id, ar.target_time_s FROM items i JOIN archetypes ar ON ar.id=i.archetype_id
            WHERE i.id IN ({','.join('?' * len(rows))})""", [r["item_id"] for r in rows])}
    n, clock = len(rows), s["clock_s"]
    tol = max(30, clock / n / 2)
    cum, plan, checkpoints = 0.0, 0.0, []
    marks = {max(1, round(n / 3)), max(1, round(2 * n / 3))}
    over, sinkholes, endgame = [], [], None
    for k, r in enumerate(rows, 1):
        t = targets.get(r["item_id"], DEFAULT_TARGET_S)
        if n - k + 1 == 3 or (n < 3 and k == 1):
            left = clock - cum
            needs = sum(targets.get(x["item_id"], DEFAULT_TARGET_S) for x in rows[k - 1:])
            endgame = dict(time_left_s=round(left), needed_s=round(0.75 * needs), ok=left >= 0.75 * needs)
        cum += r["seconds"]
        plan += t
        cap = CAP_FACTOR * t
        if r["seconds"] > cap:
            o = dict(pos=k, item_id=r["item_id"], seconds=round(r["seconds"]), cap_s=round(cap),
                     correct=r["correct"])
            over.append(o)
            if r["seconds"] > cap + SINKHOLE_GRACE_S and not r["correct"]:
                sinkholes.append(o)
        if k in marks:
            checkpoints.append(dict(after=k, used_s=round(cum), plan_s=round(plan),
                                    ok=cum <= plan + tol))
    ok = all(c["ok"] for c in checkpoints) and not sinkholes and (endgame is None or endgame["ok"])
    return dict(set_id=set_id, n=n, correct=s["correct"], clock_s=clock, used_s=round(cum),
                flags=s["flags"], changes=s["changes"], checkpoints=checkpoints,
                over_cap=over, sinkholes=sinkholes, endgame=endgame, passed=ok)


def recent_sets(con, limit=10):
    return [dict(r) for r in con.execute("""SELECT id, started, section, n, correct, clock_s, finished
        FROM sets WHERE finished IS NOT NULL ORDER BY id DESC LIMIT ?""", (limit,))]


# ---------------------------------------------------------------- reports

def sections(con):
    return [dict(section=r[0], archetypes=r[1], items=r[2]) for r in con.execute(
        f"""SELECT ar.section, COUNT(DISTINCT ar.id),
               (SELECT COUNT(*) FROM items i JOIN archetypes a2 ON a2.id=i.archetype_id
                 WHERE a2.section=ar.section AND {SERVABLE})
            FROM archetypes ar GROUP BY ar.section ORDER BY ar.section""")]


def archetype_table(con, section=None):
    q = "SELECT * FROM archetypes" + (" WHERE section=?" if section else "") + " ORDER BY section, id"
    out = []
    for a in con.execute(q, (section,) if section else ()):
        st = con.execute("""SELECT COUNT(*) n, COALESCE(SUM(is_correct),0) ok, AVG(seconds) avg_s
                            FROM attempts WHERE archetype_id=?""", (a["id"],)).fetchone()
        n_live = live(con, a["id"])
        s, d = streak(con, a["id"])
        out.append(dict(id=a["id"], section=a["section"], name=a["name"], pattern=a["pattern"],
                        status=a["status"], streak=s, distinct=d, need=need(con, a["id"])[0],
                        live_items=n_live, banked=banked(con, a["id"]), attempts=st["n"], correct=st["ok"],
                        avg_s=round(st["avg_s"] or 0, 1), target_s=a["target_time_s"],
                        recheck_stage=a["recheck_stage"]))
    return out


def progress_report(con, hours=24.0):
    """Windowed analytics shared by the API and the CLI. Time filters use julianday(ts):
    ts is ISO text with a 'T', so a plain string compare against datetime('now') output
    (space-separated) would give wrong answers."""
    span = f"-{hours} hours"
    W = "julianday(t.ts) >= julianday('now', ?)"
    base = con.execute(f"""SELECT COUNT(*) n, COALESCE(SUM(t.is_correct),0) ok,
          COALESCE(SUM(t.seconds),0)/60.0 mins,
          COALESCE(SUM(t.is_correct AND t.seconds <= COALESCE(ar.target_time_s, {DEFAULT_TARGET_S})),0) fast
        FROM attempts t JOIN archetypes ar ON ar.id=t.archetype_id WHERE {W}""", (span,)).fetchone()
    sect = {r["section"]: dict(n=r["n"], correct=r["ok"], avg_s=round(r["avg_s"] or 0, 1))
            for r in con.execute(f"""SELECT ar.section, COUNT(*) n, SUM(t.is_correct) ok, AVG(t.seconds) avg_s
                FROM attempts t JOIN archetypes ar ON ar.id=t.archetype_id WHERE {W}
                GROUP BY ar.section""", (span,))}
    mastered_rows = [dict(section=r["section"], name=r["name"], at_local=r["tl"]) for r in con.execute(
        """SELECT section, name, strftime('%m-%d %H:%M', mastered_at, 'localtime') tl FROM archetypes
           WHERE mastered_at IS NOT NULL AND julianday(mastered_at) >= julianday('now', ?)
           ORDER BY mastered_at""", (span,))]
    misses = [dict(archetype=r["name"], item_id=r["item_id"], seconds=round(r["seconds"]), at_local=r["tl"])
              for r in con.execute(f"""SELECT ar.name, t.item_id, t.seconds,
                    strftime('%m-%d %H:%M', t.ts, 'localtime') tl FROM attempts t
                  JOIN archetypes ar ON ar.id=t.archetype_id
                  WHERE {W} AND t.is_correct=0 ORDER BY t.ts""", (span,))]
    status = {r[0]: r[1] for r in con.execute("SELECT status, COUNT(*) FROM archetypes GROUP BY status")}
    return dict(window_hours=hours,
                attempts=dict(n=base["n"], correct=base["ok"], minutes=round(base["mins"], 1),
                              on_pace=base["fast"]),
                sections=sect, mastered=mastered_rows, misses=misses, archetype_status=status)


def audit_bank(con):
    """Problems that would make drilling unreliable, plus coverage gaps."""
    problems, stems = [], {}
    for i in con.execute("SELECT * FROM items WHERE retired=0"):
        try:
            ch = json.loads(i["choices"] or "[]")
        except json.JSONDecodeError:
            problems.append(dict(item_id=i["id"], issue="choices are not valid JSON")); continue
        if not (2 <= len(ch) <= len(LETTERS)):
            problems.append(dict(item_id=i["id"], issue=f"{len(ch)} choices (need 2-{len(LETTERS)})"))
        if not i["answer"] or i["answer"] not in LETTERS[:len(ch)]:
            problems.append(dict(item_id=i["id"], issue=f"key {i['answer']!r} is not one of the choices"))
        if len({c.strip().lower() for c in ch}) < len(ch):
            problems.append(dict(item_id=i["id"], issue="duplicate choice text"))
        if not (i["stem"] or "").strip():
            problems.append(dict(item_id=i["id"], issue="empty stem"))
        if not (i["explanation"] or "").strip():
            problems.append(dict(item_id=i["id"], issue="no explanation (nothing to learn from on a miss)"))
        key = re.sub(r"\s+", " ", (i["stem"] or "").strip().lower())
        if key in stems:
            problems.append(dict(item_id=i["id"], issue=f"same stem as item {stems[key]}"))
        stems.setdefault(key, i["id"])
    for a in con.execute("SELECT id FROM archetypes"):
        rows = con.execute("SELECT id, stem FROM items WHERE archetype_id=? AND retired=0", (a["id"],)).fetchall()
        for k, r in enumerate(rows):
            if too_similar(r["stem"], [x["stem"] for x in rows[:k]]):
                problems.append(dict(item_id=r["id"], issue="reuses the numbers of an earlier item in its archetype"))
    thin = [dict(id=r["id"], name=r["name"], live_items=r["live_items"]) for r in archetype_table(con)
            if r["status"] == "active" and r["live_items"] < STREAK_N]
    unverified = con.execute("SELECT COUNT(*) FROM items WHERE verified=0 AND retired=0").fetchone()[0]
    burned = con.execute(f"SELECT COUNT(DISTINCT item_id) FROM attempts WHERE item_id IN {BURNED}").fetchone()[0]
    return dict(problems=problems, thin_archetypes=thin, unverified_items=unverified, burned_items=burned,
                total_items=con.execute("SELECT COUNT(*) FROM items").fetchone()[0])


# ---------------------------------------------------------------- web server

def make_handler():
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *a):
            sys.stderr.write("%s %s\n" % (self.command, fmt % a))

        def _send(self, code, body, ctype="application/json"):
            data = body if isinstance(body, bytes) else json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def _guard(self, fn):
            con = db()
            try:
                self._send(200, fn(con))
            except (ValueError, KeyError) as e:
                self._send(400, dict(error=str(e)))
            except Exception as e:   # keep the server up; report the failure to the client
                traceback.print_exc()
                self._send(500, dict(error=f"{type(e).__name__}: {e}"))
            finally:
                con.close()

        def do_GET(self):
            u = urlparse(self.path)
            q = {k: v[0] for k, v in parse_qs(u.query).items()}
            sec = q.get("section") or None
            if u.path in ("/", "/index.html"):
                with open(UI, "rb") as f:
                    return self._send(200, f.read(), "text/html; charset=utf-8")
            if u.path == "/favicon.svg":
                return self._send(200, FAVICON_SVG.encode(), "image/svg+xml")
            routes = {
                "/api/sections": lambda c: sections(c),
                "/api/next": lambda c: pick_next(c, sec, int(q["archetype"]) if q.get("archetype") else None),
                "/api/archetypes": lambda c: archetype_table(c, sec),
                "/api/progress": lambda c: progress_report(c, float(q.get("hours", 24))),
                "/api/sets": lambda c: recent_sets(c),
                "/api/pace": lambda c: pace_report(c, int(q["id"])) or {"error": "set not finished"},
                "/api/audit": lambda c: audit_bank(c),
            }
            if u.path in routes:
                return self._guard(routes[u.path])
            self._send(404, dict(error="not found"))

        def do_POST(self):
            n = int(self.headers.get("Content-Length") or 0)
            try:
                body = json.loads(self.rfile.read(n) or b"{}")
            except json.JSONDecodeError:
                return self._send(400, dict(error="body is not JSON"))
            routes = {
                "/api/answer": lambda c: record_answer(c, int(body["item_id"]), body.get("given"),
                                                       float(body.get("seconds") or 0), body.get("perm"),
                                                       "recheck" if body.get("mode") == "recheck" else "drill"),
                "/api/set/start": lambda c: assemble_set(c, body.get("section") or None,
                                                         max(1, min(50, int(body.get("n", 10)))))
                                            or {"error": "no servable items"},
                "/api/set/finish": lambda c: finish_set(c, int(body["set_id"]), body.get("answers", []),
                                                        body.get("flags", 0), body.get("changes", 0)),
            }
            if self.path in routes:
                return self._guard(routes[self.path])
            self._send(404, dict(error="not found"))
    return Handler


def cmd_serve(args):
    db().close()
    srv = ThreadingHTTPServer((args.host, args.port), make_handler())
    print(f"drill engine on http://{args.host}:{args.port}  (db: {DB})")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


# ---------------------------------------------------------------- CLI

def cmd_status(_args):
    rows = archetype_table(db())
    print(f"{'id':>4}  {'section':<10} {'status':<9} {'streak':>6} {'items':>5} {'acc':>5}  name")
    for r in rows:
        acc = f"{r['correct'] / r['attempts']:.0%}" if r["attempts"] else "-"
        print(f"{r['id']:>4}  {r['section'][:10]:<10} {r['status']:<9} {r['streak']}/{r['need']:<4} "
              f"{r['live_items']:>5} {acc:>5}  {r['name']}")


def cmd_progress(args):
    print(json.dumps(progress_report(db(), args.hours), indent=2))


def cmd_audit(_args):
    rep = audit_bank(db())
    for p in rep["problems"]:
        print(f"item {p['item_id']}: {p['issue']}")
    for t in rep["thin_archetypes"]:
        print(f"archetype {t['id']} ({t['name']}): only {t['live_items']} live items, mastery needs {STREAK_N}")
    print(f"{rep['total_items']} items, {rep['unverified_items']} unverified, {rep['burned_items']} burned, "
          f"{len(rep['problems'])} problems")
    return 1 if rep["problems"] else 0


def cmd_pace(args):
    con = db()
    if args.list or args.set_id is None and not recent_sets(con, 1):
        for s in recent_sets(con):
            print(f"set {s['id']}: {s['started']}  {s['section'] or 'all'}  {s['correct']}/{s['n']}")
        return
    sid = args.set_id or recent_sets(con, 1)[0]["id"]
    print(json.dumps(pace_report(con, sid), indent=2))


def cmd_show(args):
    con = db()
    a = con.execute("SELECT * FROM archetypes WHERE id=?", (args.archetype,)).fetchone()
    if a is None:
        sys.exit(f"no archetype {args.archetype}")
    print(f"[{a['id']}] {a['section']} / {a['name']}  ({a['status']})\n  pattern: {a['pattern']}")
    for i in con.execute("SELECT * FROM items WHERE archetype_id=?", (a["id"],)):
        flag = "verified" if i["verified"] else "UNVERIFIED"
        print(f"\n  item {i['id']} ({flag}{', retired' if i['retired'] else ''}): {i['stem']}")
        for k, c in enumerate(json.loads(i["choices"])):
            print(f"    {LETTERS[k]}. {c}")
        print(f"    key {i['answer']}")


def cmd_verify(args):
    con = db()
    if args.archetype:
        n = con.execute("UPDATE items SET verified=1 WHERE archetype_id=?", (args.archetype,)).rowcount
    else:
        n = con.executemany("UPDATE items SET verified=1 WHERE id=?", [(i,) for i in args.ids]).rowcount
    con.commit()
    print(f"verified {n} items")


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("serve"); s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8780); s.set_defaults(fn=cmd_serve)
    sub.add_parser("status").set_defaults(fn=cmd_status)
    s = sub.add_parser("progress"); s.add_argument("--hours", type=float, default=24.0); s.set_defaults(fn=cmd_progress)
    sub.add_parser("audit").set_defaults(fn=cmd_audit)
    s = sub.add_parser("pace"); s.add_argument("set_id", type=int, nargs="?")
    s.add_argument("--list", action="store_true"); s.set_defaults(fn=cmd_pace)
    s = sub.add_parser("show"); s.add_argument("--archetype", type=int, required=True); s.set_defaults(fn=cmd_show)
    s = sub.add_parser("verify"); s.add_argument("ids", type=int, nargs="*")
    s.add_argument("--archetype", type=int); s.set_defaults(fn=cmd_verify)
    args = p.parse_args(argv)
    sys.exit(args.fn(args) or 0)


if __name__ == "__main__":
    main()
