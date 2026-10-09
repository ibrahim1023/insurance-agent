import asyncio
import hashlib
import json
import math
import os
import re
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

import httpx
from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from .groq_image import observe_groq_jpeg, read_groq_crops
from .models import CorrectionIntent, PricingStageRecord, ReadingStageRecord, VisionStageRecord
from .price_sources import default_resolver
from .pricing import PricingManager, load_pricing_contract, price_books
from .processing import PostSweepProcessor
from .reading import ReadingManager, load_reading_contract, read_spines
from .sweeps import (
    FRAME_ID_RE,
    MAX_FRAME_BYTES,
    PROJECT_ROOT,
    SWEEP_ID_RE,
    StoreError,
    SweepStore,
)
from .vision import VisionManager, analyze_sweep_frame, load_contract
from .voice_view import build_voice_view

BASE_DIR = Path(__file__).resolve().parent
WEB_DIR = BASE_DIR / "web"
VOICE_PATH = PROJECT_ROOT / "preflight" / "safari" / "voice.js"
VISION_CONTRACT = load_contract(BASE_DIR / "vision_contract.json")
GROQ_VISION_PROFILE = load_contract(BASE_DIR / "groq_vision_contract.json")
GROQ_VISION_CONTRACT = {
    **{key: value for key, value in VISION_CONTRACT.items() if key != "cost_evidence"},
    **GROQ_VISION_PROFILE,
}
TRACKING_CONTRACT = load_contract(BASE_DIR / "tracking_contract.json")
READING_CONTRACT = load_reading_contract(BASE_DIR / "reading_contract.json")
GROQ_READING_PROFILE = load_reading_contract(BASE_DIR / "groq_reading_contract.json")
GROQ_READING_CONTRACT = {
    **{key: value for key, value in READING_CONTRACT.items() if key != "cost_evidence"},
    **GROQ_READING_PROFILE,
    "selection": {**READING_CONTRACT["selection"], **GROQ_READING_PROFILE["selection"]},
}
PRICING_CONTRACT = load_pricing_contract(BASE_DIR / "pricing_contract.json")
GROQ_PRICING_PROFILE = load_pricing_contract(BASE_DIR / "groq_pricing_contract.json")
GROQ_PRICING_CONTRACT = {
    **{key: value for key, value in PRICING_CONTRACT.items() if key not in ("cost_evidence", "response_format")},
    **GROQ_PRICING_PROFILE,
}
REALTIME_CONTRACT = load_contract(BASE_DIR / "realtime_contract.json")
OPENAI_CALLS_URL = "https://api.openai.com/v1/realtime/calls"
MAX_SDP_BYTES = 65536
MAX_JSON_BYTES = 131072
REQUEST_TIMEOUT = 30.0


def resolve_inventory_provider(value: str | None) -> Literal["openai", "groq"]:
    if value is None:
        return "openai"
    if value not in ("openai", "groq"):
        raise ValueError("unsupported inventory provider")
    return value


class StartSweepRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    country: str
    currency: str
    device: str = Field(max_length=200)


class TurnRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    turn_id: str = Field(min_length=1)
    text: str = Field(min_length=1, max_length=8000)
    source: Literal["typed_user", "provider_user_transcript"]

    @field_validator("text")
    @classmethod
    def _text_not_blank(cls, value):
        if not value.strip():
            raise ValueError("turn text must not be blank")
        return value


class EndRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    duration_s: float = Field(ge=0, allow_inf_nan=False)


class AnalyzeFrameRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    frame_id: str = Field(min_length=1)


class ReadSpinesRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ref_ids: list[str] | None = None


class PriceBooksRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    book_ids: list[str] | None = None


class PacketRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


async def get_http_client() -> httpx.AsyncClient:
    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
        yield client


async def get_pricing_http_client() -> httpx.AsyncClient:
    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT, trust_env=False) as client:
        yield client


def get_pricing_resolver():
    return default_resolver


def get_reader():
    return read_spines


