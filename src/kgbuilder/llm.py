"""Structured LLM calls with a disk cache, so reruns are free and reproducible."""

import hashlib
import json
from typing import TypeVar

from pydantic import BaseModel

from .config import settings

T = TypeVar("T", bound=BaseModel)

_client = None


def _get_client():
    global _client
    if _client is None:
        from google import genai

        if not settings.gemini_api_key:
            raise RuntimeError("GEMINI_API_KEY is not set (see .env.example)")
        _client = genai.Client(api_key=settings.gemini_api_key)
    return _client


def available() -> bool:
    return bool(settings.gemini_api_key)


def embed(texts: list[str]) -> list[list[float]]:
    out = []
    for i in range(0, len(texts), 100):
        r = _get_client().models.embed_content(model=settings.embed_model, contents=texts[i : i + 100])
        out.extend(e.values for e in r.embeddings)
    return out


def generate(prompt: str, schema: type[T], model: str | None = None, use_cache: bool = True) -> T:
    """Call the LLM at temperature 0 and parse the reply into `schema`."""
    from google.genai import types

    model = model or settings.schema_model
    key_material = json.dumps([model, prompt, schema.model_json_schema()], sort_keys=True)
    cache_file = settings.cache_dir / f"{hashlib.sha256(key_material.encode()).hexdigest()}.json"

    if use_cache and cache_file.exists():
        return schema.model_validate_json(cache_file.read_text(encoding="utf-8"))

    response = _get_client().models.generate_content(
        model=model,
        contents=prompt,
        config=types.GenerateContentConfig(
            temperature=0,
            response_mime_type="application/json",
            response_schema=schema,
        ),
    )
    result = schema.model_validate_json(response.text)

    cache_file.parent.mkdir(parents=True, exist_ok=True)
    cache_file.write_text(result.model_dump_json(), encoding="utf-8")
    return result
