# ──────────────────────────────────────────────────────────────────────────────
# Voice Live function-call tool schemas — shared across outbound campaigns.
# Each campaign agent picks the subset it needs. The server filters these
# further per playbook so the model only ever sees the tools it should use.
# ──────────────────────────────────────────────────────────────────────────────

# ── Common ──────────────────────────────────────────────────────────────────
GET_CUSTOMER_PROFILE = {
    "type": "function",
    "name": "get_customer_profile",
    "description": "Look up the customer you are calling: name, segment, city, KYC status, relationship tenure. Call at the very start to personalise the conversation.",
    "parameters": {"type": "object", "properties": {}, "required": []},
}

PLAY_HOLD_MUSIC = {
    "type": "function",
    "name": "play_hold_music",
    "description": "Play hold music while you check with your manager for a special approval. IMPORTANT FLOW: In the SAME response where you call this tool, you MUST first say something like 'Let me place you on hold for a moment while I check what I can do' — announce the hold AND the reason. After the music finishes, your NEXT response MUST start by thanking them for waiting before delivering the answer. Duration in seconds (3-8).",
    "parameters": {
        "type": "object",
        "properties": {
            "duration": {"type": "integer", "description": "Hold music duration in seconds (3-8)."}
        },
        "required": ["duration"],
    },
}

END_CALL = {
    "type": "function",
    "name": "end_call",
    "description": (
        "Hang up and end the phone call. Call this ONLY after (1) the conversation is "
        "genuinely concluded or the customer's need is resolved, (2) you have ASKED the "
        "customer for permission to end — e.g. 'Is there anything else I can help you with, "
        "or shall I let you go?' — and (3) the customer has confirmed they have nothing else. "
        "In the SAME response where you call this tool you MUST first say a short, warm "
        "farewell (e.g. 'Thanks for your time, take care — goodbye.'); the call ends "
        "automatically once you finish speaking. NEVER call this while the customer still has "
        "a question, is mid-sentence, or has not agreed to end the call. NEVER hang up abruptly."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "reason": {
                "type": "string",
                "description": "Short reason the call is ending, e.g. 'customer confirmed nothing else', 'promise-to-pay recorded and confirmed'.",
            }
        },
        "required": [],
    },
}

ESCALATE_TO_HUMAN = {
    "type": "function",
    "name": "escalate_to_human",
    "description": (
        "Hand the call off to a HUMAN senior officer by e-mailing them a call summary, "
        "action items, and the transcript — then end the call. Use this when: (a) the "
        "customer is clearly frustrated/angry or negotiating unreasonably and you cannot "
        "satisfy them within your authority, (b) the customer explicitly asks to speak to a "
        "human / a senior person, (c) a human is genuinely required to proceed, OR (d) the "
        "call reached a successful resolution and a human must action the next steps "
        "(set escalation_type='resolved_handoff' for this case). BEFORE calling, briefly "
        "confirm with the customer — e.g. 'I'll escalate this to a senior officer who will "
        "call you back — is that okay?'. In the SAME response where you call this tool, tell "
        "the customer you have NOTED their request and are escalating to a human agent (for a "
        "resolved hand-off, thank them and say you've passed the details to the team for "
        "follow-up). The call ends automatically once you finish speaking. Do NOT promise any "
        "specific outcome the human has not approved."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "reason": {
                "type": "string",
                "description": (
                    "A short FREE-TEXT sentence explaining WHY you are handing off, written "
                    "for the human agent to read. This is NOT a category — do NOT put "
                    "'resolved_handoff' or 'unresolved' here (that goes in escalation_type). "
                    "Examples: 'Customer wants a full late-fee waiver beyond my authority.', "
                    "'Customer asked to speak to a senior officer.', 'Customer accepted the "
                    "loan offer and the application needs a human to complete disbursal.'"
                ),
            },
            "summary": {
                "type": "string",
                "description": "A concise 2–4 sentence summary of the call: the customer's issue, what was discussed and offered, and the current status.",
            },
            "action_items": {
                "type": "string",
                "description": "The next steps the human agent should take, as a short list separated by semicolons or newlines, e.g. 'Review full late-fee waiver; call the customer back within 24h; confirm the settlement plan'.",
            },
            "priority": {
                "type": "string",
                "enum": ["low", "medium", "high"],
                "description": "Urgency of the human callback.",
            },
            "escalation_type": {
                "type": "string",
                "enum": ["unresolved", "resolved_handoff"],
                "description": "'unresolved' = the customer still needs human help; 'resolved_handoff' = the call is resolved and a human just needs to action the next steps.",
            },
        },
        "required": ["reason", "summary"],
    },
}