def _managed_dep(app: FastAPI, dep):
    @asynccontextmanager
    async def factory():
        source = app.dependency_overrides.get(dep, dep)
        produced = source()
        if hasattr(produced, "__anext__"):
            try:
                yield await produced.__anext__()
            finally:
                await produced.aclose()
            return
        if asyncio.iscoroutine(produced) or hasattr(produced, "__await__"):
            produced = await produced
        yield produced

    return factory


def _dep_value(app: FastAPI, dep):
    return lambda: app.dependency_overrides.get(dep, dep)()


async def read_limited(request: Request, limit: int, label: str) -> bytes:
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > limit:
        raise HTTPException(status_code=413, detail=f"{label} too large")
    chunks = []
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > limit:
            raise HTTPException(status_code=413, detail=f"{label} too large")
        chunks.append(chunk)
    return b"".join(chunks)


def _sanitized_errors(errors):
    cleaned = []
    for err in errors:
        loc = [
            item if isinstance(item, (str, int)) else str(item)
            for item in err.get("loc", ())
        ]
        cleaned.append(
            {
                "loc": loc,
                "msg": str(err.get("msg", "invalid value")),
                "type": str(err.get("type", "value_error")),
            }
        )
    return cleaned


async def parse_json_body(request: Request, model):
    raw = await read_limited(request, MAX_JSON_BYTES, "JSON body")
    try:
        json.loads(raw)
    except ValueError:
        raise HTTPException(status_code=422, detail="invalid JSON")
    try:
        return model.model_validate_json(raw)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=_sanitized_errors(exc.errors()))


