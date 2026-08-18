"""Request and response schemas.

Kept apart from the routers so the shape of the API can be read in one
place, and so services never import HTTP concerns.
"""
from pydantic import BaseModel


class ClassIn(BaseModel):
    code: str
    name: str
    term: str | None = None
    color: str = "#6b7fd7"
    context: str | None = None


class ClassPatch(BaseModel):
    code: str | None = None
    name: str | None = None
    term: str | None = None
    color: str | None = None
    context: str | None = None


class EventPatch(BaseModel):
    title: str | None = None
    start_at: str | None = None
    end_at: str | None = None
    class_id: int | None = None
    location: str | None = None
    repeat_days: str | None = None
    repeat_until: str | None = None


class AssignmentIn(BaseModel):
    title: str
    class_id: int | None = None
    kind: str = "deliverable"
    description: str | None = None
    due_at: str | None = None
    auto_plan: bool = True


class AssignmentPatch(BaseModel):
    title: str | None = None
    class_id: int | None = None
    due_at: str | None = None
    description: str | None = None
    status: str | None = None
    confirmed: bool | None = None
    replan: bool = False


class SubtaskIn(BaseModel):
    title: str
    est_minutes: int = 30
    detail: str | None = None


class SubtaskPatch(BaseModel):
    action: str | None = None
    snooze_minutes: int = 90
    title: str | None = None
    est_minutes: int | None = None


class EventIn(BaseModel):
    title: str
    start_at: str
    end_at: str | None = None
    class_id: int | None = None
    location: str | None = None
    all_day: bool = False
    repeat_days: str | None = None      # "1,3,5" — 0=Sunday
    repeat_until: str | None = None
