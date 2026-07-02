from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import socket
import sys
import time
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import quote

from azure.identity.aio import AzureCliCredential, ManagedIdentityCredential
from dotenv import load_dotenv
from quart import Quart, websocket
from websockets.asyncio.client import connect as ws_connect
from websockets.exceptions import ConnectionClosed

# Ensure the server directory is on sys.path for module imports
_server_dir = str(Path(__file__).resolve().parent)
if _server_dir not in sys.path:
    sys.path.insert(0, _server_dir)

from campaigns import CAMPAIGN_REGISTRY, get_campaign, DEFAULT_CAMPAIGN  # noqa: E402
from campaigns.tool_schemas import END_CALL, ESCALATE_TO_HUMAN  # noqa: E402
from crm_tools import TOOL_FUNCTIONS  # noqa: E402
from playbooks import get_playbook  # noqa: E402

load_dotenv(override=True)

# ──────────────────────────────────────────────────────────────────────────────
# Configuration
# ──────────────────────────────────────────────────────────────────────────────
VOICE_LIVE_ENDPOINT = os.getenv("AZURE_VOICE_LIVE_ENDPOINT", "")
VOICE_LIVE_API_KEY = os.getenv("AZURE_VOICE_LIVE_API_KEY", "")
VOICE_LIVE_MODEL = os.getenv("VOICE_LIVE_MODEL", "gpt-4.1-mini")
MANAGED_IDENTITY_CLIENT_ID = os.getenv(
    "AZURE_USER_ASSIGNED_IDENTITY_CLIENT_ID", ""
)

# ── BYO LLM ─────────────────────────────────────────────────────────────────
BYOM_PROFILE = os.getenv("BYOM_PROFILE", "")
FOUNDRY_RESOURCE_OVERRIDE = os.getenv("FOUNDRY_RESOURCE_OVERRIDE", "")

# ── Conversation summarization (token optimization for long calls) ───────────
SUMMARY_EVERY_N_TURNS = int(os.getenv("SUMMARY_EVERY_N_TURNS", "18"))
SUMMARY_KEEP_RECENT = int(os.getenv("SUMMARY_KEEP_RECENT", "6"))

# ── Call termination ─────────────────────────────────────────────────────────
# Hang up the call automatically if the customer stays silent this long.
IDLE_TIMEOUT_SECONDS = int(os.getenv("IDLE_TIMEOUT_SECONDS", "60"))
# How often the idle monitor wakes up to check for silence.
IDLE_CHECK_INTERVAL_SECONDS = 2

# ──────────────────────────────────────────────────────────────────────────────
# Logging
# ──────────────────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=getattr(logging, os.getenv("LOG_LEVEL", "INFO")),
    format="%(asctime)s %(name)s %(levelname)s: %(message)s",
)
logger = logging.getLogger("outbound_rm")
logging.getLogger("websockets").setLevel(logging.WARNING)
logging.getLogger("azure").setLevel(logging.WARNING)
# debug=True enables asyncio slow-callback logging, which spams WARNING/INFO on
# Windows (az.CMD spawns, file-watcher). Silence it while keeping hot-reload.
logging.getLogger("asyncio").setLevel(logging.ERROR)

# ── Tool name redaction (safety net for agent transcripts) ───────────────────
import re as _re
_TOOL_NAMES_FOR_REDACTION = [
    "get_customer_profile", "get_customer_summary", "get_eligibility_assessment",
    "get_credit_card_details", "get_credit_card_transactions", "get_reward_points",
    "get_card_spending_analysis", "check_card_upgrade_eligibility",
    "get_active_loans", "get_loan_product_details", "get_preapproved_offers",
    "get_negotiation_terms", "calculate_emi", "get_competitor_rates",
    "check_cibil_score", "check_rbi_repo_rate", "assess_collateral",
    "get_account_details", "get_fixed_deposits", "get_recurring_deposits",
    "get_savings_transactions", "get_fd_rate_card", "get_debit_card_details",
    "get_investments", "get_all_transactions", "play_hold_music",
    "get_card_dues", "get_payment_history", "get_settlement_options",
    "record_payment_commitment", "send_payment_link", "end_call",
    "escalate_to_human",
]
_TOOL_NAME_PATTERN = _re.compile(
    r"\b(?:" + "|".join(_re.escape(n) for n in _TOOL_NAMES_FOR_REDACTION) + r")\b",
    _re.IGNORECASE,
)


def _redact_tool_names(text: str) -> str:
    """Replace any leaked tool names in agent speech with 'our system'."""
    return _TOOL_NAME_PATTERN.sub("our system", text)


# ── Friendly labels for tool calls (shown in browser UI) ─────────────────────
TOOL_DISPLAY_LABELS = {
    "get_customer_profile": "Looking up your profile",
    "get_customer_summary": "Pulling your account summary",
    "get_eligibility_assessment": "Running eligibility check",
    "get_credit_card_details": "Fetching credit card details",
    "get_credit_card_transactions": "Loading recent transactions",
    "get_reward_points": "Checking reward points balance",
    "get_card_spending_analysis": "Analyzing spending patterns",
    "check_card_upgrade_eligibility": "Checking upgrade eligibility",
    "get_active_loans": "Fetching active loans",
    "get_loan_product_details": "Looking up loan products",
    "get_preapproved_offers": "Checking pre-approved offers",
    "get_negotiation_terms": "Invoking risk model",
    "calculate_emi": "Calculating EMI",
    "get_competitor_rates": "Fetching market rates",
    "check_cibil_score": "Pulling CIBIL score",
    "check_rbi_repo_rate": "Checking RBI repo rate",
    "get_account_details": "Fetching account details",
    "get_fixed_deposits": "Loading fixed deposits",
    "get_recurring_deposits": "Loading recurring deposits",
    "get_savings_transactions": "Loading recent transactions",
    "get_fd_rate_card": "Fetching FD rate card",
    "get_debit_card_details": "Fetching debit card info",
    "get_investments": "Loading investment portfolio",
    "get_all_transactions": "Pulling transaction history",
    "play_hold_music": "Checking with supervisor",
    "assess_collateral": "Assessing property collateral",
    "get_card_dues": "Pulling overdue card position",
    "get_payment_history": "Reviewing payment history",
    "get_settlement_options": "Checking authorised options",
    "record_payment_commitment": "Recording promise-to-pay",
    "send_payment_link": "Sending secure payment link",
}

# ── Cached credential (avoids spawning az.cmd on every connection) ────────────
_cached_credential = None
_cached_token = None
_token_expiry = 0  # epoch seconds


