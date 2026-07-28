"""
CRM Tools — SQLite query functions for Voice Live function calling.
Each function takes a customer_id and returns a JSON-serializable dict.
"""

import os
import re
import smtplib
import sqlite3
from email.message import EmailMessage
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent / "crm.db"

# Human agent who receives escalation / hand-off emails.
ESCALATION_EMAIL = os.getenv("ESCALATION_EMAIL", "vguptha@microsoft.com")


def _query(sql: str, params: tuple = ()) -> list[dict]:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def _query_one(sql: str, params: tuple = ()) -> dict | None:
    rows = _query(sql, params)
    return rows[0] if rows else None


# ─── Triage / Common ──────────────────────────────────────────────────────────

def get_customer_profile(customer_id: str) -> dict:
    """Customer name, segment, city, KYC status, relationship tenure."""
    row = _query_one("SELECT * FROM customers WHERE id = ?", (customer_id,))
    if not row:
        return {"error": "Customer not found"}
    return row


def get_customer_summary(customer_id: str) -> dict:
    """Quick overview: accounts, cards, loans, FDs — for triage greeting."""
    profile = _query_one(
        "SELECT name, segment, city, relationship_since FROM customers WHERE id = ?",
        (customer_id,),
    )
    if not profile:
        return {"error": "Customer not found"}

    accounts = _query(
        "SELECT account_type, balance FROM accounts WHERE customer_id = ?",
        (customer_id,),
    )
    cards = _query(
        "SELECT card_type, outstanding, reward_points FROM credit_cards WHERE customer_id = ?",
        (customer_id,),
    )
    loans = _query(
        "SELECT loan_type, outstanding, emi FROM loans WHERE customer_id = ? AND status = 'Active'",
        (customer_id,),
    )

    return {
        **profile,
        "accounts": accounts,
        "credit_cards": cards,
        "active_loans": loans,
    }


# ─── Risk & Eligibility Model ────────────────────────────────────────────────

def get_eligibility_assessment(customer_id: str, product_type: str) -> dict:
    """
    Risk-based eligibility assessment. Evaluates customer across 6 dimensions,
    computes a risk score (0-100), and returns eligibility tier + reasons.
    
    product_type: "rate_reduction", "new_loan", "card_upgrade", "limit_increase"
    """
    customer = _query_one("SELECT * FROM customers WHERE id = ?", (customer_id,))
    if not customer:
        return {"error": "Customer not found"}

    accounts = _query(
        "SELECT balance FROM accounts WHERE customer_id = ?", (customer_id,)
    )
    total_deposits = sum(a["balance"] for a in accounts)

    fds = _query(
        "SELECT amount FROM fixed_deposits WHERE customer_id = ?", (customer_id,)
    )
    total_fd = sum(f["amount"] for f in fds)

    investments = _query(
        "SELECT current_value FROM investments WHERE customer_id = ?", (customer_id,)
    )
    total_investments = sum(i["current_value"] for i in investments)

    loans = _query(
        "SELECT outstanding, emi FROM loans WHERE customer_id = ? AND status = 'Active'",
        (customer_id,),
    )
    total_outstanding = sum(l["outstanding"] for l in loans)
    total_emi = sum(l["emi"] for l in loans)

    # ── Calculate relationship tenure in years ──
    from datetime import date
    try:
        since = date.fromisoformat(customer["relationship_since"])
        tenure_years = (date.today() - since).days / 365.25
    except (ValueError, TypeError):
        tenure_years = 0

    monthly_income = customer.get("monthly_income", 0) or 0
    cibil = customer.get("cibil_score", 0) or 0
    salary_bank = customer.get("salary_bank", 0)
    emi_delays = customer.get("emi_delays_12m", 0)
    total_products = customer.get("total_products", 1)
    total_relationship_value = total_deposits + total_fd + total_investments

    # ── SCORING MODEL (0-100) ────────────────────────────────────────
    score = 0.0
    factors = []

    # Factor 1: CIBIL Score (30 points max)
    if cibil >= 800:
        score += 30; factors.append(f"Excellent CIBIL score ({cibil}): +30pts")
    elif cibil >= 750:
        score += 25; factors.append(f"Very good CIBIL score ({cibil}): +25pts")
    elif cibil >= 700:
        score += 15; factors.append(f"Good CIBIL score ({cibil}): +15pts")
    elif cibil >= 650:
        score += 5; factors.append(f"Fair CIBIL score ({cibil}): +5pts")
    else:
        factors.append(f"Low CIBIL score ({cibil}): +0pts — needs improvement")

    # Factor 2: Relationship Tenure (15 points max)
    if tenure_years >= 7:
        score += 15; factors.append(f"Long-standing customer ({tenure_years:.0f} years): +15pts")
    elif tenure_years >= 5:
        score += 12; factors.append(f"Established customer ({tenure_years:.0f} years): +12pts")
    elif tenure_years >= 3:
        score += 8; factors.append(f"Growing relationship ({tenure_years:.0f} years): +8pts")
    elif tenure_years >= 1:
        score += 3; factors.append(f"Relatively new customer ({tenure_years:.1f} years): +3pts")
    else:
        factors.append(f"New customer ({tenure_years:.1f} years): +0pts — build history first")

    # Factor 3: Total Relationship Value (20 points max)
    if total_relationship_value >= 2500000:
        score += 20; factors.append(f"High-value portfolio (₹{total_relationship_value:,.0f}): +20pts")
    elif total_relationship_value >= 1000000:
        score += 15; factors.append(f"Strong portfolio (₹{total_relationship_value:,.0f}): +15pts")
    elif total_relationship_value >= 500000:
        score += 10; factors.append(f"Moderate portfolio (₹{total_relationship_value:,.0f}): +10pts")
    elif total_relationship_value >= 100000:
        score += 5; factors.append(f"Building portfolio (₹{total_relationship_value:,.0f}): +5pts")
    else:
        factors.append(f"Low portfolio value (₹{total_relationship_value:,.0f}): +0pts")

    # Factor 4: Payment History (15 points max)
    if emi_delays == 0:
        score += 15; factors.append("Perfect payment history (0 delays): +15pts")
    elif emi_delays == 1:
        score += 8; factors.append(f"Minor payment delay ({emi_delays} in 12m): +8pts")
    elif emi_delays <= 3:
        score += 3; factors.append(f"Payment delays ({emi_delays} in 12m): +3pts — needs attention")
    else:
        factors.append(f"Frequent delays ({emi_delays} in 12m): +0pts — not eligible")

    # Factor 5: Salary Banking (10 points max)
    if salary_bank:
        score += 10; factors.append("Salary credited to Contoso Bank: +10pts")
    else:
        factors.append("Salary at another bank: +0pts — consider moving salary for better offers")

    # Factor 6: Product Diversity (10 points max)
    if total_products >= 5:
        score += 10; factors.append(f"Deep product engagement ({total_products} products): +10pts")
    elif total_products >= 3:
        score += 7; factors.append(f"Good product engagement ({total_products} products): +7pts")
    elif total_products >= 2:
        score += 3; factors.append(f"Limited products ({total_products}): +3pts")
    else:
        factors.append(f"Single product ({total_products}): +0pts")

    # ── DTI check for new loan eligibility ──
    dti_ratio = round((total_emi / monthly_income * 100), 1) if monthly_income > 0 else 999
    if product_type == "new_loan":
        if dti_ratio > 50:
            factors.append(f"⚠️ High debt-to-income ratio ({dti_ratio}%): EMI burden too high")
        elif dti_ratio > 40:
            factors.append(f"Moderate debt-to-income ratio ({dti_ratio}%): limited additional EMI capacity")

    # ── ELIGIBILITY TIER ─────────────────────────────────────────────
    score = round(score)
    if emi_delays >= 4:
        tier = "NOT_ELIGIBLE"
        max_concession_round = 0
        recommendation = "Customer has frequent payment delays. Not eligible for rate concessions at this time."
    elif score >= 80:
        tier = "HIGHLY_ELIGIBLE"
        max_concession_round = 3
        recommendation = "Excellent profile. Full negotiation range available including retention offer."
    elif score >= 60:
        tier = "ELIGIBLE"
        max_concession_round = 2
        recommendation = "Good profile. Standard and loyalty discounts available. Retention offer only if truly at risk."
    elif score >= 40:
        tier = "CONDITIONALLY_ELIGIBLE"
        max_concession_round = 1
        recommendation = "Average profile. Only standard product discount available. Suggest improving CIBIL or relationship value."
    else:
        tier = "NOT_ELIGIBLE"
        max_concession_round = 0
        recommendation = "Profile does not meet criteria for rate concession. Suggest building credit history."

    return {
        "customer_name": customer["name"],
        "risk_score": score,
        "eligibility_tier": tier,
        "max_concession_round": max_concession_round,
        "recommendation": recommendation,
        "scoring_factors": factors,
        "summary": {
            "cibil_score": cibil,
            "relationship_years": round(tenure_years, 1),
            "total_relationship_value": total_relationship_value,
            "monthly_income": monthly_income,
            "total_emi": total_emi,
            "dti_ratio_pct": dti_ratio,
            "salary_with_us": bool(salary_bank),
            "emi_delays_12m": emi_delays,
            "product_count": total_products,
        },
    }


# ─── Credit Card Agent Tools ─────────────────────────────────────────────────

def get_credit_card_details(customer_id: str) -> dict:
    """Full credit card info: limit, outstanding, rewards, due date, fees."""
    cards = _query(
        "SELECT * FROM credit_cards WHERE customer_id = ?", (customer_id,)
    )
    return {"cards": cards} if cards else {"error": "No credit cards found"}


def get_credit_card_transactions(customer_id: str) -> dict:
    """Recent credit card transactions (last 10)."""
    txns = _query(
        "SELECT date, description, amount, category FROM transactions "
        "WHERE customer_id = ? AND account_type = 'credit_card' "
        "ORDER BY date DESC LIMIT 10",
        (customer_id,),
    )
    return {"transactions": txns}


