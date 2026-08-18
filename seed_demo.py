#!/usr/bin/env python3
"""Populate a realistic week so you can watch the TV display behave.

    python3 seed_demo.py              # classes, recurring lectures, assignments
    python3 seed_demo.py --ai         # let Claude plan the steps (~10c)
    python3 seed_demo.py --stress     # add an impossible deadline + gated work
    python3 seed_demo.py --clear      # remove everything it created

Everything is tagged [DEMO] so --clear can find it. Your real data is untouched.
"""
import argparse
import sys
import time
from datetime import datetime, timedelta, timezone

try:
    import httpx
except ImportError:
    sys.exit("httpx missing. Run: source ~/planner/.venv/bin/activate")

TAG = "[DEMO]"


class C:
    ok = "\033[92m"; dim = "\033[90m"; b = "\033[1m"
    warn = "\033[93m"; off = "\033[0m"


def local_today():
    return datetime.now().astimezone().replace(
        hour=0, minute=0, second=0, microsecond=0)


def at(day_offset, hour, minute=0):
    d = local_today() + timedelta(days=day_offset)
    return d.replace(hour=hour, minute=minute).astimezone(timezone.utc).isoformat()


def clear(c):
    n = 0
    for a in c.get("/api/assignments").json():
        if a["title"].startswith(TAG):
            c.delete(f"/api/assignments/{a['id']}"); n += 1
    for e in c.get("/api/events/series").json():
        if e["title"].startswith(TAG):
            c.delete(f"/api/events/{e['id']}"); n += 1
    for cl in c.get("/api/classes").json():
        if cl["code"].startswith(TAG):
            c.delete(f"/api/classes/{cl['id']}"); n += 1
    print(f"{C.ok}removed {n} demo records{C.off}")


