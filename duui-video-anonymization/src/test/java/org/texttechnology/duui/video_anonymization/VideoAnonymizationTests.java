package org.texttechnology.duui.video_anonymization;

import org.apache.uima.fit.factory.JCasFactory;
import org.apache.uima.fit.util.JCasUtil;
import org.apache.uima.jcas.JCas;
import org.junit.jupiter.api.Test;
import org.texttechnologylab.DockerUnifiedUIMAInterface.DUUIComposer;
import org.texttechnologylab.DockerUnifiedUIMAInterface.driver.DUUIRemoteDriver;
import org.texttechnologylab.DockerUnifiedUIMAInterface.driver.DUUIUIMADriver;
import org.texttechnologylab.DockerUnifiedUIMAInterface.lua.DUUILuaContext;
import org.texttechnologylab.annotation.type.Video;

import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.Duration;
import java.util.Base64;
import java.util.List;

import static org.junit.jupiter.api.Assertions.*;

/** Integration test against the running video-anonymization container. */
class VideoAnonymizationTests {
    private static final String URL = System.getProperty("duui.video.url", "http://127.0.0.1:9717");

    @Test
    void anonymizesWebmWithRealServices() throws Exception {
        HttpResponse<Void> health;
        try {
            health = HttpClient.newHttpClient().send(
                    HttpRequest.newBuilder(URI.create(URL + "/v1/health"))
                            .timeout(Duration.ofSeconds(5)).GET().build(),
                    HttpResponse.BodyHandlers.discarding());
        } catch (java.io.IOException e) {
            throw new IllegalStateException("Start duui-video-anonymization on port 9717 before running mvn test", e);
        }
        assertEquals(200, health.statusCode(), "Video component is not healthy");

        Path input = Path.of("src/test/resources/Ukrainian.webm");
        byte[] inputBytes = Files.readAllBytes(input);
        assertTrue(inputBytes.length > 1000);

        DUUIComposer composer = new DUUIComposer()
                .withSkipVerification(true)
                .withLuaContext(new DUUILuaContext().withJsonLibrary());
        try {
            composer.addDriver(new DUUIRemoteDriver(), new DUUIUIMADriver());
            composer.add(new DUUIRemoteDriver.Component(URL)
                    .withParameter("frame_interval", "25")
                    .withTargetView("output")
                    .build().withTimeout(1800));

            JCas cas = JCasFactory.createJCas();
            cas.setDocumentLanguage("eng");
            cas.setDocumentText("video");
            Video source = new Video(cas, 1, 4);
            source.setSrc(Base64.getEncoder().encodeToString(inputBytes));
            source.addToIndexes();
            cas.createView("output");
            composer.run(cas);

            List<Video> videos = List.copyOf(JCasUtil.select(cas.getView("output"), Video.class));
            assertEquals(1, videos.size());
            Video result = videos.get(0);
            assertEquals(1, result.getBegin());
            assertEquals(4, result.getEnd());
            assertEquals(34.233, result.getLength(), 2.0, "Output should retain the full clip duration");
            assertTrue(result.getFps() > 0);

            byte[] mp4 = Base64.getDecoder().decode(result.getSrc());
            assertTrue(mp4.length > 1000);
            assertEquals("ftyp", new String(mp4, 4, 4, StandardCharsets.US_ASCII));
            String media = new String(mp4, StandardCharsets.ISO_8859_1);
            assertTrue(media.contains("avc1"), "Output should contain H.264 video");
            assertTrue(media.contains("mp4a"), "Output should contain AAC audio");
            Path output = Path.of("target/test-output/ukrainian-anonymized.mp4");
            Files.createDirectories(output.getParent());
            Files.write(output, mp4);
        } finally {
            composer.shutdown();
        }
    }
}
