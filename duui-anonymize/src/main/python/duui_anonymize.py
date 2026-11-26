from __future__ import annotations

import gc
import logging
import json
import re
import threading
import time
from collections import OrderedDict, defaultdict
from itertools import pairwise
from typing import Any, List, NamedTuple, Optional
from unicodedata import category, normalize

import pycountry
import torch
import uvicorn
from cassis import load_typesystem
from faker import Faker
from faker.config import AVAILABLE_LOCALES, DEFAULT_LOCALE
from faker.utils.loading import find_available_locales
from fastapi import FastAPI, Request, Response
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, PlainTextResponse
from huggingface_hub import hf_hub_download
from pydantic import BaseModel, Field, field_validator
from pydantic_settings import BaseSettings
from transformers import TextClassificationPipeline, TokenClassificationPipeline
from transformers import pipeline as hf_pipeline
from unicodedataplus import script

from arabic_latin_names import ArabicLatinNameProvider

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

DEFAULT_MODEL = "openai/privacy-filter"
NAMEBERT_MODEL = "jxins/nameBERT-nationality-classification"
NAMEBERT_REVISION = "0149d5f4f2d14f9a02f8baf65fdc4b05ea141ea2"

SUPPORTED_MODELS = {
    "openai/privacy-filter",
    "OpenMed/privacy-filter-nemotron",
    "bardsai/eu-pii-anonimization-multilang",
}

MODE_REMOVE = "remove"
MODE_PLACEHOLDER = "placeholder"  # default: replace with [category]
MODE_PSEUDO = "pseudo"

NAME_PART_LABELS = {"first_name", "last_name"}
NAME_LABELS = {"private_person", "person", "person_name", "person_alias"} | NAME_PART_LABELS
NAME_FRAGMENT_LABELS = NAME_LABELS | {"user_name"}
NAME_JOINERS = "-'’‐‑"
NAME_TITLES = re.compile(r"^(?:(?:dr|prof|mr|mrs|ms|mx)\.\s+)+", re.IGNORECASE)
FAKER_PERSON_LOCALES = find_available_locales(["faker.providers.person"])
# Prefer Kana over Han when a Japanese name mixes both scripts.
FAKER_LOCALES_BY_SCRIPT = {
    "Hiragana": "ja_JP",
    "Katakana": "ja_JP",
    "Arabic": "ar_AA",
    "Armenian": "hy_AM",
    "Bengali": "bn_BD",
    "Cyrillic": "ru_RU",
    "Devanagari": "hi_IN",
    "Georgian": "ka_GE",
    "Greek": "el_GR",
    "Gujarati": "gu_IN",
    "Hangul": "ko_KR",
    "Han": "zh_CN",
    "Hebrew": "he_IL",
    "Oriya": "or_IN",
    "Sinhala": "si_LK",
    "Tamil": "ta_IN",
    "Thai": "th_TH",
}
NAMEBERT_COUNTRY_ALIASES = {
    "brunei": "BN",
    "côte d’ivoire": "CI",
    "russia": "RU",
    "the gambia": "GM",
    "turkey": "TR",
}


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class DetectedSpan(BaseModel):
    label: str
    start: int
    end: int
    text: str
    placeholder: str  # replacement text used; empty string for remove mode


class DUUIRequest(BaseModel):
    text: str
    options: dict[str, Any] = Field(default_factory=dict)
    selection: Optional[dict] = None

    @field_validator("options", mode="before")
    @classmethod
    def coerce_options(cls, v: Any) -> dict:
        if v is None or isinstance(v, list):
            return {}
        if not isinstance(v, dict):
            return {}
        return v

    @field_validator("text", mode="before")
    @classmethod
    def coerce_text(cls, v: Any) -> str:
        return "" if v is None else str(v)


class DUUIResponse(BaseModel):
    text: str
    detected_spans: List[DetectedSpan]
    redacted_text: str
    warning: Optional[str] = None


class DUUIDocumentation(BaseModel):
    annotator_name: str
    version: str
    implementation_lang: str


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

class Settings(BaseSettings):
    duui_tool_name: str = "DUUI Anonymize"
    duui_tool_version: str = "1.2"
    default_model: str = DEFAULT_MODEL
    # Unload a model after it has not been used for this many seconds (env: MODEL_TTL_SECONDS).
    # Tracked per model; 0 or less disables unloading.
    model_ttl_seconds: int = 1800
    # Maximum number of models kept loaded at once (env: MODEL_CACHE_SIZE).
    model_cache_size: int = 2