def seed(c, use_ai, stress):
    print(f"{C.b}Seeding demo data{C.off}")

    classes = {}
    for code, name, colour in [
        ("ENGL 200", "Constructions of Crime", "#C2565B"),
        ("PSYC 360", "Adult Development and Aging", "#6B7FD7"),
        ("CSCI 301", "Data Structures", "#5FA8A0"),
    ]:
        cid = c.post("/api/classes", json={
            "code": f"{TAG} {code}", "name": name, "color": colour,
            "term": "2026 Fall"}).json()["id"]
        classes[code] = cid
        print(f"  class   {code}")

    # Recurring lectures and a job. 0=Sunday.
    until = at(28, 23)
    for title, cid, days, h, m, dur, loc in [
        ("ENGL 200 seminar", classes["ENGL 200"], "2,4", 9, 30, 75, "Tucker 220"),
        ("PSYC 360 lecture", classes["PSYC 360"], "1,3,5", 11, 0, 50, "Millington 150"),
        ("CSCI 301 lab", classes["CSCI 301"], "3", 14, 0, 110, "McGlothlin 201"),
        ("Shift at the rec center", None, "2,4", 17, 0, 240, None),
    ]:
        c.post("/api/events", json={
            "title": f"{TAG} {title}", "class_id": cid,
            "start_at": at(0, h, m),
            "end_at": at(0, h + dur // 60, (m + dur % 60) % 60),
            "location": loc, "repeat_days": days, "repeat_until": until})
        print(f"  event   {title}  ({days})")

    def mk(title, code, days_out, kind, steps, desc=None):
        aid = c.post("/api/assignments", json={
            "title": f"{TAG} {title}", "class_id": classes[code], "kind": kind,
            "due_at": at(days_out, 23, 59), "description": desc,
            "auto_plan": use_ai}).json()["id"]
        if not use_ai:
            c.post(f"/api/assignments/{aid}/subtasks", json=steps)
        print(f"  work    {title}  (due +{days_out}d, {len(steps)} steps)")
        return aid

    mk("Ripley essay", "ENGL 200", 3, "deliverable", [
        {"title": "Open the prompt, read it twice, highlight every deliverable",
         "est_minutes": 10},
        {"title": "Reread ch. 4-6 and mark every passage about identity",
         "est_minutes": 45},
        {"title": "Sort marked quotes into three thematic groups", "est_minutes": 30},
        {"title": "Draft a one-paragraph thesis", "est_minutes": 25},
        {"title": "Outline the body sections", "est_minutes": 30},
        {"title": "Draft the essay", "est_minutes": 90},
        {"title": "Revise for argument and citations", "est_minutes": 60},
    ], desc="1500 words on The Talented Mr. Ripley. MLA. Four primary quotations.")

    mk("Psyc 360 midterm", "PSYC 360", 7, "exam", [
        {"title": "List every topic on the exam from the study guide",
         "est_minutes": 10},
        {"title": "Session 1: caregiving and the sandwich generation",
         "est_minutes": 50,
         "detail": "Close the book, then answer from memory:\n"
                   "1. Define caregiver burden and name two predictors.\n"
                   "2. How does filial obligation vary across cultures?\n"
                   "3. What distinguishes companionate from involved grandparenting?\n"
                   "4. Name two protective factors against caregiver strain.\n"
                   "5. Why is the sandwich generation growing?"},
        {"title": "Session 2: retirement and socioemotional selectivity",
         "est_minutes": 50,
         "detail": "1. State socioemotional selectivity theory in one sentence.\n"
                   "2. Why do older adults prune their social networks?\n"
                   "3. Contrast bridge employment with phased retirement.\n"
                   "4. What predicts retirement satisfaction?\n"
                   "5. Define the positivity effect and give an example."},
        {"title": "Session 3: end of life and optimal aging", "est_minutes": 50,
         "detail": "1. Distinguish palliative from hospice care.\n"
                   "2. What is a good death, per the literature?\n"
                   "3. Summarise the SOC model of optimal aging.\n"
                   "4. What does the paradox of well-being describe?\n"
                   "5. Name two critiques of stage models of grief."},
        {"title": "Session 4: mixed practice across all topics", "est_minutes": 45},
    ], desc="Covers chapters 8-12.")

    mk("Binary tree problem set", "CSCI 301", 5, "deliverable", [
        {"title": "Read the spec and run the provided test file", "est_minutes": 10},
        {"title": "Implement insert and search", "est_minutes": 50},
        {"title": "Implement delete, including the two-child case", "est_minutes": 60},
        {"title": "Write tests for the edge cases", "est_minutes": 40},
    ])

    if stress:
        print(f"\n{C.warn}stress cases{C.off}")
        c.post("/api/assignments", json={
            "title": f"{TAG} Impossible paper", "class_id": classes["ENGL 200"],
            "due_at": at(1, 12), "auto_plan": False})
        aid = c.get("/api/assignments").json()
        aid = [a for a in aid if a["title"].endswith("Impossible paper")][0]["id"]
        c.post(f"/api/assignments/{aid}/subtasks", json=[
            {"title": "Read six critical essays", "est_minutes": 300},
            {"title": "Write 4000 words", "est_minutes": 480}])
        print("  13h of work due tomorrow — expect the red warning")

        aid2 = c.post("/api/assignments", json={
            "title": f"{TAG} Gated response", "class_id": classes["PSYC 360"],
            "due_at": at(6, 23), "auto_plan": False}).json()["id"]
        gate = at(3, 8)
        c.post(f"/api/assignments/{aid2}/subtasks", json=[
            {"title": "Skim chapters already covered", "est_minutes": 30}])
        import sqlite3, os
        from pathlib import Path
        dbp = Path(os.path.expanduser("~/planner/data/planner.db"))
        if dbp.exists():
            conn = sqlite3.connect(dbp)
            conn.execute("INSERT INTO subtasks (assignment_id,seq,title,est_minutes,not_before)"
                         " VALUES (?,?,?,?,?)",
                         (aid2, 2, "Apply the ch 11 framework", 60, gate))
            conn.commit(); conn.close()
            print("  gated step — unavailable until day +3")

        overdue = c.post("/api/assignments", json={
            "title": f"{TAG} Forgotten reading", "class_id": classes["CSCI 301"],
            "due_at": at(-2, 23), "auto_plan": False}).json()["id"]
        c.post(f"/api/assignments/{overdue}/subtasks",
               json=[{"title": "Finish the chapter", "est_minutes": 40}])
        print("  overdue item — expect it to win the ranking")

    if use_ai:
        print(f"\n{C.dim}waiting for Claude to plan the steps…{C.off}")
        for _ in range(60):
            if not c.get("/api/now").json().get("planning"):
                break
            time.sleep(2)

    plan = c.get("/api/plan").json()
    print(f"\n{C.b}Plan{C.off}  {plan['total_work_minutes']}m of work, "
          f"{plan['planned_minutes']}m placed, {plan['unplaced_minutes']}m unplaced")
    for d in plan["days"][:6]:
        if d["blocks"]:
            day = datetime.fromisoformat(d["date"]).strftime("%a %d %b")
            print(f"  {day}  {d['planned_minutes']:>4}m  " +
                  ", ".join(b["title"][:24] for b in d["blocks"][:3]) +
                  (" …" if len(d["blocks"]) > 3 else ""))
    for u in plan["unplaceable"]:
        print(f"  {C.warn}! {u['assignment']}: {u['minutes']}m — {u['reason']}{C.off}")

    print(f"\n{C.ok}Done.{C.off} Open the display and the tablet app.")
    print(f"{C.dim}Remove it all with: python3 seed_demo.py --clear{C.off}")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--url", default="http://localhost:8000")
    p.add_argument("--ai", action="store_true", help="let Claude plan the steps")
    p.add_argument("--stress", action="store_true", help="add edge cases")
    p.add_argument("--clear", action="store_true")
    args = p.parse_args()

    c = httpx.Client(base_url=args.url, timeout=30.0)
    try:
        c.get("/api/health")
    except Exception:
        sys.exit(f"Can't reach {args.url}. Is the service running?")

    if args.clear:
        clear(c)
    else:
        clear(c)          # idempotent: never stack duplicate demo data
        seed(c, args.ai, args.stress)
    c.close()


if __name__ == "__main__":
    main()
