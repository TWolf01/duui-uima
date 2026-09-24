import json
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import PlainTextResponse, Response
from pydantic import BaseModel, ValidationError

from media import extract_audio, mux_audio


ROOT = Path(__file__).parent
app = FastAPI(title="duui-video-anon", version="1.0.0")


class Video(BaseModel):
    src: str
    length: float = -1
    fps: float = -1
    begin: int = 0
    end: int = 0


class ProcessRequest(BaseModel):
    operation: Literal["extract", "mux"]
    video: Video
    audio: str | None = None


@app.get("/v1/typesystem")
def typesystem() -> Response:
    return Response((ROOT / "typesystem.xml").read_bytes(), media_type="application/xml")


@app.get("/v1/communication_layer", response_class=PlainTextResponse)
def communication_layer() -> str:
    return (ROOT / "communication.lua").read_text(encoding="utf-8")


@app.get("/v1/documentation")
def documentation() -> dict:
    return {"annotator_name": "duui-video-anon", "version": "1.0.0",
            "implementation_lang": "Python"}


@app.get("/v1/details/input_output")
def input_output() -> dict:
    return {"inputs": ["org.texttechnologylab.annotation.type.Video"],
            "outputs": ["org.texttechnologylab.annotation.type.Video"]}


@app.get("/v1/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/v1/process")
async def process(raw_request: Request) -> dict:
    try:
        data = await raw_request.json()
        # DUUI's Lua output stream can arrive as a JSON-encoded string.
        if isinstance(data, str):
            data = json.loads(data)
        request = ProcessRequest.model_validate(data)
    except (json.JSONDecodeError, ValidationError, ValueError) as exc:
        raise HTTPException(status_code=422, detail="Invalid DUUI media request") from exc
    try:
        if request.operation == "extract":
            return {"operation": "extract", "audio": extract_audio(request.video.src)}
        if request.audio is None:
            raise ValueError("Mux requires anonymized audio")
        src, length, fps = mux_audio(request.video.src, request.audio)
        return {"operation": "mux", "video": {
            "src": src, "length": length, "fps": fps,
            "begin": request.video.begin, "end": request.video.end
        }}
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
