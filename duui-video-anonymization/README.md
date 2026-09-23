# DUUI Video Anonymization

This component accepts base64-encoded MP4 or WebM videos in `org.texttechnologylab.annotation.type.Video` annotations. It calls `duui-face_anon` for the video and `duui-speaker-anonymization` for the audio, then returns an MP4 containing the processed video and anonymized audio.

The default face mode is `redact` with `black`. Other face modes can be selected with DUUI parameters; `single_align` and `multiple_align` require an `hf_token`. The speaker service supports `en`, `de`, `fr`, `it`, `es`, `pt`, `nl`, `pl`, and `ru`. ISO 639-3 codes used by UIMA, such as `eng` and `deu`, are accepted. When the CAS has no language, English is used.

## Run

Build the image from this directory:

```bash
podman build -t duui-video-anonymization:1.0 -f src/main/docker/Dockerfile .
```

The face and speaker services must be reachable by the orchestrator. Their default URLs are `http://anduin.hucompute.org:40581` and `http://anduin.hucompute.org:38455`; set `DUUI_FACE_ANON_URL` and `DUUI_SPEAKER_ANON_URL` to change them. Set `DUUI_DOWNSTREAM_TIMEOUT_SECONDS` for long recordings. For example:

```bash
podman run --rm -p 9717:9717 \
  --name duui-video-anonymization duui-video-anonymization:1.0
```

The service is available at `http://localhost:9717/v1/health`.

## DUUI usage

Put the base64 MP4 or WebM in the `src` feature of a `Video` annotation. The output is a new `Video` annotation in the configured target view, with the original begin/end offsets and measured duration/FPS:

```java
composer.add(
    new DUUIRemoteDriver.Component("http://localhost:9717")
        .withParameter("language", "de")
        .withTargetView("anonymized_video")
        .build().withTimeout(1800)
);
```

Face parameters passed through to `duui-face_anon` include `face_type`, `blur`, `pixel`, `sampling_mode`, `frame_interval`, `segment_duration`, `representative_frames`, `anon_degree`, `diffusion_model`, `clip_model`, `seed`, `guidance`, `inference_steps`, `height`, `width`, and `hf_token`.

If the input has no audio stream, only the face service is called and the returned video has no audio. For videos with audio, the original audio is removed. The anonymized audio is padded or cut to the video duration during remuxing. A downstream failure returns an HTTP error and no output video.

## Tests

The Java test starts the Python component and calls the configured face and speaker services. It needs Java 21, Maven, `ffmpeg`, and `ffprobe` on `PATH`. From this directory, create a Python 3.12 environment and run the test with that interpreter:

```bash
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -r requirements.txt
mvn -Dtest=VideoAnonymizationTests test
```

The test uses `.venv/bin/python` automatically and falls back to `python3` if the virtual environment is absent. Ensure `ffmpeg` and `ffprobe` are available on `PATH`.

The test uses `src/test/resources/Ukrainian.webm`, processes one frame per second to keep the run practical, and writes `target/test-output/ukrainian-anonymized.mp4`.

## Cite

Author: Tim Wolf. This component is distributed under the repository's
[AGPL-3.0 license](../LICENSE).

If you use this component, please cite DUUI and the video anonymization component:

```bibtex
@inproceedings{Leonhardt:et:al:2023,
  title     = {Unlocking the Heterogeneous Landscape of Big Data {NLP} with {DUUI}},
  author    = {Leonhardt, Alexander and Abrami, Giuseppe and Baumartz, Daniel and Mehler, Alexander},
  booktitle = {Findings of the Association for Computational Linguistics: EMNLP 2023},
  year      = {2023},
  publisher = {Association for Computational Linguistics},
  url       = {https://aclanthology.org/2023.findings-emnlp.29},
  pages     = {385--399}
}

@misc{wolf2026duuivideoanonymization,
  author       = {Wolf, Tim},
  title        = {Video Anonymization as {DUUI} Component},
  year         = {2026},
  howpublished = {\url{https://github.com/texttechnologylab/duui-uima/tree/main/duui-video-anonymization}}
}
```