def create_app(runs_root: Path | None = None, *, allowed_origin: str | None = None) -> FastAPI:
    root = Path(runs_root) if runs_root is not None else PROJECT_ROOT / "runs"
    origin = (
        allowed_origin
        if allowed_origin is not None
        else os.environ.get("APP_ORIGIN", "http://localhost:8001")
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        inventory_provider = resolve_inventory_provider(os.environ.get("INVENTORY_PROVIDER"))
        store = SweepStore(root)
        app.state.store = store
        app.state.inventory_provider = inventory_provider
        vision_enabled = os.environ.get("VISION_ENABLED", "").strip().lower() in (
            "1",
            "true",
            "yes",
        )

        vision_contract = GROQ_VISION_CONTRACT if inventory_provider == "groq" else VISION_CONTRACT

        async def default_analyze(sweep_id, frame_id, client):
            if inventory_provider == "groq" and not os.environ.get("GROQ_API_KEY"):
                store.frame_path(sweep_id, frame_id)
                reason = "GROQ_API_KEY not configured"
                ref = store.persist_stage(
                    sweep_id,
                    f"vision_{frame_id}_{uuid.uuid4().hex}.json",
                    json.dumps(
                        {"frame_id": frame_id, "status": "failed", "provider": "groq", "error": reason}
                    ).encode(),
                )
                store.record_observation(
                    sweep_id,
                    frame_id,
                    {
                        "stage": VisionStageRecord(
                            frame_id=frame_id,
                            status="failed",
                            provider="groq",
                            persisted_ref=ref,
                            error=reason,
                        ),
                        "observation": None,
                    },
                )
                raise StoreError(503, reason)
            return await analyze_sweep_frame(
                store,
                sweep_id,
                frame_id,
                client=client,
                api_key=os.environ.get("GROQ_API_KEY" if inventory_provider == "groq" else "OPENAI_API_KEY"),
                contract=vision_contract,
                sha_cache=manager.sha_cache(sweep_id),
                tracking_contract=TRACKING_CONTRACT,
                **({"observer": observe_groq_jpeg} if inventory_provider == "groq" else {}),
            )

        def sha_for(sweep_id, frame_id):
            try:
                path = store.frame_path(sweep_id, frame_id)
            except StoreError:
                return None
            try:
                return hashlib.sha256(path.read_bytes()).hexdigest()
            except OSError:
                return None

        def record_skip(sweep_id, frame_id, reason):
            try:
                store.record_observation(
                    sweep_id,
                    frame_id,
                    {
                        "stage": VisionStageRecord(
                            frame_id=frame_id,
                            status="skipped",
                            persisted_ref=None,
                            model=None,
                            cost_usd_estimate=None,
                            elapsed_ms=None,
                            error=reason,
                        ),
                        "observation": None,
                    },
                )
            except StoreError:
                pass

        async def selected_groq_read_spines(
            target_store, sweep_id, *, client=None, api_key=None, contract=None,
            ref_ids=None, manager=None, **kwargs
        ):
            if api_key:
                return await read_spines(
                    target_store,
                    sweep_id,
                    client=client,
                    api_key=api_key,
                    contract=contract or GROQ_READING_CONTRACT,
                    ref_ids=ref_ids,
                    manager=manager or app.state.reading,
                    crop_provider=read_groq_crops,
                )
            state = target_store.get(sweep_id)
            if not state.books:
                raise StoreError(409, "no recorded book tracks; analyze a saved frame before reading spines")
            known = {book.id for book in state.books}
            if ref_ids is not None and set(ref_ids) - known:
                raise StoreError(404, "unknown reading target")
            targets = [
                book.id for book in state.books
                if not book.excluded and (ref_ids is None or book.id in ref_ids)
            ]
            reason = "GROQ_API_KEY not configured"
            target_store.record_reading_stage(
                sweep_id,
                ReadingStageRecord(ref_ids=targets, status="skipped", provider="groq", error=reason),
            )
            raise StoreError(503, reason)

        async def selected_groq_price_books(
            target_store, sweep_id, *, client=None, api_key=None, contract=None,
            book_ids=None, manager=None, resolver=None, **kwargs
        ):
            if api_key:
                return await price_books(
                    target_store,
                    sweep_id,
                    client=client,
                    api_key=api_key,
                    contract=contract or GROQ_PRICING_CONTRACT,
                    book_ids=book_ids,
                    manager=manager or app.state.pricing,
                    resolver=resolver,
                )
            state = target_store.get(sweep_id)
            known = {book.id for book in state.books}
            if book_ids is not None and set(book_ids) - known:
                raise StoreError(404, "unknown pricing target")
            reason = "GROQ_API_KEY not configured"
            target_store.record_price_stage(
                sweep_id,
                PricingStageRecord(
                    kind="discovery",
                    ref_ids=book_ids if book_ids is not None else [book.id for book in state.books],
                    status="skipped",
                    provider="groq",
                    error=reason,
                ),
            )
            raise StoreError(503, reason)

        app.state.groq_reader = selected_groq_read_spines
        app.state.groq_pricer = selected_groq_price_books
        manager = VisionManager(
            analyze=default_analyze,
            contract=vision_contract,
            enabled=vision_enabled,
            record_skip=record_skip,
            client_factory=lambda: httpx.AsyncClient(timeout=REQUEST_TIMEOUT),
            sha_for=sha_for,
        )
        app.state.vision = manager
        app.state.reading = ReadingManager(
            GROQ_READING_CONTRACT if inventory_provider == "groq" else READING_CONTRACT
        )
        app.state.pricing = PricingManager(
            GROQ_PRICING_CONTRACT if inventory_provider == "groq" else PRICING_CONTRACT
        )
        app.state.postwork = PostSweepProcessor(
            store=store,
            vision=manager,
            reading=app.state.reading,
            pricing=app.state.pricing,
            reader=(lambda: selected_groq_read_spines) if inventory_provider == "groq" else _dep_value(app, get_reader),
            pricer=selected_groq_price_books if inventory_provider == "groq" else price_books,
            resolver=_dep_value(app, get_pricing_resolver),
            api_key=lambda: os.environ.get("GROQ_API_KEY" if inventory_provider == "groq" else "OPENAI_API_KEY"),
            client_factory=_managed_dep(app, get_http_client),
            pricing_client_factory=_managed_dep(app, get_pricing_http_client),
            tracking_contract=TRACKING_CONTRACT,
        )
        app.state.vision_enabled = vision_enabled
        yield
        await app.state.postwork.shutdown()
        await app.state.vision.shutdown()

    app = FastAPI(title="library-claim-agent", lifespan=lifespan)
    app.state.allowed_origin = origin

    @app.middleware("http")
    async def restrict_client(request: Request, call_next):
        allowed_ip = os.environ.get("APP_ALLOWED_CLIENT_IP")
        if allowed_ip is not None and (
            request.client is None
            or request.client.host not in (allowed_ip, "127.0.0.1", "::1")
        ):
            return Response(status_code=403)
        return await call_next(request)

    def check_origin(request: Request) -> None:
        if request.headers.get("origin") != origin:
            raise HTTPException(status_code=403, detail="origin not allowed")

    def require_idle_postwork(sweep_id: str) -> None:
        if app.state.store.get(sweep_id).postwork_status == "running":
            raise HTTPException(status_code=409, detail="automatic processing is in progress")

    @app.exception_handler(StoreError)
    async def store_error(request: Request, exc: StoreError) -> JSONResponse:
        return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)

    @app.exception_handler(RequestValidationError)
    async def request_validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse({"detail": _sanitized_errors(exc.errors())}, status_code=422)

    @app.get("/", response_class=HTMLResponse)
    async def index() -> FileResponse:
        return FileResponse(WEB_DIR / "index.html")

    @app.get("/app.js")
    async def app_js() -> FileResponse:
        return FileResponse(WEB_DIR / "app.js", media_type="application/javascript")

    @app.get("/capture.js")
    async def capture_js() -> FileResponse:
        return FileResponse(WEB_DIR / "capture.js", media_type="application/javascript")

    @app.get("/lifecycle.js")
    async def lifecycle_js() -> FileResponse:
        return FileResponse(WEB_DIR / "lifecycle.js", media_type="application/javascript")

    @app.get("/proposals.js")
    async def proposals_js() -> FileResponse:
        return FileResponse(WEB_DIR / "proposals.js", media_type="application/javascript")

    @app.get("/guidance.js")
    async def guidance_js() -> FileResponse:
        return FileResponse(WEB_DIR / "guidance.js", media_type="application/javascript")

    @app.get("/voice.js")
    async def voice_js() -> FileResponse:
        return FileResponse(VOICE_PATH, media_type="application/javascript")

    @app.get("/api/health")
    async def health() -> dict:
        return {"ok": True, "openai_key_present": bool(os.environ.get("OPENAI_API_KEY"))}

    @app.get("/api/capture_config")
    async def capture_config() -> dict:
        vc = REALTIME_CONTRACT["visual_context"]
        gc = REALTIME_CONTRACT["guidance"]
        return {
            "vision_enabled": app.state.vision_enabled,
            "visual_context": {
                key: vc[key]
                for key in (
                    "maximum_images_per_sweep",
                    "minimum_image_interval_ms",
                    "maximum_frame_age_s",
                    "request_timeout_ms",
                    "maximum_message_bytes",
                    "maximum_targets_per_view",
                    "allow_upscale",
                    "automatic_retries",
                )
            },
            "guidance": {
                key: gc[key]
                for key in (
                    "enabled",
                    "maximum_responses_per_sweep",
                    "minimum_response_interval_ms",
                    "maximum_event_age_ms",
                )
            },
        }

    @app.post("/api/sweeps")
    async def start_sweep(request: Request):
        check_origin(request)
        body = await parse_json_body(request, StartSweepRequest)
        return app.state.store.start(body.country, body.currency, body.device)

    @app.get("/api/sweeps/{sweep_id}")
    async def get_sweep(sweep_id: str):
        return app.state.store.get(sweep_id)

    @app.post("/api/sweeps/{sweep_id}/frames")
    async def add_frame(sweep_id: str, request: Request, ts: float = Query(...)):
        check_origin(request)
        media_type = request.headers.get("content-type", "").split(";")[0].strip()
        if media_type != "image/jpeg":
            raise HTTPException(status_code=415, detail="expected image/jpeg")
        if not math.isfinite(ts) or ts < 0:
            raise HTTPException(status_code=422, detail="invalid timestamp")
        body = await read_limited(request, MAX_FRAME_BYTES, "frame")
        frame = app.state.store.add_frame(sweep_id, body, ts)
        app.state.vision.notify_frame(
            sweep_id, frame.id, hashlib.sha256(body).hexdigest()
        )
        return {"saved": True, "frame_id": frame.id, "frame_ref": frame.frame_ref}

    @app.get("/api/sweeps/{sweep_id}/frames/{frame_file}")
    async def get_frame(sweep_id: str, frame_file: str):
        if not re.fullmatch(r"frame_[0-9a-f]{32}\.jpg", frame_file):
            raise HTTPException(status_code=404, detail="unknown frame")
        frame_id = frame_file[:-4]
        frame_path = app.state.store.frame_path(sweep_id, frame_id)
        return FileResponse(frame_path, media_type="image/jpeg")

    @app.post("/api/sweeps/{sweep_id}/turns")
    async def add_turn(sweep_id: str, request: Request):
        check_origin(request)
        body = await parse_json_body(request, TurnRequest)
        return app.state.store.add_turn(sweep_id, body.turn_id, body.text, body.source)

    @app.post("/api/sweeps/{sweep_id}/corrections")
    async def correct(sweep_id: str, request: Request):
        check_origin(request)
        intent = await parse_json_body(request, CorrectionIntent)
        return app.state.store.correct(sweep_id, intent)

    @app.post("/api/sweeps/{sweep_id}/analyze_frame")
    async def analyze_frame(
        sweep_id: str,
        request: Request,
        client: httpx.AsyncClient = Depends(get_http_client),
    ):
        check_origin(request)
        require_idle_postwork(sweep_id)
        body = await parse_json_body(request, AnalyzeFrameRequest)
        return await app.state.vision.analyze_now(sweep_id, body.frame_id, client=client)

    @app.post("/api/sweeps/{sweep_id}/read_spines")
    async def read_spines_endpoint(
        sweep_id: str,
        request: Request,
        client: httpx.AsyncClient = Depends(get_http_client),
        reader=Depends(get_reader),
    ):
        check_origin(request)
        require_idle_postwork(sweep_id)
        body = await parse_json_body(request, ReadSpinesRequest)
        if app.state.inventory_provider == "groq":
            return await app.state.groq_reader(
                app.state.store,
                sweep_id,
                client=client,
                api_key=os.environ.get("GROQ_API_KEY"),
                ref_ids=body.ref_ids,
            )
        return await reader(
            app.state.store,
            sweep_id,
            client=client,
            api_key=os.environ.get("OPENAI_API_KEY"),
            contract=READING_CONTRACT,
            ref_ids=body.ref_ids,
            manager=app.state.reading,
        )

    @app.post("/api/sweeps/{sweep_id}/price_books")
    async def price_books_endpoint(
        sweep_id: str,
        request: Request,
        client: httpx.AsyncClient = Depends(get_pricing_http_client),
        resolver=Depends(get_pricing_resolver),
    ):
        check_origin(request)
        require_idle_postwork(sweep_id)
        body = await parse_json_body(request, PriceBooksRequest)
        if app.state.inventory_provider == "groq":
            return await app.state.groq_pricer(
                app.state.store,
                sweep_id,
                client=client,
                api_key=os.environ.get("GROQ_API_KEY"),
                book_ids=body.book_ids,
                resolver=resolver,
            )
        return await price_books(
            app.state.store,
            sweep_id,
            client=client,
            api_key=os.environ.get("OPENAI_API_KEY"),
            contract=PRICING_CONTRACT,
            book_ids=body.book_ids,
            manager=app.state.pricing,
            resolver=resolver,
        )

    @app.get("/api/sweeps/{sweep_id}/voice_view")
    async def voice_view(sweep_id: str, frame_id: str = Query(...)):
        return build_voice_view(
            app.state.store, sweep_id, frame_id, REALTIME_CONTRACT
        )

    @app.get("/api/sweeps/{sweep_id}/crops/{crop_file}")
    async def get_crop(sweep_id: str, crop_file: str):
        crop_path = app.state.store.crop_path(sweep_id, crop_file)
        return FileResponse(crop_path, media_type="image/jpeg")

    def packet_artifact(sweep_id: str, name: str, media_type: str):
        content, revision, stale = app.state.store.read_packet_artifact(
            sweep_id, name
        )
        return Response(
            content=content,
            media_type=media_type,
            headers={
                "x-packet-revision": str(revision),
                "x-packet-stale": "true" if stale else "false",
            },
        )

    @app.post("/api/sweeps/{sweep_id}/packet")
    async def publish_packet_endpoint(sweep_id: str, request: Request):
        check_origin(request)
        await parse_json_body(request, PacketRequest)
        store = app.state.store
        result = store.publish_packet(sweep_id)
        current = store.get(sweep_id).revision
        return {
            **result,
            "current_revision": current,
            "stale": result["revision"] != current,
        }

    @app.get("/api/sweeps/{sweep_id}/claim_packet.json")
    async def get_claim_packet(sweep_id: str):
        return packet_artifact(sweep_id, "claim_packet.json", "application/json")

    @app.get("/api/sweeps/{sweep_id}/report.html")
    async def get_report(sweep_id: str):
        return packet_artifact(sweep_id, "report.html", "text/html")

    @app.post("/api/sweeps/{sweep_id}/end")
    async def end_sweep(sweep_id: str, request: Request):
        check_origin(request)
        body = await parse_json_body(request, EndRequest)
        result = app.state.store.end(sweep_id, body.duration_s)
        app.state.vision.notify_ended(sweep_id)
        app.state.postwork.start(sweep_id)
        payload = result.model_dump(mode="json")
        payload["packet"] = None
        return payload

    @app.post("/api/sweeps/{sweep_id}/interrupt")
    async def interrupt_sweep(sweep_id: str, request: Request):
        check_origin(request)
        result = app.state.store.interrupt(sweep_id)
        app.state.vision.notify_ended(sweep_id)
        app.state.postwork.cancel(sweep_id)
        return result

    @app.post("/api/sweeps/{sweep_id}/realtime")
    async def realtime(
        sweep_id: str,
        request: Request,
        client: httpx.AsyncClient = Depends(get_http_client),
    ) -> Response:
        check_origin(request)
        state = app.state.store.get(sweep_id)
        if state.status != "capturing":
            raise HTTPException(status_code=409, detail="sweep is not capturing")
        media_type = request.headers.get("content-type", "").split(";")[0].strip()
        if media_type != "application/sdp":
            raise HTTPException(status_code=415, detail="expected application/sdp")
        body = await read_limited(request, MAX_SDP_BYTES, "SDP offer")
        if not body.strip():
            raise HTTPException(status_code=400, detail="empty SDP offer")
        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            raise HTTPException(status_code=503, detail="OPENAI_API_KEY not configured")
        session = dict(REALTIME_CONTRACT["session"])
        session["instructions"] = session["instructions"] + "\n" + json.dumps(
            {"country": state.country, "currency": state.currency}
        )
        try:
            upstream = await client.post(
                OPENAI_CALLS_URL,
                headers={"Authorization": f"Bearer {api_key}"},
                files={
                    "sdp": (None, body.decode("utf-8", "replace")),
                    "session": (None, json.dumps(session)),
                },
            )
        except httpx.HTTPError:
            raise HTTPException(status_code=502, detail="realtime provider unreachable")
        if not 200 <= upstream.status_code < 300:
            raise HTTPException(status_code=502, detail="realtime provider rejected offer")
        if not upstream.content.lstrip().startswith(b"v=0"):
            raise HTTPException(status_code=502, detail="realtime provider rejected offer")
        return Response(content=upstream.content, media_type="application/sdp")

    return app


app = create_app()
