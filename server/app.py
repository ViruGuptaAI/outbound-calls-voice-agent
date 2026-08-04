from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import socket
import sys
import time
import unicodedata
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
from ambient_mixer import get_ambient_mixer, ambient_config_summary, SAMPLE_RATE as AMBIENT_SAMPLE_RATE  # noqa: E402
from savings_account_tools import (  # noqa: E402
    SAVINGS_CALL_HEARTBEAT_SECONDS,
    SAVINGS_TOOL_FUNCTIONS,
    finalize_call as finalize_savings_call,
    get_savings_runtime_context,
    mark_savings_opening_delivered,
    start_savings_call,
    submit_step_result as submit_savings_step_result,
    touch_savings_call,
)

ALL_TOOL_FUNCTIONS = {**TOOL_FUNCTIONS, **SAVINGS_TOOL_FUNCTIONS}

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

# ── Call tone / tonality (operator-selectable) ───────────────────────────────
# In collections, tonality escalates with the number of contact attempts / days
# past due. The operator picks the tone from the console; it shapes HOW firmly
# the agent speaks WITHOUT ever crossing into harassment or threats (this stays
# within RBI fair-practices / dignified-conduct rules — see TONE_COMPLIANCE_FLOOR).
DEFAULT_TONE = "cordial"
TONE_INSTRUCTIONS = {
    "cordial": (
        "\n\n# CALL TONE — CORDIAL & EMPATHETIC (early-stage / first contact)\n"
        "- Speak warmly and patiently, like a helpful relationship manager. Assume good "
        "faith — the customer may simply have forgotten or hit a temporary rough patch.\n"
        "- Lead with understanding: acknowledge their situation, ask open questions, and "
        "offer help (reminders, flexible options) before mentioning any consequence.\n"
        "- Keep pressure low. No urgency language; the goal is a comfortable, willing commitment.\n"
    ),
    "firm": (
        "\n\n# CALL TONE — FIRM & PROFESSIONAL (repeated contact / rising overdue)\n"
        "- Stay polite and respectful, but be noticeably more direct and businesslike. "
        "This is a follow-up — the customer has been contacted before.\n"
        "- Clearly state the outstanding position and that it needs to be resolved now. "
        "Reduce small talk; steer firmly toward a concrete payment commitment today.\n"
        "- Calmly mention standard consequences of continued non-payment (late fees, credit "
        "bureau reporting) as facts, not threats. Press for a specific date and amount.\n"
    ),
    "stern": (
        "\n\n# CALL TONE — STERN & URGENT (final notice / seriously overdue)\n"
        "- Adopt a serious, urgent, no-nonsense tone. This is a final-reminder call; the "
        "account is significantly past due and needs immediate resolution.\n"
        "- Be crisp and insistent. State plainly that this cannot be deferred further and "
        "that a payment or a firm commitment is required on THIS call.\n"
        "- Spell out the concrete, formal consequences of continued default (escalation to "
        "recovery, credit-score impact, applicable charges) as factual next steps.\n"
    ),
    "aggressive": (
        "\n\n# CALL TONE — ASSERTIVE & HIGH-PRESSURE (hardest lawful firmness)\n"
        "- This is the firmest, most insistent tier, for a repeatedly-defaulted, seriously "
        "overdue account. Speak with authority and gravity, like a senior recovery officer "
        "who will not be brushed off. Short, direct, forceful sentences.\n"
        "- Convey real urgency and seriousness: make it unmistakably clear the account is in "
        "a critical state and that a payment or a firm, dated commitment is required NOW, on "
        "this call. Do not soften or trail off.\n"
        "- Press hard and repeatedly for a concrete outcome — an exact amount and an exact "
        "date — and do not accept vague deferrals. Hold the line firmly if they stall.\n"
        "- State the concrete formal consequences of continued default plainly and factually "
        "(recovery escalation, credit-bureau reporting, penal charges, legal-recovery steps "
        "as per contract). Present them as real, imminent facts — NEVER as personal threats.\n"
        "- CRITICAL: firmness and pressure ONLY. This is assertiveness, not hostility. You "
        "must still obey the TONE COMPLIANCE FLOOR below at all times — no abuse, no "
        "intimidation, no threats of harm, and always leave a clear way to pay.\n"
    ),
}
# Hard compliance floor — applied on top of EVERY firmer tone, including 'stern'.
# The agent must never harass, threaten, abuse, or intimidate the customer.
# TONE_COMPLIANCE_FLOOR = (
#     "\n# TONE COMPLIANCE FLOOR (ALWAYS, even when stern or aggressive)\n"
#     "- NEVER shout, abuse, insult, threaten, or use intimidating or coercive language. "
#     "No threats of arrest, violence, seizing homes, public shaming, or contacting the "
#     "customer's employer/family/references. Stay within RBI fair-practices and dignified "
#     "conduct at all times.\n"
#     "- Firmness and pressure are about CLARITY, DIRECTNESS, and URGENCY — never hostility, "
#     "fear-mongering, or personal attacks. Remain professional and factual no matter how "
#     "overdue the account is, and no matter how the customer behaves.\n"
#     "- If the customer becomes abusive, stay calm and professional; do NOT retaliate. "
#     "De-escalate or offer to escalate to a human — never trade insults.\n"
#     "- Always give the customer a clear way forward (payment options, help, or escalation "
#     "to a human). Pressure without a path to pay is not allowed.\n"
# )

# Per-tone TTS delivery — makes the tonality AUDIBLE. Firmer tiers speak louder
# and lower-pitched so the escalation is heard, not just worded.
# Voice choice per tone:
#   • Diya (Dragon HD) for cordial/firm/stern — natural en-IN, auto-adapts tone
#     from the wording (its `style` field is best-effort/ignored).
#   • Kavya (MAI-Voice-2) for aggressive — a style-capable voice that HONOURS
#     explicit emotion styles ('angry', 'shouting', …) + `styledegree` (0.01–2),
#     so the hard tier actually sounds forceful. NOTE: Kavya is a hi-IN voice; it
#     is best suited to Hindi/Hinglish collections calls.
_VOICE_DIYA = "en-IN-Diya:DragonHDV2.3Neural"
_VOICE_KAVYA_MAI = "hi-IN-Kavya:MAI-Voice-2"
TONE_VOICE = {
    "cordial":    {"name": _VOICE_DIYA, "style": "empathetic", "pitch": "call center slightly fast", "rate": "adaptive", "volume": "-5%"},
    "firm":       {"name": _VOICE_DIYA, "style": "serious",    "pitch": "slightly fast", "rate": "adaptive", "volume": "+0%"},
    "stern":      {"name": _VOICE_DIYA, "style": "angry",    "pitch": "slightly fast",    "rate": "adaptive", "volume": "+10%"},
    "aggressive": {"name": _VOICE_KAVYA_MAI, "style": "shouting", "styledegree": "2", "pitch": "slightly fast", "rate": "1.05", "volume": "+10%"},
}

