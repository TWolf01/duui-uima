"""DUUI adapter that combines the face and speaker anonymization services."""

from __future__ import annotations

import base64
import binascii
import json
import os
import subprocess
import tempfile
from fractions import Fraction
from pathlib import Path
from typing import Any, Literal

import httpx
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import PlainTextResponse
from starlette.concurrency import run_in_threadpool
from pydantic import BaseModel, Field, ValidationError

HERE = Path(__file__).resolve().parent
FACE_URL = os.getenv("DUUI_FACE_ANON_URL", "http://anduin.hucompute.org:40581").rstrip("/")
SPEAKER_URL = os.getenv(
    "DUUI_SPEAKER_ANON_URL", "http://anduin.hucompute.org:38455"
).rstrip("/")
TIMEOUT_SECONDS = float(os.getenv("DUUI_DOWNSTREAM_TIMEOUT_SECONDS", "1800"))

LANGUAGE_CODES = {
    "eng": "en", "deu": "de", "ger": "de", "fra": "fr", "fre": "fr",
    "ita": "it", "spa": "es", "por": "pt", "nld": "nl", "dut": "nl",
    "pol": "pl", "rus": "ru",
}
SUPPORTED_LANGUAGES = {"en", "de", "fr", "it", "es", "pt", "nl", "pl", "ru"}


class Video(BaseModel):
    src: str
    length: float = -1
    fps: float = -1
    begin: int
    end: int


class Options(BaseModel):
    language: str = "en"
    anon_type: Literal["redact", "single_align", "multiple_align"] = "redact"
    redact_type: Literal["blur", "black", "pixel"] = "black"
    face_type: str = "full_face"
    blur: int = 51
    pixel: int = 16
    sampling_mode: Literal["uniform", "adaptive"] = "uniform"
    frame_interval: int = Field(default=1, ge=1)
    segment_duration: float = Field(default=10.0, gt=0)
    representative_frames: int = Field(default=5, ge=1)
    anon_degree: float = 1.25
    diffusion_model: str = "sd2-community/stable-diffusion-2-1"
    clip_model: str = "openai/clip-vit-large-patch14"
    seed: int = 1
    guidance: float = 4.0
    inference_steps: int = 25
    vis_input: bool = False
    height: int | None = None
    width: int | None = None
    hf_token: str = "None"


class DUUIRequest(BaseModel):
    videos: list[Video] = Field(default_factory=list)
    options: Options = Field(default_factory=Options)


class DUUIResponse(BaseModel):
    output_videos: list[Video]
    warnings: list[str] = Field(default_factory=list)


app = FastAPI(
    docs_url="/api",
    redoc_url=None,
    title="DUUI Video Anonymization",
    description="Video and speaker anonymization for TTLab DUUI",
    version="1.0",
    terms_of_service="https://www.texttechnologylab.org/legal_notice/",
    contact={
        "name": "Tim Wolf",
        "url": "https://www.texttechnologylab.org",
        "email": "T.Wolf@em.uni-frankfurt.de",
    },
    license_info={
        "name": "AGPL",
        "url": "http://www.gnu.org/licenses/agpl-3.0.en.html",
    },
)


@app.get("/v1/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/v1/documentation")
def documentation() -> dict[str, str]:
    return {
        "annotator_name": "DUUI Video Anonymization",
        "version": "1.0",
        "implementation_lang": "Python",
    }


@app.get("/v1/details/input_output")
def input_output() -> dict[str, list[str]]:
    video_type = "org.texttechnologylab.annotation.type.Video"
    return {"inputs": [video_type], "outputs": [video_type]}


@app.get("/v1/typesystem")
def typesystem() -> Response:
    return Response((HERE / "typesystem.xml").read_bytes(), media_type="application/xml")


@app.get("/v1/communication_layer", response_class=PlainTextResponse)
def communication_layer() -> str:
    return (HERE / "communication.lua").read_text(encoding="utf-8")


def _run(command: list[str]) -> bytes:
    try:
        result = subprocess.run(
            command, check=True, capture_output=True, timeout=TIMEOUT_SECONDS
        )
        return result.stdout
    except FileNotFoundError as exc:
        raise HTTPException(500, f"Required media tool is missing: {command[0]}") from exc
    except subprocess.TimeoutExpired as exc:
        raise HTTPException(504, f"Media processing timed out: {command[0]}") from exc
    except subprocess.CalledProcessError as exc:
        message = exc.stderr.decode("utf-8", errors="replace").strip()[-1000:]
        raise HTTPException(422, f"{command[0]} failed: {message}") from exc


def _probe(path: Path) -> tuple[float, float, bool]:
    raw = _run([
        "ffprobe", "-v", "error", "-show_entries",
        "stream=codec_type,avg_frame_rate:format=duration", "-of", "json", str(path),
    ])
    try:
        data = json.loads(raw)
        streams = data["streams"]
        video_stream = next(s for s in streams if s["codec_type"] == "video")
        duration = float(data["format"]["duration"])
        fps = float(Fraction(video_stream["avg_frame_rate"]))
        if duration <= 0 or fps <= 0:
            raise ValueError("nonpositive duration or frame rate")
        return duration, fps, any(s["codec_type"] == "audio" for s in streams)
    except (KeyError, ValueError, ZeroDivisionError, StopIteration) as exc:
        raise HTTPException(422, "Video has no usable video stream or duration") from exc