# ── Loan / sales tools ──────────────────────────────────────────────────────
GET_PREAPPROVED_OFFERS = {
    "type": "function",
    "name": "get_preapproved_offers",
    "description": "Get the pre-approved loan and card offers the bank has already sanctioned for this customer. This is your primary hook for an outbound sales call — lead with the relevant offer.",
    "parameters": {"type": "object", "properties": {}, "required": []},
}

GET_ACTIVE_LOANS = {
    "type": "function",
    "name": "get_active_loans",
    "description": "Get the customer's existing active loans (type, outstanding, EMI, interest rate, tenure remaining). Useful for a balance-transfer or top-up pitch.",
    "parameters": {"type": "object", "properties": {}, "required": []},
}

GET_LOAN_PRODUCT_DETAILS = {
    "type": "function",
    "name": "get_loan_product_details",
    "description": "Get the bank rate card for a loan product: base rate, ceiling rate, amount range, tenure range, fees, LTV ratio, eligibility.",
    "parameters": {
        "type": "object",
        "properties": {
            "product_name": {
                "type": "string",
                "enum": ["Home Loan", "Personal Loan", "Car Loan", "Education Loan", "Loan Against FD", "Gold Loan", "Top-up Home Loan"],
                "description": "The loan product to look up.",
            }
        },
        "required": ["product_name"],
    },
}

GET_NEGOTIATION_TERMS = {
    "type": "function",
    "name": "get_negotiation_terms",
    "description": "Calculate the best possible interest rate for this customer on a specific loan product. Runs a risk/eligibility model (CIBIL, relationship value, payment history) and returns only the concessions the customer qualifies for. Use this BEFORE making any rate offer.",
    "parameters": {
        "type": "object",
        "properties": {
            "product_name": {
                "type": "string",
                "enum": ["Home Loan", "Personal Loan", "Car Loan", "Education Loan", "Loan Against FD", "Gold Loan", "Top-up Home Loan"],
                "description": "The loan product to negotiate on.",
            }
        },
        "required": ["product_name"],
    },
}

GET_ELIGIBILITY_ASSESSMENT = {
    "type": "function",
    "name": "get_eligibility_assessment",
    "description": "Run a full risk/eligibility assessment. Returns risk score (0-100), eligibility tier, and scoring factors (CIBIL, tenure, portfolio value, payment history, salary banking, product diversity). Use to justify or qualify an offer.",
    "parameters": {
        "type": "object",
        "properties": {
            "product_type": {
                "type": "string",
                "enum": ["rate_reduction", "new_loan", "card_upgrade", "limit_increase"],
                "description": "The type of action being assessed. Use 'new_loan' for a fresh loan sale.",
            }
        },
        "required": ["product_type"],
    },
}

CALCULATE_EMI = {
    "type": "function",
    "name": "calculate_emi",
    "description": "Calculate EMI for a loan. Amount in LAKHS, tenure in YEARS. Example: '50 lakh for 20 years' → principal_lakhs=50, tenure_years=20. '8 lakh for 7 years' → principal_lakhs=8, tenure_years=7.",
    "parameters": {
        "type": "object",
        "properties": {
            "principal_lakhs": {"type": "number", "description": "Loan amount in LAKHS. 10 lakh = 10, 50 lakh = 50, 1 crore = 100."},
            "annual_rate": {"type": "number", "description": "Annual interest rate as percentage (e.g. 8.9)."},
            "tenure_years": {"type": "integer", "description": "Loan tenure in YEARS (e.g. 5, 7, 20)."},
        },
        "required": ["principal_lakhs", "annual_rate", "tenure_years"],
    },
}

GET_COMPETITOR_RATES = {
    "type": "function",
    "name": "get_competitor_rates",
    "description": "Get current market benchmark rates from competitor banks (SBI, HDFC, ICICI, Axis, Kotak) for a loan product — for use when the customer compares offers.",
    "parameters": {
        "type": "object",
        "properties": {
            "product_name": {
                "type": "string",
                "enum": ["Home Loan", "Personal Loan", "Car Loan", "Education Loan"],
                "description": "The loan product to compare competitor rates for.",
            }
        },
        "required": ["product_name"],
    },
}

