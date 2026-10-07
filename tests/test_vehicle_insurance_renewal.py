from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import unittest
from datetime import date, timedelta
from contextlib import closing
from pathlib import Path


SERVER_DIR = Path(__file__).resolve().parents[1] / "server"
sys.path.insert(0, str(SERVER_DIR))

import crm_tools  # noqa: E402  # pyright: ignore[reportMissingImports]
from app import build_session_config  # noqa: E402  # pyright: ignore[reportMissingImports]
from campaigns import CAMPAIGN_REGISTRY  # noqa: E402  # pyright: ignore[reportMissingImports]
from crm_tools import (  # noqa: E402  # pyright: ignore[reportMissingImports]
    get_vehicle_insurance_information,
    get_vehicle_insurance_policy,
    get_vehicle_renewal_quote,
    record_vehicle_renewal_intent,
)
from playbooks import PLAYBOOK_REGISTRY  # noqa: E402  # pyright: ignore[reportMissingImports]
from seed_db import create_schema  # noqa: E402  # pyright: ignore[reportMissingImports]


class VehicleInsuranceRenewalTestCase(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.previous_db_path = crm_tools.DB_PATH
        self.db_path = Path(self.temp_dir.name) / "crm.db"
        crm_tools.DB_PATH = self.db_path

        with closing(sqlite3.connect(self.db_path)) as db:
            create_schema(db.cursor())
            db.execute(
                "INSERT INTO customers (id,name,segment,city) VALUES (?,?,?,?)",
                ("customer-1", "Test Customer", "Gold", "Mumbai"),
            )
            db.execute(
                "INSERT INTO vehicle_insurance_policies "
                "(customer_id,policy_number,vehicle_make_model,registration_number,"
                "registration_year,engine_cc,fuel_type,coverage_type,policy_start_date,"
                "expiry_date,third_party_valid_until,idv,renewal_idv,renewal_idv_basis,"
                "pricing_zone,last_total_premium,ncb_pct,claims_last_year,previous_od_rate,"
                "previous_own_damage_base,previous_ncb_discount,previous_tp_premium,"
                "previous_add_ons_breakdown_json,previous_tax,renewal_od_rate,"
                "renewal_tp_premium,rate_card_version,compulsory_deductible,"
                "voluntary_deductible,add_ons_json,renewal_status) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    "customer-1", "CVI-TEST-123456", "Test Hatchback", "MH-01-AB-9876",
                    2022, 1197, "Petrol", "comprehensive", "2098-10-01", "2099-09-30",
                    "2099-09-30", 500000, 450000,
                    "Age-based depreciation from the current policy IDV", "Mumbai Zone A",
                    14710, 25, 0, 0.018, 9000, 2250, 3416,
                    json.dumps({"zero_depreciation": 1900, "roadside_assistance": 400}),
                    2244, 0.018, 3416, "TEST-MOTOR-1", 1000, 0,
                    json.dumps(["zero_depreciation", "roadside_assistance"]), "Due Soon",
                ),
            )
            db.commit()

    def tearDown(self):
        crm_tools.DB_PATH = self.previous_db_path
        self.temp_dir.cleanup()

    def test_campaign_and_playbook_are_registered_with_only_allowed_tools(self):
        campaign = CAMPAIGN_REGISTRY["vehicle_insurance_renewal"]
        self.assertEqual(campaign["agent_name"], "Nisha")
        self.assertEqual(campaign["audience"], "vehicle_policy_expiring")
        self.assertIn("क्या मैं {first_name} से बात कर रही हूँ?", campaign["opening_scripts"]["hindi"])
        self.assertIn("vehicle_insurance_renewal", PLAYBOOK_REGISTRY)

        config = build_session_config("vehicle_insurance_renewal", "Test Customer")
        names = {tool["name"] for tool in config["session"]["tools"]}
        self.assertEqual(
            names,
            {
                "get_customer_profile",
                "get_vehicle_insurance_policy",
                "get_vehicle_insurance_information",
                "get_vehicle_renewal_quote",
                "record_vehicle_renewal_intent",
                "send_payment_link",
                "play_hold_music",
                "end_call",
                "escalate_to_human",
            },
        )

    def test_policy_lookup_masks_sensitive_references(self):
        policy = get_vehicle_insurance_policy("customer-1")
        self.assertEqual(policy["status"], "FOUND")
        self.assertEqual(policy["policy_reference"], "…123456")
        self.assertEqual(policy["registration_reference"], "…9876")
        self.assertNotIn("CVI-TEST", json.dumps(policy))
        self.assertNotIn("MH-01-AB", json.dumps(policy))

    def test_quote_is_grounded_and_third_party_cannot_take_own_damage_addons(self):
        comprehensive = get_vehicle_renewal_quote(
            "customer-1", "comprehensive", ["roadside_assistance"]
        )
        third_party = get_vehicle_renewal_quote("customer-1", "third_party", [])
        invalid = get_vehicle_renewal_quote(
            "customer-1", "third_party", ["zero_depreciation"]
        )

        self.assertEqual(comprehensive["status"], "QUOTED")
        self.assertEqual(comprehensive["quote_status"], "INDICATIVE")
        comparison = comprehensive["premium_comparison"]
        self.assertEqual(
            comparison["difference_amount"],
            comprehensive["total_premium"] - 14710,
        )
        self.assertEqual(
            comparison["explanation_status"],
            "AUDITABLE_COMPONENT_BREAKDOWN",
        )
        self.assertEqual(comprehensive["ncb_comparison"]["renewal_ncb_pct"], 35)
        previous = comparison["previous_breakdown"]
        self.assertEqual(
            previous["net_own_damage"]
            + previous["third_party"]
            + sum(previous["add_ons"].values())
            + previous["tax"],
            previous["total"] - previous["rounding_adjustment"],
        )
        renewal = comparison["renewal_breakdown"]
        self.assertEqual(
            renewal["net_own_damage"]
            + renewal["third_party"]
            + sum(renewal["add_ons"].values())
            + renewal["tax"]
            + renewal["rounding_adjustment"],
            renewal["total"],
        )
        self.assertGreater(comprehensive["total_premium"], third_party["total_premium"])
        self.assertEqual(comprehensive["ncb_pct_applied_to_own_damage"], 35)
        self.assertEqual(third_party["ncb_pct_applied_to_own_damage"], 0)
        self.assertIn("error", invalid)

    def test_recorded_claim_resets_renewal_ncb(self):
        with closing(sqlite3.connect(self.db_path)) as db:
            db.execute(
                "UPDATE vehicle_insurance_policies SET claims_last_year = 1 "
                "WHERE customer_id = ?",
                ("customer-1",),
            )
            db.commit()
        quote = get_vehicle_renewal_quote("customer-1", "comprehensive", [])
        self.assertEqual(quote["ncb_comparison"]["renewal_ncb_pct"], 0)
        self.assertEqual(quote["ncb_discount_amount"], 0)

    def test_past_payment_date_is_rejected_without_persisting_intent(self):
        result = record_vehicle_renewal_intent(
            "customer-1",
            "comprehensive",
            [],
            (date.today() - timedelta(days=1)).isoformat(),
            "payment_link",
        )
        self.assertIn("past", result["error"])
        with closing(sqlite3.connect(self.db_path)) as db:
            count = db.execute(
                "SELECT COUNT(*) FROM vehicle_insurance_renewal_intents"
            ).fetchone()[0]
        self.assertEqual(count, 0)

    def test_recorded_intent_recalculates_and_persists_quote_amount(self):
        quote = get_vehicle_renewal_quote(
            "customer-1", "comprehensive", ["roadside_assistance"]
        )
        result = record_vehicle_renewal_intent(
            "customer-1",
            "comprehensive",
            ["roadside_assistance"],
            "2099-09-15",
            "payment_link",
        )
        self.assertEqual(result["status"], "RECORDED")
        self.assertEqual(result["premium_amount"], quote["total_premium"])
        self.assertEqual(result["policy_status"], "NOT_ISSUED")

        with closing(sqlite3.connect(self.db_path)) as db:
            stored = db.execute(
                "SELECT premium_amount,status FROM vehicle_insurance_renewal_intents"
            ).fetchone()
        self.assertEqual(stored, (quote["total_premium"], "Intent Recorded"))

    def test_faq_answers_material_renewal_questions(self):
        for topic in ("coverage", "ncb", "idv", "claims", "inspection", "payment"):
            result = get_vehicle_insurance_information(topic)
            self.assertEqual(result["status"], "FOUND")
            self.assertTrue(result["answer"])


if __name__ == "__main__":
    unittest.main()
