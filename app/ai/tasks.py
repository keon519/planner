"""The jobs themselves: extract, decompose, summarise."""
import base64
from datetime import datetime, timezone
from pathlib import Path

from .client import MODEL, _client, _json, _record, _text
from .prompts import DECOMPOSE_SYSTEM, EXTRACT_SYSTEM, SUMMARY_SYSTEM


def extract_pdf(path: str, today: str | None = None, usage_sink=None) -> dict:
    today = today or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    data = base64.standard_b64encode(Path(path).read_bytes()).decode()
    resp = _client().messages.create(
        model=MODEL,
        max_tokens=1500,
        system=EXTRACT_SYSTEM,
        messages=[{"role": "user", "content": [
            {"type": "document",
             "source": {"type": "base64", "media_type": "application/pdf", "data": data}},
            {"type": "text", "text": f"Today is {today}. Extract this assignment."},
        ]}],
    )
    _record(usage_sink, "extract_pdf", resp)
    return _json(_text(resp))


def extract_text(body: str, today: str | None = None, usage_sink=None) -> dict:
    today = today or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    resp = _client().messages.create(
        model=MODEL,
        max_tokens=1500,
        system=EXTRACT_SYSTEM,
        messages=[{"role": "user",
                   "content": f"Today is {today}. Extract this assignment.\n\n{body}"}],
    )
    _record(usage_sink, "extract_text", resp)
    return _json(_text(resp))


def decompose(assignment: dict, class_context: str | None = None,
              hours_available: float | None = None,
              topic_schedule: list | None = None,
              today: str | None = None, usage_sink=None,
              portfolio: str | None = None) -> list[dict]:
    today = today or datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d")
    parts = [f"Today is {today}.",
             f"Assignment: {assignment['title']}",
             f"Type: {assignment.get('kind', 'deliverable')}"]
    if assignment.get("due_at"):
        parts.append(f"Due: {assignment['due_at']}")
    if hours_available is not None:
        parts.append(f"Hours available before the deadline: {hours_available:.1f}")
    if class_context:
        parts.append(f"\nCourse context:\n{class_context}")
    if portfolio:
        # Already includes course context and the topic calendar.
        parts.append(f"\n--- SURROUNDING CONTEXT ---\n{portfolio}\n--- END CONTEXT ---")
    elif topic_schedule:
        cal = "\n".join(f"  {e['date']}: {e['topics']}" for e in topic_schedule)
        parts.append(f"\nCourse topic calendar (material is taught on these dates):\n{cal}")
    if assignment.get("description"):
        parts.append(f"\nWhat the assignment says:\n{assignment['description']}")

    resp = _client().messages.create(
        model=MODEL,
        max_tokens=4000,
        system=DECOMPOSE_SYSTEM,
        messages=[{"role": "user", "content": "\n".join(parts)}],
    )
    _record(usage_sink, "decompose", resp)
    steps = _json(_text(resp))

    clean = []
    for s in steps[:12]:
        title = str(s.get("title", "")).strip()
        if not title:
            continue
        nb = s.get("not_before")
        if nb:
            try:
                datetime.strptime(str(nb)[:10], "%Y-%m-%d")
                nb = str(nb)[:10]
            except ValueError:
                nb = None
        clean.append({
            "title": title[:200],
            "est_minutes": max(5, min(int(s.get("est_minutes") or 30), 180)),
            "detail": (s.get("detail") or None),
            "not_before": nb,
        })
    return clean


def summarize_class_doc(path: str, usage_sink=None) -> dict:
    """Returns {"summary": str, "schedule": [{"date","topics"}]}."""
    data = base64.standard_b64encode(Path(path).read_bytes()).decode()
    resp = _client().messages.create(
        model=MODEL,
        max_tokens=2500,
        system=SUMMARY_SYSTEM,
        messages=[{"role": "user", "content": [
            {"type": "document",
             "source": {"type": "base64", "media_type": "application/pdf", "data": data}},
            {"type": "text", "text": "Extract the summary and dated topic schedule."},
        ]}],
    )
    _record(usage_sink, "summarize", resp)
    out = _json(_text(resp))
    if not isinstance(out, dict):
        return {"summary": str(out)[:700], "schedule": []}
    out.setdefault("summary", "")
    out.setdefault("schedule", [])
    out.setdefault("readings", [])
    out["readings"] = [r for r in out["readings"]
                       if isinstance(r, dict) and r.get("title")][:40]
    out["schedule"] = [e for e in out["schedule"]
                       if isinstance(e, dict) and e.get("date") and e.get("topics")][:60]
    return out
