import json
import logging
import mimetypes
from pathlib import Path

from google import genai
from google.genai import types


HTR_MODEL_OPTIONS = [
    ("Gemini 4 Argon", "gemini-4-argon"),
    ("Gemini 3.1 Pro Preview", "gemini-3.1-pro-preview"),
    ("Gemini 3.5 Flash", "gemini-3.5-flash"),
    ("Gemini 3.6 Flash", "gemini-3.6-flash"),
    ("Gemini 3.5 Flash Lite", "gemini-3.5-flash-lite"),
    ("Gemini 3.8 Flash","gemini-3.8-flash")
]

ANALYSIS_MODEL_OPTIONS = [
    ("Gemini 3.5 Flash", "gemini-3.5-flash"),
    ("Gemini 3.5 Flash Lite", "gemini-3.5-flash-lite"),
]

BOX_MODEL_OPTIONS = [
    ("Gemini 3.1 Flash Image", "gemini-3.1-flash-image"),
]

DEFAULT_HTR_MODEL = "gemini-3.5-flash"
DEFAULT_FIX_MODEL = DEFAULT_HTR_MODEL
DEFAULT_ANALYSIS_MODEL = "gemini-3.5-flash"
DEFAULT_BOX_MODEL = "gemini-3.1-flash-image"
DEFAULT_API_TIMEOUT_SECONDS = 300

logger = logging.getLogger(__name__)

MODEL_OPTIONS = {
    "htr": HTR_MODEL_OPTIONS,
    "fix": HTR_MODEL_OPTIONS,
    "analysis": ANALYSIS_MODEL_OPTIONS,
    "box": BOX_MODEL_OPTIONS,
}

DEFAULT_MODELS = {
    "htr": DEFAULT_HTR_MODEL,
    "fix": DEFAULT_FIX_MODEL,
    "analysis": DEFAULT_ANALYSIS_MODEL,
    "box": DEFAULT_BOX_MODEL,
}


def model_choices(task_type: str) -> list[tuple[str, str]]:
    return MODEL_OPTIONS[task_type]


def model_labels(task_type: str) -> list[str]:
    return [label for label, _ in model_choices(task_type)]


def model_label_for_code(task_type: str, code: str) -> str:
    for label, value in model_choices(task_type):
        if code == value:
            return label
    default_code = DEFAULT_MODELS[task_type]
    for label, value in model_choices(task_type):
        if value == default_code:
            return label
    return model_choices(task_type)[0][0]


def model_code_for_label(task_type: str, label: str) -> str:
    for option_label, code in model_choices(task_type):
        if option_label == label:
            return code
    return DEFAULT_MODELS[task_type]


def normalize_model_selection(task_type: str, code: str | None) -> str:
    allowed_codes = {value for _, value in model_choices(task_type)}
    if code in allowed_codes:
        return code
    return DEFAULT_MODELS[task_type]


def _client(api_key: str, timeout_seconds: int | None = None):
    timeout_seconds = timeout_seconds or DEFAULT_API_TIMEOUT_SECONDS
    timeout_ms = int(timeout_seconds * 1000)
    return genai.Client(api_key=api_key, http_options=types.HttpOptions(timeout=timeout_ms))


def _read_image_part(image_path: str):
    path = Path(image_path)
    with path.open("rb") as handle:
        image_bytes = handle.read()

    mime_type, _ = mimetypes.guess_type(path.name)
    if mime_type not in {"image/jpeg", "image/png", "image/webp", "image/heic", "image/heif"}:
        mime_type = "image/jpeg"

    logger.info(
        "Loaded image for Gemini: image=%s mime=%s size_bytes=%s",
        image_path,
        mime_type,
        len(image_bytes),
    )
    return types.Part.from_bytes(data=image_bytes, mime_type=mime_type)


def _default_image_config():
    return types.GenerateContentConfig(
        temperature=0,
        thinking_config=types.ThinkingConfig(thinking_level=types.ThinkingLevel.LOW),
        media_resolution=types.MediaResolution.MEDIA_RESOLUTION_HIGH,
        automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
    )


