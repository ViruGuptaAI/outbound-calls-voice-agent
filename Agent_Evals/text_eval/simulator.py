"""Adaptive LLM customer that role-plays each scenario and reacts to what Asha
actually said (so stumbles get natural recovery instead of a fixed-script cascade)."""

from __future__ import annotations

import os

os.environ.setdefault("AZURE_TOKEN_CREDENTIALS", "dev")

from azure.identity import DefaultAzureCredential, get_bearer_token_provider
from openai import AzureOpenAI


SIM_ENDPOINT = "https://foundryresourcefordemos.cognitiveservices.azure.com/"
SIM_API_VERSION = "2024-10-21"
SIM_MODEL = "gpt-4.1"  # stronger role-player, different from the agent (gpt-4.1-mini)

_token_provider = get_bearer_token_provider(
    DefaultAzureCredential(), "https://cognitiveservices.azure.com/.default"
)
_client = AzureOpenAI(
    azure_endpoint=SIM_ENDPOINT, azure_ad_token_provider=_token_provider, api_version=SIM_API_VERSION
)

END_TOKEN = "<END>"


_SYSTEM_TEMPLATE = """You are role-playing a bank CUSTOMER on an outbound phone call. Contoso Bank's
virtual assistant "Asha" has called you. Stay fully in character as the customer.

# YOUR PERSONA / BEHAVIOUR FOR THIS CALL
{behaviour}

# HOW TO SPEAK
- Speak naturally in Hindi/Hinglish (Devanagari script), ONE short realistic
  utterance per turn — like a real Indian customer on a phone call.
- Do NOT narrate, explain, or add stage directions. Say only what the customer says.
- REACT to what Asha JUST said. If she asks something, answer as your persona
  dictates. If she mishears or asks you to repeat, repeat or rephrase naturally.
- Reveal specific facts (age, PIN code, product choice, etc.) only when asked, and
  exactly as your behaviour specifies.
- Do not be robotic; a little impatience or casual phrasing is fine if it fits.

# ENDING
When the call has clearly ended — Asha has spoken a closing/goodbye, OR your goal is
fully achieved and there is nothing left for you to say — reply with exactly: {end}
"""


class SimulatedCustomer:
    def __init__(self, behaviour: str):
        system = _SYSTEM_TEMPLATE.format(behaviour=behaviour.strip(), end=END_TOKEN)
        self.messages: list[dict] = [{"role": "system", "content": system}]

    def observe_agent(self, agent_utterance: str) -> None:
        # From the customer's point of view, the agent is the other party (user role).
        self.messages.append({"role": "user", "content": f"[Asha]: {agent_utterance}"})

    def next_utterance(self) -> str | None:
        resp = _client.chat.completions.create(
            model=SIM_MODEL, messages=self.messages, temperature=0.7, max_tokens=200
        )
        text = (resp.choices[0].message.content or "").strip()
        self.messages.append({"role": "assistant", "content": text})
        if END_TOKEN in text:
            return None
        return text
