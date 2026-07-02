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