def transcribe_image(
    api_key: str,
    prompt_text: str,
    image_path: str,
    model_name: str = DEFAULT_HTR_MODEL,
    timeout_seconds: int = DEFAULT_API_TIMEOUT_SECONDS,
):
    client = _client(api_key, timeout_seconds)

    image_part = _read_image_part(image_path)

    contents = [
        types.Content(
            role="user",
            parts=[
                types.Part.from_text(text=prompt_text),
                image_part,
            ],
        )
    ]

    logger.info("Gemini request started: task=transcription model=%s image=%s timeout=%ss", model_name, image_path, timeout_seconds)
    try:
        response = client.models.generate_content(
            model=model_name,
            contents=contents,
            config=_default_image_config(),
        )
    except Exception:
        logger.exception("Gemini request failed: task=transcription model=%s image=%s", model_name, image_path)
        raise
    logger.info("Gemini request finished: task=transcription model=%s image=%s", model_name, image_path)
    return model_name, response


def stream_transcribe_image(
    api_key: str,
    prompt_text: str,
    image_path: str,
    model_name: str = DEFAULT_HTR_MODEL,
    timeout_seconds: int = DEFAULT_API_TIMEOUT_SECONDS,
):
    image_part = _read_image_part(image_path)

    contents = [
        types.Content(
            role="user",
            parts=[
                types.Part.from_text(text=prompt_text),
                image_part,
            ],
        )
    ]

    def response_stream():
        client = _client(api_key, timeout_seconds)
        logger.info("Gemini stream started: task=transcription model=%s image=%s timeout=%ss", model_name, image_path, timeout_seconds)
        try:
            stream = client.models.generate_content_stream(
                model=model_name,
                contents=contents,
                config=_default_image_config(),
            )
            for response in stream:
                yield response
        except Exception:
            logger.exception("Gemini stream failed: task=transcription model=%s image=%s", model_name, image_path)
            raise
        logger.info("Gemini stream finished: task=transcription model=%s image=%s", model_name, image_path)

    return model_name, response_stream()


def verify_transcription(
    api_key: str,
    image_path: str,
    original_text: str,
    model_name: str = DEFAULT_HTR_MODEL,
    timeout_seconds: int = DEFAULT_API_TIMEOUT_SECONDS,
):
    client = _client(api_key, timeout_seconds)

    image_part = _read_image_part(image_path)

    prompt = """
Otrzymasz skan dokumentu oraz jego wstępną transkrypcję.

Twoim zadaniem jest zweryfikować tekst z obrazem i poprawić wszelkie błędy:
1. Popraw literówki i błędnie odczytane słowa.
2. Uzupełnij pominięte słowa.
3. Zachowaj oryginalny układ wierszy.
4. Nie dodawaj własnych komentarzy, zwróć TYLKO poprawiony tekst.

Pamiętaj o zasadach oznaczania niepewności:
- Jeśli fragment (słowo lub litera) jest całkowicie nieczytelny (plama, zniszczenie), oznacz go jako: [nieczytelne].
- Jeśli odczyt jest wątpliwy, ale masz przypuszczenie, zapisz je i dodaj znak zapytania w nawiasie, np.: [słowo?] lub słow[o?].
- Jeśli w tekście występuje skreślenie, oznacz je jako: [skreślenie].
"""

    logger.info("Gemini request started: task=fix model=%s image=%s timeout=%ss", model_name, image_path, timeout_seconds)
    try:
        response = client.models.generate_content(
            model=model_name,
            contents=[
                types.Part.from_text(text=prompt + "\nTranskrypcja: " + original_text),
                image_part,
            ],
            config=_default_image_config(),
        )
    except Exception:
        logger.exception("Gemini request failed: task=fix model=%s image=%s", model_name, image_path)
        raise
    logger.info("Gemini request finished: task=fix model=%s image=%s", model_name, image_path)
    return model_name, response