CHECK_CIBIL_SCORE = {
    "type": "function",
    "name": "check_cibil_score",
    "description": "Pull the customer's CIBIL credit score. Returns score, band, key factors, active accounts, and debt-to-income ratio.",
    "parameters": {"type": "object", "properties": {}, "required": []},
}

CHECK_RBI_REPO_RATE = {
    "type": "function",
    "name": "check_rbi_repo_rate",
    "description": "Get the current RBI repo rate and policy rates — useful for explaining how loan rates are benchmarked and whether rates may fall.",
    "parameters": {"type": "object", "properties": {}, "required": []},
}

ASSESS_COLLATERAL = {
    "type": "function",
    "name": "assess_collateral",
    "description": "Assess property collateral for a home loan. Takes property type, city, pin code, city tier (YOU determine this), and estimated market value. Returns applicable LTV ratio and maximum eligible loan amount.",
    "parameters": {
        "type": "object",
        "properties": {
            "property_type": {
                "type": "string",
                "enum": ["residential_apartment", "independent_house", "villa", "plot", "commercial", "under_construction"],
                "description": "Type of property. flat/apartment → residential_apartment, house/bungalow → independent_house, new project → under_construction.",
            },
            "city": {"type": "string", "description": "City where the property is located."},
            "pin_code": {"type": "string", "description": "6-digit Indian PIN code of the property."},
            "city_tier": {
                "type": "string",
                "enum": ["tier1", "tier2", "tier3"],
                "description": "City tier YOU determine. tier1 = metros (Mumbai, Delhi/NCR, Bengaluru, Hyderabad, Chennai, Kolkata, Pune, Ahmedabad). tier2 = other cities/towns. tier3 = rural.",
            },
            "estimated_value_lakhs": {"type": "number", "description": "Estimated market value in LAKHS. 50 lakh = 50, 1 crore = 100."},
        },
        "required": ["property_type", "city", "pin_code", "city_tier", "estimated_value_lakhs"],
    },
}

ASSESS_VEHICLE_FUNDING = {
    "type": "function",
    "name": "assess_vehicle_funding",
    "description": "Determine the MAXIMUM vehicle-loan amount the bank can fund, given the on-road price, whether the vehicle is new or used, and the fuel type. Returns the funding cap (LTV %) and the maximum eligible loan — the bank NEVER funds 100%, there is always a down payment. Call this BEFORE calculating any EMI, and cap the loan at the max_eligible_loan it returns.",
    "parameters": {
        "type": "object",
        "properties": {
            "on_road_price_lakhs": {"type": "number", "description": "On-road price of the vehicle in LAKHS. 5 lakh = 5, 12 lakh = 12."},
            "condition": {"type": "string", "enum": ["new", "used"], "description": "Whether the vehicle is new or used."},
            "fuel_type": {"type": "string", "enum": ["petrol", "diesel", "ev"], "description": "Fuel type of the vehicle. electric → ev."},
        },
        "required": ["on_road_price_lakhs", "condition", "fuel_type"],
    },
}

# ── Collections tools ─────────────────────────────────────────────────────────
GET_CARD_DUES = {
    "type": "function",
    "name": "get_card_dues",
    "description": "Get the customer's current credit-card overdue position: total outstanding, overdue amount, minimum due, days past due, late fee, penal interest, and last payment. Call this FIRST on a collections call.",
    "parameters": {"type": "object", "properties": {}, "required": []},
}

GET_PAYMENT_HISTORY = {
    "type": "function",
    "name": "get_payment_history",
    "description": "Get the customer's recent payment behaviour: last payment, delays in the last 12 months, CIBIL score, and monthly income. Helps you gauge intent-to-pay vs ability-to-pay.",
    "parameters": {"type": "object", "properties": {}, "required": []},
}

GET_SETTLEMENT_OPTIONS = {
    "type": "function",
    "name": "get_settlement_options",
    "description": "Get the recovery options you are AUTHORISED to offer, gated by the delinquency bucket: pay-in-full, minimum-due, EMI conversion, and one-time settlement with late-fee / penal-interest waivers. Call this BEFORE offering any waiver or EMI plan. Never offer a waiver that is not authorised here.",
    "parameters": {"type": "object", "properties": {}, "required": []},
}