def get_reward_points(customer_id: str) -> dict:
    """Reward points balance and estimated value."""
    cards = _query(
        "SELECT card_type, reward_points FROM credit_cards WHERE customer_id = ?",
        (customer_id,),
    )
    for c in cards:
        c["estimated_value_inr"] = round(c["reward_points"] * 0.25, 2)
    return {"cards": cards}


def get_card_spending_analysis(customer_id: str) -> dict:
    """Analyze spending patterns from transactions for negotiation and advisory."""
    txns = _query(
        "SELECT amount, category FROM transactions "
        "WHERE customer_id = ? AND account_type = 'credit_card'",
        (customer_id,),
    )
    card = _query_one(
        "SELECT credit_limit, outstanding, annual_fee, fee_waiver_condition, issued_on "
        "FROM credit_cards WHERE customer_id = ? LIMIT 1",
        (customer_id,),
    )
    if not card:
        return {"error": "No credit card found"}

    total_spend = sum(t["amount"] for t in txns)
    category_spend: dict[str, float] = {}
    for t in txns:
        cat = t["category"] or "other"
        category_spend[cat] = category_spend.get(cat, 0) + t["amount"]

    # Annualize (assume transactions cover ~1 month of data)
    monthly_avg = total_spend
    annual_projected = monthly_avg * 12
    utilization = round((card["outstanding"] / card["credit_limit"]) * 100, 1) if card["credit_limit"] else 0

    # Fee waiver check
    waiver_threshold = None
    fee_waiver_condition = card.get("fee_waiver_condition", "")
    if fee_waiver_condition:
        import re
        # Match patterns like "₹3L", "₹3,00,000", "₹300000", "₹2L"
        m = re.search(r'₹([\d,]+)L', fee_waiver_condition)
        if m:
            waiver_threshold = int(m.group(1).replace(",", "")) * 100000
        else:
            m = re.search(r'₹([\d,]+)', fee_waiver_condition)
            if m:
                waiver_threshold = int(m.group(1).replace(",", ""))

    fee_waiver_eligible = bool(waiver_threshold and annual_projected >= waiver_threshold)

    return {
        "total_recent_spend": total_spend,
        "monthly_average_spend": monthly_avg,
        "annual_projected_spend": annual_projected,
        "category_breakdown": category_spend,
        "credit_utilization_pct": utilization,
        "annual_fee": card["annual_fee"],
        "fee_waiver_condition": fee_waiver_condition,
        "fee_waiver_threshold": waiver_threshold,
        "fee_waiver_eligible": fee_waiver_eligible,
        "card_age_since": card["issued_on"],
        "top_category": max(category_spend, key=category_spend.get) if category_spend else None,
    } 


def check_card_upgrade_eligibility(customer_id: str) -> dict:
    """Check eligible upgrade paths with benefits comparison."""
    card = _query_one(
        "SELECT card_type, credit_limit, reward_points FROM credit_cards WHERE customer_id = ? LIMIT 1",
        (customer_id,),
    )
    if not card:
        return {"error": "No credit card found"}

    profile = _query_one(
        "SELECT segment FROM customers WHERE id = ?", (customer_id,)
    )
    segment = profile["segment"] if profile else "Classic"

    upgrade_paths = _query(
        "SELECT * FROM card_upgrade_paths WHERE from_card = ?",
        (card["card_type"],),
    )

    eligible = []
    for path in upgrade_paths:
        is_eligible = card["credit_limit"] >= path["min_spend_required"]
        eligible.append({
            "from_card": path["from_card"],
            "to_card": path["to_card"],
            "annual_fee_difference": path["to_annual_fee"] - path["from_annual_fee"],
            "from_annual_fee": path["from_annual_fee"],
            "to_annual_fee": path["to_annual_fee"],
            "new_benefits": path["additional_benefits"],
            "min_spend_required": path["min_spend_required"],
            "eligible": is_eligible,
            "customer_segment": segment,
        })

    return {
        "current_card": card["card_type"],
        "current_limit": card["credit_limit"],
        "upgrade_options": eligible,
    }


# ─── Loan Agent Tools ─────────────────────────────────────────────────────────

def get_active_loans(customer_id: str) -> dict:
    """All active loans with EMI, rate, outstanding, tenure remaining."""
    loans = _query(
        "SELECT * FROM loans WHERE customer_id = ? AND status = 'Active'",
        (customer_id,),
    )
    return {"loans": loans}


def get_loan_product_details(product_name: str) -> dict:
    """Bank rate card for customer-facing info: base rate, tenure, fees, eligibility."""
    row = _query_one(
        "SELECT * FROM loan_products WHERE product_name = ?", (product_name,)
    )
    if not row:
        return {"error": f"Product '{product_name}' not found"}
    # Return only customer-facing fields — hide internal pricing thresholds
    return {
        "product_name": row["product_name"],
        "base_rate": row["base_rate"],
        "ceiling_rate": row["ceiling_rate"],
        "min_amount": row["min_amount"],
        "max_amount": row["max_amount"],
        "min_tenure_months": row["min_tenure_months"],
        "max_tenure_months": row["max_tenure_months"],
        "processing_fee_pct": row["processing_fee_pct"],
        "prepayment_penalty_pct": row["prepayment_penalty_pct"],
        "foreclosure_fee_pct": row["foreclosure_fee_pct"],
        "ltv_ratio": row["ltv_ratio"],
        "collateral_required": row["collateral_required"],
        "eligibility_note": row["eligibility_note"],
    }


def get_preapproved_offers(customer_id: str) -> dict:
    """Pre-approved loan/card offers for this customer."""
    offers = _query(
        "SELECT * FROM preapproved_offers WHERE customer_id = ?",
        (customer_id,),
    )
    return {"offers": offers}


def get_negotiation_terms(customer_id: str, product_name: str) -> dict:
    """Calculate best possible rate for this customer + product, gated by eligibility."""
    profile = _query_one(
        "SELECT segment FROM customers WHERE id = ?", (customer_id,)
    )
    if not profile:
        return {"error": "Customer not found"}

    product = _query_one(
        "SELECT * FROM loan_products WHERE product_name = ?", (product_name,)
    )
    if not product:
        return {"error": f"Product '{product_name}' not found"}

    rules = _query_one(
        "SELECT * FROM negotiation_rules WHERE segment = ? AND product_name = ?",
        (profile["segment"], product_name),
    )

    # ── Run eligibility assessment ────────────────────────────────────
    eligibility = get_eligibility_assessment(customer_id, "rate_reduction")
    risk_score = eligibility["risk_score"]
    max_round = eligibility["max_concession_round"]

    base_rate = product["base_rate"]
    floor_rate = product["floor_rate"]
    max_discount = product["max_discount_bps"]
    bonus_discount = rules["bonus_discount_bps"] if rules else 0
    retention_bps = rules["retention_offer_bps"] if rules else 0

    # ── Proportional tier calculation ─────────────────────────────────
    spread = base_rate - floor_rate
    if spread >= 0.05:
        seg_shift = min(bonus_discount / 100, spread * 0.10)
        round1_rate = round(base_rate - spread * 0.35, 2)
        round2_rate = round(base_rate - spread * 0.65 - seg_shift, 2)
        round3_rate = round(floor_rate, 2)
        round2_rate = max(round2_rate, round3_rate)
        round1_rate = max(round1_rate, round2_rate + 0.05)
    else:
        round1_rate = round2_rate = round3_rate = round(base_rate, 2)

    # ── Gate tiers by eligibility ─────────────────────────────────────
    # If customer isn't eligible for Round 2/3, cap those at Round 1 rate
    tiers = {}
    if max_round >= 1:
        tiers["round1_rate"] = round(round1_rate, 2)
        tiers["round1_label"] = "standard product discount"
        tiers["round1_reason"] = f"Based on your CIBIL score and payment history"
    if max_round >= 2:
        tiers["round2_rate"] = round(round2_rate, 2)
        tiers["round2_label"] = "loyalty + relationship bonus"
        tiers["round2_reason"] = f"Based on your {eligibility['summary']['relationship_years']:.0f}-year relationship and ₹{eligibility['summary']['total_relationship_value']:,.0f} portfolio"
    if max_round >= 3:
        tiers["round3_rate"] = round(round3_rate, 2)
        tiers["round3_label"] = "retention offer (only if threatening to leave)"
        tiers["round3_reason"] = "Special retention pricing for valued customers"

    return {
        "customer_segment": profile["segment"],
        "product": product_name,
        "base_rate": base_rate,
        "ceiling_rate": product["ceiling_rate"],
        "eligibility": {
            "risk_score": risk_score,
            "tier": eligibility["eligibility_tier"],
            "max_round": max_round,
            "top_factors": eligibility["scoring_factors"][:3],
        },
        "negotiation_tiers": tiers,
        "_INTERNAL_do_not_disclose_to_customer": {
            "absolute_floor": floor_rate,
            "max_product_discount_bps": max_discount,
            "segment_bonus_bps": bonus_discount,
            "retention_bps": retention_bps,
        },
        "fee_waiver_eligible": bool(rules["fee_waiver_eligible"]) if rules else False,
        "priority_processing": bool(rules["priority_processing"]) if rules else False,
        "processing_fee_pct": product["processing_fee_pct"],
        "prepayment_penalty_pct": product["prepayment_penalty_pct"],
        "foreclosure_fee_pct": product["foreclosure_fee_pct"],
    }


