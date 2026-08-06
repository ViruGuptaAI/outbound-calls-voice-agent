"""Call-level pass: submit ONE record per call to the whole-arc evaluators only.

The turn-by-turn run (evaluate_text.py) deflates task_completion / task_adherence /
intent_resolution because every mid-call turn is graded as "not yet complete". Those
three are whole-TASK questions — only well-defined at the end of a call. Here each call
is collapsed into a single record (full dialogue as query; the agent's full trajectory —
every tool call + result plus the final reply — as response) and scored by just those
three evaluators.

Built from the existing datasets/text_eval.jsonl — does NOT re-run the 20 calls.
Key-less: uses managed identity / AzureCliCredential via AZURE_TOKEN_CREDENTIALS=dev.
"""

from __future__ import annotations

import json
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

from dotenv import load_dotenv  # noqa: E402

load_dotenv(HARNESS / ".env")

if str(HARNESS) not in sys.path:
    sys.path.insert(0, str(HARNESS))

import voice_agent_evaluation  # noqa: E402

# Only the whole-arc evaluators — the per-step (tool_*, groundedness, relevance) ones
# stay on the turn-level run where each step is a self-contained judgment.
ARC_EVALUATORS = ["intent_resolution", "task_adherence", "task_completion"]


def build_call_level(turn_path: Path, call_path: Path) -> int:
    """Collapse the per-turn JSONL into one record per call.

    `query` = the LAST turn's full-dialogue snapshot (system + every prior turn + the
    last customer line). `response` = the whole-call trajectory: every turn's tool_call
    and tool_result messages (re-id'd globally so each result stays paired with its call)
    followed by the final spoken reply, so the arc evaluators can actually SEE the tool
    actions + results. `tool_calls` aggregates the calls for tool_call_accuracy/selection.
    """
    by_conv: dict[str, list[dict]] = {}
    with turn_path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            item = json.loads(line)
            by_conv.setdefault(item.get("conversation_id", "?"), []).append(item)

    records = []
    for conv_id, items in by_conv.items():
        items.sort(key=lambda x: x.get("turn_number", 0))
        final = items[-1]

        # The whole-call `response` must carry the FULL trajectory so task_adherence /
        # intent_resolution can see the tool actions + results (they read tool activity
        # from `response`, never from the side `tool_calls` field). Concatenate every
        # turn's tool_call / tool_result messages, re-id'd globally so each result stays
        # paired with its call, then append the call's final spoken reply.
        agg_calls = []
        agg_response: list[dict] = []
        gid = 0
        for item in items:
            id_map: dict[str, str] = {}
            for msg in item.get("response", []):
                role = msg.get("role")
                content = msg.get("content")
                if role == "assistant" and isinstance(content, list):
                    new_content = []
                    for c in content:
                        c = dict(c)
                        if c.get("type") == "tool_call":
                            new_id = f"call_{gid}"
                            gid += 1
                            id_map[c.get("tool_call_id")] = new_id
                            c["tool_call_id"] = new_id
                        new_content.append(c)
                    agg_response.append({"role": "assistant", "content": new_content})
                elif role == "tool":
                    m = dict(msg)
                    old = m.get("tool_call_id")
                    if old in id_map:
                        m["tool_call_id"] = id_map[old]
                    agg_response.append(m)
                # trailing plain-text assistant replies are already in `query`; skip here
            for tc in item.get("tool_calls", []):
                agg_calls.append(dict(tc))
        for i, tc in enumerate(agg_calls):
            tc["tool_call_id"] = f"call_{i}"

        final_text = ""
        for msg in reversed(final.get("response", [])):
            if msg.get("role") == "assistant" and isinstance(msg.get("content"), str):
                final_text = msg["content"]
                break
        agg_response.append({"role": "assistant", "content": final_text})

        records.append({
            "query": final["query"],
            "response": agg_response,
            "tool_calls": agg_calls,
            "tool_definitions": final["tool_definitions"],
            "conversation_id": conv_id,
        })

    records.sort(key=lambda r: r["conversation_id"])
    with call_path.open("w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    return len(records)


def main() -> None:
    turn_path = HERE / "datasets" / "text_eval.jsonl"
    call_path = HERE / "datasets" / "text_eval_calls.jsonl"
    if not turn_path.exists():
        raise SystemExit(f"Missing {turn_path}. Run run.py first.")

    n = build_call_level(turn_path, call_path)
    print(f"Built {n} call-level records \u2192 {call_path}")

    out_dir = HERE / "output_calls"
    out_dir.mkdir(parents=True, exist_ok=True)

    voice_agent_evaluation.main(
        eval_input_path=str(call_path),
        output_folder=str(out_dir),
        eval_group_name="text_asha_savings_calllevel",
        evaluators=ARC_EVALUATORS,
    )


if __name__ == "__main__":
    main()
