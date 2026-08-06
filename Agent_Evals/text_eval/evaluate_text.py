"""Submit the text-driven eval JSONL to the Foundry evaluators (key-less).

Reuses the harness's voice_agent_evaluation.main() — the same eval submission that
worked for the voice run — but on our text_eval.jsonl, and now WITH the tool
evaluators enabled (they're meaningful here because the tools actually executed).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # pragma: no cover
    pass

os.environ.setdefault("AZURE_TOKEN_CREDENTIALS", "dev")

HERE = Path(__file__).resolve().parent
HARNESS = Path(r"C:\src\voicelive-evaluation\evaluation_harness")

# Load the harness .env (PROJECT_ENDPOINT, AOAI_DEPLOYMENT_NAME, AOAI_REASONING_DEPLOYMENT_NAME).
from dotenv import load_dotenv  # noqa: E402

load_dotenv(HARNESS / ".env")

if str(HARNESS) not in sys.path:
    sys.path.insert(0, str(HARNESS))

import voice_agent_evaluation  # noqa: E402


EVALUATORS = [
    "intent_resolution",
    "task_adherence",
    "task_completion",
    "tool_call_accuracy",
    "tool_selection",
    "tool_input_accuracy",
    "tool_output_utilization",
    "groundedness",
    "relevance",
]


def main() -> None:
    eval_input = HERE / "datasets" / "text_eval.jsonl"
    out_dir = HERE / "output"
    out_dir.mkdir(parents=True, exist_ok=True)
    if not eval_input.exists():
        raise SystemExit(f"Missing {eval_input}. Run run.py first.")

    voice_agent_evaluation.main(
        eval_input_path=str(eval_input),
        output_folder=str(out_dir),
        eval_group_name="text_asha_savings",
        evaluators=EVALUATORS,
    )


if __name__ == "__main__":
    main()
