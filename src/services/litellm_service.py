import base64
import json
import logging
import mimetypes
import os
import socket
import urllib.error
import urllib.request
from urllib.parse import urlsplit
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)
DEFAULT_ENDPOINT = "https://ai-test.ihpan.edu.pl/v1/chat/completions"
DEFAULT_MODEL = "qwen3.8-flash-next-fp8"


@dataclass
class Usage:
    prompt_token_count: int = 0
    candidates_token_count: int = 0
    total_token_count: int = 0


@dataclass
class Response:
    text: str
    usage_metadata: Usage | None = None


def get_api_key():
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parents[2] / ".env")
    return os.environ.get("AI_TEST_KEY", "").strip()


def transcribe_image(image_path, prompt, *, endpoint=DEFAULT_ENDPOINT, model=DEFAULT_MODEL, timeout_seconds=300):
    parsed = urlsplit(endpoint)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("Adres LiteLLM musi być poprawnym adresem HTTPS bez danych uwierzytelniających i parametrów.")
    key = get_api_key()
    if not key:
        raise RuntimeError("Brak klucza AI_TEST_KEY w środowisku lub pliku .env.")
    path = Path(image_path)
    mime = mimetypes.guess_type(path.name)[0]
    if mime not in {"image/jpeg", "image/png", "image/webp", "image/gif"}:
        raise ValueError(f"Nieobsługiwany format obrazu: {path.name}")
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    payload = {"model": model, "temperature": 0, "stream": False,
               "chat_template_kwargs": {"enable_thinking": False},
               "messages": [{"role": "user", "content": [
                   {"type": "text", "text": prompt},
                   {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{encoded}"}},
               ]}]}
    request = urllib.request.Request(endpoint, data=json.dumps(payload).encode("utf-8"),
                                     headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"}, method="POST")
    logger.info("LiteLLM request started: model=%s image=%s timeout=%ss", model, image_path, timeout_seconds)
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as result:
            data = json.load(result)
    except urllib.error.HTTPError as exc:
        details = exc.read().decode("utf-8", errors="replace")[:2000]
        raise RuntimeError(f"LiteLLM: HTTP {exc.code}: {details}") from exc
    except (TimeoutError, socket.timeout, urllib.error.URLError) as exc:
        raise RuntimeError(f"Błąd połączenia z LiteLLM: {exc}") from exc
    choices = data.get("choices") or []
    content = (choices[0].get("message") or {}).get("content") if choices else None
    if not isinstance(content, str) or not content.strip():
        raise RuntimeError("LiteLLM nie zwrócił tekstu. Sprawdź obsługę obrazów przez wybrany model.")
    usage = data.get("usage") or {}
    metadata = Usage(usage.get("prompt_tokens") or 0, usage.get("completion_tokens") or 0, usage.get("total_tokens") or 0)
    logger.info("LiteLLM request finished: model=%s image=%s", model, image_path)
    return model, Response(content, metadata)
