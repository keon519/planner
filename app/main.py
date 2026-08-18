"""Application entry point.

Assembly only. Every route lives in `routers/`, every rule in `services/`.
Adding a feature should mean touching this file once, to register a router.
"""
import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import db
from .config import STATIC
from .routers import admin, assignments, classes, events, plan, subtasks
from .services import worker

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init()
    task = asyncio.create_task(worker.loop())
    try:
        yield
    finally:
        task.cancel()


app = FastAPI(
    title="Study Planner",
    description="One question: what should I be doing right now?",
    version="1.0.0",
    lifespan=lifespan,
)

for r in (classes.router, assignments.router, subtasks.router,
          events.router, plan.router, admin.router):
    app.include_router(r)


@app.get("/display", include_in_schema=False)
def display():
    """The always-on TV view."""
    return FileResponse(STATIC / "display.html")


@app.get("/", include_in_schema=False)
def index():
    """The phone and tablet app."""
    return FileResponse(STATIC / "index.html")


# Mounted last so it can never shadow an /api route.
app.mount("/static", StaticFiles(directory=STATIC), name="static")
