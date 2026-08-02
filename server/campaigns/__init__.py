# ──────────────────────────────────────────────────────────────────────────────
# Campaign registry — maps a campaign key to its outbound agent + metadata.
#
# Unlike the inbound Virtual RM (which had a triage agent that routed to
# specialists), an OUTBOUND call has NO triage. The human operator picks a
# campaign and a customer, and the matching goal-driven agent initiates the call.
# ──────────────────────────────────────────────────────────────────────────────

from .home_loan_agent import HOME_LOAN_PROMPT, HOME_LOAN_TOOLS
from .vehicle_loan_agent import VEHICLE_LOAN_PROMPT, VEHICLE_LOAN_TOOLS
from .collections_agent import COLLECTIONS_PROMPT, COLLECTIONS_TOOLS
from .vehicle_loan_collections_agent import (
    VEHICLE_LOAN_COLLECTIONS_PROMPT,
    VEHICLE_LOAN_COLLECTIONS_TOOLS,
)
from .life_insurance_agent import LIFE_INSURANCE_PROMPT, LIFE_INSURANCE_TOOLS
from .life_insurance_collections_agent import (
    LIFE_INSURANCE_COLLECTIONS_PROMPT,
    LIFE_INSURANCE_COLLECTIONS_TOOLS,
)
from .savings_account_sales_agent import (
    SAVINGS_ACCOUNT_SALES_PROMPT,
    SAVINGS_ACCOUNT_SALES_TOOLS,
)

