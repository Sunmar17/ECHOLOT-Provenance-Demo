"""A small HTTP trigger for the enrich workflow.

Separate from Core on purpose. Core is a store; it does not run workflows. In
production a curator starts an Enrich workflow through the Enrich service, and
NeoWiki never does. Keeping the trigger here means the demo has the same shape.

Run it beside Core:

    uv run uvicorn enrich_demo.service:app --port 8001
"""

import os
from importlib import resources
from typing import Annotated, Any, Literal

from echolot_prov.client import CoreValidationError
from fastapi import FastAPI, File, Form, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from enrich_demo.flow import correct_value, curate_upload
from enrich_demo.upload import UploadError, parse_upload, upload_source

app = FastAPI(
    title="ECHOLOT Enrich (demo)",
    description="Runs the curation workflow over an uploaded file. One upload is "
    "one batch.",
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

SAMPLES = {"person": "person.json", "artifacts": "artifacts.csv"}
"""The packaged sample uploads. Synthetic; see samples/README.md."""


class SampleRequest(BaseModel):
    sample: Literal["person", "artifacts"]
    uploader: str | None = None
    core_url: str | None = None


class CorrectRequest(BaseModel):
    """One value a curator replaces, and the snapshot that asserted it."""

    subject: str = Field(min_length=1)
    curator: str = Field(min_length=1)
    property: str = Field(min_length=1)
    value: str = Field(min_length=1)
    value_is_iri: bool = False
    statement: str | None = None
    """The claim the value sits on; absent for a plain value on the subject."""
    corrects: str | None = None
    """The snapshot whose value is being corrected."""
    core_url: str | None = None


def _run(filename: str, data: bytes, uploader: str | None, url: str | None) -> Any:
    """Read the file first, so a bad file is refused before anything is written.

    Declared as a plain function and called from ``def`` endpoints: FastAPI then
    runs the endpoint in a worker thread, so a flow run of several seconds does
    not block the server from answering anything else.
    """
    try:
        records = parse_upload(filename, data)
    except UploadError as exc:
        return JSONResponse(status_code=422, content={"errors": [exc.as_dict()]})
    return curate_upload(records, upload_source(filename, data), uploader or None, url)


@app.post("/upload")
def upload(
    file: Annotated[UploadFile, File()],
    uploader: Annotated[str | None, Form()] = None,
    core_url: Annotated[str | None, Form()] = None,
) -> Any:
    """Run the workflow over an uploaded CSV or JSON file."""
    return _run(file.filename or "upload", file.file.read(), uploader, core_url)


@app.post("/upload/sample")
def upload_sample(request: SampleRequest) -> Any:
    """Run the workflow over a packaged sample, through the same path as a file."""
    name = SAMPLES[request.sample]
    data = resources.files("enrich_demo").joinpath("samples", name).read_bytes()
    return _run(name, data, request.uploader, request.core_url)


@app.post("/correct")
def correct(request: CorrectRequest) -> Any:
    """Apply a curator's correction to one value, plain or on a claim.

    If Core refuses it, Core's own reasons are passed back, so the page can say
    why instead of reporting a server error.
    """
    try:
        return correct_value(
            request.subject,
            request.curator,
            request.property,
            request.value,
            request.value_is_iri,
            request.statement,
            request.corrects,
            request.core_url,
        )
    except CoreValidationError as exc:
        return JSONResponse(status_code=422, content={"errors": exc.errors})


@app.get("/health")
def health() -> Any:
    return {"status": "ok"}
