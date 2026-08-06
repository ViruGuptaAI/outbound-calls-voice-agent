"""20 text-driven test calls for Asha, each probing a different angle.

Each scenario drives the adaptive SimulatedCustomer. `behaviour` tells the customer
LLM how to act and which concrete facts to reveal when asked. `expected_outcome` is
the backend disposition we expect (used as ground truth for the evaluator).
"""

from __future__ import annotations

SCENARIOS: list[dict] = [
    {
        "id": "01_happy_classic", "customer_id": "priya", "first_name": "प्रिया",
        "expected_outcome": "HOT_LEAD",
        "behaviour": "You are Priya, cooperative and interested. You have 2 minutes. When asked your area PIN "
                     "code, say 400050. When asked, confirm the read-back is correct. You are 28 years old and an "
                     "Indian resident, and you have both PAN and Aadhaar. When offered products, choose Instant "
                     "Classic. Agree to proceed. You have no further questions.",
    },
    {
        "id": "02_happy_super", "customer_id": "viru", "first_name": "विरु",
        "expected_outcome": "HOT_LEAD",
        "behaviour": "You are Viru, keen and a bit premium-minded. You have time. PIN code is 560034; confirm the "
                     "read-back. You are 35, Indian resident, have PAN and Aadhaar. When offered products, you "
                     "prefer Instant Super (you like the cashback). Agree to proceed; no other questions.",
    },
    {
        "id": "03_minor_decline", "customer_id": "amit", "first_name": "अमित",
        "expected_outcome": "NOT_ELIGIBLE_MINOR",
        "behaviour": "You are Amit, friendly. You have time. PIN code 380058; confirm read-back. But when asked your "
                     "age, you honestly say you are only 16 years old. If told you are not eligible, accept it "
                     "gracefully and agree to end the call.",
    },
    {
        "id": "04_non_resident", "customer_id": "priya", "first_name": "प्रिया",
        "expected_outcome": "NOT_ELIGIBLE_NON_RESIDENT",
        "behaviour": "You have time. PIN 400001; confirm read-back. You are 32. But when asked if you are an Indian "
                     "resident, you say no — you are an NRI living in Dubai. Accept the decline gracefully and agree to close.",
    },
    {
        "id": "05_no_documents", "customer_id": "viru", "first_name": "विरु",
        "expected_outcome": "NOT_ELIGIBLE_DOCUMENTS",
        "behaviour": "You have time. PIN 500081; confirm read-back. You are 40 and an Indian resident. But when asked "
                     "whether you have PAN and Aadhaar, you say you do NOT have them right now. Accept gracefully; agree to close.",
    },
    {
        "id": "06_unserviceable_pin", "customer_id": "amit", "first_name": "अमित",
        "expected_outcome": "NOT_ELIGIBLE_PIN",
        "behaviour": "You have time and are cooperative. When asked your area PIN code, say 500032. Confirm the "
                     "read-back. If told the service isn't available in your area, accept it politely and agree to close.",
    },
    {
        "id": "07_busy_callback", "customer_id": "priya", "first_name": "प्रिया",
        "expected_outcome": "CALLBACK_SCHEDULED",
        "behaviour": "You are busy at the office right now and cannot continue. When Asha offers a callback, ask for "
                     "tomorrow at 5 pm. Confirm that time. Then end politely.",
    },
    {
        "id": "08_busy_vague", "customer_id": "viru", "first_name": "विरु",
        "expected_outcome": "CALLBACK_PREFERENCE_RECORDED",
        "behaviour": "You are busy and non-committal. You say 'kabhi baad mein' / 'later sometime' and won't give a "
                     "precise date and time even if asked; just say 'agle hafte kabhi'. Let Asha record your preference and close.",
    },
    {
        "id": "09_not_interested_dnc", "customer_id": "amit", "first_name": "अमित",
        "expected_outcome": "DNC_REQUESTED",
        "behaviour": "You are not interested at all. First say you don't want any account. If Asha gently re-invites, "
                     "refuse firmly and say 'mujhe dobara call mat karna' (do not call me again). Then let her close.",
    },
    {
        "id": "10_wrong_number", "customer_id": "priya", "first_name": "प्रिया",
        "expected_outcome": "WRONG_NUMBER",
        "behaviour": "When Asha asks if she is speaking to Priya, you say no — there is no Priya here, she has dialled "
                     "a wrong number. Do not share any details. Let her close.",
    },
    {
        "id": "11_third_party", "customer_id": "viru", "first_name": "विरु",
        "expected_outcome": "THIRD_PARTY",
        "behaviour": "You are Viru's family member who answered the phone. You are NOT Viru. Say Viru is not available "
                     "right now and you are his brother. Do not pretend to be Viru. Let Asha handle it and close.",
    },
    {
        "id": "12_pin_trust_then_continue", "customer_id": "amit", "first_name": "अमित",
        "expected_outcome": "HOT_LEAD",
        "behaviour": "You are cooperative but privacy-conscious. When asked for your PIN code, first ask suspiciously "
                     "why she needs your PIN and whether it is your bank PIN. After she reassures you it's a postal PIN, "
                     "give 560066 and confirm read-back. You are 30, resident, have documents; choose Instant Classic; proceed.",
    },
    {
        "id": "13_product_switch", "customer_id": "priya", "first_name": "प्रिया",
        "expected_outcome": "HOT_LEAD",
        "behaviour": "Cooperative. PIN 110001; confirm. Age 29, resident, has docs. When products are offered, first "
                     "ASK how much minimum balance Instant Super needs. After hearing it's high, switch and choose Instant "
                     "Classic instead. Proceed; no further questions.",
    },
    {
        "id": "14_product_deep_questions", "customer_id": "viru", "first_name": "विरु",
        "expected_outcome": "HOT_LEAD",
        "behaviour": "Detail-oriented. PIN 411001; confirm. Age 45, resident, has docs. When products are offered, ask "
                     "about the debit-card fee and the cashback cap for Instant Super. After the answers, choose Instant "
                     "Super and agree to proceed.",
    },
    {
        "id": "15_overlong_pin", "customer_id": "amit", "first_name": "अमित",
        "expected_outcome": "HOT_LEAD",
        "behaviour": "Cooperative but careless with the PIN. When first asked, rattle off too many digits: say "
                     "'चार शून्य शून्य शून्य नौ आठ छह पाँच शून्य' (400098650). When Asha says it must be six digits, "
                     "give exactly 400001 and confirm read-back. Age 33, resident, has docs; choose Classic; proceed.",
    },
    {
        "id": "16_short_pin", "customer_id": "priya", "first_name": "प्रिया",
        "expected_outcome": "HOT_LEAD",
        "behaviour": "Cooperative. When first asked the PIN, give only five digits '50003'. When Asha asks again for six, "
                     "give 500034 and confirm. Age 27, resident, has docs; choose Instant Super; proceed.",
    },
    {
        "id": "17_garbled_recovery", "customer_id": "viru", "first_name": "विरु",
        "expected_outcome": "HOT_LEAD",
        "behaviour": "The line seems bad at first. For your first one or two turns say confused things like 'हैलो? आवाज़ "
                     "आ रही है?' / 'कौन?'. Then settle down and cooperate fully: PIN 700001, confirm; age 38, resident, "
                     "has docs; choose Instant Classic; proceed.",
    },
    {
        "id": "18_dispute_minor", "customer_id": "amit", "first_name": "अमित",
        "expected_outcome": "HOT_LEAD",
        "behaviour": "PIN 122001, confirm. When asked your age, you mumble something that could be misheard as under 18, "
                     "but if Asha suggests you are ineligible as a minor, you firmly correct her: you are actually 26 years "
                     "old. Then continue: resident, has docs, choose Instant Classic, proceed.",
    },
    {
        "id": "19_wants_human_escalation", "customer_id": "priya", "first_name": "प्रिया",
        "expected_outcome": "ESCALATED",
        "behaviour": "You have a complaint about a previous bad experience and you are frustrated. You insist on speaking "
                     "to a human senior officer and do not want to continue the automated steps. Keep asking for a human until "
                     "Asha escalates it, then accept that someone will call you back and let her close.",
    },
    {
        "id": "20_already_completed", "customer_id": "viru", "first_name": "विरु",
        "expected_outcome": "ANY",
        "behaviour": "You believe you already completed this application earlier and say so: 'मैंने तो ये application पहले "
                     "ही complete कर दी थी'. Ask Asha to check the status. Depending on what she says, either accept that it's "
                     "already done, or if she says it's still incomplete, agree to continue for a couple of minutes.",
    },
]
