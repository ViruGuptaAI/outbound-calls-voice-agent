"""Text driver for the REAL Asha savings agent (backend state machine + tools).

This reproduces the managed per-turn loop from server/app.py, but over TEXT chat
completions instead of the Voice Live WebSocket. Every turn:

  * reads the live SQLite runtime context (get_savings_runtime_context)
  * injects the exact turn guard + context that production injects
  * calls the same LLM (gpt-4.1-mini) with the same system prompt + the 8 tools
  * executes tool calls against the REAL backend (savings_account_tools), applying
    the same guards production applies (one transition per utterance, PIN transcript
    match, current_state injection, finalize close)

So this exercises the true agent — including recovery and state — not just the prompt.
"""

from __future__ import annotations

import json
import os
import sys
import unicodedata
from contextlib import closing
from pathlib import Path
from typing import Any

os.environ.setdefault("AZURE_TOKEN_CREDENTIALS", "dev")

# Import the REAL agent + backend from the server package.
_SERVER_DIR = Path(__file__).resolve().parents[2] / "server"
if str(_SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(_SERVER_DIR))

from campaigns.savings_account_sales_agent import (  # noqa: E402
    SAVINGS_ACCOUNT_SALES_PROMPT,
    SAVINGS_ACCOUNT_SALES_TOOLS,
)
from savings_account_tools import (  # noqa: E402
    SAVINGS_TOOL_FUNCTIONS,
    get_savings_runtime_context,
    mark_savings_opening_delivered,
    start_savings_call,
    submit_step_result,
)
import savings_account_tools as _sat  # noqa: E402  (for the eval-only state reset)

from azure.identity import DefaultAzureCredential, get_bearer_token_provider  # noqa: E402
from openai import AzureOpenAI  # noqa: E402


AGENT_ENDPOINT = "https://foundryresourcefordemos.cognitiveservices.azure.com/"
AGENT_API_VERSION = "2024-10-21"
AGENT_MODEL = "gpt-4.1-mini"

_token_provider = get_bearer_token_provider(
    DefaultAzureCredential(), "https://cognitiveservices.azure.com/.default"
)
_client = AzureOpenAI(
    azure_endpoint=AGENT_ENDPOINT, azure_ad_token_provider=_token_provider, api_version=AGENT_API_VERSION
)


# ── Ported verbatim from server/app.py (the managed per-turn injection) ───────
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
            return "".join(str(unicodedata.decimal(c)) for c in text if c.isdecimal())
    return ""


def _chat_tools() -> list[dict]:
    """Convert Voice Live function schemas to Chat Completions tool format."""
    out = []
    for t in SAVINGS_ACCOUNT_SALES_TOOLS:
        out.append({
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t.get("description", ""),
                "parameters": t.get("parameters", {"type": "object", "properties": {}}),
            },
        })
    return out


_CHAT_TOOLS = _chat_tools()
TOOL_DEFINITIONS_FOR_EVAL = [
    {"type": "function", "name": t["name"], "description": t.get("description", ""), "parameters": t.get("parameters", {})}
    for t in SAVINGS_ACCOUNT_SALES_TOOLS
]


def _reset_customer_state(customer_id: str) -> None:
    """Eval-only: force a clean slate so reused demo customers never collide.

    Clears any do-not-call suppression, resets the application to INCOMPLETE, and
    terminates dangling non-terminal sessions (bypasses the lease guard, which is
    safe here because scenarios run strictly sequentially).
    """
    with closing(_sat._connect()) as db:
        db.execute("DELETE FROM do_not_call WHERE customer_id = ?", (customer_id,))
        db.execute(
            "UPDATE savings_applications SET status = 'INCOMPLETE', selected_product = NULL, "
            "confirmed_product = NULL WHERE customer_id = ?",
            (customer_id,),
        )
        db.execute(
            "UPDATE savings_call_sessions SET terminal_outcome = 'SYSTEM_ERROR', "
            "terminal_reason = 'eval reset', call_state = 'TERMINAL' "
            "WHERE customer_id = ? AND terminal_outcome IS NULL",
            (customer_id,),
        )
        db.commit()


