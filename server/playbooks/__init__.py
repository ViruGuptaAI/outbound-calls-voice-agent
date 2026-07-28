# ──────────────────────────────────────────────────────────────────────────────
# Playbook registry — maps a campaign key to its workflow text + the exact set
# of tools that workflow needs. The server injects the playbook into the agent's
# instructions and filters the tool list to only what the playbook requires,
# keeping each LLM call lean.
# ──────────────────────────────────────────────────────────────────────────────

from .home_loan_playbook import HOME_LOAN_PLAYBOOK, HOME_LOAN_TOOL_NAMES
from .vehicle_loan_playbook import VEHICLE_LOAN_PLAYBOOK, VEHICLE_LOAN_TOOL_NAMES
from .collections_playbook import COLLECTIONS_PLAYBOOK, COLLECTIONS_TOOL_NAMES
from .vehicle_loan_collections_playbook import (
    VEHICLE_LOAN_COLLECTIONS_PLAYBOOK,
    VEHICLE_LOAN_COLLECTIONS_TOOL_NAMES,
)
from .life_insurance_playbook import LIFE_INSURANCE_PLAYBOOK, LIFE_INSURANCE_TOOL_NAMES
from .life_insurance_collections_playbook import (
    LIFE_INSURANCE_COLLECTIONS_PLAYBOOK,
    LIFE_INSURANCE_COLLECTIONS_TOOL_NAMES,
)

PLAYBOOK_REGISTRY: dict[str, tuple[str, frozenset[str]]] = {
    "life_insurance": (LIFE_INSURANCE_PLAYBOOK, LIFE_INSURANCE_TOOL_NAMES),
    "life_premium_recovery": (
        LIFE_INSURANCE_COLLECTIONS_PLAYBOOK,
        LIFE_INSURANCE_COLLECTIONS_TOOL_NAMES,
    ),
    "home_loan": (HOME_LOAN_PLAYBOOK, HOME_LOAN_TOOL_NAMES),
    "vehicle_loan": (VEHICLE_LOAN_PLAYBOOK, VEHICLE_LOAN_TOOL_NAMES),
    "cc_collections": (COLLECTIONS_PLAYBOOK, COLLECTIONS_TOOL_NAMES),
    "vehicle_loan_collections": (
        VEHICLE_LOAN_COLLECTIONS_PLAYBOOK,
        VEHICLE_LOAN_COLLECTIONS_TOOL_NAMES,
    ),
}


def get_playbook(campaign_key: str) -> tuple[str, frozenset[str] | None]:
    """Return (playbook_text, required_tool_names) for a campaign."""
    return PLAYBOOK_REGISTRY.get(campaign_key, ("", None))
