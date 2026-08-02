from __future__ import annotations

import asyncio
import datetime as dt
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import AsyncMock


SERVER_DIR = Path(__file__).resolve().parents[1] / "server"
sys.path.insert(0, str(SERVER_DIR))

from app import VoiceLiveSession, build_session_config  # noqa: E402  # pyright: ignore[reportMissingImports]
from campaigns import CAMPAIGN_REGISTRY  # noqa: E402  # pyright: ignore[reportMissingImports]
from playbooks import PLAYBOOK_REGISTRY  # noqa: E402  # pyright: ignore[reportMissingImports]
from savings_account_tools import (  # noqa: E402  # pyright: ignore[reportMissingImports]
    APPROVED_PRODUCT_SNAPSHOT,
    SAVINGS_CALL_LEASE_SECONDS,
    finalize_call,
    get_savings_runtime_context,
    initialize_savings_schema,
    mark_savings_opening_delivered,
    register_do_not_call,
    schedule_callback,
    start_savings_call,
    submit_step_result,
    touch_savings_call,
    validate_pin_code,
)


class SavingsWorkflowTestCase(unittest.TestCase):
    customer_id = "test-customer"
    application_id = "SA-TEST-001"

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.previous_db_path = os.environ.get("CRM_DB_PATH")
        self.db_path = Path(self.temp_dir.name) / "workflow.db"
        os.environ["CRM_DB_PATH"] = str(self.db_path)
        self.previous_demo_reset = os.environ.get("SAVINGS_DEMO_RESET")
        os.environ["SAVINGS_DEMO_RESET"] = "0"

        with closing(sqlite3.connect(self.db_path)) as db:
            db.execute("CREATE TABLE customers (id TEXT PRIMARY KEY, name TEXT NOT NULL)")
            db.execute(
                "INSERT INTO customers VALUES (?, ?)",
                (self.customer_id, "Test Customer"),
            )
            initialize_savings_schema(db)
            db.execute(
                "INSERT INTO savings_applications "
                "(application_id,customer_id,status,started_at,last_completed_step,"
                "selected_product,confirmed_product,snapshot_version,created_at,updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    self.application_id,
                    self.customer_id,
                    "INCOMPLETE",
                    "2026-08-01T10:00:00+05:30",
                    "mobile number verification",
                    None,
                    None,
                    APPROVED_PRODUCT_SNAPSHOT["version"],
                    "2026-08-01T10:00:00+05:30",
                    "2026-08-01T10:05:00+05:30",
                ),
            )
            db.execute(
                "INSERT INTO serviceable_pin_codes VALUES (?,?,?,?)",
                ("400050", "Mumbai", "Maharashtra", 1),
            )
            db.execute(
                "INSERT INTO serviceable_pin_codes VALUES (?,?,?,?)",
                ("560066", "Bengaluru", "Karnataka", 0),
            )
            db.commit()

    def tearDown(self):
        if self.previous_db_path is None:
            os.environ.pop("CRM_DB_PATH", None)
        else:
            os.environ["CRM_DB_PATH"] = self.previous_db_path
        if self.previous_demo_reset is None:
            os.environ.pop("SAVINGS_DEMO_RESET", None)
        else:
            os.environ["SAVINGS_DEMO_RESET"] = self.previous_demo_reset
        self.temp_dir.cleanup()

    def start_call(self, call_id: str = "CALL-1") -> str:
        context = start_savings_call(call_id, self.customer_id)
        self.assertEqual(context["call_state"], "OPENING")
        context = mark_savings_opening_delivered(call_id, self.customer_id)
        self.assertEqual(context["call_state"], "RECIPIENT_CONFIRMATION")
        return call_id

    def submit(
        self,
        call_id: str,
        state: str,
        result: str,
        value: str | None = None,
    ) -> dict:
        response = submit_step_result(
            call_id,
            self.customer_id,
            state,
            result,
            value,
        )
        self.assertEqual(response["status"], "ACCEPTED", response)
        return response

    def test_demo_reset_on_start_makes_used_customer_reusable(self):
        with closing(sqlite3.connect(self.db_path)) as db:
            db.execute(
                "UPDATE savings_applications SET status = 'READY_FOR_KYC' WHERE application_id = ?",
                (self.application_id,),
            )
            db.execute(
                "INSERT INTO do_not_call VALUES (?,?,?,?)",
                (self.customer_id, "prior demo", "old-call", "2026-08-01T10:00:00+05:30"),
            )
            db.commit()

        os.environ["SAVINGS_DEMO_RESET"] = "0"
        blocked = start_savings_call("demo-off", self.customer_id)
        self.assertEqual(blocked["error"], "CUSTOMER_SUPPRESSED")

        os.environ["SAVINGS_DEMO_RESET"] = "1"
        context = start_savings_call("demo-on", self.customer_id)
        os.environ["SAVINGS_DEMO_RESET"] = "0"
        self.assertNotIn("error", context)
        self.assertEqual(context["call_state"], "OPENING")
        with closing(sqlite3.connect(self.db_path)) as db:
            status = db.execute(
                "SELECT status FROM savings_applications WHERE application_id = ?",
                (self.application_id,),
            ).fetchone()[0]
            dnc = db.execute(
                "SELECT COUNT(*) FROM do_not_call WHERE customer_id = ?",
                (self.customer_id,),
            ).fetchone()[0]
        self.assertEqual(status, "INCOMPLETE")
        self.assertEqual(dnc, 0)

    def test_happy_path_reaches_ready_for_kyc_and_finalizes_once(self):
        call_id = self.start_call()
        self.submit(call_id, "RECIPIENT_CONFIRMATION", "CONFIRMED")
        self.submit(call_id, "AVAILABILITY", "AVAILABLE")
        self.submit(call_id, "PIN_CAPTURE", "CAPTURED", "400050")
        self.submit(call_id, "PIN_CONFIRMATION", "CONFIRMED")
        pin_result = validate_pin_code(call_id, self.customer_id, "400050")
        self.assertEqual(pin_result["status"], "SERVICEABLE")
        self.submit(call_id, "AGE_CHECK", "ELIGIBLE")
        self.submit(call_id, "RESIDENCY_CHECK", "RESIDENT")
        self.submit(call_id, "DOCUMENT_CHECK", "AVAILABLE")
        self.submit(call_id, "PRODUCT_SELECTION", "SELECTED", "INSTANT_SUPER")
        self.submit(call_id, "PRODUCT_CONFIRMATION", "CONFIRMED")
        self.submit(call_id, "KYC_PREPARATION", "PRESENTED")
        self.submit(call_id, "FINAL_QUESTION", "NO_MORE_QUESTIONS")

        context = get_savings_runtime_context(call_id, self.customer_id)
        self.assertTrue(context["ready_to_finalize"])
        self.assertEqual(context["confirmed_product"], "INSTANT_SUPER")

        final = finalize_call(
            call_id,
            self.customer_id,
            "HOT_LEAD",
            "Customer selected and confirmed Instant Super.",
            product="INSTANT_SUPER",
            response_style="HINGLISH",
        )
        self.assertEqual(final["status"], "FINALIZED")
        self.assertEqual(final["outcome"], "HOT_LEAD")

        replay = finalize_call(
            call_id,
            self.customer_id,
            "NOT_INTERESTED",
            "Conflicting retry must not replace the first disposition.",
        )
        self.assertTrue(replay["idempotent_replay"])
        self.assertEqual(replay["outcome"], "HOT_LEAD")

        with closing(sqlite3.connect(self.db_path)) as db:
            count = db.execute(
                "SELECT COUNT(*) FROM savings_dispositions WHERE call_id = ?",
                (call_id,),
            ).fetchone()[0]
            status = db.execute(
                "SELECT status FROM savings_applications WHERE application_id = ?",
                (self.application_id,),
            ).fetchone()[0]
        self.assertEqual(count, 1)
        self.assertEqual(status, "READY_FOR_KYC")

    def test_active_call_lease_blocks_live_call_and_reclaims_expired_orphan(self):
        first = start_savings_call("CALL-1", self.customer_id)
        self.assertNotIn("error", first)

        blocked = start_savings_call("CALL-2", self.customer_id)
        self.assertEqual(blocked["error"], "APPLICATION_ALREADY_IN_CALL")
        self.assertTrue(touch_savings_call("CALL-1", self.customer_id))

        expired_at = (
            dt.datetime.now(dt.timezone.utc)
            - dt.timedelta(seconds=SAVINGS_CALL_LEASE_SECONDS + 1)
        ).isoformat(timespec="seconds")
        with closing(sqlite3.connect(self.db_path)) as db:
            db.execute(
                "UPDATE savings_call_sessions SET updated_at = ? WHERE call_id = 'CALL-1'",
                (expired_at,),
            )
            db.commit()

        recovered = start_savings_call("CALL-2", self.customer_id)
        self.assertEqual(recovered["call_state"], "OPENING")
        with closing(sqlite3.connect(self.db_path)) as db:
            old_session = db.execute(
                "SELECT call_state, terminal_outcome FROM savings_call_sessions WHERE call_id = 'CALL-1'"
            ).fetchone()
            disposition = db.execute(
                "SELECT outcome FROM savings_dispositions WHERE call_id = 'CALL-1'"
            ).fetchone()
        self.assertEqual(old_session, ("TERMINAL", "SYSTEM_ERROR"))
        self.assertEqual(disposition, ("SYSTEM_ERROR",))

    def test_stale_transition_and_premature_hot_lead_are_rejected(self):
        call_id = self.start_call()
        self.submit(call_id, "RECIPIENT_CONFIRMATION", "CONFIRMED")
        self.submit(call_id, "AVAILABILITY", "AVAILABLE")

        stale = submit_step_result(
            call_id,
            self.customer_id,
            "RECIPIENT_CONFIRMATION",
            "CONFIRMED",
        )
        self.assertEqual(stale["status"], "REJECTED")
        self.assertEqual(stale["current_state"], "PIN_CAPTURE")

        premature = finalize_call(
            call_id,
            self.customer_id,
            "HOT_LEAD",
            "Attempted before product consent.",
            product="INSTANT_CLASSIC",
        )
        self.assertEqual(premature["status"], "REJECTED")

    def test_runtime_context_limits_results_and_pin_requires_confirmation(self):
        call_id = self.start_call()
        context = get_savings_runtime_context(call_id, self.customer_id)
        self.assertIn("हाँ बोलिए", context["next_action"])
        self.submit(call_id, "RECIPIENT_CONFIRMATION", "CONFIRMED")
        context = get_savings_runtime_context(call_id, self.customer_id)
        self.assertEqual(
            context["allowed_step_results"],
            ["AVAILABLE", "BUSY", "ALREADY_COMPLETED", "NOT_INTERESTED"],
        )
        self.assertIn("FinServe Instant savings-account application", context["next_action"])
        self.assertIn("Do not reuse the recipient-confirmation answer", context["next_action"])

        self.submit(call_id, "AVAILABILITY", "AVAILABLE")
        context = get_savings_runtime_context(call_id, self.customer_id)
        self.assertEqual(context["call_state"], "PIN_CAPTURE")
        self.assertEqual(context["allowed_step_results"], ["CAPTURED", "NOT_INTERESTED"])
        self.assertIn("sharing it on this call is optional", context["postal_pin_purpose"])
        self.assertIn("PIN code", context["next_action"])
        self.submit(call_id, "PIN_CAPTURE", "CAPTURED", "400050")
        context = get_savings_runtime_context(call_id, self.customer_id)
        self.assertEqual(context["captured_pin_readback"], "four, zero, zero, zero, five, zero")

        premature = validate_pin_code(call_id, self.customer_id, "400050")
        self.assertEqual(premature["status"], "CONFIRMATION_REQUIRED")
        self.assertEqual(
            get_savings_runtime_context(call_id, self.customer_id)["call_state"],
            "PIN_CONFIRMATION",
        )

    def test_not_serviceable_close_never_exposes_backend_wording(self):
        call_id = self.start_call()
        self.submit(call_id, "RECIPIENT_CONFIRMATION", "CONFIRMED")
        self.submit(call_id, "AVAILABILITY", "AVAILABLE")
        self.submit(call_id, "PIN_CAPTURE", "CAPTURED", "560066")
        self.submit(call_id, "PIN_CONFIRMATION", "CONFIRMED")
        self.assertEqual(
            validate_pin_code(call_id, self.customer_id, "560066")["status"],
            "NOT_SERVICEABLE",
        )
        final = finalize_call(
            call_id,
            self.customer_id,
            "NOT_ELIGIBLE_PIN",
            "The confirmed postal PIN is not serviceable.",
            response_style="HINGLISH",
        )
        close = final["customer_close"].lower()
        self.assertNotIn("backend", close)
        self.assertNotIn("outcome", close)
        self.assertIn("postal pin code", close)

    def test_dispatch_uses_server_state_and_blocks_second_transition(self):
        call_id = self.start_call()
        session = VoiceLiveSession(
            None,
            customer_id=self.customer_id,
            campaign_key="savings_account_completion",
        )
        session._call_id = call_id
        session._send_json = AsyncMock()
        session._send_to_browser = AsyncMock()
        session._safe_response_create = AsyncMock()

        asyncio.run(session._send_response_create())
        self.assertEqual(session._managed_expected_state, "RECIPIENT_CONFIRMATION")
        asyncio.run(
            session._handle_function_call({
                "call_id": "tool-1",
                "name": "submit_step_result",
                "arguments": json.dumps({
                    "current_state": "AGE_CHECK",
                    "result": "CONFIRMED",
                }),
            })
        )
        self.assertEqual(
            get_savings_runtime_context(call_id, self.customer_id)["call_state"],
            "AVAILABILITY",
        )

        asyncio.run(session._send_response_create())
        asyncio.run(
            session._handle_function_call({
                "call_id": "tool-2",
                "name": "submit_step_result",
                "arguments": json.dumps({"result": "AVAILABLE"}),
            })
        )
        self.assertEqual(
            get_savings_runtime_context(call_id, self.customer_id)["call_state"],
            "AVAILABILITY",
        )
        blocked_output = json.loads(session._send_json.await_args_list[-1].args[0]["item"]["output"])
        self.assertEqual(blocked_output["status"], "WAIT_FOR_NEW_CUSTOMER_INPUT")

        session._user_turn_count += 1
        asyncio.run(session._send_response_create())
        asyncio.run(
            session._handle_function_call({
                "call_id": "tool-3",
                "name": "submit_step_result",
                "arguments": json.dumps({"result": "AVAILABLE"}),
            })
        )
        asyncio.run(
            session._handle_function_call({
                "call_id": "tool-4",
                "name": "submit_step_result",
                "arguments": json.dumps({"result": "ELIGIBLE"}),
            })
        )
        self.assertEqual(
            get_savings_runtime_context(call_id, self.customer_id)["call_state"],
            "PIN_CAPTURE",
        )
        second_output = json.loads(session._send_json.await_args_list[-1].args[0]["item"]["output"])
        self.assertEqual(second_output["status"], "WAIT_FOR_NEW_CUSTOMER_INPUT")

    def test_pin_question_changes_no_state_and_refusal_can_finalize(self):
        call_id = self.start_call()
        self.submit(call_id, "RECIPIENT_CONFIRMATION", "CONFIRMED")
        self.submit(call_id, "AVAILABILITY", "AVAILABLE")

        session = VoiceLiveSession(
            None,
            customer_id=self.customer_id,
            campaign_key="savings_account_completion",
        )
        session._call_id = call_id
        session._user_turn_count = 1
        session._send_json = AsyncMock()
        session._send_to_browser = AsyncMock()
        session._safe_response_create = AsyncMock()
        asyncio.run(session._send_response_create())

        with closing(sqlite3.connect(self.db_path)) as db:
            events_before = db.execute(
                "SELECT COUNT(*) FROM savings_step_events WHERE call_id = ?",
                (call_id,),
            ).fetchone()[0]

        asyncio.run(
            session._handle_function_call({
                "call_id": "tool-question",
                "name": "submit_step_result",
                "arguments": json.dumps({"result": "HAS_QUESTION"}),
            })
        )
        question_output = json.loads(session._send_json.await_args_list[-1].args[0]["item"]["output"])
        self.assertEqual(question_output["status"], "NO_STATE_CHANGE")
        self.assertIn("postal PIN, not a banking PIN", question_output["recovery_action"])
        self.assertIn("sharing it on this call is optional", question_output["recovery_action"])
        self.assertEqual(
            get_savings_runtime_context(call_id, self.customer_id)["call_state"],
            "PIN_CAPTURE",
        )
        with closing(sqlite3.connect(self.db_path)) as db:
            events_after = db.execute(
                "SELECT COUNT(*) FROM savings_step_events WHERE call_id = ?",
                (call_id,),
            ).fetchone()[0]
        self.assertEqual(events_after, events_before)

        refusal = submit_step_result(
            call_id,
            self.customer_id,
            "PIN_CAPTURE",
            "NOT_INTERESTED",
        )
        self.assertEqual(refusal["status"], "ACCEPTED")
        final = finalize_call(
            call_id,
            self.customer_id,
            "NOT_INTERESTED",
            "Customer declined to share the optional postal PIN on this call.",
        )
        self.assertEqual(final["status"], "FINALIZED")

    def test_premature_pin_validation_is_recovered_as_capture(self):
        call_id = self.start_call()
        self.submit(call_id, "RECIPIENT_CONFIRMATION", "CONFIRMED")
        self.submit(call_id, "AVAILABILITY", "AVAILABLE")

        session = VoiceLiveSession(
            None,
            customer_id=self.customer_id,
            campaign_key="savings_account_completion",
        )
        session._call_id = call_id
        session._user_turn_count = 1
        session._send_json = AsyncMock()
        session._send_to_browser = AsyncMock()
        session._safe_response_create = AsyncMock()
        asyncio.run(session._send_response_create())
        asyncio.run(
            session._handle_function_call({
                "call_id": "tool-pin",
                "name": "validate_pin_code",
                "arguments": json.dumps({"pin_code": "400050"}),
            })
        )

        context = get_savings_runtime_context(call_id, self.customer_id)
        self.assertEqual(context["call_state"], "PIN_CONFIRMATION")
        self.assertEqual(context["captured_pin_readback"], "four, zero, zero, zero, five, zero")
        tool_output = json.loads(session._send_json.await_args_list[-1].args[0]["item"]["output"])
        self.assertEqual(tool_output["status"], "ACCEPTED")
        self.assertIn("captured only", tool_output["recovered_action"])

        with closing(sqlite3.connect(self.db_path)) as db:
            pin_status = db.execute(
                "SELECT pin_validation_status FROM savings_call_sessions WHERE call_id = ?",
                (call_id,),
            ).fetchone()[0]
        self.assertIsNone(pin_status)

    def test_wrong_number_requires_an_accepted_recipient_result(self):
        call_id = self.start_call()
        premature = finalize_call(
            call_id,
            self.customer_id,
            "WRONG_NUMBER",
            "No state evidence.",
        )
        self.assertEqual(premature["status"], "REJECTED")

        self.submit(call_id, "RECIPIENT_CONFIRMATION", "WRONG_NUMBER")
        final = finalize_call(
            call_id,
            self.customer_id,
            "WRONG_NUMBER",
            "The intended customer did not answer.",
        )
        self.assertEqual(final["status"], "FINALIZED")

    def test_callback_is_state_gated_and_bound_to_the_disposition(self):
        call_id = self.start_call()
        out_of_state = schedule_callback(
            call_id,
            self.customer_id,
            "someday",
            "later",
            "Customer is busy.",
        )
        self.assertEqual(out_of_state["status"], "ERROR")

        self.submit(call_id, "RECIPIENT_CONFIRMATION", "CONFIRMED")
        self.submit(call_id, "AVAILABILITY", "BUSY")
        callback_date = (dt.date.today() + dt.timedelta(days=2)).isoformat()
        callback = schedule_callback(
            call_id,
            self.customer_id,
            callback_date,
            "10:30 am",
            "Customer requested a later conversation.",
        )
        self.assertEqual(callback["status"], "SCHEDULED")

        final = finalize_call(
            call_id,
            self.customer_id,
            "CALLBACK_SCHEDULED",
            "Customer chose an exact callback slot.",
            callback_id=callback["callback_id"],
        )
        self.assertEqual(final["status"], "FINALIZED")

    def test_do_not_call_is_durable_and_blocks_a_new_call(self):
        call_id = self.start_call()
        applied = register_do_not_call(
            call_id,
            self.customer_id,
            "Customer explicitly requested no further calls.",
        )
        self.assertEqual(applied["status"], "APPLIED")
        final = finalize_call(
            call_id,
            self.customer_id,
            "DNC_REQUESTED",
            "Explicit suppression request.",
        )
        self.assertEqual(final["status"], "FINALIZED")

        with closing(sqlite3.connect(self.db_path)) as db:
            db.execute(
                "INSERT INTO savings_applications "
                "(application_id,customer_id,status,started_at,last_completed_step,"
                "selected_product,confirmed_product,snapshot_version,created_at,updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    "SA-TEST-002",
                    self.customer_id,
                    "INCOMPLETE",
                    "2026-08-02T10:00:00+05:30",
                    "basic details",
                    None,
                    None,
                    APPROVED_PRODUCT_SNAPSHOT["version"],
                    "2026-08-02T10:00:00+05:30",
                    "2026-08-02T10:00:00+05:30",
                ),
            )
            db.commit()
        blocked = start_savings_call("CALL-2", self.customer_id)
        self.assertEqual(blocked["error"], "CUSTOMER_SUPPRESSED")