RECORD_PAYMENT_COMMITMENT = {
    "type": "function",
    "name": "record_payment_commitment",
    "description": "Record a Promise-to-Pay (PTP): the amount the customer commits to pay and the date. Returns a reference number. Call ONLY after the customer verbally commits to a specific amount and date.",
    "parameters": {
        "type": "object",
        "properties": {
            "amount": {"type": "number", "description": "The amount the customer commits to pay, in rupees."},
            "promise_date": {"type": "string", "description": "The date they commit to pay by, e.g. '2026-06-25' or 'tomorrow'."},
            "method": {"type": "string", "description": "How they will pay, e.g. 'UPI', 'payment link', 'net banking'."},
        },
        "required": ["amount", "promise_date"],
    },
}

SEND_PAYMENT_LINK = {
    "type": "function",
    "name": "send_payment_link",
    "description": "Send a secure payment link / UPI collect request to the customer's registered mobile and email for a given amount. Use once the customer agrees to pay now or shortly.",
    "parameters": {
        "type": "object",
        "properties": {
            "amount": {"type": "number", "description": "The amount to collect, in rupees."}
        },
        "required": ["amount"],
    },
}

# ── Vehicle-loan collections tools ────────────────────────────────────────────
GET_LOAN_DUES = {
    "type": "function",
    "name": "get_loan_dues",
    "description": "Get the customer's current overdue position on their VEHICLE loan: loan id, vehicle, EMI amount, number of EMIs overdue, total overdue amount, days past due (DPD), late fee, penal interest, outstanding balance, delinquency bucket, and last payment. Call this FIRST on a vehicle-loan collections call.",
    "parameters": {"type": "object", "properties": {}, "required": []},
}

GET_LOAN_SETTLEMENT_OPTIONS = {
    "type": "function",
    "name": "get_loan_settlement_options",
    "description": "Get the recovery options you are AUTHORISED to offer on the overdue vehicle loan, gated by the delinquency bucket: clear all overdue EMIs, pay the oldest EMI to stop the account worsening, restructure / extend tenure to lower the EMI, and one-time regularisation with late-fee / penal-interest waivers. Call this BEFORE offering any waiver, restructure, or EMI plan. Never offer a waiver that is not authorised here.",
    "parameters": {"type": "object", "properties": {}, "required": []},
}

# ── Life insurance tools ──────────────────────────────────────────────────────
GET_INSURANCE_PLANS = {
    "type": "function",
    "name": "get_insurance_plans",
    "description": "Get the life insurance product catalogue: plan categories (protection/term, guaranteed savings, market-linked ULIP, child, retirement/pension) with their purpose, key benefits, and who they suit. Use this to match a plan to the customer's need AFTER you understand their situation. Pass a category to get one family, or omit it to get an overview of all.",
    "parameters": {
        "type": "object",
        "properties": {
            "category": {
                "type": "string",
                "enum": ["protection", "savings", "ulip", "child", "retirement"],
                "description": "Optional. Which plan family to detail. protection = pure term life cover; savings = guaranteed savings/endowment with maturity benefit; ulip = market-linked investment plan; child = child's education/future; retirement = pension/annuity. Omit for an overview of all families.",
            }
        },
        "required": [],
    },
}

GET_COVER_RECOMMENDATION = {
    "type": "function",
    "name": "get_cover_recommendation",
    "description": "Run a Human Life Value (HLV) assessment for the customer you are calling. Uses their income, age, and outstanding liabilities to recommend how much life cover (sum assured) their family actually needs, and the likely protection gap. Call this to make the protection conversation concrete and personal — never invent a cover figure yourself.",
    "parameters": {"type": "object", "properties": {}, "required": []},
}

CALCULATE_INSURANCE_PREMIUM = {
    "type": "function",
    "name": "calculate_insurance_premium",
    "description": "Calculate an INDICATIVE annual and monthly premium for a life insurance plan. Provide the sum assured (life cover) in LAKHS, the policy term in YEARS, and the plan type. The customer's age is taken automatically from their profile — do NOT ask for or invent it. The figure is indicative and subject to underwriting; always present it as such and never as a final/guaranteed quote.",
    "parameters": {
        "type": "object",
        "properties": {
            "cover_lakhs": {"type": "number", "description": "Sum assured / life cover in LAKHS. 50 lakh = 50, 1 crore = 100."},
            "term_years": {"type": "integer", "description": "Policy term in YEARS (e.g. 10, 20, 30)."},
            "plan_type": {
                "type": "string",
                "enum": ["term", "savings", "ulip", "child", "retirement"],
                "description": "term = pure protection; savings = guaranteed savings/endowment; ulip = market-linked; child = child plan; retirement = pension/annuity build-up.",
            },
        },
        "required": ["cover_lakhs", "term_years", "plan_type"],
    },
}