def correct_transcription(
    api_key: str,
    image_path: str,
    original_text: str,
    model_name: str = DEFAULT_HTR_MODEL,
    timeout_seconds: int = DEFAULT_API_TIMEOUT_SECONDS,
):
    client = _client(api_key, timeout_seconds)

    image_part = _read_image_part(image_path)

    prompt = """
Otrzymasz skan dokumentu oraz aktualny odczyt OCR/HTR.

Twoim zadaniem jest przygotować poprawioną wersję odczytu na podstawie porównania tekstu ze skanem.

Zasady:
1. Popraw błędnie odczytane znaki, słowa, interpunkcję i oczywiste pominięcia.
2. Zachowaj układ tekstu, podział na akapity, tabele oraz istniejący format Markdown albo HTML.
3. Jeżeli tekst zawiera znaczniki stylu, popraw również błędnie rozpoznane pogrubienia i kursywę.
4. Dodawaj albo usuwaj znaczniki pogrubienia/kursywy tylko wtedy, gdy wynika to ze skanu.
5. Nie modernizuj pisowni i nie parafrazuj tekstu.
6. Nie dodawaj komentarzy, objaśnień ani opisu zmian.
7. Zwróć WYŁĄCZNIE pełny poprawiony tekst.

Jeśli fragment jest nieczytelny, użyj oznaczenia [nieczytelne]. Jeśli odczyt jest niepewny, oznacz go znakiem zapytania w nawiasie, np. [słowo?].
"""

    logger.info("Gemini request started: task=fix_replace model=%s image=%s timeout=%ss", model_name, image_path, timeout_seconds)
    try:
        response = client.models.generate_content(
            model=model_name,
            contents=[
                types.Part.from_text(text=prompt + "\nAktualny odczyt:\n" + original_text),
                image_part,
            ],
            config=_default_image_config(),
        )
    except Exception:
        logger.exception("Gemini request failed: task=fix_replace model=%s image=%s", model_name, image_path)
        raise
    logger.info("Gemini request finished: task=fix_replace model=%s image=%s", model_name, image_path)
    return model_name, response


def extract_entities(
    api_key: str,
    text: str,
    model_name: str = DEFAULT_ANALYSIS_MODEL,
    timeout_seconds: int = DEFAULT_API_TIMEOUT_SECONDS,
):
    client = _client(api_key, timeout_seconds)

    prompt = """
Jesteś ekspertem w dziedzinie historii i paleografii XVIII, XIX oraz XX wieku. Twoim zadaniem
jest ekstrakcja nazw własnych z transkrypcji dokumentów historycznych.

Zasady klasyfikacji:
1. PERS (Osoby): Wyodrębnij nazwy osób, mogą to być pełne imiona i nazwiska, ale także zapisy
   samych nazwisk lub imion, zapisy inicjałów np. A. T., zapisy nazw stosowane w średniowieczu
   np. Jan z Dąbrówki, uwzględnij także nazwy narodów lub plemion. DOŁĄCZ do nazwy towarzyszące im
   tytuły szlacheckie (np. hr., margrabia), stopnie wojskowe (np. kpt., gen.),
   funkcje urzędowe (np. rządzca, wójt) oraz zwroty grzecznościowe (np. JW Pan, Ob.),
   jeśli występują bezpośrednio przy nazwisku.
2. LOC (Geografia): Wyodrębnij nazwy miast, wsi, krajów, państw, folwarków, majątków ziemskich, rzek,
   jezior, guberni oraz konkretne nazwy ulic i placów.
3. ORG (Organizacje): Wyodrębnij nazwy urzędów, instytucji, pułków wojskowych, parafii, komitetów, stowarzyszeń,
   fabryk i towarzystw (np. "Towarzystwo Kredytowe Ziemskie").

Instrukcje techniczne:
- Rekonstrukcja: Jeśli nazwa jest podzielona między wiersze (np. "Krak-" i "ów"),
  połącz ją w jedno słowo bez dywizu ("Kraków").
- Normalizacja: Zwróć nazwy w takiej formie (deklinacji), w jakiej występują w tekście, ale usuń
  znaki podziału wiersza.
- Czystość: Ignoruj nazwy pospolite, chyba że są częścią nazwy własnej.

Zwróć wynik WYŁĄCZNIE jako JSON w formacie:
{
  "PERS": ["nazwa1", ...],
  "LOC": ["nazwa1", ...],
  "ORG": ["nazwa1", ...]
}
"""

    logger.info("Gemini request started: task=ner model=%s chars=%s timeout=%ss", model_name, len(text), timeout_seconds)
    try:
        response = client.models.generate_content(
            model=model_name,
            contents=prompt + "\nTekst: " + text,
            config=types.GenerateContentConfig(
                automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True)
            ),
        )
    except Exception:
        logger.exception("Gemini request failed: task=ner model=%s chars=%s", model_name, len(text))
        raise
    logger.info("Gemini request finished: task=ner model=%s chars=%s", model_name, len(text))
    return model_name, response


