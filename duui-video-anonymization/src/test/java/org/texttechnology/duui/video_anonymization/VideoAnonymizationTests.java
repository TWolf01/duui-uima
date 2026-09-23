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

import java.net.ServerSocket;
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

/** Integration test using the real face and speaker services. */
class VideoAnonymizationTests {
    @Test
    void anonymizesWebmWithRealServices() throws Exception {
        Path input = Path.of("src/test/resources/Ukrainian.webm");
        assertTrue(Files.isRegularFile(input));
        byte[] inputBytes = Files.readAllBytes(input);
        assertTrue(inputBytes.length > 1000);

        int port;
        try (ServerSocket socket = new ServerSocket(0)) {
            port = socket.getLocalPort();
        }
        String url = "http://127.0.0.1:" + port;
        Path log = Path.of("target/test-output/component.log");
        Files.createDirectories(log.getParent());
        Path projectPython = Path.of(".venv/bin/python").toAbsolutePath();
        String python = Files.isExecutable(projectPython)
                ? projectPython.toString() : "python3";
        ProcessBuilder builder = new ProcessBuilder(
                python, "-m", "uvicorn", "duui_video_anonymization:app",
                "--host", "127.0.0.1", "--port", Integer.toString(port));
        builder.directory(Path.of("src/main/python").toFile());
        builder.redirectErrorStream(true);
        builder.redirectOutput(log.toFile());
        Process process = builder.start();

        DUUIComposer composer = null;
        try {
            awaitHealth(process, url, log);
            composer = new DUUIComposer()
                    .withSkipVerification(true)
                    .withLuaContext(new DUUILuaContext().withJsonLibrary());
            composer.addDriver(new DUUIRemoteDriver(), new DUUIUIMADriver());
            composer.add(new DUUIRemoteDriver.Component(url)
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
            double inputDuration = Double.parseDouble(new String(commandOutput(List.of(
                    "ffprobe", "-v", "error", "-show_entries", "format=duration",
                    "-of", "default=noprint_wrappers=1:nokey=1", input.toString())),
                    StandardCharsets.UTF_8).trim());
            assertEquals(inputDuration, result.getLength(), 2.0,
                    "Output should retain the full clip duration");
            assertTrue(result.getFps() > 0);
            Path output = Path.of("target/test-output/ukrainian-anonymized.mp4");
            Files.write(output, Base64.getDecoder().decode(result.getSrc()));
            String streams = new String(commandOutput(List.of(
                    "ffprobe", "-v", "error", "-show_entries", "stream=codec_type",
                    "-of", "csv=p=0", output.toString())), StandardCharsets.UTF_8);
            assertTrue(streams.contains("video"));
            assertTrue(streams.contains("audio"));
        } finally {
            if (composer != null) composer.shutdown();
            process.destroyForcibly();
        }
    }

    private static void awaitHealth(Process process, String url, Path log) throws Exception {
        HttpClient client = HttpClient.newHttpClient();
        for (int attempt = 0; attempt < 100; attempt++) {
            if (!process.isAlive()) break;
            try {
                HttpResponse<String> response = client.send(
                        HttpRequest.newBuilder(URI.create(url + "/v1/health"))
                                .timeout(Duration.ofSeconds(1)).GET().build(),
                        HttpResponse.BodyHandlers.ofString());
                if (response.statusCode() == 200) return;
            } catch (java.io.IOException ignored) {
                // The service is still starting.
            }
            Thread.sleep(100);
        }
        throw new IllegalStateException("Component did not start: " + Files.readString(log));
    }

    private static byte[] commandOutput(List<String> command) throws Exception {
        Process process = new ProcessBuilder(command).redirectErrorStream(true).start();
        byte[] output = process.getInputStream().readAllBytes();
        if (process.waitFor() != 0) {
            throw new IllegalStateException(String.join(" ", command) + ": "
                    + new String(output, StandardCharsets.UTF_8));
        }
        return output;
    }
}