GET_PREMIUM_NEGOTIATION = {
    "type": "function",
    "name": "get_premium_negotiation",
    "description": "Get the AUTHORISED premium-concession band for a life plan: the base indicative premium, the staged concession ROUNDS you may offer (each tied to a real lever — annual payment mode, healthy/non-smoker, or a final underwriting-approved concession), and the FLOOR below which you must never quote. Call this BEFORE negotiating on price so every concession is grounded and bounded — never invent a discount or a lower premium. Concede round by round; use a hold before the final round; never go below the floor.",
    "parameters": {
        "type": "object",
        "properties": {
            "cover_lakhs": {"type": "number", "description": "Sum assured / life cover in LAKHS. 50 lakh = 50, 1 crore = 100."},
            "term_years": {"type": "integer", "description": "Policy term in YEARS (e.g. 10, 20, 30)."},
            "plan_type": {
                "type": "string",
                "enum": ["term", "savings", "ulip", "child", "retirement"],
                "description": "term = pure protection; savings = guaranteed savings/endowment; ulip = market-linked; child = child plan; retirement = pension/annuity build-up.",
            },
        },
        "required": ["cover_lakhs", "term_years", "plan_type"],
    },
}

# ── Life insurance premium recovery (persistency) tools ───────────────────────
GET_POLICY_DUES = {
    "type": "function",
    "name": "get_policy_dues",
    "description": "Get the current premium position on the customer's EXISTING life insurance policy: plan, sum assured, premium amount and frequency, premiums paid/overdue, days past due, whether it is still in the grace period or has LAPSED, the grace/revival dates, the amount payable now, and the benefits currently AT RISK (life cover + any accrued value). Call this FIRST on a premium-recovery call.",
    "parameters": {"type": "object", "properties": {}, "required": []},
}

GET_REVIVAL_OPTIONS = {
    "type": "function",
    "name": "get_revival_options",
    "description": "Get the options you are AUTHORISED to offer to bring an overdue life policy back on track, gated by whether it is in grace or lapsed: pay the pending premium now (in grace, no interest), revive a lapsed policy (arrears + interest, possibly a health declaration), switch premium frequency to a smaller instalment, set up auto-debit to avoid future misses, and — for savings plans with enough premiums paid — make it paid-up. Call this BEFORE proposing any option. This is persistency, NOT debt recovery — the customer's own cover and money are at stake, so guide, never pressure.",
    "parameters": {"type": "object", "properties": {}, "required": []},
}

# ── Managed savings-account completion tools ────────────────────────────────
SUBMIT_STEP_RESULT = {
    "type": "function",
    "name": "submit_step_result",
    "description": (
        "Submit one answer for the backend's current savings-application state. Use ONLY a result listed "
        "in the latest runtime allowed_step_results. A customer question or objection is not a result; "
        "answer it without this tool. Never ask the next journey question before ACCEPTED."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "result": {
                "type": "string",
                "description": (
                    "Must be listed in runtime allowed_step_results. HAS_QUESTION is valid only when "
                    "call_state is FINAL_QUESTION, never for an earlier customer question."
                ),
                "enum": [
                    "CONFIRMED", "WRONG_NUMBER", "THIRD_PARTY", "AVAILABLE", "BUSY",
                    "ALREADY_COMPLETED", "NOT_INTERESTED", "CAPTURED", "CORRECTED",
                    "ELIGIBLE", "UNDERAGE_CONFIRMED", "RESIDENT",
                    "NON_RESIDENT_CONFIRMED", "UNAVAILABLE_CONFIRMED", "SELECTED",
                    "DECLINED", "NO_MORE_QUESTIONS", "HAS_QUESTION",
                ],
            },
            "value": {"type": "string", "description": "Only the value required by this step, such as six PIN digits or the selected internal product code."},
        },
        "required": ["result"],
        "additionalProperties": False,
    },
}

VALIDATE_PIN_CODE = {
    "type": "function",
    "name": "validate_pin_code",
    "description": "Validate the six-digit postal PIN only after the customer confirms the read-back. The backend checks format, captured-value consistency, serviceability, and advances state.",
    "parameters": {
        "type": "object",
        "properties": {"pin_code": {"type": "string", "pattern": "^[0-9]{6}$"}},
        "required": ["pin_code"],
        "additionalProperties": False,
    },
}