settings = Settings()

# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------

app = FastAPI(
    docs_url="/api",
    redoc_url=None,
    title="DUUI Anonymize",
    description="PII detection and redaction for TTLab DUUI using openai/privacy-filter",
    version="1.2",
    terms_of_service="https://www.texttechnologylab.org/legal_notice/",
    contact={
        "name": "Ali Abusaleh",
        "url": "https://www.texttechnologylab.org",
        "email": "abusaleh@em.uni-frankfurt.de",
    },
    license_info={
        "name": "AGPL",
        "url": "http://www.gnu.org/licenses/agpl-3.0.en.html",
    },
)


@app.exception_handler(ValueError)
async def value_error_handler(request: Request, exc: ValueError) -> JSONResponse:
    logger.error("Bad request: %s", exc)
    return JSONResponse(status_code=400, content={"detail": str(exc)})


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    body = await request.body()
    logger.error("422 validation errors: %s", exc.errors())
    logger.error("Raw body: %s", body.decode("utf-8", errors="replace"))
    return JSONResponse(
        status_code=422,
        content=jsonable_encoder({"detail": exc.errors(), "body": body.decode("utf-8", errors="replace")}),
    )


# ---------------------------------------------------------------------------
# Static assets
# ---------------------------------------------------------------------------

with open("communication.lua", "rb") as _f:
    _communication_lua: str = _f.read().decode("utf-8")

with open("typesystem.xml", "rb") as _f:
    _typesystem = load_typesystem(_f)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get("/v1/details/input_output")
def get_input_output() -> JSONResponse:
    return JSONResponse(content=jsonable_encoder({
        "inputs": [],
        "outputs": ["de.tudarmstadt.ukp.dkpro.core.api.anomaly.type.Anomaly"],
    }))


@app.get("/v1/typesystem")
def get_typesystem() -> Response:
    return Response(content=_typesystem.to_xml().encode("utf-8"), media_type="application/xml")


@app.get("/v1/communication_layer", response_class=PlainTextResponse)
def get_communication_layer() -> str:
    return _communication_lua


@app.get("/v1/documentation")
def get_documentation() -> DUUIDocumentation:
    return DUUIDocumentation(
        annotator_name=settings.duui_tool_name,
        version=settings.duui_tool_version,
        implementation_lang="Python",
    )


@app.post("/v1/process")
async def post_process(raw_request: Request) -> DUUIResponse:
    body = await raw_request.body()
    try:
        data = json.loads(body)
    except json.JSONDecodeError as exc:
        raise RequestValidationError([{"type": "json_invalid", "loc": ("body",), "msg": str(exc), "input": body}])
    request = DUUIRequest.model_validate(data)
    return _process(request)


# ---------------------------------------------------------------------------
# Business logic
# ---------------------------------------------------------------------------

