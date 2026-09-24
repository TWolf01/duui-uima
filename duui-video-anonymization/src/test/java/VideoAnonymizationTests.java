import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.nio.file.Files;
import java.nio.file.Path;
import java.util.Base64;
import java.util.List;

import org.apache.uima.fit.factory.JCasFactory;
import org.apache.uima.fit.util.JCasUtil;
import org.apache.uima.jcas.JCas;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.texttechnologylab.DockerUnifiedUIMAInterface.DUUIComposer;
import org.texttechnologylab.DockerUnifiedUIMAInterface.driver.DUUIRemoteDriver;
import org.texttechnologylab.DockerUnifiedUIMAInterface.driver.DUUIUIMADriver;
import org.texttechnologylab.DockerUnifiedUIMAInterface.lua.DUUILuaContext;
import org.texttechnologylab.annotation.type.Video;

public class VideoAnonymizationTests {

    private static final String URL = "http://127.0.0.1:36591";
    private DUUIComposer composer;
    private JCas cas;

    @BeforeEach
    void setUp() throws Exception {
        composer = new DUUIComposer()
                .withSkipVerification(true)
                .withLuaContext(new DUUILuaContext().withJsonLibrary());
        composer.addDriver(new DUUIRemoteDriver(), new DUUIUIMADriver());
        cas = JCasFactory.createJCas();
    }

    @AfterEach
    void tearDown() throws Exception {
        composer.shutdown();
    }

    @Test
    void anonymizesUkrainianVideo() throws Exception {
        cas.setDocumentLanguage("en");
        cas.setDocumentText("video");

        Video input = new Video(cas, 0, 5);
        input.setSrc(Base64.getEncoder().encodeToString(
                Files.readAllBytes(Path.of("src/test/resources/Ukrainian.webm"))));
        input.setFps(25.0);
        input.setLength(34.233);
        input.addToIndexes();
        cas.createView("output");

        composer.add(new DUUIRemoteDriver.Component(URL)
                .withParameter("anon_type", "redact")
                .withParameter("redact_type", "black")
                .withParameter("frame_interval", "25")
                .withTargetView("output")
                .build().withTimeout(1800));
        composer.run(cas);

        List<Video> videos = List.copyOf(JCasUtil.select(cas.getView("output"), Video.class));
        assertEquals(1, videos.size());
        Video output = videos.get(0);
        assertFalse(output.getSrc().isBlank());
        assertTrue(output.getFps() > 0);
        assertTrue(output.getLength() > 0);
        assertEquals(input.getBegin(), output.getBegin());
        assertEquals(input.getEnd(), output.getEnd());

        Path result = Path.of("target/test-output/Ukrainian-anonymized.mp4");
        Files.createDirectories(result.getParent());
        Files.write(result, Base64.getDecoder().decode(output.getSrc()));
    }
}
