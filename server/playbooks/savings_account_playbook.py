"""Tool allowlist and compact workflow reminder for Asha."""


SAVINGS_ACCOUNT_PLAYBOOK = """
# MANAGED SAVINGS WORKFLOW

The backend runtime context and tool results own the journey. Handle only the
current state, submit each unambiguous answer, and never ask the next journey
question before `ACCEPTED`. Use only approved product facts. A terminal outcome
is valid only after `finalize_call` returns `FINALIZED`.

One customer utterance can produce at most one accepted transition. A question
or objection is not a result; answer it without a tool. In `PIN_CAPTURE`, simply
ask for the area PIN code; the customer sharing it is the consent. Only if they
ask why or hesitate, reassure them it is a postal PIN that just checks area
availability, cannot access banking activity, and is optional.
An utterance answers only the state that was active when it arrived; never reuse
an identity or earlier-step answer after the backend enters a new state.
"""


SAVINGS_ACCOUNT_TOOL_NAMES = frozenset(
    {
        "submit_step_result",
        "validate_pin_code",
        "get_product_information",
        "check_application_status",
        "schedule_callback",
        "register_do_not_call",
        "create_escalation",
        "finalize_call",
    }
)