# ──────────────────────────────────────────────────────────────────────────────
# Voice Live session configuration builder
# ──────────────────────────────────────────────────────────────────────────────
def build_session_config(
    campaign_key: str,
    customer_name: str = "",
) -> dict:
    """
    Build the session.update payload for an outbound campaign agent.
    Assembles: customer context header + lean base prompt + playbook workflow,
    and filters tools to only what the playbook needs.
    """
    campaign = get_campaign(campaign_key)
    base_prompt = campaign["prompt"]

    # Look up the campaign's playbook + its required tool names
    playbook_text, playbook_tool_names = get_playbook(campaign_key)

    # Assemble instructions: base prompt + playbook
    instructions = base_prompt
    if playbook_text:
        instructions += "\n" + playbook_text

    # Universal guardrail — appended to every agent's instructions
    instructions += (
        "\n\n# CRITICAL: NEVER EXPOSE TOOL OR FUNCTION NAMES\n"
        "- NEVER say tool names, function names, or API names to the customer. "
        "Examples of what you must NEVER say: 'get_preapproved_offers', 'assess_collateral', 'get_card_dues', 'get_settlement_options', etc.\n"
        "- Instead of 'Let me call get_preapproved_offers', say 'Let me pull up the offer we have for you.'\n"
        "- Instead of 'Running get_settlement_options', say 'Let me see what options I can offer you.'\n"
        "- A real bank officer would NEVER say function names. Speak naturally.\n"
    )

    # Universal delivery style — makes the agent sound like a human, not a bot.
    instructions += (
        "\n\n# DELIVERY — SOUND LIKE A HUMAN, NOT A BOT\n"
        "- ASK BEFORE YOU EXPLAIN — when the customer asks 'how does it work' / 'tell me more', "
        "or before you can quantify anything, do NOT dump product specs (funding %, tenure, "
        "rate, processing fee) in one breath. FIRST collect the inputs you need by asking ONE "
        "short question at a time — e.g. new or used car, which car / on-road price, how much "
        "loan they need, when they plan to buy. Gather their inputs, THEN give ONE tailored line.\n"
        "- WHEN GATHERING INFO, keep the turn to ONE short question (max 1–2 sentences). Do NOT "
        "precede the question with a paragraph of explanation. Question first, details later.\n"
        "- DRIP information — share ONE number or ONE benefit per turn, then pause and ask a "
        "question or check reaction. Do NOT stack multiple EMIs, rates, and figures into a "
        "single reply. Never quote more than one EMI or one rate in a turn.\n"
        "- ROUND spoken money — say 'about eighteen and a half thousand a month' or 'around "
        "₹18,500', never exact paise like '₹18,661'. Precision to the rupee sounds computed.\n"
        "- Be a CONVERSATIONAL CHAMELEON — match the customer's pace and energy. If they sound "
        "rushed, keep it short. If they hesitate, slow down and simplify. Use light back-channels "
        "('right', 'got it', 'makes sense') so they feel heard.\n"
        "- VARY your close and only push once interest shows. Do NOT end every turn with the same "
        "'Shall I proceed / start the application?' Read their temperature first, then make a "
        "specific, low-friction next-step offer.\n"
        "- Keep turns to 2–3 short sentences. This is a live phone call, not a monologue.\n"
    )

    # Universal language matching — the full reply MUST match the language of the
    # short filler and of the customer, so interim + main response never diverge.
    instructions += (
        "\n\n# LANGUAGE — MIRROR THE CUSTOMER, STAY CONSISTENT\n"
        "- ALWAYS reply in the SAME language the customer is speaking. If they speak Hindi, "
        "answer fully in Hindi; if English, answer in English. Do NOT switch to English just "
        "because the data or product names are in English.\n"
        "- Your short holding phrases (fillers) and your full answer MUST be in the SAME language. "
        "Never start a wait in Hindi and then deliver the answer in English — that confuses the "
        "customer. Pick the customer's language and stay in it for the entire turn.\n"
        "- If the customer switches language mid-call, switch with them on your very next turn.\n"
        "- Do NOT splice full English clauses into a Hindi sentence (e.g. 'मैं समझ रही हूँ, and I "
        "want to be transparent'). Say it in Hindi. Only keep short fixed product terms (EMI, "
        "interest rate, processing fee, pre-approved) in English.\n"
        "- After a tool result, hold music, or any system prompt, STILL reply in the customer's "
        "current language — a tool/data lookup is never a reason to switch to English.\n"
        "- Hinglish is fine when the customer mixes — keep loan terms (EMI, interest rate, "
        "pre-approved) in English but frame the sentence in Hindi if that is how they speak.\n"
        "- YOU ARE A WOMAN — speak with FEMININE grammar. In Hindi/Hinglish, verbs and "
        "self-references must take the feminine form when you refer to yourself. Say "
        "'मैं बता रही हूँ' (not 'बता रहा हूँ'), 'मैं कर दूँगी' / 'भेज दूँगी' (not 'करूँगा' / "
        "'भेज दूँगा'), 'मैं समझ सकती हूँ' (not 'समझ सकता हूँ'), 'मैंने देखा, आपके लिए एक ऑफ़र है'. "
        "NEVER use masculine self-conjugations like 'करूँगा', 'बताऊँगा', 'रहा हूँ', 'सकता हूँ'. "
        "This applies to every Hindi turn, including after tool results.\n"
    )

    # Universal call-ending etiquette — how and WHEN to hang up gracefully.
    instructions += (
        "\n\n# ENDING THE CALL — ALWAYS ASK PERMISSION FIRST\n"
        "- When the conversation is genuinely concluded (goal met, promise-to-pay recorded, "
        "question fully answered, or the customer clearly wants to go), do NOT hang up "
        "silently. FIRST ask permission to end — e.g. 'Is there anything else I can help you "
        "with, or shall I let you go?' or 'If there's nothing else, may I end the call here?'\n"
        "- ONLY after the customer confirms they have nothing else (a clear 'no / that's all / "
        "you can end it / thanks, bye'), call the `end_call` tool. In the SAME turn, first say "
        "a short warm farewell (e.g. 'Thank you for your time, {name} — take care, goodbye.'), "
        "then call `end_call`. The call ends automatically once you finish that farewell.\n"
        "- NEVER call `end_call` while the customer still has a question, is mid-sentence, is "
        "asking for more, or has not agreed to end. When in doubt, ask — do NOT hang up.\n"
        "- If the customer says something new or asks another question after you've asked to "
        "end, DROP the idea of ending and keep helping them.\n"
    )

    # Universal human-escalation etiquette — when and how to hand off to a person.
    instructions += (
        "\n\n# ESCALATING TO A HUMAN AGENT\n"
        "- Escalate to a human senior officer with the `escalate_to_human` tool when ANY of "
        "these are true: the customer is clearly frustrated/angry or negotiating unreasonably "
        "and you cannot satisfy them within your authority; the customer explicitly asks for a "
        "human or a senior person; or a human is genuinely required to proceed. Do this rather "
        "than repeating the same refusal over and over — if you've tried a few times and the "
        "customer is still not satisfied, offer to escalate.\n"
        "- ALWAYS confirm briefly BEFORE escalating — e.g. 'I'll escalate this to a senior "
        "officer who will call you back — is that okay?'. Only call the tool once they agree "
        "(or clearly demand a human).\n"
        "- When you call `escalate_to_human`, in the SAME turn tell the customer: 'I've noted "
        "your request and I'm escalating this to a human agent who will call you back shortly.' "
        "Then STOP — the call ends automatically. Do NOT promise any specific outcome the human "
        "has not approved.\n"
        "- ALSO use `escalate_to_human` with escalation_type='resolved_handoff' at the END of a "
        "SUCCESSFULLY resolved call when a human must action the next steps (e.g. process a "
        "disbursal, verify documents, honour a recorded promise-to-pay). In that case thank the "
        "customer warmly and say you've passed the details to the team for follow-up.\n"
        "- Provide a clear `summary` and concrete `action_items` (next steps) whenever you "
        "escalate — this is what the human agent receives.\n"
        "- FIELD DISCIPLINE: `reason` is a short human-readable sentence explaining WHY you "
        "are handing off (e.g. 'Customer accepted the offer; application needs a human to "
        "complete disbursal.'). It is NOT a category — NEVER put 'resolved_handoff' or "
        "'unresolved' in `reason`; those values belong ONLY in `escalation_type`.\n"
    )

    # Inject customer name as context (the opening line is handled by response.create)
    if customer_name:
        first_name = customer_name.split()[0]
        instructions = (
            f"CUSTOMER YOU ARE CALLING: {customer_name}\n"
            f"Address the customer as {first_name}. This is an OUTBOUND call that YOU placed. "
            f"Your VERY FIRST turn is the opening: greet {first_name}, introduce yourself by name "
            f"AND state you are calling from Contoso Bank, give the reason, and ask permission. "
            f"On EVERY turn AFTER that opening, do NOT greet or re-introduce yourself again.\n\n"
            + instructions
        )

    config = {
        "type": "session.update",
        "session": {
            "instructions": instructions,
            "modalities": ["text", "audio"],
            # ── Turn detection (VAD) ─────────────────────────────────────
            "turn_detection": {
            "type": "azure_semantic_vad_multilingual",
            "threshold": 0.6,
            "prefix_padding_ms": 700,
            "silence_duration_ms": 400,
            "create_response": True,
            "interrupt_response": True,
            "speech_duration_ms": 200,
            # "speech_duration_assistant_speaking_ms": 500,
            "remove_filler_words": False,
            "auto_truncate": True,
            "appended_text_after_truncation": " -- [user interrupted | response incomplete]"
            },
            # ── Interim responses (native filler for tool calls / latency) ──
            # Requires cascaded mode (text LLM + azure-speech voice) — which we
            # use (BYOM chat-completion + azure-standard voice). Bridges the
            # silence while a tool runs so the agent never goes dead-air.
            "interim_response": {
                "type": "llm_interim_response",
                "triggers": ["tool", "latency"],
                "latency_threshold_ms": 2000,
                "max_completion_tokens": 30,
                "instructions": (
                    "You are a warm Contoso Bank phone agent. Produce ONE very short, "
                    "natural filler to cover a brief wait while data loads — e.g. "
                    "'Let me quickly pull that up…', 'One moment, checking that for you…', "
                    "'Give me a second…'. Match the customer's language (English or Hindi). "
                    "Do NOT quote any numbers, rates, or account details. Do NOT add a filler "
                    "to every turn — only when there is a real wait. Never say tool or function names."
                ),
            },
            # ── Input audio transcription ────────────────────────────────
            "input_audio_transcription": {
                "model": "azure-speech",
                "language": "en-IN,hi-IN",
                "phrase_list": [
                    "Contoso Bank", "credit card", "debit card",
                    "home loan", "car loan", "vehicle loan", "balance transfer",
                    "pre-approved", "preapproved", "processing fee", "top-up loan",
                    "EMI", "CIBIL", "KYC", "UPI", "NEFT", "RTGS", "IMPS",
                    "interest rate", "outstanding", "minimum due", "overdue",
                    "late fee", "settlement", "promise to pay", "payment link",
                    "Priya", "Kavya", "Neha",
                    "प्रिया", "काव्या", "नेहा",
                ],
            },
            # ── Noise / echo handling ────────────────────────────────────
            "input_audio_noise_reduction": {
                "type": "azure_deep_noise_suppression",
            },
            "input_audio_echo_cancellation": {
                "type": "server_echo_cancellation",
                "reference_source": "server",
                "channels": 1
            },
            # ── TTS voice ────────────────────────────────────────────────
            "voice": {
            "name": "en-IN-Diya:DragonHDV2.3Neural",
            "type": "azure-standard",
            "temperature": 0.8,
            "style": "empathetic",
            "pitch": "call center slightly fast",
            "rate": "adaptive",
            "volume": "-5%"
        },
            "input_audio_sampling_rate": 24000,
            # ── Model behaviour ──────────────────────────────────────────
            # "temperature": 0.1,
            "reasoning_effort":"none",
            "max_response_output_tokens": "320",
        },
    }

    # Add tools — filter to only what the playbook needs
    if campaign["tools"]:
        if playbook_tool_names is not None:
            tools = [t for t in campaign["tools"] if t["name"] in playbook_tool_names]
        else:
            tools = list(campaign["tools"])
    else:
        tools = []
    # `end_call` is universal — every agent can gracefully hang up the call.
    if not any(t.get("name") == "end_call" for t in tools):
        tools.append(END_CALL)
    # `escalate_to_human` is universal — every agent can hand off to a human.
    if not any(t.get("name") == "escalate_to_human" for t in tools):
        tools.append(ESCALATE_TO_HUMAN)
    config["session"]["tools"] = tools
    config["session"]["tool_choice"] = "auto"

    return config


