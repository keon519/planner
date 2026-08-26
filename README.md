# Study Planner

An always-on study planner that answers one question: **what should I be doing right now?**

Upload an assignment, and Claude breaks it into startable steps. A scheduler
places those steps into the real gaps between your lectures and shifts, working
backward from each deadline. A TV in the corner of the room shows the result,
so you never have to open an app to find out what's next.

Runs on one small always-on machine. No cloud, no accounts, no subscription
beyond your own Claude API usage — a few dollars a semester.

---

## Why it works this way

Two failure modes drove every design decision:

**"I forget due dates because I don't check."** Any system you have to open
inherits the habit that's already failing. The always-on display isn't a
feature; it's the fix.

**"I don't know what I should be studying."** This is why there's no research
or link-gathering feature. Sitting down to eight links to evaluate is *more*
decisions at the moment you're already stuck. The answer is one specific action,
small enough to start, with nothing to choose.

---

## What it does

- **Assignment → steps.** Upload a PDF or type a title. Claude extracts the due
  date and requirements, then produces 4–9 concrete steps. The first is always
  under 15 minutes and requires no thinking, because activation energy on step
  one decides whether the list ever gets started.
- **Steps → calendar.** A scheduler places every step into free time before its
  deadline, front-loading within a daily cap, respecting your recurring classes,
  and spacing exam revision instead of cramming it.
- **Pressure ranking.** Priority is `work remaining ÷ time remaining`, not raw
  deadline. An essay due Friday with twelve hours left outranks a quiz tomorrow
  with thirty minutes left.
- **Honest infeasibility.** When the time doesn't exist, it says so — *"4h 20m
  won't fit before it's due"* — rather than producing an impossible schedule.
- **Syllabus context.** Upload a syllabus and it extracts the topic calendar, so
  steps needing untaught material are gated until the lecture that covers them.
  Prep that can start now still starts now.
- **Portfolio awareness.** Decomposition sees your other assignments, work
  already completed for that course, competing deadlines, and how your real pace
  compares to past estimates.
- **Cost tracking.** Every API call is priced locally from its own usage block.

---

## Architecture

One Python process. No Docker, no Redis, no message broker — at this scale they
add failure modes without buying anything.

```
┌──────────────────────────────────────────┐
│  Always-on mini PC                       │
│                                          │
│  uvicorn ──┬── FastAPI (routers/)        │
│            └── worker (asyncio task)     │
│                                          │
│  SQLite (WAL) + data/uploads/            │
│  Chromium kiosk ──> localhost/display    │
└──────────┬───────────────────┬───────────┘
           │ HDMI              │ LAN
           v                   v
        ┌─────┐          ┌──────────┐
        │ TV  │          │  Phone   │
        └─────┘          └──────────┘
                              │ HTTPS
                              v
                      ┌──────────────┐
                      │  Claude API  │
                      └──────────────┘
```

The worker runs as an `asyncio` task inside the same uvicorn process. That means
a single SQLite writer, which sidesteps concurrent-write locking entirely.

The schedule is **computed, never stored**. Same inputs always produce the same
plan, so it doesn't jitter between refreshes — but finish a step early and
everything downstream re-flows immediately. No stale calendar rows to reconcile.

### Layout

```
app/
├── main.py              app factory, lifespan, static mounts
├── config.py            paths and environment
├── db.py                connection, schema, migrations
├── models.py            request/response schemas
├── routers/             HTTP layer, one module per resource
│   ├── classes.py       classes and uploaded documents
│   ├── assignments.py   CRUD, PDF upload, context
│   ├── subtasks.py      step lifecycle
│   ├── events.py        events and weekly recurrence
│   ├── plan.py          now, day, window, week, plan
│   └── admin.py         settings, usage, calibration
├── services/            domain logic, no HTTP
│   ├── ranking.py       pick_now, pressure, agenda
│   ├── scheduling.py    horizon planner, free windows
│   ├── context.py       portfolio context, calibration
│   └── worker.py        background job loop
└── ai/                  Claude API
    ├── client.py        client, pricing, JSON parsing
    ├── prompts.py       every system prompt, in one place
    └── tasks.py         extract, decompose, summarize
static/
├── display.html         TV view
└── index.html           phone/tablet app
```

Routers hold no logic; services know nothing about HTTP. Adding a feature means
one service module and one router, and prompts live together so they can be
tuned without touching code.

---

## Setup

Needs Python 3.11+ and an [Anthropic API key](https://console.anthropic.com).

```bash
git clone https://github.com/YOU/study-planner.git
cd study-planner

python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env      # add your API key
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

- Phone/tablet: `http://<host>:8000` — "Add to Home Screen" for a real app icon
- TV: `http://<host>:8000/display` — Chromium in kiosk mode

### Running it for real

`docs/DEPLOY.md` covers the systemd units, kiosk autostart, nightly backups to a
cloud remote, and remote access over Tailscale.

**Never port-forward this.** There is no authentication. Use Tailscale.

---

## Testing

```bash
python3 test_planner.py --no-ai    # logic only, free, ~15s
python3 test_planner.py            # with real Claude calls, ~30c
python3 seed_demo.py --stress      # realistic week + edge cases
```

`MANUAL_TESTS.md` covers what automation can't: whether the display reads
correctly across the room, whether generated steps are genuinely startable, and
whether the estimates survive contact with a real week.

---

## Configuration

Everything is tunable from **Settings** in the app, or the API:

| Setting | Default | What it does |
|---|---|---|
| `day_start_hour` / `day_end_hour` | 8 / 23 | When work may be scheduled |
| `max_daily_min` | 240 | Cap per day; stops front-loading from meaning an 11-hour Monday |
| `min_chunk` / `max_block` | 20 / 90 | Block size bounds |
| `buffer_min` | 10 | Breathing room around events |
| `horizon_days` | 14 | How far ahead to plan |
| `exam_per_day` | 1 | Spacing beats cramming |
| `layout` | auto | TV orientation |

---

## Limitations

- **Single user.** No authentication, no multi-tenancy. Runs on your LAN.
- **Estimate quality is the ceiling.** If the generated estimates are wrong, the
  schedule is fiction. Calibration corrects for this over time, but it needs a
  couple of weeks of real completions first.
- **Syllabus gating is only as good as the syllabus.** No dated outline means no
  gates — the feature degrades to absent rather than to wrong.

---

## License

MIT.