def calculate_emi(principal_lakhs: float, annual_rate: float, tenure_years: int) -> dict:
    """Calculate EMI using standard reducing balance formula.
    principal_lakhs: loan amount in LAKHS (e.g. 10 for ₹10 lakh, 500 for ₹5 crore)
    tenure_years: loan tenure in YEARS (e.g. 10, 20, 30)
    """
    principal = principal_lakhs * 1_00_000  # Convert lakhs to INR
    tenure_months = tenure_years * 12
    monthly_rate = annual_rate / 12 / 100
    if monthly_rate == 0:
        emi = principal / tenure_months
    else:
        emi = principal * monthly_rate * (1 + monthly_rate) ** tenure_months / (
            (1 + monthly_rate) ** tenure_months - 1
        )
    total_payment = emi * tenure_months
    total_interest = total_payment - principal

    def _inr_readable(val: float) -> str:
        """Format a number in Indian lakhs/crores for readability."""
        if val >= 1_00_00_000:
            return f"₹{val / 1_00_00_000:.2f} crore"
        elif val >= 1_00_000:
            return f"₹{val / 1_00_000:.2f} lakh"
        else:
            return f"₹{val:,.0f}"

    return {
        "loan_amount": _inr_readable(principal),
        "tenure": f"{tenure_years} years ({tenure_months} months)",
        "interest_rate": f"{annual_rate}%",
        "emi": round(emi, 2),
        "emi_readable": _inr_readable(emi),
        "total_payment": round(total_payment, 2),
        "total_payment_readable": _inr_readable(total_payment),
        "total_interest": round(total_interest, 2),
        "total_interest_readable": _inr_readable(total_interest),
    }


# ── Collateral / Property Assessment ──────────────────────────────────────────

# LTV ratios by property type and city tier (RBI-style guidelines)
# City tier is determined by the agent from city name + pin code — not hardcoded.
_LTV_MATRIX = {
    # (property_type, city_tier) → LTV ratio
    ("residential_apartment", "tier1"): 0.75,
    ("residential_apartment", "tier2"): 0.70,
    ("residential_apartment", "tier3"): 0.65,
    ("independent_house", "tier1"): 0.70,
    ("independent_house", "tier2"): 0.65,
    ("independent_house", "tier3"): 0.60,
    ("villa", "tier1"): 0.70,
    ("villa", "tier2"): 0.65,
    ("villa", "tier3"): 0.60,
    ("plot", "tier1"): 0.60,
    ("plot", "tier2"): 0.55,
    ("plot", "tier3"): 0.50,
    ("commercial", "tier1"): 0.60,
    ("commercial", "tier2"): 0.55,
    ("commercial", "tier3"): 0.50,
    ("under_construction", "tier1"): 0.70,
    ("under_construction", "tier2"): 0.65,
    ("under_construction", "tier3"): 0.60,
}


def assess_collateral(
    property_type: str,
    city: str,
    pin_code: str,
    city_tier: str,
    estimated_value_lakhs: float,
) -> dict:
    """
    Assess collateral for a home loan.
    city_tier is determined by the agent from city name + pin code.
    Accepts: 'tier1', 'tier2', or 'tier3'.
    Returns applicable LTV ratio and max eligible loan amount.
    """
    tier_key = city_tier.strip().lower().replace(" ", "")
    if tier_key in ("tier1", "metro"):
        tier_key = "tier1"
        tier_label = "Tier 1 (Metro)"
    elif tier_key in ("tier2",):
        tier_key = "tier2"
        tier_label = "Tier 2"
    else:
        tier_key = "tier3"
        tier_label = "Tier 3 / Rural"

    prop_key = property_type.strip().lower().replace(" ", "_")
    ltv = _LTV_MATRIX.get((prop_key, tier_key))
    if ltv is None:
        # Fallback: use conservative LTV
        ltv = 0.60
        prop_label = property_type.title()
    else:
        prop_label = property_type.replace("_", " ").title()

    estimated_value = estimated_value_lakhs * 1_00_000
    max_loan = estimated_value * ltv

    def _inr_readable(val: float) -> str:
        if val >= 1_00_00_000:
            return f"₹{val / 1_00_00_000:.2f} crore"
        elif val >= 1_00_000:
            return f"₹{val / 1_00_000:.2f} lakh"
        else:
            return f"₹{val:,.0f}"

    return {
        "property_type": prop_label,
        "city": city.strip().title(),
        "pin_code": pin_code.strip(),
        "city_tier": tier_label,
        "estimated_property_value": _inr_readable(estimated_value),
        "estimated_property_value_lakhs": estimated_value_lakhs,
        "ltv_ratio": ltv,
        "ltv_percentage": f"{int(ltv * 100)}%",
        "max_eligible_loan": _inr_readable(max_loan),
        "max_eligible_loan_lakhs": round(max_loan / 1_00_000, 2),
        "guideline": f"As per bank guidelines, for a {prop_label} in a {tier_label} city, we can finance up to {int(ltv * 100)}% of the property value.",
        "note": "Final loan amount subject to property valuation by our empanelled valuers and title verification.",
    }


# Vehicle-loan funding caps (max loan as a fraction of on-road price).
# The bank NEVER funds 100% of a vehicle — the customer always pays a down payment.
_VEHICLE_LTV = {
    # (condition, fuel_type) → funding cap on on-road price
    ("new", "petrol"): 0.85,
    ("new", "diesel"): 0.85,
    ("new", "ev"): 0.90,   # modest green-financing bump — still NOT 100%
    ("used", "petrol"): 0.70,
    ("used", "diesel"): 0.70,
    ("used", "ev"): 0.75,
}


def assess_vehicle_funding(
    on_road_price_lakhs: float,
    condition: str,
    fuel_type: str,
) -> dict:
    """
    Assess the maximum funding available for a vehicle loan.
    condition: 'new' or 'used'. fuel_type: 'petrol', 'diesel', or 'ev'.
    Returns the applicable funding cap (LTV) and the maximum eligible loan amount.
    The bank NEVER funds 100% of a vehicle — the customer always pays a down payment.
    """
    cond_key = condition.strip().lower()
    if cond_key not in ("new", "used"):
        cond_key = "used"  # conservative default
    fuel_key = fuel_type.strip().lower()
    if fuel_key in ("electric", "ev", "e.v.", "electric vehicle"):
        fuel_key = "ev"
    elif fuel_key not in ("petrol", "diesel"):
        fuel_key = "petrol"  # conservative default

    ltv = _VEHICLE_LTV.get((cond_key, fuel_key), 0.70)
    on_road = on_road_price_lakhs * 1_00_000
    max_loan = on_road * ltv
    down_payment = on_road - max_loan

    def _inr_readable(val: float) -> str:
        if val >= 1_00_00_000:
            return f"₹{val / 1_00_00_000:.2f} crore"
        elif val >= 1_00_000:
            return f"₹{val / 1_00_000:.2f} lakh"
        else:
            return f"₹{val:,.0f}"

    return {
        "condition": cond_key,
        "fuel_type": fuel_key,
        "on_road_price": _inr_readable(on_road),
        "on_road_price_lakhs": on_road_price_lakhs,
        "ltv_ratio": ltv,
        "ltv_percentage": f"{int(ltv * 100)}%",
        "max_eligible_loan": _inr_readable(max_loan),
        "max_eligible_loan_lakhs": round(max_loan / 1_00_000, 2),
        "minimum_down_payment": _inr_readable(down_payment),
        "minimum_down_payment_lakhs": round(down_payment / 1_00_000, 2),
        "guideline": (
            f"For a {cond_key} {fuel_key} vehicle we can finance up to {int(ltv * 100)}% of the "
            f"on-road price. The remaining {int(round((1 - ltv) * 100))}% is the customer's down "
            f"payment. The bank never funds 100%."
        ),
    }


def get_competitor_rates(product_name: str) -> dict:
    """Get competitor bank rates for a loan product for negotiation context."""
    rates = _query(
        "SELECT * FROM competitor_rates WHERE product_name = ?",
        (product_name,),
    )
    if not rates:
        return {"error": f"No competitor data for '{product_name}'"}

    rates_list = []
    for r in rates:
        rates_list.append({
            "bank": r["bank_name"],
            "min_rate": r["min_rate"],
            "max_rate": r["max_rate"],
            "processing_fee_pct": r["processing_fee_pct"],
        })

    min_market = min(r["min_rate"] for r in rates)
    max_market = max(r["max_rate"] for r in rates)

    return {
        "product": product_name,
        "competitor_rates": rates_list,
        "market_range_low": min_market,
        "market_range_high": max_market,
    }


# ─── Savings Agent Tools ─────────────────────────────────────────────────────

def get_account_details(customer_id: str) -> dict:
    """Savings/salary account details: balance, rate, min balance."""
    accounts = _query(
        "SELECT * FROM accounts WHERE customer_id = ?", (customer_id,)
    )
    return {"accounts": accounts}


def get_fixed_deposits(customer_id: str) -> dict:
    """All FDs: amount, rate, maturity, tax-saver flag."""
    fds = _query(
        "SELECT * FROM fixed_deposits WHERE customer_id = ?", (customer_id,)
    )
    return {"fixed_deposits": fds}


def get_recurring_deposits(customer_id: str) -> dict:
    """All RDs: monthly amount, rate, balance, maturity."""
    rds = _query(
        "SELECT * FROM recurring_deposits WHERE customer_id = ?",
        (customer_id,),
    )
    return {"recurring_deposits": rds}


def get_savings_transactions(customer_id: str) -> dict:
    """Recent savings account transactions (last 10)."""
    txns = _query(
        "SELECT date, description, amount, txn_type, category, balance_after "
        "FROM transactions "
        "WHERE customer_id = ? AND account_type = 'savings' "
        "ORDER BY date DESC LIMIT 10",
        (customer_id,),
    )
    return {"transactions": txns}


def get_fd_rate_card() -> dict:
    """Current FD rates by tenure — for advisory and comparison."""
    rates = _query("SELECT * FROM fd_rate_card ORDER BY min_days ASC")
    return {"fd_rates": rates}


# ─── General Banking Agent Tools ──────────────────────────────────────────────

def get_debit_card_details(customer_id: str) -> dict:
    """Debit card: network, type, daily limit, intl status."""
    cards = _query(
        "SELECT * FROM debit_cards WHERE customer_id = ?", (customer_id,)
    )
    return {"debit_cards": cards}


def get_investments(customer_id: str) -> dict:
    """All investments: MF, equity, PPF, NPS, Gold ETF."""
    inv = _query(
        "SELECT * FROM investments WHERE customer_id = ?", (customer_id,)
    )
    return {"investments": inv}


def get_all_transactions(customer_id: str) -> dict:
    """All recent transactions across savings + credit card (last 15)."""
    txns = _query(
        "SELECT account_type, date, description, amount, txn_type, category "
        "FROM transactions "
        "WHERE customer_id = ? "
        "ORDER BY date DESC LIMIT 15",
        (customer_id,),
    )
    return {"transactions": txns}


