import json
import math
import os
import re
import time
from pathlib import Path

import httpx
from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response

BASE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = BASE_DIR.parents[1]
INDEX_PATH = BASE_DIR / "index.html"
VOICE_PATH = BASE_DIR / "voice.js"
OPENAI_CALLS_URL = "https://api.openai.com/v1/realtime/calls"
MAX_SDP_BYTES = 65536
MAX_FRAME_BYTES = 5 * 1024 * 1024
REQUEST_TIMEOUT = 30.0
RUN_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
JPEG_MAGIC = b"\xff\xd8\xff"

SESSION_CONFIG = {
    "type": "realtime",
    "model": "gpt-realtime-2.1",
    "audio": {"output": {"voice": "marin"}},
    "instructions": (
        "You are helping test live camera and voice concurrency. Speak briefly. "
        "On request, speak for several seconds so the user can interrupt you. "
        "Do not claim to see images, count books, measure objects, or retrieve "
        "prices in this test."
    ),
}

app = FastAPI(title="preflight-safari")


@app.middleware("http")
async def restrict_client(request: Request, call_next):
    allowed_ip = os.environ.get("PREFLIGHT_ALLOWED_CLIENT_IP")
    allowed_clients = (allowed_ip, "127.0.0.1", "::1") if allowed_ip else ("127.0.0.1", "::1", "testclient")
    if request.client is None or request.client.host not in allowed_clients:
        return Response(status_code=403)
    return await call_next(request)


app.state.default_run_id = time.strftime("run-%Y%m%d-%H%M%S")
app.state.frame_counters = {}


async def get_http_client() -> httpx.AsyncClient:
    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
        yield client


def allowed_origin() -> str:
    return os.environ.get("PREFLIGHT_ORIGIN", "http://localhost:8000")


def runs_dir() -> Path:
    return Path(
        os.environ.get(
            "PREFLIGHT_RUNS_DIR", str(PROJECT_ROOT / "runs" / "preflight-safari")
        )
    )


def check_origin(request: Request) -> None:
    if request.headers.get("origin") != allowed_origin():
        raise HTTPException(status_code=403, detail="origin not allowed")


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


@app.get("/", response_class=HTMLResponse)
async def index() -> HTMLResponse:
    return HTMLResponse(INDEX_PATH.read_text())


@app.get("/voice.js")
async def voice_js() -> FileResponse:
    return FileResponse(VOICE_PATH, media_type="application/javascript")


@app.get("/preflight/health")
async def health() -> JSONResponse:
    return JSONResponse(
        {"ok": True, "openai_key_present": bool(os.environ.get("OPENAI_API_KEY"))}
    )


@app.post("/preflight/session")
async def create_session(
    request: Request, client: httpx.AsyncClient = Depends(get_http_client)
) -> Response:
    check_origin(request)
    media_type = request.headers.get("content-type", "").split(";")[0].strip()
    if media_type != "application/sdp":
        raise HTTPException(status_code=415, detail="expected application/sdp")
    body = await read_limited(request, MAX_SDP_BYTES, "SDP offer")
    if not body.strip():
        raise HTTPException(status_code=400, detail="empty SDP offer")
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise HTTPException(status_code=503, detail="OPENAI_API_KEY not configured")
    model = os.environ.get("REALTIME_MODEL")
    session = dict(SESSION_CONFIG)
    if model:
        session["model"] = model
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


@app.post("/preflight/frame")
async def save_frame(
    request: Request,
    ts: float = Query(...),
    run_id: str | None = Query(None),
) -> JSONResponse:
    check_origin(request)
    media_type = request.headers.get("content-type", "").split(";")[0].strip()
    if media_type != "image/jpeg":
        raise HTTPException(status_code=415, detail="expected image/jpeg")
    body = await read_limited(request, MAX_FRAME_BYTES, "frame")
    if not body.startswith(JPEG_MAGIC):
        raise HTTPException(status_code=422, detail="not a JPEG image")
    if not math.isfinite(ts) or ts < 0:
        raise HTTPException(status_code=422, detail="invalid timestamp")
    if run_id is None:
        run_id = app.state.default_run_id
    elif not RUN_ID_RE.match(run_id):
        raise HTTPException(status_code=400, detail="invalid run_id")
    counters = app.state.frame_counters
    index = counters.get(run_id, 0)
    target = runs_dir() / run_id
    target.mkdir(parents=True, exist_ok=True)
    while True:
        frame_id = f"frame_{index:06d}"
        try:
            with open(target / f"{frame_id}.jpg", "xb") as f:
                f.write(body)
        except FileExistsError:
            index += 1
            continue
        try:
            with open(target / f"{frame_id}.json", "xb") as f:
                f.write(
                    json.dumps({"frame_id": frame_id, "timestamp_s": ts}).encode()
                )
        except FileExistsError:
            index += 1
            continue
        break
    counters[run_id] = index + 1
    return JSONResponse({"saved": True, "frame_id": frame_id})


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        app,
        host=os.environ.get("PREFLIGHT_HOST", "127.0.0.1"),
        port=int(os.environ.get("PREFLIGHT_PORT", "8000")),
    )
