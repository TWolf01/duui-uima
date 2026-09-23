# DUUI Video Anonymization

Anonymizes faces with `duui-face_anon` and speech with `duui-speaker-anonymization`. Input is a base64 MP4 or WebM in a `Video` annotation; output is an MP4 `Video` annotation. Faces are blacked out by default. Videos without audio use only the face service.

## Run

From this directory:

```bash
podman build -t duui-video-anonymization:1.0 -f src/main/docker/Dockerfile .
podman run -d -p 9717 --name duui-video-anonymization localhost/duui-video-anonymization:1.0
podman port duui-video-anonymization 9717/tcp
```

Podman chooses the host port shown by `podman port`; use that port in the DUUI URL. It uses `http://anduin.hucompute.org:40581` for faces and `http://anduin.hucompute.org:38455` for speakers. Override these with `DUUI_FACE_ANON_URL` and `DUUI_SPEAKER_ANON_URL`; use `DUUI_DOWNSTREAM_TIMEOUT_SECONDS` for long recordings.

## DUUI usage

Put the base64 video in the `src` feature of a `Video` annotation. The output appears in the target view:

```java
composer.add(new DUUIRemoteDriver.Component("http://localhost:<published-port>")
    .withTargetView("anonymized_video")
    .build().withTimeout(1800));
```

The CAS language selects the speaker language (English if unspecified). Face options such as `anon_type`, `redact_type`, and `frame_interval` can be passed with `.withParameter(...)`; see the [face component](../duui-face_anon/README.md) for options. `single_align` and `multiple_align` require `hf_token`.

## Test

With the named container running, run:

```bash
mvn test
```

The Java test sends `src/test/resources/Ukrainian.webm` through the live face and speaker services and saves `target/test-output/ukrainian-anonymized.mp4`.

## Cite

Author: Tim Wolf. License: [AGPL-3.0](../LICENSE). Cite DUUI and this component:

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