# ─── CIBIL Score Check (mock) ────────────────────────────────────────────────

def check_cibil_score(customer_id: str) -> dict:
    """
    Simulates a CIBIL bureau pull. Returns the customer's credit score,
    score band, key factors, and recent inquiry count.
    """
    customer = _query_one("SELECT * FROM customers WHERE id = ?", (customer_id,))
    if not customer:
        return {"error": "Customer not found"}

    cibil = customer.get("cibil_score", 0) or 0
    emi_delays = customer.get("emi_delays_12m", 0) or 0
    total_products = customer.get("total_products", 1) or 1

    # Determine score band
    if cibil >= 800:
        band = "Excellent"
    elif cibil >= 750:
        band = "Very Good"
    elif cibil >= 700:
        band = "Good"
    elif cibil >= 650:
        band = "Fair"
    else:
        band = "Poor"

    # Mock key factors
    factors = []
    if emi_delays == 0:
        factors.append("No missed payments in last 12 months")
    else:
        factors.append(f"{emi_delays} payment delay(s) in last 12 months")

    loans = _query(
        "SELECT COUNT(*) as count, SUM(outstanding) as total FROM loans WHERE customer_id = ? AND status = 'Active'",
        (customer_id,),
    )
    active_loans = loans[0]["count"] if loans else 0
    total_outstanding = loans[0]["total"] or 0 if loans else 0

    if active_loans <= 2:
        factors.append(f"Low credit utilization ({active_loans} active loans)")
    else:
        factors.append(f"Multiple active loans ({active_loans}) — moderate utilization")

    if total_products >= 3:
        factors.append(f"Healthy credit mix ({total_products} products)")

    monthly_income = customer.get("monthly_income", 0) or 0
    if monthly_income > 0 and total_outstanding > 0:
        dti = round((total_outstanding / (monthly_income * 12)) * 100, 1)
        factors.append(f"Debt-to-income ratio: {dti}%")

    return {
        "source": "CIBIL TransUnion",
        "report_date": "2026-06-01",
        "cibil_score": cibil,
        "score_band": band,
        "score_range": "300-900",
        "key_factors": factors,
        "active_accounts": active_loans,
        "total_outstanding": total_outstanding,
        "recent_inquiries_90d": 1 if active_loans > 0 else 0,
        "oldest_account_years": round(
            ((__import__("datetime").date.today() - __import__("datetime").date.fromisoformat(customer.get("relationship_since", "2020-01-01"))).days / 365.25), 1
        ),
    }


# ─── RBI Repo Rate Check (mock) ──────────────────────────────────────────────

def check_rbi_repo_rate() -> dict:
    """
    Returns the current RBI repo rate and related policy rates.
    Static mock values for the demo.
    """
    return {
        "source": "Reserve Bank of India",
        "as_of": "2026-06-16",
        "repo_rate": 5.25,
        "reverse_repo_rate": 3.35,
        "marginal_standing_facility_rate": 5.50,
        "bank_rate": 5.50,
        "crr": 4.0,
        "slr": 18.0,
        "last_change": {
            "date": "2025-12-05",
            "action": "Held steady (neutral stance)",
            "previous_rate": 5.25,
        },
        "next_mpc_meeting": "2026-08-05",
        "outlook": "Neutral — rate held to balance growth and inflation",
        "note": "Home loan rates typically benchmarked to repo rate. Current spread: 2.5-4.0% above repo.",
    }


# ─── Collections Agent Tools (Credit Card Recovery) ──────────────────────────

def get_card_dues(customer_id: str) -> dict:
    """
    Current overdue position on the customer's credit card:
    total outstanding, overdue amount, minimum due, days past due (DPD),
    late fee, penal interest, delinquency bucket, and last payment.
    Returns a 'no_dues' flag if the customer is not delinquent.
    """
    row = _query_one(
        "SELECT * FROM collections WHERE customer_id = ?", (customer_id,)
    )
    profile = _query_one("SELECT name FROM customers WHERE id = ?", (customer_id,))
    name = profile["name"] if profile else ""

    if not row:
        # Customer is not delinquent — no dues to recover
        card = _query_one(
            "SELECT card_number_last4, card_type, outstanding, minimum_due, due_date "
            "FROM credit_cards WHERE customer_id = ? LIMIT 1",
            (customer_id,),
        )
        return {
            "customer_name": name,
            "no_dues": True,
            "message": "No overdue amount on record. The account is current.",
            "card_last4": card["card_number_last4"] if card else None,
            "current_outstanding": card["outstanding"] if card else 0,
        }

    total_payable = round(
        (row["overdue_amount"] or 0) + (row["late_fee"] or 0) + (row["penal_interest"] or 0), 2
    )
    return {
        "customer_name": name,
        "no_dues": False,
        "card_last4": row["card_last4"],
        "total_outstanding": row["total_outstanding"],
        "overdue_amount": row["overdue_amount"],
        "minimum_due": row["minimum_due"],
        "days_past_due": row["days_past_due"],
        "late_fee": row["late_fee"],
        "penal_interest": row["penal_interest"],
        "delinquency_bucket": row["bucket"],
        "original_due_date": row["due_date"],
        "last_payment_date": row["last_payment_date"],
        "last_payment_amount": row["last_payment_amount"],
        "total_payable_now": total_payable,
        "status": row["status"],
    }


def get_payment_history(customer_id: str) -> dict:
    """
    Recent payment behaviour for the credit card: last payment made,
    number of EMI/payment delays in the last 12 months, and CIBIL score.
    Helps the collections officer gauge intent-to-pay vs. ability-to-pay.
    """
    row = _query_one(
        "SELECT last_payment_date, last_payment_amount, days_past_due, bucket "
        "FROM collections WHERE customer_id = ?",
        (customer_id,),
    )
    customer = _query_one(
        "SELECT name, emi_delays_12m, cibil_score, monthly_income FROM customers WHERE id = ?",
        (customer_id,),
    )
    if not customer:
        return {"error": "Customer not found"}

    return {
        "customer_name": customer["name"],
        "last_payment_date": row["last_payment_date"] if row else None,
        "last_payment_amount": row["last_payment_amount"] if row else None,
        "days_past_due": row["days_past_due"] if row else 0,
        "delinquency_bucket": row["bucket"] if row else "current",
        "payment_delays_12m": customer["emi_delays_12m"],
        "cibil_score": customer["cibil_score"],
        "monthly_income": customer["monthly_income"],
        "note": "A drop in CIBIL and rising DPD indicate the account needs urgent resolution.",
    }


def get_settlement_options(customer_id: str) -> dict:
    """
    Compute the recovery options the collections officer is authorised to offer,
    gated by the delinquency bucket (days past due). Options may include:
    pay-in-full, minimum-due, EMI conversion, and one-time settlement with
    late-fee / penal-interest waivers. Call this BEFORE offering any waiver.
    """
    row = _query_one(
        "SELECT * FROM collections WHERE customer_id = ?", (customer_id,)
    )
    if not row:
        return {"no_dues": True, "message": "Account is current — no settlement required."}

    overdue = row["overdue_amount"] or 0
    late_fee = row["late_fee"] or 0
    penal = row["penal_interest"] or 0
    dpd = row["days_past_due"] or 0
    bucket = row["bucket"]
    total_payable = round(overdue + late_fee + penal, 2)

    def _emi_plan(principal: float, months: int, annual_rate: float = 18.0) -> dict:
        r = annual_rate / 12 / 100
        emi = principal * r * (1 + r) ** months / ((1 + r) ** months - 1) if r else principal / months
        return {
            "months": months,
            "monthly_emi": round(emi, 2),
            "total_repayment": round(emi * months, 2),
            "interest_rate_pa": annual_rate,
        }

    options = []

    # Always available: pay in full to clear the account
    options.append({
        "option": "pay_in_full",
        "label": "Clear the full outstanding today",
        "amount": total_payable,
        "benefit": "Account marked current immediately; protects your CIBIL score from further damage.",
    })

    # Minimum due keeps the account from worsening (only meaningful early)
    if dpd <= 60:
        options.append({
            "option": "minimum_due",
            "label": "Pay the minimum due to stop further late charges",
            "amount": round((row["minimum_due"] or 0) + late_fee, 2),
            "benefit": "Stops the account from slipping into a worse bucket. Balance continues to attract finance charges.",
        })

    # EMI conversion — convert overdue outstanding into instalments
    options.append({
        "option": "emi_conversion",
        "label": "Convert outstanding into easy EMIs",
        "plans": [_emi_plan(overdue, 3), _emi_plan(overdue, 6), _emi_plan(overdue, 12)],
        "benefit": "Spreads the burden into affordable monthly instalments.",
    })

    # One-time settlement with waivers — authorised only for deeper buckets
    authorised_waivers = {}
    if dpd >= 30:
        authorised_waivers["late_fee_waiver"] = late_fee
    if dpd >= 60:
        authorised_waivers["penal_interest_waiver"] = penal
    if authorised_waivers:
        settle_amount = round(overdue + max(0, penal - authorised_waivers.get("penal_interest_waiver", 0)), 2)
        options.append({
            "option": "one_time_settlement",
            "label": "One-time settlement with fee waiver",
            "waivers_authorised": authorised_waivers,
            "settlement_amount_if_paid_now": settle_amount,
            "condition": "Valid only if paid in full within 7 days.",
            "benefit": "Waive late fee"
            + (" and penal interest" if "penal_interest_waiver" in authorised_waivers else "")
            + " if the settlement amount is paid promptly.",
        })

    return {
        "customer_id": customer_id,
        "delinquency_bucket": bucket,
        "days_past_due": dpd,
        "total_payable_now": total_payable,
        "authorised_options": options,
        "_INTERNAL_do_not_disclose": {
            "max_late_fee_waiver": late_fee if dpd >= 30 else 0,
            "max_penal_waiver": penal if dpd >= 60 else 0,
            "note": "Do NOT offer waivers beyond authorised buckets. Never volunteer a settlement before attempting full/minimum recovery.",
        },
    }


