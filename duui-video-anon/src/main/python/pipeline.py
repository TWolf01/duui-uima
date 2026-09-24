"""Run the complete DUUI composer pipeline for one incoming video."""

from __future__ import annotations

import base64
import os
import subprocess
import tempfile
from pathlib import Path

from media import decode_media, probe


RUNNER_CLASS = "org.texttechnologylab.duui.videoanon.VideoAnonPipeline"
JAVA_CLASSPATH = os.getenv("DUUI_JAVA_CLASSPATH", "/app/java/classes:/app/java/lib/*")


def run_pipeline(video_b64: str, mimetype: str | None,
                 options: dict[str, str]) -> tuple[str, float, float]:
    raw = decode_media(video_b64, "video")
    if mimetype not in (None, "video/mp4", "video/webm"):
        raise ValueError("Input must be an MP4 or WebM video")

    face_url = os.getenv("DUUI_FACE_ANON_URL")
    speaker_url = os.getenv("DUUI_SPEAKER_ANON_URL")
    if not face_url or not speaker_url:
        raise ValueError("DUUI_FACE_ANON_URL and DUUI_SPEAKER_ANON_URL must be set")

    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        input_path = root / ("input.webm" if mimetype == "video/webm" else "input.mp4")
        output_path = root / "output.mp4"
        input_path.write_bytes(raw)
        probe(input_path)

        environment = os.environ.copy()
        environment["DUUI_VIDEO_ANON_URL"] = os.getenv(
            "DUUI_INTERNAL_URL", "http://127.0.0.1:9715")
        for key, setting in (
            ("anon_type", "DUUI_FACE_MODE"),
            ("redact_type", "DUUI_REDACT_TYPE"),
            ("language", "DUUI_LANGUAGE"),
            ("hf_token", "HF_TOKEN"),
        ):
            if options.get(key):
                environment[setting] = options[key]

        command = [
            "java", "--add-opens", "java.base/java.util=ALL-UNNAMED",
            "-cp", JAVA_CLASSPATH, RUNNER_CLASS,
            str(input_path), str(output_path),
        ]
        try:
            with (root / "pipeline.log").open("w+") as log:
                try:
                    subprocess.run(command, env=environment, check=True, timeout=7200,
                                   stdout=log, stderr=subprocess.STDOUT)
                except subprocess.CalledProcessError as exc:
                    log.seek(0)
                    detail = log.read()[-4000:].strip()
                    raise RuntimeError(
                        f"Internal DUUI pipeline failed with exit code {exc.returncode}: {detail}"
                    ) from exc
        except FileNotFoundError as exc:
            raise RuntimeError("Java 21 is required by duui-video-anon") from exc
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError("Internal DUUI pipeline timed out") from exc

        info = probe(output_path)
        return (base64.b64encode(output_path.read_bytes()).decode("ascii"),
                info["length"], info["fps"])