# ──────────────────────────────────────────────────────────────────────────────
# Authentication helper
# ──────────────────────────────────────────────────────────────────────────────
async def get_auth_headers() -> dict:
    """Build authentication headers for Voice Live WebSocket.
    Caches the credential and token to avoid spawning az.cmd on every call.
    """
    global _cached_credential, _cached_token, _token_expiry
    headers = {"x-ms-client-request-id": str(uuid.uuid4())}

    if VOICE_LIVE_API_KEY:
        headers["api-key"] = VOICE_LIVE_API_KEY
        logger.info("Auth: using API key")
        return headers

    scope = "https://cognitiveservices.azure.com/.default"
    now = time.time()

    # Reuse cached token if still valid (with 5-min buffer)
    if _cached_token and _token_expiry > now + 300:
        headers["Authorization"] = f"Bearer {_cached_token}"
        logger.info("Auth: using cached token (expires in %ds)", int(_token_expiry - now))
        return headers

    # Create credential if not cached
    if _cached_credential is None:
        if MANAGED_IDENTITY_CLIENT_ID:
            _cached_credential = ManagedIdentityCredential(
                client_id=MANAGED_IDENTITY_CLIENT_ID
            )
            logger.info("Auth: created Managed Identity credential")
        else:
            _cached_credential = AzureCliCredential()
            logger.info("Auth: created Azure CLI credential")

    t0 = time.time()
    token = await _cached_credential.get_token(scope)
    elapsed = time.time() - t0
    _cached_token = token.token
    _token_expiry = token.expires_on
    headers["Authorization"] = f"Bearer {_cached_token}"
    logger.info("Auth: fetched token in %.1fs (expires in %ds)",
                elapsed, int(_token_expiry - now))
    return headers


def build_wss_url() -> str:
    """Build the Voice Live WebSocket URL."""
    endpoint = VOICE_LIVE_ENDPOINT.rstrip("/")
    model = VOICE_LIVE_MODEL.strip()
    # Only convert the base endpoint to wss://, not query-parameter URLs
    wss_endpoint = endpoint.replace("https://", "wss://")
    url = (
        f"{wss_endpoint}/voice-live/realtime"
        f"?api-version=2026-01-01-preview&model={model}&debug=on"
    )
    if BYOM_PROFILE:
        url += f"&profile={BYOM_PROFILE}"
        if FOUNDRY_RESOURCE_OVERRIDE:
            url += f"&foundry-resource-override={FOUNDRY_RESOURCE_OVERRIDE}&debug=on"
    return url