def record_payment_commitment(
    customer_id: str, amount: float, promise_date: str, method: str = "UPI/link"
) -> dict:
    """
    Record a Promise-to-Pay (PTP) from the customer: the amount they commit to
    pay and the date. Returns a reference number for follow-up. Call this only
    AFTER the customer verbally commits to a specific amount and date.
    """
    import datetime, random
    ref = "PTP-" + datetime.date.today().strftime("%Y%m%d") + "-" + str(random.randint(1000, 9999))
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute(
        "INSERT INTO payment_promises (customer_id, amount, promise_date, method, reference, created_at) "
        "VALUES (?,?,?,?,?,?)",
        (customer_id, amount, promise_date, method, ref, datetime.datetime.now().isoformat(timespec="seconds")),
    )
    conn.commit()
    conn.close()
    return {
        "status": "recorded",
        "reference": ref,
        "amount": amount,
        "promise_date": promise_date,
        "method": method,
        "message": f"Promise-to-pay of ₹{amount:,.0f} by {promise_date} recorded. Reference {ref}.",
    }


def send_payment_link(customer_id: str, amount: float) -> dict:
    """
    Send a secure payment link / UPI collect request to the customer's
    registered mobile and email for the given amount. Returns a mock link
    and reference. Use after the customer agrees to pay now or shortly.
    """
    import random
    customer = _query_one("SELECT phone, email FROM customers WHERE id = ?", (customer_id,))
    ref = "PAY-" + str(random.randint(100000, 999999))
    return {
        "status": "sent",
        "reference": ref,
        "amount": amount,
        "sent_to_phone": customer["phone"] if customer else None,
        "sent_to_email": customer["email"] if customer else None,
        "link": f"https://pay.contosobank.example/collect/{ref}",
        "expires_in_hours": 24,
        "message": f"A secure payment link for ₹{amount:,.0f} has been sent to the customer's registered mobile and email.",
    }


# ─── Collections Agent Tools (Vehicle Loan Recovery) ─────────────────────────

def get_loan_dues(customer_id: str) -> dict:
    """
    Current overdue position on the customer's VEHICLE loan:
    loan id, vehicle, EMI amount, EMIs overdue, total overdue amount,
    days past due (DPD), late fee, penal interest, outstanding balance,
    delinquency bucket, and last payment.
    Returns a 'no_dues' flag if the customer has no overdue vehicle loan.
    """
    row = _query_one(
        "SELECT * FROM loan_collections WHERE customer_id = ?", (customer_id,)
    )
    profile = _query_one("SELECT name FROM customers WHERE id = ?", (customer_id,))
    name = profile["name"] if profile else ""

    if not row:
        return {
            "customer_name": name,
            "no_dues": True,
            "message": "No overdue vehicle loan on record. The loan account is current.",
        }

    total_payable = round(
        (row["overdue_amount"] or 0) + (row["late_fee"] or 0) + (row["penal_interest"] or 0), 2
    )
    return {
        "customer_name": name,
        "no_dues": False,
        "loan_id": row["loan_id"],
        "loan_type": row["loan_type"],
        "vehicle": row["vehicle"],
        "total_outstanding": row["total_outstanding"],
        "emi_amount": row["emi_amount"],
        "emis_overdue": row["emis_overdue"],
        "overdue_amount": row["overdue_amount"],
        "days_past_due": row["days_past_due"],
        "late_fee": row["late_fee"],
        "penal_interest": row["penal_interest"],
        "delinquency_bucket": row["bucket"],
        "original_due_date": row["due_date"],
        "last_payment_date": row["last_payment_date"],
        "last_payment_amount": row["last_payment_amount"],
        "total_payable_now": total_payable,
        "status": row["status"],
    }


def get_loan_settlement_options(customer_id: str) -> dict:
    """
    Compute the recovery options the officer is authorised to offer on an
    overdue vehicle loan, gated by the delinquency bucket (days past due).
    Options may include: clear all overdue EMIs, pay the oldest EMI to arrest
    the account, tenure extension / restructure to lower the EMI, and one-time
    regularisation with late-fee / penal-interest waivers. Call this BEFORE
    offering any waiver, restructure, or EMI plan.
    """
    row = _query_one(
        "SELECT * FROM loan_collections WHERE customer_id = ?", (customer_id,)
    )
    if not row:
        return {"no_dues": True, "message": "Vehicle loan account is current — no settlement required."}

    emi = row["emi_amount"] or 0
    emis_overdue = row["emis_overdue"] or 0
    overdue = row["overdue_amount"] or 0
    late_fee = row["late_fee"] or 0
    penal = row["penal_interest"] or 0
    dpd = row["days_past_due"] or 0
    outstanding = row["total_outstanding"] or 0
    bucket = row["bucket"]
    total_payable = round(overdue + late_fee + penal, 2)

    options = []

    # Always available: clear all overdue EMIs + charges to regularise the loan
    options.append({
        "option": "clear_all_overdue",
        "label": "Clear all overdue EMIs and charges today",
        "amount": total_payable,
        "benefit": "Loan marked regular immediately; stops further penal interest and protects your CIBIL score.",
    })

    # Pay the oldest single EMI to arrest the account (meaningful early)
    if emis_overdue >= 2 and dpd <= 90:
        options.append({
            "option": "pay_one_emi",
            "label": "Pay at least the oldest overdue EMI now",
            "amount": round(emi, 2),
            "benefit": "Reduces days-past-due and stops the account slipping into a worse bucket.",
        })

    # Restructure / tenure extension to lower the EMI — for genuine hardship
    def _restructure(extra_months: int, annual_rate: float = 9.5) -> dict:
        r = annual_rate / 12 / 100
        # crude re-amortisation of the outstanding over a longer remaining tenure
        base_months = 48
        months = base_months + extra_months
        new_emi = outstanding * r * (1 + r) ** months / ((1 + r) ** months - 1) if r else outstanding / months
        return {"extend_by_months": extra_months, "revised_emi": round(new_emi, 2)}

    options.append({
        "option": "restructure_tenure",
        "label": "Extend the tenure to lower your monthly EMI",
        "plans": [_restructure(12), _restructure(24)],
        "benefit": "Lowers the monthly instalment to fit your cash flow. Subject to approval; total interest rises.",
    })

    # One-time regularisation with waivers — authorised only for deeper buckets
    authorised_waivers = {}
    if dpd >= 30:
        authorised_waivers["late_fee_waiver"] = late_fee
    if dpd >= 60:
        authorised_waivers["penal_interest_waiver"] = penal
    if authorised_waivers:
        settle_amount = round(
            overdue + max(0, penal - authorised_waivers.get("penal_interest_waiver", 0)), 2
        )
        options.append({
            "option": "one_time_regularisation",
            "label": "Regularise now with fee waiver",
            "waivers_authorised": authorised_waivers,
            "amount_if_paid_now": settle_amount,
            "condition": "Prompt-payment offer: pay the FULL overdue amount within 7 days FROM TODAY to get this waiver. This does NOT depend on how many days the account is already past due.",
            "benefit": "Waive late fee"
            + (" and penal interest" if "penal_interest_waiver" in authorised_waivers else "")
            + " if the amount is paid promptly.",
        })

    return {
        "customer_id": customer_id,
        "delinquency_bucket": bucket,
        "days_past_due": dpd,
        "emi_amount": emi,
        "emis_overdue": emis_overdue,
        "total_payable_now": total_payable,
        "authorised_options": options,
        "_INTERNAL_do_not_disclose": {
            "max_late_fee_waiver": late_fee if dpd >= 30 else 0,
            "max_penal_waiver": penal if dpd >= 60 else 0,
            "note": "Do NOT offer waivers beyond authorised buckets. Attempt full/oldest-EMI recovery before volunteering a restructure or waiver.",
        },
    }


# ─── Life Insurance Premium Recovery (Persistency) Tools ─────────────────────

_FREQ_PER_YEAR = {"Monthly": 12, "Quarterly": 4, "Half-Yearly": 2, "Annual": 1}


def get_policy_dues(customer_id: str) -> dict:
    """
    Current premium position on the customer's EXISTING life insurance policy:
    plan, sum assured, premium amount + frequency, premiums paid/overdue, days
    past due, grace-period / lapse / revival status, the amount payable now, and
    the benefits currently AT RISK (life cover + any accrued value). Call this
    FIRST on a premium-recovery call. Returns 'no_policy' if the customer holds
    no policy with us (i.e. they are only a sales prospect).
    """
    row = _query_one("SELECT * FROM life_policies WHERE customer_id = ?", (customer_id,))
    profile = _query_one("SELECT name FROM customers WHERE id = ?", (customer_id,))
    name = profile["name"] if profile else ""

    if not row:
        return {
            "customer_name": name,
            "no_policy": True,
            "message": "No life insurance policy on record for this customer.",
        }

    overdue = row["total_overdue_amount"] or 0
    interest = row["revival_interest"] or 0
    status = row["status"]
    total_payable = round(overdue + interest, 2)

    return {
        "customer_name": name,
        "no_policy": False,
        "policy_number": row["policy_number"],
        "plan_name": row["plan_name"],
        "plan_type": row["plan_type"],
        "sum_assured": row["sum_assured"],
        "premium_amount": row["premium_amount"],
        "premium_frequency": row["premium_frequency"],
        "premiums_paid": row["premiums_paid"],
        "premiums_overdue": row["premiums_overdue"],
        "total_overdue_amount": overdue,
        "revival_interest": interest,
        "total_payable_now": total_payable,
        "days_past_due": row["days_past_due"],
        "grace_period_days": row["grace_period_days"],
        "grace_end_date": row["grace_end_date"],
        "revival_end_date": row["revival_end_date"],
        "policy_status": status,  # 'In Grace' or 'Lapsed'
        "benefits_at_risk": {
            "life_cover": row["sum_assured"],
            "accrued_value": row["accrued_benefit"] or 0,
            "note": (
                "Cover is currently INACTIVE — a claim would NOT be paid until revived."
                if status == "Lapsed"
                else "Cover is still active during the grace period, but will STOP if the premium is not paid by the grace end date."
            ),
        },
        "last_payment_date": row["last_payment_date"],
        "last_payment_amount": row["last_payment_amount"],
        "status_note": (
            "LAPSED — pay the arrears plus interest within the revival window to restore cover."
            if status == "Lapsed"
            else "IN GRACE — pay the pending premium before the grace end date to keep the policy in-force with no interest."
        ),
    }


