"""A fake MCQ Test System, for tests and local mode only (address "fake://demo").

It answers the same actions as appsscript/Api.gs, from data kept in memory, so the SkillOps
pages can be tried without Google. "fake://down" behaves like a system that does not answer.
"""
import threading
import time

SECRET = "local-fake-secret-0123456789"
_lock = threading.Lock()
_state = {}


def reset():
    with _lock:
        _state.clear()


def _seed():
    now = int(time.time() * 1000)
    students = [{"email": f"{c.lower()}{n}@student.test", "name": f"Student {c}{n}", "batch": c, "active": True}
                for c in ("A", "B", "C") for n in range(1, 6)]
    tests = [{"id": "W01", "title": "W01 - SQL basics", "active": False, "duration": 30, "questions": 20, "batches": []},
             {"id": "W02", "title": "W02 - Python data", "active": True, "duration": 25, "questions": 15, "batches": ["A", "B"]}]
    results = {"W01": [{"email": s["email"], "name": s["name"], "score": 12 + i % 7, "max": 20,
                        "percentage": round((12 + i % 7) * 5, 1), "submittedAt": now - 86400000 * 3, "type": "SUBMITTED",
                        "sessionId": f"S{i}", "earlier": False} for i, s in enumerate(students) if i % 5 != 4]}
    sessions = [{"id": f"L{i}", "email": s["email"], "name": s["name"], "status": "IN PROGRESS", "answered": 3 + i,
                 "total": 15, "warnings": i % 3, "lastSeenSec": 5 + 20 * i, "remainingSec": 900 - 30 * i, "score": "",
                 "offline": i == 3} for i, s in enumerate([x for x in students if x["batch"] == "A"][:4])]
    return {"students": students, "tests": tests, "results": results, "sessions": sessions, "idem": {}}


def _st():
    if not _state:
        _state.update(_seed())
    return _state


def _batches(args):
    return [str(b).strip().upper() for b in args.get("batches") or [] if str(b).strip()]


def _roster(args):
    bs = _batches(args)
    return {s["email"]: s for s in _st()["students"] if not bs or s["batch"].upper() in bs}


def handle(url, payload):
    import json
    if url.startswith("fake://down"):
        raise OSError("fake system is down")
    if payload.get("secret") != SECRET:
        return json.dumps({"ok": False, "code": "bad_secret", "message": "Wrong or missing secret."})
    with _lock:
        st = _st()
        idem = payload.get("idem")
        if idem and idem in st["idem"]:
            return st["idem"][idem]
        try:
            out = {"ok": True, "result": ACTIONS[payload["action"]](payload.get("args") or {})}
        except KeyError as e:
            out = {"ok": False, "code": "error", "message": f"Unknown: {e}"}
        except ValueError as e:
            out = {"ok": False, "code": "error", "message": str(e)}
        text = json.dumps(out)
        if idem and out["ok"]:
            st["idem"][idem] = text
        return text


def _test(tid):
    t = next((t for t in _st()["tests"] if t["id"].upper() == str(tid).upper()), None)
    if not t:
        raise ValueError("There is no test with that ID.")
    return t


def a_ping(args):
    return {"institute": "Fake MCQ system", "method": "PIN", "tests": len(_st()["tests"]), "emailQuota": 100,
            "sheet": "Fake sheet", "now": int(time.time() * 1000)}


def a_list(args):
    bs = _batches(args)
    return [dict(t) for t in _st()["tests"] if not bs or not t["batches"] or set(t["batches"]) & set(bs)]


def _check_test(t, args):
    bs = _batches(args)
    if not bs:
        return
    if not t["batches"]:
        raise ValueError(f"Test {t['id']} is shared by all batches. Ask your supervisor to change it.")
    if not set(t["batches"]) & set(bs):
        raise ValueError(f"Test {t['id']} is not for your batch.")


def a_active(args):
    t = _test(args.get("testId"))
    _check_test(t, args)
    t["active"] = bool(args.get("active"))
    return {"testId": t["id"], "active": t["active"]}


def a_monitor(args):
    roster = _roster(args)
    sessions = [dict(s) for s in _st()["sessions"] if s["email"] in roster]
    started = {s["email"] for s in sessions}
    return {"testId": args.get("testId"), "tests": [{"id": t["id"], "title": t["title"], "active": t["active"]} for t in _st()["tests"]],
            "sessions": sessions, "events": [], "codes": [], "method": "PIN", "emailQuota": 100,
            "serverNow": int(time.time() * 1000),
            "notStarted": [{"email": e, "name": s["name"]} for e, s in roster.items() if e not in started]}


