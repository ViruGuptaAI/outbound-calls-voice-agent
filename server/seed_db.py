"""
Contoso Bank — CRM Database Seed Script
========================================
Creates and populates a SQLite database with synthetic customer data
for all 5 demo users. Covers every data point referenced by:
  - Agent prompts (credit card, loan, savings, general banking)
  - Home page dashboard (accounts, transactions)

Run once:  python seed_db.py
Output:    server/crm.db
"""

import sqlite3
import os
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent / "crm.db"


def create_schema(cur: sqlite3.Cursor):
    cur.executescript("""
    -- ─── Customers ────────────────────────────────────────────────────
    CREATE TABLE IF NOT EXISTS customers (
        id              TEXT PRIMARY KEY,
        name            TEXT NOT NULL,
        email           TEXT,
        phone           TEXT,
        pan             TEXT,
        aadhaar_last4   TEXT,
        dob             TEXT,
        gender          TEXT,
        address         TEXT,
        city            TEXT,
        state           TEXT,
        pincode         TEXT,
        kyc_status      TEXT DEFAULT 'Completed',
        segment         TEXT,  -- Premium, Gold, Classic, Platinum, Elite
        relationship_since TEXT,
        cibil_score     INTEGER DEFAULT 750,
        monthly_income  REAL DEFAULT 0,
        salary_bank     INTEGER DEFAULT 0,  -- 1 if salary credited to Contoso
        emi_delays_12m  INTEGER DEFAULT 0,  -- EMI delays in last 12 months
        total_products  INTEGER DEFAULT 1   -- count of active products
    );

    -- ─── Savings / Current Accounts ───────────────────────────────────
    CREATE TABLE IF NOT EXISTS accounts (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        customer_id     TEXT NOT NULL REFERENCES customers(id),
        account_number  TEXT NOT NULL,
        account_type    TEXT NOT NULL,  -- Premium Savings, Salary, Regular Savings
        balance         REAL NOT NULL,
        interest_rate   REAL,
        min_balance     REAL,
        ifsc            TEXT DEFAULT 'CTSO0001234',
        nomination      TEXT DEFAULT 'Registered',
        opened_on       TEXT
    );

    -- ─── Credit Cards ─────────────────────────────────────────────────
    CREATE TABLE IF NOT EXISTS credit_cards (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        customer_id     TEXT NOT NULL REFERENCES customers(id),
        card_number_last4 TEXT NOT NULL,
        card_type       TEXT NOT NULL,  -- Platinum, Gold, Signature, Classic
        credit_limit    REAL NOT NULL,
        available_limit REAL NOT NULL,
        outstanding     REAL NOT NULL,
        minimum_due     REAL,
        due_date        INTEGER,  -- day of month
        reward_points   INTEGER DEFAULT 0,
        annual_fee      REAL,
        fee_waiver_condition TEXT,
        interest_rate_monthly REAL,
        card_status     TEXT DEFAULT 'Active',
        issued_on       TEXT
    );

    -- ─── Loans ────────────────────────────────────────────────────────
    CREATE TABLE IF NOT EXISTS loans (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        customer_id     TEXT NOT NULL REFERENCES customers(id),
        loan_id         TEXT NOT NULL,
        loan_type       TEXT NOT NULL,  -- Home Loan, Personal Loan, Car Loan, Education Loan
        principal       REAL NOT NULL,
        outstanding     REAL NOT NULL,
        emi             REAL NOT NULL,
        interest_rate   REAL NOT NULL,
        tenure_months   INTEGER,
        tenure_remaining_months INTEGER,
        start_date      TEXT,
        status          TEXT DEFAULT 'Active'
    );

    -- ─── Pre-approved Offers ──────────────────────────────────────────
    CREATE TABLE IF NOT EXISTS preapproved_offers (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        customer_id     TEXT NOT NULL REFERENCES customers(id),
        offer_type      TEXT NOT NULL,  -- Personal Loan, Top-up Home Loan, Credit Card Upgrade
        max_amount      REAL,
        interest_rate   REAL,
        description     TEXT,
        valid_until     TEXT
    );
    -- ─── Loan Products (Bank-level rate card & limits) ────────────────
    CREATE TABLE IF NOT EXISTS loan_products (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        product_name    TEXT NOT NULL UNIQUE,
        min_amount      REAL NOT NULL,
        max_amount      REAL NOT NULL,
        floor_rate      REAL NOT NULL,  -- absolute minimum rate (p.a.)
        ceiling_rate    REAL NOT NULL,  -- absolute maximum rate (p.a.)
        base_rate       REAL NOT NULL,  -- standard offered rate (p.a.)
        max_discount_bps INTEGER NOT NULL DEFAULT 0,  -- max bps reduction agent can offer
        max_hike_bps    INTEGER NOT NULL DEFAULT 0,   -- max bps increase for high-risk
        min_tenure_months INTEGER,
        max_tenure_months INTEGER,
        processing_fee_pct REAL,
        prepayment_penalty_pct REAL DEFAULT 0,
        foreclosure_fee_pct REAL DEFAULT 0,
        ltv_ratio       REAL,           -- loan-to-value (home/car)
        collateral_required INTEGER DEFAULT 0,
        eligibility_note TEXT
    );

    -- ─── Negotiation Rules (per customer segment) ─────────────────────
    CREATE TABLE IF NOT EXISTS negotiation_rules (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        segment         TEXT NOT NULL,  -- Premium, Gold, Classic, Platinum, Elite
        product_name    TEXT NOT NULL,
        bonus_discount_bps INTEGER DEFAULT 0,  -- extra bps discount for segment
        fee_waiver_eligible INTEGER DEFAULT 0,  -- 1 = can waive processing fee
        priority_processing INTEGER DEFAULT 0,  -- 1 = fast-track approval
        retention_offer_bps INTEGER DEFAULT 0,  -- extra bps off if threatening to leave
        UNIQUE(segment, product_name)
    );
    -- ─── Fixed Deposits ───────────────────────────────────────────────
    CREATE TABLE IF NOT EXISTS fixed_deposits (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        customer_id     TEXT NOT NULL REFERENCES customers(id),
        fd_number       TEXT NOT NULL,
        amount          REAL NOT NULL,
        interest_rate   REAL NOT NULL,
        tenure_months   INTEGER,
        start_date      TEXT,
        maturity_date   TEXT,
        is_tax_saver    INTEGER DEFAULT 0,
        auto_renew      INTEGER DEFAULT 1
    );

    -- ─── Recurring Deposits ───────────────────────────────────────────
    CREATE TABLE IF NOT EXISTS recurring_deposits (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        customer_id     TEXT NOT NULL REFERENCES customers(id),
        rd_number       TEXT NOT NULL,
        monthly_amount  REAL NOT NULL,
        interest_rate   REAL NOT NULL,
        tenure_months   INTEGER,
        start_date      TEXT,
        maturity_date   TEXT,
        current_balance REAL
    );

    -- ─── Transactions ─────────────────────────────────────────────────
    CREATE TABLE IF NOT EXISTS transactions (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        customer_id     TEXT NOT NULL REFERENCES customers(id),
        account_type    TEXT NOT NULL,  -- savings, credit_card, loan
        date            TEXT NOT NULL,
        description     TEXT NOT NULL,
        amount          REAL NOT NULL,
        txn_type        TEXT NOT NULL,  -- debit, credit
        category        TEXT,  -- shopping, food, fuel, salary, emi, utility, transfer, investment
        balance_after   REAL
    );

    -- ─── Debit Cards ──────────────────────────────────────────────────
    CREATE TABLE IF NOT EXISTS debit_cards (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        customer_id     TEXT NOT NULL REFERENCES customers(id),
        card_number_last4 TEXT NOT NULL,
        card_network    TEXT DEFAULT 'Visa',
        card_type       TEXT DEFAULT 'Classic',
        daily_limit     REAL DEFAULT 50000,
        international   INTEGER DEFAULT 0,
        status          TEXT DEFAULT 'Active'
    );

    -- ─── Investments (MF, Equity, etc.) ───────────────────────────────
    CREATE TABLE IF NOT EXISTS investments (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        customer_id     TEXT NOT NULL REFERENCES customers(id),
        investment_type TEXT NOT NULL,  -- Mutual Fund, Equity, PPF, NPS, Gold ETF
        name            TEXT NOT NULL,
        current_value   REAL NOT NULL,
        invested_amount REAL,
        returns_pct     REAL,
        sip_amount      REAL,
        sip_date        INTEGER  -- day of month
    );

    -- ─── FD Rate Card (bank-level, not per customer) ─────────────────
    CREATE TABLE IF NOT EXISTS fd_rate_card (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        tenure_label    TEXT NOT NULL,
        min_days        INTEGER NOT NULL,
        max_days        INTEGER NOT NULL,
        general_rate    REAL NOT NULL,
        senior_rate     REAL NOT NULL,
        special_rate    REAL,
        special_scheme  TEXT
    );

    -- ─── Competitor Rates (for loan negotiation context) ──────────────
    CREATE TABLE IF NOT EXISTS competitor_rates (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        bank_name       TEXT NOT NULL,
        product_name    TEXT NOT NULL,
        min_rate        REAL NOT NULL,
        max_rate        REAL NOT NULL,
        processing_fee_pct REAL,
        as_of_date      TEXT DEFAULT '2026-05-01'
    );

    -- ─── Card Upgrade Paths ──────────────────────────────────────────
    CREATE TABLE IF NOT EXISTS card_upgrade_paths (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        from_card       TEXT NOT NULL,
        to_card         TEXT NOT NULL,
        from_annual_fee REAL NOT NULL,
        to_annual_fee   REAL NOT NULL,
        min_spend_required REAL NOT NULL,
        additional_benefits TEXT NOT NULL
    );

    -- ─── Collections (Credit Card Delinquency) ───────────────────────
    CREATE TABLE IF NOT EXISTS collections (
        id                INTEGER PRIMARY KEY AUTOINCREMENT,
        customer_id       TEXT NOT NULL REFERENCES customers(id),
        card_last4        TEXT,
        total_outstanding REAL,
        overdue_amount    REAL,
        minimum_due       REAL,
        days_past_due     INTEGER,
        late_fee          REAL,
        penal_interest    REAL,
        last_payment_date TEXT,
        last_payment_amount REAL,
        due_date          TEXT,
        bucket            TEXT,   -- '1-30', '31-60', '61-90', '90+'
        status            TEXT DEFAULT 'Overdue'
    );

    -- ─── Payment Promises (Promise-to-Pay log) ───────────────────────
    CREATE TABLE IF NOT EXISTS payment_promises (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        customer_id   TEXT NOT NULL REFERENCES customers(id),
        amount        REAL,
        promise_date  TEXT,
        method        TEXT,
        reference     TEXT,
        created_at    TEXT
    );

    -- ─── Loan Collections (Vehicle Loan Delinquency) ─────────────────
    CREATE TABLE IF NOT EXISTS loan_collections (
        id                INTEGER PRIMARY KEY AUTOINCREMENT,
        customer_id       TEXT NOT NULL REFERENCES customers(id),
        loan_id           TEXT,
        loan_type         TEXT,   -- Car Loan
        vehicle           TEXT,   -- e.g. 'Hyundai Creta (MH-12-CD-8890)'
        total_outstanding REAL,
        emi_amount        REAL,
        emis_overdue      INTEGER,
        overdue_amount    REAL,
        days_past_due     INTEGER,
        late_fee          REAL,
        penal_interest    REAL,
        last_payment_date TEXT,
        last_payment_amount REAL,
        due_date          TEXT,
        bucket            TEXT,   -- '1-30', '31-60', '61-90', '90+'
        status            TEXT DEFAULT 'Overdue'
    );

    -- ─── Life Insurance Policies (for premium recovery / persistency) ─
    CREATE TABLE IF NOT EXISTS life_policies (
        id                  INTEGER PRIMARY KEY AUTOINCREMENT,
        customer_id         TEXT NOT NULL REFERENCES customers(id),
        policy_number       TEXT,
        plan_name           TEXT,
        plan_type           TEXT,   -- protection, savings, ulip, child, retirement
        sum_assured         REAL,
        premium_amount      REAL,   -- per instalment
        premium_frequency   TEXT,   -- Monthly, Quarterly, Half-Yearly, Annual
        policy_start        TEXT,
        policy_term_years   INTEGER,
        premium_paying_term_years INTEGER,
        premiums_paid       INTEGER,
        premiums_overdue    INTEGER,
        total_overdue_amount REAL,  -- sum of overdue premiums (excl. interest)
        days_past_due       INTEGER,
        grace_period_days   INTEGER, -- 15 (monthly) or 30 (other modes)
        grace_end_date      TEXT,
        revival_end_date    TEXT,    -- end of revival window (for lapsed policies)
        revival_interest    REAL,    -- interest/penalty to revive a lapsed policy
        accrued_benefit     REAL,    -- accrued bonus (savings) or fund value (ulip)
        last_payment_date   TEXT,
        last_payment_amount REAL,
        status              TEXT     -- 'In Grace' or 'Lapsed'
    );
    """)