def get_revival_options(customer_id: str) -> dict:
    """
    Compute the options authorised to bring an overdue life policy back on track,
    gated by whether it is still in the grace period or has lapsed. Options may
    include: pay the pending premium now (in grace, no interest), revive a lapsed
    policy (arrears + interest, possibly a health declaration), switch premium
    frequency to lower each instalment, set up auto-debit to avoid future misses,
    and — for savings plans with enough premiums paid — make the policy paid-up.
    Call this BEFORE proposing any option. This is persistency, NOT debt recovery:
    the customer's OWN cover and money are at stake, so guide, never pressure.
    """
    row = _query_one("SELECT * FROM life_policies WHERE customer_id = ?", (customer_id,))
    if not row:
        return {"no_policy": True, "message": "No life insurance policy on record."}

    status = row["status"]
    premium = row["premium_amount"] or 0
    freq = row["premium_frequency"] or "Annual"
    overdue = row["total_overdue_amount"] or 0
    interest = row["revival_interest"] or 0
    plan_type = row["plan_type"]
    premiums_paid = row["premiums_paid"] or 0

    per_year = _FREQ_PER_YEAR.get(freq, 1)
    annual_premium = premium * per_year
    monthly_equiv = round(annual_premium / 12, 2)

    options = []

    if status == "In Grace":
        options.append({
            "option": "pay_pending_premium",
            "label": "Pay the pending premium now (before the grace end date)",
            "amount": round(overdue, 2),
            "benefit": "Keeps the policy in-force with NO interest or penalty; your cover simply continues uninterrupted.",
        })
    else:  # Lapsed
        options.append({
            "option": "revive_policy",
            "label": "Revive the policy to restore full cover",
            "amount": round(overdue + interest, 2),
            "requires": "A simple declaration of good health (a medical check may be needed only for large gaps).",
            "condition": f"Revivable until {row['revival_end_date']}.",
            "benefit": "Restores your full life cover and all accrued benefits, as if the policy never stopped.",
        })

    # Easier future payments — always offer if not already monthly
    if freq != "Monthly":
        options.append({
            "option": "switch_to_monthly",
            "label": "Switch to monthly premiums to make each instalment smaller",
            "current": f"{freq}: ₹{premium:,.0f} per instalment",
            "proposed": f"Monthly: about ₹{monthly_equiv:,.0f} per month",
            "benefit": "Smaller, more manageable amounts so a payment is less likely to be missed.",
        })
    options.append({
        "option": "setup_auto_debit",
        "label": "Set up auto-debit / NACH so premiums are never missed again",
        "benefit": "Automatic on-time payment protects your cover and your accrued benefits going forward.",
    })

    # Savings-type plans with enough premiums paid can be made paid-up as a last resort
    if plan_type in ("savings", "child", "retirement") and premiums_paid >= 8:
        options.append({
            "option": "reduced_paid_up",
            "label": "Make the policy 'paid-up' if you truly cannot continue",
            "benefit": "Stops future premiums; you keep a reduced guaranteed cover and the value already built — better than surrendering.",
            "caveat": "Cover and maturity value reduce. Offer this ONLY if the customer genuinely cannot continue premiums.",
        })

    return {
        "customer_id": customer_id,
        "policy_status": status,
        "plan_type": plan_type,
        "total_payable_now": round(overdue + (interest if status == "Lapsed" else 0), 2),
        "authorised_options": options,
        "_INTERNAL_do_not_disclose": {
            "note": "Lead with keeping the cover intact (pay pending / revive). Offer paid-up or premium reduction ONLY on genuine affordability hardship — never volunteer it first.",
        },
    }


# ─── Human escalation / hand-off (email) ──────────────────────────────────────