# ── Conversation languages (operator-selectable, multi-select up to 3) ────────
# The operator picks which language(s) the bot may converse in. This drives:
#   (1) the STT language-identification list (input_audio_transcription.language), and
#   (2) a dynamic LANGUAGE POLICY block injected into the system message.
# The TTS voice is NOT changed — the Diya (Dragon HD) voice already speaks all
# these Indian regional languages, so it stays as the tone-selected voice.
DEFAULT_LANGUAGES = ["english", "hindi"]
LANGUAGE_REGISTRY = {
    "english":   {"label": "English (India)", "locale": "en-IN"},
    "hindi":     {"label": "Hindi",           "locale": "hi-IN"},
    "marathi":   {"label": "Marathi",         "locale": "mr-IN"},
    "kannada":   {"label": "Kannada",         "locale": "kn-IN"},
    "telugu":    {"label": "Telugu",          "locale": "te-IN"},
    "tamil":     {"label": "Tamil",           "locale": "ta-IN"},
    "gujarati":  {"label": "Gujarati",        "locale": "gu-IN"},
    "odia":      {"label": "Odia (Oriya)",    "locale": "or-IN"},
    "bengali":   {"label": "Bengali",         "locale": "bn-IN"},
    "malayalam": {"label": "Malayalam",       "locale": "ml-IN"},
}
MAX_LANGUAGES = 3


def normalize_languages(languages) -> list[str]:
    """Validate + de-dupe the operator's language selection, capped at MAX_LANGUAGES."""
    if not languages:
        return list(DEFAULT_LANGUAGES)
    out: list[str] = []
    for lang in languages:
        key = str(lang).strip().lower()
        if key in LANGUAGE_REGISTRY and key not in out:
            out.append(key)
        if len(out) >= MAX_LANGUAGES:
            break
    return out or list(DEFAULT_LANGUAGES)


def _language_policy_block(lang_keys: list[str]) -> str:
    """Build the dynamic LANGUAGE POLICY instruction from the selected languages."""
    labels = [LANGUAGE_REGISTRY[k]["label"] for k in lang_keys]
    primary = labels[0]
    allowed = ", ".join(labels)
    multi = len(labels) > 1
    return (
        "\n\n# LANGUAGE POLICY (operator-selected — OVERRIDES any 'open in English' default)\n"
        f"- You may converse ONLY in these languages: {allowed}.\n"
        f"- BEGIN the call in {primary}: your opening greeting and first question MUST be in {primary}.\n"
        + (
            "- If the customer replies in ANOTHER of these allowed languages, MIRROR them and continue "
            "in that language. Keep your short fillers and your full answer in the SAME language.\n"
            if multi else
            "- Stay in this language for the whole call.\n"
        )
        + f"- If the customer uses a language NOT in this list, politely continue in {primary} "
        "(or the closest allowed language) — NEVER use a language outside this set.\n"
        "- Keep fixed product terms (premium, EMI, sum assured, pre-approved) in English even inside "
        "a regional-language sentence.\n"
        "- You are a WOMAN — always use FEMININE self-conjugations, in every language.\n"
    )



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
    "escalate_to_human", "submit_step_result", "validate_pin_code",
    "get_product_information", "check_application_status", "schedule_callback",
    "register_do_not_call", "create_escalation", "finalize_call",
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
    "submit_step_result": "Saving journey progress",
    "validate_pin_code": "Checking service availability",
    "get_product_information": "Refreshing approved product details",
    "check_application_status": "Checking application status",
    "schedule_callback": "Checking callback availability",
    "register_do_not_call": "Applying contact preference",
    "create_escalation": "Creating human follow-up",
    "finalize_call": "Saving final outcome",
}

# ── Cached credential (avoids spawning az.cmd on every connection) ────────────
_cached_credential = None
_cached_token = None
_token_expiry = 0  # epoch seconds


# ──────────────────────────────────────────────────────────────────────────────
# Voice Live session configuration builder
# ──────────────────────────────────────────────────────────────────────────────
_MANAGED_TURN_GUARD = """
MANDATORY TURN PROCEDURE:
1. Interpret the latest customer utterance only against
   state_when_latest_customer_utterance_arrived and choose only from
   allowed_step_results. Never submit a volunteered answer for a future state.
2. At most ONE state transition may be accepted per customer utterance. If
   current_state_was_entered_after_latest_utterance or
   transition_already_accepted_for_latest_customer_utterance is true, follow the
   current next_action naturally and wait for a fresh customer reply.
3. A question or objection is not a step result. Answer it directly without a tool
   unless the same utterance also unambiguously answers the current state.
4. When next_action directs a tool call or finalization, perform it before speaking.
   Never ask a future-state question until the current transition returns ACCEPTED.
""".strip()


def _build_managed_turn_instructions(context: dict) -> str:
    """Render the response-scoped instructions used for one managed model turn."""
    return (
        _MANAGED_TURN_GUARD
        + "\n\nAUTHORITATIVE RUNTIME CONTEXT FOR THIS TURN. "
        "Never read this JSON aloud. Handle only call_state and follow next_action.\n"
        + json.dumps(context, ensure_ascii=False)
    )


def _build_managed_savings_opening(first_name: str) -> str:
    return (
        f"नमस्कार {first_name} जी, मैं Contoso Bank की virtual assistant Asha बोल रही हूँ। "
        f"क्या मेरी बात {first_name} जी से हो रही है?"
    )


def _latest_customer_decimal_digits(transcript: list[tuple[str, str]]) -> str:
    for role, text in reversed(transcript):
        if role == "user":
            return "".join(
                str(unicodedata.decimal(char))
                for char in text
                if char.isdecimal()
            )
    return ""


def _build_managed_savings_config(
    campaign: dict,
    playbook_tool_names: frozenset[str] | None,
) -> dict:
    """Build the isolated Voice Live configuration for the managed savings flow."""
    instructions = campaign["prompt"]

    tools = [
        tool for tool in campaign["tools"]
        if playbook_tool_names is None or tool["name"] in playbook_tool_names
    ]
    voice = TONE_VOICE["cordial"]
    return {
        "type": "session.update",
        "session": {
            "instructions": instructions,
            "modalities": ["text", "audio"],
            "turn_detection": {
                "type": "azure_semantic_vad_multilingual",
                "threshold": 0.6,
                "prefix_padding_ms": 700,
                "silence_duration_ms": 400,
                "create_response": False,
                "interrupt_response": True,
                "speech_duration_ms": 200,
                "remove_filler_words": False,
                "auto_truncate": True,
                "appended_text_after_truncation": " -- [user interrupted | response incomplete]",
            },
            "input_audio_transcription": {
                "model": "mai-transcribe",
            },
            "input_audio_noise_reduction": {"type": "azure_deep_noise_suppression"},
            "input_audio_echo_cancellation": {
                "type": "server_echo_cancellation",
                "reference_source": "server",
                "channels": 1,
            },
            "voice": {
                "name": voice["name"],
                "type": "azure-standard",
                "temperature": 0.5,
                "style": voice["style"],
                "pitch": voice["pitch"],
                "rate": voice["rate"],
                "volume": voice["volume"],
            },
            "input_audio_sampling_rate": 24000,
            # "reasoning_effort": "none",
            "max_response_output_tokens": "320",
            "tools": tools,
            "tool_choice": "auto",
        },
    }