# ──────────────────────────────────────────────────────────────────────────────
# Voice Live Session — manages a single call lifecycle
# ──────────────────────────────────────────────────────────────────────────────
class VoiceLiveSession:
    """
    Manages a single OUTBOUND Voice Live call for a chosen campaign.

    Flow:
      1. Operator picks a campaign + customer in the browser and starts the call.
      2. Browser connects via /web/ws and sends {campaignId, customerId}.
      3. Server opens a WebSocket to the Voice Live API and configures the
         campaign agent (no triage, no handoff — the campaign is pre-selected).
      4. The agent INITIATES the call: it introduces itself and states the
         purpose (sell a loan / recover a due), then drives the conversation.
    """

    def __init__(
        self,
        browser_ws,
        customer_id: str = "rajesh",
        campaign_key: str = DEFAULT_CAMPAIGN,
    ):
        self.browser_ws = browser_ws
        self.vl_ws: Any = None
        self._send_queue: asyncio.Queue = asyncio.Queue()
        self._user_speech_end_ts = None
        self._first_audio_latency_logged = False
        self._campaign_key = campaign_key if campaign_key in CAMPAIGN_REGISTRY else DEFAULT_CAMPAIGN
        self._agent_name = CAMPAIGN_REGISTRY[self._campaign_key]["name"]
        self._call_id = str(uuid.uuid4())[:8]
        self._customer_id = customer_id
        self._customer_name = ""
        self._response_active = False  # True while a response is being generated
        self._pending_response_create = False  # deferred response.create
        self._pending_hold_music: int | None = None  # deferred hold music duration
        self._last_vl_event_ts: float = time.monotonic()  # heartbeat tracking
        self._response_watchdog: asyncio.Task | None = None  # safety net for dead sessions
        self._last_transcription_empty: bool = True  # track if last speech had real content
        self._tool_call_counts: dict[str, int] = {}  # loop guard: per-tool call count within a response cycle
        self._conversation_item_ids: list[str] = []  # ordered list of conversation item IDs
        self._deleted_item_ids: set[str] = set()  # ids already deleted / known removed (compaction guard)
        self._retrieved_items: dict[str, dict] = {}  # item_id → item data (populated at close)
        self._retrieve_pending = 0  # counter for in-flight retrieval requests
        self._transcript_log: list[tuple[str, str]] = []  # local (role, text) pairs for compaction
        # ── Summarization state ──────────────────────────────────────────────
        self._user_turn_count = 0
        self._last_summarized_at_turn = 0
        self._compaction_running = False
        # ── Barge-in transcript truncation ────────────────────────────────────
        self._last_agent_transcript: tuple[str, str] | None = None  # (text, agent_name)
        self._response_audio_bytes: int = 0  # audio bytes sent in current response
        self._response_truncated: bool = False  # set when truncated fires before transcript.done
        self._truncation_audio_end_ms: int = 0  # audio_end_ms from the truncation event
        # ── Call termination state ────────────────────────────────────────────
        self._call_ended: bool = False  # True once we've begun tearing the call down
        self._pending_end_call: bool = False  # agent asked to hang up; fire on response.done
        self._end_call_reason: str = ""  # reason string for the hang-up
        self._user_speaking: bool = False  # True between speech_started and speech_stopped
        self._last_user_activity_ts: float = time.monotonic()  # last customer speech/turn
        self._idle_monitor: asyncio.Task | None = None  # silence-timeout watchdog

    # ── 1. Connect to Voice Live ─────────────────────────────────────────

    async def start(self, auth_task=None):
        """Open WebSocket to Voice Live, send triage config, spawn loops."""
        url = build_wss_url()
        # Use pre-started auth task if available, otherwise fetch fresh
        if auth_task:
            headers = await auth_task
        else:
            headers = await get_auth_headers()

        logger.info("[%s] Connecting to Voice Live: %s", self._call_id, url)
        try:
            # Run WebSocket connect and CRM lookup in parallel
            from crm_tools import get_customer_profile

            async def _connect_ws():
                return await ws_connect(
                    url, additional_headers=headers, family=socket.AF_INET,
                    max_size=16 * 1024 * 1024,  # 16MB — retrieved items include audio
                )

            async def _lookup_customer():
                # SQLite is sync but fast — wrap for gather()
                return get_customer_profile(self._customer_id)

            ws_result, profile = await asyncio.gather(
                _connect_ws(), _lookup_customer()
            )
            self.vl_ws = ws_result
            self._customer_name = profile.get("name", "") if isinstance(profile, dict) else ""
        except Exception as exc:
            logger.error(
                "[%s] Voice Live connection failed: %s", self._call_id, exc
            )
            await self._send_to_browser(
                json.dumps({"Kind": "AgentTranscription",
                 "Text": f"Connection failed: {exc}",
                 "Agent": "System"})
            )
            return
        logger.info(
            "[%s] Voice Live connected — outbound campaign '%s' to %s",
            self._call_id,
            self._campaign_key,
            self._customer_name or self._customer_id,
        )

        # Configure the session with the selected campaign agent + playbook
        await self._send_json(
            build_session_config(self._campaign_key, customer_name=self._customer_name)
        )

        # Trigger the OUTBOUND opening: the agent introduces itself and states
        # the purpose of the call, then asks permission to proceed.
        campaign = CAMPAIGN_REGISTRY[self._campaign_key]
        first_name = self._customer_name.split()[0] if self._customer_name else "there"
        opening_instructions = (
            f"This is the very start of an OUTBOUND phone call that YOU placed to {first_name}. "
            f"You MUST introduce yourself first — say clearly 'Hi {first_name}, this is "
            f"{campaign['agent_name']} calling from Contoso Bank.' Do not skip your name or the bank. "
            f"Then give ONE short trigger-based reason for the call. {campaign['opening_purpose']} "
            f"{campaign.get('opening_ask', 'Then ask if this is a good time to talk for a couple of minutes.')} "
            f"Keep it warm, natural, and under three sentences. Do NOT quote any specific "
            f"numbers or account details yet."
        )
        await self._send_json({
            "type": "response.create",
            "response": {
                "modalities": ["audio", "text"],
                "instructions": opening_instructions,
            },
        })

        # Notify browser which campaign agent is on the call
        await self._send_agent_info(self._campaign_key)

        # Start bidirectional relay loops
        asyncio.create_task(self._receiver_loop())
        asyncio.create_task(self._sender_loop())
        asyncio.create_task(self._heartbeat_loop())
        # Start the silence watchdog — auto-ends the call if the customer goes quiet.
        self._last_user_activity_ts = time.monotonic()
        self._idle_monitor = asyncio.create_task(self._idle_monitor_loop())

    # ── 2. Browser → Voice Live (sender) ─────────────────────────────────

    async def handle_browser_audio(self, raw_pcm: bytes):
        """Queue raw PCM16 audio from the browser for Voice Live."""
        if self._call_ended:
            return
        audio_b64 = base64.b64encode(raw_pcm).decode("ascii")
        await self._send_queue.put(
            json.dumps({
                "type": "input_audio_buffer.append",
                "audio": audio_b64,
            })
        )

    async def _sender_loop(self):
        """Drain queue and forward to Voice Live WebSocket."""
        try:
            while True:
                msg = await self._send_queue.get()
                if self.vl_ws:
                    try:
                        await self.vl_ws.send(msg)
                    except Exception:
                        logger.warning("[%s] Voice Live connection lost", self._call_id)
                        break
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("[%s] Sender loop error", self._call_id)

    # ── 4. Voice Live → Browser (receiver) ───────────────────────────────

    async def _receiver_loop(self):
        """Handle all Voice Live events, including function calls for routing."""
        try:
            async for message in self.vl_ws:
                self._last_vl_event_ts = time.monotonic()  # heartbeat
                event = json.loads(message)
                event_type = event.get("type")

                match event_type:
                    # ── Lifecycle ─────────────────────────────────────────
                    case "session.created":
                        vl_session_id = event.get("session", {}).get("id", "unknown")
                        logger.info(
                            "[%s] Session created (vl_session_id=%s)",
                            self._call_id,
                            vl_session_id,
                        )

                    case "conversation.item.created":
                        item = event.get("item", {})
                        item_id = item.get("id", "")
                        # Skip interim response items — they're ephemeral and get removed
                        # once the real response arrives. Dedupe: Voice Live can emit
                        # this event more than once for the same item, which would
                        # otherwise cause duplicate delete attempts during compaction.
                        if (
                            item_id
                            and not item_id.startswith("interim_")
                            and item_id not in self._conversation_item_ids
                        ):
                            self._conversation_item_ids.append(item_id)

                    case "conversation.item.retrieved":
                        item = event.get("item", {})
                        item_id = item.get("id", "")
                        if item_id:
                            self._retrieved_items[item_id] = item
                        self._retrieve_pending -= 1

                    case "session.updated":
                        logger.info(
                            "[%s] Session updated (agent: %s)",
                            self._call_id,
                            self._agent_name,
                        )

                    case "input_audio_buffer.cleared":
                        pass

                    # ── User speech events ────────────────────────────────
                    case "input_audio_buffer.speech_started":
                        # The customer is speaking — record activity and clear any
                        # scheduled hang-up so we NEVER end the call mid-utterance.
                        self._user_speaking = True
                        self._last_user_activity_ts = time.monotonic()
                        if self._pending_end_call:
                            logger.info(
                                "[%s] Customer resumed speaking — cancelling scheduled call end",
                                self._call_id,
                            )
                            self._pending_end_call = False
                        await self._send_to_browser(
                            json.dumps({"Kind": "StopAudio"})
                        )

                    case "input_audio_buffer.speech_stopped":
                        self._user_speaking = False
                        self._last_user_activity_ts = time.monotonic()
                        self._user_speech_end_ts = time.monotonic()
                        self._first_audio_latency_logged = False
                        self._last_transcription_empty = True  # assume empty until transcription proves otherwise
                        # Start watchdog: if no response starts within 5s, force one
                        if self._response_watchdog and not self._response_watchdog.done():
                            self._response_watchdog.cancel()
                        self._response_watchdog = asyncio.create_task(
                            self._response_watchdog_timer()
                        )

                    # ── User transcription ────────────────────────────────
                    case "conversation.item.input_audio_transcription.completed":
                        self._tool_call_counts.clear()  # reset loop guard on new user input
                        transcript = event.get("transcript", "")
                        stt_lang = event.get("language", "")
                        # Track whether this was real speech or just noise
                        if transcript.strip():
                            self._last_transcription_empty = False
                            self._user_turn_count += 1
                            self._last_user_activity_ts = time.monotonic()
                            self._transcript_log.append(("user", transcript.strip()))
                        logger.info(
                            "[%s] USER [lang=%s]: %s",
                            self._call_id,
                            stt_lang,
                            transcript,
                        )
                        await self._send_to_browser(
                            json.dumps({
                                "Kind": "UserTranscription",
                                "Text": transcript,
                                "Language": stt_lang,
                            })
                        )

                    case "conversation.item.input_audio_transcription.failed":
                        logger.error(
                            "[%s] Transcription failed: %s",
                            self._call_id,
                            event.get("error"),
                        )

                    # ── Function call (agent routing) ─────────────────────
                    case "response.function_call_arguments.done":
                        await self._handle_function_call(event)

                    # ── Agent response audio ─────────────────────────────
                    case "response.audio.delta":
                        if (
                            self._user_speech_end_ts
                            and not self._first_audio_latency_logged
                        ):
                            latency_ms = int(
                                (time.monotonic() - self._user_speech_end_ts)
                                * 1000
                            )
                            logger.info(
                                "[%s] Time to first audio byte: [latency=%dms]",
                                self._call_id,
                                latency_ms,
                            )
                            self._first_audio_latency_logged = True

                        delta = event.get("delta", "")
                        audio_bytes = base64.b64decode(delta)
                        self._response_audio_bytes += len(audio_bytes)
                        await self._send_to_browser(audio_bytes)

                    # ── Agent transcript ──────────────────────────────────
                    case "response.audio_transcript.done":
                        transcript = event.get("transcript", "").strip()
                        if not transcript:
                            break
                        agent_name = self._agent_name
                        transcript = _redact_tool_names(transcript)
                        logger.info(
                            "[%s] AGENT (%s): %s",
                            self._call_id,
                            agent_name,
                            transcript,
                        )
                        self._transcript_log.append(("agent", transcript))

                        # Case B: truncation already fired before transcript arrived
                        if self._response_truncated:
                            transcript = self._truncate_text(
                                transcript, self._truncation_audio_end_ms
                            )
                            self._response_truncated = False

                        self._last_agent_transcript = (transcript, agent_name)
                        await self._send_to_browser(
                            json.dumps({
                                "Kind": "AgentTranscription",
                                "Text": transcript,
                                "Agent": agent_name,
                            })
                        )

                    # ── Truncation (interruption) ────────────────────────
                    case "conversation.item.truncated":
                        audio_end_ms = event.get("audio_end_ms", 0)
                        logger.info(
                            "[%s] Response truncated (user interrupted). item_id=%s audio_end_ms=%d",
                            self._call_id,
                            event.get("item_id"),
                            audio_end_ms,
                        )
                        if self._last_agent_transcript:
                            # Case A: transcript already sent — replace it on the UI
                            text, agent_name = self._last_agent_transcript
                            text = self._truncate_text(text, audio_end_ms)
                            await self._send_to_browser(
                                json.dumps({
                                    "Kind": "ReplaceLastAgent",
                                    "Text": text,
                                    "Agent": agent_name,
                                })
                            )
                            self._last_agent_transcript = None
                        else:
                            # Case B: transcript hasn't arrived yet — set flag for when it does
                            self._response_truncated = True
                            self._truncation_audio_end_ms = audio_end_ms

                    # ── Intermediate events (ignored) ────────────────────
                    case (
                        "response.created"
                        | "response.output_item.added"
                        | "response.audio_transcript.delta"
                        | "response.function_call_arguments.delta"
                    ):
                        if event_type == "response.created":
                            self._response_active = True
                            self._response_audio_bytes = 0
                            self._response_truncated = False
                            self._truncation_audio_end_ms = 0
                            # Cancel watchdog — response started normally
                            if self._response_watchdog and not self._response_watchdog.done():
                                self._response_watchdog.cancel()
                                self._response_watchdog = None

                    # ── Response complete ─────────────────────────────────
                    case "response.done":
                        self._response_active = False
                        resp = event.get("response", {})
                        status = resp.get("status")
                        # Reset the idle clock — the agent just finished, so start
                        # counting the customer's silence from now.
                        self._last_user_activity_ts = time.monotonic()
                        # ── Graceful termination: agent finished its farewell ──
                        if self._pending_end_call:
                            if status == "completed":
                                self._pending_end_call = False
                                logger.info(
                                    "[%s] Farewell delivered — terminating call",
                                    self._call_id,
                                )
                                asyncio.create_task(
                                    self._terminate_call(
                                        self._end_call_reason or "Call ended."
                                    )
                                )
                                continue
                            # Response was cancelled/failed (e.g. customer barged in) —
                            # drop the pending hang-up and carry on normally.
                            self._pending_end_call = False
                        if status == "cancelled":
                            details = resp.get("status_details", {})
                            logger.info(
                                "[%s] Response cancelled (reason=%s)",
                                self._call_id,
                                details.get("reason", "unknown"),
                            )
                            # Clear deferred state so hold music doesn't play
                            # when the agent never got to say "please hold".
                            if self._pending_hold_music is not None:
                                logger.info("[%s] Clearing pending hold music (response was cancelled)", self._call_id)
                                self._pending_hold_music = None
                            self._pending_response_create = False
                        elif status != "completed":
                            logger.error(
                                "[%s] Response error: %s",
                                self._call_id,
                                json.dumps(resp.get("status_details", {})),
                            )

                        # Play deferred hold music AFTER agent finishes speaking
                        if self._pending_hold_music is not None:
                            duration = self._pending_hold_music
                            self._pending_hold_music = None
                            logger.info("[%s] 🎵 Playing hold music (%ds)", self._call_id, duration)
                            await self._send_to_browser(
                                json.dumps({"Kind": "PlayHoldMusic", "Duration": duration})
                            )
                            await asyncio.sleep(duration)
                            logger.info("[%s] 🎵 Hold music finished, injecting thank-for-waiting instruction", self._call_id)
                            # Inject a system hint so the model thanks the customer for waiting
                            await self._send_json({
                                "type": "conversation.item.create",
                                "item": {
                                    "type": "message",
                                    "role": "user",
                                    "content": [{
                                        "type": "input_text",
                                        "text": "[System: Hold music has ended. Reply in the SAME language the customer has been speaking (do NOT switch to English). Begin by thanking the customer for waiting, then deliver your answer.]",
                                    }],
                                },
                            })
                            # Use safe create — VAD may have already started a response
                            await self._safe_response_create()
                        # Fire deferred response.create (from handoff or tool calls)
                        elif self._pending_response_create:
                            self._pending_response_create = False
                            logger.info("[%s] Firing deferred response.create", self._call_id)
                            await self._send_json({"type": "response.create"})

                        # ── Trigger background summarization (non-blocking) ──
                        if (
                            status == "completed"
                            and SUMMARY_EVERY_N_TURNS > 0
                            and not self._compaction_running
                            and (self._user_turn_count - self._last_summarized_at_turn) >= SUMMARY_EVERY_N_TURNS
                        ):
                            asyncio.create_task(self._compact_conversation())

                    # ── Errors ────────────────────────────────────────────
                    case "error":
                        err = event.get("error", {})
                        code = err.get("code", "")
                        if code in (
                            "item_retrieve_invalid_item_id",
                            "item_delete_invalid_item_id",
                        ):
                            # Item was already removed server-side (barge-in truncation
                            # or a cancelled response). Harmless — our local tracking is
                            # rebuilt on compaction/close. Decrement the retrieval counter
                            # so close() doesn't hang waiting on it.
                            if code == "item_retrieve_invalid_item_id":
                                self._retrieve_pending -= 1
                            logger.debug(
                                "[%s] Stale item id ignored (%s) — item already removed",
                                self._call_id,
                                code,
                            )
                        else:
                            logger.error(
                                "[%s] Voice Live error: %s",
                                self._call_id,
                                json.dumps(event),
                            )

                    case _:
                        logger.debug(
                            "[%s] Unhandled event: %s",
                            self._call_id,
                            event_type,
                        )

        except asyncio.CancelledError:
            pass
        except (ConnectionClosed, ConnectionResetError, OSError) as e:
            # Expected when the call ends and the Voice Live socket is torn down.
            logger.info("[%s] Voice Live connection closed: %s", self._call_id, e)
        except Exception:
            logger.exception("[%s] Receiver loop error", self._call_id)

    # ── Function call handler ────────────────────────────────────────────

    async def _handle_function_call(self, event: dict):
        """
        Process function calls from the LLM: CRM data lookups and hold music.
        (Outbound calls have no triage/handoff — the campaign agent is fixed.)
        """
        call_id = event.get("call_id", "")
        fn_name = event.get("name", "")
        args_str = event.get("arguments", "{}")

        # ── Loop guard: cap repeated calls to the same tool ──────────
        MAX_TOOL_CALLS_PER_CYCLE = 3
        self._tool_call_counts[fn_name] = self._tool_call_counts.get(fn_name, 0) + 1
        if self._tool_call_counts[fn_name] > MAX_TOOL_CALLS_PER_CYCLE:
            blocked_count = self._tool_call_counts[fn_name] - MAX_TOOL_CALLS_PER_CYCLE
            logger.warning(
                "[%s] Tool loop detected: %s called %d times (blocked #%d) — returning error",
                self._call_id, fn_name, self._tool_call_counts[fn_name], blocked_count,
            )
            await self._send_json({
                "type": "conversation.item.create",
                "item": {
                    "type": "function_call_output",
                    "call_id": call_id,
                    "output": json.dumps({
                        "error": f"STOP. Tool '{fn_name}' already called {MAX_TOOL_CALLS_PER_CYCLE} times. "
                                 "Do NOT call this tool again. Respond to the customer using the data you already have.",
                    }),
                },
            })
            if blocked_count <= 1:
                # Give the model ONE more chance (first blocked call).
                await self._safe_response_create()
            else:
                # Force a speech-only response — inject a strong instruction
                # and prevent any further tool calls so the model MUST speak.
                logger.warning(
                    "[%s] Forcing speech-only response to break tool loop for %s",
                    self._call_id, fn_name,
                )
                await self._send_json({
                    "type": "conversation.item.create",
                    "item": {
                        "type": "message",
                        "role": "user",
                        "content": [{
                            "type": "input_text",
                            "text": (
                                "[System: CRITICAL — You are stuck in a tool loop. "
                                "Do NOT call any tool. Respond to the customer RIGHT NOW "
                                "using the information you already have. Summarize what you know "
                                "and ask the customer how to proceed.]"
                            ),
                        }],
                    },
                })
                await self._safe_response_create()
            return

        logger.info(
            "[%s] Function call: %s(%s)",
            self._call_id,
            fn_name,
            args_str,
        )

        # Send workflow step to browser UI
        label = TOOL_DISPLAY_LABELS.get(fn_name)
        if label:
            await self._send_to_browser(
                json.dumps({"Kind": "ToolStatus", "Tool": fn_name, "Label": label})
            )

        if fn_name == "play_hold_music":
            # ── Hold music — DEFERRED until current response finishes ────
            # The model says "please hold" as audio in the same response.
            # We defer music playback until response.done so the spoken
            # hold message plays BEFORE the music starts.
            try:
                args = json.loads(args_str) if args_str.strip() else {}
            except json.JSONDecodeError:
                args = {}
            duration = args.get("duration", 5)
            logger.info("[%s] 🎵 Hold music queued (%ds, will play after response finishes)", self._call_id, duration)

            # Store for later — will fire in response.done handler
            self._pending_hold_music = duration

            # Return tool output immediately so the model can finish speaking
            await self._send_json({
                "type": "conversation.item.create",
                "item": {
                    "type": "function_call_output",
                    "call_id": call_id,
                    "output": json.dumps({
                        "status": "hold_music_queued",
                        "message": "Hold music will play after you finish speaking. Finish your hold announcement now. After the music ends, your next response MUST start with thanking the customer for waiting before delivering your answer.",
                    }),
                },
            })
            # Do NOT trigger response.create here — let the model finish
            # its current response (saying "please hold"), then music
            # plays on response.done, then we trigger a new response.

        elif fn_name == "end_call":
            # ── Graceful hang-up — DEFERRED until the farewell finishes ──
            # The model says a short goodbye as audio in the SAME response.
            # We defer the actual teardown to response.done so the farewell
            # plays fully before the line drops. If the customer barges in
            # before then, speech_started clears _pending_end_call (no
            # false-positive hang-up).
            try:
                args = json.loads(args_str) if args_str.strip() else {}
            except json.JSONDecodeError:
                args = {}
            self._end_call_reason = args.get("reason") or "Call ended."
            self._pending_end_call = True
            logger.info(
                "[%s] 📞 end_call requested (reason=%s) — hanging up after farewell",
                self._call_id, self._end_call_reason,
            )
            # Ack the tool so the model finishes its farewell in this response.
            await self._send_json({
                "type": "conversation.item.create",
                "item": {
                    "type": "function_call_output",
                    "call_id": call_id,
                    "output": json.dumps({
                        "status": "ending_call",
                        "message": "Acknowledged. Finish your short spoken farewell NOW in the "
                                   "customer's language. The call ends automatically once you "
                                   "finish. Do NOT ask any further questions.",
                    }),
                },
            })
            # Do NOT trigger response.create — the current farewell response
            # completes on its own, and termination fires on response.done.

        elif fn_name == "escalate_to_human":
            # ── Hand the call off to a human agent (email) then hang up ──
            # Sends a summary + action items + transcript to the human agent,
            # shows an escalation banner in the browser, then ends the call
            # via the same deferred-farewell path as end_call.
            try:
                args = json.loads(args_str) if args_str.strip() else {}
            except json.JSONDecodeError:
                args = {}
            from crm_tools import send_escalation

            esc_type = args.get("escalation_type", "unresolved")
            reason = args.get("reason", "")
            try:
                result = send_escalation(
                    customer_id=self._customer_id,
                    campaign=get_campaign(self._campaign_key).get("title", self._campaign_key),
                    agent=self._agent_name,
                    reason=reason,
                    summary=args.get("summary", ""),
                    action_items=args.get("action_items"),
                    priority=args.get("priority", "medium"),
                    escalation_type=esc_type,
                    transcript=list(self._transcript_log),
                )
            except Exception as exc:
                logger.exception("[%s] Escalation failed", self._call_id)
                result = {"error": str(exc), "reference": "", "priority": "medium"}

            ref = result.get("reference", "")
            logger.info(
                "[%s] 🚨 Escalation (%s) → %s | ticket=%s | delivery=%s",
                self._call_id, esc_type, result.get("email_to", ""),
                ref, result.get("delivery", ""),
            )

            # Escalation banner for the browser UI
            await self._send_to_browser(json.dumps({
                "Kind": "Escalation",
                "Ticket": ref,
                "Reason": reason,
                "Priority": result.get("priority", "medium"),
                "Type": esc_type,
                "EmailTo": result.get("email_to", ""),
            }))

            # Schedule the graceful hang-up (fires after the spoken confirmation)
            self._pending_end_call = True
            self._end_call_reason = (
                f"Escalated to a human agent — ticket {ref}." if ref
                else "Escalated to a human agent."
            )

            if esc_type == "resolved_handoff":
                spoken = (
                    "Warmly thank the customer and tell them you've passed a summary and the "
                    "next steps to the team who will follow up. "
                )
            else:
                spoken = (
                    "Tell the customer: 'I've noted your request and I'm escalating this to a "
                    "human agent who will call you back shortly.' "
                )
            await self._send_json({
                "type": "conversation.item.create",
                "item": {
                    "type": "function_call_output",
                    "call_id": call_id,
                    "output": json.dumps({
                        "status": "escalated",
                        "reference": ref,
                        "message": spoken + "Say it in the customer's language, then STOP. The "
                                   "call ends automatically once you finish. Do NOT promise any "
                                   "specific outcome or ask further questions.",
                    }),
                },
            })
            # Do NOT trigger response.create — the current response finishes
            # with the spoken confirmation; termination fires on response.done.

        elif fn_name in TOOL_FUNCTIONS:
            # ── CRM data tool call ───────────────────────────────────────
            try:
                args = json.loads(args_str) if args_str.strip() else {}
            except json.JSONDecodeError:
                args = {}

            tool_fn = TOOL_FUNCTIONS[fn_name]

            # Determine which params the function needs
            import inspect
            sig = inspect.signature(tool_fn)
            call_kwargs = {}
            for param_name in sig.parameters:
                if param_name == "customer_id":
                    call_kwargs["customer_id"] = self._customer_id
                elif param_name in args:
                    call_kwargs[param_name] = args[param_name]

            try:
                result = tool_fn(**call_kwargs)
            except Exception as exc:
                logger.exception("[%s] CRM tool error: %s", self._call_id, fn_name)
                result = {"error": str(exc)}

            logger.info(
                "[%s] Tool %s → %d chars",
                self._call_id, fn_name, len(json.dumps(result)),
            )

            # Return the result to the model
            await self._send_json({
                "type": "conversation.item.create",
                "item": {
                    "type": "function_call_output",
                    "call_id": call_id,
                    "output": json.dumps(result),
                },
            })

            # Trigger the model to continue with the data
            await self._safe_response_create()

        else:
            logger.warning(
                "[%s] Unknown function call: %s",
                self._call_id,
                fn_name,
            )

    # ── Helpers ───────────────────────────────────────────────────────────

    async def _safe_response_create(self):
        """Send response.create, or defer if a response is already active."""
        if self._response_active:
            logger.info("[%s] Deferring response.create (response still active)", self._call_id)
            self._pending_response_create = True
        else:
            await self._send_json({"type": "response.create"})

    async def _response_watchdog_timer(self):
        """Safety net: if no response starts within 5s of speech ending, force one."""
        try:
            await asyncio.sleep(5)
            if not self._response_active:
                # Don't force a response for empty transcriptions (noise/breathing)
                if self._last_transcription_empty:
                    logger.debug(
                        "[%s] Watchdog: skipping — last transcription was empty (noise/breathing)",
                        self._call_id,
                    )
                    return
                logger.warning(
                    "[%s] ⚠️ Watchdog: no response 5s after speech ended — forcing response.create",
                    self._call_id,
                )
                await self._send_json({"type": "response.create"})
        except asyncio.CancelledError:
            pass  # Normal — response started before timeout

    async def _heartbeat_loop(self):
        """Detect dead Voice Live connections and notify the browser."""
        try:
            while self.vl_ws and not self.vl_ws.close_code:
                await asyncio.sleep(10)
                silence = time.monotonic() - self._last_vl_event_ts
                if silence > 30:
                    logger.error(
                        "[%s] 💔 No Voice Live events for %.0fs — connection appears dead",
                        self._call_id, silence,
                    )
                    await self._send_to_browser(
                        json.dumps({
                            "Kind": "AgentTranscription",
                            "Text": "Connection lost. Please refresh the page to reconnect.",
                            "Agent": "System",
                        })
                    )
                    break
        except asyncio.CancelledError:
            pass
        except Exception:
            pass

    async def _idle_monitor_loop(self):
        """
        End the call gracefully if the customer stays silent past the timeout.

        False-positive guards (a hang-up while the user is talking is very bad):
          • never fire while the AGENT is speaking (a response is active)
          • never fire while the CUSTOMER is speaking (between speech_started/stopped)
          • never fire if a hang-up is already scheduled
          • re-check the speaking flag right before triggering
        The idle clock is reset on every customer utterance and whenever the
        agent finishes a response, so it only counts genuine dead air.
        """
        try:
            while not self._call_ended:
                await asyncio.sleep(IDLE_CHECK_INTERVAL_SECONDS)
                if self._call_ended:
                    break
                if (
                    self._response_active
                    or self._user_speaking
                    or self._pending_end_call
                ):
                    continue
                idle = time.monotonic() - self._last_user_activity_ts
                if idle < IDLE_TIMEOUT_SECONDS:
                    continue
                # Final guard: make sure the customer isn't mid-utterance right now.
                if self._user_speaking or self._response_active:
                    continue
                logger.info(
                    "[%s] ⏳ Customer silent for %.0fs — ending call gracefully",
                    self._call_id, idle,
                )
                # Ask the agent to say a brief goodbye; termination fires on
                # response.done via the _pending_end_call path.
                self._pending_end_call = True
                self._end_call_reason = "No response from the customer."
                await self._send_json({
                    "type": "conversation.item.create",
                    "item": {
                        "type": "message",
                        "role": "user",
                        "content": [{
                            "type": "input_text",
                            "text": (
                                "[System: The customer has been silent for a while and may have "
                                "stepped away. In the customer's language, briefly and warmly say "
                                "you'll let them go for now and they're welcome to call back "
                                "anytime, then STOP. Do NOT ask another question.]"
                            ),
                        }],
                    },
                })
                await self._safe_response_create()
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("[%s] Idle monitor error", self._call_id)

    async def _terminate_call(self, reason: str):
        """
        Tear the call down: let the farewell audio drain, tell the browser to
        end, then close the Voice Live socket. Idempotent.
        """
        if self._call_ended:
            return
        self._call_ended = True
        # Estimate how long the farewell audio takes to finish playing in the
        # browser (PCM16 @ 24kHz → 48000 bytes/sec) and hold that long so we
        # don't cut off the goodbye.
        playback_s = self._response_audio_bytes / 48000 if self._response_audio_bytes else 0
        grace_s = min(max(playback_s + 0.7, 1.5), 15)
        grace_ms = int(grace_s * 1000)
        logger.info(
            "[%s] 📞 Ending call — reason: %s (grace %.1fs)",
            self._call_id, reason, grace_s,
        )
        await self._send_to_browser(
            json.dumps({"Kind": "EndCall", "Reason": reason, "GraceMs": grace_ms})
        )
        # Stop the idle watchdog if it's still running.
        if self._idle_monitor and not self._idle_monitor.done():
            self._idle_monitor.cancel()
        await asyncio.sleep(grace_s)
        if self.vl_ws:
            try:
                await self.vl_ws.close()
            except Exception:
                pass

    async def _send_json(self, obj: dict):
        if self.vl_ws:
            await self.vl_ws.send(json.dumps(obj))

    async def _send_to_browser(self, data):
        try:
            await self.browser_ws.send(data)
        except Exception:
            logger.exception("[%s] Failed to send to browser", self._call_id)

    async def _send_agent_info(self, campaign_key: str):
        """Notify browser which campaign agent is on the call."""
        campaign = get_campaign(campaign_key)
        await self._send_to_browser(
            json.dumps({
                "Kind": "AgentSwitch",
                "Agent": campaign["name"],
                "AgentKey": campaign_key,
            })
        )

    def _truncate_text(self, text: str, audio_end_ms: int) -> str:
        """Truncate transcript to what the user heard, based on audio timing."""
        sent_s = self._response_audio_bytes / 48000  # PCM16 @ 24kHz
        heard_s = audio_end_ms / 1000
        # Only truncate if we have a reliable audio_end_ms (> 0)
        # API sometimes returns 0 even when user heard audio
        if audio_end_ms > 0 and sent_s > 0 and heard_s < sent_s:
            heard_ratio = heard_s / sent_s
            chars_heard = int(len(text) * heard_ratio)
            if chars_heard < len(text):
                cut = text[:chars_heard].rfind(" ")
                if cut > 0:
                    text = text[:cut]
        return text + " [interrupted]"

    async def _compact_conversation(self):
        """
        Background task: summarize older conversation items and delete them
        to reduce token consumption. Runs only when no response is active and
        user is not speaking, so the user is never interrupted.
        """
        if self._compaction_running:
            return
        self._compaction_running = True
        try:
            # Wait until no response is in-flight (user finished hearing the agent)
            for _ in range(50):  # up to 5s
                if not self._response_active:
                    break
                await asyncio.sleep(0.1)

            total_ids = len(self._conversation_item_ids)
            keep = max(SUMMARY_KEEP_RECENT, 4)
            if total_ids <= keep:
                return

            old_ids = set(self._conversation_item_ids[:-keep])

            # Build summary from local transcript log (no network round-trip)
            lines: list[str] = []
            remaining: list[tuple[str, str]] = []
            # We don't have a 1:1 mapping of transcript entries to item IDs,
            # so summarize the oldest entries proportionally
            entries_to_summarize = max(0, len(self._transcript_log) - keep)
            for i, (role, text) in enumerate(self._transcript_log):
                if i < entries_to_summarize:
                    speaker = "Customer" if role == "user" else "Agent"
                    if len(text) > 200:
                        text = text[:200] + "..."
                    lines.append(f"- {speaker}: {text}")
                else:
                    remaining.append((role, text))

            if not lines:
                return

            summary = "\n".join(lines)

            # Inject summary as a single context item
            await self._send_json({
                "type": "conversation.item.create",
                "item": {
                    "type": "message",
                    "role": "user",
                    "content": [{
                        "type": "input_text",
                        "text": (
                            "[System — Conversation Summary for context. "
                            "Do NOT read this aloud. Use as memory of earlier turns.]\n"
                            f"{summary}"
                        ),
                    }],
                },
            })

            # Delete old items from Voice Live context. Skip any we've already deleted
            # (guards against duplicate ids and repeat compaction runs) so we don't
            # trigger item_delete_invalid_item_id errors.
            for item_id in self._conversation_item_ids[:-keep]:
                if item_id in self._deleted_item_ids:
                    continue
                self._deleted_item_ids.add(item_id)
                await self._send_json({"type": "conversation.item.delete", "item_id": item_id})

            # Update local tracking
            self._conversation_item_ids = self._conversation_item_ids[-keep:]
            self._transcript_log = remaining
            self._last_summarized_at_turn = self._user_turn_count
            logger.info(
                "[%s] ✂️ Compacted conversation at turn %d (summarized=%d entries, kept=%d)",
                self._call_id, self._user_turn_count, entries_to_summarize, len(remaining),
            )
        except Exception:
            logger.exception("[%s] Conversation compaction failed", self._call_id)
        finally:
            self._compaction_running = False

    async def _dump_conversation_history(self):
        """
        Retrieve all remaining conversation items at session close
        via conversation.item.retrieve and log the full history.
        This is the ONLY time we use retrieval — one-time at close, acceptable cost.
        """
        if not self.vl_ws or not self._conversation_item_ids:
            return

        logger.info(
            "[%s] Retrieving %d conversation items for audit log...",
            self._call_id,
            len(self._conversation_item_ids),
        )
        self._retrieved_items = {}
        self._retrieve_pending = len(self._conversation_item_ids)

        for item_id in self._conversation_item_ids:
            await self._send_json({
                "type": "conversation.item.retrieve",
                "item_id": item_id,
            })

        # Wait for all retrieve responses (up to 5s timeout)
        deadline = time.monotonic() + 5.0
        while self._retrieve_pending > 0 and time.monotonic() < deadline:
            await asyncio.sleep(0.1)

        if self._retrieve_pending > 0:
            logger.warning(
                "[%s] Timed out waiting for %d item retrievals",
                self._call_id,
                self._retrieve_pending,
            )

        logger.info("[%s] " + "=" * 50, self._call_id)
        logger.info("[%s] CONVERSATION HISTORY (as seen by LLM)", self._call_id)
        logger.info("[%s] " + "-" * 50, self._call_id)

        for item_id in self._conversation_item_ids:
            item = self._retrieved_items.get(item_id, {})
            if not item:
                continue
            role = item.get("role", item.get("type", "?"))
            contents = item.get("content", [])
            texts = []
            for part in contents:
                if part.get("text"):
                    texts.append(part["text"])
                elif part.get("transcript"):
                    texts.append(part["transcript"])
                elif part.get("type") == "input_audio":
                    texts.append("[audio]")
            text = " ".join(texts) if texts else "(no text content)"
            if len(text) > 300:
                text = text[:300] + "..."
            logger.info(
                "[%s]   %s: %s",
                self._call_id,
                role.upper(),
                text,
            )
        logger.info("[%s] " + "=" * 50, self._call_id)

    async def close(self):
        # ── Retrieve full conversation history from Voice Live ────
        await self._dump_conversation_history()
        
        if self.vl_ws:
            try:
                await self.vl_ws.close()
            except Exception:
                pass


