package org.texttechnologylab.duui.videoanon;

import java.io.InvalidObjectException;
import java.io.OutputStream;
import java.io.PrintStream;
import java.net.ConnectException;
import java.net.UnknownHostException;
import java.net.http.HttpTimeoutException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.Base64;
import java.util.List;
import java.util.Locale;
import java.util.regex.Pattern;

import org.apache.uima.fit.factory.JCasFactory;
import org.apache.uima.fit.util.JCasUtil;
import org.apache.uima.jcas.JCas;
import org.luaj.vm2.LuaError;
import org.texttechnologylab.DockerUnifiedUIMAInterface.DUUIComposer;
import org.texttechnologylab.DockerUnifiedUIMAInterface.driver.DUUIRemoteDriver;
import org.texttechnologylab.DockerUnifiedUIMAInterface.exception.CommunicationLayerException;
import org.texttechnologylab.DockerUnifiedUIMAInterface.lua.DUUILuaContext;
import org.texttechnologylab.annotation.type.Video;

/** Runs one remote DUUI stage; Python handles the local media stages. */
public final class VideoAnonPipeline {
    // DUUI 1.5.5 includes the HTTP status and the raw upstream body in this exception.
    private static final Pattern HTTP_ERROR = Pattern.compile("^Expected response 200, got ([1-5][0-9]{2}):");

    private VideoAnonPipeline() { }

    private static String setting(String name, String defaultValue) {
        String value = System.getenv(name);
        return value == null || value.isBlank() ? defaultValue : value;
    }

    private static String requiredUrl(String name) {
        String value = System.getenv(name);
        if (value == null || value.isBlank()) {
            throw new IllegalArgumentException(name + " must be set");
        }
        return value;
    }

    public static void main(String[] args) {
        if (args.length != 3 || !List.of("face", "speaker").contains(args[0])) {
            throw new IllegalArgumentException(
                    "Usage: VideoAnonPipeline face|speaker input.mp4|input.wav output.mp4|output.wav");
        }
        PrintStream diagnostic = System.err;
        // Only the diagnostic code below should reach the parent process.
        System.setErr(new PrintStream(OutputStream.nullOutputStream()));
        try {
            Path input = Path.of(args[1]);
            Path output = Path.of(args[2]);
            if (args[0].equals("face")) {
                runFace(input, output);
            } else {
                runSpeaker(input, output);
            }
        } catch (Exception | LinkageError failure) {
            diagnostic.println("DUUI_STAGE_ERROR " + failureCode(failure));
            System.exit(1);
        }
    }

    private static String failureCode(Throwable failure) {
        for (Throwable cause = failure; cause != null; cause = cause.getCause()) {
            String message = cause.getMessage();
            if (cause instanceof InvalidObjectException && message != null) {
                var status = HTTP_ERROR.matcher(message);
                if (status.find()) {
                    return "http_" + status.group(1);
                }
            }
            if (cause instanceof MissingOutputException) {
                return "missing_output";
            }
            if (cause instanceof HttpTimeoutException) {
                return "timeout";
            }
            if (cause instanceof ConnectException || cause instanceof UnknownHostException
                    || "Could not reach endpoint after 50 tries!".equals(message)) {
                return "unreachable";
            }
            if (cause instanceof CommunicationLayerException && message != null) {
                if (message.startsWith("Caught LuaError while calling serialize(")) {
                    return "serialize";
                }
                if (message.startsWith("Caught LuaError while calling deserialize(")) {
                    return "deserialize";
                }
            }
            if (cause instanceof LuaError) {
                return "lua";
            }
            if (cause instanceof LinkageError) {
                return "java_dependencies";
            }
        }
        return "unknown";
    }

    private static final class MissingOutputException extends Exception { }

