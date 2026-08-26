# Manual Test Plan

Automated tests cover the logic. These cover what only a human can check:
does it look right, and does it behave sensibly when you're actually using it.

**Run in this order.** The automated suite must run against an empty planner —
demo data outranks its fixtures and will cause false failures.

```bash
cd ~/planner && source .venv/bin/activate

python3 seed_demo.py --clear          # 1. clear the decks
python3 test_planner.py --no-ai       # 2. logic, free, ~15s
python3 test_planner.py               # 3. with real Claude, ~30c, 2-3 min
python3 seed_demo.py --stress         # 4. seed a realistic week + edge cases
```

Then work through the checks below with the TV and your phone both open.
Tick as you go; anything that fails, note what you saw.

---

## A. Display — first look

Open `http://localhost:8000/display` on the NUC, fullscreen with F11.

- [ ] **A1** Left panel shows today's timeline, right panel shows the week
      (or stacked, if the screen is tall)
- [ ] **A2** Red now-line sits about 44% down and shows the current time
- [ ] **A3** Time labels read fully — `10:30`, not `10:3`
- [ ] **A4** Hour lines are solid; 10-minute lines are fainter
- [ ] **A5** Lectures appear as filled, receded blocks
- [ ] **A6** Study blocks are brighter with a coloured left edge
- [ ] **A7** Block colours match their class colour
- [ ] **A8** Week panel lists due dates only — no individual steps
- [ ] **A9** Header reads e.g. `3h 50m planned · 8h 15m free`

**Resize test** — this is where the geometry bug lived:

- [ ] **A10** Press F11 to leave fullscreen. Now-line still on the correct time?
- [ ] **A11** Press F11 again. Still correct?
- [ ] **A12** Drag the window narrow, then wide. Layout flips between
      side-by-side and stacked, and the line stays put

**Scrolling** — the tape moves, the line doesn't:

- [ ] **A13** Watch for 60 seconds. Blocks creep upward past the fixed line
- [ ] **A14** Nothing jumps or stutters at the minute boundary

---

## B. Stress cases

`--stress` seeded three deliberate problems.

- [ ] **B1** Red warning under the timeline about work that won't fit
- [ ] **B2** The overdue item (`Forgotten reading`) is what the NOW strip shows —
      overdue outranks everything
- [ ] **B3** The overdue item **also appears as a scheduled block**, not just a
      warning. It's late, but it still has to happen
- [ ] **B4** The gated step (`Gated response`) is **not** scheduled before
      day +3, and shows as waiting rather than vanishing
- [ ] **B5** No day exceeds the daily cap. Add up one day's blocks against the
      `Max study per day` setting

---

## C. Completing work — the green behaviour

On your phone, open the app and go to **Now**.

- [ ] **C1** The step shown matches the display's NOW strip
- [ ] **C2** Tap **Done**. Toast appears, a new step loads
- [ ] **C3** Within 20 seconds the TV updates on its own
- [ ] **C4** The completed block **turned green in place** — it did not move or
      duplicate
- [ ] **C5** Complete two more in quick succession. They tile back-to-back with
      **no overlap or stacking**
- [ ] **C6** Green blocks sit behind the now-line, blue ahead of it
- [ ] **C7** In Work → the assignment, uncheck a completed step. It returns to
      the plan and the green block disappears from the TV
- [ ] **C8** Tap **Not now**. The next-highest item takes over
- [ ] **C9** Finish every step of one assignment. It auto-closes and leaves the
      week panel

---

## D. Adding work

- [ ] **D1** Add → Assignment. Enter a title and a due date 4 days out. Save
- [ ] **D2** "Planning the steps…" spinner appears
- [ ] **D3** Within ~20s, 4–9 steps appear
- [ ] **D4** **Step 1 is under 15 minutes and requires no thinking.**
      If it says "research the topic," the prompt is drifting
