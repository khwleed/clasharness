"""Read the opponent's name and clan from the banner crop with Claude vision."""

from __future__ import annotations

import base64

import anthropic
from pydantic import BaseModel, Field

PROMPT = """This is the top of a Clash Royale battle screen. It shows the opponent's \
player name and, under it, their clan name (absent if they have no clan).

Transcribe both exactly as displayed: keep capitalization, spacing, symbols, emoji and \
non-Latin characters. Use an empty string for clan_name if there's no clan. Set \
confident to false if either name is cut off, blurred, or you're guessing any character."""


class BannerRead(BaseModel):
    player_name: str = Field(description="Opponent's in-game name, exactly as shown")
    clan_name: str = Field(description="Opponent's clan name, or empty string if none")
    confident: bool


class ReadError(Exception):
    pass


class BannerReader:
    def __init__(self, model: str, effort: str = "low", api_key: str | None = None,
                 client: anthropic.AsyncAnthropic | None = None):
        self.model = model
        self.effort = effort
        # With no key, the SDK falls back to ANTHROPIC_API_KEY or an `ant auth login` profile.
        self.client = client or (anthropic.AsyncAnthropic(api_key=api_key) if api_key else anthropic.AsyncAnthropic())

    async def read(self, png: bytes) -> BannerRead:
        try:
            response = await self.client.beta.messages.parse(
                model=self.model,
                max_tokens=2048,
                output_config={"effort": self.effort},
                output_format=BannerRead,
                # Retry on a substitute model if a safety classifier declines.
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
                messages=[{
                    "role": "user",
                    "content": [
                        {"type": "image", "source": {
                            "type": "base64", "media_type": "image/png",
                            "data": base64.standard_b64encode(png).decode(),
                        }},
                        {"type": "text", "text": PROMPT},
                    ],
                }],
            )
        except anthropic.AuthenticationError as e:
            raise ReadError("Anthropic API key rejected") from e
        except anthropic.RateLimitError as e:
            raise ReadError("Anthropic API rate limited; try again shortly") from e
        except anthropic.APIStatusError as e:
            raise ReadError(f"Anthropic API error {e.status_code}: {e.message}") from e
        except anthropic.APIConnectionError as e:
            raise ReadError("couldn't reach the Anthropic API") from e

        if response.stop_reason == "refusal":
            raise ReadError("the model declined to read this image")
        if response.parsed_output is None:
            raise ReadError(f"no structured output (stop_reason={response.stop_reason})")
        return response.parsed_output
