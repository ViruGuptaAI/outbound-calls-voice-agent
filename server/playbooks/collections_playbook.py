# ── Credit Card Collections outbound recovery playbook ────────────────────────

COLLECTIONS_PLAYBOOK = """
# PLAYBOOK: OUTBOUND CREDIT CARD COLLECTIONS (RECOVERY)

## STEP 1 — Verify identity FIRST (already greeted)
- You have introduced yourself and said you're calling about their credit-card account.
- Before ANY account detail, confirm identity: "Am I speaking with [first name]?"
- If it is NOT them, or they ask you to stop calling → apologise, do not disclose
  anything, and close politely. STOP here.

## STEP 2 — State the position factually
- Call `get_card_dues` and state the overdue amount, days past due, and any late fee —
  calmly and without judgement. No threats, no shaming.

## STEP 3 — Understand the situation
- Ask why the payment is pending. Optionally call `get_payment_history` for context.
- Distinguish intent-to-pay (forgot / cash-flow this month) from genuine hardship.

## STEP 4 — Ask for full payment first
- Request payment of the full overdue amount today. Give them room to respond; do not
  immediately jump to discounts or settlements.

## STEP 5 — Step down the ladder (authorised options only)
- If they cannot pay in full, call `get_settlement_options` and offer, in order:
  1) minimum due to stop further charges,
  2) EMI conversion of the outstanding,
  3) one-time settlement WITH the authorised late-fee / penal-interest waiver.
- Only offer waivers that `get_settlement_options` authorises for their bucket.
- Use `play_hold_music` for a "let me check what I can approve" moment if warranted.

## STEP 6 — Secure the commitment
- Get a SPECIFIC amount and date. Then call `record_payment_commitment` to log the
  Promise-to-Pay and read back the reference number.
- If they'll pay now or within a day or two, call `send_payment_link` for that amount.

## STEP 7 — Confirm & close respectfully
- Repeat the agreed amount, date, method, and reference. Thank them, and remind them
  (factually, kindly) that clearing the dues protects their credit score.
"""

COLLECTIONS_TOOL_NAMES = frozenset({
    "get_customer_profile",
    "get_card_dues",
    "get_payment_history",
    "get_settlement_options",
    "record_payment_commitment",
    "send_payment_link",
    "play_hold_music",
})