def locate_entities(
    api_key: str,
    image_path: str,
    entities_to_find: list[tuple[str, str]],
    model_name: str = DEFAULT_BOX_MODEL,
    timeout_seconds: int = DEFAULT_API_TIMEOUT_SECONDS,
):
    client = _client(api_key, timeout_seconds)

    image_part = _read_image_part(image_path)

    entities_str = "".join(f"{name},{category}\n" for name, category in entities_to_find)

    prompt = f"""
Na załączonym obrazie znajdź lokalizację następujących nazw,
(podanych w formie listy par: nazwa_do_wyszukania, kategoria_nazwy, każda para w osobnym wierszu np.
Felicjan Słomkowski, PERS
Gniezno, LOC):

{entities_str}.

Uwzględnij tylko i wyłącznie nazwy z listy, inne zignoruj.
Dla każdej nazwy podaj współrzędne ramki w formacie:

nazwa, nazwa_kategorii [ymin, xmin, ymax, xmax]

na przykład:
Krakowa, LOC [ymin, xmin, ymax, xmax]
Henryk Walezy, PERS [ymin, xmin, ymax, xmax]
...

Wszystkie współrzędne w skali 0-1000.
Zwróć tylko listę tych danych bez żadnych dodatkowych komentarzy.
"""

    logger.info(
        "Gemini request started: task=box model=%s image=%s entities=%s timeout=%ss",
        model_name,
        image_path,
        len(entities_to_find),
        timeout_seconds,
    )
    try:
        response = client.models.generate_content(
            model=model_name,
            contents=[
                types.Part.from_text(text=prompt),
                image_part,
            ],
            config=types.GenerateContentConfig(
                automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
                image_config=types.ImageConfig(image_size="1K"),
                response_modalities=["TEXT"],
            ),
        )
    except Exception:
        logger.exception("Gemini request failed: task=box model=%s image=%s", model_name, image_path)
        raise
    logger.info("Gemini request finished: task=box model=%s image=%s", model_name, image_path)
    return model_name, response


def build_nominative_map(
    api_key: str,
    names_list: list[str],
    model_name: str = DEFAULT_ANALYSIS_MODEL,
    timeout_seconds: int = DEFAULT_API_TIMEOUT_SECONDS,
):
    client = _client(api_key, timeout_seconds)
    nominative_map = {}
    usage_entries = []

    for i in range(0, len(names_list), 50):
        batch = names_list[i:i + 50]
        prompt = (
            "Dla podanej listy nazw własnych z dokumentów historycznych, "
            "podaj ich formę w mianowniku, nie zmieniaj rodzaju nazw (męski, żeński, nijaki). "
            "Zwróć WYŁĄCZNIE czysty JSON: {\"oryginał\": \"mianownik\", ...}. "
            f"Lista: {', '.join(batch)}"
        )

        logger.info(
            "Gemini request started: task=nominative model=%s batch=%s-%s timeout=%ss",
            model_name,
            i + 1,
            i + len(batch),
            timeout_seconds,
        )
        try:
            response = client.models.generate_content(
                model=model_name,
                contents=prompt,
                config=types.GenerateContentConfig(
                    automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True)
                ),
            )
        except Exception:
            logger.exception("Gemini request failed: task=nominative model=%s batch_start=%s", model_name, i + 1)
            raise
        logger.info("Gemini request finished: task=nominative model=%s batch_start=%s", model_name, i + 1)

        if response.usage_metadata:
            usage_entries.append((model_name, response.usage_metadata))

        if response.text:
            json_str = response.text.replace("```json", "").replace("```", "").strip()
            nominative_map.update(json.loads(json_str))

    return nominative_map, usage_entries