class AshaConversation:
    """One managed savings call, driven over text against the real backend."""

    def __init__(self, call_id: str, customer_id: str, first_name: str):
        self.call_id = call_id
        self.customer_id = customer_id
        self.first_name = first_name
        self.history: list[dict] = [{"role": "system", "content": SAVINGS_ACCOUNT_SALES_PROMPT}]
        self.transcript: list[tuple[str, str]] = []  # (role, text) for PIN match + escalation
        self.tool_interactions: list[dict] = []  # {name, arguments, result} for eval
        self.user_turn_count = 0
        self._state_at_user_turn: str | None = None
        self._state_user_turn: int | None = None
        self._last_transition_user_turn: int | None = None
        self.terminal = False
        self.terminal_outcome: str | None = None

    # ── opening (agent speaks first, like production) ─────────────────────────
    def start(self) -> str:
        _reset_customer_state(self.customer_id)
        ctx = start_savings_call(self.call_id, self.customer_id)
        if ctx.get("error"):
            raise RuntimeError(f"start_savings_call rejected: {ctx}")
        opening = _build_managed_savings_opening(self.first_name)
        self.history.append({"role": "assistant", "content": opening})
        self.transcript.append(("assistant", opening))
        mark_savings_opening_delivered(self.call_id, self.customer_id)
        return opening

    def _runtime_context(self) -> dict:
        ctx = get_savings_runtime_context(self.call_id, self.customer_id)
        expected = ctx.get("call_state")
        if self._state_user_turn != self.user_turn_count:
            self._state_user_turn = self.user_turn_count
            self._state_at_user_turn = expected
        ctx["state_when_latest_customer_utterance_arrived"] = self._state_at_user_turn
        ctx["current_state_was_entered_after_latest_utterance"] = self._state_at_user_turn != expected
        ctx["transition_already_accepted_for_latest_customer_utterance"] = (
            self._last_transition_user_turn == self.user_turn_count
        )
        return ctx

    def _dispatch_tool(self, name: str, args: dict, expected_state: str, allowed: list[str]) -> dict:
        """Apply production guards then execute against the real backend."""
        proposed_result = str(args.get("result", "")).strip().upper()

        # PIN transcript match (mirror app.py)
        if expected_state == "PIN_CAPTURE" and (
            (name == "submit_step_result" and proposed_result == "CAPTURED") or name == "validate_pin_code"
        ):
            arg_name = "value" if name == "submit_step_result" else "pin_code"
            proposed_pin = "".join(c for c in str(args.get(arg_name, "")) if c.isdigit())
            spoken = _latest_customer_decimal_digits(self.transcript)
            if spoken and (len(spoken) != 6 or proposed_pin != spoken):
                return {"status": "INVALID_PIN", "recovery_action": "Ask the customer to repeat only the six-digit postal PIN."}

        if name not in SAVINGS_TOOL_FUNCTIONS:
            return {"error": "not permitted in managed savings workflow"}
        if self.terminal:
            return {"error": "Call already finalized."}

        # premature validate_pin_code recovered as capture (mirror app.py)
        if name == "validate_pin_code" and expected_state == "PIN_CAPTURE":
            pin_code = "".join(c for c in str(args.get("pin_code", "")) if c.isdigit())
            if len(pin_code) == 6:
                result = submit_step_result(self.call_id, self.customer_id, "PIN_CAPTURE", "CAPTURED", pin_code)
                if result.get("status") == "ACCEPTED":
                    self._last_transition_user_turn = self.user_turn_count
                return {**result, "recovered_action": "PIN captured only; not validated."}

        if name == "submit_step_result":
            if self._last_transition_user_turn == self.user_turn_count:
                return {"status": "WAIT_FOR_NEW_CUSTOMER_INPUT",
                        "recovery_action": "One transition already accepted this turn; follow next_action and wait."}
            if proposed_result not in allowed:
                return {"status": "NO_STATE_CHANGE",
                        "recovery_action": "Not a valid result for the current step; follow next_action."}

        # execute with injected identity/state
        import inspect
        fn = SAVINGS_TOOL_FUNCTIONS[name]
        kwargs: dict[str, Any] = {}
        for p in inspect.signature(fn).parameters:
            if p == "customer_id":
                kwargs["customer_id"] = self.customer_id
            elif p == "call_id":
                kwargs["call_id"] = self.call_id
            elif p == "transcript":
                kwargs["transcript"] = list(self.transcript)
            elif p == "current_state":
                kwargs["current_state"] = expected_state
            elif p in args:
                kwargs[p] = args[p]
        try:
            result = fn(**kwargs)
        except Exception as exc:  # noqa: BLE001
            result = {"error": str(exc)}

        if name == "submit_step_result" and result.get("status") == "ACCEPTED":
            self._last_transition_user_turn = self.user_turn_count
        if name == "finalize_call" and result.get("status") == "FINALIZED":
            self.terminal = True
            self.terminal_outcome = result.get("outcome")
        return result

    # ── one Asha turn in response to a customer utterance ────────────────────
    def respond_to(self, customer_utterance: str, max_tool_iters: int = 6) -> str:
        self.user_turn_count += 1
        self.history.append({"role": "user", "content": customer_utterance})
        self.transcript.append(("user", customer_utterance))

        spoken = ""
        for _ in range(max_tool_iters):
            ctx = self._runtime_context()
            expected_state = ctx.get("call_state")
            allowed = ctx.get("allowed_step_results", [])
            turn_instr = {"role": "system", "content": _build_managed_turn_instructions(ctx)}
            call_messages = self.history + [turn_instr]

            resp = _client.chat.completions.create(
                model=AGENT_MODEL, messages=call_messages, tools=_CHAT_TOOLS,
                tool_choice="auto", temperature=0.4, max_tokens=500,
            )
            msg = resp.choices[0].message

            if msg.tool_calls:
                # persist the assistant tool-call message
                self.history.append({
                    "role": "assistant",
                    "content": msg.content or "",
                    "tool_calls": [
                        {"id": tc.id, "type": "function",
                         "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
                        for tc in msg.tool_calls
                    ],
                })
                for tc in msg.tool_calls:
                    try:
                        args = json.loads(tc.function.arguments or "{}")
                    except json.JSONDecodeError:
                        args = {}
                    result = self._dispatch_tool(tc.function.name, args, expected_state, allowed)
                    self.tool_interactions.append(
                        {"name": tc.function.name, "arguments": args, "result": result}
                    )
                    self.history.append({
                        "role": "tool", "tool_call_id": tc.id,
                        "content": json.dumps(result, ensure_ascii=False),
                    })
                    if self.terminal:
                        spoken = result.get("customer_close", "") or spoken
                if self.terminal and spoken:
                    self.history.append({"role": "assistant", "content": spoken})
                    self.transcript.append(("assistant", spoken))
                    return spoken
                continue  # feed tool results back for the spoken reply

            spoken = msg.content or ""
            self.history.append({"role": "assistant", "content": spoken})
            self.transcript.append(("assistant", spoken))
            return spoken

        # tool loop exhausted without a spoken reply
        fallback = "क्षमा कीजिए, कृपया एक बार फिर बताइए।"
        self.history.append({"role": "assistant", "content": fallback})
        self.transcript.append(("assistant", fallback))
        return spoken or fallback