def _write_outbox(to_addr: str, subject: str, body: str, error: str = "") -> Path:
    """Persist a composed email to the local outbox/ folder (simulated send)."""
    import datetime

    outbox = Path(__file__).resolve().parent / "outbox"
    outbox.mkdir(exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    path = outbox / f"{stamp}.txt"
    header = (
        f"To: {to_addr}\n"
        f"Subject: {subject}\n"
        + (f"X-Delivery-Error: {error}\n" if error else "")
        + "\n"
    )
    path.write_text(header + body, encoding="utf-8")
    return path


def _deliver_via_acs(to_addr: str, subject: str, body: str) -> str:
    """
    Send an email through Azure Communication Services (ACS) Email.

    Auth priority mirrors the rest of the app:
      1. ACS_EMAIL_CONNECTION_STRING  (endpoint=...;accesskey=...)
      2. ACS_EMAIL_ENDPOINT + Managed Identity / Azure CLI credential
    The verified MailFrom address must be set in ACS_EMAIL_SENDER
    (e.g. DoNotReply@<guid>.azurecomm.net or notify@yourdomain.com).

    Raises on any failure so the caller can fall back to SMTP / outbox.
    """
    from azure.communication.email import EmailClient  # lazy import

    sender = os.getenv("ACS_EMAIL_SENDER", "").strip()
    conn = os.getenv("ACS_EMAIL_CONNECTION_STRING", "").strip()
    endpoint = os.getenv("ACS_EMAIL_ENDPOINT", "").strip()

    if conn:
        client = EmailClient.from_connection_string(conn)
    else:
        from azure.identity import DefaultAzureCredential  # lazy import

        client = EmailClient(endpoint, DefaultAzureCredential())

    message = {
        "senderAddress": sender,
        "recipients": {"to": [{"address": to_addr}]},
        "content": {"subject": subject, "plainText": body},
    }
    poller = client.begin_send(message)
    result = poller.result()
    status = getattr(result, "status", None) or (
        result.get("status") if isinstance(result, dict) else None
    )
    msg_id = getattr(result, "id", None) or (
        result.get("id") if isinstance(result, dict) else None
    )
    return f"sent via ACS (status={status}, id={msg_id})"


def _deliver_email(to_addr: str, subject: str, body: str) -> str:
    """
    Deliver an escalation email using the first configured backend:
      1. Azure Communication Services  (ACS_EMAIL_SENDER + connection string/endpoint)
      2. SMTP                          (SMTP_HOST)
      3. Simulated outbox              (server/outbox/*.txt)
    Returns a delivery status string. Never raises — a failed send falls back to
    the outbox so the handoff is never silently lost.
    """
    # ── 1. Azure Communication Services ──
    acs_sender = os.getenv("ACS_EMAIL_SENDER", "").strip()
    acs_conn = os.getenv("ACS_EMAIL_CONNECTION_STRING", "").strip()
    acs_endpoint = os.getenv("ACS_EMAIL_ENDPOINT", "").strip()
    if acs_sender and (acs_conn or acs_endpoint):
        try:
            return _deliver_via_acs(to_addr, subject, body)
        except Exception as exc:
            path = _write_outbox(to_addr, subject, body, error=f"ACS: {exc}")
            return f"acs_error, written to outbox {path} ({exc})"

    # ── 2. SMTP ──
    host = os.getenv("SMTP_HOST", "").strip()
    if not host:
        path = _write_outbox(to_addr, subject, body)
        return f"simulated (no ACS/SMTP configured) — written to {path}"
    try:
        msg = EmailMessage()
        msg["From"] = os.getenv("SMTP_FROM", os.getenv("SMTP_USER", "noreply@contosobank.example"))
        msg["To"] = to_addr
        msg["Subject"] = subject
        msg.set_content(body)
        port = int(os.getenv("SMTP_PORT", "587"))
        use_tls = os.getenv("SMTP_USE_TLS", "true").lower() != "false"
        with smtplib.SMTP(host, port, timeout=15) as smtp:
            if use_tls:
                smtp.starttls()
            user = os.getenv("SMTP_USER", "")
            pwd = os.getenv("SMTP_PASSWORD", "")
            if user and pwd:
                smtp.login(user, pwd)
            smtp.send_message(msg)
        return "sent"
    except Exception as exc:
        path = _write_outbox(to_addr, subject, body, error=str(exc))
        return f"smtp_error, written to outbox {path} ({exc})"


def send_escalation(
    customer_id: str,
    campaign: str,
    agent: str,
    reason: str,
    summary: str,
    action_items=None,
    priority: str = "medium",
    escalation_type: str = "unresolved",
    transcript=None,
) -> dict:
    """
    Hand a call off to a human senior officer: e-mail the human agent a call
    summary + action items + transcript, persist an escalation record to the
    DB, and return a reference ticket. Used both for unresolved escalations
    (frustrated customer / human required) and resolved hand-offs (a human
    needs to action the next steps).
    """
    import datetime
    import random

    priority = (priority or "medium").lower()
    if priority not in ("low", "medium", "high"):
        priority = "medium"
    if escalation_type not in ("unresolved", "resolved_handoff"):
        escalation_type = "unresolved"

    # Defensive: the model sometimes echoes the escalation_type enum into the
    # free-text reason. Don't let that leak into the human's email.
    reason = (reason or "").strip()
    if reason.lower().replace(" ", "_") in ("resolved_handoff", "unresolved", ""):
        reason = (
            "Call resolved — needs a human to action the next steps."
            if escalation_type == "resolved_handoff"
            else "Customer needs human assistance."
        )

    profile = get_customer_profile(customer_id)
    if not isinstance(profile, dict) or profile.get("error"):
        profile = {}
    customer_name = profile.get("name", customer_id)

    created_at = datetime.datetime.now().isoformat(timespec="seconds")
    ref = "ESC-" + datetime.date.today().strftime("%Y%m%d") + "-" + str(random.randint(1000, 9999))

    # ── Normalise action items into a bullet list ──
    if isinstance(action_items, (list, tuple)):
        items = [str(a).strip() for a in action_items if str(a).strip()]
    elif action_items:
        items = [s.strip(" -•\t") for s in re.split(r"[\n;]+", str(action_items)) if s.strip()]
    else:
        items = []
    action_md = "\n".join(f"  - {a}" for a in items) if items else "  - (none specified)"

    # ── Readable transcript block ──
    # Accepts (role, text) pairs, {"role":..,"text":..} dicts, or plain strings.
    t_lines = []
    for entry in (transcript or []):
        role, text = None, None
        if isinstance(entry, (list, tuple)) and len(entry) == 2:
            role, text = entry
        elif isinstance(entry, dict):
            role = entry.get("role")
            text = entry.get("text") or entry.get("content")
        else:
            text = str(entry)
        if not text:
            continue
        speaker = "Customer" if role == "user" else (agent or "Agent")
        t_lines.append(f"  {speaker}: {text}")
    transcript_text = "\n".join(t_lines) if t_lines else "  (no transcript captured)"

    kind = "ESCALATION" if escalation_type == "unresolved" else "RESOLVED HAND-OFF"
    subject = f"[{kind} · {priority.upper()}] {campaign} — {customer_name} ({ref})"
    body = (
        f"{kind} — reference {ref}\n"
        f"Created:      {created_at}\n"
        f"Priority:     {priority.upper()}\n"
        f"Campaign:     {campaign}\n"
        f"Handled by:   {agent} (AI voice agent)\n"
        f"\n"
        f"CUSTOMER\n"
        f"  Name:        {customer_name}\n"
        f"  Customer ID: {customer_id}\n"
        f"  Segment:     {profile.get('segment', 'N/A')}\n"
        f"  City:        {profile.get('city', 'N/A')}\n"
        f"  Phone:       {profile.get('phone', 'N/A')}\n"
        f"\n"
        f"REASON\n  {reason or '(not specified)'}\n"
        f"\n"
        f"CALL SUMMARY\n  {summary or '(not specified)'}\n"
        f"\n"
        f"ACTION ITEMS / NEXT STEPS\n{action_md}\n"
        f"\n"
        f"CALL TRANSCRIPT\n{transcript_text}\n"
    )

    delivery = _deliver_email(ESCALATION_EMAIL, subject, body)

    # ── Persist escalation record (table created lazily — no reseed needed) ──
    try:
        conn = sqlite3.connect(str(DB_PATH))
        conn.execute(
            """CREATE TABLE IF NOT EXISTS escalations (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                reference      TEXT,
                customer_id    TEXT,
                customer_name  TEXT,
                campaign       TEXT,
                agent          TEXT,
                escalation_type TEXT,
                reason         TEXT,
                summary        TEXT,
                action_items   TEXT,
                priority       TEXT,
                transcript     TEXT,
                email_to       TEXT,
                delivery       TEXT,
                status         TEXT,
                created_at     TEXT
            )"""
        )
        conn.execute(
            "INSERT INTO escalations (reference, customer_id, customer_name, campaign, agent, "
            "escalation_type, reason, summary, action_items, priority, transcript, email_to, "
            "delivery, status, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                ref, customer_id, customer_name, campaign, agent, escalation_type,
                reason, summary, "\n".join(items),
                priority,
                "\n".join(f"{r}: {t}" for r, t in (transcript or [])),
                ESCALATION_EMAIL, delivery, "open", created_at,
            ),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass  # persistence is best-effort; the email is the primary handoff

    return {
        "reference": ref,
        "priority": priority,
        "escalation_type": escalation_type,
        "customer_name": customer_name,
        "email_to": ESCALATION_EMAIL,
        "delivery": delivery,
        "created_at": created_at,
        "message": "Escalation e-mailed to the human agent. A senior officer will follow up.",
    }


# ─── Life Insurance ───────────────────────────────────────────────────────────

# Static product catalogue. Generic protection/savings/investment families —
# not tied to any specific insurer. Returns only customer-facing benefit copy.
_INSURANCE_PLANS = {
    "protection": {
        "family": "Protection (Term Life)",
        "purpose": "Pure life cover — the most affordable way to secure your family's income if something happens to you.",
        "key_benefits": [
            "High life cover at a low premium (best value for money)",
            "Cover for your family's living costs, EMIs and future goals",
            "Optional riders: accidental death, critical illness, terminal illness",
            "Level premium locked for the whole term",
        ],
        "best_for": "The primary breadwinner, anyone with dependents or an outstanding loan.",
        "return_nature": "No maturity payout — this is protection, not investment.",
    },
    "savings": {
        "family": "Guaranteed Savings (Endowment)",
        "purpose": "Disciplined long-term savings with life cover and a guaranteed* maturity benefit.",
        "key_benefits": [
            "Guaranteed* lump sum or regular income on maturity",
            "Life cover throughout the policy term",
            "Ideal for goal-based saving (a home, a wedding, a corpus)",
            "Steady, low-risk growth — not linked to markets",
        ],
        "best_for": "Conservative savers who want protection plus assured returns.",
        "return_nature": "Guaranteed/assured benefits (*as per plan terms) — low risk.",
    },
    "ulip": {
        "family": "Market-Linked (ULIP)",
        "purpose": "Grow wealth through market-linked funds while keeping a life cover.",
        "key_benefits": [
            "Choice of equity/debt/balanced funds with free switches",
            "Potential for higher, market-linked growth over the long term",
            "Life cover bundled with investment",
            "Tax-efficient long-term wealth creation",
        ],
        "best_for": "Customers comfortable with market risk and a long horizon.",
        "return_nature": "MARKET-LINKED and NOT guaranteed — returns can go up or down.",
    },
    "child": {
        "family": "Child Plan",
        "purpose": "Build a guaranteed corpus for your child's education/future, protected even if you're not around.",
        "key_benefits": [
            "Payouts timed to education milestones",
            "Premium waiver — the plan continues even if the parent passes away",
            "Guaranteed* or market-linked options",
        ],
        "best_for": "Parents planning for a child's higher education or wedding.",
        "return_nature": "Depends on variant chosen (guaranteed* or market-linked).",
    },
    "retirement": {
        "family": "Retirement / Pension",
        "purpose": "Build a retirement corpus now and convert it into a regular pension for life.",
        "key_benefits": [
            "Regular guaranteed* income after retirement",
            "Deferred or immediate annuity options",
            "Helps beat inflation over a long saving horizon",
        ],
        "best_for": "Anyone who wants a dependable income after they stop working.",
        "return_nature": "Guaranteed* annuity income (*as per plan terms).",
    },
}


def get_insurance_plans(category: str | None = None) -> dict:
    """Life insurance product catalogue. One family if category given, else all."""
    if category:
        plan = _INSURANCE_PLANS.get(category)
        if not plan:
            return {"error": f"Unknown category '{category}'", "available": list(_INSURANCE_PLANS)}
        return plan
    return {
        "families": list(_INSURANCE_PLANS.values()),
        "note": "Match the family to the customer's need. Never overstate returns — ULIP and market-linked variants are NOT guaranteed.",
    }


def _age_from_dob(dob: str | None) -> int | None:
    """Whole-years age from an ISO 'YYYY-MM-DD' date of birth."""
    from datetime import date
    if not dob:
        return None
    try:
        d = date.fromisoformat(dob)
    except (ValueError, TypeError):
        return None
    today = date.today()
    return today.year - d.year - ((today.month, today.day) < (d.month, d.day))


def get_cover_recommendation(customer_id: str) -> dict:
    """
    Human Life Value (HLV) assessment: how much life cover the family needs.
    Uses annual income (income-replacement multiple by age band) + outstanding
    liabilities. Returns recommended cover and the likely protection gap.
    """
    customer = _query_one(
        "SELECT name, dob, monthly_income FROM customers WHERE id = ?", (customer_id,)
    )
    if not customer:
        return {"error": "Customer not found"}

    monthly_income = customer.get("monthly_income") or 0
    annual_income = monthly_income * 12
    age = _age_from_dob(customer.get("dob"))

    # Income-replacement multiple falls as you age (fewer earning years left).
    if age is None:
        multiple = 12
    elif age < 35:
        multiple = 15
    elif age < 45:
        multiple = 12
    else:
        multiple = 10

    income_cover = annual_income * multiple

    # Add outstanding liabilities so cover clears debts too.
    loans = _query(
        "SELECT outstanding FROM loans WHERE customer_id = ? AND status = 'Active'",
        (customer_id,),
    )
    liabilities = sum(l["outstanding"] for l in loans)

    # No policies held in this demo CRM → assume no existing cover.
    existing_cover = 0
    recommended_total = income_cover + liabilities
    protection_gap = max(0, recommended_total - existing_cover)

    def _to_lakhs(v: float) -> float:
        return round(v / 100000, 1)

    # Insurance is sold in round cover slabs, never odd figures like ₹1,96,00,000.
    # Round to the nearest ₹25 lakh and write it as a full Indian-format numeral
    # (₹2,00,00,000), NOT as "crore"/"lakh" words.
    def _round_slab(v: float) -> int:
        slab = 2_500_000  # ₹25 lakh
        return int(round(v / slab) * slab) if v > 0 else 0

    def _indian_commas(n: float) -> str:
        s = str(int(round(n)))
        if len(s) <= 3:
            return s
        last3 = s[-3:]
        rest = re.sub(r"(?<=\d)(?=(\d\d)+$)", ",", s[:-3])
        return rest + "," + last3

    def _spoken(v: float) -> str:
        return f"₹{_indian_commas(v)}" if v > 0 else "₹0"

    recommended_rounded = _round_slab(recommended_total)

    return {
        "age": age,
        "annual_income": annual_income,
        "income_multiple": multiple,
        # Internal components — for records/underwriting ONLY, do NOT read these
        # out to the customer (no liability itemisation, no exact figures).
        "income_replacement_cover_INTERNAL": income_cover,
        "outstanding_liabilities_INTERNAL": liabilities,
        "existing_cover": existing_cover,
        "recommended_total_cover_exact_INTERNAL": recommended_total,
        # This is the ONLY figure to speak — a clean, round cover slab.
        "recommended_cover_rounded": recommended_rounded,
        "recommended_cover_spoken": f"around {_spoken(recommended_rounded)}",
        "protection_gap": protection_gap,
        "protection_gap_lakhs": _to_lakhs(protection_gap),
        "note": (
            "Speak ONLY 'recommended_cover_spoken' — a ROUND figure written in full Indian "
            "rupees, e.g. 'around ₹2,00,00,000' (NOT 'crore'/'lakh' words). NEVER read out the "
            "exact amount, the income-multiple arithmetic, or the liabilities/loans breakdown "
            "(all *_INTERNAL). Justify it simply, e.g. 'roughly what your family would need to "
            "maintain their lifestyle and stay secure'."
        ),
    }


def _base_annual_premium(
    age_used: int, cover_lakhs: float, term_years: int, plan_type: str
) -> tuple[float, list[str]]:
    """Shared base indicative annual premium + plan notes (pre-concession)."""
    sum_assured = cover_lakhs * 100000
    plan_type = (plan_type or "").lower()
    notes: list[str] = []

    if plan_type == "term":
        # Pure protection: cheap, level premium. Mortality-driven by age.
        rate_per_lakh = 45 + max(0, age_used - 25) * 7
        annual = cover_lakhs * rate_per_lakh
        notes.append("Pure term cover — no maturity payout; premium buys protection only.")
    elif plan_type == "ulip":
        # Market-linked: min sum assured is typically ~10x the annual premium.
        annual = sum_assured / 10
        notes.append("Market-linked (ULIP): returns are NOT guaranteed and depend on fund performance.")
        notes.append("Sum assured is typically ~10× the annual premium; final allocation charges apply.")
    else:
        # savings / child / retirement: accumulate roughly the sum assured over the
        # term, with a small load and a mild age loading.
        term = max(1, term_years)
        base = sum_assured / term
        annual = base * 1.05 * (1 + max(0, age_used - 30) * 0.005)
        if plan_type == "savings":
            notes.append("Guaranteed savings/endowment — includes life cover plus a guaranteed* maturity benefit.")
        elif plan_type == "child":
            notes.append("Child plan — builds a corpus for the child; includes a premium-waiver on the parent.")
        elif plan_type == "retirement":
            notes.append("Retirement plan — accumulates a corpus to convert into a pension later.")
        else:
            notes.append("Savings-type plan.")

    return annual, notes


def calculate_insurance_premium(
    customer_id: str,
    cover_lakhs: float,
    term_years: int,
    plan_type: str,
) -> dict:
    """
    Indicative annual/monthly premium for a life plan. Age is derived from the
    customer's profile (never asked). Figures are indicative, pre-underwriting,
    and exclusive of applicable taxes.
    """
    customer = _query_one("SELECT dob FROM customers WHERE id = ?", (customer_id,))
    if not customer:
        return {"error": "Customer not found"}

    age = _age_from_dob(customer.get("dob"))
    age_used = age if age is not None else 35  # safe default if DOB missing

    annual, notes = _base_annual_premium(age_used, cover_lakhs, term_years, plan_type)

    annual = int(round(annual / 100.0) * 100)  # round to nearest ₹100
    monthly = int(round((annual / 12) / 10.0) * 10)  # round to nearest ₹10

    return {
        "plan_type": (plan_type or "").lower(),
        "age_used": age_used,
        "cover_lakhs": cover_lakhs,
        "term_years": term_years,
        "annual_premium": annual,
        "monthly_premium": monthly,
        "notes": notes,
        "disclaimer": (
            "Indicative only — subject to underwriting, health declarations and final board-approved "
            "rates; exclusive of applicable taxes. Do NOT present as a final or guaranteed quote. The "
            "FINAL premium is confirmed only AFTER the medical check-up."
        ),
    }


# Maximum total premium concession the agent may offer, by plan family. Savings
# and market-linked plans carry less margin than pure protection, so they flex
# less. This is the CEILING on any discount — the agent must never go below the
# resulting floor premium.
_MAX_PREMIUM_DISCOUNT_PCT = {
    "term": 15.0,
    "savings": 8.0,
    "child": 8.0,
    "retirement": 8.0,
    "ulip": 5.0,
}


def get_premium_negotiation(
    customer_id: str,
    cover_lakhs: float,
    term_years: int,
    plan_type: str,
) -> dict:
    """
    The AUTHORISED premium-concession band for a life plan: the base indicative
    premium, staged concession ROUNDS the agent may offer (each tied to a real
    lever), and the FLOOR premium below which the agent must never go. Call this
    BEFORE negotiating on price so every concession is grounded and bounded —
    never invent a discount or a lower premium. Concede round by round; use a
    hold ("check with underwriting") before offering the best (final) round.
    """
    customer = _query_one("SELECT dob FROM customers WHERE id = ?", (customer_id,))
    if not customer:
        return {"error": "Customer not found"}

    age = _age_from_dob(customer.get("dob"))
    age_used = age if age is not None else 35
    ptype = (plan_type or "").lower()

    base_annual_f, _ = _base_annual_premium(age_used, cover_lakhs, term_years, ptype)
    max_pct = _MAX_PREMIUM_DISCOUNT_PCT.get(ptype, 8.0)

    def _r100(v: float) -> int:
        return int(round(v / 100.0) * 100)

    def _m10(annual: float) -> int:
        return int(round((annual / 12) / 10.0) * 10)

    base_annual = _r100(base_annual_f)

    # Staged rounds — cumulative discount, capped at max_pct (the ceiling).
    # Round 1 ≈ a third of the band, Round 2 ≈ two-thirds, Round 3 = floor.
    r1_pct = round(max_pct * 0.35, 1)
    r2_pct = round(max_pct * 0.65, 1)
    r3_pct = max_pct

    def _round(annual_after: float, pct: float, label: str, lever: str, requires: str, use_hold=False) -> dict:
        a = _r100(annual_after)
        return {
            "discount_pct": pct,
            "annual_premium": a,
            "monthly_premium": _m10(a),
            "label": label,
            "lever": lever,
            "requires": requires,
            "use_hold_before_offering": use_hold,
        }

    rounds = [
        _round(base_annual_f * (1 - r1_pct / 100), r1_pct,
               "Pay yearly instead of monthly",
               "annual payment mode (removes the monthly modal loading)",
               "Customer agrees to pay annually."),
        _round(base_annual_f * (1 - r2_pct / 100), r2_pct,
               "Healthy non-smoker / online-direct concession",
               "non-smoker + good health, bought online/direct",
               "Confirmed only AFTER the medical check-up; assumes non-smoker, clean health."),
        _round(base_annual_f * (1 - r3_pct / 100), r3_pct,
               "Best possible — with underwriting/senior approval",
               "special underwriting approval",
               "Senior/underwriting approval; offer ONLY as your final position.",
               use_hold=True),
    ]

    floor_annual = rounds[-1]["annual_premium"]

    return {
        "customer_id": customer_id,
        "plan_type": ptype,
        "cover_lakhs": cover_lakhs,
        "term_years": term_years,
        "base_annual_premium": base_annual,
        "base_monthly_premium": _m10(base_annual_f),
        "concession_rounds": rounds,
        "guidance": (
            "Negotiate round by round — never jump straight to the best round. Use `play_hold_music` "
            "for a 'let me check with underwriting' beat BEFORE offering the final round. Every figure "
            "here is INDICATIVE; the FINAL premium is confirmed only AFTER the medical check-up."
        ),
        "_INTERNAL_do_not_disclose": {
            "max_total_discount_pct": max_pct,
            "floor_annual_premium": floor_annual,
            "floor_monthly_premium": rounds[-1]["monthly_premium"],
            "note": "NEVER quote a premium below the floor. Do NOT reveal the max discount or the floor to the customer.",
        },
    }



# ─── Function dispatch map ────────────────────────────────────────────────────


TOOL_FUNCTIONS = {
    # Triage
    "get_customer_profile": get_customer_profile,    
    "get_customer_summary": get_customer_summary,
    "get_eligibility_assessment": get_eligibility_assessment,
    # Credit Card
    "get_credit_card_details": get_credit_card_details,
    "get_credit_card_transactions": get_credit_card_transactions,
    "get_reward_points": get_reward_points,
    "get_card_spending_analysis": get_card_spending_analysis,
    "check_card_upgrade_eligibility": check_card_upgrade_eligibility,
    # Loan
    "get_active_loans": get_active_loans,
    "get_loan_product_details": get_loan_product_details,
    "get_preapproved_offers": get_preapproved_offers,
    "get_negotiation_terms": get_negotiation_terms,
    "calculate_emi": calculate_emi,
    "get_competitor_rates": get_competitor_rates,
    "assess_collateral": assess_collateral,
    "assess_vehicle_funding": assess_vehicle_funding,
    # CIBIL & RBI
    "check_cibil_score": check_cibil_score,
    "check_rbi_repo_rate": check_rbi_repo_rate,
    # Collections (credit card recovery)
    "get_card_dues": get_card_dues,
    "get_payment_history": get_payment_history,
    "get_settlement_options": get_settlement_options,
    "record_payment_commitment": record_payment_commitment,
    "send_payment_link": send_payment_link,
    # Collections (vehicle loan recovery)
    "get_loan_dues": get_loan_dues,
    "get_loan_settlement_options": get_loan_settlement_options,
    # Life insurance premium recovery (persistency)
    "get_policy_dues": get_policy_dues,
    "get_revival_options": get_revival_options,
    # Life insurance
    "get_insurance_plans": get_insurance_plans,
    "get_cover_recommendation": get_cover_recommendation,
    "calculate_insurance_premium": calculate_insurance_premium,
    "get_premium_negotiation": get_premium_negotiation,
    # Savings
    "get_account_details": get_account_details,
    "get_fixed_deposits": get_fixed_deposits,
    "get_recurring_deposits": get_recurring_deposits,
    "get_savings_transactions": get_savings_transactions,
    "get_fd_rate_card": get_fd_rate_card,
    # General Banking
    "get_debit_card_details": get_debit_card_details,
    "get_investments": get_investments,
    "get_all_transactions": get_all_transactions,
}
