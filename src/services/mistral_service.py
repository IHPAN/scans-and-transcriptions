import base64
import json
import logging
import mimetypes
import os
import re
from dataclasses import dataclass
from pathlib import Path


DEFAULT_MISTRAL_OCR_MODEL = "mistral-ocr-4-1"

logger = logging.getLogger(__name__)


@dataclass
class MistralUsageMetadata:
    prompt_token_count: int = 0
    candidates_token_count: int = 0
    total_token_count: int = 0
    pages_processed: int = 0
    doc_size_bytes: int = 0


@dataclass
class MistralResponse:
    text: str = ""
    usage_metadata: MistralUsageMetadata | None = None
    raw_response: dict | None = None


def get_api_key() -> str:
    api_key = os.environ.get("MISTRAL_API_KEY", "").strip()
    if api_key:
        return api_key

    try:
        from dotenv import load_dotenv

        project_root = Path(__file__).resolve().parents[2]
        load_dotenv(project_root / ".env")
    except Exception:
        pass

    return os.environ.get("MISTRAL_API_KEY", "").strip()


def _data_url(image_path: str) -> tuple[str, int, str]:
    path = Path(image_path)
    with path.open("rb") as handle:
        image_bytes = handle.read()

    mime_type, _ = mimetypes.guess_type(path.name)
    if mime_type not in {"image/jpeg", "image/png", "image/webp", "image/gif"}:
        mime_type = "image/jpeg"

    encoded = base64.b64encode(image_bytes).decode("ascii")
    return f"data:{mime_type};base64,{encoded}", len(image_bytes), mime_type


def _usage_from_response(data: dict, doc_size_bytes: int) -> MistralUsageMetadata:
    usage = data.get("usage_info") or {}
    pages_processed = int(
        usage.get("pages_processed")
        or usage.get("pages")
        or len(data.get("pages") or [])
        or 0
    )
    return MistralUsageMetadata(
        pages_processed=pages_processed,
        doc_size_bytes=doc_size_bytes,
    )


def _decode_base64_text(value: str) -> str:
    try:
        return base64.b64decode(value).decode("utf-8").strip()
    except Exception:
        return ""


def _artifact_id(item: dict) -> str:
    return (
        item.get("id")
        or item.get("name")
        or item.get("filename")
        or item.get("file_name")
        or ""
    )


def _artifact_text(item: dict) -> str:
    for key in ["html", "markdown", "text", "content"]:
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, (dict, list)):
            return json.dumps(value, ensure_ascii=False, indent=2)

    value = item.get("image_base64") or item.get("base64")
    if isinstance(value, str) and value.strip():
        return _decode_base64_text(value)

    return ""


def _page_artifacts(page: dict) -> dict[str, str]:
    artifacts = {}
    for key in ["tables", "images", "artifacts"]:
        for index, item in enumerate(page.get(key) or []):
            if not isinstance(item, dict):
                continue
            item_id = _artifact_id(item)
            item_text = _artifact_text(item)
            if key == "tables" and not item_id:
                item_id = f"tbl-{index}.html"
            if item_id and item_text:
                artifacts[item_id] = item_text
                if key == "tables":
                    artifacts.setdefault(f"tbl-{index}.html", item_text)
                    artifacts.setdefault(f"tbl-{index}.md", item_text)
    return artifacts


def _inline_linked_artifacts(markdown: str, artifacts: dict[str, str]) -> str:
    if not artifacts:
        return markdown

    def replace_link(match):
        label = match.group(1)
        target = match.group(2)
        artifact = artifacts.get(target) or artifacts.get(label)
        if not artifact:
            return match.group(0)
        return artifact

    return re.sub(r"!?\[([^\]]+)\]\(([^)]+)\)", replace_link, markdown)


def _extract_markdown(data: dict) -> str:
    pages = data.get("pages") or []
    texts = []
    for page in pages:
        markdown = (page.get("markdown") or "").strip()
        if markdown:
            texts.append(_inline_linked_artifacts(markdown, _page_artifacts(page)))
    return "\n\n".join(texts).strip()


def ocr_image(
    image_path: str,
    *,
    api_key: str | None = None,
    model_name: str = DEFAULT_MISTRAL_OCR_MODEL,
    timeout_seconds: int = 300,
    include_blocks: bool = False,
    table_format: str = "markdown",
):
    api_key = (api_key or get_api_key()).strip()
    if not api_key:
        raise RuntimeError("Brak klucza Mistral API. Ustaw zmienną środowiskową MISTRAL_API_KEY.")

    image_url, doc_size_bytes, mime_type = _data_url(image_path)
    payload = {
        "model": model_name,
        "document": {
            "type": "image_url",
            "image_url": image_url,
        },
        "include_image_base64": table_format == "html",
        "include_blocks": bool(include_blocks),
        "table_format": table_format,
    }

    logger.info(
        "Mistral OCR request started: model=%s image=%s mime=%s size_bytes=%s timeout=%ss",
        model_name,
        image_path,
        mime_type,
        doc_size_bytes,
        timeout_seconds,
    )
    try:
        from mistralai.client import Mistral

        with Mistral(api_key=api_key, timeout_ms=int(timeout_seconds * 1000)) as client:
            response = client.ocr.process(**payload)
        data = response.model_dump(mode="json", by_alias=True)
    except ImportError as exc:
        raise RuntimeError("Brak biblioteki mistralai. Zainstaluj zależności z requirements.txt.") from exc
    except Exception as exc:
        logger.exception("Mistral OCR request failed: model=%s image=%s", model_name, image_path)
        raise RuntimeError(f"Błąd Mistral OCR: {exc}") from exc

    logger.info("Mistral OCR request finished: model=%s image=%s", model_name, image_path)
    return model_name, MistralResponse(
        text=_extract_markdown(data),
        usage_metadata=_usage_from_response(data, doc_size_bytes),
        raw_response=data,
    )
