"""Coordinate remote DUUI face and speaker stages with local media processing."""

from __future__ import annotations

import base64
import math
import os
import re
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

from duui_logging import log_info

from media import decode_media, extract_audio, mux_audio, probe


RUNNER_CLASS = "org.texttechnologylab.duui.videoanon.VideoAnonPipeline"
JAVA_CLASSPATH = os.getenv("DUUI_JAVA_CLASSPATH", "/app/java/classes:/app/java/lib/*")

OPTION_ENV = {
    "anon_type": "DUUI_FACE_MODE",
    "redact_type": "DUUI_REDACT_TYPE",
    "language": "DUUI_LANGUAGE",
    "hf_token": "HF_TOKEN",
    "sampling_mode": "DUUI_FACE_SAMPLING_MODE",
    "frame_interval": "DUUI_FACE_FRAME_INTERVAL",
    "segment_duration": "DUUI_FACE_SEGMENT_DURATION",
    "representative_frames": "DUUI_FACE_REPRESENTATIVE_FRAMES",
    "face_type": "DUUI_FACE_FACE_TYPE",
    "blur": "DUUI_FACE_BLUR",
    "pixel": "DUUI_FACE_PIXEL",
    "anon_degree": "DUUI_FACE_ANON_DEGREE",
    "diffusion_model": "DUUI_FACE_DIFFUSION_MODEL",
    "clip_model": "DUUI_FACE_CLIP_MODEL",
    "seed": "DUUI_FACE_SEED",
    "guidance": "DUUI_FACE_GUIDANCE",
    "inference_steps": "DUUI_FACE_INFERENCE_STEPS",
    "model_ttl_seconds": "DUUI_FACE_MODEL_TTL_SECONDS",
    "model_cache_size": "DUUI_FACE_MODEL_CACHE_SIZE",
}

CHOICES = {
    "anon_type": ("redact", "single_align", "multiple_align"),
    "sampling_mode": ("uniform", "adaptive"),
    "redact_type": ("blur", "black", "pixel"),
    "face_type": ("half_face", "midfull_face", "full_face", "full_face_no_align",
                  "whole_face", "whole_face_no_align", "head", "head_no_align"),
    "language": ("en", "de", "fr", "it", "es", "pt", "nl", "pl", "ru"),
}

INTEGER_MINIMUMS = {
    "frame_interval": 1,
    "representative_frames": 1,
    "blur": 1,
    "pixel": 1,
    "inference_steps": 1,
    "model_ttl_seconds": 0,
    "model_cache_size": 1,
}


def stage_environment(options: dict[str, str]) -> dict[str, str]:
    dimensions = {"width", "height", "output_width", "output_height"}
    if dimensions.intersection(options):
        raise ValueError("Custom output dimensions are unsupported for video")
    if "vis_input" in options:
        if options["vis_input"].strip().lower() not in ("false", "0", "no", "off", "n", "f"):
            raise ValueError("vis_input must be false for video")
    if options.keys() - OPTION_ENV.keys() - {"mode", "vis_input"}:
        raise ValueError("Unsupported video anonymization option")

    options = options.copy()
    if "anon_type" not in options and "mode" in options:
        options["anon_type"] = options["mode"]
    environment = os.environ.copy()
    for key, name in OPTION_ENV.items():
        if key in options:
            value = options[key].strip()
            if "\x00" in value or (not value and key != "hf_token"):
                raise ValueError(f"{key} must contain a valid non-empty value")
            if value:
                environment[name] = value

    settings = {key: environment.get(name, "").strip() for key, name in OPTION_ENV.items()}
    for key, choices in CHOICES.items():
        if settings[key] and settings[key] not in choices:
            raise ValueError(f"{key} must be one of: {', '.join(choices)}")
        if settings[key]:
            environment[OPTION_ENV[key]] = settings[key]

    integers = {}
    for key in (*INTEGER_MINIMUMS, "seed"):
        if settings[key]:
            try:
                integers[key] = int(settings[key])
            except ValueError:
                raise ValueError(f"{key} must be an integer") from None
            if key in INTEGER_MINIMUMS and integers[key] < INTEGER_MINIMUMS[key]:
                raise ValueError(f"{key} must be an integer >= {INTEGER_MINIMUMS[key]}")
            environment[OPTION_ENV[key]] = str(integers[key])
    if integers.get("blur", 1) % 2 == 0:
        raise ValueError("blur must be an odd integer")

    for key in ("segment_duration", "anon_degree", "guidance"):
        if settings[key]:
            try:
                number = float(settings[key])
            except ValueError:
                number = math.nan
            if not math.isfinite(number) or (key == "segment_duration" and number <= 0):
                requirement = "a positive finite number" if key == "segment_duration" else "a finite number"
                raise ValueError(f"{key} must be {requirement}")
            environment[OPTION_ENV[key]] = str(number)

    if settings["anon_type"] in ("single_align", "multiple_align"):
        if settings["hf_token"] in ("", "None"):
            raise ValueError("hf_token is required for generative face anonymization")
    else:
        environment.pop("HF_TOKEN", None)
    environment.update(required_service_urls())
    return environment


