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

CAMPAIGN_REGISTRY = {
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
