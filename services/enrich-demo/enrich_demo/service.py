"""A small HTTP trigger for the enrich workflow.

Separate from Core on purpose. Core is a store; it does not run workflows. In
production a curator starts an Enrich workflow through the Enrich service, and
NeoWiki never does. Keeping the trigger here means the demo has the same shape.

Run it beside Core:

    uv run uvicorn enrich_demo.service:app --port 8001
"""

import os
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from enrich_demo.flow import correct_attribution, curate_entity

app = FastAPI(
    title="ECHOLOT Enrich (demo)",
    description="Starts the curation workflow. One run is one batch.",
)

# The page is served by Core on another port, so the browser calls this from a
# different origin. The demo allows that origin only.
CORE_ORIGIN_ENV = "ECHOLOT_UI_ORIGIN"
DEFAULT_UI_ORIGINS = ["http://127.0.0.1:8000", "http://localhost:8000"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=[os.environ[CORE_ORIGIN_ENV]]
    if os.environ.get(CORE_ORIGIN_ENV)
    else DEFAULT_UI_ORIGINS,
    allow_methods=["POST"],
    allow_headers=["Content-Type"],
)


class RunRequest(BaseModel):
    name: str = Field(min_length=1)
    subject: str = Field(min_length=1)
    core_url: str | None = None


class CorrectRequest(BaseModel):
    subject: str = Field(min_length=1)
    corrected: str = Field(min_length=1)
    curator: str = Field(min_length=1)
    core_url: str | None = None


@app.post("/run")
def run(request: RunRequest) -> Any:
    """Run the three-stage workflow and return what each stage produced.

    Declared with ``def`` rather than ``async def`` on purpose: FastAPI then
    runs it in a worker thread, so a flow run of several seconds does not block
    the server from answering anything else.
    """
    return curate_entity(request.name, request.subject, request.core_url)


@app.post("/correct")
def correct(request: CorrectRequest) -> Any:
    """Apply a curator's correction to a subject the workflow produced."""
    return correct_attribution(
        request.subject, request.corrected, request.curator, request.core_url
    )


@app.get("/health")
def health() -> Any:
    return {"status": "ok"}
