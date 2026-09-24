package org.texttechnologylab.duui.videoanon;

import java.nio.file.Files;
import java.nio.file.Path;
import java.util.Base64;
import java.util.List;

import org.apache.uima.fit.factory.JCasFactory;
import org.apache.uima.fit.util.JCasUtil;
import org.apache.uima.jcas.JCas;
import org.texttechnologylab.DockerUnifiedUIMAInterface.DUUIComposer;
import org.texttechnologylab.DockerUnifiedUIMAInterface.driver.DUUIRemoteDriver;
import org.texttechnologylab.DockerUnifiedUIMAInterface.lua.DUUILuaContext;
import org.texttechnologylab.annotation.type.Video;

/** Runs the four remote DUUI stages for a single MP4 document. */
public final class VideoAnonPipeline {
    private VideoAnonPipeline() { }

    private static String setting(String name, String defaultValue) {
        String value = System.getenv(name);
        return value == null || value.isBlank() ? defaultValue : value;
    }

    public static void main(String[] args) throws Exception {
        if (args.length != 2) {
            throw new IllegalArgumentException("Usage: VideoAnonPipeline input.mp4 output.mp4");
        }
        Path input = Path.of(args[0]);
        Path output = Path.of(args[1]);
        String mode = setting("DUUI_FACE_MODE", "redact");
        if (!List.of("redact", "single_align", "multiple_align").contains(mode)) {
            throw new IllegalArgumentException("DUUI_FACE_MODE must be redact, single_align, or multiple_align");
        }
        String token = setting("HF_TOKEN", "");
        if (!mode.equals("redact") && token.isBlank()) {
            throw new IllegalArgumentException("HF_TOKEN is required for generative face anonymization");
        }

        DUUIComposer composer = createComposer(
                setting("DUUI_FACE_ANON_URL", "http://127.0.0.1:9714"),
                setting("DUUI_VIDEO_ANON_URL", "http://127.0.0.1:9715"),
                setting("DUUI_SPEAKER_ANON_URL", "http://127.0.0.1:9716"),
                mode, setting("DUUI_REDACT_TYPE", "black"), token,
                setting("DUUI_LANGUAGE", "en"));
        try {
            JCas cas = JCasFactory.createJCas();
            cas.setDocumentText("video");
            cas.setDocumentLanguage(setting("DUUI_LANGUAGE", "en"));
            Video video = new Video(cas, 0, 5);
            video.setSrc(Base64.getEncoder().encodeToString(Files.readAllBytes(input)));
            video.setMimetype("video/mp4");
            video.addToIndexes();

            composer.run(cas);
            List<Video> videos = List.copyOf(JCasUtil.select(cas.getView("output"), Video.class));
            if (videos.size() != 1 || videos.get(0).getSrc() == null
                    || videos.get(0).getSrc().isBlank()) {
                throw new IllegalStateException("Pipeline produced no anonymized video");
            }
            Files.write(output, Base64.getDecoder().decode(videos.get(0).getSrc()));
        } finally {
            composer.shutdown();
        }
    }

    static DUUIComposer createComposer(String faceUrl, String bridgeUrl, String speakerUrl,
                                       String mode, String redactType, String token,
                                       String language) throws Exception {
        DUUIComposer composer = new DUUIComposer()
                .withSkipVerification(true)
                .withLuaContext(new DUUILuaContext().withJsonLibrary());
        composer.addDriver(new DUUIRemoteDriver());
        composer.add(new DUUIRemoteDriver.Component(faceUrl)
                    .withName("face-anonymization")
                    .withParameter("anon_type", mode)
                    .withParameter("redact_type", redactType)
                    .withParameter("hf_token", token)
                    .withParameter("sampling_mode", "uniform")
                    .withParameter("frame_interval", "1")
                    .withTargetView("face_anonymized")
                    .build().withTimeout(3600));

        composer.add(new DUUIRemoteDriver.Component(bridgeUrl)
                    .withName("extract-audio")
                    .withParameter("operation", "extract")
                    .withSourceView("face_anonymized")
                    .withTargetView("extracted_audio")
                    .build().withTimeout(3600));

        composer.add(new DUUIRemoteDriver.Component(speakerUrl)
                    .withName("speaker-anonymization")
                    .withParameter("language", language)
                    .withSourceView("extracted_audio")
                    .withTargetView("anonymized_audio")
                    .build().withTimeout(3600));

        composer.add(new DUUIRemoteDriver.Component(bridgeUrl)
                    .withName("mux-anonymized-audio")
                    .withParameter("operation", "mux")
                    .withParameter("audio_view", "anonymized_audio")
                    .withSourceView("face_anonymized")
                    .withTargetView("output")
                    .build().withTimeout(3600));
        return composer;
    }

}
