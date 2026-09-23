package org.texttechnology.duui.video_anonymization;

import org.json.JSONArray;
import org.json.JSONObject;
import org.junit.jupiter.api.Test;

import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.Duration;
import java.util.Base64;

import static org.junit.jupiter.api.Assertions.*;

/** Integration test against the running video-anonymization container. */
class VideoAnonymizationTests {
    private static String componentUrl() throws Exception {
        String configured = System.getProperty("duui.video.url");
        if (configured != null) return configured;

        Process process = new ProcessBuilder(
                "podman", "port", "duui-video-anonymization", "9717/tcp")
                .redirectErrorStream(true)
                .start();
        String binding = new String(process.getInputStream().readAllBytes(), StandardCharsets.UTF_8)
                .strip().split("\\R")[0];
        if (process.waitFor() != 0 || !binding.contains(":")) {
            throw new IllegalStateException(
                    "Start the named video container with: podman run -d -p 9717 "
                    + "--name duui-video-anonymization localhost/duui-video-anonymization:1.0 "
                    + "(podman port returned: " + binding + ")");
        }
        int port = Integer.parseInt(binding.substring(binding.lastIndexOf(':') + 1));
        return "http://127.0.0.1:" + port;
    }

    @Test
    void anonymizesWebmWithRealServices() throws Exception {
        String url = componentUrl();
        HttpClient client = HttpClient.newBuilder()
                .connectTimeout(Duration.ofSeconds(5))
                .build();
        try {
            HttpResponse<Void> health = client.send(
                    HttpRequest.newBuilder(URI.create(url + "/v1/health"))
                            .timeout(Duration.ofSeconds(5)).GET().build(),
                    HttpResponse.BodyHandlers.discarding());
            assertEquals(200, health.statusCode(), "Video component is not healthy");
        } catch (java.io.IOException e) {
            throw new IllegalStateException("Cannot reach duui-video-anonymization at " + url + " before running mvn test", e);
        }

        Path input = Path.of("src/test/resources/Ukrainian.webm");
        byte[] inputBytes = Files.readAllBytes(input);
        assertTrue(inputBytes.length > 1000);
        JSONObject video = new JSONObject()
                .put("src", Base64.getEncoder().encodeToString(inputBytes))
                .put("begin", 1)
                .put("end", 4);
        JSONObject request = new JSONObject()
                .put("videos", new JSONArray().put(video))
                .put("options", new JSONObject().put("language", "en").put("frame_interval", 25));
        HttpResponse<String> response = client.send(
                HttpRequest.newBuilder(URI.create(url + "/v1/process"))
                        .timeout(Duration.ofMinutes(30))
                        .header("Content-Type", "application/json")
                        .POST(HttpRequest.BodyPublishers.ofString(request.toString()))
                        .build(),
                HttpResponse.BodyHandlers.ofString());
        assertEquals(200, response.statusCode(), () -> "Processing failed: " + response.body());

        JSONArray videos = new JSONObject(response.body()).getJSONArray("output_videos");
        assertEquals(1, videos.length());
        JSONObject result = videos.getJSONObject(0);
        assertEquals(1, result.getInt("begin"));
        assertEquals(4, result.getInt("end"));
        assertEquals(34.233, result.getDouble("length"), 2.0,
                "Output should retain the full clip duration");
        assertTrue(result.getDouble("fps") > 0);

        byte[] mp4 = Base64.getDecoder().decode(result.getString("src"));
        assertTrue(mp4.length > 1000);
        assertEquals("ftyp", new String(mp4, 4, 4, StandardCharsets.US_ASCII));
        String media = new String(mp4, StandardCharsets.ISO_8859_1);
        assertTrue(media.contains("avc1"), "Output should contain H.264 video");
        assertTrue(media.contains("mp4a"), "Output should contain AAC audio");
        Path output = Path.of("target/test-output/ukrainian-anonymized.mp4");
        Files.createDirectories(output.getParent());
        Files.write(output, mp4);
    }
}