# ──────────────────────────────────────────────────────────────────────────────
# Quart app
# ──────────────────────────────────────────────────────────────────────────────
app = Quart(__name__, static_folder="static")


@app.route("/")
async def index():
    """Campaign selection console — pick a campaign + customer, then start a call."""
    return await app.send_static_file("index.html")


@app.route("/call")
async def call_page():
    """Live outbound call UI."""
    return await app.send_static_file("call.html")


@app.route("/api/campaigns")
async def api_campaigns():
    """Return the available outbound campaigns for the selection UI."""
    from quart import jsonify
    campaigns = [
        {
            "id": key,
            "title": c["title"],
            "description": c["description"],
            "agent": c["name"],
            "icon": c["icon"],
            "color": c["color"],
            "audience": c.get("audience"),
        }
        for key, c in CAMPAIGN_REGISTRY.items()
    ]
    return jsonify(campaigns)


@app.route("/api/customers")
async def api_customers():
    """Return the demo customers (with per-product overdue flags) for the picker."""
    from quart import jsonify
    from crm_tools import _query
    rows = _query(
        "SELECT c.id, c.name, c.segment, c.city, c.phone, "
        "(SELECT COUNT(*) FROM collections cl WHERE cl.customer_id = c.id) AS card_overdue, "
        "(SELECT COUNT(*) FROM loan_collections lc WHERE lc.customer_id = c.id) AS loan_overdue "
        "FROM customers c ORDER BY c.name"
    )
    for r in rows:
        r["card_overdue"] = bool(r.get("card_overdue"))
        r["loan_overdue"] = bool(r.get("loan_overdue"))
        r["overdue"] = r["card_overdue"] or r["loan_overdue"]
    return jsonify(rows)