def _decode(value: str, label: str) -> bytes:
    try:
        decoded = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise HTTPException(422, f"{label} is not valid base64") from exc
    if not decoded:
        raise HTTPException(422, f"{label} is empty")
    return decoded


def _post(client: httpx.Client, url: str, payload: dict[str, Any], service: str) -> dict[str, Any]:
    try:
        response = client.post(url + "/v1/process", json=payload)
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict):
            raise ValueError("response is not a JSON object")
        return data
    except httpx.TimeoutException as exc:
        raise HTTPException(504, f"{service} timed out") from exc
    except (httpx.HTTPError, ValueError) as exc:
        raise HTTPException(502, f"{service} failed: {exc}") from exc


def _language(value: str) -> str:
    code = value.strip().lower()
    code = LANGUAGE_CODES.get(code, code)
    if code not in SUPPORTED_LANGUAGES:
        raise HTTPException(422, f"Unsupported audio language: {value}")
    return code


def _process_video(video: Video, options: Options, client: httpx.Client) -> tuple[Video, list[str]]:
    with tempfile.TemporaryDirectory(prefix="duui-video-anonymization-") as directory:
        work = Path(directory)
        source = work / "source.mp4"
        source.write_bytes(_decode(video.src, "Video src"))
        _, _, has_audio = _probe(source)

        audio_path = work / "audio.wav"
        if has_audio:
            _run([
                "ffmpeg", "-v", "error", "-nostdin", "-i", str(source),
                "-map", "0:a:0", "-vn", "-ac", "1", "-ar", "16000",
                "-c:a", "pcm_s16le", "-y", str(audio_path),
            ])
            speaker_result = _post(client, SPEAKER_URL, {
                "audio": base64.b64encode(audio_path.read_bytes()).decode("ascii"),
                "options": {"language": _language(options.language)},
            }, "Speaker anonymization")
            anonymized_audio = speaker_result.get("anonymized_audio")
            if not isinstance(anonymized_audio, str):
                raise HTTPException(502, "Speaker anonymization returned no audio")
            audio_path.write_bytes(_decode(anonymized_audio, "Anonymized audio"))

        face_payload = options.model_dump(exclude={"language"})
        face_payload.update({"images": [], "videos": [video.model_dump()]})
        face_result = _post(client, FACE_URL, face_payload, "Face anonymization")
        videos = face_result.get("output_videos")
        if not isinstance(videos, list) or len(videos) != 1:
            errors = face_result.get("out_errors") or []
            raise HTTPException(502, f"Face anonymization returned no video: {errors}")
        try:
            face_video = Video.model_validate(videos[0])
        except ValidationError as exc:
            raise HTTPException(502, "Face anonymization returned invalid video metadata") from exc
        face_path = work / "face.mp4"
        face_path.write_bytes(_decode(face_video.src, "Face-anonymized video"))
        duration, _, _ = _probe(face_path)

        output_path = work / "output.mp4"
        command = [
            "ffmpeg", "-v", "error", "-nostdin", "-i", str(face_path),
        ]
        if has_audio:
            command += ["-i", str(audio_path)]
        command += ["-map", "0:v:0"]
        if has_audio:
            command += [
                "-map", "1:a:0", "-c:a", "aac", "-af", "apad", "-t", str(duration),
            ]
        else:
            command += ["-an"]
        command += [
            "-c:v", "copy", "-map_metadata", "-1", "-map_chapters", "-1",
            "-movflags", "+faststart", "-y", str(output_path),
        ]
        _run(command)
        length, fps, final_has_audio = _probe(output_path)
        if has_audio and not final_has_audio:
            raise HTTPException(502, "Finished video has no anonymized audio")
        if not has_audio and final_has_audio:
            raise HTTPException(502, "Finished video unexpectedly contains audio")

        warnings = face_result.get("out_errors") or []
        return Video(
            src=base64.b64encode(output_path.read_bytes()).decode("ascii"),
            length=length, fps=fps, begin=video.begin, end=video.end,
        ), [str(warning) for warning in warnings]


@app.post("/v1/process", response_model=DUUIResponse)
async def process(raw_request: Request) -> DUUIResponse:
    try:
        data = json.loads(await raw_request.body())
        if isinstance(data, str):
            data = json.loads(data)
        request = DUUIRequest.model_validate(data)
    except (json.JSONDecodeError, ValidationError) as exc:
        raise HTTPException(422, "Invalid DUUI request") from exc
    return await run_in_threadpool(_process_request, request)


def _process_request(request: DUUIRequest) -> DUUIResponse:
    if not request.videos:
        raise HTTPException(422, "No Video annotations provided")
    output: list[Video] = []
    warnings: list[str] = []
    with httpx.Client(timeout=TIMEOUT_SECONDS) as client:
        for video in request.videos:
            result, video_warnings = _process_video(video, request.options, client)
            output.append(result)
            warnings.extend(video_warnings)
    return DUUIResponse(output_videos=output, warnings=warnings)