class SavingsCampaignContractTestCase(unittest.TestCase):
    def test_campaign_tools_match_the_playbook_and_are_closed(self):
        campaign = CAMPAIGN_REGISTRY["savings_account_completion"]
        _, allowed_names = PLAYBOOK_REGISTRY["savings_account_completion"]
        tools = campaign["tools"]
        tool_names = {tool["name"] for tool in tools}

        self.assertEqual(len(tools), 8)
        self.assertEqual(tool_names, set(allowed_names))
        self.assertNotIn("end_call", tool_names)
        self.assertNotIn("escalate_to_human", tool_names)
        for tool in tools:
            parameters = tool["parameters"]
            self.assertFalse(parameters["additionalProperties"])
            self.assertNotIn("call_id", parameters["properties"])
            self.assertNotIn("customer_id", parameters["properties"])
        submit_schema = next(tool for tool in tools if tool["name"] == "submit_step_result")
        self.assertNotIn("current_state", submit_schema["parameters"]["properties"])

    def test_managed_session_disables_automatic_responses_and_generic_blocks(self):
        context = {
            "call_state": "OPENING",
            "next_action": "Deliver the approved opening only.",
            "approved_product_snapshot": {"version": "test"},
        }
        config = build_session_config(
            "savings_account_completion",
            customer_name="Test Customer",
            tone="aggressive",
            languages=["english"],
            runtime_context=context,
        )["session"]
        tool_names = {tool["name"] for tool in config["tools"]}

        self.assertFalse(config["turn_detection"]["create_response"])
        self.assertEqual(config["input_audio_transcription"]["language"], "hi-IN,en-IN")
        self.assertNotIn("end_call", tool_names)
        self.assertNotIn("escalate_to_human", tool_names)
        self.assertNotIn("CALL TONE — ASSERTIVE", config["instructions"])
        self.assertIn("AUTHORITATIVE RUNTIME CONTEXT", config["instructions"])
        normalized_instructions = " ".join(config["instructions"].split())
        self.assertIn("one state transition may be accepted per customer utterance", normalized_instructions)
        self.assertIn("A question or objection is not a step result", normalized_instructions)
        self.assertIn("sharing it is optional", normalized_instructions)


if __name__ == "__main__":
    unittest.main()