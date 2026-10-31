"""Read the opponent's name and clan from the banner crop with a vision model on OpenRouter."""

from __future__ import annotations

import base64
import json
import re

import httpx
from pydantic import BaseModel, Field, ValidationError

PROMPT = """This is the top of a Clash Royale battle screen. It shows the opponent's \
player name and, under it, their clan name (absent if they have no clan).

Transcribe both exactly as displayed: keep capitalization, spacing, symbols, emoji and \
non-Latin characters. Use an empty string for clan_name if there's no clan. Set \
confident to false if either name is cut off, blurred, or you're guessing any character.

Reply with only a JSON object: {"player_name": "...", "clan_name": "...", "confident": true}"""


class BannerRead(BaseModel):
    player_name: str = Field(description="Opponent's in-game name, exactly as shown")
    clan_name: str = Field(description="Opponent's clan name, or empty string if none")
    confident: bool


class ReadError(Exception):
    pass


def _response_format() -> dict:
    schema = BannerRead.model_json_schema()
    schema["additionalProperties"] = False
    return {"type": "json_schema", "json_schema": {"name": "banner_read", "strict": True, "schema": schema}}


def parse_reply(text: str) -> BannerRead:
    """Accept a bare JSON object, or one wrapped in a code fence or surrounding prose."""
    match = re.search(r"\{.*\}", text or "", re.S)
    if not match:
        raise ReadError(f"model didn't return JSON: {text[:120]!r}")
    try:
        return BannerRead.model_validate(json.loads(match.group(0)))
    except (ValueError, ValidationError) as e:
        raise ReadError(f"couldn't parse model reply: {e}") from e


class BannerReader:
    def __init__(self, model: str, api_key: str | None,
                 base_url: str = "https://openrouter.ai/api/v1",
                 transport: httpx.AsyncBaseTransport | None = None):
        if not api_key:
            raise ReadError("no OpenRouter API key (set [openrouter].api_key or OPENROUTER_API_KEY)")
        self.model = model
        self.client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            headers={
                "Authorization": f"Bearer {api_key}",
                # Optional OpenRouter attribution headers.
                "HTTP-Referer": "https://github.com/khwleed/clasharness",
                "X-Title": "clasharness",
            },
            timeout=30.0,
            transport=transport,
        )

    async def read(self, png: bytes) -> BannerRead:
        image_url = "data:image/png;base64," + base64.standard_b64encode(png).decode()
        body = {
            "model": self.model,
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "text", "text": PROMPT},
                    {"type": "image_url", "image_url": {"url": image_url}},
                ],
            }],
            "response_format": _response_format(),
            "temperature": 0,
            "max_tokens": 300,
            # Transcription needs no thinking. Reasoning models otherwise spend the whole
            # token budget reasoning and return empty content.
            "reasoning": {"enabled": False},
        }
        try:
            r = await self.client.post("/chat/completions", json=body)
        except httpx.HTTPError as e:
            raise ReadError(f"couldn't reach OpenRouter: {e}") from e
        finally:
            await self.client.aclose()

        try:
            data = r.json()
        except ValueError as e:
            raise ReadError(f"OpenRouter returned {r.status_code}: {r.text[:200]}") from e
        if r.status_code != 200 or "error" in data:
            err = data.get("error")
            err = err if isinstance(err, dict) else {"message": str(err or r.text[:200])}
            raise ReadError(f"OpenRouter error {err.get('code', r.status_code)}: {err.get('message')}")
        choices = data.get("choices") or []
        if not choices:
            raise ReadError("OpenRouter returned no choices")
        content = choices[0].get("message", {}).get("content") or ""
        if not content and choices[0].get("finish_reason") == "length":
            raise ReadError(f"{self.model} hit max_tokens before answering (reasoning not disabled?)")
        return parse_reply(content)