def _session(args):
    s = next((x for x in _st()["sessions"] if x["id"] == args.get("sessionId")), None)
    if not s:
        raise ValueError("That attempt was not found.")
    if s["email"] not in _roster(args):
        raise ValueError("That student is not in your batch.")
    if s["status"] != "IN PROGRESS":
        raise ValueError("This student is not writing the test now.")
    return s


def a_add(args):
    s = _session(args)
    s["remainingSec"] += 60 * max(1, min(60, int(args.get("minutes") or 5)))
    return {"done": True}


def a_end(args):
    s = _session(args)
    s["status"] = "ENDED BY TRAINER"
    s["score"] = 9
    return {"done": True}


def a_results(args):
    roster = _roster(args)
    rows = [dict(r, batch=roster[r["email"]]["batch"]) for r in _st()["results"].get(str(args.get("testId")).upper(), [])
            if r["email"] in roster]
    return {"testId": str(args.get("testId")).upper(), "rows": rows}


def a_summary(args):
    roster = _roster(args)
    tests = []
    for tid, rows in _st()["results"].items():
        rows = [r for r in rows if r["email"] in roster and not r.get("earlier")]
        if rows:
            tests.append({"id": tid, "title": _test(tid)["title"], "written": len(rows),
                          "average": round(sum(r["percentage"] for r in rows) / len(rows), 1),
                          "last": max(r["submittedAt"] for r in rows)})
    tests.sort(key=lambda t: -t["last"])
    return {"tests": tests, "latest": tests[0] if tests else None,
            "open": [{"id": t["id"], "title": t["title"]} for t in a_list(args) if t["active"]]}


def a_batch_summaries(args):
    return {b: a_summary({"batches": [b]}) for b in _batches(args)}


def a_register(args):
    roster = _roster(args)
    tids = sorted(_st()["results"])
    header = ["Name", "Email", "Batch"] + [f"{t} (out of 20)" for t in tids] + ["Tests Written", "Average %"]
    rows = []
    for e, s in sorted(roster.items(), key=lambda kv: (kv[1]["batch"], kv[1]["name"])):
        marks = {t: next((r for r in _st()["results"][t] if r["email"] == e), None) for t in tids}
        written = [m for m in marks.values() if m]
        rows.append([s["name"], e, s["batch"]] + [m["score"] if m else "ABSENT" for m in marks.values()] +
                    [len(written), round(sum(m["percentage"] for m in written) / len(written), 1) if written else ""])
    return {"header": header, "rows": rows}


def a_import(args):
    csv = str(args.get("csv") or "").strip().splitlines()
    if len(csv) < 2:
        raise ValueError("Need a heading row and at least one data row.")
    tid = str(args.get("testId") or "").upper()
    t = next((t for t in _st()["tests"] if t["id"] == tid), None)
    if t:
        _check_test(t, args)
    else:
        t = {"id": tid, "title": f"{tid} - Weekly Test", "active": False, "duration": 30, "questions": 0,
             "batches": _batches(args)}
        _st()["tests"].append(t)
    if args.get("mode") == "replace":
        t["questions"] = 0
    t["questions"] += len(csv) - 1
    return {"ok": True, "added": len(csv) - 1, "removed": 0, "total": t["questions"], "testId": tid}


def a_reattempt(args):
    _test(args.get("testId"))
    if str(args.get("email", "")).lower() not in _roster(args):
        raise ValueError("That student is not in your batch.")
    return {"allowed": True, "attempts": 1}


def a_regrade(args):
    _test(args.get("testId"))
    return {"done": True, "message": "Re-marked 12 attempt(s). Scores changed for 0 student(s)."}


def a_links(args):
    return {"studentUrl": "https://script.google.com/macros/s/FAKE/exec",
            "monitorUrl": "https://script.google.com/macros/s/FAKE/exec?page=monitor&key=fakekey"}


ACTIONS = {"ping": a_ping, "listTests": a_list, "setTestActive": a_active, "getMonitor": a_monitor, "addTime": a_add,
           "endTest": a_end, "results": a_results, "summary": a_summary, "batchSummaries": a_batch_summaries, "marksRegister": a_register,
           "importQuestions": a_import, "allowReattempt": a_reattempt, "regradeTest": a_regrade, "links": a_links}
