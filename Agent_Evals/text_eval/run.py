"""Run the 20 text scenarios: drive the REAL Asha against the adaptive customer,
capture the conversation + tool interactions, and write the Foundry-eval JSONL."""

from __future__ import annotations

import sys

# Windows consoles default to cp1252; the progress lines contain non-ASCII markers.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # pragma: no cover
    pass

import argparse
import json
import uuid
from pathlib import Path

from driver import AshaConversation, SAVINGS_ACCOUNT_SALES_PROMPT, TOOL_DEFINITIONS_FOR_EVAL
from simulator import SimulatedCustomer
from scenarios import SCENARIOS

HERE = Path(__file__).resolve().parent
DATASETS = HERE / "datasets"
MAX_TURNS = 16


def _tool_calls_for_eval(interactions: list[dict]) -> list[dict]:
    calls = []
    for i, it in enumerate(interactions):
        calls.append({
            "type": "tool_call",
            "tool_call_id": f"call_{i}",
            "name": it["name"],
            "arguments": it["arguments"],
        })
    return calls


def _response_trajectory(interactions: list[dict], final_text: str) -> list[dict]:
    """Build the agent's turn as the message list the Azure agentic evaluators read:
    assistant(tool_call) -> tool(tool_result) -> ... -> assistant(final text).

    task_adherence / intent_resolution / groundedness / tool_output_utilization see
    tool activity + results ONLY from `response` (they do NOT read the separate
    `tool_calls` field). This is the SDK schema emitted by
    azure.ai.evaluation._converters._models.break_tool_call_into_messages.
    """
    msgs: list[dict] = []
    for i, it in enumerate(interactions):
        tc_id = f"call_{i}"
        msgs.append({
            "role": "assistant",
            "content": [{
                "type": "tool_call",
                "tool_call_id": tc_id,
                "name": it["name"],
                "arguments": it["arguments"],
            }],
        })
        msgs.append({
            "role": "tool",
            "tool_call_id": tc_id,
            "content": [{"type": "tool_result", "tool_result": it.get("result")}],
        })
    msgs.append({"role": "assistant", "content": final_text})
    return msgs


def run_scenario(scenario: dict) -> dict:
    call_id = f"TXT-{scenario['id']}-{uuid.uuid4().hex[:6]}"
    conv = AshaConversation(call_id, scenario["customer_id"], scenario["first_name"])
    opening = conv.start()
    sim = SimulatedCustomer(scenario["behaviour"])
    sim.observe_agent(opening)

    turns = [{"role": "assistant", "text": opening, "tools": []}]
    query_messages = [
        {"role": "system", "content": SAVINGS_ACCOUNT_SALES_PROMPT},
        {"role": "assistant", "content": opening},
    ]
    eval_items: list[dict] = []

    turn_no = 0
    for _ in range(MAX_TURNS):
        customer = sim.next_utterance()
        if customer is None:
            break
        turn_no += 1
        turns.append({"role": "user", "text": customer, "tools": []})
        query_messages.append({"role": "user", "content": customer})
        query_snapshot = [dict(m) for m in query_messages]

        tool_start = len(conv.tool_interactions)
        asha = conv.respond_to(customer)
        turn_tools = conv.tool_interactions[tool_start:]
        turns.append({"role": "assistant", "text": asha, "tools": turn_tools})
        query_messages.append({"role": "assistant", "content": asha})

        eval_items.append({
            "query": query_snapshot,
            "response": _response_trajectory(turn_tools, asha),
            "tool_calls": _tool_calls_for_eval(turn_tools),
            "tool_definitions": TOOL_DEFINITIONS_FOR_EVAL,
            "conversation_id": scenario["id"],
            "turn_number": turn_no,
        })

        sim.observe_agent(asha)
        if conv.terminal:
            break

    return {
        "scenario": scenario["id"],
        "customer_id": scenario["customer_id"],
        "expected_outcome": scenario["expected_outcome"],
        "actual_outcome": conv.terminal_outcome,
        "terminal": conv.terminal,
        "turns": turns,
        "tool_interactions": conv.tool_interactions,
        "eval_items": eval_items,
    }


def _write_transcript(results: list[dict], path: Path) -> None:
    lines = [f"# Text-driven Asha eval — {len(results)} calls\n"]
    for r in results:
        lines.append(f"\n══════════════ {r['scenario']}  (expected={r['expected_outcome']} → actual={r['actual_outcome']}) ══════════════")
        for t in r["turns"]:
            who = "ASHA " if t["role"] == "assistant" else "CUST "
            lines.append(f"{who}: {t['text']}")
            for tool in t["tools"]:
                res = tool["result"]
                status = res.get("status") or res.get("next_state") or res.get("outcome") or list(res)[:1]
                lines.append(f"        ↳ tool {tool['name']}({json.dumps(tool['arguments'], ensure_ascii=False)}) → {status}")
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="Run only the first N scenarios (0 = all).")
    ap.add_argument("--only", default="", help="Comma-separated scenario ids to run.")
    args = ap.parse_args()

    scenarios = SCENARIOS
    if args.only:
        want = {s.strip() for s in args.only.split(",")}
        scenarios = [s for s in scenarios if s["id"] in want]
    elif args.limit:
        scenarios = scenarios[: args.limit]

    DATASETS.mkdir(parents=True, exist_ok=True)
    results = []
    all_items = []
    for s in scenarios:
        print(f"▶ {s['id']} (customer={s['customer_id']}) ...", flush=True)
        try:
            r = run_scenario(s)
        except Exception as exc:  # noqa: BLE001
            print(f"   ! ERROR in {s['id']}: {exc}", flush=True)
            continue
        results.append(r)
        all_items.extend(r["eval_items"])
        ok = "✓" if (r["actual_outcome"] == r["expected_outcome"] or r["expected_outcome"] == "ANY") else "✗"
        print(f"   {ok} expected={r['expected_outcome']} actual={r['actual_outcome']} "
              f"turns={sum(1 for t in r['turns'] if t['role']=='user')} tools={len(r['tool_interactions'])}", flush=True)

    with (DATASETS / "text_eval.jsonl").open("w", encoding="utf-8") as f:
        for it in all_items:
            f.write(json.dumps(it, ensure_ascii=False) + "\n")
    (DATASETS / "text_run_summary.json").write_text(
        json.dumps([{k: r[k] for k in ("scenario", "expected_outcome", "actual_outcome", "terminal")} for r in results],
                   ensure_ascii=False, indent=2), encoding="utf-8")
    _write_transcript(results, DATASETS / "text_transcripts.txt")

    matched = sum(1 for r in results if r["actual_outcome"] == r["expected_outcome"] or r["expected_outcome"] == "ANY")
    print(f"\nWrote {len(all_items)} eval turns from {len(results)} calls → {DATASETS/'text_eval.jsonl'}")
    print(f"Outcome match: {matched}/{len(results)}")


if __name__ == "__main__":
    main()