- [ ] **D5** Steps are concrete — you can tell when each is finished
- [ ] **D6** The TV picks the new work up and schedules it across days
- [ ] **D7** Add → Assignment → **Upload a PDF** of a real assignment sheet
- [ ] **D8** Review banner appears with the extracted due date
- [ ] **D9** The date is right. If not, fix it in the field
- [ ] **D10** Tap **Looks right**. Steps generate
- [ ] **D11** The description captured real requirements — word count, format

---

## E. Recurring events

- [ ] **E1** Add → Event. Title, start time, tap **M** and **W**, set a
      repeat-until date. Save
- [ ] **E2** Both days show it on the TV timeline over the coming week
- [ ] **E3** Study blocks route around it with a gap either side
- [ ] **E4** Settings → the series appears **once**, not as many copies

---

## F. Settings and editing

- [ ] **F1** Settings tab lists Classes, Assignments, Events, Schedule
- [ ] **F2** Tap a class → change its colour → Save
- [ ] **F3** That class's blocks change colour on the TV
- [ ] **F4** Tap an assignment → change the due date → Save
- [ ] **F5** The schedule re-flows to the new deadline
- [ ] **F6** Change **Max study per day** to 120. Save
- [ ] **F7** Days now cap at 2 hours and work spreads further out
- [ ] **F8** Set it back to 240
- [ ] **F9** Delete an event from Settings. Gone from the TV within 20s
- [ ] **F10** Delete an assignment. Confirmation prompt appears first
- [ ] **F11** Delete a class. Its assignments survive, showing "No class"

---

## G. Syllabus context

- [ ] **G1** Settings → a class → **Upload a syllabus**
- [ ] **G2** After ~15s the extracted context appears
- [ ] **G3** If the syllabus has a dated outline, a **topic calendar** is listed
- [ ] **G4** Dates match the real syllabus. Spot-check three
- [ ] **G5** Add an assignment about material taught later in the term.
      Steps needing it should be gated to that date, while prep steps
      (reading the prompt, gathering sources) still start today

```bash
sqlite3 ~/planner/data/planner.db \
  "SELECT code, substr(topic_schedule,1,300) FROM classes;"
```

---

## H. Resilience

- [ ] **H1** `sudo systemctl stop planner`. Within 3 minutes the TV shows
      **NO CONNECTION** in amber rather than stale data
- [ ] **H2** `sudo systemctl start planner`. Recovers within a few seconds
- [ ] **H3** `sudo reboot`. Service and kiosk both return unattended
- [ ] **H4** Airplane-mode the phone, tap Done. It fails visibly rather than
      pretending to succeed
- [ ] **H5** Upload a non-PDF. Rejected with a message, no crash
- [ ] **H6** Upload a blank PDF. No invented due date

---

## I. Overnight

- [ ] **I1** After midnight the display drops to ~5% brightness
- [ ] **I2** Dark enough to sleep with. If not:
      `/display?nightdim=0.02`
- [ ] **I3** Back to full brightness after 7am

To test without waiting: `/display?nightfrom=0&nightto=23`

---

## J. Backups

```bash
~/planner/backup.sh
~/planner/restore.sh --list
```

- [ ] **J1** Backup reports sensible counts
- [ ] **J2** The file appears in your cloud remote
- [ ] **J3** `restore.sh --list` shows both local and remote copies
- [ ] **J4** **Actually restore it.** An untested backup isn't a backup
- [ ] **J5** Service comes back healthy and your data is intact

---

## K. Live for a day

The checks no test can make. Leave it running and note:

- [ ] **K1** Did you look at the TV without being prompted?
- [ ] **K2** Were the estimates close? Track two or three real steps
- [ ] **K3** Did the 8am–11pm window match when you actually work?
- [ ] **K4** Was the suggested next action ever obviously wrong?
- [ ] **K5** Did you do a step *because* it was shown?

**K2 and K3 matter most.** If estimates run 50% under, the whole schedule is
fiction and `max_daily_min` needs to come down. If it plans 8am blocks you
never take, narrow the day window. A plan you don't believe gets ignored the
same way a paper planner does.

---

## Cleanup

```bash
python3 seed_demo.py --clear
```

