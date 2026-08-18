# Architecture notes

Decisions that aren't obvious from reading the code.

## The plan is computed, not stored

`plan_horizon` recalculates from scratch on every request. Same inputs give the
same output, so nothing jitters between refreshes — but completing a step early
re-flows everything downstream immediately, with no stale rows to reconcile.

A full 14-day plan takes about 1.6 ms, so recomputing every 20 seconds costs
nothing.

The one exception: when a step is completed, its slot is written to
`planned_start` / `planned_end`. That lets the timeline turn the block green
*in place*, and — importantly — marks the time as occupied. Without it, each
completion frees its slot and the next step slides in, so every step records the
same start time and they pile up.

## Pressure, not deadline

`remaining_minutes / minutes_until_due`. Sorting by deadline alone gets it
backwards: an essay due Friday with twelve hours left is more urgent than a quiz
tomorrow with thirty minutes left.

Recomputed after every placement, which is what produces interleaving — as a
heavy assignment gets scheduled, its remaining work drops, its pressure falls,
and another course takes the floor.

Infeasibility falls out for free: pressure above 1.0 means the time doesn't
exist, and the display says so rather than producing an impossible schedule.

## One SQLite writer

The worker runs as an `asyncio` task inside the uvicorn process, not a separate
service. One event loop means one writer, which sidesteps concurrent-write
locking entirely.

`check_same_thread=False` is required: FastAPI can run a dependency's setup and
its teardown on different threadpool threads. Each connection is still used by
exactly one request at a time, so this is safe — but without it you get
intermittent 500s that only appear under real traffic.

## Prompt rules that carry weight

In `app/ai/prompts.py`:

- **Step one must be nearly effortless.** 5–15 minutes, purely mechanical, no
  decisions. Activation energy on the first step decides whether the list is
  ever started. This is the single most important line in the file.
- **Never invent a due date.** A silently wrong deadline paces everything else
  wrong and destroys trust in the system. A blank date the user fills in is a
  far better failure.
- **Concrete actions with a visible finish line.** "Reread ch. 4-6 and mark
  passages about identity", not "study chapter 4" — you can't tell when the
  second one is done.

## Allowlist, not blocklist

`services/materials.py` restricts searching to vetted open-access, OER, and
lending domains, and re-checks every returned URL against the same list. A
blocklist needs updating as new mirrors appear; an allowlist can only ever
return sources vetted going in.

## Timezones

Everything is stored UTC and converted at the edges — the browser for display,
`astimezone()` for scheduling. Mixing local and UTC in a calendar app is the
single most common source of off-by-one-day bugs.

The scheduler works in local time deliberately: "don't schedule before 8am"
means 8am where the person is, not 8am UTC.