GET_PRODUCT_INFORMATION = {
    "type": "function",
    "name": "get_product_information",
    "description": "Refresh approved facts for exactly one FinServe Instant product. Use only if the supplied snapshot is missing or stale for the requested topic.",
    "parameters": {
        "type": "object",
        "properties": {
            "product": {"type": "string", "enum": ["INSTANT_CLASSIC", "INSTANT_SUPER"]},
            "topics": {
                "type": "array",
                "items": {"type": "string", "enum": ["display_name", "comparison", "confirmation_pitch", "mandatory_disclosures", "kyc_preparation"]},
            },
        },
        "required": ["product"],
        "additionalProperties": False,
    },
}

CHECK_APPLICATION_STATUS = {
    "type": "function",
    "name": "check_application_status",
    "description": "Re-read the server-bound savings application when the customer says it is already complete. Never request credentials or full identity numbers.",
    "parameters": {
        "type": "object",
        "properties": {},
        "required": [],
        "additionalProperties": False,
    },
}

SCHEDULE_CALLBACK = {
    "type": "function",
    "name": "schedule_callback",
    "description": "Pass the customer's callback date and time wording unchanged. The backend resolves IST, validates future time and the 9 AM–6 PM window, and distinguishes a booking from a recorded preference.",
    "parameters": {
        "type": "object",
        "properties": {
            "date_expression": {"type": "string"},
            "time_expression": {"type": "string"},
            "reason": {"type": "string"},
        },
        "required": ["date_expression", "time_expression", "reason"],
        "additionalProperties": False,
    },
}

REGISTER_DO_NOT_CALL = {
    "type": "function",
    "name": "register_do_not_call",
    "description": "Durably suppress this customer immediately after an explicit do-not-call request. Never resume selling after calling it.",
    "parameters": {
        "type": "object",
        "properties": {"reason": {"type": "string"}},
        "required": ["reason"],
        "additionalProperties": False,
    },
}

CREATE_SAVINGS_ESCALATION = {
    "type": "function",
    "name": "create_escalation",
    "description": "Create a human follow-up for a savings-application issue. Do not claim success or a response time until CREATED or QUEUED is returned.",
    "parameters": {
        "type": "object",
        "properties": {
            "category": {
                "type": "string",
                "enum": ["FRAUD", "GRIEVANCE", "TECHNICAL_KYC", "CALLBACK_TECHNICAL", "PRODUCT_INFORMATION", "FUNDED_KYC_INCOMPLETE", "APPLICATION_STATUS", "PRIOR_PROMISE", "LEGAL_THREAT", "LANGUAGE_SUPPORT", "VULNERABILITY", "DOMESTIC_ABUSE", "DNC_COMPLIANCE"],
            },
            "summary": {"type": "string"},
            "urgency": {"type": "string", "enum": ["low", "medium", "high"]},
            "contact_preference": {"type": "string"},
        },
        "required": ["category", "summary", "urgency", "contact_preference"],
        "additionalProperties": False,
    },
}

FINALIZE_SAVINGS_CALL = {
    "type": "function",
    "name": "finalize_call",
    "description": "Write the single terminal CRM disposition. The backend validates all prerequisites and returns the exact customer_close. Call it once and speak that close exactly.",
    "parameters": {
        "type": "object",
        "properties": {
            "outcome": {
                "type": "string",
                "enum": ["HOT_LEAD", "CALLBACK_SCHEDULED", "CALLBACK_PREFERENCE_RECORDED", "NOT_INTERESTED", "NOT_ELIGIBLE_PIN", "NOT_ELIGIBLE_MINOR", "NOT_ELIGIBLE_NON_RESIDENT", "NOT_ELIGIBLE_DOCUMENTS", "ALREADY_COMPLETED", "APPLICATION_STATUS_UNCONFIRMED", "DNC_REQUESTED", "WRONG_NUMBER", "THIRD_PARTY", "WRONG_INTENT", "ESCALATED", "SAFETY_EXIT", "SYSTEM_ERROR", "NO_RESPONSE", "IRATE_CUSTOMER"],
            },
            "reason": {"type": "string"},
            "product": {"type": "string", "enum": ["INSTANT_CLASSIC", "INSTANT_SUPER"]},
            "callback_id": {"type": "string"},
            "escalation_id": {"type": "string"},
            "response_style": {"type": "string", "enum": ["HINDI", "HINGLISH"]},
        },
        "required": ["outcome", "reason", "response_style"],
        "additionalProperties": False,
    },
}

