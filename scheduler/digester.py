# scheduler/digester.py
"""Haiku-powered session digest generator."""
import logging
from typing import Optional

log = logging.getLogger(__name__)

DIGEST_MAX_CHARS = 6000

DIGEST_PROMPT_TEMPLATE = """You are summarizing a trading session for the AI fund manager that will run the NEXT session.
Your summary must preserve the reasoning chain so the next session starts with full context.

Session type: {session_name}
Session response:
{response}

Write a structured digest with exactly these 4 sections. Be specific — include tickers, prices, indicator values, and conditions wherever they appear in the response.

DECISIONS MADE:
For each action taken: what was done, why (specific signals/data that justified it), and what conditions would invalidate it.
Example: "Opened NVDA long at $875 — RSI 58 pullback with ADX 31 confirming trend, volume 1.8x avg. Thesis breaks if price closes below 9-EMA ($869) or volume dries up."

DECISIONS NOT MADE:
For each setup considered but skipped: what it was, why it was passed on, and what would need to change to make it actionable.
Example: "Skipped TSLA short — setup valid but VIX spiking fast, regime shift risk. Would reconsider if VIX stabilizes and TSLA breaks below $188 support on volume."

OPEN UNCERTAINTIES:
What was unclear, ambiguous, or being monitored. What information would resolve it.
Example: "AAPL thesis unclear — strong RS but earnings tomorrow. Watching after-hours reaction before forming a view."

KEY CONDITIONS TO WATCH:
Specific price levels, events, or signals that matter for active positions or pending setups.
Example: "NVDA: 9-EMA at $869 is the line. SPY: needs to hold $520 for bull thesis to remain intact."

Keep each section to 2-4 bullet points. Total output under 350 words."""


class SessionDigester:
    def __init__(self, api_key: str, _client=None):
        if _client is not None:
            self.client = _client
        else:
            import anthropic
            self.client = anthropic.Anthropic(api_key=api_key)

    def summarize(self, raw_response: str, session_name: str) -> str:
        """Return a 4-section structured digest. Returns empty string on any error."""
        try:
            truncated = raw_response[:DIGEST_MAX_CHARS]
            prompt = DIGEST_PROMPT_TEMPLATE.format(
                session_name=session_name,
                response=truncated,
            )
            response = self.client.messages.create(
                model="claude-haiku-4-5",
                max_tokens=400,
                messages=[{"role": "user", "content": prompt}],
            )
            return response.content[0].text
        except Exception as exc:
            log.warning("Session digest failed for %s: %s", session_name, exc)
            return ""
