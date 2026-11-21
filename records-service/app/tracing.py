"""Optional AWS X-Ray tracing (T62). Off unless XRAY_ENABLED=true.

What it does: one segment per request, plus one subsegment per DynamoDB call, sent over UDP to
the X-Ray daemon that runs next to this container in the same ECS task (127.0.0.1:2000).

Rules this module keeps:
  * Tracing must never break a request. Every call into the X-Ray library is wrapped, and the
    service runs the same whether the daemon is there or not (UDP does not wait for an answer).
  * Privacy. A segment carries only: the service name, HTTP method, the ROUTE TEMPLATE
    (/patients/{patient_id}/notes, never the real id), the status code, the caller role if it is
    one of the known roles, and the request id. No patient ids, caller ids, tokens, headers, query
    strings or bodies. DynamoDB subsegments carry only the operation name and the table name.
  * The aws-xray-sdk import is lazy, so an image built without it still starts.
"""
import contextvars
import os

from . import config, obs

ENABLED_ENV = "XRAY_ENABLED"
DAEMON_ENV = "AWS_XRAY_DAEMON_ADDRESS"

_segment_var: contextvars.ContextVar = contextvars.ContextVar("xray_segment", default=None)
_emitter = None
_lib = None  # holds the SDK classes once loaded


def enabled() -> bool:
    return _lib is not None


def _load(emitter=None) -> bool:
    """Import the SDK and build the UDP emitter. Returns False (and logs once) on any failure."""
    global _emitter, _lib
    try:
        from aws_xray_sdk.core.emitters.udp_emitter import UDPEmitter
        from aws_xray_sdk.core.models import http
        from aws_xray_sdk.core.models.segment import Segment
        from aws_xray_sdk.core.models.subsegment import Subsegment
        from aws_xray_sdk.core.models.trace_header import TraceHeader

        _emitter = emitter or UDPEmitter(os.environ.get(DAEMON_ENV, "127.0.0.1:2000"))
        _lib = {
            "http": http,
            "Segment": Segment,
            "Subsegment": Subsegment,
            "TraceHeader": TraceHeader,
        }
        return True
    except Exception as exc:  # noqa: BLE001 - tracing must never stop the service
        obs.log("xray_setup_failed", error=type(exc).__name__)
        _lib = None
        return False


def setup(app, emitter=None) -> bool:
    """Turn tracing on for this FastAPI app when XRAY_ENABLED=true. Returns True if it is on."""
    if os.environ.get(ENABLED_ENV, "false").lower() != "true":
        return False
    if not _load(emitter):
        return False
    app.middleware("http")(_trace_request)
    obs.log("xray_enabled")
    return True


def reset():
    """Switch tracing off again (used by tests)."""
    global _emitter, _lib
    _emitter = None
    _lib = None


def _safe(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except Exception:  # noqa: BLE001
        return None


def _send(entity):
    if _emitter is not None:
        _safe(_emitter.send_entity, entity)


async def _trace_request(request, call_next):
    if _lib is None:
        return await call_next(request)

    segment = None
    token = None
    try:
        header = _lib["TraceHeader"].from_header_str(request.headers.get("x-amzn-trace-id"))
        segment = _lib["Segment"](config.SERVICE_NAME, traceid=header.root, parent_id=header.parent)
        if header.sampled == 0:  # the caller said "do not sample"; respect it
            segment.sampled = False
        token = _segment_var.set(segment)
    except Exception:  # noqa: BLE001
        segment = None

    status_code = 500
    try:
        response = await call_next(request)
        status_code = response.status_code
        return response
    finally:
        if segment is not None:
            _safe(_finish_segment, segment, request, status_code)
            if token is not None:
                _segment_var.reset(token)


def _finish_segment(segment, request, status_code: int):
    http = _lib["http"]
    route = request.scope.get("route")
    template = getattr(route, "path", None) or "unmatched"  # never the raw path: it holds patient ids
    segment.put_http_meta(http.METHOD, request.method)
    segment.put_http_meta(http.URL, template)
    segment.put_http_meta(http.STATUS, status_code)
    if 400 <= status_code < 500:
        segment.add_error_flag()
    if status_code >= 500:
        segment.add_fault_flag()

    role = request.headers.get("x-caller-role", "")
    if role in (config.WRITE_ROLES | config.READ_OWN_ROLES):
        segment.put_annotation("role", role)
    rid = getattr(request.state, "request_id", None)
    if rid:
        segment.put_annotation("request_id", rid)

    segment.close()
    if segment.sampled:
        _send(segment)


# ---------- DynamoDB subsegments (botocore event hooks) ----------

def instrument(client) -> None:
    """Add subsegments for every DynamoDB call made through this boto3 client. No-op when tracing is off."""
    if _lib is None:
        return
    events = client.meta.events
    events.register("provide-client-params.dynamodb", _remember_table)
    events.register("before-call.dynamodb", _before_call)
    events.register("after-call.dynamodb", _after_call)
    events.register("after-call-error.dynamodb", _after_call_error)


def _remember_table(params=None, context=None, **_):
    # Only the table name is taken from the call parameters (never Key, Item or expressions).
    if context is not None and isinstance(params, dict):
        context["xray_table"] = params.get("TableName")


def _before_call(model=None, params=None, context=None, **_):
    segment = _segment_var.get()
    if segment is None or context is None or _lib is None:
        return
    try:
        sub = _lib["Subsegment"]("DynamoDB", "aws", segment)
        # Operation and table only; never Key or Item values.
        sub.set_aws({"operation": getattr(model, "name", "unknown"), "table_name": context.get("xray_table")})
        segment.add_subsegment(sub)
        context["xray_subsegment"] = sub
    except Exception:  # noqa: BLE001
        pass


def _close_sub(context, status=None, fault=False):
    sub = (context or {}).pop("xray_subsegment", None)
    if sub is None:
        return
    try:
        if status is not None:
            sub.put_http_meta(_lib["http"].STATUS, status)
        if fault:
            sub.add_fault_flag()
        sub.close()
    except Exception:  # noqa: BLE001
        pass


def _after_call(http_response=None, context=None, **_):
    _close_sub(context, status=getattr(http_response, "status_code", None))


def _after_call_error(context=None, **_):
    _close_sub(context, fault=True)
