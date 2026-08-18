#!/usr/bin/env python3
"""End-to-end tests for the study planner.

Runs against the LIVE server, so it exercises the real worker, real SQLite,
and real Claude calls — not a mock. Anything it creates is prefixed [TEST]
and deleted at the end.

    python3 test_planner.py              # full run, makes real API calls (~25c)
    python3 test_planner.py --no-ai      # plumbing only, no API calls, free
    python3 test_planner.py --keep       # leave test data in place to eyeball on the TV
    python3 test_planner.py --url http://192.168.68.53:8000
"""
import argparse
import io
import sys
import time
from datetime import datetime, timedelta, timezone

try:
    import httpx
except ImportError:
    sys.exit("httpx missing. Run:  source ~/planner/.venv/bin/activate")

MARK = "[TEST]"
results = []
created = {"assignments": [], "classes": [], "events": []}


# --------------------------------------------------------------- reporting

class C:
    ok = "\033[92m"; bad = "\033[91m"; warn = "\033[93m"
    dim = "\033[90m"; b = "\033[1m"; off = "\033[0m"


def check(name, condition, detail=""):
    results.append((name, bool(condition), detail))
    tick = f"{C.ok}PASS{C.off}" if condition else f"{C.bad}FAIL{C.off}"
    print(f"  {tick}  {name}")
    if detail and not condition:
        print(f"        {C.dim}{detail}{C.off}")
    return bool(condition)


def section(title):
    print(f"\n{C.b}{title}{C.off}")


def note(msg):
    print(f"  {C.dim}{msg}{C.off}")


# ------------------------------------------------------------ PDF building

