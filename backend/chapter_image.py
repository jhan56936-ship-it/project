"""Gemini-based chapter illustration generator.

Given a chapter's title + summary + opening text, asks Gemini to produce a
single atmospheric illustration that matches the scene. Result is PNG bytes;
the caller is responsible for caching to disk (one image per book/page).

Tries several model names in order because Google has renamed the image-gen
model a few times and accounts have different access tiers."""

import asyncio
import os

from google import genai
from google.genai import types

# "Nano Banana" — Google's codename for gemini-2.5-flash-image. This is the
# only model we use. (Imagen-4 fallback kept ONLY for hard outages of the
# Nano Banana endpoint; tried last.)
MODEL_CANDIDATES: list[tuple[str, str]] = [
    ("gemini-2.5-flash-image", "gemini"),  # Nano Banana
    ("imagen-4.0-fast-generate-001", "imagen"),  # emergency fallback
]

_client: genai.Client | None = None


def _get_client() -> genai.Client:
    global _client
    if _client is None:
        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key:
            raise RuntimeError("GEMINI_API_KEY is not set")
        _client = genai.Client(api_key=api_key)
    return _client


def _build_prompt(title: str, summary: str, excerpt: str) -> str:
    title = (title or "").strip()
    summary = (summary or "").strip()
    excerpt = (excerpt or "").strip()[:1200]
    parts = ["A single atmospheric book illustration for this chapter."]
    if title:
        parts.append(f'Chapter title: "{title}"')
    if summary:
        parts.append(f"Chapter summary: {summary}")
    if excerpt:
        parts.append(f"Opening excerpt: {excerpt}")
    parts.append(
        "Style: painterly, cinematic, soft lighting, evocative mood that matches "
        "the chapter. No text, no captions, no logos. Wide landscape orientation. "
        "Render the scene faithfully — characters, setting, time of day, weather "
        "should reflect the text."
    )
    return "\n\n".join(parts)


def _extract_image_bytes_from_content(response) -> bytes | None:
    for cand in getattr(response, "candidates", None) or []:
        content = getattr(cand, "content", None)
        if not content:
            continue
        for part in getattr(content, "parts", None) or []:
            inline = getattr(part, "inline_data", None)
            if inline and getattr(inline, "data", None):
                return inline.data
    return None


def _extract_image_bytes_from_images(response) -> bytes | None:
    imgs = getattr(response, "generated_images", None) or []
    for gi in imgs:
        img = getattr(gi, "image", None)
        data = getattr(img, "image_bytes", None) if img else None
        if data:
            return data
    return None


def _call_gemini_image_model(model: str, prompt: str) -> bytes | None:
    client = _get_client()
    resp = client.models.generate_content(
        model=model,
        contents=prompt,
        config=types.GenerateContentConfig(
            response_modalities=["IMAGE", "TEXT"],
        ),
    )
    return _extract_image_bytes_from_content(resp)


def _call_imagen_model(model: str, prompt: str) -> bytes | None:
    client = _get_client()
    resp = client.models.generate_images(
        model=model,
        prompt=prompt,
        config=types.GenerateImagesConfig(
            number_of_images=1,
            aspect_ratio="16:9",
        ),
    )
    return _extract_image_bytes_from_images(resp)


def _generate_sync(prompt: str) -> bytes:
    last_err: Exception | None = None
    for model, kind in MODEL_CANDIDATES:
        try:
            if kind == "imagen":
                data = _call_imagen_model(model, prompt)
            else:
                data = _call_gemini_image_model(model, prompt)
            if data:
                print(f"[chapter_image] generated via {model}")
                return data
            last_err = RuntimeError(f"{model} returned no image data")
        except Exception as exc:
            last_err = exc
            print(f"[chapter_image] {model} failed: {exc!r}")
            continue
    raise RuntimeError(
        f"All image models failed. Last error: {last_err!r}. "
        "Check your Gemini API key access tier — image generation may require "
        "a paid project."
    )


async def generate_chapter_image(title: str, summary: str, excerpt: str) -> bytes:
    """Returns PNG bytes for the chapter, or raises on failure."""
    prompt = _build_prompt(title, summary, excerpt)
    return await asyncio.to_thread(_generate_sync, prompt)
