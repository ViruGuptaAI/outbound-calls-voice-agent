"""Backend-owned workflow for the incomplete savings-account campaign."""

from __future__ import annotations

import datetime as dt
import json
import os
import re
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any


DEFAULT_DB_PATH = Path(__file__).resolve().parent / "crm.db"
PRODUCT_CODES = ("INSTANT_CLASSIC", "INSTANT_SUPER")
_PIN_DIGIT_WORDS = {
    "0": "zero",
    "1": "one",
    "2": "two",
    "3": "three",
    "4": "four",
    "5": "five",
    "6": "six",
    "7": "seven",
    "8": "eight",
    "9": "nine",
}

APPROVED_PRODUCT_SNAPSHOT: dict[str, Any] = {
    "version": "2026-08-02.demo.1",
    "demo_only": True,
    "products": {
        "INSTANT_CLASSIC": {
            "display_name": "Instant Classic",
            "comparison": [
                "A zero-balance digital savings account for everyday banking.",
                "Includes a virtual debit card; a physical card is optional.",
            ],
            "confirmation_pitch": [
                "No opening deposit and no monthly average balance requirement.",
                "A physical debit card is optional and carries an annual fee.",
            ],
            "mandatory_disclosures": [
                "The minimum opening deposit is zero rupees.",
                "The monthly average balance requirement is zero rupees.",
                "The optional physical debit card costs one hundred ninety nine rupees per year plus applicable taxes.",
                "No cashback benefit is included with Instant Classic.",
            ],
            "kyc_preparation": [
                "Keep the original physical PAN card available.",
                "Keep Aadhaar details and the Aadhaar-linked mobile available.",
                "Never share an OTP, PIN, password, CVV, full PAN, or full Aadhaar number on this call.",
            ],
        },
        "INSTANT_SUPER": {
            "display_name": "Instant Super",
            "comparison": [
                "A benefits-led savings account with a monthly balance requirement.",
                "Includes a physical debit card and eligible debit-card cashback.",
            ],
            "confirmation_pitch": [
                "An opening deposit and monthly average balance of ten thousand rupees are required.",
                "The debit card carries an annual fee and eligible cashback is capped monthly.",
            ],
            "mandatory_disclosures": [
                "The minimum opening deposit is ten thousand rupees.",
                "The monthly average balance requirement is ten thousand rupees.",
                "The physical debit card costs four hundred ninety nine rupees per year plus applicable taxes.",
                "Eligible debit-card purchases earn two percent cashback, capped at five hundred rupees per month and credited within thirty days.",
            ],
            "kyc_preparation": [
                "Keep the original physical PAN card available.",
                "Keep Aadhaar details and the Aadhaar-linked mobile available.",
                "Never share an OTP, PIN, password, CVV, full PAN, or full Aadhaar number on this call.",
            ],
        },
    },
}


_NEXT_ACTION = {
    "OPENING": "Deliver the approved opening only.",
    "RECIPIENT_CONFIRMATION": (
        "The opening directly asked whether this is the named customer. Replies such as 'हाँ', 'हाँ बोलिए', "
        "'जी, बोलिए', or 'मैं ही Priya हूँ' are explicit confirmation: submit CONFIRMED. 'बोलिए' alone, "
        "hello, or an unrelated acknowledgement is not confirmation. Reveal no application details until accepted."
    ),
    "AVAILABILITY": (
        "Open this turn like a warm, real relationship manager who has just reviewed the customer's file. "
        "In one or two natural, conversational sentences, say your team noticed their FinServe Instant "
        "savings-account application — their account-opening process — was started but left midway, and "
        "warmly offer to help them complete it right now; then check whether this is a good time. Frame it "
        "as 'हमने देखा / हमारी team ने देखा' (we noticed), not as reciting that a form was created. Do not "
        "enumerate completed steps, do not recite the start date, do not say they need not redo anything, "
        "and do not read this verbatim. Do not reuse the recipient-confirmation answer for availability; "
        "wait for a new customer utterance that directly answers this question."
    ),
    "PIN_CAPTURE": (
        "Warmly ask the customer for their area PIN code so you can take the application forward — for "
        "example, 'application आगे बढ़ाने के लिए मुझे आपका area का PIN code चाहिए, बता दीजिए?'. Accept whatever "
        "they say, even all six digits together, and never make them repeat it one digit at a time. If they "
        "ask why or seem unsure, briefly reassure them using postal_pin_purpose, then wait. If they clearly "
        "refuse, submit NOT_INTERESTED."
    ),
    "PIN_CONFIRMATION": (
        "Read captured_pin_readback exactly and ask for confirmation. Never display the raw six-digit "
        "sequence or speak it as one number. After confirmation, submit CONFIRMED before calling validate_pin_code."
    ),
    "AGE_CHECK": "Ask whether the customer is at least eighteen years old.",
    "RESIDENCY_CHECK": "Ask whether the customer is an Indian resident.",
    "DOCUMENT_CHECK": (
        "Ask once whether the customer has their PAN and Aadhaar. A yes is AVAILABLE — do not "
        "interrogate physical-versus-digital, and never repeat the question once answered."
    ),
    "PRODUCT_SELECTION": (
        "Briefly compare only Instant Classic and Instant Super from the approved snapshot, then ask "
        "which one they want. Do not repeat the comparison once given. As soon as the customer clearly "
        "picks one (e.g. 'Classic', 'क्लासिक चलेगा'), submit SELECTED with that product and move on."
    ),
    "PRODUCT_CONFIRMATION": (
        "State the selected product's mandatory disclosures once, briefly, then ask the customer to "
        "confirm. If you already stated them on an earlier turn, do NOT repeat them. Any clear go-ahead — "
        "'हाँ', 'करो', 'कर दो', 'चलेगा', 'खोल दो', 'आगे बढ़ाओ', 'सहमत हूँ' — is CONFIRMED; submit it at once. "
        "Only a clear 'नहीं' or a switch to the other product is DECLINED. If the customer sounds impatient, "
        "take it as CONFIRMED and move on."
    ),
    "FINAL_QUESTION": (
        "Ask the one concrete question 'क्या आपका कोई और सवाल है?' (do you have any other question?) — "
        "never a vague 'क्या मैं आगे बढ़ाउँ?' or 'shall I proceed?'. If they ask what happens next or who "
        "completes the process, explain clearly and warmly: this call gets their application ready, and a "
        "member of our team will then call them shortly to complete the KYC and open the account, so they "
        "should keep their original PAN and Aadhaar handy. Do not repeat the KYC list. As soon as they have "
        "no further question — including 'नहीं', 'करो', 'कर दो', 'आगे बढ़ाओ', 'go ahead', or 'proceed' — submit "
        "NO_MORE_QUESTIONS and finalize confidently. Never keep re-asking whether to proceed or to close."
    ),
    "CALLBACK_CAPTURE": "Collect a callback date and time, then call schedule_callback.",
    "ESCALATION": "Create the required escalation and do not continue selling.",
    "TERMINAL": "Do not ask questions or perform another journey action.",
}

# Product confirmation lands directly on FINAL_QUESTION: the KYC preparation is presentation-only
# (no customer decision), so it is delivered once here as the entry action rather than living in a
# self-advancing state the model must submit PRESENTED to leave (which it kept forgetting, causing
# the disclosure to repeat every turn).
_AFTER_CONFIRM_ACTION = (
    "First, briefly deliver the approved KYC preparation ONCE: keep the original PAN and Aadhaar "
    "(with the Aadhaar-linked mobile) ready, and never share an OTP, PIN, password, CVV, full PAN "
    "or full Aadhaar on this call. Then, in the SAME turn, ask the single final question "
    "'क्या आपका कोई और सवाल है?' (do you have any other question?). Do not ask permission to proceed, "
    "never say 'क्या मैं आगे बढ़ाउँ?', do not ask again whether documents are available, and never "
    "repeat the KYC list on a later turn."
)