def make_pdf(lines: list[str]) -> bytes:
    """Minimal single-page PDF. No dependencies."""
    def esc(s):
        return s.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")

    body = "BT\n/F1 11 Tf\n50 740 Td\n15 TL\n"
    body += "".join(f"({esc(l)}) Tj T*\n" for l in lines)
    body += "ET"
    stream = body.encode("latin-1", "replace")

    objs = [
        b"<</Type/Catalog/Pages 2 0 R>>",
        b"<</Type/Pages/Kids[3 0 R]/Count 1>>",
        b"<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]"
        b"/Contents 4 0 R/Resources<</Font<</F1 5 0 R>>>>>>",
        b"<</Length %d>>\nstream\n" % len(stream) + stream + b"\nendstream",
        b"<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, o in enumerate(objs, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i + o + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    for off in offsets:
        out += b"%010d 00000 n \n" % off
    out += b"trailer\n<</Size %d/Root 1 0 R>>\nstartxref\n%d\n%%%%EOF\n" % (
        len(objs) + 1, xref)
    return bytes(out)


GOOD_PDF = make_pdf([
    "ENGL 200 - Constructions of Crime",
    "Assignment 3: Literary Analysis Essay",
    "",
    "Due: 11:59pm, Friday 4 September 2026",
    "Weighting: 25% of final grade",
    "",
    "Write a 1500-word analytical essay on Patricia Highsmith's",
    "The Talented Mr. Ripley. Your essay must argue a specific",
    "thesis about how the novel uses crime to explore identity.",
    "",
    "Requirements:",
    "- 1500 words, plus or minus 10%",
    "- MLA citation style, works cited page not counted",
    "- Minimum four direct quotations from the primary text",
    "- At least two peer-reviewed secondary sources",
    "- Double spaced, 12pt Times New Roman",
    "",
    "Submit as PDF via Blackboard. Late work loses 5% per day.",
])

VAGUE_PDF = make_pdf([
    "PSYC 360 - Reading Response",
    "Write a short response to this week's reading.",
    "Due at the end of week 4. See Blackboard for details.",
])

BLANK_PDF = make_pdf([])


# ----------------------------------------------------------------- helpers

def iso(**kw):
    return (datetime.now(timezone.utc) + timedelta(**kw)).isoformat()


def wait_for_job(client, aid, timeout=120):
    """Poll until the assignment's job chain settles."""
    start = time.time()
    last = None
    while time.time() - start < timeout:
        a = client.get(f"/api/assignments/{aid}").json()
        job = a.get("job")
        last = job
        if not job:
            return a, None
        if job["status"] == "done" and (job["kind"] == "decompose" or a["subtasks"]):
            return a, job
        if job["status"] == "failed":
            return a, job
        time.sleep(2)
    return client.get(f"/api/assignments/{aid}").json(), last


def new_assignment(client, **body):
    body.setdefault("title", f"{MARK} untitled")
    r = client.post("/api/assignments", json=body)
    aid = r.json()["id"]
    created["assignments"].append(aid)
    return aid


def upload(client, name, data, class_id=None):
    files = {"file": (name, io.BytesIO(data), "application/pdf")}
    form = {"class_id": str(class_id)} if class_id else {}
    r = client.post("/api/assignments/upload", files=files, data=form)
    if r.status_code == 201:
        created["assignments"].append(r.json()["id"])
    return r


# ------------------------------------------------------------------- tests

def t_health(client):
    section("0. Server reachable")
    up = False
    for attempt in range(15):
        try:
            up = client.get("/api/health").status_code == 200
            if up:
                break
        except Exception:
            pass
        if attempt == 0:
            note("waiting for the server to bind…")
        time.sleep(1)
    if not check("/api/health responds", up):
        sys.exit(f"\n{C.bad}Server not reachable. Check: systemctl status planner{C.off}")
    check("/ serves the tablet app", client.get("/").status_code == 200)
    check("/display serves the TV view", client.get("/display").status_code == 200)
    check("/static/manifest.json present", client.get("/static/manifest.json").status_code == 200)


def t_display_contract(client):
    """The exact fields display.html reads. Breaking these breaks the TV silently."""
    section("1. Display data contract")
    d = client.get("/api/now").json()
    for key in ("step", "needs_breakdown", "planning"):
        check(f"/api/now has '{key}'", key in d)
    a = client.get("/api/agenda?limit=4").json()
    check("/api/agenda returns a list", isinstance(a, list))
    if a:
        for key in ("title", "due_at", "class_code", "done_n", "total_n"):
            check(f"agenda item has '{key}'", key in a[0])


def t_manual_only(client, ai):
    section("2. Manual entry — title and due date only, no upload")
    aid = new_assignment(client, title=f"{MARK} Ripley essay", due_at=iso(days=6))
    a = client.get(f"/api/assignments/{aid}").json()
    check("assignment created", a["title"].endswith("Ripley essay"))
    check("auto-confirmed (no PDF to review)", a["confirmed"] == 1)
    check("decompose job queued automatically", a["job"] and a["job"]["kind"] == "decompose")

    if not ai:
        note("skipping step generation (--no-ai)")
        return aid

    a, job = wait_for_job(client, aid)
    if not check("planning completed", job and job["status"] == "done",
                 str(job and job.get("last_error"))[:200]):
        return aid

    steps = a["subtasks"]
    check("steps generated", len(steps) >= 3, f"got {len(steps)}")
    if steps:
        first = steps[0]
        check("step 1 is short (activation energy)", first["est_minutes"] <= 15,
              f"{first['est_minutes']} min: {first['title']}")
        check("all steps 5-180 min",
              all(5 <= s["est_minutes"] <= 180 for s in steps))
        check("steps are ordered", [s["seq"] for s in steps] == sorted(s["seq"] for s in steps))
        vague = [s["title"] for s in steps
                 if s["title"].lower().strip() in
                 ("research", "study", "write the essay", "work on the essay")]
        check("no vague steps", not vague, f"vague: {vague}")
        note(f"step 1: {first['title']} ({first['est_minutes']} min)")
        note(f"total planned: {sum(s['est_minutes'] for s in steps)} min across {len(steps)} steps")
    return aid


def t_good_pdf(client, ai):
    section("3. PDF upload — well-formed assignment sheet")
    r = upload(client, "engl200_essay.pdf", GOOD_PDF)
    if not check("upload accepted", r.status_code == 201, r.text[:200]):
        return
    aid = r.json()["id"]
    a = client.get(f"/api/assignments/{aid}").json()
    check("held for review (confirmed=0)", a["confirmed"] == 0)
    check("extract job queued", a["job"] and a["job"]["kind"] == "extract")

    if not ai:
        note("skipping extraction (--no-ai)")
        return

    a, job = wait_for_job(client, aid, timeout=180)
    if not check("extract + decompose completed", job and job["status"] == "done",
                 str(job and job.get("last_error"))[:200]):
        return
    check("title extracted, not the filename", "engl200_essay" not in a["title"].lower(),
          a["title"])
    check("due date extracted", a["due_at"] is not None)
    if a["due_at"]:
        # Stored as UTC. The sheet says 11:59pm local, so convert back before
        # comparing — 4 Sept 23:59 EDT is 5 Sept 03:59 UTC.
        due = datetime.fromisoformat(a["due_at"]).astimezone()
        check("due date is 4 Sept 2026 local",
              (due.year, due.month, due.day) == (2026, 9, 4),
              f"{a['due_at']} -> local {due.isoformat()}")
        check("due time is 11:59pm local", (due.hour, due.minute) == (23, 59),
              due.strftime("%H:%M"))
    desc = (a["description"] or "").lower()
    check("requirements captured (word count)", "1500" in desc, desc[:150])
    check("requirements captured (MLA)", "mla" in desc, desc[:150])
    check("kind = deliverable", a["kind"] == "deliverable")
    check("steps generated from PDF", len(a["subtasks"]) >= 3)
    if a["subtasks"]:
        note(f"step 1: {a['subtasks'][0]['title']} ({a['subtasks'][0]['est_minutes']} min)")


def t_blank_pdf(client, ai):
    section("4. Blank PDF — nothing to extract")
    r = upload(client, "blank.pdf", BLANK_PDF)
    if not check("upload accepted (no crash)", r.status_code == 201, r.text[:200]):
        return
    aid = r.json()["id"]
    if not ai:
        note("skipping extraction (--no-ai)")
        return
    a, job = wait_for_job(client, aid, timeout=180)
    check("job settled rather than hanging", job and job["status"] in ("done", "failed"),
          str(job))
    check("assignment still exists and is reviewable",
          client.get(f"/api/assignments/{aid}").status_code == 200)
    if job and job["status"] == "done":
        check("no fabricated due date", a["due_at"] is None, a["due_at"])
        note(f"fell back to title: {a['title']!r}")
    else:
        note(f"failed cleanly: {str(job.get('last_error'))[:120]}")


def t_vague_pdf(client, ai):
    section("5. PDF with a vague due date")
    r = upload(client, "vague.pdf", VAGUE_PDF)
    if not check("upload accepted", r.status_code == 201):
        return
    if not ai:
        note("skipping (--no-ai)")
        return
    aid = r.json()["id"]
    a, job = wait_for_job(client, aid, timeout=180)
    if job and job["status"] == "done":
        check("did not invent a due date", a["due_at"] is None, a["due_at"])
        note("correctly left the date blank for you to fill in")


def t_exam(client, ai):
    section("6. Exam — should produce study sessions, not production steps")
    aid = new_assignment(client, title=f"{MARK} PSYC 360 midterm",
                         kind="exam", due_at=iso(days=8),
                         description="Covers chapters 8-12: caregiving, grandparenting, "
                                     "retirement, socioemotional selectivity, end of life.")
    a = client.get(f"/api/assignments/{aid}").json()
    check("kind stored as exam", a["kind"] == "exam")
    if not ai:
        note("skipping (--no-ai)")
        return
    a, job = wait_for_job(client, aid)
    if not check("planning completed", job and job["status"] == "done",
                 str(job and job.get("last_error"))[:200]):
        return
    steps = a["subtasks"]
    check("study sessions generated", len(steps) >= 3, f"got {len(steps)}")
    with_qs = [s for s in steps if s["detail"] and len(s["detail"]) > 80]
    check("sessions include practice questions in detail",
          len(with_qs) >= max(1, len(steps) // 2),
          f"{len(with_qs)}/{len(steps)} sessions have substantial detail")
    lazy = [s["title"] for s in steps if "review your notes" in s["title"].lower()]
    check("no 'review your notes' filler", not lazy, str(lazy))
    if with_qs:
        note(f"sample detail: {with_qs[0]['detail'][:110]}...")


def t_pressure(client):
    section("7. Pressure ranking — workload beats raw deadline")
    # Nearer deadline, trivial work.
    near = new_assignment(client, title=f"{MARK} Small quiz prep",
                          due_at=iso(hours=20), auto_plan=False)
    client.post(f"/api/assignments/{near}/subtasks",
                json=[{"title": "Skim the study guide", "est_minutes": 20}])
    # Later deadline, heavy work.
    far = new_assignment(client, title=f"{MARK} Big essay",
                         due_at=iso(hours=70), auto_plan=False)
    client.post(f"/api/assignments/{far}/subtasks", json=[
        {"title": "Read the prompt", "est_minutes": 10},
        {"title": "Draft", "est_minutes": 600},
        {"title": "Revise", "est_minutes": 400},
    ])
    d = client.get("/api/now").json()
    step = d.get("step") or {}
    check("picks the heavier assignment, not the nearer deadline",
          step.get("assignment", "").endswith("Big essay"),
          f"picked: {step.get('assignment')}")
    note("20 min due in 20h vs 17h due in 70h — pressure ranks the second higher")
    for x in (near, far):
        client.delete(f"/api/assignments/{x}")
        created["assignments"].remove(x)


def t_infeasible(client):
    section("8. Infeasible workload — the warning that matters")
    aid = new_assignment(client, title=f"{MARK} Impossible paper",
                         due_at=iso(hours=3), auto_plan=False)
    client.post(f"/api/assignments/{aid}/subtasks", json=[
        {"title": "Read sources", "est_minutes": 300},
        {"title": "Write", "est_minutes": 600},
    ])
    d = client.get("/api/now").json()
    check("flagged infeasible", d.get("infeasible") is True,
          f"infeasible={d.get('infeasible')} remaining={d.get('remaining_min')}")
    check("remaining_min exposed for the display", d.get("remaining_min", 0) > 0)
    note("15h of work, 3h available — display shows 'something has to give'")
    # This one outranks everything by design. Remove it before later tests.
    client.delete(f"/api/assignments/{aid}")
    created["assignments"].remove(aid)


def t_overdue(client):
    section("9. Overdue assignment")
    aid = new_assignment(client, title=f"{MARK} Late homework",
                         due_at=iso(days=-2), auto_plan=False)
    client.post(f"/api/assignments/{aid}/subtasks",
                json=[{"title": "Finish it", "est_minutes": 45}])
    d = client.get("/api/now").json()
    check("overdue wins the ranking", d.get("overdue") is True, str(d.get("overdue")))
    check("overdue item is the one surfaced",
          (d.get("step") or {}).get("assignment", "").endswith("Late homework"))
    # Overdue always sorts first. Remove it so later tests can see their own data.
    client.delete(f"/api/assignments/{aid}")
    created["assignments"].remove(aid)


def t_lifecycle(client):
    section("10. Step lifecycle — done, snooze, auto-close")
    aid = new_assignment(client, title=f"{MARK} Lifecycle check",
                         due_at=iso(hours=5), auto_plan=False)
    client.post(f"/api/assignments/{aid}/subtasks", json=[
        {"title": "Step one", "est_minutes": 10},
        {"title": "Step two", "est_minutes": 20},
    ])
    a = client.get(f"/api/assignments/{aid}").json()
    s1, s2 = a["subtasks"][0]["id"], a["subtasks"][1]["id"]

    client.patch(f"/api/subtasks/{s1}", json={"action": "done"})
    a = client.get(f"/api/assignments/{aid}").json()
    check("marking done updates status", a["subtasks"][0]["status"] == "done")
    check("completed_at recorded", a["subtasks"][0]["completed_at"] is not None)
    check("assignment still open", a["status"] == "todo")

    client.patch(f"/api/subtasks/{s2}", json={"action": "snooze", "snooze_minutes": 120})
    a = client.get(f"/api/assignments/{aid}").json()
    check("snooze sets snoozed_until", a["subtasks"][1]["snoozed_until"] is not None)

    client.patch(f"/api/subtasks/{s2}", json={"action": "undo"})
    client.patch(f"/api/subtasks/{s2}", json={"action": "done"})
    a = client.get(f"/api/assignments/{aid}").json()
    check("assignment auto-closes when all steps done", a["status"] == "done")

    client.patch(f"/api/subtasks/{s2}", json={"action": "undo"})
    a = client.get(f"/api/assignments/{aid}").json()
    check("reopening a step reopens the assignment", a["status"] == "todo")
    return aid


def t_needs_breakdown(client):
    section("11. Assignment with no steps is surfaced, not lost")
    aid = new_assignment(client, title=f"{MARK} Orphan assignment",
                         due_at=iso(days=3), auto_plan=False)
    d = client.get("/api/now").json()
    titles = [x["title"] for x in d.get("needs_breakdown", [])]
    check("appears in needs_breakdown", any(t.endswith("Orphan assignment") for t in titles),
          str(titles))
    return aid


def t_recurring(client):
    section("12. Weekly recurring events")
    cid = client.post("/api/classes", json={
        "code": f"{MARK} PSYC 360", "name": "Aging", "color": "#6B7FD7"}).json()["id"]
    created["classes"].append(cid)

    start = datetime.now(timezone.utc).replace(hour=13, minute=30, second=0, microsecond=0)
    while start.weekday() != 0:                       # next Monday
        start += timedelta(days=1)
    until = start + timedelta(days=21)

    r = client.post("/api/events", json={
        "title": f"{MARK} Lecture", "class_id": cid,
        "start_at": start.isoformat(),
        "end_at": (start + timedelta(minutes=75)).isoformat(),
        "repeat_days": "1,3", "repeat_until": until.isoformat()})
    check("recurring event created", r.status_code == 201)
    created["events"].append(r.json()["id"])

    occ = client.get("/api/events", params={
        "start": start.isoformat(), "end": until.isoformat()}).json()
    mine = [o for o in occ if o["title"].startswith(MARK)]
    check("expands to Mon+Wed occurrences", len(mine) == 7, f"got {len(mine)}")
    weekdays = {datetime.fromisoformat(o["occurrence_start"]).weekday() for o in mine}
    check("occurrences fall on Mon and Wed only", weekdays <= {0, 2}, str(weekdays))
    times = {datetime.fromisoformat(o["occurrence_start"]).strftime("%H:%M") for o in mine}
    check("time of day preserved across occurrences", len(times) == 1, str(times))
    check("stored as a single row",
          len([e for e in client.get("/api/events/series").json()
               if e["title"].startswith(MARK)]) == 1)
    note(f"{len(mine)} occurrences from 1 database row")

    one = client.post("/api/events", json={
        "title": f"{MARK} One-off", "start_at": (start + timedelta(days=1)).isoformat()})
    created["events"].append(one.json()["id"])
    occ2 = client.get("/api/events", params={
        "start": start.isoformat(), "end": until.isoformat()}).json()
    check("one-off events still work",
          len([o for o in occ2 if o["title"].startswith(MARK)]) == 8)


def t_bad_input(client):
    section("13. Bad input handling")
    r = client.post("/api/assignments/upload",
                    files={"file": ("notes.txt", io.BytesIO(b"hello"), "text/plain")})
    check("non-PDF rejected with 400", r.status_code == 400, f"got {r.status_code}")
    check("missing assignment returns 404",
          client.get("/api/assignments/999999").status_code == 404)
    check("missing subtask returns 404",
          client.patch("/api/subtasks/999999", json={"action": "done"}).status_code == 404)
    r = client.post("/api/assignments", json={"due_at": iso(days=1)})
    check("assignment without a title rejected", r.status_code == 422, f"got {r.status_code}")


def t_display_reflects(client):
    section("14. Display reflects state changes")
    # Clear anything this run left open so /api/now can only pick our fixture.
    for a in client.get("/api/assignments?status=todo").json():
        if a["title"].startswith(MARK):
            client.delete(f"/api/assignments/{a['id']}")
            if a["id"] in created["assignments"]:
                created["assignments"].remove(a["id"])
    aid = new_assignment(client, title=f"{MARK} Display check",
                         due_at=iso(hours=4), auto_plan=False)
    client.post(f"/api/assignments/{aid}/subtasks", json=[
        {"title": "Visible step alpha", "est_minutes": 15},
        {"title": "Visible step beta", "est_minutes": 25}])

    d = client.get("/api/now").json()
    step = d.get("step") or {}
    shown = step.get("title") == "Visible step alpha"
    check("first step surfaces on /api/now", shown, f"showing: {step.get('title')}")
    if shown:
        check("progress counter correct", step["done_n"] == 0 and step["total_n"] == 2,
              f"{step.get('done_n')}/{step.get('total_n')}")
        client.patch(f"/api/subtasks/{step['subtask_id']}", json={"action": "done"})
        d2 = client.get("/api/now").json()
        check("display advances to the next step",
              (d2.get("step") or {}).get("title") == "Visible step beta",
              f"now showing: {(d2.get('step') or {}).get('title')}")
        check("progress counter increments",
              (d2.get("step") or {}).get("done_n") == 1)
    return aid


# -------------------------------------------------------------- entrypoint

def cleanup(client):
    """Best effort — never let cleanup mask a real test failure."""
    try:
        _cleanup(client)
    except Exception as e:
        print(f"  {C.warn}cleanup incomplete: {e}{C.off}")


def _cleanup(client):
    for aid in created["assignments"]:
        client.delete(f"/api/assignments/{aid}")
    for eid in created["events"]:
        client.delete(f"/api/events/{eid}")
    for cid in created["classes"]:
        client.delete(f"/api/classes/{cid}")
    leftovers = [a for a in client.get("/api/assignments").json()
                 if a["title"].startswith(MARK)]
    for a in leftovers:
        client.delete(f"/api/assignments/{a['id']}")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--url", default="http://localhost:8000")
    p.add_argument("--no-ai", action="store_true", help="skip real Claude calls")
    p.add_argument("--keep", action="store_true", help="leave test data in place")
    args = p.parse_args()
    ai = not args.no_ai

    print(f"{C.b}Planner end-to-end tests{C.off}")
    print(f"{C.dim}target: {args.url}   claude calls: {'yes' if ai else 'no'}{C.off}")

    client = httpx.Client(base_url=args.url, timeout=30.0)
    try:
        t_health(client)
        t_display_contract(client)
        t_manual_only(client, ai)
        t_good_pdf(client, ai)
        t_blank_pdf(client, ai)
        t_vague_pdf(client, ai)
        t_exam(client, ai)
        t_pressure(client)
        t_infeasible(client)
        t_overdue(client)
        t_lifecycle(client)
        t_needs_breakdown(client)
        t_recurring(client)
        t_bad_input(client)
        t_display_reflects(client)
    finally:
        if args.keep:
            print(f"\n{C.warn}--keep set: test data left in place. "
                  f"Re-run without --keep to remove it.{C.off}")
        else:
            print(f"\n{C.dim}cleaning up…{C.off}")
            cleanup(client)
        client.close()

    passed = sum(1 for _, ok, _ in results if ok)
    total = len(results)
    print(f"\n{C.b}{'─' * 46}{C.off}")
    print(f"{C.b}{passed}/{total} passed{C.off}")
    fails = [(n, d) for n, ok, d in results if not ok]
    if fails:
        print(f"\n{C.bad}Failures:{C.off}")
        for n, d in fails:
            print(f"  · {n}")
            if d:
                print(f"    {C.dim}{d[:180]}{C.off}")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
