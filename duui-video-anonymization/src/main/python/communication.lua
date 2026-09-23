StandardCharsets = luajava.bindClass("java.nio.charset.StandardCharsets")
Class = luajava.bindClass("java.lang.Class")
JCasUtil = luajava.bindClass("org.apache.uima.fit.util.JCasUtil")
Video = Class:forName("org.texttechnologylab.annotation.type.Video")

local OPTION_KEYS = {
    "language", "anon_type", "redact_type", "face_type", "blur", "pixel",
    "sampling_mode", "frame_interval", "segment_duration", "representative_frames",
    "anon_degree", "diffusion_model", "clip_model", "seed", "guidance",
    "inference_steps", "vis_input", "height", "width", "hf_token"
}

local function parameter(params, key)
    if params == nil then return nil end
    local value = params[key]
    if value == nil then
        local ok, result = pcall(function() return params:get(key) end)
        if ok then value = result end
    end
    if value == nil then return nil end
    return tostring(value)
end

function serialize(inputCas, outputStream, parameters)
    local videos = {}
    local iterator = JCasUtil:select(inputCas, Video):iterator()
    while iterator:hasNext() do
        local video = iterator:next()
        videos[#videos + 1] = {
            src = video:getSrc(),
            length = video:getLength(),
            fps = video:getFps(),
            begin = video:getBegin(),
            ['end'] = video:getEnd()
        }
    end

    local options = {}
    for _, key in ipairs(OPTION_KEYS) do
        local value = parameter(parameters, key)
        if value ~= nil then options[key] = value end
    end
    if options.language == nil then
        local language = inputCas:getDocumentLanguage()
        if language ~= nil and language ~= "" and language ~= "x-unspecified" then
            options.language = language
        end
    end
    outputStream:write(json.encode({videos = videos, options = options}))
end

function deserialize(inputCas, inputStream)
    local inputString = luajava.newInstance(
        "java.lang.String", inputStream:readAllBytes(), StandardCharsets.UTF_8)
    local result = json.decode(inputString)
    for _, data in ipairs(result.output_videos or {}) do
        local video = luajava.newInstance("org.texttechnologylab.annotation.type.Video", inputCas)
        video:setSrc(data.src)
        video:setLength(data.length)
        video:setFps(data.fps)
        video:setBegin(data.begin)
        video:setEnd(data['end'])
        video:addToIndexes()
    end
    for _, warning in ipairs(result.warnings or {}) do
        local comment = luajava.newInstance("org.texttechnologylab.annotation.AnnotationComment", inputCas)
        comment:setKey("warning")
        comment:setValue(warning)
        comment:addToIndexes()
    end
end
