"""Render an application-side view of one managed savings LLM generation."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from app import _build_managed_turn_instructions, build_session_config


SAMPLE_HISTORY: list[dict[str, Any]] = [
    {
        "role": "assistant",
        "content": "नमस्कार Priya जी, मैं Contoso Bank की virtual assistant Asha बोल रही हूँ। क्या मेरी बात Priya जी से हो रही है?",
    },
    {
        "role": "user",
        "content": "हाँ, मैं Priya हूँ।",
    },
    {
        "type": "function_call",
        "name": "submit_step_result",
        "arguments": {"result": "CONFIRMED"},
    },
    {
        "type": "function_call_output",
        "name": "submit_step_result",
        "output": {
            "status": "ACCEPTED",
            "previous_state": "RECIPIENT_CONFIRMATION",
            "next_state": "AVAILABILITY",
            "next_action": "Offer to help complete the application and ask whether this is a good time.",
        },
    },
]

SAMPLE_CONTEXT: dict[str, Any] = {
    "call_state": "AVAILABILITY",
    "allowed_step_results": ["AVAILABLE", "BUSY", "ALREADY_COMPLETED", "NOT_INTERESTED"],
    "next_action": (
        "Warmly say the team noticed the FinServe Instant savings-account application "
        "was left midway, offer to help complete it now, and ask whether this is a good time."
    ),
    "customer_first_name": "Priya",
    "application_started_date_spoken": "1 August 2026",
    "last_completed_step_spoken": "mobile number verification",
    "last_completed_step_status": "COMPLETED_DO_NOT_REPEAT",
    "estimated_eligibility_time_spoken": "कुछ ही minutes",
    "application_status": "INCOMPLETE",
    "state_when_latest_customer_utterance_arrived": "RECIPIENT_CONFIRMATION",
    "current_state_was_entered_after_latest_utterance": True,
    "transition_already_accepted_for_latest_customer_utterance": True,
}


def render_effective_input(
    context: dict[str, Any] | None = None,
    history: list[dict[str, Any]] | None = None,
) -> str:
    """Flatten app-supplied model inputs into one inspectable debug document."""
    session = build_session_config("savings_account_completion")["session"]
    runtime_context = dict(context or SAMPLE_CONTEXT)
    conversation = history or SAMPLE_HISTORY
    sections = [
        "APPLICATION-SUPPLIED EFFECTIVE LLM INPUT (REPRESENTATIVE TURN)",
        (
            "This is a debug visualization, not Azure's private wire serialization. "
            "The API receives these as separate instruction, tool, and conversation fields. "
            "Provider-internal instructions are not exposed to this application."
        ),
        "\n===== 1. PERSISTENT SESSION INSTRUCTIONS =====\n" + session["instructions"],
        "\n===== 2. AVAILABLE TOOL SCHEMAS =====\n"
        + json.dumps(session["tools"], ensure_ascii=False, indent=2),
        "\n===== 3. CONVERSATION HISTORY =====\n"
        + json.dumps(conversation, ensure_ascii=False, indent=2),
        "\n===== 4. RESPONSE-SCOPED INSTRUCTIONS FOR THIS GENERATION =====\n"
        + _build_managed_turn_instructions(runtime_context),
    ]
    return "\n".join(sections) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="Write the rendered context to this UTF-8 file.")
    args = parser.parse_args()
    rendered = render_effective_input()
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")


if __name__ == "__main__":
    main()