class _PipelineCache:
    """LRU cache of loaded pipelines that also unloads entries idle longer than ``ttl`` seconds."""

    def __init__(self, ttl: int, maxsize: int) -> None:
        self.ttl = ttl
        self.maxsize = max(1, maxsize)
        self._entries: OrderedDict[tuple, list] = OrderedDict()  # key -> [pipeline, last_used]
        self._lock = threading.RLock()
        self._sweeper: Optional[threading.Thread] = None

    def get(self, key: tuple, loader):
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                entry = [loader(), 0.0]
                self._entries[key] = entry
                while len(self._entries) > self.maxsize:
                    old_key, _ = self._entries.popitem(last=False)
                    logger.info("Unloading pipeline (cache full): %s", old_key)
                    self._release()
                self._start_sweeper()
            entry[1] = time.monotonic()
            self._entries.move_to_end(key)
            return entry[0]

    def evict_expired(self) -> None:
        if self.ttl <= 0:
            return
        now = time.monotonic()
        with self._lock:
            expired = [k for k, (_, last_used) in self._entries.items() if now - last_used > self.ttl]
            for key in expired:
                del self._entries[key]
                logger.info("Unloading pipeline (idle > %ds): %s", self.ttl, key)
            if expired:
                self._release()

    def _start_sweeper(self) -> None:
        if self.ttl <= 0 or self._sweeper is not None:
            return
        interval = min(60, max(1, self.ttl // 2))

        def run() -> None:
            while True:
                time.sleep(interval)
                try:
                    self.evict_expired()
                except Exception:
                    logger.exception("Pipeline TTL sweep failed")

        self._sweeper = threading.Thread(target=run, name="pipeline-ttl-sweeper", daemon=True)
        self._sweeper.start()

    @staticmethod
    def _release() -> None:
        gc.collect()
        if torch.cuda.is_available():
            torch.cuda.empty_cache()


_pipelines = _PipelineCache(ttl=settings.model_ttl_seconds, maxsize=settings.model_cache_size)


class _PIITokenClassificationPipeline(TokenClassificationPipeline):
    def get_tag(self, entity_name: str) -> tuple[str, str]:
        # Privacy Filter models use BIOES. For grouping, an end tag continues
        # the current span and a singleton starts its own span.
        if entity_name.startswith("E-"):
            return "I", entity_name[2:]
        if entity_name.startswith("S-"):
            return "B", entity_name[2:]
        return super().get_tag(entity_name)


def _load_pipeline(model: str, device: str, trust_remote_code: bool = False):
    def load():
        logger.info("Loading pipeline: model=%s device=%s trust_remote_code=%s", model, device, trust_remote_code)
        return hf_pipeline(
            task="token-classification",
            model=model,
            aggregation_strategy="simple",
            pipeline_class=_PIITokenClassificationPipeline,
            device=device,
            trust_remote_code=trust_remote_code,
        )

    return _pipelines.get((model, device, trust_remote_code), load)


def _load_namebert(device: str) -> TextClassificationPipeline:
    def load():
        logger.info("Loading NameBERT: device=%s", device)
        metadata_path = hf_hub_download(NAMEBERT_MODEL, "meta.json", revision=NAMEBERT_REVISION)
        with open(metadata_path, encoding="utf-8") as metadata_file:
            metadata = json.load(metadata_file)
        classifier = hf_pipeline(
            task="text-classification",
            model=NAMEBERT_MODEL,
            revision=NAMEBERT_REVISION,
            device=device,
            truncation=True,
            max_length=metadata["MAX_LEN"],
        )
        classifier.model.config.id2label = {int(index): label for index, label in metadata["idx2label"].items()}
        return classifier

    return _pipelines.get((NAMEBERT_MODEL, device, False), load)


def _resolve_selection(options: dict[str, Any], text_length: int) -> Optional[tuple[int, int]]:
    sel = options.get("selection")
    if isinstance(sel, dict):
        begin = sel.get("begin", sel.get("start"))
        end = sel.get("end", sel.get("stop"))
    else:
        begin = options.get("selection_begin", options.get("selection_start"))
        end = options.get("selection_end", options.get("selection_stop"))

    if begin is None or end is None:
        return None
    begin, end = int(begin), int(end)
    if begin < 0 or end < begin or end > text_length:
        raise ValueError(f"selection must satisfy 0 <= begin <= end <= {text_length}")
    return begin, end


def _parse_exclude_labels(value: Any) -> set[str]:
    """Accept comma-separated DUUI parameters or a JSON array of labels."""
    if value is None:
        return set()
    if isinstance(value, str):
        value = value.split(",")
    if not isinstance(value, list) or any(not isinstance(label, str) for label in value):
        raise ValueError("exclude_labels must be a comma-separated string or an array of strings")
    return {label.strip() for label in value if label.strip()}


def _trimmed_bounds(text: str, span: DetectedSpan) -> tuple[int, int]:
    covered = text[span.start:span.end]
    return span.end - len(covered.lstrip()), span.start + len(covered.rstrip())


def _name_word_char(char: str) -> bool:
    return char.isalpha() or char in NAME_JOINERS or category(char).startswith("M")


def _merge_name_fragments(text: str, spans: list[DetectedSpan], excluded: set[str]) -> list[DetectedSpan]:
    merged: list[DetectedSpan] = []
    for span in sorted(spans, key=lambda item: item.start):
        if not merged or span.label in excluded:
            merged.append(span)
            continue
        previous = merged[-1]
        left, right = previous.label.casefold(), span.label.casefold()
        if (previous.label in excluded or left not in NAME_FRAGMENT_LABELS
                or right not in NAME_FRAGMENT_LABELS or left == right == "user_name"):
            merged.append(span)
            continue
        left_start, left_end = _trimmed_bounds(text, previous)
        right_start, right_end = _trimmed_bounds(text, span)
        if (left_start >= left_end or right_start >= right_end or left_end > right_start
                or text[left_end:right_start].strip(NAME_JOINERS)
                or not all(_name_word_char(c) for c in text[left_end - 1] + text[right_start])):
            merged.append(span)
            continue

        first_word = text[right_start:right_end].split(maxsplit=1)[0]
        if ((left == "user_name" and not all(map(_name_word_char, text[left_start:left_end])))
                or (right == "user_name" and not all(map(_name_word_char, first_word)))):
            merged.append(span)
            continue

        # If a differently tagged span also contains the next word, keep that word's label.
        first_word_end = right_start + len(first_word)
        end = span.end if left == right or first_word_end == right_end else first_word_end
        label = span.label if left == "user_name" else previous.label
        merged[-1] = DetectedSpan(label=label, start=previous.start, end=end,
                                  text=text[previous.start:end].strip(), placeholder=f"[{label}]")
        if end < span.end:
            merged.append(span.model_copy(update={"start": end, "text": text[end:span.end].strip()}))
    return merged


def _name_part_links(
    name_keys: set[tuple[str, str]], split_names: dict[str, tuple[str, str]] | None = None,
) -> dict[tuple[str, str], tuple[str, str]]:
    candidates: dict[tuple[str, str], set[tuple[str, str]]] = defaultdict(set)
    split_names = split_names or {}
    for kind, full_name in name_keys:
        if kind != "name":
            continue
        confirmed = split_names.get(full_name)
        if confirmed:
            splits = iter((confirmed,))
        else:
            splits = ((full_name[:index], full_name[index + 1:])
                      for index, char in enumerate(full_name) if char == " ")
        for first, last in splits:
            for role, part in (("first_name", first), ("last_name", last)):
                candidates[role, part].add((full_name, role))
                if " " not in part or confirmed:
                    candidates["alias", part].add((full_name, role))

    links = {}
    for kind, part in name_keys:
        if kind == "name":
            continue
        # A model can label the standalone part differently from the adjacent full name.
        matches = candidates.get((kind, part)) or candidates.get(("alias", part), ())
        if len(matches) == 1:
            links[kind, part] = next(iter(matches))
    return links


class _NameMention(NamedTuple):
    span: DetectedSpan
    kind: str
    name: str


def _find_adjacent_name_parts(
    text: str, mentions: list[_NameMention], name_keys: set[tuple[str, str]],
) -> tuple[dict[str, tuple[str, str]], dict[int, tuple[str, str]]]:
    full_names: dict[str, tuple[str, str]] = {}
    links: dict[int, tuple[str, str]] = {}
    # Prefer explicitly tagged pairs. A separate mention can resolve a same-label pair.
    pairs = sorted(pairwise(mentions), key=lambda pair: pair[0].kind == pair[1].kind)
    for first, last in pairs:
        if first.span.start in links or last.span.start in links:
            continue
        roles = (first.kind, last.kind)
        if not (
            roles == ("first_name", "last_name")
            or (roles == ("last_name", "last_name") and ("first_name", first.name.casefold()) in name_keys)
            or (roles == ("first_name", "first_name") and ("last_name", last.name.casefold()) in name_keys)
        ):
            continue
        _, first_end = _trimmed_bounds(text, first.span)
        last_start, _ = _trimmed_bounds(text, last.span)
        gap = text[first_end:last_start]
        if not gap or gap.strip(" \t\u00a0"):
            continue
        key = f"{first.name} {last.name}".casefold()
        full_names.setdefault(key, (first.name, last.name))
        links[first.span.start] = key, "first_name"
        links[last.span.start] = key, "last_name"
    return full_names, links


def _pseudonymize_names(text: str, spans: list[DetectedSpan], device: str, use_nationality: bool) -> None:
    mentions: list[_NameMention] = []
    for span in spans:
        label = span.label.casefold()
        if label not in NAME_LABELS:
            continue
        # Pipeline tokens can differ from the original spelling and whitespace.
        name = " ".join(text[span.start:span.end].split())
        name = NAME_TITLES.sub("", name)
        if not name:
            continue
        kind = label if label in NAME_PART_LABELS else ("name" if " " in name else "alias")
        mentions.append(_NameMention(span, kind, name))

    name_keys = {(kind, name.casefold()) for _, kind, name in mentions}
    adjacent_names, adjacent_links = _find_adjacent_name_parts(text, mentions, name_keys)
    split_names = {key: (first.casefold(), last.casefold()) for key, (first, last) in adjacent_names.items()}
    links = _name_part_links(name_keys | {("name", key) for key in split_names}, split_names)
    linked_parts: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
    for key, (first, last) in split_names.items():
        linked_parts[key]["first_name"].add(first)
        linked_parts[key]["last_name"].add(last)
    for (_, part), (full_name, role) in links.items():
        linked_parts[full_name][role].add(part)

    predictor = None
    replacements: dict[tuple[str, str], tuple[str, dict[str, str]]] = {}

    def replacement_for(key: tuple[str, str], name: str, placeholder: str) -> tuple[str, dict[str, str]]:
        nonlocal predictor
        if key not in replacements:
            if use_nationality and predictor is None:
                predictor = _load_namebert(device)
            kind, name_key = key
            replacements[key] = _fake_name(
                predictor, name, kind, placeholder,
                source_parts=linked_parts.get(name_key) if kind == "name" else None,
            )
        return replacements[key]

    # Generate full names first so earlier standalone mentions can reuse their parts.
    for span, kind, name in mentions:
        if kind == "name":
            span.placeholder, _ = replacement_for((kind, name.casefold()), name, span.placeholder)
    for key, (first, last) in adjacent_names.items():
        replacement_for(("name", key), f"{first} {last}", "[private_person]")

    for span, kind, name in mentions:
        if kind == "name":
            continue
        name_key = name.casefold()
        link = adjacent_links.get(span.start) or links.get((kind, name_key))
        if link is not None:
            full_name, role = link
            _, fake_parts = replacements["name", full_name]
            part = fake_parts.get(role)
            if part and _name_scripts(part) <= _name_scripts(name):
                span.placeholder = part
                continue
        generation_kind = "first_name" if kind == "alias" else kind
        span.placeholder, _ = replacement_for((generation_kind, name_key), name, span.placeholder)


def _faker_locale(country_label: str) -> str:
    label = normalize("NFC", country_label)
    try:
        country = pycountry.countries.lookup(NAMEBERT_COUNTRY_ALIASES.get(label, label))
    except LookupError:
        return DEFAULT_LOCALE

    country_suffix = f"_{country.alpha_2}"
    for locale in FAKER_PERSON_LOCALES:
        if locale.endswith(country_suffix):
            return locale

    # Faker's advertised locale list includes providers for other data types.
    languages = {locale.split("_", 1)[0] for locale in AVAILABLE_LOCALES if locale.endswith(country_suffix)}
    return next(
        (locale for locale in FAKER_PERSON_LOCALES if locale.split("_", 1)[0] in languages),
        DEFAULT_LOCALE,
    )


def _name_scripts(name: str) -> set[str]:
    return {script(char) for char in name if char.isalpha()} - {"Common", "Inherited"}


def _fake_name(
    predictor: TextClassificationPipeline | None,
    name: str,
    kind: str,
    placeholder: str,
    *,
    source_parts: dict[str, set[str]] | None = None,
) -> tuple[str, dict[str, str]]:
    source_scripts = _name_scripts(name)
    if predictor is None:
        locale = next(
            (locale for name_script, locale in FAKER_LOCALES_BY_SCRIPT.items() if name_script in source_scripts),
            DEFAULT_LOCALE,
        )
    else:
        country_label = predictor(name, top_k=1)[0]["label"]
        locale = _faker_locale(country_label)
    faker = Faker(locale)
    if source_scripts == {"Latin"} and locale.startswith("ar_"):
        faker.add_provider(ArabicLatinNameProvider)
    generate = getattr(faker, kind)
    for _ in range(10):
        fake_parts = {}
        if source_parts:
            fake_parts = {"first_name": faker.first_name(), "last_name": faker.last_name()}
            if any(" ".join(fake_parts[role].split()).casefold() in originals
                   for role, originals in source_parts.items()):
                continue
            replacement = f"{fake_parts['first_name']} {fake_parts['last_name']}"
        else:
            replacement = generate()
        replacement_scripts = _name_scripts(replacement)
        if not replacement_scripts or (source_scripts and replacement_scripts - source_scripts):
            continue
        if " ".join(replacement.split()).casefold() != name.casefold():
            return replacement, fake_parts
    return placeholder, {}


def _build_redacted(text: str, spans: list[DetectedSpan], mode: str) -> str:
    """Apply mode transformation to text using already-computed spans."""
    if not spans:
        return text
    parts: list[str] = []
    cursor = 0
    for span in sorted(spans, key=lambda s: s.start):
        if span.start < cursor:
            continue
        parts.append(text[cursor:span.start])
        if mode in (MODE_PLACEHOLDER, MODE_PSEUDO):
            covered = text[span.start:span.end]
            without_leading = covered.lstrip()
            parts.append(covered[:len(covered) - len(without_leading)])
            parts.append(span.placeholder)
            parts.append(without_leading[len(without_leading.rstrip()):])
        # MODE_REMOVE: append nothing - the PII is deleted
        cursor = span.end
    parts.append(text[cursor:])
    return "".join(parts)


def _process(request: DUUIRequest) -> DUUIResponse:
    options = request.options
    logger.info("RAW OPTIONS RECEIVED: %s", dict(options))
    model = str(options.get("model", settings.default_model))

    if model not in SUPPORTED_MODELS:
        raise ValueError(
            f"Unsupported model '{model}'. "
            f"Supported models: {sorted(SUPPORTED_MODELS)}"
        )

    device = str(options.get("device") or "").strip().lower()
    if device in ("", "auto", "none", "null"):
        device = "cuda" if torch.cuda.is_available() else "cpu"
    elif device == "gpu":
        device = "cuda"
    if device.startswith("cuda") and not torch.cuda.is_available():
        raise ValueError(
            f"device '{device}' requested but CUDA is not available "
            f"(torch {torch.__version__}; use the CUDA image and start the container with GPU access)"
        )
    mode = str(options.get("mode", MODE_PLACEHOLDER))
    pseudo_use_nationality = str(options.get("pseudo_use_nationality", True)).strip().lower()
    if mode == MODE_PSEUDO and pseudo_use_nationality not in {"true", "false"}:
        raise ValueError("pseudo_use_nationality must be true or false")
    exclude_labels = _parse_exclude_labels(options.get("exclude_labels"))

    logger.info("Processing: model=%s device=%s mode=%s text_length=%d", model, device, mode, len(request.text))
    
    if not request.text:
        return DUUIResponse(text="", detected_spans=[], redacted_text="")

    sel = _resolve_selection(options, text_length=len(request.text))
    selected_text = request.text[sel[0]:sel[1]] if sel else request.text
    offset = sel[0] if sel else 0

    trust_remote_code = bool(options.get("trust_remote_code", False))
    pipe = _load_pipeline(model, device, trust_remote_code)
    raw = pipe(selected_text)

    spans = [
        DetectedSpan(
            label=item["entity_group"],
            start=int(item["start"]) + offset,
            end=int(item["end"]) + offset,
            text=str(item["word"]).strip(),
            placeholder=f"[{item['entity_group']}]" if mode in (MODE_PLACEHOLDER, MODE_PSEUDO) else "",
        )
        for item in raw
    ]
    if mode == MODE_PSEUDO and model != DEFAULT_MODEL:
        spans = _merge_name_fragments(request.text, spans, exclude_labels)
    spans = [span for span in spans if span.label not in exclude_labels]
    if mode == MODE_PSEUDO:
        _pseudonymize_names(request.text, spans, device, use_nationality=pseudo_use_nationality == "true")

    redacted_text = _build_redacted(request.text, spans, mode)
    if sel is not None:
        # only the selected window was processed; rebuild full text around it
        local_spans = [
            DetectedSpan(label=s.label, start=s.start - offset, end=s.end - offset,
                         text=s.text, placeholder=s.placeholder)
            for s in spans
        ]
        redacted_window = _build_redacted(selected_text, local_spans, mode)
        redacted_text = request.text[:sel[0]] + redacted_window + request.text[sel[1]:]

    return DUUIResponse(
        text=request.text,
        detected_spans=spans,
        redacted_text=redacted_text,
    )


if __name__ == "__main__":
    uvicorn.run("duui_anonymize:app", host="0.0.0.0", port=9714, workers=1)
