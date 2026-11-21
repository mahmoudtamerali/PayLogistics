"""SecureCare records service.

T09: health endpoints. T54 (early version): visit notes, audit entries and
input validation. The caller contract (headers, roles) is a DRAFT until the
API owner confirms it in T40; see README.md.
"""
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import Depends, FastAPI, Query, Request, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from botocore.exceptions import BotoCoreError, ClientError

from . import audit, auth, config, db, models, obs, tracing
from .auth import ApiError, Caller


@asynccontextmanager
async def lifespan(app: FastAPI):
    if config.CREATE_TABLE_ON_START:
        db.ensure_tables()
        if config.SEED_LOCAL_DATA:
            # Synthetic local data only: one clinician assigned to one patient.
            db.put_assignment("doc-demo", "pat-demo")
    yield


app = FastAPI(
    title="SecureCare records service",
    version=config.SERVICE_VERSION,
    lifespan=lifespan,
    docs_url=None,  # no interactive docs on a service that handles records
    redoc_url=None,
    openapi_url=None,
)

# X-Ray tracing is off unless XRAY_ENABLED=true (T62, see tracing.py).
tracing.setup(app)


# ---------- errors: short codes, never echo the input back ----------

@app.exception_handler(ApiError)
async def api_error_handler(request: Request, exc: ApiError):
    return JSONResponse(status_code=exc.status, content={"error": exc.code})


@app.exception_handler(RequestValidationError)
async def validation_handler(request: Request, exc: RequestValidationError):
    # Default FastAPI errors include the submitted value, which could be note
    # text. Return only which fields failed.
    fields = sorted({".".join(str(p) for p in e["loc"][1:]) or "body" for e in exc.errors()})
    obs.metric("ValidationFailures", fields=",".join(fields))
    return JSONResponse(status_code=422, content={"error": "invalid_request", "fields": fields})


@app.exception_handler(ClientError)
@app.exception_handler(BotoCoreError)
async def aws_error_handler(request: Request, exc: Exception):
    # DynamoDB unreachable, throttled or denied: tell the caller to retry later.
    obs.log("aws_error", error=type(exc).__name__, path=request.url.path)
    obs.metric("AwsErrors")
    return JSONResponse(status_code=503, content={"error": "dependency_unavailable"})


@app.exception_handler(Exception)
async def unexpected_handler(request: Request, exc: Exception):
    obs.log("unhandled_error", error=type(exc).__name__, path=request.url.path)
    return JSONResponse(status_code=500, content={"error": "internal_error"})


@app.middleware("http")
async def add_request_id_header(request: Request, call_next):
    response = await call_next(request)
    rid = getattr(request.state, "request_id", None)
    if rid:
        response.headers["X-Request-Id"] = rid
    return response


# ---------- health (no authentication) ----------

@app.get("/health")
def health():
    """Liveness: the process is up. The load balancer health check uses this."""
    return {"status": "ok", "service": config.SERVICE_NAME, "version": config.SERVICE_VERSION}


@app.get("/health/ready")
def ready(response: Response):
    """Readiness: the process can reach its tables."""
    if db.table_is_reachable():
        return {"status": "ready"}
    response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return {"status": "not ready"}


# ---------- visit notes ----------

def _check_patient_id(patient_id: str):
    if not models.valid_id(patient_id):
        raise ApiError(422, "invalid_request")


def _authorize(caller: Caller, patient_id: str, allowed: bool, action: str, note_id: str = ""):
    """Audit the decision, then raise 403 if it was a denial."""
    if allowed:
        audit.record(caller, patient_id, action, "allowed", note_id)
        return
    audit.record(caller, patient_id, action, "denied", note_id)
    obs.metric("DeniedRequests", reason="forbidden", action=action, request_id=caller.request_id)
    raise ApiError(403, "forbidden")


def _out(item: dict) -> models.NoteOut:
    return models.NoteOut(
        note_id=item["note_id"],
        patient_id=item["patient_id"],
        author_id=item["author_id"],
        visit_date=item["visit_date"],
        text=item["text"],
        created_at=item["created_at"],
    )


@app.post("/patients/{patient_id}/notes", status_code=201, response_model=models.NoteOut)
def create_note(patient_id: str, body: models.NoteIn, caller: Caller = Depends(auth.authenticate)):
    _check_patient_id(patient_id)
    note_id = audit.new_event_id()  # same time-sortable format as note ids
    allowed = auth.can_write(caller, patient_id)
    _authorize(caller, patient_id, allowed, "create_note", note_id)

    item = {
        "patient_id": patient_id,
        "note_id": note_id,
        "author_id": caller.caller_id,
        "visit_date": body.visit_date.isoformat(),
        "text": body.text,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    db.put_note(item)
    obs.metric("NotesCreated", request_id=caller.request_id)
    obs.log("note_created", patient_id=patient_id, note_id=note_id, request_id=caller.request_id)
    return _out(item)


@app.get("/patients/{patient_id}/notes", response_model=models.NoteList)
def list_notes(
    patient_id: str,
    limit: int = Query(50, ge=1, le=100),
    caller: Caller = Depends(auth.authenticate),
):
    _check_patient_id(patient_id)
    _authorize(caller, patient_id, auth.can_read(caller, patient_id), "list_notes")
    items = db.list_notes(patient_id, limit)
    return models.NoteList(patient_id=patient_id, notes=[_out(i) for i in items])


@app.get("/patients/{patient_id}/notes/{note_id}", response_model=models.NoteOut)
def get_note(patient_id: str, note_id: str, caller: Caller = Depends(auth.authenticate)):
    _check_patient_id(patient_id)
    if not models.valid_note_id(note_id):
        raise ApiError(422, "invalid_request")
    _authorize(caller, patient_id, auth.can_read(caller, patient_id), "read_note", note_id)
    item = db.get_note(patient_id, note_id)
    if item is None:
        raise ApiError(404, "not_found")
    return _out(item)