def seed_data(cur: sqlite3.Cursor):
    # ── Customers ──────────────────────────────────────────────────────
    customers = [
        # id, name, email, phone, pan, aadhaar_last4, dob, gender, address, city, state, pincode, kyc, segment, since, cibil, income, salary_bank, emi_delays, products
        ('rajesh', 'Rajesh Kumar', 'rajesh.kumar@email.com', '+91-98765-43210', 'ABCPK1234A', '4521', '1985-03-15', 'Male', '42, MG Road, Koramangala', 'Bengaluru', 'Karnataka', '560034', 'Completed', 'Elite', '2011-03-15', 845, 350000, 1, 0, 10),
        ('priya', 'Priya Sharma', 'priya.sharma@email.com', '+91-98765-12345', 'DEFPS5678B', '8734', '1990-07-22', 'Female', '15, Banjara Hills', 'Hyderabad', 'Telangana', '500034', 'Completed', 'Gold', '2019-11-05', 745, 95000, 1, 0, 4),
        ('amit', 'Amit Patel', 'amit.patel@email.com', '+91-99887-76655', 'GHIAP9012C', '2198', '1992-01-08', 'Male', '88, SG Highway, Bopal', 'Ahmedabad', 'Gujarat', '380058', 'Completed', 'Classic', '2021-03-20', 698, 65000, 1, 1, 3),
        ('sneha', 'Sneha Reddy', 'sneha.reddy@email.com', '+91-97654-32100', 'JKLSR3456D', '6673', '1988-11-30', 'Female', '23, Jubilee Hills', 'Hyderabad', 'Telangana', '500033', 'Completed', 'Platinum', '2017-02-14', 810, 110000, 0, 0, 4),
        ('vikram', 'Vikram Singh', 'vikram.singh@email.com', '+91-98211-55443', 'MNOVS7890E', '1190', '1975-05-18', 'Male', '7, Civil Lines', 'New Delhi', 'Delhi', '110001', 'Completed', 'Elite', '2012-08-01', 835, 250000, 1, 0, 8),
        ('viru', 'Viru Gupta', 'viru.gupta@email.com', '+91-98200-11223', 'PQRVS2345F', '7788', '1978-09-25', 'Male', '12, Pali Hill, Bandra West', 'Mumbai', 'Maharashtra', '400050', 'Completed', 'Premium', '2018-06-10', 782, 125000, 1, 0, 5),
        ('harshal', 'Harshal Patil', 'harshal.patil@email.com', '+91-99223-44556', 'STUHP6789G', '3342', '1993-04-12', 'Male', '55, Kothrud', 'Pune', 'Maharashtra', '411038', 'Completed', 'Classic', '2022-09-01', 710, 72000, 1, 2, 3),
    ]
    cur.executemany("INSERT INTO customers VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", customers)

    # ── Savings Accounts ───────────────────────────────────────────────
    accounts = [
        ('rajesh', '5968-6484-4521', 'Premium Savings', 2850000, 5.5, 100000, 'CTSO0001234', 'Registered', '2011-03-15'),
        ('priya', '3391-5968-8734', 'Regular Savings', 132800, 3.5, 10000, 'CTSO0002345', 'Registered', '2019-11-05'),
        ('amit', '5567-5478-2198', 'Salary Account', 78450, 3.0, 0, 'CTSO0003456', 'Registered', '2021-03-20'),
        ('sneha', '9912-6673-6673', 'Premium Savings', 245600, 4.0, 25000, 'CTSO0004567', 'Registered', '2017-02-14'),
        ('vikram', '4488-1190-1190', 'Premium Savings', 1245000, 5.5, 100000, 'CTSO0005678', 'Registered', '2012-08-01'),
        ('viru', '6622-7788-7788', 'Premium Savings', 485230, 4.5, 25000, 'CTSO0006789', 'Registered', '2018-06-10'),
        ('harshal', '8833-3342-3342', 'Salary Account', 64500, 3.0, 0, 'CTSO0007890', 'Registered', '2022-09-01'),
    ]
    cur.executemany("INSERT INTO accounts (customer_id,account_number,account_type,balance,interest_rate,min_balance,ifsc,nomination,opened_on) VALUES (?,?,?,?,?,?,?,?,?)", accounts)

    # ── Credit Cards ───────────────────────────────────────────────────
    credit_cards = [
        ('rajesh', '7842', 'Signature', 750000, 710000, 40000, 2000, 10, 62000, 5000, 'Complimentary', 3.0, 'Active', '2012-05-01'),
        ('priya', '3391', 'Gold', 200000, 131080, 68920, 3446, 10, 8200, 1000, 'Waived if spend > ₹2L', 3.5, 'Active', '2020-04-15'),
        ('amit', '5567', 'Classic', 100000, 72500, 27500, 1375, 20, 3400, 500, 'Waived if spend > ₹1L', 3.5, 'Active', '2021-07-10'),
        ('sneha', '9912', 'Platinum', 300000, 254770, 45230, 2262, 20, 24560, 1500, 'Waived if spend > ₹3L', 3.5, 'Active', '2017-08-22'),
        ('vikram', '4488', 'Signature', 500000, 462000, 38000, 1900, 5, 45200, 5000, 'Complimentary', 3.0, 'Active', '2013-01-10'),
        ('viru', '7788', 'Platinum', 300000, 215000, 85000, 4250, 15, 12450, 1500, 'Waived if spend > ₹3L', 3.5, 'Active', '2019-02-01'),
        ('harshal', '3342', 'Classic', 75000, 42000, 33000, 1650, 18, 1800, 500, 'Waived if spend > \u20b91L', 3.5, 'Active', '2022-12-15'),
    ]
    cur.executemany("INSERT INTO credit_cards (customer_id,card_number_last4,card_type,credit_limit,available_limit,outstanding,minimum_due,due_date,reward_points,annual_fee,fee_waiver_condition,interest_rate_monthly,card_status,issued_on) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", credit_cards)

    # ── Loans ──────────────────────────────────────────────────────────
    loans = [
        ('viru', 'HL-2024-78542', 'Home Loan', 4500000, 4280000, 39200, 8.90, 360, 324, '2024-01-15', 'Active'),
        ('viru', 'PL-2025-12087', 'Personal Loan', 500000, 320000, 11500, 11.50, 60, 30, '2025-01-10', 'Active'),
        ('priya', 'HL-2023-55210', 'Home Loan', 5000000, 4215000, 39500, 8.75, 360, 216, '2023-06-01', 'Active'),
        ('amit', 'PL-2024-33890', 'Personal Loan', 300000, 280000, 10200, 11.75, 60, 36, '2024-06-15', 'Active'),
        ('amit', 'CL-2025-10112', 'Car Loan', 600000, 520000, 12800, 9.25, 84, 72, '2025-02-01', 'Active'),
        ('sneha', 'PL-2025-44567', 'Personal Loan', 200000, 145000, 7400, 10.99, 36, 22, '2025-03-01', 'Active'),
        ('vikram', 'HL-2018-12001', 'Home Loan', 8000000, 3200000, 65000, 8.50, 240, 84, '2018-09-01', 'Active'),
        ('harshal', 'CL-2023-55678', 'Car Loan', 700000, 480000, 14500, 9.50, 84, 54, '2023-06-01', 'Active'),
        ('harshal', 'EL-2024-22345', 'Education Loan', 400000, 350000, 8200, 10.25, 60, 42, '2024-01-15', 'Active'),
    ]
    cur.executemany("INSERT INTO loans (customer_id,loan_id,loan_type,principal,outstanding,emi,interest_rate,tenure_months,tenure_remaining_months,start_date,status) VALUES (?,?,?,?,?,?,?,?,?,?,?)", loans)

    # ── Pre-approved Offers ────────────────────────────────────────────
    offers = [
        ('rajesh', 'Home Loan', 15000000, 8.45, 'Premium home loan for Elite customer', '2026-12-31'),
        ('rajesh', 'Personal Loan', 2500000, 9.75, 'Top-tier rate for long-standing Elite customer', '2026-12-31'),
        ('rajesh', 'Credit Card Limit Increase', 1000000, None, 'Increase Signature card limit to ₹10L', '2026-12-31'),
        ('priya', 'Personal Loan', 500000, 10.99, 'Pre-approved based on salary credits', '2026-12-31'),
        ('priya', 'Credit Card Upgrade', None, None, 'Upgrade to Platinum with 2x rewards', '2026-09-30'),
        ('amit', 'Personal Loan', 400000, 10.80, 'Instant approval for salary account holders', '2026-12-31'),
        ('sneha', 'Credit Card Limit Increase', 500000, None, 'Increase Platinum card limit to ₹5L', '2026-12-31'),
        ('sneha', 'Home Loan', 6000000, 8.5, 'Pre-qualified home loan offer', '2026-12-31'),
        ('vikram', 'Loan Against FD', 2000000, 7.0, 'Loan against your ₹25L FD portfolio', '2026-12-31'),
        ('vikram', 'Personal Loan', 1500000, 9.5, 'Premium rate for Elite customers', '2026-12-31'),
        ('viru', 'Personal Loan', 800000, 10.75, 'Instant disbursement, minimal documentation', '2026-12-31'),
        ('viru', 'Top-up Home Loan', 500000, 9.10, 'Top-up on existing home loan', '2026-12-31'),
        ('harshal', 'Home Loan', 3500000, 9.25, 'Home loan offer for salary account holder', '2026-12-31'),
    ]
    cur.executemany("INSERT INTO preapproved_offers (customer_id,offer_type,max_amount,interest_rate,description,valid_until) VALUES (?,?,?,?,?,?)", offers)

    # ── Loan Products (rate card) ──────────────────────────────────────
    loan_products = [
        # (product_name, min_amt, max_amt, floor_rate, ceil_rate, base_rate, max_disc_bps, max_hike_bps, min_tenure, max_tenure, proc_fee%, prepay%, foreclose%, ltv, collateral, note)
        ('Home Loan',       500000,  50000000, 8.40, 10.50, 8.90, 75, 200, 12, 360, 0.5, 0, 2.0, 0.80, 1, 'Property as collateral; CIBIL 700+ for best rate'),
        ('Personal Loan',   50000,   2500000,  10.25, 18.00, 11.25, 100, 300, 12, 60, 2.0, 2.0, 4.0, None, 0, 'No collateral; salary > 25K; CIBIL 650+'),
        ('Car Loan',        100000,  5000000,  8.40, 12.00, 9.25, 75, 150, 12, 84, 1.0, 0, 2.5, 0.90, 1, 'Vehicle as collateral; new car up to 90% LTV'),
        ('Education Loan',  100000,  7500000,  8.75, 13.00, 9.75, 100, 200, 12, 180, 0, 0, 0, None, 0, 'Collateral-free up to 7.5L; moratorium during study'),
        ('Loan Against FD', 25000,   50000000, 6.50, 8.50, 7.25, 50, 50, 1, 60, 0, 0, 0, 0.90, 1, 'Up to 90% of FD value; no CIBIL check'),
        ('Gold Loan',       25000,   5000000,  7.50, 11.00, 8.50, 75, 100, 3, 36, 1.0, 0, 1.0, 0.75, 1, 'Gold ornaments as collateral; instant disbursal'),
        ('Top-up Home Loan', 100000, 5000000,  8.50, 10.00, 9.25, 75, 100, 12, 180, 0.5, 0, 2.0, None, 1, 'On existing home loan; same property'),
    ]
    cur.executemany("""
        INSERT INTO loan_products
        (product_name, min_amount, max_amount, floor_rate, ceiling_rate, base_rate,
         max_discount_bps, max_hike_bps, min_tenure_months, max_tenure_months,
         processing_fee_pct, prepayment_penalty_pct, foreclosure_fee_pct,
         ltv_ratio, collateral_required, eligibility_note)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, loan_products)

    # ── Negotiation Rules (per segment × product) ──────────────────────
    segments = ['Classic', 'Gold', 'Premium', 'Platinum', 'Elite']
    products = ['Home Loan', 'Personal Loan', 'Car Loan', 'Education Loan', 'Loan Against FD', 'Gold Loan', 'Top-up Home Loan']
    negotiation_rules = []
    # bps discounts escalate by segment tier
    tier_config = {
        'Classic':   {'bonus_bps': 0,  'fee_waiver': 0, 'priority': 0, 'retention_bps': 10},
        'Gold':      {'bonus_bps': 10, 'fee_waiver': 0, 'priority': 0, 'retention_bps': 15},
        'Premium':   {'bonus_bps': 15, 'fee_waiver': 1, 'priority': 0, 'retention_bps': 20},
        'Platinum':  {'bonus_bps': 25, 'fee_waiver': 1, 'priority': 1, 'retention_bps': 30},
        'Elite':     {'bonus_bps': 40, 'fee_waiver': 1, 'priority': 1, 'retention_bps': 50},
    }
    for seg in segments:
        cfg = tier_config[seg]
        for prod in products:
            negotiation_rules.append((
                seg, prod, cfg['bonus_bps'], cfg['fee_waiver'],
                cfg['priority'], cfg['retention_bps']
            ))
    cur.executemany("""
        INSERT INTO negotiation_rules
        (segment, product_name, bonus_discount_bps, fee_waiver_eligible,
         priority_processing, retention_offer_bps)
        VALUES (?,?,?,?,?,?)
    """, negotiation_rules)

    # ── Fixed Deposits ─────────────────────────────────────────────────
    fds = [
        ('rajesh', 'FD-2023-3318', 2000000, 7.3, 36, '2023-06-01', '2026-06-01', 0, 1),
        ('rajesh', 'FD-2024-8891', 1500000, 7.25, 24, '2024-09-01', '2026-09-01', 0, 1),
        ('rajesh', 'FD-80C-3319', 150000, 6.5, 60, '2025-01-15', '2030-01-15', 1, 0),
        ('priya', 'FD-2025-6712', 300000, 7.0, 24, '2025-01-10', '2027-01-10', 0, 1),
        ('sneha', 'FD-2024-4490', 500000, 7.25, 36, '2024-06-01', '2027-06-01', 0, 1),
        ('vikram', 'FD-2023-1001', 1000000, 7.3, 36, '2023-04-01', '2026-04-01', 0, 0),
        ('vikram', 'FD-2024-1002', 800000, 7.25, 24, '2024-07-01', '2026-07-01', 0, 1),
        ('vikram', 'FD-2025-1003', 700000, 7.1, 12, '2025-11-01', '2026-11-01', 0, 1),
        ('vikram', 'FD-80C-2024', 150000, 6.5, 60, '2024-03-01', '2029-03-01', 1, 0),
        ('viru', 'FD-2025-8801', 500000, 7.1, 24, '2025-03-15', '2027-03-15', 0, 1),
        ('viru', 'FD-2024-8802', 200000, 6.8, 12, '2025-09-01', '2026-09-01', 0, 1),
    ]
    cur.executemany("INSERT INTO fixed_deposits (customer_id,fd_number,amount,interest_rate,tenure_months,start_date,maturity_date,is_tax_saver,auto_renew) VALUES (?,?,?,?,?,?,?,?,?)", fds)

    # ── Recurring Deposits ─────────────────────────────────────────────
    rds = [
        ('rajesh', 'RD-2024-1102', 25000, 6.8, 36, '2024-04-01', '2027-04-01', 375000),
        ('priya', 'RD-2024-2233', 6000, 6.8, 36, '2024-08-01', '2027-08-01', 72000),
        ('amit', 'RD-2025-3344', 5000, 6.5, 24, '2025-01-01', '2027-01-01', 25000),
        ('viru', 'RD-2025-8804', 10000, 6.5, 30, '2025-07-01', '2028-01-01', 105000),
    ]
    cur.executemany("INSERT INTO recurring_deposits (customer_id,rd_number,monthly_amount,interest_rate,tenure_months,start_date,maturity_date,current_balance) VALUES (?,?,?,?,?,?,?,?)", rds)

    # ── Debit Cards ────────────────────────────────────────────────────
    debit_cards = [
        ('rajesh', '4521', 'Visa', 'Signature', 200000, 1, 'Active'),
        ('priya', '8734', 'Mastercard', 'Classic', 50000, 0, 'Active'),
        ('amit', '2198', 'Visa', 'Classic', 50000, 0, 'Active'),
        ('sneha', '6673', 'Visa', 'Gold', 75000, 1, 'Active'),
        ('vikram', '1190', 'Visa', 'Signature', 200000, 1, 'Active'),
        ('viru', '7788', 'Visa', 'Platinum', 100000, 1, 'Active'),
        ('harshal', '3342', 'Mastercard', 'Classic', 50000, 0, 'Active'),
    ]
    cur.executemany("INSERT INTO debit_cards (customer_id,card_number_last4,card_network,card_type,daily_limit,international,status) VALUES (?,?,?,?,?,?,?)", debit_cards)

    # ── Investments ────────────────────────────────────────────────────
    investments = [
        ('rajesh', 'Mutual Fund', 'ICICI Pru Bluechip Fund', 1200000, 950000, 26.3, 30000, 1),
        ('rajesh', 'Equity', 'Direct Equity Portfolio', 1800000, 1200000, 50.0, None, None),
        ('rajesh', 'Gold ETF', 'HDFC Gold ETF', 500000, 400000, 25.0, None, None),
        ('rajesh', 'NPS', 'National Pension System', 680000, 550000, 12.4, 20000, 5),
        ('rajesh', 'PPF', 'Public Provident Fund', 1200000, 1050000, 7.1, None, None),
        ('priya', 'Mutual Fund', 'SBI Blue Chip Fund', 185000, 168000, 10.1, 5000, 10),
        ('amit', 'PPF', 'Public Provident Fund', 185000, 170000, 7.1, None, None),
        ('sneha', 'Mutual Fund', 'Axis Long Term Equity', 290000, 250000, 16.0, 8000, 1),
        ('sneha', 'NPS', 'National Pension System', 180000, 165000, 9.1, 5000, 5),
        ('vikram', 'Mutual Fund', 'HDFC Flexi Cap Fund', 872000, 720000, 21.1, 25000, 1),
        ('vikram', 'Equity', 'Direct Equity Portfolio', 1000000, 780000, 28.2, None, None),
        ('vikram', 'Gold ETF', 'SBI Gold ETF', 350000, 300000, 16.7, None, None),
        ('vikram', 'NPS', 'National Pension System', 450000, 380000, 10.5, 15000, 5),
        ('viru', 'Mutual Fund', 'HDFC Flexi Cap Fund', 324500, 280000, 15.9, 10000, 5),
        ('viru', 'PPF', 'Public Provident Fund', 620000, 560000, 7.1, None, None),
        ('harshal', 'PPF', 'Public Provident Fund', 85000, 78000, 7.1, None, None),
    ]
    cur.executemany("INSERT INTO investments (customer_id,investment_type,name,current_value,invested_amount,returns_pct,sip_amount,sip_date) VALUES (?,?,?,?,?,?,?,?)", investments)

    # ── FD Rate Card ───────────────────────────────────────────────────
    fd_rates = [
        # (tenure_label, min_days, max_days, general_rate, senior_rate, special_rate, special_scheme)
        ('7-14 days',       7,     14,   3.50, 4.00, None, None),
        ('15-29 days',      15,    29,   4.00, 4.50, None, None),
        ('30-45 days',      30,    45,   4.50, 5.00, None, None),
        ('46-90 days',      46,    90,   5.25, 5.75, None, None),
        ('91-180 days',     91,    180,  5.75, 6.25, None, None),
        ('181-270 days',    181,   270,  6.25, 6.75, None, None),
        ('271 days - 1 yr', 271,   365,  6.50, 7.00, 6.75, 'Contoso Flexi FD'),
        ('1-2 years',       366,   730,  7.00, 7.50, 7.25, 'Contoso Growth FD'),
        ('2-3 years',       731,   1095, 7.10, 7.60, 7.35, 'Contoso Growth FD'),
        ('3-5 years',       1096,  1825, 6.75, 7.25, 7.00, None),
        ('5 years (80C)',   1826,  1826, 6.50, 7.00, None, 'Tax Saver FD'),
        ('5-10 years',      1827,  3650, 6.50, 7.00, None, None),
    ]
    cur.executemany("""
        INSERT INTO fd_rate_card
        (tenure_label, min_days, max_days, general_rate, senior_rate, special_rate, special_scheme)
        VALUES (?,?,?,?,?,?,?)
    """, fd_rates)

    # ── Competitor Rates (for loan negotiation) ────────────────────────
    competitor_rates = [
        # Home Loan (May 2026 market rates)
        ('SBI',    'Home Loan', 8.50, 9.85, 0.35),
        ('HDFC',   'Home Loan', 8.75, 9.90, 0.50),
        ('ICICI',  'Home Loan', 8.60, 9.75, 0.50),
        ('Axis',   'Home Loan', 8.75, 10.15, 1.00),
        ('Kotak',  'Home Loan', 8.70, 9.95, 0.50),
        # Personal Loan
        ('SBI',    'Personal Loan', 10.50, 15.65, 1.50),
        ('HDFC',   'Personal Loan', 10.75, 16.00, 2.50),
        ('ICICI',  'Personal Loan', 10.50, 16.50, 2.00),
        ('Axis',   'Personal Loan', 10.75, 17.00, 2.00),
        ('Kotak',  'Personal Loan', 11.00, 16.50, 2.50),
        # Car Loan
        ('SBI',    'Car Loan', 8.65, 10.50, 0),
        ('HDFC',   'Car Loan', 8.70, 11.00, 0.50),
        ('ICICI',  'Car Loan', 8.65, 10.85, 0.50),
        ('Axis',   'Car Loan', 8.75, 11.25, 1.00),
        ('Kotak',  'Car Loan', 8.90, 11.50, 0.50),
        # Education Loan
        ('SBI',    'Education Loan', 8.50, 10.75, 0),
        ('HDFC',   'Education Loan', 9.25, 13.25, 1.00),
        ('ICICI',  'Education Loan', 9.25, 13.50, 0),
        ('Axis',   'Education Loan', 9.50, 13.75, 1.50),
        ('Kotak',  'Education Loan', 9.50, 14.00, 1.00),
    ]
    cur.executemany("""
        INSERT INTO competitor_rates
        (bank_name, product_name, min_rate, max_rate, processing_fee_pct)
        VALUES (?,?,?,?,?)
    """, competitor_rates)

    # ── Card Upgrade Paths ──────────────────────────────────────────────
    card_upgrade_paths = [
        # (from_card, to_card, from_fee, to_fee, min_spend_req, benefits)
        ('Classic', 'Gold', 500, 1000, 100000,
         'Airport lounge (2/yr), 2x rewards on dining, ₹2L free insurance, higher limit'),
        ('Classic', 'Platinum', 500, 1500, 200000,
         'Airport lounge (4/yr), 3x rewards on dining & travel, ₹5L insurance, fuel surcharge waiver, concierge service'),
        ('Gold', 'Platinum', 1000, 1500, 200000,
         'Airport lounge (4/yr vs 2), 3x rewards (vs 2x), ₹5L insurance (vs ₹2L), concierge service'),
        ('Gold', 'Signature', 1000, 5000, 500000,
         'Unlimited lounge access, 5x rewards, ₹10L insurance, dedicated RM, complimentary golf, global concierge'),
        ('Platinum', 'Signature', 1500, 5000, 500000,
         'Unlimited lounge (vs 4/yr), 5x rewards (vs 3x), ₹10L insurance (vs ₹5L), complimentary golf, global concierge, fee often waived'),
    ]
    cur.executemany("""
        INSERT INTO card_upgrade_paths
        (from_card, to_card, from_annual_fee, to_annual_fee, min_spend_required, additional_benefits)
        VALUES (?,?,?,?,?,?)
    """, card_upgrade_paths)

    # ── Transactions ───────────────────────────────────────────────────
    txns = [
        # Rajesh
        ('rajesh', 'savings', '2026-05-28', 'SIP - ICICI Pru Bluechip', 30000, 'debit', 'investment', 2850000),
        ('rajesh', 'savings', '2026-05-27', 'Salary Credit - JP Morgan', 350000, 'credit', 'salary', 2880000),
        ('rajesh', 'savings', '2026-05-26', 'FD Interest Credit', 18500, 'credit', 'interest', 2530000),
        ('rajesh', 'savings', '2026-05-25', 'NPS Contribution', 20000, 'debit', 'investment', 2511500),
        ('rajesh', 'savings', '2026-05-23', 'RD Installment', 25000, 'debit', 'investment', 2531500),
        ('rajesh', 'savings', '2026-05-20', 'Insurance Premium - HDFC Life', 15000, 'debit', 'insurance', 2556500),
        ('rajesh', 'credit_card', '2026-05-22', 'The Oberoi', 35000, 'debit', 'dining', None),
        ('rajesh', 'credit_card', '2026-05-18', 'Emirates Airlines', 120000, 'debit', 'travel', None),
        ('rajesh', 'credit_card', '2026-05-12', 'Amazon.in', 8500, 'debit', 'shopping', None),
        # Priya
        ('priya', 'savings', '2026-05-28', 'Home Loan EMI', 38500, 'debit', 'emi', 132800),
        ('priya', 'savings', '2026-05-27', 'Flipkart', 7499, 'debit', 'shopping', 171300),
        ('priya', 'savings', '2026-05-27', 'Salary Credit - Infosys', 95000, 'credit', 'salary', 178799),
        ('priya', 'savings', '2026-05-26', 'Netflix', 649, 'debit', 'entertainment', 83799),
        ('priya', 'savings', '2026-05-25', 'RD Auto-Debit', 6000, 'debit', 'investment', 84448),
        ('priya', 'credit_card', '2026-05-24', 'Myntra', 5600, 'debit', 'shopping', None),
        ('priya', 'credit_card', '2026-05-20', 'Zomato', 1250, 'debit', 'food', None),
        ('priya', 'credit_card', '2026-05-15', 'Uber', 890, 'debit', 'transport', None),
        # Amit
        ('amit', 'savings', '2026-05-28', 'Zomato', 1245, 'debit', 'food', 78450),
        ('amit', 'savings', '2026-05-27', 'Salary Credit - Wipro', 65000, 'credit', 'salary', 79695),
        ('amit', 'savings', '2026-05-26', 'Personal Loan EMI', 9800, 'debit', 'emi', 14695),
        ('amit', 'savings', '2026-05-25', 'Petrol - HPCL', 2100, 'debit', 'fuel', 24495),
        ('amit', 'savings', '2026-05-24', 'UPI - Rent', 18000, 'debit', 'transfer', 26595),
        ('amit', 'savings', '2026-05-22', 'Car Loan EMI', 12500, 'debit', 'emi', 44595),
        ('amit', 'credit_card', '2026-05-23', 'Croma Electronics', 15999, 'debit', 'shopping', None),
        ('amit', 'credit_card', '2026-05-18', 'BookMyShow', 800, 'debit', 'entertainment', None),
        # Sneha
        ('sneha', 'savings', '2026-05-28', 'Myntra Shopping', 8299, 'debit', 'shopping', 245600),
        ('sneha', 'savings', '2026-05-27', 'Credit Card Cashback', 1250, 'credit', 'cashback', 253899),
        ('sneha', 'savings', '2026-05-27', 'Uber', 540, 'debit', 'transport', 252649),
        ('sneha', 'savings', '2026-05-26', 'Salary Credit - Google', 110000, 'credit', 'salary', 253189),
        ('sneha', 'savings', '2026-05-25', 'Spotify', 119, 'debit', 'entertainment', 143189),
        ('sneha', 'credit_card', '2026-05-26', 'Apple Store', 19999, 'debit', 'shopping', None),
        ('sneha', 'credit_card', '2026-05-22', 'Starbucks', 680, 'debit', 'food', None),
        ('sneha', 'credit_card', '2026-05-18', 'Nykaa', 3450, 'debit', 'shopping', None),
        ('sneha', 'credit_card', '2026-05-15', 'Zara', 7890, 'debit', 'shopping', None),
        # Vikram
        ('vikram', 'savings', '2026-05-28', 'SIP - HDFC Flexi Cap', 25000, 'debit', 'investment', 1245000),
        ('vikram', 'savings', '2026-05-27', 'Dividend - TCS', 8400, 'credit', 'investment', 1270000),
        ('vikram', 'savings', '2026-05-26', 'FD Interest', 15200, 'credit', 'interest', 1261600),
        ('vikram', 'savings', '2026-05-25', 'Insurance Premium - LIC', 12500, 'debit', 'insurance', 1246400),
        ('vikram', 'savings', '2026-05-24', 'Gold ETF Purchase', 50000, 'debit', 'investment', 1258900),
        ('vikram', 'savings', '2026-05-22', 'Home Loan EMI', 65000, 'debit', 'emi', 1308900),
        ('vikram', 'savings', '2026-05-20', 'NPS Contribution', 15000, 'debit', 'investment', 1373900),
        ('vikram', 'credit_card', '2026-05-21', 'Taj Hotel', 22000, 'debit', 'dining', None),
        ('vikram', 'credit_card', '2026-05-15', 'Singapore Airlines', 85000, 'debit', 'travel', None),
        # Viru
        ('viru', 'savings', '2026-05-28', 'Amazon India', 4299, 'debit', 'shopping', 485230),
        ('viru', 'savings', '2026-05-27', 'Salary Credit - TCS', 125000, 'credit', 'salary', 489529),
        ('viru', 'savings', '2026-05-27', 'Swiggy', 856, 'debit', 'food', 364529),
        ('viru', 'savings', '2026-05-26', 'Electricity Bill - BESCOM', 2340, 'debit', 'utility', 365385),
        ('viru', 'savings', '2026-05-25', 'UPI - Priya S.', 5000, 'debit', 'transfer', 367725),
        ('viru', 'savings', '2026-05-23', 'RD Installment', 10000, 'debit', 'investment', 372725),
        ('viru', 'savings', '2026-05-20', 'NEFT - Rent', 35000, 'debit', 'transfer', 382725),
        ('viru', 'savings', '2026-05-18', 'ATM Withdrawal', 10000, 'debit', 'cash', 417725),
        ('viru', 'credit_card', '2026-05-22', 'Amazon.in', 12500, 'debit', 'shopping', None),
        ('viru', 'credit_card', '2026-05-20', 'Swiggy', 850, 'debit', 'food', None),
        ('viru', 'credit_card', '2026-05-18', 'Reliance Fuel', 3200, 'debit', 'fuel', None),
        ('viru', 'credit_card', '2026-05-15', 'BigBasket', 2100, 'debit', 'shopping', None),
        ('viru', 'credit_card', '2026-05-12', 'PVR Cinemas', 1400, 'debit', 'entertainment', None),
        # Harshal
        ('harshal', 'savings', '2026-05-28', 'Swiggy', 680, 'debit', 'food', 64500),
        ('harshal', 'savings', '2026-05-27', 'Salary Credit - Persistent', 72000, 'credit', 'salary', 65180),
        ('harshal', 'savings', '2026-05-26', 'Car Loan EMI', 14500, 'debit', 'emi', -6820),
        ('harshal', 'savings', '2026-05-25', 'Education Loan EMI', 8200, 'debit', 'emi', 7680),
        ('harshal', 'savings', '2026-05-24', 'UPI - Rent', 12000, 'debit', 'transfer', 15880),
        ('harshal', 'savings', '2026-05-22', 'Petrol - IOCL', 1800, 'debit', 'fuel', 27880),
        ('harshal', 'credit_card', '2026-05-20', 'Flipkart', 5499, 'debit', 'shopping', None),
        ('harshal', 'credit_card', '2026-05-16', 'Zomato', 890, 'debit', 'food', None),
    ]
    cur.executemany("INSERT INTO transactions (customer_id,account_type,date,description,amount,txn_type,category,balance_after) VALUES (?,?,?,?,?,?,?,?)", txns)

    # ── Collections (credit card delinquency for recovery campaign) ─────
    # customer_id, card_last4, total_outstanding, overdue_amount, minimum_due,
    # days_past_due, late_fee, penal_interest, last_payment_date,
    # last_payment_amount, due_date, bucket, status
    collections = [
        # Priya — mild, recently slipped (1-30 bucket)
        ('priya', '3391', 131080, 12000, 3446, 12, 500, 350, '2026-05-05', 5000, '2026-05-18', '1-30', 'Overdue'),
        # Amit — one missed cycle (1-30 bucket, near rollover)
        ('amit', '5567', 72500, 27500, 1375, 28, 500, 620, '2026-04-15', 5000, '2026-05-10', '1-30', 'Overdue'),
        # Harshal — deeper delinquency (31-60 bucket)
        ('harshal', '3342', 42000, 33000, 1650, 48, 750, 1180, '2026-03-20', 2000, '2026-04-12', '31-60', 'Overdue'),
    ]
    cur.executemany(
        "INSERT INTO collections "
        "(customer_id,card_last4,total_outstanding,overdue_amount,minimum_due,"
        "days_past_due,late_fee,penal_interest,last_payment_date,last_payment_amount,"
        "due_date,bucket,status) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        collections,
    )

    # ── Loan Collections (vehicle-loan delinquency for recovery campaign) ─
    # customer_id, loan_id, loan_type, vehicle, total_outstanding, emi_amount,
    # emis_overdue, overdue_amount, days_past_due, late_fee, penal_interest,
    # last_payment_date, last_payment_amount, due_date, bucket, status
    loan_collections = [
        # Amit — car loan CL-2025-10112, one EMI missed (1-30 bucket)
        ('amit', 'CL-2025-10112', 'Car Loan', 'Maruti Baleno (GJ-01-AB-4567)',
         520000, 12800, 1, 12800, 22, 600, 310, '2026-04-20', 12800, '2026-05-20', '1-30', 'Overdue'),
        # Harshal — car loan CL-2023-55678, two EMIs missed (31-60 bucket)
        ('harshal', 'CL-2023-55678', 'Car Loan', 'Hyundai Creta (MH-12-CD-8890)',
         480000, 14500, 2, 29000, 52, 1200, 1650, '2026-03-18', 14500, '2026-04-15', '31-60', 'Overdue'),
    ]
    cur.executemany(
        "INSERT INTO loan_collections "
        "(customer_id,loan_id,loan_type,vehicle,total_outstanding,emi_amount,"
        "emis_overdue,overdue_amount,days_past_due,late_fee,penal_interest,"
        "last_payment_date,last_payment_amount,due_date,bucket,status) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        loan_collections,
    )

    # ── Life Insurance Policies (premium recovery / persistency campaign) ─
    # These customers ALREADY hold a policy with us and have missed a premium.
    # (Viru deliberately has NO policy — he is a fresh sales prospect only.)
    # customer_id, policy_number, plan_name, plan_type, sum_assured,
    # premium_amount, premium_frequency, policy_start, policy_term_years,
    # premium_paying_term_years, premiums_paid, premiums_overdue,
    # total_overdue_amount, days_past_due, grace_period_days, grace_end_date,
    # revival_end_date, revival_interest, accrued_benefit, last_payment_date,
    # last_payment_amount, status
    life_policies = [
        # Harshal — guaranteed savings plan, still WITHIN the grace period.
        # One quarterly premium missed 18 days ago; grace ends 2026-08-09.
        ('harshal', 'CL-SAV-2022-778210', 'Contoso Life Guaranteed Savings', 'savings',
         1500000, 18750, 'Quarterly', '2022-05-10', 20, 15, 15, 1,
         18750, 18, 30, '2026-08-09', None, 0, 95000, '2026-04-10', 18750, 'In Grace'),
        # Sneha — pure term protection plan, LAPSED (missed the grace window).
        # Two monthly premiums overdue; cover is currently INACTIVE; revivable.
        ('sneha', 'CL-TRM-2021-556300', 'Contoso Life Smart Term Shield', 'protection',
         10000000, 4200, 'Monthly', '2021-08-01', 30, 30, 58, 2,
         8400, 53, 15, '2026-06-20', '2031-06-05', 340, 0, '2026-05-05', 4200, 'Lapsed'),
    ]
    cur.executemany(
        "INSERT INTO life_policies "
        "(customer_id,policy_number,plan_name,plan_type,sum_assured,premium_amount,"
        "premium_frequency,policy_start,policy_term_years,premium_paying_term_years,"
        "premiums_paid,premiums_overdue,total_overdue_amount,days_past_due,"
        "grace_period_days,grace_end_date,revival_end_date,revival_interest,"
        "accrued_benefit,last_payment_date,last_payment_amount,status) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        life_policies,
    )


def seed_savings_workflow(conn: sqlite3.Connection):
    """Seed synthetic incomplete applications for Asha's managed campaign."""
    from savings_account_tools import APPROVED_PRODUCT_SNAPSHOT, initialize_savings_schema

    initialize_savings_schema(conn)
    version = APPROVED_PRODUCT_SNAPSHOT["version"]
    applications = [
        (
            "SA-2026-VIRU-001", "viru", "INCOMPLETE", "2026-07-29T14:20:00+05:30",
            "mobile number verification", None, None, version,
            "2026-07-29T14:20:00+05:30", "2026-07-29T14:24:00+05:30",
        ),
        (
            "SA-2026-PRIYA-001", "priya", "INCOMPLETE", "2026-07-30T10:05:00+05:30",
            "basic personal details", None, None, version,
            "2026-07-30T10:05:00+05:30", "2026-07-30T10:11:00+05:30",
        ),
        (
            "SA-2026-AMIT-001", "amit", "INCOMPLETE", "2026-07-31T17:40:00+05:30",
            "mobile number verification", None, None, version,
            "2026-07-31T17:40:00+05:30", "2026-07-31T17:44:00+05:30",
        ),
    ]
    conn.executemany(
        "INSERT INTO savings_applications "
        "(application_id,customer_id,status,started_at,last_completed_step,selected_product,"
        "confirmed_product,snapshot_version,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
        applications,
    )
    conn.executemany(
        "INSERT INTO serviceable_pin_codes (pin_code,city,state,serviceable) VALUES (?,?,?,?)",
        [
            ("400050", "Mumbai", "Maharashtra", 1),
            ("400001", "Mumbai", "Maharashtra", 1),
            ("500034", "Hyderabad", "Telangana", 1),
            ("500081", "Hyderabad", "Telangana", 1),
            ("380058", "Ahmedabad", "Gujarat", 1),
            ("560034", "Bengaluru", "Karnataka", 1),
            ("560066", "Bengaluru", "Karnataka", 1),
            ("560001", "Bengaluru", "Karnataka", 1),
            ("110001", "New Delhi", "Delhi", 1),
            ("110002", "New Delhi", "Delhi", 1),
            ("600001", "Chennai", "Tamil Nadu", 1),
            ("700001", "Kolkata", "West Bengal", 1),
            ("411001", "Pune", "Maharashtra", 1),
            ("122001", "Gurugram", "Haryana", 1),
            ("201301", "Noida", "Uttar Pradesh", 1),
            ("500032", "unserviceable area", "Hyderabad", 0),
        ],
    )


def main():
    if DB_PATH.exists():
        DB_PATH.unlink()
        print(f"Removed old {DB_PATH}")

    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    cur = conn.cursor()

    print("Creating schema...")
    create_schema(cur)

    print("Seeding data...")
    seed_data(cur)
    seed_savings_workflow(conn)

    conn.commit()

    # Summary
    tables = cur.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    print(f"\nDatabase: {DB_PATH}")
    print(f"Tables: {len(tables)}")
    for (t,) in tables:
        count = cur.execute(f"SELECT COUNT(*) FROM [{t}]").fetchone()[0]
        print(f"  {t}: {count} rows")

    conn.close()
    print("\nDone!")


if __name__ == "__main__":
    main()
