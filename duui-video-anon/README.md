# Video Anonymization DUUI

DUUI pipeline for anonymizing faces and voices in MP4 videos. It combines
[`duui-face_anon`](../duui-face_anon) and
[`duui-speaker-anonymization`](../duui-speaker-anonymization) with a media bridge
for audio extraction and remuxing. A Java `DUUIComposer` runs four stages:

1. Anonymize faces in the video.
2. Extract its audio as a WAV file.
3. Anonymize the speaker's voice.
4. Replace the video's audio with the anonymized voice.

Face redaction supports blur, blackout, and pixelation. Generative face
anonymization is also available with a Hugging Face token. The speaker component
supports English, German, French, Italian, Spanish, Portuguese, Dutch, Polish,
and Russian.

## Parameters

| Setting | Default | Description |
|---|---|---|
| `DUUI_FACE_MODE` | `redact` | `redact`, `single_align`, or `multiple_align` |
| `DUUI_REDACT_TYPE` | `blur` | `blur`, `black`, or `pixel` when redacting faces |
| `HF_TOKEN` | empty | Required for generative face anonymization |
| `DUUI_LANGUAGE` | `en` | Language passed to speaker anonymization |
| `DUUI_FACE_ANON_URL` | `http://127.0.0.1:9714` | Face component URL |
| `DUUI_VIDEO_ANON_URL` | `http://127.0.0.1:9715` | Media bridge URL |
| `DUUI_SPEAKER_ANON_URL` | `http://127.0.0.1:9716` | Speaker component URL |
| `MAX_MEDIA_BYTES` | 500 MiB | Maximum decoded media size accepted by the bridge |

The runner sets face sampling to `uniform` with `frame_interval=1` to keep the
video timeline aligned with the audio. See the face component's README for its
additional model settings when building a custom DUUI pipeline. Set
`MAX_MEDIA_BYTES` in the bridge container.

## How To Use

This component requires the
[Docker Unified UIMA Interface (DUUI)](https://github.com/texttechnologylab/DockerUnifiedUIMAInterface),
Java 21, Maven, and running face and speaker components. The speaker container
needs its model files. Face redaction does not need an HF token.

### Build and start the media bridge

From the `duui-video-anon` directory:

```bash
docker build -t duui-video-anon -f src/main/docker/Dockerfile .
docker run --rm -p 9715:9715 duui-video-anon
```

The bridge exposes the standard DUUI `/v1/typesystem`,
`/v1/communication_layer`, `/v1/documentation`, and `/v1/process` endpoints.
Its health endpoint is `/v1/health`.

### Run the video pipeline

Start the face and speaker components as described in their READMEs. The
speaker container listens on port 9714, so publish it on a different host port
when the face component also uses 9714.

This example uses services published on `anduin.hucompute.org` and writes one
anonymized MP4 file:

```bash
DUUI_FACE_ANON_URL=http://anduin.hucompute.org:42927 \
DUUI_VIDEO_ANON_URL=http://anduin.hucompute.org:9715 \
DUUI_SPEAKER_ANON_URL=http://anduin.hucompute.org:38455 \
mvn -q compile exec:exec \
  -Dexec.args="--add-opens java.base/java.util=ALL-UNNAMED -cp %classpath org.texttechnologylab.duui.videoanon.VideoAnonPipeline input.mp4 output.mp4"
```

`input.mp4` must be an MP4 video. The runner creates one CAS containing one
`Video` annotation and writes the `output` view's video to `output.mp4`.

### Use within DUUI

The [pipeline runner](src/main/java/org/texttechnologylab/duui/videoanon/VideoAnonPipeline.java)
and [Java integration test](src/test/java/org/texttechnologylab/duui/videoanon/VideoAnonPipelineTest.java)
show the full setup. Add these remote components to a `DUUIComposer` with a JSON
Lua context:

```java
composer.add(new DUUIRemoteDriver.Component(faceUrl)
    .withParameter("anon_type", "redact")
    .withParameter("redact_type", "blur")
    .withParameter("sampling_mode", "uniform")
    .withParameter("frame_interval", "1")
    .withTargetView("face_anonymized")
    .build().withTimeout(3600));

composer.add(new DUUIRemoteDriver.Component(bridgeUrl)
    .withParameter("operation", "extract")
    .withSourceView("face_anonymized")
    .withTargetView("extracted_audio")
    .build().withTimeout(3600));

composer.add(new DUUIRemoteDriver.Component(speakerUrl)
    .withParameter("language", "en")
    .withSourceView("extracted_audio")
    .withTargetView("anonymized_audio")
    .build().withTimeout(3600));

composer.add(new DUUIRemoteDriver.Component(bridgeUrl)
    .withParameter("operation", "mux")
    .withParameter("audio_view", "anonymized_audio")
    .withSourceView("face_anonymized")
    .withTargetView("output")
    .build().withTimeout(3600));
```

## Input and Output

| Direction | CAS view | Data |
|---|---|---|
| Input | `_InitialView` | Base64 MP4 in `org.texttechnologylab.annotation.type.Video.src` |
| Intermediate | `face_anonymized` | Face-anonymized `Video` |
| Intermediate | `extracted_audio` | Base64 WAV in the sofa |
| Intermediate | `anonymized_audio` or `opf_anonymized_audio` | Base64 WAV in the sofa |
| Output | `output` | Base64 MP4 in a `Video` annotation |

Some speaker images write the WAV to `opf_anonymized_audio`; the repository
version writes to the configured target view. The mux stage accepts both.

The bridge extracts the first audio stream as a 16 kHz mono WAV. It maps only
the face-anonymized video and new audio into the output, pads or trims the new
audio to the video duration, and never copies the original voice. Silent videos
remain silent. If the speaker result is empty for a video with audio, muxing
fails. Resynthesized speech can differ from the source video in word timing.

Media is Base64 encoded throughout the pipeline, so the composer and services
need enough memory for the video and audio data.

## Tests

The Java integration test uses the face component's video fixture, checks the
CAS views, and saves `target/test-output/video-anonymized.mp4`. With all three
services running, enable it with:

```bash
DUUI_RUN_INTEGRATION=true mvn -q -Dtest=VideoAnonPipelineTest test
```

For a shorter run, pass `-Dduui.video.input=/path/to/short.mp4`. Override the
three service URLs with the environment variables above or Maven properties
`duui.face.url`, `duui.video.url`, and `duui.speaker.url`.

The separate Python media tests require FFmpeg and FFprobe:

```bash
PYTHONPATH=src/main/python python -m unittest discover -s src/test/python -v
```

They generate short synthetic videos and verify that the replacement audio is
present and the original audio is absent.

## Cite

If you use this component, please cite DUUI:

Alexander Leonhardt, Giuseppe Abrami, Daniel Baumartz and Alexander Mehler.
(2023). “Unlocking the Heterogeneous Landscape of Big Data NLP with DUUI.”
Findings of the Association for Computational Linguistics: EMNLP 2023,
385–399. [Paper](https://aclanthology.org/2023.findings-emnlp.29).

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
```

This component is distributed under the repository's [AGPL-3.0 license](../LICENSE).