_ALLOWED_STEP_RESULTS = {
    "RECIPIENT_CONFIRMATION": ["CONFIRMED", "WRONG_NUMBER", "THIRD_PARTY"],
    "AVAILABILITY": ["AVAILABLE", "BUSY", "ALREADY_COMPLETED", "NOT_INTERESTED"],
    "PIN_CAPTURE": ["CAPTURED", "NOT_INTERESTED"],
    "PIN_CONFIRMATION": ["CONFIRMED", "CORRECTED"],
    "AGE_CHECK": ["ELIGIBLE", "UNDERAGE_CONFIRMED"],
    "RESIDENCY_CHECK": ["RESIDENT", "NON_RESIDENT_CONFIRMED"],
    "DOCUMENT_CHECK": ["AVAILABLE", "UNAVAILABLE_CONFIRMED"],
    "PRODUCT_SELECTION": ["SELECTED"],
    "PRODUCT_CONFIRMATION": ["CONFIRMED", "DECLINED"],
    "FINAL_QUESTION": ["NO_MORE_QUESTIONS", "HAS_QUESTION"],
}


def _db_path() -> Path:
    return Path(os.getenv("CRM_DB_PATH", str(DEFAULT_DB_PATH)))


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(_db_path()), timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


SAVINGS_CALL_LEASE_SECONDS = 15
SAVINGS_CALL_HEARTBEAT_SECONDS = 5


def _now_ist() -> str:
    from zoneinfo import ZoneInfo

    return dt.datetime.now(ZoneInfo("Asia/Kolkata")).isoformat(timespec="seconds")


def _lease_is_current(updated_at: str, now: str) -> bool:
    try:
        updated = dt.datetime.fromisoformat(updated_at)
        current = dt.datetime.fromisoformat(now)
    except (TypeError, ValueError):
        return False
    if updated.tzinfo is None:
        updated = updated.replace(tzinfo=current.tzinfo)
    return updated > current - dt.timedelta(seconds=SAVINGS_CALL_LEASE_SECONDS)


def _demo_reset_enabled() -> bool:
    """Demo builds restore a customer to a fresh state on each new call (default on)."""
    return os.getenv("SAVINGS_DEMO_RESET", "1").strip().lower() not in ("0", "false", "no", "off", "")