def build_session_config(
    campaign_key: str,
    customer_name: str = "",
    tone: str = DEFAULT_TONE,
    languages: list[str] | None = None,
) -> dict:
    """
    Build the session.update payload for an outbound campaign agent.
    Assembles: customer context header + lean base prompt + playbook workflow +
    operator-selected tonality, and filters tools to only what the playbook needs.
    """
    campaign = get_campaign(campaign_key)
    base_prompt = campaign["prompt"]
    # Brand the agent speaks under — defaults to the bank, overridden per campaign
    # (e.g. the life-insurance campaigns speak as "Contoso Life").
    company = campaign.get("company", "Contoso Bank")
    # Operator-selected conversation languages (validated; primary = first).
    lang_keys = normalize_languages(languages)
    # MAI Transcribe takes a SINGLE ISO-639-1 code (e.g. "hi"), or none = auto
    # multilingual; it can't take a multi-locale list, so pin only when exactly one.
    stt_config: dict[str, Any] = {"model": "mai-transcribe"}
    if len(lang_keys) == 1:
        stt_config["language"] = LANGUAGE_REGISTRY[lang_keys[0]]["locale"].split("-")[0]

    # Look up the campaign's playbook + its required tool names
    playbook_text, playbook_tool_names = get_playbook(campaign_key)

    if campaign.get("managed_flow") == "savings_account":
        return _build_managed_savings_config(
            campaign,
            playbook_tool_names,
        )

    # Assemble instructions: base prompt + playbook
    instructions = base_prompt
    if playbook_text:
        instructions += "\n" + playbook_text

    # Universal guardrail — appended to every agent's instructions
    instructions += (
        "\n\n# CRITICAL: NEVER EXPOSE TOOL OR FUNCTION NAMES\n"
        "- NEVER say tool, function, or API names to the customer — anything with underscores or a "
        "code-like shape (e.g. names starting with get_, calculate_, assess_, record_, send_).\n"
        "- Instead of naming a tool, say something natural: 'Let me pull that up for you' or "
        "'Let me quickly check what I can offer you.'\n"
        "- A real officer would NEVER say function names. Speak naturally.\n"
    )

    # Universal delivery style — makes the agent sound like a human, not a bot.
    # Domain-neutral: each campaign's own prompt carries its product specifics.
    instructions += (
        "\n\n# DELIVERY — SOUND LIKE A HUMAN, NOT A BOT\n"
        "- ASK BEFORE YOU EXPLAIN — when the customer asks 'how does it work' / 'tell me more', "
        "or before you can quantify anything, do NOT dump a paragraph of specs in one breath. "
        "FIRST collect what you need by asking ONE short question at a time; gather their inputs, "
        "THEN give ONE tailored line.\n"
        "- WHEN GATHERING INFO, keep the turn to ONE short question (max 1–2 sentences). Do NOT "
        "precede the question with a paragraph of explanation. Question first, details later.\n"
        "- DRIP information — share ONE number or ONE benefit per turn, then pause and check "
        "reaction. Do NOT stack multiple figures or quotes into a single reply. Never quote more "
        "than one price/figure in a turn.\n"
        "- ROUND spoken money — say 'around ₹18,500' or 'about eighteen and a half thousand', "
        "never exact paise like '₹18,661'. Precision to the rupee sounds computed.\n"
        "- Be a CONVERSATIONAL CHAMELEON — match the customer's pace and energy. If they sound "
        "rushed, keep it short. If they hesitate, slow down and simplify. Use light back-channels "
        "('right', 'got it', 'makes sense') so they feel heard.\n"
        "- VARY your close and only push once interest shows. Do NOT end every turn with the same "
        "'Shall I proceed?' Read their temperature first, then make a specific, low-friction "
        "next-step offer.\n"
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
        "- NEVER switch languages on your own — only change language if the CUSTOMER changes first. "
        "If the customer has spoken only English, you MUST stay in English for the ENTIRE call, "
        "including every hold announcement, every 'let me check' filler, and your reply after the "
        "hold music. A hold or a tool/data lookup is NEVER a reason to switch languages.\n"
        "- Do NOT splice full English clauses into a Hindi sentence (e.g. 'मैं समझ रही हूँ, and I "
        "want to be transparent'). Say it in Hindi. Only keep short fixed product/industry terms "
        "(e.g. premium, EMI, interest rate, pre-approved) in English.\n"
        "- After a tool result, hold music, or any system prompt, STILL reply in the customer's "
        "current language — a tool/data lookup is never a reason to switch to English.\n"
        "- Hinglish is fine when the customer mixes — keep short product terms (e.g. premium, EMI, "
        "interest rate, pre-approved) in English but frame the sentence in Hindi if that is how they speak.\n"
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
        "- When you call `escalate_to_human` for an UNRESOLVED case, in the SAME turn tell the "
        "customer you've noted their issue and escalated it to a human senior officer, and that "
        "they will receive a CALL BACK shortly — it is a callback, NOT a live transfer, so NEVER "
        "say 'hold on while I connect you' or imply someone is joining the line right now. Then "
        "ask whether they need anything else, and if not, whether you may close the call; only "
        "call `end_call` after they agree. Do NOT promise any specific outcome the human has not "
        "approved.\n"
        "- ALSO use `escalate_to_human` with escalation_type='resolved_handoff' at the END of a "
        "SUCCESSFULLY resolved call when a human must action the next steps (e.g. process a "
        "disbursal, verify documents, honour a recorded promise-to-pay). A resolved_handoff does "
        "NOT end the call by itself: thank the customer warmly, tell them you've passed the "
        "details to the team for follow-up, and THEN ask whether there's anything else you can "
        "help with or whether you may end the call. Do NOT hang up until they confirm — once they "
        "do, follow the ENDING THE CALL flow (short warm farewell, then `end_call`).\n"
        "- Provide a clear `summary` and concrete `action_items` (next steps) whenever you "
        "escalate — this is what the human agent receives.\n"
        "- FIELD DISCIPLINE: `reason` is a short human-readable sentence explaining WHY you "
        "are handing off (e.g. 'Customer accepted the offer; application needs a human to "
        "complete disbursal.'). It is NOT a category — NEVER put 'resolved_handoff' or "
        "'unresolved' in `reason`; those values belong ONLY in `escalation_type`.\n"
    )

    # Operator-selected tonality — shapes HOW firmly the agent speaks. In
    # collections the tone escalates with contact attempts / days past due; the
    # compliance floor keeps even the stern tone within fair-practices limits.
    tone_key = tone if tone in TONE_INSTRUCTIONS else DEFAULT_TONE
    instructions += TONE_INSTRUCTIONS[tone_key]
    # if tone_key != "cordial":
    #     instructions += TONE_COMPLIANCE_FLOOR

    # Operator-selected conversation languages — overrides any 'open in English'
    # default and constrains the bot to the chosen language(s).
    instructions += _language_policy_block(lang_keys)

    # Build the per-tone TTS voice config. The aggressive tier switches to a
    # style-capable voice (Kavya / MAI-Voice-2) that honours the explicit
    # `style` + `styledegree`; the other tones stay on Diya.
    _tv = TONE_VOICE[tone_key]
    voice_cfg = {
        "name": _tv["name"],
        "type": "azure-standard",
        "temperature": 0.6,
        "style": _tv["style"],
        "pitch": _tv["pitch"],
        "rate": _tv["rate"],
        "volume": _tv["volume"],
    }
    if _tv.get("styledegree"):
        voice_cfg["styledegree"] = _tv["styledegree"]

    # NOTE: TTS voice is intentionally NOT changed per language — the Diya
    # (Dragon HD) voice already speaks all the supported Indian regional
    # languages, so it renders whatever language the model produces.

    # Inject customer name as context (the opening line is handled by response.create)
    if customer_name:
        first_name = customer_name.split()[0]
        instructions = (
            f"CUSTOMER YOU ARE CALLING: {customer_name}\n"
            f"Address the customer as {first_name}. This is an OUTBOUND call that YOU placed. "
            f"Your VERY FIRST turn is the opening: greet {first_name}, introduce yourself by name "
            f"AND state you are calling from {company} (say exactly '{company}', never any other "
            f"company name), give the reason, and ask permission. "
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
                    f"You are a warm phone agent for {company}. Produce ONE very short, "
                    "natural filler to cover a brief wait while data loads — e.g. "
                    "'Let me quickly pull that up…', 'One moment, checking that for you…', "
                    "'Give me a second…'. Match the customer's language (English or Hindi). "
                    "Do NOT quote any numbers, rates, or account details. Do NOT add a filler "
                    "to every turn — only when there is a real wait. Never say tool or function names."
                ),
            },
            # ── Input audio transcription ────────────────────────────────
            "input_audio_transcription": stt_config,
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
            "voice": voice_cfg,
            "input_audio_sampling_rate": 24000,
            # ── Model behaviour ──────────────────────────────────────────
            # "temperature": 0.1,
            # "reasoning_effort":"none",
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
        tone: str = DEFAULT_TONE,
        languages: list[str] | None = None,
    ):
        self.browser_ws = browser_ws
        self.vl_ws: Any = None
        self._send_queue: asyncio.Queue = asyncio.Queue()
        self._user_speech_end_ts = None
        self._first_audio_latency_logged = False
        self._campaign_key = campaign_key if campaign_key in CAMPAIGN_REGISTRY else DEFAULT_CAMPAIGN
        self._managed_flow = CAMPAIGN_REGISTRY[self._campaign_key].get("managed_flow")
        self._tone = DEFAULT_TONE if self._managed_flow else (
            tone if tone in TONE_INSTRUCTIONS else DEFAULT_TONE
        )
        self._languages = ["hindi", "english"] if self._managed_flow else normalize_languages(languages)
        self._agent_name = CAMPAIGN_REGISTRY[self._campaign_key]["name"]
        self._call_id = str(uuid.uuid4())[:8]
        self._customer_id = customer_id
        self._customer_name = ""
        self._response_active = False  # True while a response is being generated
        self._pending_response_create = False  # deferred response.create
        self._pending_hold_music: int | None = None  # deferred hold music duration
        self._hold_announced: bool = False  # True once a spoken hold announcement has been forced/confirmed
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
        # ── Follow-up email state ─────────────────────────────────────────────
        self._handoff_email_sent: bool = False  # True once any escalation/handoff email is sent
        self._recorded_ptp: dict | None = None  # last promise-to-pay details (for the handoff email)
        # ── Call termination state ────────────────────────────────────────────
        self._call_ended: bool = False  # True once we've begun tearing the call down
        self._pending_end_call: bool = False  # agent asked to hang up; fire on response.done
        # True when the hang-up was requested explicitly (end_call / escalate_to_human)
        # rather than by the idle watchdog. An explicit hang-up is committed: a customer
        # barge-in during the farewell must NOT keep the call alive (they already agreed
        # to end), whereas an idle goodbye IS cancellable (the customer came back).
        self._end_call_explicit: bool = False
        self._end_call_reason: str = ""  # reason string for the hang-up
        self._managed_opening_pending: bool = False
        self._managed_close_waiting: bool = False
        self._managed_close_armed: bool = False
        self._managed_terminal: bool = False
        self._managed_session_started: bool = False
        self._managed_lease_heartbeat: asyncio.Task | None = None
        self._managed_customer_close: str = ""
        self._managed_last_transition_user_turn: int | None = None
        self._managed_expected_state: str | None = None
        self._managed_allowed_step_results: frozenset[str] = frozenset()
        self._managed_state_user_turn: int | None = None
        self._managed_state_at_user_turn: str | None = None
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

        runtime_context = None
        if self._managed_flow == "savings_account":
            runtime_context = await asyncio.to_thread(
                start_savings_call,
                self._call_id,
                self._customer_id,
            )
            if runtime_context.get("error"):
                message = runtime_context.get("message", "This customer is not eligible for this campaign.")
                logger.warning("[%s] Managed savings call rejected: %s", self._call_id, message)
                await self._send_to_browser(
                    json.dumps({"Kind": "AgentTranscription", "Text": message, "Agent": "System"})
                )
                await self._terminate_call(message)
                return
            self._managed_session_started = True
            self._managed_lease_heartbeat = asyncio.create_task(
                self._managed_lease_heartbeat_loop()
            )

        # Configure the session with the selected campaign agent + playbook
        await self._send_json(
            build_session_config(
                self._campaign_key,
                customer_name=self._customer_name,
                tone=self._tone,
                languages=self._languages,
            )
        )

        # Trigger the OUTBOUND opening: the agent introduces itself and states
        # the purpose of the call, then asks permission to proceed.
        campaign = CAMPAIGN_REGISTRY[self._campaign_key]
        first_name = self._customer_name.split()[0] if self._customer_name else "there"
        if self._managed_flow == "savings_account":
            opening = _build_managed_savings_opening(first_name)
            self._managed_opening_pending = True
            await self._send_json({
                "type": "response.create",
                "response": {
                    "modalities": ["audio", "text"],
                    "tool_choice": "none",
                    "instructions": (
                        "Speak exactly the following Hindi opening with no preface, addition, "
                        f"translation, or tool call:\n{opening}"
                    ),
                },
            })
        else:
            company = campaign.get("company", "Contoso Bank")
            primary_label = LANGUAGE_REGISTRY[self._languages[0]]["label"]
            is_english = self._languages[0] == "english"
            # Front-load a hard language mandate so the greeting is deterministic, not probabilistic.
            lang_mandate = (
                f"LANGUAGE — MANDATORY: Speak your ENTIRE opening (greeting AND question) in {primary_label}. "
                + ("" if is_english else f"Every word must be in {primary_label}; do NOT use English at all. ")
            )
            greet_line = (
                f"Introduce yourself first: warmly greet {first_name} and say you are "
                f"{campaign['agent_name']} from {company} — phrased naturally in {primary_label}, "
                f"not translated word-for-word. Do not skip your name or the company. "
            )
            opening_instructions = (
                lang_mandate
                + f"This is the very start of an OUTBOUND phone call that YOU placed to {first_name}. "
                + greet_line
                + f"Then give ONE short trigger-based reason for the call. {campaign['opening_purpose']} "
                + f"{campaign.get('opening_ask', 'Then ask if this is a good time to talk for a couple of minutes.')} "
                + f"The example wording above is illustrative — say it in {primary_label}. "
                + f"Keep it warm, natural, and under three sentences. Do NOT quote any specific "
                + f"numbers or account details yet. Remember: the whole opening must be in {primary_label}."
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
        if self._call_ended or self._managed_terminal:
            return
        audio_b64 = base64.b64encode(raw_pcm).decode("ascii")
        await self._send_queue.put(
            json.dumps({
                "type": "input_audio_buffer.append",
                "audio": audio_b64,
            })
        )

    async def _managed_lease_heartbeat_loop(self):
        """Keep this session's application lease alive until the call is finalized."""
        try:
            while not self._call_ended and not self._managed_terminal:
                await asyncio.sleep(SAVINGS_CALL_HEARTBEAT_SECONDS)
                refreshed = await asyncio.to_thread(
                    touch_savings_call,
                    self._call_id,
                    self._customer_id,
                )
                if not refreshed:
                    logger.warning("[%s] Managed call lease is no longer active", self._call_id)
                    return
        except asyncio.CancelledError:
            pass

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
                        session_obj = event.get("session", {})
                        vl_session_id = session_obj.get("id", "unknown")
                        logger.info(
                            "[%s] Session created (vl_session_id=%s, stt=%s)",
                            self._call_id,
                            vl_session_id,
                            session_obj.get("input_audio_transcription"),
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
                            "[%s] Session updated (agent: %s, stt=%s)",
                            self._call_id,
                            self._agent_name,
                            event.get("session", {}).get("input_audio_transcription"),
                        )

                    case "input_audio_buffer.cleared":
                        pass

                    # ── User speech events ────────────────────────────────
                    case "input_audio_buffer.speech_started":
                        # The customer is speaking — record activity and clear any
                        # scheduled hang-up so we NEVER end the call mid-utterance.
                        self._user_speaking = True
                        self._last_user_activity_ts = time.monotonic()
                        if self._pending_end_call and not self._end_call_explicit:
                            # Idle-triggered goodbye only: the customer came back
                            # after silence, so keep the line open.
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
                        if transcript.strip() and self._managed_flow == "savings_account":
                            await self._safe_response_create()

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
                            if self._managed_close_waiting:
                                self._managed_close_waiting = False
                                self._managed_close_armed = True
                            # Cancel watchdog — response started normally
                            if self._response_watchdog and not self._response_watchdog.done():
                                self._response_watchdog.cancel()
                                self._response_watchdog = None

                    # ── Response complete ─────────────────────────────────
                    case "response.done":
                        self._response_active = False
                        resp = event.get("response", {})
                        status = resp.get("status")
                        if self._managed_opening_pending and status == "completed":
                            self._managed_opening_pending = False
                            await asyncio.to_thread(
                                mark_savings_opening_delivered,
                                self._call_id,
                                self._customer_id,
                            )
                        if self._managed_close_armed:
                            self._managed_close_armed = False
                            logger.info("[%s] Managed close delivered — terminating call", self._call_id)
                            asyncio.create_task(
                                self._terminate_call(self._end_call_reason or "Managed workflow finalized.")
                            )
                            continue
                        # Reset the idle clock — the agent just finished, so start
                        # counting the customer's silence from now.
                        self._last_user_activity_ts = time.monotonic()
                        # ── Graceful termination: agent finished its farewell ──
                        if self._pending_end_call:
                            if status == "completed" or self._end_call_explicit:
                                # Terminate when the farewell finishes cleanly, OR
                                # whenever the hang-up was explicit (end_call /
                                # escalate_to_human) — even if the customer barged in
                                # and cut the farewell short. They already agreed to
                                # end, so a reply like "bye / cut the call" must not
                                # keep the line open.
                                self._pending_end_call = False
                                self._end_call_explicit = False
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
                            # Idle goodbye that was cancelled/failed (e.g. the customer
                            # came back and barged in) — drop the hang-up, carry on.
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
                                self._hold_announced = False
                            if self._managed_flow != "savings_account":
                                self._pending_response_create = False
                        elif status != "completed":
                            logger.error(
                                "[%s] Response error: %s",
                                self._call_id,
                                json.dumps(resp.get("status_details", {})),
                            )

                        # Play deferred hold music AFTER agent finishes speaking
                        if self._pending_hold_music is not None:
                            # ── Deterministic consent: never start hold music
                            # unless the agent actually SPOKE a hold announcement
                            # in this response. If the model called
                            # play_hold_music silently, force a brief spoken
                            # announcement first and play the music on the NEXT
                            # response.done. Guarded to fire the forced
                            # announcement at most once, so a mis-behaving model
                            # can't loop the music forever.
                            MIN_ANNOUNCE_BYTES = 24000  # ~0.5s of PCM16 @ 24kHz speech
                            if (
                                self._response_audio_bytes < MIN_ANNOUNCE_BYTES
                                and not self._hold_announced
                            ):
                                self._hold_announced = True
                                logger.info(
                                    "[%s] 🎵 Hold requested without a spoken announcement "
                                    "(%d audio bytes) — forcing announcement before music",
                                    self._call_id, self._response_audio_bytes,
                                )
                                await self._send_json({
                                    "type": "conversation.item.create",
                                    "item": {
                                        "type": "message",
                                        "role": "user",
                                        "content": [{
                                            "type": "input_text",
                                            "text": "[System: You are about to place the customer on hold. BEFORE the hold music starts you MUST tell the customer — in the EXACT SAME language you have both been speaking in this call, do NOT change languages (if the customer has been speaking English, stay in English) — that you're placing them on hold for a moment while you check. Say ONLY that brief hold announcement now. Do NOT answer their question yet and do NOT call any tool.]",
                                        }],
                                    },
                                })
                                await self._safe_response_create()
                            else:
                                duration = self._pending_hold_music
                                self._pending_hold_music = None
                                self._hold_announced = False  # reset for the next hold
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
                                            "text": "[System: Hold music has ended. Reply in the EXACT SAME language you have both been speaking in this call — do NOT change languages (if the customer has been speaking English, stay in English). Begin by thanking the customer for waiting, then deliver your answer.]",
                                        }],
                                    },
                                })
                                # Use safe create — VAD may have already started a response
                                await self._safe_response_create()
                        # Fire deferred response.create (from handoff or tool calls)
                        elif self._pending_response_create:
                            self._pending_response_create = False
                            logger.info("[%s] Firing deferred response.create", self._call_id)
                            await self._send_response_create()

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
            if self._managed_session_started and not self._managed_terminal and not self._call_ended:
                await self._finalize_managed_call(
                    "SYSTEM_ERROR",
                    "Voice connection closed before workflow finalization.",
                    speak_close=False,
                )
                await self._terminate_call("Voice connection closed before workflow finalization.")
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

        try:
            proposed_args = json.loads(args_str) if args_str.strip() else {}
        except json.JSONDecodeError:
            proposed_args = {}

        proposed_result = str(proposed_args.get("result", "")).strip().upper()
        is_pin_capture_attempt = (
            self._managed_flow == "savings_account"
            and self._managed_expected_state == "PIN_CAPTURE"
            and (
                (fn_name == "submit_step_result" and proposed_result == "CAPTURED")
                or fn_name == "validate_pin_code"
            )
        )
        if is_pin_capture_attempt:
            argument_name = "value" if fn_name == "submit_step_result" else "pin_code"
            proposed_pin = "".join(
                char for char in str(proposed_args.get(argument_name, "")) if char.isdigit()
            )
            spoken_digits = _latest_customer_decimal_digits(self._transcript_log)
            if spoken_digits and (len(spoken_digits) != 6 or proposed_pin != spoken_digits):
                if len(spoken_digits) > 6:
                    recovery_action = (
                        "The customer said more than six digits. Do not infer, truncate, or confirm any "
                        "subset. Briefly explain that a postal PIN must contain exactly six digits, then "
                        "ask them to repeat only the six-digit postal PIN."
                    )
                    status = "INVALID_PIN_LENGTH"
                elif len(spoken_digits) < 6:
                    recovery_action = (
                        "The customer said fewer than six digits. Do not fill in missing digits. Briefly "
                        "explain that a postal PIN must contain exactly six digits, then ask them to repeat it."
                    )
                    status = "INVALID_PIN_LENGTH"
                else:
                    recovery_action = (
                        "The proposed PIN does not exactly match the six digits in the latest customer "
                        "utterance. Do not confirm or store it; ask the customer to repeat the six digits."
                    )
                    status = "PIN_TRANSCRIPT_MISMATCH"
                await self._send_json({
                    "type": "conversation.item.create",
                    "item": {
                        "type": "function_call_output",
                        "call_id": call_id,
                        "output": json.dumps({
                            "status": status,
                            "recovery_action": recovery_action,
                        }),
                    },
                })
                await self._safe_response_create()
                return

        if self._managed_flow == "savings_account" and fn_name not in SAVINGS_TOOL_FUNCTIONS:
            logger.error("[%s] Blocked non-savings tool in managed flow: %s", self._call_id, fn_name)
            await self._send_json({
                "type": "conversation.item.create",
                "item": {
                    "type": "function_call_output",
                    "call_id": call_id,
                    "output": json.dumps({
                        "error": "This operation is not permitted in the managed savings workflow.",
                    }),
                },
            })
            await self._safe_response_create()
            return

        if self._managed_terminal:
            await self._send_json({
                "type": "conversation.item.create",
                "item": {
                    "type": "function_call_output",
                    "call_id": call_id,
                    "output": json.dumps({"error": "Call already finalized. Speak no further content."}),
                },
            })
            return

        if self._managed_flow == "savings_account" and fn_name == "submit_step_result":
            if self._managed_last_transition_user_turn == self._user_turn_count:
                await self._send_json({
                    "type": "conversation.item.create",
                    "item": {
                        "type": "function_call_output",
                        "call_id": call_id,
                        "output": json.dumps({
                            "status": "WAIT_FOR_NEW_CUSTOMER_INPUT",
                            "recovery_action": (
                                "One transition was already accepted for the latest customer utterance. "
                                "Do not call any tool now. Follow the current next_action in natural "
                                "customer language, then wait for the customer to speak again."
                            ),
                        }),
                    },
                })
                await self._safe_response_create()
                return

            if proposed_result not in self._managed_allowed_step_results:
                if (
                    self._managed_expected_state == "PIN_CAPTURE"
                    and proposed_result == "HAS_QUESTION"
                ):
                    recovery_action = (
                        "The customer asked a trust question, not a workflow question result. Call no tool. "
                        "Explain that this is a postal PIN, not a banking PIN; it is used only to check "
                        "whether digital savings-account opening is available in their area; it cannot "
                        "access their account or transactions; and sharing it on this call is optional. "
                        "Then ask once, warmly, for the area PIN code. Do not badger or repeat the request "
                        "in this response."
                    )
                else:
                    recovery_action = (
                        "This is not a valid result for the current workflow step. Do not mention this "
                        "internally. Follow the authoritative next_action and wait for a valid customer answer."
                    )
                await self._send_json({
                    "type": "conversation.item.create",
                    "item": {
                        "type": "function_call_output",
                        "call_id": call_id,
                        "output": json.dumps({
                            "status": "NO_STATE_CHANGE",
                            "recovery_action": recovery_action,
                        }),
                    },
                })
                await self._safe_response_create()
                return

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
            self._end_call_explicit = True  # committed hang-up — barge-in won't cancel it
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
            # ── Hand the call off to a human agent (email) ──────────────
            # Sends a summary + action items + transcript to the human agent
            # and shows an escalation banner in the browser. The call is NOT
            # ended here: the agent confirms the hand-off, then asks the
            # customer's permission to close, and only then ends via end_call.
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
            if not result.get("error"):
                # An escalation/handoff email went out — suppress the
                # deterministic fallback email at call termination.
                self._handoff_email_sent = True
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

            if esc_type == "resolved_handoff":
                # Successful close: the team is notified (email + ticket already
                # sent above), but we do NOT hang up here. Let the agent wrap up
                # naturally — thank the customer, mention the follow-up, then ask
                # permission to end. The actual hang-up goes through the normal
                # end_call flow once the customer confirms.
                await self._send_json({
                    "type": "conversation.item.create",
                    "item": {
                        "type": "function_call_output",
                        "call_id": call_id,
                        "output": json.dumps({
                            "status": "handed_off",
                            "reference": ref,
                            "message": (
                                "Warmly thank the customer and tell them you've passed a summary "
                                "and the next steps to the team who will follow up. Then ASK "
                                "whether there's anything else you can help with, or whether you "
                                "may end the call. Do NOT call end_call yet and do NOT hang up — "
                                "wait for their answer. Do NOT promise any specific outcome the "
                                "team has not approved. Say it in the customer's language."
                            ),
                        }),
                    },
                })
                # Let the model speak the hand-off confirmation + permission-to-end ask.
                await self._safe_response_create()
            else:
                # Unresolved escalation: the customer wants a human / is frustrated.
                # The team is notified (email + ticket sent above). We do NOT hang
                # up here — the agent confirms the callback, then asks permission to
                # close. The actual hang-up goes through the normal end_call flow.
                await self._send_json({
                    "type": "conversation.item.create",
                    "item": {
                        "type": "function_call_output",
                        "call_id": call_id,
                        "output": json.dumps({
                            "status": "escalated",
                            "reference": ref,
                            "message": (
                                "Tell the customer, warmly, that you've NOTED their issue and "
                                "escalated it to a human senior officer, and that they will "
                                "receive a CALL BACK shortly. It is a callback, NOT a live "
                                "transfer — do NOT say 'hold on while I connect you' or imply "
                                "anyone is joining the line now. Then ASK whether they need "
                                "anything else, and if not, whether you may close the call. Do "
                                "NOT call end_call yet and do NOT hang up — wait for their "
                                "answer. Do NOT promise any specific outcome the team has not "
                                "approved. Say it in the customer's language."
                            ),
                        }),
                    },
                })
                # Let the model speak the escalation confirmation + permission-to-end ask.
                await self._safe_response_create()

        elif fn_name in ALL_TOOL_FUNCTIONS:
            # ── CRM data tool call ───────────────────────────────────────
            try:
                args = json.loads(args_str) if args_str.strip() else {}
            except json.JSONDecodeError:
                args = {}

            if (
                self._managed_flow == "savings_account"
                and fn_name == "validate_pin_code"
                and self._managed_expected_state == "PIN_CAPTURE"
            ):
                pin_code = "".join(char for char in str(args.get("pin_code", "")) if char.isdigit())
                if len(pin_code) == 6:
                    logger.warning(
                        "[%s] Recovering premature PIN validation call as PIN capture",
                        self._call_id,
                    )
                    result = submit_savings_step_result(
                        self._call_id,
                        self._customer_id,
                        "PIN_CAPTURE",
                        "CAPTURED",
                        pin_code,
                    )
                    if result.get("status") == "ACCEPTED":
                        self._managed_last_transition_user_turn = self._user_turn_count
                    await self._send_json({
                        "type": "conversation.item.create",
                        "item": {
                            "type": "function_call_output",
                            "call_id": call_id,
                            "output": json.dumps({
                                **result,
                                "recovered_action": "PIN captured only; it has not been validated.",
                            }),
                        },
                    })
                    await self._safe_response_create()
                    return

            tool_fn = ALL_TOOL_FUNCTIONS[fn_name]

            # Determine which params the function needs
            import inspect
            sig = inspect.signature(tool_fn)
            call_kwargs = {}
            for param_name in sig.parameters:
                if param_name == "customer_id":
                    call_kwargs["customer_id"] = self._customer_id
                elif param_name == "call_id":
                    call_kwargs["call_id"] = self._call_id
                elif param_name == "transcript":
                    call_kwargs["transcript"] = list(self._transcript_log)
                elif param_name in args:
                    call_kwargs[param_name] = args[param_name]
            if fn_name == "submit_step_result":
                call_kwargs["current_state"] = self._managed_expected_state or ""

            try:
                result = tool_fn(**call_kwargs)
            except Exception as exc:
                logger.exception("[%s] CRM tool error: %s", self._call_id, fn_name)
                result = {"error": str(exc)}

            if (
                self._managed_flow == "savings_account"
                and fn_name == "submit_step_result"
                and result.get("status") == "ACCEPTED"
            ):
                self._managed_last_transition_user_turn = self._user_turn_count

            # Capture a recorded promise-to-pay so the deterministic follow-up
            # email at call end can include the commitment details.
            if (
                fn_name == "record_payment_commitment"
                and isinstance(result, dict)
                and not result.get("error")
            ):
                self._recorded_ptp = {
                    "amount": result.get("amount"),
                    "promise_date": result.get("promise_date"),
                    "method": result.get("method"),
                    "reference": result.get("reference"),
                }
            if (
                self._managed_flow == "savings_account"
                and fn_name == "create_escalation"
                and result.get("status") in ("CREATED", "QUEUED")
            ):
                self._handoff_email_sent = True
            if (
                self._managed_flow == "savings_account"
                and fn_name == "finalize_call"
                and result.get("status") == "FINALIZED"
            ):
                self._managed_terminal = True
                self._managed_close_waiting = True
                self._managed_customer_close = result.get("customer_close", "")
                self._end_call_reason = f"Savings workflow finalized: {result.get('outcome', 'completed')}"

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
            await self._send_response_create()

    async def _send_response_create(self):
        """Create a response, injecting fresh authoritative context for managed calls."""
        if self._managed_flow != "savings_account":
            await self._send_json({"type": "response.create"})
            return
        if self._managed_terminal and self._managed_customer_close:
            await self._send_json({
                "type": "response.create",
                "response": {
                    "modalities": ["audio", "text"],
                    "tool_choice": "none",
                    "instructions": (
                        "Speak exactly the following closing text with no preface, addition, "
                        "translation, question, or tool call:\n"
                        + self._managed_customer_close
                    ),
                },
            })
            return
        context = await asyncio.to_thread(
            get_savings_runtime_context,
            self._call_id,
            self._customer_id,
        )
        self._managed_expected_state = context.get("call_state")
        self._managed_allowed_step_results = frozenset(context.get("allowed_step_results", []))
        if self._managed_state_user_turn != self._user_turn_count:
            self._managed_state_user_turn = self._user_turn_count
            self._managed_state_at_user_turn = self._managed_expected_state
        context["state_when_latest_customer_utterance_arrived"] = self._managed_state_at_user_turn
        context["current_state_was_entered_after_latest_utterance"] = (
            self._managed_state_at_user_turn != self._managed_expected_state
        )
        context["transition_already_accepted_for_latest_customer_utterance"] = (
            self._managed_last_transition_user_turn == self._user_turn_count
        )
        await self._send_json({
            "type": "response.create",
            "response": {
                "modalities": ["audio", "text"],
                "instructions": _build_managed_turn_instructions(context),
            },
        })

    async def _finalize_managed_call(
        self,
        outcome: str,
        reason: str,
        *,
        speak_close: bool,
    ) -> dict:
        """Finalize an infrastructure-driven managed outcome outside a model tool call."""
        if not self._managed_session_started:
            return {"status": "REJECTED"}
        result = await asyncio.to_thread(
            finalize_savings_call,
            self._call_id,
            self._customer_id,
            outcome,
            reason,
            response_style="HINDI",
        )
        if result.get("status") == "FINALIZED":
            self._managed_terminal = True
            if self._managed_lease_heartbeat and not self._managed_lease_heartbeat.done():
                self._managed_lease_heartbeat.cancel()
            self._managed_customer_close = result.get("customer_close", "")
            self._end_call_reason = f"Savings workflow finalized: {result.get('outcome', outcome)}"
            if speak_close:
                self._managed_close_waiting = True
                await self._safe_response_create()
        return result

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
                await self._send_response_create()
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
                if self._managed_flow == "savings_account":
                    await self._finalize_managed_call(
                        "NO_RESPONSE",
                        "Customer remained silent until the configured idle timeout.",
                        speak_close=True,
                    )
                    return
                # Ask the agent to say a brief goodbye; termination fires on
                # response.done via the _pending_end_call path. This goodbye IS
                # cancellable — if the customer comes back and speaks, keep going.
                self._pending_end_call = True
                self._end_call_explicit = False
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

    async def _send_fallback_handoff_email(self):
        """
        Deterministic safety net: if the call is ending and NO follow-up email
        has been sent this session, e-mail a resolved-handoff summary to the
        human team so a recovery/commitment call always produces a follow-up.
        Runs the blocking send off the event loop and never raises.
        """
        if self._managed_flow == "savings_account" or self._handoff_email_sent:
            return
        self._handoff_email_sent = True  # guard against double-send / re-entry
        try:
            from crm_tools import send_escalation

            ptp = self._recorded_ptp
            if ptp:
                amount = ptp.get("amount")
                amount_str = f"₹{amount:,.0f}" if isinstance(amount, (int, float)) else str(amount)
                reason = "Promise-to-pay recorded — a human must follow up to ensure it is honoured."
                summary = (
                    f"Customer committed to pay {amount_str} by "
                    f"{ptp.get('promise_date', 'the agreed date')} via "
                    f"{ptp.get('method', 'the agreed method')} "
                    f"(reference {ptp.get('reference', 'n/a')}). "
                    "Auto-generated at call end because no follow-up email was sent during the call."
                )
                action_items = [
                    f"Confirm payment of {amount_str} on/around {ptp.get('promise_date', 'the promised date')}",
                    "Escalate if the payment is not received by the promised date",
                ]
            else:
                reason = "Auto-generated end-of-call summary — no follow-up email was sent during the call."
                summary = (
                    "Call ended without a recorded commitment or escalation. "
                    "Review the transcript and decide on next steps."
                )
                action_items = [
                    "Review the call transcript",
                    "Decide whether a follow-up contact is needed",
                ]

            result = await asyncio.to_thread(
                send_escalation,
                customer_id=self._customer_id,
                campaign=get_campaign(self._campaign_key).get("title", self._campaign_key),
                agent=self._agent_name,
                reason=reason,
                summary=summary,
                action_items=action_items,
                priority="medium",
                escalation_type="resolved_handoff",
                transcript=list(self._transcript_log),
            )
            logger.info(
                "[%s] 📧 Auto follow-up email sent at call end | ticket=%s | delivery=%s",
                self._call_id, result.get("reference", ""), result.get("delivery", ""),
            )
        except Exception:
            logger.exception("[%s] Auto follow-up email failed", self._call_id)

    async def _terminate_call(self, reason: str):
        """
        Tear the call down: let the farewell audio drain, tell the browser to
        end, then close the Voice Live socket. Idempotent.
        """
        if self._call_ended:
            return
        self._call_ended = True
        if self._managed_lease_heartbeat and not self._managed_lease_heartbeat.done():
            self._managed_lease_heartbeat.cancel()
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
        # Deterministic follow-up: if no email went out this session, send one
        # now (concurrently with the farewell grace period) so a recovery /
        # commitment call always yields a human follow-up.
        email_task = None
        if self._managed_flow != "savings_account":
            email_task = asyncio.create_task(self._send_fallback_handoff_email())
        # Stop the idle watchdog if it's still running.
        if self._idle_monitor and not self._idle_monitor.done():
            self._idle_monitor.cancel()
        await asyncio.sleep(grace_s)
        if email_task:
            try:
                await asyncio.wait_for(email_task, timeout=10)
            except Exception:
                pass
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

        try:
            for item_id in self._conversation_item_ids:
                await self._send_json({
                    "type": "conversation.item.retrieve",
                    "item_id": item_id,
                })
        except ConnectionClosed:
            self._retrieve_pending = 0
            logger.info(
                "[%s] Skipping conversation retrieval because Voice Live is already closed",
                self._call_id,
            )
            return

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
        if self._managed_session_started and not self._managed_terminal:
            outcome = "NO_RESPONSE" if self._user_turn_count == 0 else "SYSTEM_ERROR"
            await self._finalize_managed_call(
                outcome,
                "Browser connection closed before workflow finalization.",
                speak_close=False,
            )
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
            "managedFlow": c.get("managed_flow"),
        }
        for key, c in CAMPAIGN_REGISTRY.items()
    ]
    return jsonify(campaigns)


