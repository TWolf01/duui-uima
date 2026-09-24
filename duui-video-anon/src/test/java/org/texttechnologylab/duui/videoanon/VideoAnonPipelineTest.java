package org.texttechnologylab.duui.videoanon;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.Base64;
import java.util.List;

import org.apache.uima.fit.factory.JCasFactory;
import org.apache.uima.fit.util.JCasUtil;
import org.apache.uima.jcas.JCas;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.condition.EnabledIfEnvironmentVariable;
import org.texttechnologylab.DockerUnifiedUIMAInterface.DUUIComposer;
import org.texttechnologylab.annotation.type.Video;

/** Integration test against running DUUI face, speaker, and bridge services. */
class VideoAnonPipelineTest {
    private static String setting(String property, String environment, String fallback) {
        String value = System.getProperty(property);
        if (value == null || value.isBlank()) {
            value = System.getenv(environment);
        }
        return value == null || value.isBlank() ? fallback : value;
    }

    private static void assertWav(String audio) {
        assertNotNull(audio);
        assertFalse(audio.isBlank());
        byte[] bytes = Base64.getDecoder().decode(audio);
        assertTrue(bytes.length > 44);
        assertEquals("RIFF", new String(bytes, 0, 4, StandardCharsets.US_ASCII));
        assertEquals("WAVE", new String(bytes, 8, 4, StandardCharsets.US_ASCII));
    }

    @Test
    @EnabledIfEnvironmentVariable(named = "DUUI_RUN_INTEGRATION", matches = "true")
    void anonymizesVideoWithFaceAndSpeakerPipeline() throws Exception {
        Path input = Path.of(setting("duui.video.input", "DUUI_VIDEO_INPUT",
                "../duui-face_anon/duui/test/resources/input/videos/Trump.mp4"));
        byte[] inputBytes = Files.readAllBytes(input);
        assertTrue(inputBytes.length > 0);

        DUUIComposer composer = VideoAnonPipeline.createComposer(
                setting("duui.face.url", "DUUI_FACE_ANON_URL", "http://anduin.hucompute.org:42927"),
                setting("duui.video.url", "DUUI_VIDEO_ANON_URL", "http://anduin.hucompute.org:9715"),
                setting("duui.speaker.url", "DUUI_SPEAKER_ANON_URL", "http://anduin.hucompute.org:38455"),
                "redact", "blur", "", "en");
        try {
            JCas cas = JCasFactory.createJCas();
            cas.setDocumentText("video");
            cas.setDocumentLanguage("en");
            Video video = new Video(cas, 0, 5);
            video.setSrc(Base64.getEncoder().encodeToString(inputBytes));
            video.setMimetype("video/mp4");
            video.addToIndexes();

            composer.run(cas);

            List<Video> faceVideos = List.copyOf(
                    JCasUtil.select(cas.getView("face_anonymized"), Video.class));
            assertEquals(1, faceVideos.size());
            assertWav(cas.getView("extracted_audio").getSofaDataString());

            String voice = cas.getView("anonymized_audio").getSofaDataString();
            if (voice == null) {
                voice = cas.getView("opf_anonymized_audio").getSofaDataString();
            }
            assertWav(voice);

            List<Video> outputVideos = List.copyOf(JCasUtil.select(cas.getView("output"), Video.class));
            assertEquals(1, outputVideos.size());
            Video output = outputVideos.get(0);
            assertEquals(video.getBegin(), output.getBegin());
            assertEquals(video.getEnd(), output.getEnd());
            assertTrue(output.getLength() > 0);
            assertTrue(output.getFps() > 0);
            byte[] result = Base64.getDecoder().decode(output.getSrc());
            assertTrue(result.length > 1000);
            assertEquals("ftyp", new String(result, 4, 4, StandardCharsets.US_ASCII));

            Path destination = Path.of("target/test-output/video-anonymized.mp4");
            Files.createDirectories(destination.getParent());
            Files.write(destination, result);
        } finally {
            composer.shutdown();
        }
    }
}