def initialize_savings_schema(conn: sqlite3.Connection | None = None) -> None:
    """Create the managed-workflow tables without modifying existing CRM tables."""
    owns_connection = conn is None
    db = conn or _connect()
    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS savings_applications (
            application_id          TEXT PRIMARY KEY,
            customer_id             TEXT NOT NULL REFERENCES customers(id),
            status                  TEXT NOT NULL DEFAULT 'INCOMPLETE',
            started_at              TEXT NOT NULL,
            last_completed_step     TEXT,
            selected_product        TEXT,
            confirmed_product       TEXT,
            snapshot_version        TEXT NOT NULL,
            created_at              TEXT NOT NULL,
            updated_at              TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS savings_call_sessions (
            call_id                 TEXT PRIMARY KEY,
            application_id          TEXT NOT NULL REFERENCES savings_applications(application_id),
            customer_id             TEXT NOT NULL REFERENCES customers(id),
            call_state              TEXT NOT NULL DEFAULT 'OPENING',
            opening_status          TEXT NOT NULL DEFAULT 'NOT_DELIVERED',
            recipient_status        TEXT NOT NULL DEFAULT 'UNCONFIRMED',
            response_style          TEXT NOT NULL DEFAULT 'HINDI',
            captured_pin            TEXT,
            pin_validation_status   TEXT,
            ready_to_finalize       INTEGER NOT NULL DEFAULT 0,
            callback_id             TEXT,
            escalation_id           TEXT,
            terminal_outcome        TEXT,
            terminal_reason         TEXT,
            created_at              TEXT NOT NULL,
            updated_at              TEXT NOT NULL,
            finalized_at            TEXT
        );

        CREATE TABLE IF NOT EXISTS savings_step_events (
            id                      INTEGER PRIMARY KEY AUTOINCREMENT,
            call_id                 TEXT NOT NULL REFERENCES savings_call_sessions(call_id),
            from_state              TEXT NOT NULL,
            submitted_result        TEXT NOT NULL,
            submitted_value         TEXT,
            to_state                TEXT NOT NULL,
            transition_status       TEXT NOT NULL,
            created_at              TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS savings_callbacks (
            callback_id             TEXT PRIMARY KEY,
            call_id                 TEXT NOT NULL REFERENCES savings_call_sessions(call_id),
            customer_id             TEXT NOT NULL REFERENCES customers(id),
            requested_expression    TEXT NOT NULL,
            scheduled_at_ist        TEXT,
            reason                  TEXT,
            status                  TEXT NOT NULL,
            created_at              TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS serviceable_pin_codes (
            pin_code                TEXT PRIMARY KEY,
            city                    TEXT,
            state                   TEXT,
            serviceable             INTEGER NOT NULL DEFAULT 1
        );

        CREATE TABLE IF NOT EXISTS do_not_call (
            customer_id             TEXT PRIMARY KEY REFERENCES customers(id),
            reason                  TEXT,
            source_call_id          TEXT,
            applied_at              TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS savings_dispositions (
            call_id                 TEXT PRIMARY KEY REFERENCES savings_call_sessions(call_id),
            application_id          TEXT NOT NULL,
            customer_id             TEXT NOT NULL,
            outcome                 TEXT NOT NULL,
            reason                  TEXT,
            product                 TEXT,
            callback_id             TEXT,
            escalation_id           TEXT,
            response_style          TEXT NOT NULL,
            created_at              TEXT NOT NULL
        );
        """
    )
    if owns_connection:
        db.commit()
        db.close()


def _row_dict(row: sqlite3.Row | None) -> dict[str, Any]:
    return dict(row) if row is not None else {}


def _spoken_date(value: str) -> str:
    try:
        parsed = dt.date.fromisoformat(value[:10])
    except (TypeError, ValueError):
        return ""
    return f"{parsed.day} {parsed.strftime('%B %Y')}"


def _pin_readback(value: str | None) -> str | None:
    if not value or len(value) != 6 or not value.isdigit():
        return None
    return ", ".join(_PIN_DIGIT_WORDS[digit] for digit in value)


def _load_session(db: sqlite3.Connection, call_id: str, customer_id: str) -> sqlite3.Row | None:
    return db.execute(
        "SELECT * FROM savings_call_sessions WHERE call_id = ? AND customer_id = ?",
        (call_id, customer_id),
    ).fetchone()


def _record_event(
    db: sqlite3.Connection,
    call_id: str,
    from_state: str,
    result: str,
    value: Any,
    to_state: str,
    status: str,
) -> None:
    db.execute(
        "INSERT INTO savings_step_events "
        "(call_id, from_state, submitted_result, submitted_value, to_state, transition_status, created_at) "
        "VALUES (?,?,?,?,?,?,?)",
        (call_id, from_state, result, json.dumps(value, ensure_ascii=False), to_state, status, _now_ist()),
    )


def _has_accepted_result(
    db: sqlite3.Connection,
    call_id: str,
    state: str,
    result: str,
) -> bool:
    return db.execute(
        "SELECT 1 FROM savings_step_events WHERE call_id = ? AND from_state = ? "
        "AND submitted_result = ? AND transition_status = 'ACCEPTED' LIMIT 1",
        (call_id, state, result),
    ).fetchone() is not None


def get_savings_runtime_context(call_id: str, customer_id: str) -> dict[str, Any]:
    """Return only customer-safe facts and the backend's current workflow directive."""
    with closing(_connect()) as db:
        row = db.execute(
            "SELECT s.*, a.started_at, a.last_completed_step, a.selected_product, "
            "a.confirmed_product, a.snapshot_version, a.status AS application_status, c.name "
            "FROM savings_call_sessions s "
            "JOIN savings_applications a ON a.application_id = s.application_id "
            "JOIN customers c ON c.id = s.customer_id "
            "WHERE s.call_id = ? AND s.customer_id = ?",
            (call_id, customer_id),
        ).fetchone()
    if row is None:
        return {"error": "SAVINGS_CALL_NOT_FOUND"}

    data = _row_dict(row)
    state = data["call_state"]
    first_name = (data.get("name") or "").split()[0]
    return {
        "current_datetime_ist": _now_ist(),
        "call_state": state,
        "opening_status": data["opening_status"],
        "recipient_status": data["recipient_status"],
        "customer_first_name": first_name,
        "application_started_date_spoken": _spoken_date(data["started_at"]),
        "last_completed_step_spoken": data.get("last_completed_step") or "बीच में",
        "last_completed_step_status": "COMPLETED_DO_NOT_REPEAT",
        "estimated_eligibility_time_spoken": "कुछ ही minutes",
        "captured_pin_readback": _pin_readback(data.get("captured_pin")),
        "postal_pin_purpose": (
            "The postal PIN is used only to check whether digital savings-account opening is available "
            "in the customer's area. It is not a banking PIN, cannot access an account or transaction, "
            "and sharing it on this call is optional. Without it, this assisted eligibility check cannot continue."
        ),
        "selected_product": data.get("selected_product"),
        "confirmed_product": data.get("confirmed_product"),
        "application_status": data["application_status"],
        "ready_to_finalize": bool(data["ready_to_finalize"]),
        "callback_id": data.get("callback_id"),
        "escalation_id": data.get("escalation_id"),
        "allowed_step_results": _ALLOWED_STEP_RESULTS.get(state, []),
        "next_action": _NEXT_ACTION.get(state, "Stop and create a technical escalation."),
        "approved_product_snapshot": APPROVED_PRODUCT_SNAPSHOT,
    }


def start_savings_call(call_id: str, customer_id: str) -> dict[str, Any]:
    """Bind one server-generated call ID to one eligible incomplete application."""
    now = _now_ist()
    with closing(_connect()) as db:
        initialize_savings_schema(db)
        db.execute("BEGIN IMMEDIATE")
        # Demo repeatability: unless a call is genuinely active right now, restore the
        # customer to a fresh incomplete state (and clear any prior do-not-call flag)
        # so the same profile can be demoed any number of times without reseeding.
        # Turn off with SAVINGS_DEMO_RESET=0 (tests set this to keep durable semantics).
        if _demo_reset_enabled():
            prior = db.execute(
                "SELECT application_id FROM savings_applications WHERE customer_id = ? "
                "ORDER BY started_at DESC LIMIT 1",
                (customer_id,),
            ).fetchone()
            if prior is not None:
                live = db.execute(
                    "SELECT updated_at FROM savings_call_sessions "
                    "WHERE application_id = ? AND terminal_outcome IS NULL "
                    "ORDER BY updated_at DESC LIMIT 1",
                    (prior["application_id"],),
                ).fetchone()
                if not (live is not None and _lease_is_current(live["updated_at"], now)):
                    db.execute("DELETE FROM do_not_call WHERE customer_id = ?", (customer_id,))
                    db.execute(
                        "UPDATE savings_applications SET status = 'INCOMPLETE', selected_product = NULL, "
                        "confirmed_product = NULL, updated_at = ? WHERE application_id = ?",
                        (now, prior["application_id"]),
                    )
        if db.execute("SELECT 1 FROM do_not_call WHERE customer_id = ?", (customer_id,)).fetchone():
            db.rollback()
            return {"error": "CUSTOMER_SUPPRESSED", "message": "Customer is on the do-not-call list."}

        existing = _load_session(db, call_id, customer_id)
        if existing is not None:
            db.commit()
            return get_savings_runtime_context(call_id, customer_id)

        application = db.execute(
            "SELECT * FROM savings_applications "
            "WHERE customer_id = ? AND status = 'INCOMPLETE' "
            "ORDER BY started_at DESC LIMIT 1",
            (customer_id,),
        ).fetchone()
        if application is None:
            db.rollback()
            return {"error": "NO_INCOMPLETE_APPLICATION", "message": "No incomplete savings application found."}

        active = db.execute(
            "SELECT call_id, application_id, customer_id, response_style, updated_at "
            "FROM savings_call_sessions "
            "WHERE application_id = ? AND terminal_outcome IS NULL LIMIT 1",
            (application["application_id"],),
        ).fetchone()
        if active is not None and _lease_is_current(active["updated_at"], now):
            db.rollback()
            return {"error": "APPLICATION_ALREADY_IN_CALL", "message": "This application already has an active call."}
        if active is not None:
            reason = "Managed call lease expired before workflow finalization."
            db.execute(
                "INSERT OR IGNORE INTO savings_dispositions "
                "(call_id, application_id, customer_id, outcome, reason, response_style, created_at) "
                "VALUES (?,?,?,?,?,?,?)",
                (
                    active["call_id"],
                    active["application_id"],
                    active["customer_id"],
                    "SYSTEM_ERROR",
                    reason,
                    active["response_style"],
                    now,
                ),
            )
            db.execute(
                "UPDATE savings_call_sessions SET call_state = 'TERMINAL', terminal_outcome = 'SYSTEM_ERROR', "
                "terminal_reason = ?, finalized_at = ?, updated_at = ? WHERE call_id = ?",
                (reason, now, now, active["call_id"]),
            )

        db.execute(
            "INSERT INTO savings_call_sessions "
            "(call_id, application_id, customer_id, created_at, updated_at) VALUES (?,?,?,?,?)",
            (call_id, application["application_id"], customer_id, now, now),
        )
        db.commit()
    return get_savings_runtime_context(call_id, customer_id)


def touch_savings_call(call_id: str, customer_id: str) -> bool:
    """Refresh a live managed call lease; return False after it has ended or disappeared."""
    with closing(_connect()) as db:
        cursor = db.execute(
            "UPDATE savings_call_sessions SET updated_at = ? "
            "WHERE call_id = ? AND customer_id = ? AND terminal_outcome IS NULL",
            (_now_ist(), call_id, customer_id),
        )
        db.commit()
        return cursor.rowcount == 1


def mark_savings_opening_delivered(call_id: str, customer_id: str) -> dict[str, Any]:
    """Advance the server-owned opening state only after the opening response completes."""
    with closing(_connect()) as db:
        db.execute("BEGIN IMMEDIATE")
        session = _load_session(db, call_id, customer_id)
        if session is None:
            db.rollback()
            return {"status": "REJECTED", "recovery_action": "End the call; no workflow session exists."}
        if session["call_state"] != "OPENING":
            db.commit()
            return get_savings_runtime_context(call_id, customer_id)

        now = _now_ist()
        db.execute(
            "UPDATE savings_call_sessions SET call_state = 'RECIPIENT_CONFIRMATION', "
            "opening_status = 'COMPLETE', updated_at = ? WHERE call_id = ?",
            (now, call_id),
        )
        _record_event(db, call_id, "OPENING", "OPENING_DELIVERED", None, "RECIPIENT_CONFIRMATION", "ACCEPTED")
        db.commit()
    return get_savings_runtime_context(call_id, customer_id)


def _normalise_result(result: str) -> str:
    return re.sub(r"[^A-Z0-9]+", "_", (result or "").strip().upper()).strip("_")


def _extract_product(value: Any) -> str:
    if isinstance(value, dict):
        candidate = value.get("product", "")
    else:
        candidate = value
    return str(candidate or "").strip().upper()


def _transition_for(state: str, result: str) -> tuple[str, str] | None:
    if result == "NOT_INTERESTED" and state not in ("OPENING", "TERMINAL"):
        return (state, "Call finalize_call with outcome NOT_INTERESTED.")

    transitions = {
        "RECIPIENT_CONFIRMATION": {
            "CONFIRMED": ("AVAILABILITY", _NEXT_ACTION["AVAILABILITY"]),
            "WRONG_NUMBER": ("RECIPIENT_CONFIRMATION", "Call finalize_call with outcome WRONG_NUMBER."),
            "THIRD_PARTY": ("RECIPIENT_CONFIRMATION", "Call finalize_call with outcome THIRD_PARTY."),
        },
        "AVAILABILITY": {
            "AVAILABLE": ("PIN_CAPTURE", _NEXT_ACTION["PIN_CAPTURE"]),
            "BUSY": ("CALLBACK_CAPTURE", _NEXT_ACTION["CALLBACK_CAPTURE"]),
            "ALREADY_COMPLETED": ("AVAILABILITY", "Call check_application_status."),
            "NOT_INTERESTED": ("AVAILABILITY", "Call finalize_call with outcome NOT_INTERESTED."),
        },
        "PIN_CAPTURE": {
            "CAPTURED": ("PIN_CONFIRMATION", _NEXT_ACTION["PIN_CONFIRMATION"]),
        },
        "PIN_CONFIRMATION": {
            "CONFIRMED": ("PIN_CONFIRMATION", "Call validate_pin_code with the confirmed captured PIN."),
            "CORRECTED": ("PIN_CAPTURE", _NEXT_ACTION["PIN_CAPTURE"]),
        },
        "AGE_CHECK": {
            "ELIGIBLE": ("RESIDENCY_CHECK", _NEXT_ACTION["RESIDENCY_CHECK"]),
            "UNDERAGE_CONFIRMED": ("AGE_CHECK", "Call finalize_call with outcome NOT_ELIGIBLE_MINOR."),
        },
        "RESIDENCY_CHECK": {
            "RESIDENT": ("DOCUMENT_CHECK", _NEXT_ACTION["DOCUMENT_CHECK"]),
            "NON_RESIDENT_CONFIRMED": ("RESIDENCY_CHECK", "Call finalize_call with outcome NOT_ELIGIBLE_NON_RESIDENT."),
        },
        "DOCUMENT_CHECK": {
            "AVAILABLE": ("PRODUCT_SELECTION", _NEXT_ACTION["PRODUCT_SELECTION"]),
            "UNAVAILABLE_CONFIRMED": ("DOCUMENT_CHECK", "Call finalize_call with outcome NOT_ELIGIBLE_DOCUMENTS."),
        },
        "PRODUCT_SELECTION": {
            "SELECTED": ("PRODUCT_CONFIRMATION", _NEXT_ACTION["PRODUCT_CONFIRMATION"]),
        },
        "PRODUCT_CONFIRMATION": {
            "CONFIRMED": ("FINAL_QUESTION", _AFTER_CONFIRM_ACTION),
            "DECLINED": ("PRODUCT_SELECTION", _NEXT_ACTION["PRODUCT_SELECTION"]),
        },
        "FINAL_QUESTION": {
            "NO_MORE_QUESTIONS": ("FINAL_QUESTION", "Call finalize_call with outcome HOT_LEAD and the confirmed product."),
            "HAS_QUESTION": ("FINAL_QUESTION", "Answer only from the approved snapshot or get_product_information, then ask the final question again."),
        },
    }
    return transitions.get(state, {}).get(result)


def submit_step_result(
    call_id: str,
    customer_id: str,
    current_state: str,
    result: str,
    value: Any = None,
) -> dict[str, Any]:
    """Compare-and-transition one journey step in a single SQLite transaction."""
    submitted_state = (current_state or "").strip().upper()
    submitted_result = _normalise_result(result)
    with closing(_connect()) as db:
        db.execute("BEGIN IMMEDIATE")
        session = _load_session(db, call_id, customer_id)
        if session is None:
            db.rollback()
            return {"status": "REJECTED", "recovery_action": "End the call; no workflow session exists."}

        stored_state = session["call_state"]
        if session["terminal_outcome"] or stored_state == "TERMINAL":
            db.rollback()
            return {"status": "REJECTED", "current_state": "TERMINAL", "recovery_action": "Do not continue; the call is finalized."}
        if submitted_state != stored_state:
            _record_event(db, call_id, stored_state, submitted_result, value, stored_state, "REJECTED")
            db.commit()
            return {
                "status": "REJECTED",
                "current_state": stored_state,
                "recovery_action": (
                    "Recover silently. Do not mention the backend, system, state, stage, rejection, or alignment. "
                    + _NEXT_ACTION.get(stored_state, "Stop and create a technical escalation.")
                ),
            }

        transition = _transition_for(stored_state, submitted_result)
        if transition is None:
            _record_event(db, call_id, stored_state, submitted_result, value, stored_state, "REJECTED")
            db.commit()
            return {
                "status": "REJECTED",
                "current_state": stored_state,
                "recovery_action": (
                    "Recover silently. Do not mention the backend, system, state, stage, rejection, or alignment. "
                    + _NEXT_ACTION.get(stored_state, "Stop safely.")
                ),
            }

        next_state, next_action = transition
        updates: dict[str, Any] = {"call_state": next_state, "updated_at": _now_ist()}
        application_updates: dict[str, Any] = {}

        if stored_state == "RECIPIENT_CONFIRMATION" and submitted_result == "CONFIRMED":
            updates["recipient_status"] = "CONFIRMED"
        elif stored_state == "PIN_CAPTURE" and submitted_result == "CAPTURED":
            pin_code = re.sub(r"\D", "", str(value or ""))
            if len(pin_code) != 6:
                _record_event(db, call_id, stored_state, submitted_result, value, stored_state, "REJECTED")
                db.commit()
                return {"status": "REJECTED", "current_state": stored_state, "recovery_action": "Ask for all six postal PIN digits again."}
            updates["captured_pin"] = pin_code
        elif stored_state == "PIN_CONFIRMATION" and submitted_result == "CORRECTED":
            updates["captured_pin"] = None
        elif stored_state == "PRODUCT_SELECTION" and submitted_result == "SELECTED":
            product = _extract_product(value)
            if product not in PRODUCT_CODES:
                _record_event(db, call_id, stored_state, submitted_result, value, stored_state, "REJECTED")
                db.commit()
                return {"status": "REJECTED", "current_state": stored_state, "recovery_action": "Ask the customer to choose only Instant Classic or Instant Super."}
            application_updates["selected_product"] = product
        elif stored_state == "PRODUCT_CONFIRMATION" and submitted_result == "CONFIRMED":
            application = db.execute(
                "SELECT selected_product FROM savings_applications WHERE application_id = ?",
                (session["application_id"],),
            ).fetchone()
            if application is None or application["selected_product"] not in PRODUCT_CODES:
                db.rollback()
                return {"status": "REJECTED", "current_state": stored_state, "recovery_action": "Return to product selection; no valid product is stored."}
            application_updates["confirmed_product"] = application["selected_product"]
        elif stored_state == "FINAL_QUESTION" and submitted_result == "NO_MORE_QUESTIONS":
            updates["ready_to_finalize"] = 1

        set_clause = ", ".join(f"{name} = ?" for name in updates)
        db.execute(
            f"UPDATE savings_call_sessions SET {set_clause} WHERE call_id = ?",
            (*updates.values(), call_id),
        )
        if application_updates:
            application_updates["updated_at"] = _now_ist()
            app_set_clause = ", ".join(f"{name} = ?" for name in application_updates)
            db.execute(
                f"UPDATE savings_applications SET {app_set_clause} WHERE application_id = ?",
                (*application_updates.values(), session["application_id"]),
            )

        _record_event(db, call_id, stored_state, submitted_result, value, next_state, "ACCEPTED")
        db.commit()

    context = get_savings_runtime_context(call_id, customer_id)
    return {
        "status": "ACCEPTED",
        "previous_state": stored_state,
        "next_state": context["call_state"],
        "next_action": next_action,
        "stored_value": value,
    }


def validate_pin_code(call_id: str, customer_id: str, pin_code: str) -> dict[str, Any]:
    """Validate the confirmed postal PIN and advance only when serviceable."""
    normalized = re.sub(r"\D", "", pin_code or "")
    with closing(_connect()) as db:
        db.execute("BEGIN IMMEDIATE")
        session = _load_session(db, call_id, customer_id)
        if session is None or session["terminal_outcome"]:
            db.rollback()
            return {"status": "ERROR", "next_action": "End the call; no active workflow exists."}
        if session["call_state"] != "PIN_CONFIRMATION":
            db.rollback()
            return {
                "status": "ERROR",
                "current_state": session["call_state"],
                "next_action": _NEXT_ACTION.get(session["call_state"], "Stop safely."),
            }
        if not _has_accepted_result(db, call_id, "PIN_CONFIRMATION", "CONFIRMED"):
            db.rollback()
            return {
                "status": "CONFIRMATION_REQUIRED",
                "current_state": "PIN_CONFIRMATION",
                "next_action": (
                    "First submit PIN_CONFIRMATION as CONFIRMED for the customer's explicit readback "
                    "confirmation. Do not mention this internal requirement."
                ),
            }
        if len(normalized) != 6 or normalized != session["captured_pin"]:
            db.execute(
                "UPDATE savings_call_sessions SET call_state = 'PIN_CAPTURE', captured_pin = NULL, "
                "pin_validation_status = 'INVALID_FORMAT', updated_at = ? WHERE call_id = ?",
                (_now_ist(), call_id),
            )
            _record_event(db, call_id, "PIN_CONFIRMATION", "PIN_INVALID", pin_code, "PIN_CAPTURE", "ACCEPTED")
            db.commit()
            return {
                "status": "INVALID_FORMAT",
                "next_state": "PIN_CAPTURE",
                "next_action": "Warmly ask the customer again for their six-digit area PIN code.",
            }

        pin = db.execute(
            "SELECT city, state, serviceable FROM serviceable_pin_codes WHERE pin_code = ?",
            (normalized,),
        ).fetchone()
        # Demo: any well-formed PIN is serviceable unless it is an explicit deny-list
        # entry (serviceable = 0), so a tester can use any realistic PIN and continue.
        if pin is not None and not pin["serviceable"]:
            db.execute(
                "UPDATE savings_call_sessions SET pin_validation_status = 'NOT_SERVICEABLE', "
                "updated_at = ? WHERE call_id = ?",
                (_now_ist(), call_id),
            )
            _record_event(db, call_id, "PIN_CONFIRMATION", "PIN_NOT_SERVICEABLE", normalized, "PIN_CONFIRMATION", "ACCEPTED")
            db.commit()
            return {
                "status": "NOT_SERVICEABLE",
                "next_state": "PIN_CONFIRMATION",
                "next_action": "Call finalize_call with outcome NOT_ELIGIBLE_PIN.",
            }

        db.execute(
            "UPDATE savings_call_sessions SET call_state = 'AGE_CHECK', "
            "pin_validation_status = 'SERVICEABLE', updated_at = ? WHERE call_id = ?",
            (_now_ist(), call_id),
        )
        _record_event(db, call_id, "PIN_CONFIRMATION", "PIN_SERVICEABLE", normalized, "AGE_CHECK", "ACCEPTED")
        db.commit()
        return {
            "status": "SERVICEABLE",
            "city": pin["city"] if pin else None,
            "state": pin["state"] if pin else None,
            "next_state": "AGE_CHECK",
            "next_action": _NEXT_ACTION["AGE_CHECK"],
        }


def get_product_information(product: str, topics: list[str] | None = None) -> dict[str, Any]:
    """Return approved, versioned facts for one of the two demo products."""
    product_code = (product or "").strip().upper()
    if product_code not in PRODUCT_CODES:
        return {"status": "NOT_FOUND", "available_products": list(PRODUCT_CODES)}

    facts = APPROVED_PRODUCT_SNAPSHOT["products"][product_code]
    requested = [str(topic).strip().lower() for topic in (topics or []) if str(topic).strip()]
    if not requested:
        selected_facts = facts
    else:
        selected_facts = {topic: facts[topic] for topic in requested if topic in facts}
        if not selected_facts:
            return {
                "status": "NOT_FOUND",
                "product": product_code,
                "available_topics": list(facts),
            }
    return {
        "status": "FOUND",
        "snapshot_version": APPROVED_PRODUCT_SNAPSHOT["version"],
        "demo_only": True,
        "product": product_code,
        "facts": selected_facts,
    }


def check_application_status(call_id: str, customer_id: str) -> dict[str, Any]:
    """Re-read the bound application instead of trusting a spoken status claim."""
    with closing(_connect()) as db:
        row = db.execute(
            "SELECT a.status FROM savings_call_sessions s "
            "JOIN savings_applications a ON a.application_id = s.application_id "
            "WHERE s.call_id = ? AND s.customer_id = ?",
            (call_id, customer_id),
        ).fetchone()
    if row is None:
        return {"status": "ERROR", "next_action": "Create an application-status escalation."}
    if row["status"] in ("COMPLETED", "READY_FOR_KYC"):
        return {"status": "COMPLETED", "next_action": "Call finalize_call with outcome ALREADY_COMPLETED."}
    if row["status"] == "INCOMPLETE":
        return {
            "status": "INCOMPLETE",
            "next_action": (
                "The previously named last_completed_step is already complete. If the customer has said "
                "they want to proceed now, immediately submit AVAILABILITY as AVAILABLE before speaking. "
                "Otherwise ask only whether help now or a callback is preferred. Never ask age, residency, "
                "or documents while AVAILABILITY remains current."
            ),
        }
    return {"status": "UNKNOWN", "next_action": "Offer an application-status escalation without challenging the customer."}


def _parse_callback_date(expression: str, today: dt.date) -> dt.date | None:
    text = (expression or "").strip().lower()
    if not text:
        return None
    if text in ("today", "आज"):
        return today
    if text in ("tomorrow", "कल"):
        return today + dt.timedelta(days=1)
    if text in ("day after tomorrow", "परसों"):
        return today + dt.timedelta(days=2)
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return dt.datetime.strptime(text, fmt).date()
        except ValueError:
            pass
    weekdays = {
        "monday": 0,
        "tuesday": 1,
        "wednesday": 2,
        "thursday": 3,
        "friday": 4,
        "saturday": 5,
        "sunday": 6,
    }
    if text in weekdays:
        delta = (weekdays[text] - today.weekday()) % 7 or 7
        return today + dt.timedelta(days=delta)
    return None


def _parse_callback_time(expression: str) -> dt.time | None:
    text = re.sub(r"\s+", " ", (expression or "").strip().lower())
    match = re.fullmatch(r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)?", text)
    if not match:
        return None
    hour = int(match.group(1))
    minute = int(match.group(2) or 0)
    meridiem = match.group(3)
    if minute > 59 or hour > (12 if meridiem else 23) or hour == 0 and meridiem:
        return None
    if meridiem == "pm" and hour != 12:
        hour += 12
    elif meridiem == "am" and hour == 12:
        hour = 0
    return dt.time(hour, minute)


def schedule_callback(
    call_id: str,
    customer_id: str,
    date_expression: str,
    time_expression: str,
    reason: str,
) -> dict[str, Any]:
    """Resolve and persist a callback in the demo human-callback queue."""
    from zoneinfo import ZoneInfo

    if not (date_expression or "").strip():
        return {"status": "NEED_DATE", "next_action": "Ask only for a callback date."}
    if not (time_expression or "").strip():
        return {"status": "NEED_TIME", "next_action": "Ask only for a callback time."}

    now = dt.datetime.now(ZoneInfo("Asia/Kolkata"))
    callback_date = _parse_callback_date(date_expression, now.date())
    callback_time = _parse_callback_time(time_expression)
    requested = f"{date_expression} {time_expression}".strip()
    if callback_date is None or callback_time is None:
        callback_id = f"CB-PREF-{dt.datetime.now().strftime('%Y%m%d%H%M%S%f')}"
        with closing(_connect()) as db:
            session = _load_session(db, call_id, customer_id)
            if session is None or session["terminal_outcome"]:
                return {"status": "ERROR", "next_action": "Create a callback-technical escalation."}
            if session["call_state"] != "CALLBACK_CAPTURE":
                return {
                    "status": "ERROR",
                    "current_state": session["call_state"],
                    "next_action": _NEXT_ACTION.get(session["call_state"], "Stop safely."),
                }
            db.execute(
                "INSERT INTO savings_callbacks VALUES (?,?,?,?,?,?,?,?)",
                (callback_id, call_id, customer_id, requested, None, reason, "PREFERENCE_RECORDED", _now_ist()),
            )
            db.execute(
                "UPDATE savings_call_sessions SET callback_id = ?, updated_at = ? WHERE call_id = ?",
                (callback_id, _now_ist(), call_id),
            )
            db.commit()
        return {
            "status": "PREFERENCE_RECORDED",
            "callback_id": callback_id,
            "message": "The requested wording was recorded for a human callback team; no exact time was booked.",
            "next_action": "Call finalize_call with outcome CALLBACK_PREFERENCE_RECORDED.",
        }

    scheduled = dt.datetime.combine(callback_date, callback_time, tzinfo=ZoneInfo("Asia/Kolkata"))
    if scheduled <= now:
        return {"status": "PAST", "next_action": "Ask for a future date and time."}
    if callback_time < dt.time(9, 0) or callback_time > dt.time(18, 0):
        return {
            "status": "OUT_OF_WINDOW",
            "allowed_window": "9:00 AM to 6:00 PM IST",
            "next_action": "Ask once for a time between 9:00 AM and 6:00 PM IST.",
        }

    callback_id = f"CB-{scheduled.strftime('%Y%m%d%H%M')}-{call_id}"
    with closing(_connect()) as db:
        db.execute("BEGIN IMMEDIATE")
        session = _load_session(db, call_id, customer_id)
        if session is None or session["terminal_outcome"]:
            db.rollback()
            return {"status": "ERROR", "next_action": "Create a callback-technical escalation."}
        if session["call_state"] != "CALLBACK_CAPTURE":
            db.rollback()
            return {
                "status": "ERROR",
                "current_state": session["call_state"],
                "next_action": _NEXT_ACTION.get(session["call_state"], "Stop safely."),
            }
        conflict = db.execute(
            "SELECT 1 FROM savings_callbacks WHERE customer_id = ? AND scheduled_at_ist = ? "
            "AND status = 'SCHEDULED'",
            (customer_id, scheduled.isoformat(timespec="minutes")),
        ).fetchone()
        if conflict:
            db.rollback()
            return {"status": "ERROR", "next_action": "Ask for a different callback time."}
        db.execute(
            "INSERT INTO savings_callbacks VALUES (?,?,?,?,?,?,?,?)",
            (
                callback_id,
                call_id,
                customer_id,
                requested,
                scheduled.isoformat(timespec="minutes"),
                reason,
                "SCHEDULED",
                _now_ist(),
            ),
        )
        db.execute(
            "UPDATE savings_call_sessions SET callback_id = ?, updated_at = ? WHERE call_id = ?",
            (callback_id, _now_ist(), call_id),
        )
        db.commit()
    return {
        "status": "SCHEDULED",
        "callback_id": callback_id,
        "customer_facing_datetime": scheduled.strftime("%A, %d %B at %I:%M %p IST").replace(" 0", " "),
        "follow_up_mode": "demo_human_callback_queue",
        "next_action": "Confirm the returned date and time, then call finalize_call with outcome CALLBACK_SCHEDULED.",
    }


def register_do_not_call(call_id: str, customer_id: str, reason: str) -> dict[str, Any]:
    """Persist suppression before permitting the model to close the call."""
    with closing(_connect()) as db:
        db.execute("BEGIN IMMEDIATE")
        session = _load_session(db, call_id, customer_id)
        if session is None:
            db.rollback()
            return {"status": "ERROR", "next_action": "Create a high-priority DNC compliance escalation."}
        existing = db.execute("SELECT 1 FROM do_not_call WHERE customer_id = ?", (customer_id,)).fetchone()
        if not existing:
            db.execute(
                "INSERT INTO do_not_call VALUES (?,?,?,?)",
                (customer_id, reason, call_id, _now_ist()),
            )
        db.execute(
            "UPDATE savings_applications SET status = 'DNC_REQUESTED', updated_at = ? "
            "WHERE application_id = ?",
            (_now_ist(), session["application_id"]),
        )
        db.commit()
    return {
        "status": "ALREADY_APPLIED" if existing else "APPLIED",
        "next_action": "Call finalize_call with outcome DNC_REQUESTED. Do not resume the pitch.",
    }


def create_escalation(
    call_id: str,
    customer_id: str,
    category: str,
    summary: str,
    urgency: str = "medium",
    contact_preference: str = "registered phone",
    transcript: list[tuple[str, str]] | None = None,
) -> dict[str, Any]:
    """Create the existing email handoff and bind its reference to this call."""
    with closing(_connect()) as db:
        session = _load_session(db, call_id, customer_id)
        if session is None or session["terminal_outcome"]:
            return {"status": "ERROR", "fallback_message": "The request could not be recorded."}
    try:
        from crm_tools import send_escalation

        result = send_escalation(
            customer_id=customer_id,
            campaign="Savings Account Completion",
            agent="Asha",
            reason=f"{category}: {summary}",
            summary=summary,
            action_items=f"Follow up through {contact_preference}",
            priority=urgency,
            escalation_type="unresolved",
            transcript=transcript or [],
        )
    except Exception as exc:
        return {
            "status": "ERROR",
            "error": str(exc),
            "fallback_message": "The human follow-up could not be created. Please use the bank's approved contact route.",
        }

    escalation_id = result.get("reference")
    if not escalation_id:
        return {"status": "ERROR", "fallback_message": "The human follow-up could not be confirmed."}
    with closing(_connect()) as db:
        db.execute(
            "UPDATE savings_call_sessions SET call_state = 'ESCALATION', escalation_id = ?, "
            "updated_at = ? WHERE call_id = ?",
            (escalation_id, _now_ist(), call_id),
        )
        db.commit()
    queued = str(result.get("delivery", "")).startswith(("simulated", "smtp_error", "acs_error"))
    return {
        "status": "QUEUED" if queued else "CREATED",
        "escalation_id": escalation_id,
        "customer_message": "Your request has been recorded for human follow-up.",
        "follow_up_mode": "registered contact details",
        "next_action": "Call finalize_call with outcome ESCALATED and this escalation_id.",
    }


_ALLOWED_OUTCOMES = {
    "HOT_LEAD",
    "CALLBACK_SCHEDULED",
    "CALLBACK_PREFERENCE_RECORDED",
    "NOT_INTERESTED",
    "NOT_ELIGIBLE_PIN",
    "NOT_ELIGIBLE_MINOR",
    "NOT_ELIGIBLE_NON_RESIDENT",
    "NOT_ELIGIBLE_DOCUMENTS",
    "ALREADY_COMPLETED",
    "APPLICATION_STATUS_UNCONFIRMED",
    "DNC_REQUESTED",
    "WRONG_NUMBER",
    "THIRD_PARTY",
    "WRONG_INTENT",
    "ESCALATED",
    "SAFETY_EXIT",
    "SYSTEM_ERROR",
    "NO_RESPONSE",
    "IRATE_CUSTOMER",
}


def _customer_close(outcome: str, response_style: str) -> str:
    hindi = {
        "HOT_LEAD": "बहुत बढ़िया! मुझे आपकी सारी ज़रूरी जानकारी मिल गई है और आपकी पसंद सुरक्षित रूप से दर्ज हो गई है। अब हमारी team का एक member जल्द ही आपको KYC पूरा करने के लिए call करेगा। आपका समय देने और सभी सवालों के जवाब देने के लिए बहुत-बहुत धन्यवाद — आपका दिन शुभ हो!",
        "CALLBACK_SCHEDULED": "धन्यवाद। आपका callback तय समय के लिए दर्ज हो गया है।",
        "CALLBACK_PREFERENCE_RECORDED": "धन्यवाद। आपकी callback preference दर्ज हो गई है, लेकिन कोई निश्चित समय confirm नहीं हुआ है।",
        "NOT_INTERESTED": "ठीक है। आपकी इच्छा के अनुसार application assistance यहीं रोक दी गई है। धन्यवाद।",
        "NOT_ELIGIBLE_PIN": "धन्यवाद। फिलहाल आपके postal PIN code पर यह digital account उपलब्ध नहीं है, इसलिए application आगे नहीं बढ़ सकती।",
        "NOT_ELIGIBLE_MINOR": "मैं समझ सकती हूँ। यह savings account अभी केवल अठारह साल या उससे अधिक उम्र के customers के लिए ही available है, इसलिए फ़िलहाल मैं इसे आगे नहीं बढ़ा पाऊँगी। जैसे ही आप eligible हों, आपका स्वागत रहेगा। आपके समय के लिए धन्यवाद — आपका दिन शुभ हो!",
        "NOT_ELIGIBLE_NON_RESIDENT": "मैं समझ सकती हूँ। यह savings account फ़िलहाल केवल eligible Indian residents के लिए ही है, इसलिए मैं इसे अभी आगे नहीं बढ़ा पाऊँगी। भविष्य में eligibility बदलने पर आपका स्वागत रहेगा। आपके समय के लिए धन्यवाद — आपका दिन शुभ हो!",
        "NOT_ELIGIBLE_DOCUMENTS": "कोई बात नहीं। जब आपके पास PAN और Aadhaar तैयार हों, तब आप official process से यह application आसानी से दोबारा आगे बढ़ा सकते हैं। आपके समय के लिए धन्यवाद — आपका दिन शुभ हो!",
        "ALREADY_COMPLETED": "आपका application पहले ही complete है और किसी अन्य action की जरूरत नहीं है। धन्यवाद।",
        "APPLICATION_STATUS_UNCONFIRMED": "मैं अभी application status confirm नहीं कर पाई। कृपया official banking channel से status check करें। धन्यवाद।",
        "DNC_REQUESTED": "आपकी do-not-call request दर्ज हो गई है। धन्यवाद।",
        "WRONG_NUMBER": "क्षमा कीजिए, आगे कोई जानकारी साझा नहीं की जाएगी। धन्यवाद।",
        "THIRD_PARTY": "धन्यवाद। गोपनीयता के कारण मैं कोई application detail साझा नहीं करूँगी।",
        "ESCALATED": "मैंने आपका request हमारी team तक पहुँचा दिया है — वे जल्द ही आपसे follow-up करेंगे। आपके समय के लिए बहुत धन्यवाद।",
        "WRONG_INTENT": "क्षमा कीजिए, यह call आपके अनुरोध से संबंधित नहीं है। हम इसे यहीं समाप्त कर रहे हैं।",
        "SAFETY_EXIT": "हम banking discussion यहीं रोक रहे हैं। कृपया तुरंत local emergency support या किसी trusted person से संपर्क करें।",
        "SYSTEM_ERROR": "तकनीकी समस्या के कारण यह call आगे नहीं बढ़ सकती। कृपया बाद में official banking channel का उपयोग करें।",
        "NO_RESPONSE": "लगता है अभी बात नहीं हो पा रही है। हम यह call यहीं समाप्त कर रहे हैं।",
        "IRATE_CUSTOMER": "ठीक है। हम यह call यहीं समाप्त कर रहे हैं। धन्यवाद।",
    }
    hinglish = {
        "HOT_LEAD": "बहुत बढ़िया! मुझे आपकी सारी ज़रूरी जानकारी मिल गई है और आपकी choice securely record हो गई है। अब हमारी team का एक member जल्द ही आपको KYC complete करने के लिए call करेगा। आपका time देने और सभी questions का जवाब देने के लिए बहुत-बहुत धन्यवाद — have a great day!",
        "CALLBACK_SCHEDULED": "Thank you। आपका callback returned date and time के लिए record हो गया है।",
        "CALLBACK_PREFERENCE_RECORDED": "Thank you। आपकी callback preference record हुई है, but कोई exact time confirm नहीं हुआ है।",
        "NOT_INTERESTED": "ठीक है। आपकी preference के अनुसार application assistance यहीं stop कर दी गई है। Thank you।",
        "NOT_ELIGIBLE_PIN": "Thank you। फिलहाल आपके postal PIN code पर यह digital account available नहीं है, इसलिए application आगे नहीं बढ़ सकती।",
        "NOT_ELIGIBLE_MINOR": "मैं समझ सकती हूँ। यह savings account अभी सिर्फ़ eighteen years या उससे ऊपर के customers के लिए ही available है, इसलिए फ़िलहाल मैं इसे आगे नहीं बढ़ा पाऊँगी। जैसे ही आप eligible हों, आपका स्वागत रहेगा। Thank you so much आपके time के लिए — have a great day!",
        "NOT_ELIGIBLE_NON_RESIDENT": "मैं समझ सकती हूँ। यह savings account फ़िलहाल केवल eligible Indian residents के लिए ही है, इसलिए मैं इसे अभी आगे नहीं बढ़ा पाऊँगी। Eligibility बदलने पर आपका स्वागत रहेगा। Thank you so much आपके time के लिए — have a great day!",
        "NOT_ELIGIBLE_DOCUMENTS": "कोई बात नहीं। जब आपके पास PAN और Aadhaar ready हों, आप official process से यह application आसानी से दोबारा आगे बढ़ा सकते हैं। Thank you so much आपके time के लिए — have a great day!",
        "ALREADY_COMPLETED": "आपका application already complete है और किसी further action की जरूरत नहीं है। Thank you।",
        "APPLICATION_STATUS_UNCONFIRMED": "मैं अभी application status confirm नहीं कर पाई। Please official banking channel से status check करें। Thank you।",
        "DNC_REQUESTED": "आपकी do-not-call request record हो गई है। Thank you।",
        "WRONG_NUMBER": "Sorry, आगे कोई information share नहीं की जाएगी। Thank you।",
        "THIRD_PARTY": "Thank you। Privacy के कारण मैं कोई application detail share नहीं करूँगी।",
        "ESCALATED": "मैंने आपका request हमारी team तक forward कर दिया है — वे जल्द ही आपसे follow-up करेंगे। Thank you so much आपके time के लिए।",
        "WRONG_INTENT": "Sorry, यह call आपके request से related नहीं है। हम इसे यहीं end कर रहे हैं।",
        "SAFETY_EXIT": "हम banking discussion यहीं stop कर रहे हैं। Please local emergency support या किसी trusted person से तुरंत contact करें।",
        "SYSTEM_ERROR": "Technical issue के कारण यह call आगे नहीं बढ़ सकती। Please बाद में official banking channel use करें।",
        "NO_RESPONSE": "लगता है अभी बात नहीं हो पा रही है। हम यह call यहीं end कर रहे हैं।",
        "IRATE_CUSTOMER": "ठीक है। हम यह call यहीं end कर रहे हैं। Thank you।",
    }
    default_hindi = "धन्यवाद। यह call अब यहीं समाप्त होगी।"
    default_hinglish = "Thank you। यह call अब यहीं end होगी।"
    return (hinglish if response_style == "HINGLISH" else hindi).get(
        outcome,
        default_hinglish if response_style == "HINGLISH" else default_hindi,
    )


def finalize_call(
    call_id: str,
    customer_id: str,
    outcome: str,
    reason: str,
    product: str | None = None,
    callback_id: str | None = None,
    escalation_id: str | None = None,
    response_style: str = "HINDI",
) -> dict[str, Any]:
    """Write exactly one disposition and return the only approved spoken close."""
    normalized_outcome = (outcome or "").strip().upper()
    style = (response_style or "HINDI").strip().upper()
    product_code = (product or "").strip().upper() or None
    if normalized_outcome not in _ALLOWED_OUTCOMES or style not in ("HINDI", "HINGLISH"):
        return {"status": "REJECTED", "next_action": "Correct the outcome or response style and retry once."}

    with closing(_connect()) as db:
        db.execute("BEGIN IMMEDIATE")
        session = _load_session(db, call_id, customer_id)
        if session is None:
            db.rollback()
            return {"status": "REJECTED", "next_action": "No active workflow exists; end safely."}
        existing = db.execute("SELECT * FROM savings_dispositions WHERE call_id = ?", (call_id,)).fetchone()
        if existing is not None:
            db.commit()
            return {
                "status": "FINALIZED",
                "outcome": existing["outcome"],
                "customer_close": _customer_close(existing["outcome"], existing["response_style"]),
                "idempotent_replay": True,
            }

        application = db.execute(
            "SELECT * FROM savings_applications WHERE application_id = ?",
            (session["application_id"],),
        ).fetchone()
        rejection = None
        required_result = {
            "WRONG_NUMBER": ("RECIPIENT_CONFIRMATION", "WRONG_NUMBER"),
            "THIRD_PARTY": ("RECIPIENT_CONFIRMATION", "THIRD_PARTY"),
            "NOT_ELIGIBLE_MINOR": ("AGE_CHECK", "UNDERAGE_CONFIRMED"),
            "NOT_ELIGIBLE_NON_RESIDENT": ("RESIDENCY_CHECK", "NON_RESIDENT_CONFIRMED"),
            "NOT_ELIGIBLE_DOCUMENTS": ("DOCUMENT_CHECK", "UNAVAILABLE_CONFIRMED"),
        }.get(normalized_outcome)
        if required_result:
            required_state, required_value = required_result
            if session["call_state"] != required_state or not _has_accepted_result(
                db,
                call_id,
                required_state,
                required_value,
            ):
                rejection = f"An accepted {required_value} result from {required_state} is required."
        if normalized_outcome == "NOT_INTERESTED" and not db.execute(
            "SELECT 1 FROM savings_step_events WHERE call_id = ? AND submitted_result = 'NOT_INTERESTED' "
            "AND transition_status = 'ACCEPTED' LIMIT 1",
            (call_id,),
        ).fetchone():
            rejection = "An accepted NOT_INTERESTED result is required."
        if normalized_outcome == "HOT_LEAD":
            if not session["ready_to_finalize"] or application is None or not application["confirmed_product"]:
                rejection = "Complete the final-question state and confirm a product before HOT_LEAD."
            elif product_code != application["confirmed_product"]:
                rejection = "Use the backend-confirmed product."
        elif normalized_outcome == "NOT_ELIGIBLE_PIN" and (
            session["call_state"] != "PIN_CONFIRMATION"
            or session["pin_validation_status"] != "NOT_SERVICEABLE"
        ):
            rejection = "PIN eligibility has not returned NOT_SERVICEABLE in PIN_CONFIRMATION."
        elif normalized_outcome == "CALLBACK_SCHEDULED":
            bound_id = callback_id or session["callback_id"]
            callback = db.execute(
                "SELECT status FROM savings_callbacks WHERE callback_id = ? AND call_id = ?",
                (bound_id, call_id),
            ).fetchone()
            if callback is None or callback["status"] != "SCHEDULED":
                rejection = "A scheduled callback ID is required."
            elif session["call_state"] != "CALLBACK_CAPTURE":
                rejection = "The call is not in callback capture."
            callback_id = bound_id
        elif normalized_outcome == "CALLBACK_PREFERENCE_RECORDED":
            bound_id = callback_id or session["callback_id"]
            callback = db.execute(
                "SELECT status FROM savings_callbacks WHERE callback_id = ? AND call_id = ?",
                (bound_id, call_id),
            ).fetchone()
            if callback is None or callback["status"] != "PREFERENCE_RECORDED":
                rejection = "A recorded callback-preference ID is required."
            elif session["call_state"] != "CALLBACK_CAPTURE":
                rejection = "The call is not in callback capture."
            callback_id = bound_id
        elif normalized_outcome == "ESCALATED":
            bound_id = escalation_id or session["escalation_id"]
            if not bound_id or bound_id != session["escalation_id"]:
                rejection = "A backend-created escalation ID is required."
            elif session["call_state"] != "ESCALATION":
                rejection = "The call is not in escalation."
            escalation_id = bound_id
        elif normalized_outcome == "DNC_REQUESTED":
            if not db.execute("SELECT 1 FROM do_not_call WHERE customer_id = ?", (customer_id,)).fetchone():
                rejection = "Apply the DNC request before finalizing."
        elif normalized_outcome == "ALREADY_COMPLETED" and (
            application is None or application["status"] not in ("COMPLETED", "READY_FOR_KYC")
        ):
            rejection = "The backend application status is not complete."
        elif normalized_outcome == "APPLICATION_STATUS_UNCONFIRMED" and session["call_state"] != "AVAILABILITY":
            rejection = "Application-status uncertainty must be handled from AVAILABILITY."
        elif normalized_outcome == "WRONG_INTENT" and session["call_state"] not in (
            "RECIPIENT_CONFIRMATION",
            "AVAILABILITY",
        ):
            rejection = "Wrong intent may only close an early-stage call."

        if rejection:
            db.rollback()
            return {
                "status": "REJECTED",
                "current_state": session["call_state"],
                "next_action": rejection,
            }

        application_status = {
            "HOT_LEAD": "READY_FOR_KYC",
            "CALLBACK_SCHEDULED": "CALLBACK_SCHEDULED",
            "CALLBACK_PREFERENCE_RECORDED": "CALLBACK_PREFERENCE_RECORDED",
            "DNC_REQUESTED": "DNC_REQUESTED",
            "SYSTEM_ERROR": "INCOMPLETE",
            "NO_RESPONSE": "INCOMPLETE",
        }.get(normalized_outcome, "CLOSED")
        now = _now_ist()
        db.execute(
            "INSERT INTO savings_dispositions "
            "(call_id, application_id, customer_id, outcome, reason, product, callback_id, "
            "escalation_id, response_style, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                call_id,
                session["application_id"],
                customer_id,
                normalized_outcome,
                reason,
                product_code,
                callback_id,
                escalation_id,
                style,
                now,
            ),
        )
        db.execute(
            "UPDATE savings_call_sessions SET call_state = 'TERMINAL', terminal_outcome = ?, "
            "terminal_reason = ?, response_style = ?, finalized_at = ?, updated_at = ? WHERE call_id = ?",
            (normalized_outcome, reason, style, now, now, call_id),
        )
        db.execute(
            "UPDATE savings_applications SET status = ?, updated_at = ? WHERE application_id = ?",
            (application_status, now, session["application_id"]),
        )
        db.commit()

    return {
        "status": "FINALIZED",
        "outcome": normalized_outcome,
        "customer_close": _customer_close(normalized_outcome, style),
        "next_action": "Speak customer_close exactly and perform no further action.",
    }


SAVINGS_TOOL_FUNCTIONS = {
    "submit_step_result": submit_step_result,
    "validate_pin_code": validate_pin_code,
    "get_product_information": get_product_information,
    "check_application_status": check_application_status,
    "schedule_callback": schedule_callback,
    "register_do_not_call": register_do_not_call,
    "create_escalation": create_escalation,
    "finalize_call": finalize_call,
}