@app.route("/api/customers")
async def api_customers():
    """Return the demo customers (with per-product overdue flags) for the picker."""
    from quart import jsonify
    from crm_tools import _query
    from savings_account_tools import _demo_reset_enabled
    # With demo-reset on, a finalized savings app is restored to INCOMPLETE at call
    # start, so any customer with a savings app is demo-eligible (not just INCOMPLETE).
    savings_pred = (
        "COUNT(*)" if _demo_reset_enabled()
        else "SUM(CASE WHEN sa.status = 'INCOMPLETE' THEN 1 ELSE 0 END)"
    )
    rows = _query(
        "SELECT c.id, c.name, c.segment, c.city, c.phone, "
        "(SELECT COUNT(*) FROM collections cl WHERE cl.customer_id = c.id) AS card_overdue, "
        "(SELECT COUNT(*) FROM loan_collections lc WHERE lc.customer_id = c.id) AS loan_overdue, "
        "(SELECT COUNT(*) FROM life_policies lp WHERE lp.customer_id = c.id "
        "  AND lp.status IN ('In Grace','Lapsed')) AS premium_overdue, "
        f"(SELECT {savings_pred} FROM savings_applications sa WHERE sa.customer_id = c.id) AS incomplete_savings_application "
        "FROM customers c ORDER BY c.name"
    )
    for r in rows:
        r["card_overdue"] = bool(r.get("card_overdue"))
        r["loan_overdue"] = bool(r.get("loan_overdue"))
        r["premium_overdue"] = bool(r.get("premium_overdue"))
        r["incomplete_savings_application"] = bool(r.get("incomplete_savings_application"))
        r["overdue"] = r["card_overdue"] or r["loan_overdue"] or r["premium_overdue"]
    return jsonify(rows)