@app.websocket("/web/ws")
async def web_ws():
    """
    Browser WebSocket endpoint.

    Protocol:
        Browser → Server:  first text msg = JSON {"campaignId": "...", "customerId": "..."}
                           then raw PCM16 bytes (ArrayBuffer)
        Server → Browser:  raw PCM16 bytes (TTS audio)
                           OR JSON: {"Kind": "StopAudio"}
                           OR JSON: {"Kind": "AgentTranscription", "Text": "...", "Agent": "..."}
                           OR JSON: {"Kind": "UserTranscription", "Text": "...", "Language": "..."}
                           OR JSON: {"Kind": "AgentSwitch", "Agent": "...", "AgentKey": "..."}
    """
    logger.info("Browser connected")

    # Start auth + WSS connection IN PARALLEL with waiting for the first message.
    auth_task = asyncio.create_task(get_auth_headers())

    # First text message from browser carries the campaign + customer selection
    customer_id = "rajesh"  # default fallback
    campaign_id = DEFAULT_CAMPAIGN
    try:
        first_msg = await websocket.receive()
        if isinstance(first_msg, str):
            data = json.loads(first_msg)
            customer_id = data.get("customerId", "rajesh")
            campaign_id = data.get("campaignId", DEFAULT_CAMPAIGN)
            logger.info("Call setup — campaign=%s customer=%s", campaign_id, customer_id)
    except Exception:
        pass

    # Pass the pre-started auth task to the session
    session = VoiceLiveSession(
        websocket, customer_id=customer_id, campaign_key=campaign_id
    )
    asyncio.create_task(session.start(auth_task=auth_task))

    try:
        while True:
            msg = await websocket.receive()
            await session.handle_browser_audio(msg)
    except asyncio.CancelledError:
        pass
    except Exception:
        logger.exception("Web WebSocket error")
    finally:
        await session.close()


# ──────────────────────────────────────────────────────────────────────────────
# Startup — pre-warm auth token so first connection is fast
# ──────────────────────────────────────────────────────────────────────────────
@app.before_serving
async def _prewarm_auth():
    """Fetch auth token at startup so the first browser connection is instant."""
    if not VOICE_LIVE_API_KEY:
        logger.info("Pre-warming auth token...")
        try:
            await get_auth_headers()
            logger.info("Auth token pre-warmed successfully")
        except Exception as e:
            logger.warning("Auth pre-warm failed (will retry on first connection): %s", e)


# ──────────────────────────────────────────────────────────────────────────────
# Entry point
# ──────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    app.run(
        debug=True,
        host=os.getenv("APP_HOST", "0.0.0.0"),
        port=int(os.getenv("APP_PORT", "8000")),
    )