    private static void runFace(Path input, Path output) throws Exception {
        String mode = setting("DUUI_FACE_MODE", "redact");
        if (!List.of("redact", "single_align", "multiple_align").contains(mode)) {
            throw new IllegalArgumentException("DUUI_FACE_MODE must be redact, single_align, or multiple_align");
        }
        String token = setting("HF_TOKEN", "");
        if (!mode.equals("redact") && token.isBlank()) {
            throw new IllegalArgumentException("HF_TOKEN is required for generative face anonymization");
        }

        String faceUrl = requiredUrl("DUUI_FACE_ANON_URL");
        DUUIComposer composer = newComposer();
        var component = new DUUIRemoteDriver.Component(faceUrl)
                .withName("face-anonymization")
                .withParameter("anon_type", mode)
                .withParameter("redact_type", setting("DUUI_REDACT_TYPE", "black"))
                .withParameter("sampling_mode", setting("DUUI_FACE_SAMPLING_MODE", "uniform"))
                .withParameter("frame_interval", setting("DUUI_FACE_FRAME_INTERVAL", "1"))
                .withTargetView("face_anonymized");
        for (String key : List.of("segment_duration", "representative_frames", "face_type",
                "blur", "pixel", "anon_degree", "diffusion_model", "clip_model", "seed",
                "guidance", "inference_steps", "model_ttl_seconds", "model_cache_size")) {
            String value = setting("DUUI_FACE_" + key.toUpperCase(Locale.ROOT), "");
            if (!value.isEmpty()) {
                component.withParameter(key, value);
            }
        }
        if (!mode.equals("redact")) {
            component.withParameter("hf_token", token);
        }
        composer.add(component.build().withTimeout(3600));
        try {
            JCas cas = JCasFactory.createJCas();
            Video video = new Video(cas, 0, 0);
            video.setSrc(Base64.getEncoder().encodeToString(Files.readAllBytes(input)));
            video.setMimetype(input.getFileName().toString().toLowerCase().endsWith(".webm")
                    ? "video/webm" : "video/mp4");
            video.addToIndexes();

            composer.run(cas);
            List<Video> videos = List.copyOf(JCasUtil.select(cas.getView("face_anonymized"), Video.class));
            if (videos.size() != 1 || videos.get(0).getSrc() == null
                    || videos.get(0).getSrc().isBlank()) {
                throw new MissingOutputException();
            }
            Files.write(output, Base64.getDecoder().decode(videos.get(0).getSrc()));
        } finally {
            composer.shutdown();
        }
    }

    private static void runSpeaker(Path input, Path output) throws Exception {
        String speakerUrl = requiredUrl("DUUI_SPEAKER_ANON_URL");
        DUUIComposer composer = newComposer();
        composer.add(new DUUIRemoteDriver.Component(speakerUrl)
                .withName("speaker-anonymization")
                .withParameter("language", setting("DUUI_LANGUAGE", "en"))
                .withTargetView("anonymized_audio")
                .build().withTimeout(3600));
        try {
            JCas cas = JCasFactory.createJCas();
            cas.setSofaDataString(Base64.getEncoder().encodeToString(Files.readAllBytes(input)), "audio/wav");
            composer.run(cas);

            String audio = null;
            for (String viewName : List.of("anonymized_audio", "opf_anonymized_audio")) {
                try {
                    audio = cas.getView(viewName).getSofaDataString();
                } catch (Exception ignored) {
                    // Older speaker images write to a fixed output view.
                }
                if (audio != null) {
                    break;
                }
            }
            if (audio == null) {
                throw new MissingOutputException();
            }
            Files.write(output, Base64.getDecoder().decode(audio));
        } finally {
            composer.shutdown();
        }
    }

    private static DUUIComposer newComposer() throws Exception {
        DUUIComposer composer = new DUUIComposer()
                .withSkipVerification(true)
                .withLuaContext(new DUUILuaContext().withJsonLibrary());
        composer.addDriver(new DUUIRemoteDriver());
        return composer;
    }

}