@app.route("/api/ambient")
async def api_ambient():
    """Continuous ambient bed (PCM16 mono @ 24 kHz) for the browser to loop; 204 when off."""
    from quart import Response
    mixer = get_ambient_mixer()
    pcm = mixer.bed_pcm() if mixer is not None else b""
    if not pcm:
        return Response(b"", status=204)
    return Response(
        pcm,
        content_type="application/octet-stream",
        headers={"X-Sample-Rate": str(AMBIENT_SAMPLE_RATE), "Cache-Control": "no-store"},
    )


@app.websocket("/web/ws")
async def web_ws():
    """
    Browser WebSocket endpoint.

    Protocol:
        Browser → Server:  first text msg = JSON {"campaignId": "...", "customerId": "...", "tone": "..."}
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
    tone = DEFAULT_TONE
    languages = None
    try:
        first_msg = await websocket.receive()
        if isinstance(first_msg, str):
            data = json.loads(first_msg)
            customer_id = data.get("customerId", "rajesh")
            campaign_id = data.get("campaignId", DEFAULT_CAMPAIGN)
            tone = data.get("tone", DEFAULT_TONE)
            languages = data.get("languages")
            logger.info(
                "Call setup — campaign=%s customer=%s tone=%s languages=%s",
                campaign_id, customer_id, tone, languages,
            )
    except Exception:
        pass

    # Pass the pre-started auth task to the session
    session = VoiceLiveSession(
        websocket, customer_id=customer_id, campaign_key=campaign_id,
        tone=tone, languages=languages,
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


@app.before_serving
async def _log_ambient_config():
    """Announce + pre-load the ambient mixer at startup so the first call is instant."""
    logger.info(ambient_config_summary())
    # Build the shared mixer now (loads the WAV off the event loop) — the
    # AmbientMixer log then confirms whether the source is a file or synthetic.
    await asyncio.to_thread(get_ambient_mixer)


# ──────────────────────────────────────────────────────────────────────────────
# Entry point
# ──────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    app.run(
        debug=True,
        host=os.getenv("APP_HOST", "0.0.0.0"),
        port=int(os.getenv("APP_PORT", "8000")),
    )
