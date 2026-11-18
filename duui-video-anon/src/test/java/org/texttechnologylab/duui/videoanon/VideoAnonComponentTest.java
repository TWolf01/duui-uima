package org.texttechnologylab.duui.videoanon;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.io.ByteArrayOutputStream;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.Base64;
import java.util.List;
import java.util.Map;

import org.apache.uima.fit.factory.JCasFactory;
import org.apache.uima.fit.util.JCasUtil;
import org.apache.uima.jcas.JCas;
import org.json.JSONObject;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.condition.EnabledIfEnvironmentVariable;
import org.texttechnologylab.DockerUnifiedUIMAInterface.DUUIComposer;
import org.texttechnologylab.DockerUnifiedUIMAInterface.driver.DUUIRemoteDriver;
import org.texttechnologylab.DockerUnifiedUIMAInterface.lua.DUUILuaCommunicationLayer;
import org.texttechnologylab.DockerUnifiedUIMAInterface.lua.DUUILuaContext;
import org.texttechnologylab.annotation.type.Video;

/** Integration test against the video anonymization component as one DUUI service. */
class VideoAnonComponentTest {
    @Test
    void serializesVideoOptionsWithoutDuuiDriverSettings() throws Exception {
        var options = Map.of("sampling_mode", "adaptive", "frame_interval", "3",
                "segment_duration", "2.5", "representative_frames", "4", "mode", "redact",
                "language", "de", "width", "720");
        var component = new DUUIRemoteDriver.Component("http://localhost");
        options.forEach(component::withParameter);
        var parameters = component.build().withTimeout(3600).getParameters();

        var layer = new DUUILuaCommunicationLayer(
                Files.readString(Path.of("src/main/python/communication.lua")), "video",
                new DUUILuaContext().withJsonLibrary());
        JCas cas = JCasFactory.createJCasFromPath("src/main/python/typesystem.xml");
        Video video = new Video(cas, 0, 0);
        video.setSrc("dmlkZW8=");
        video.addToIndexes();

        var output = new ByteArrayOutputStream();
        layer.serialize(cas, output, parameters, "_InitialView");
        var request = new JSONObject(output.toString(StandardCharsets.UTF_8));
        assertEquals(options, request.getJSONObject("options").toMap());
    }

    private static String setting(String property, String environment, String fallback) {
        String value = System.getProperty(property);
        if (value == null || value.isBlank()) {
            value = System.getenv(environment);
        }
        return value == null || value.isBlank() ? fallback : value;
    }

    @Test
    @EnabledIfEnvironmentVariable(named = "DUUI_RUN_INTEGRATION", matches = "true")
    void anonymizesVideoThroughSingleComponent() throws Exception {
        String override = setting("duui.video.input", "DUUI_VIDEO_INPUT", "");
        Path input;
        if (override.isBlank()) {
            var resource = VideoAnonComponentTest.class.getResource("/videos/hope.webm");
            assertNotNull(resource, "Missing test video: /videos/hope.webm"); //Source: https://commons.wikimedia.org/wiki/File:%27We_have_to_give_the_Ukrainian_people_hope%27.webm World Economic Forum, CC BY 3.0 <https://creativecommons.org/licenses/by/3.0>, via Wikimedia Commons
            input = Path.of(resource.toURI());
        } else {
            input = Path.of(override);
        }
        byte[] inputBytes = Files.readAllBytes(input);
        assertTrue(inputBytes.length > 0);

        DUUIComposer composer = new DUUIComposer()
                .withSkipVerification(true)
                .withLuaContext(new DUUILuaContext().withJsonLibrary());
        composer.addDriver(new DUUIRemoteDriver());
        composer.add(new DUUIRemoteDriver.Component(
                    setting("duui.video.url", "DUUI_VIDEO_ANON_URL",
                            "http://anduin.hucompute.org:9715"))
                .withName("video-anonymization")
                .withParameter("anon_type", "redact")
                .withParameter("redact_type", "black")
                .withParameter("language", "en")
                .withTargetView("output")
                .build().withTimeout(7200));
        try {
            JCas cas = JCasFactory.createJCas();
            cas.setDocumentText("video");
            cas.setDocumentLanguage("en");
            Video video = new Video(cas, 0, 5);
            video.setSrc(Base64.getEncoder().encodeToString(inputBytes));
            video.setMimetype(input.getFileName().toString().endsWith(".webm")
                    ? "video/webm" : "video/mp4");
            video.addToIndexes();

            composer.run(cas);

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