def required_service_urls() -> dict[str, str]:
    urls = {}
    for name in ("DUUI_FACE_ANON_URL", "DUUI_SPEAKER_ANON_URL"):
        value = os.getenv(name, "").strip()
        try:
            parsed = urlsplit(value)
            port = parsed.port
            valid = (parsed.scheme in ("http", "https") and parsed.hostname is not None
                     and parsed.username is None and parsed.password is None
                     and (port is None or port > 0) and not parsed.query
                     and not parsed.fragment and not any(c.isspace() for c in value))
        except ValueError:
            valid = False
        if not valid:
            raise RuntimeError(f"{name} must be an explicit HTTP(S) service URL")
        urls[name] = value
    return urls


def run_java_stage(stage: str, input_path: Path, output_path: Path,
                   environment: dict[str, str]) -> None:
    log_info(f"Starting DUUI {stage} stage")
    command = [
        "java", "--add-opens", "java.base/java.util=ALL-UNNAMED",
        "-cp", JAVA_CLASSPATH, RUNNER_CLASS,
        stage, str(input_path), str(output_path),
    ]
    try:
        # Discard DUUI output. The Java runner emits a fixed diagnostic code on stderr.
        subprocess.run(command, env=environment, check=True, timeout=7200,
                       stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    except subprocess.CalledProcessError as exc:
        match = re.fullmatch(rb"DUUI_STAGE_ERROR ([a-z0-9_]+)\r?\n", exc.stderr or b"")
        code = match[1].decode("ascii") if match else ""
        status = re.fullmatch(r"http_([1-5][0-9]{2})", code)
        if status:
            detail = f"upstream /v1/process returned HTTP {status[1]}"
        else:
            media_kind = "video" if stage == "face" else "audio"
            detail = {
                "missing_output": f"the upstream service returned no {media_kind}. Check its logs.",
                "timeout": "upstream request timed out",
                "unreachable": "the runner could not reach the upstream service",
                "serialize": "upstream Lua failed to serialize the input CAS",
                "deserialize": "upstream Lua failed to deserialize the response",
                "lua": "upstream Lua communication layer failed",
                "java_dependencies": "the Java runner could not load a dependency",
            }.get(code)
        if detail:
            raise RuntimeError(f"DUUI {stage} stage failed because {detail}") from None
        raise RuntimeError(f"DUUI {stage} stage failed with exit code {exc.returncode}") from None
    except FileNotFoundError as exc:
        raise RuntimeError("Java 21 is required by duui-video-anon") from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"DUUI {stage} stage timed out") from exc
    log_info(f"Completed DUUI {stage} stage")


def run_pipeline(video_b64: str, mimetype: str | None,
                 options: dict[str, str]) -> tuple[str, float, float]:
    environment = stage_environment(options)
    raw = decode_media(video_b64, "video")
    if mimetype not in (None, "video/mp4", "video/webm"):
        raise ValueError("Input must be an MP4 or WebM video")

    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        input_path = root / ("input.webm" if mimetype == "video/webm" else "input.mp4")
        input_path.write_bytes(raw)
        probe(input_path)

        face_path = root / "face.mp4"
        run_java_stage("face", input_path, face_path, environment)
        face_video = base64.b64encode(face_path.read_bytes()).decode("ascii")
        audio = extract_audio(face_video)

        anonymized_audio = ""
        if audio:
            audio_path = root / "extracted.wav"
            audio_path.write_bytes(base64.b64decode(audio, validate=True))
            speaker_path = root / "anonymized.wav"
            speaker_environment = environment.copy()
            speaker_environment.pop("HF_TOKEN", None)
            run_java_stage("speaker", audio_path, speaker_path, speaker_environment)
            anonymized_audio = base64.b64encode(speaker_path.read_bytes()).decode("ascii")
        else:
            log_info("Skipping speaker stage for silent video")

        return mux_audio(face_video, anonymized_audio)