CAMPAIGN_REGISTRY = {
    "savings_account_completion": {
        "prompt": SAVINGS_ACCOUNT_SALES_PROMPT,
        "agent_name": "Asha",
        "name": "Asha (Savings Account Assistant)",
        "voice": "en-IN-Diya:DragonHDLatestNeural",
        "tools": SAVINGS_ACCOUNT_SALES_TOOLS,
        "company": "Contoso Bank",
        "title": "Incomplete Savings Account",
        "description": "Help a customer resume an incomplete FinServe Instant savings-account application.",
        "icon": "🏦",
        "color": "#0f766e",
        "audience": "incomplete_savings_application",
        "managed_flow": "savings_account",
        "opening_purpose": "Confirm the intended recipient before revealing application details.",
    },
    "life_insurance": {
        "prompt": LIFE_INSURANCE_PROMPT,
        "agent_name": "Ananya",
        "name": "Ananya (Life Insurance Advisor)",
        "voice": "en-IN-Diya:DragonHDLatestNeural",
        "tools": LIFE_INSURANCE_TOOLS,
        # This campaign is a life insurer, not the bank — override the opening brand.
        "company": "Contoso Life",
        "title": "Life Insurance Sales",
        "description": "Assess a family's protection gap and recommend the right life cover, savings, child or retirement plan.",
        "icon": "\U0001F6E1\uFE0F",
        "color": "#38a169",
        "opening_purpose": (
            "Your opening hook is a gentle, thought-provoking QUESTION you actually ASK — do NOT "
            "replace it with a generic 'I help people…' statement, and do NOT mention any number. "
            "Ask this (or a very close variant): 'Can I ask you one quick thing — if your income "
            "were to stop tomorrow, how many months could your family comfortably manage the home "
            "and the expenses?' Framed around love/responsibility, never morbid. This is life cover "
            "(protecting the family's income), not health insurance — but do NOT turn the opening "
            "into a definition."
        ),
        "opening_ask": (
            "Then add a LOW-PRESSURE promise — e.g. 'Give me just two minutes, and if it's not "
            "useful you can hang up on me.' Do NOT quote any cover amount or premium. LISTEN to "
            "their answer and acknowledge it, then CONTINUE DISCOVERY — ask who depends on their "
            "income and their rough annual income — BEFORE any cover figure. Never jump to the "
            "number just because they said 'go ahead' or 'tell me'."
        ),
    },
    "life_premium_recovery": {
        "prompt": LIFE_INSURANCE_COLLECTIONS_PROMPT,
        "agent_name": "Anjali",
        "name": "Anjali (Policy Servicing Officer)",
        "voice": "en-IN-Diya:DragonHDLatestNeural",
        "tools": LIFE_INSURANCE_COLLECTIONS_TOOLS,
        "company": "Contoso Life",
        "title": "Life Insurance Premium Recovery",
        "description": "Remind an existing policyholder about an overdue premium and help keep the policy in-force (or revive a lapsed one), respectfully.",
        "icon": "\U0001F6DF",
        "color": "#805ad5",
        # Only offer this campaign for customers who actually hold an overdue policy.
        "audience": "premium_overdue",
        "opening_purpose": (
            "You are calling an EXISTING policyholder about a pending premium on their LIFE "
            "insurance policy (this is NOT a loan or credit card, and NOT health insurance). "
            "You are helping them keep their family's cover from lapsing — a friendly reminder, "
            "not a debt-collection call. Verify you are speaking to the right person before "
            "sharing any policy details."
        ),
    },
    "home_loan": {
        "prompt": HOME_LOAN_PROMPT,
        "agent_name": "Priya",
        "name": "Priya (Home Loan Advisor)",
        "voice": "en-IN-Diya:DragonHDLatestNeural",
        "tools": HOME_LOAN_TOOLS,
        # UI card metadata
        "title": "Home Loan Sales",
        "description": "Pitch a pre-approved home loan or balance-transfer offer and book the application.",
        "icon": "🏠",
        "color": "#ed8936",
        # Outbound opening — what the agent says to kick off the call
        "opening_purpose": (
            "You are calling to tell them about an attractive, pre-approved home loan offer "
            "from Contoso Bank (they may be buying, building, renovating, or paying a higher "
            "rate elsewhere)."
        ),
    },
    "vehicle_loan": {
        "prompt": VEHICLE_LOAN_PROMPT,
        "agent_name": "Kavya",
        "name": "Kavya (Vehicle Loan Advisor)",
        "voice": "en-IN-Diya:DragonHDLatestNeural",
        "tools": VEHICLE_LOAN_TOOLS,
        "title": "Vehicle Loan Sales",
        "description": "Pitch a pre-approved car loan with quick disbursal and drive to an application.",
        "icon": "🚗",
        "color": "#4299e1",
        "opening_purpose": (
            "You are calling about a pre-approved vehicle loan offer from Contoso Bank."
        ),
        "opening_ask": (
            "Then ask a simple intent question — whether they are planning to buy a vehicle "
            "or take a vehicle loan any time soon (e.g. 'Are you planning a vehicle purchase "
            "soon?'). Do NOT quote any rate, amount, tenure, or funding % in the opening."
        ),
    },
    "cc_collections": {
        "prompt": COLLECTIONS_PROMPT,
        "agent_name": "Neha",
        "name": "Neha (Collections Officer)",
        "voice": "en-IN-Diya:DragonHDLatestNeural",
        "tools": COLLECTIONS_TOOLS,
        "title": "Credit Card Collections",
        "description": "Recover an overdue credit-card payment and secure a promise-to-pay, respectfully.",
        "icon": "💳",
        "color": "#e53e3e",
        # Only offer this campaign for customers who actually have credit-card dues.
        "audience": "card_overdue",
        "opening_purpose": (
            "You are calling regarding an overdue payment on their Contoso Bank credit card. "
            "Verify you are speaking to the right person before discussing any details."
        ),
    },
    "vehicle_loan_collections": {
        "prompt": VEHICLE_LOAN_COLLECTIONS_PROMPT,
        "agent_name": "Meera",
        "name": "Meera (Vehicle Loan Recovery Officer)",
        "voice": "en-IN-Diya:DragonHDLatestNeural",
        "tools": VEHICLE_LOAN_COLLECTIONS_TOOLS,
        "title": "Vehicle Loan Collections",
        "description": "Recover an overdue vehicle-loan EMI and secure a promise-to-pay, respectfully.",
        "icon": "\U0001F699",
        "color": "#dd6b20",
        # Only offer this campaign for customers who actually have vehicle-loan dues.
        "audience": "loan_overdue",
        "opening_purpose": (
            "You are calling regarding an overdue EMI on their Contoso Bank vehicle (car) loan. "
            "Verify you are speaking to the right person before discussing any details."
        ),
    },
}

DEFAULT_CAMPAIGN = "home_loan"


def get_campaign(campaign_key: str) -> dict:
    """Return the campaign config, falling back to the default campaign."""
    return CAMPAIGN_REGISTRY.get(campaign_key, CAMPAIGN_REGISTRY[DEFAULT_CAMPAIGN])
