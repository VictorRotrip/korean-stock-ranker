"""Fundamental factor calculators.

Functions take:
- fin: dict with keys from financial_statements (revenue, net_income, etc.) - latest period
- prior: dict with same keys - prior period (for growth calcs)
- market_cap: float - latest market cap
- shares_outstanding: float - latest shares outstanding

Return float or None.
"""


def safe_div(a, b):
    """Safe division returning None if inputs are invalid."""
    if a is None or b is None or b == 0:
        return None
    return float(a) / float(b)


def _ev(market_cap, total_debt, cash):
    """Enterprise Value = Market Cap + Debt - Cash."""
    if market_cap is None:
        return None
    d = total_debt if total_debt is not None else 0
    c = cash if cash is not None else 0
    ev = market_cap + d - c
    return ev if ev > 0 else None


def _growth(current, prior):
    """Year-over-year growth rate."""
    if current is None or prior is None or prior == 0:
        return None
    return (float(current) - float(prior)) / abs(float(prior))


# =========================================================================
# VALUE
# =========================================================================

def calc_earnings_yield(fin, prior, market_cap, shares):
    """Net Income / Market Cap."""
    if fin is None:
        return None
    return safe_div(fin.get("net_income"), market_cap)


def calc_ebitda_ev(fin, prior, market_cap, shares):
    """EBITDA / Enterprise Value."""
    if fin is None:
        return None
    ev = _ev(market_cap, fin.get("total_debt"), fin.get("cash"))
    return safe_div(fin.get("ebitda"), ev)


def calc_sales_yield(fin, prior, market_cap, shares):
    """Revenue / Market Cap."""
    if fin is None:
        return None
    return safe_div(fin.get("revenue"), market_cap)


def calc_revenue_ev(fin, prior, market_cap, shares):
    """Revenue / Enterprise Value."""
    if fin is None:
        return None
    ev = _ev(market_cap, fin.get("total_debt"), fin.get("cash"))
    return safe_div(fin.get("revenue"), ev)


def calc_gross_profit_ev(fin, prior, market_cap, shares):
    """Gross Profit / Enterprise Value."""
    if fin is None:
        return None
    ev = _ev(market_cap, fin.get("total_debt"), fin.get("cash"))
    return safe_div(fin.get("gross_profit"), ev)


def calc_fcf_yield(fin, prior, market_cap, shares):
    """Free Cash Flow / Market Cap."""
    if fin is None:
        return None
    return safe_div(fin.get("free_cash_flow"), market_cap)


def calc_ocf_yield(fin, prior, market_cap, shares):
    """Operating Cash Flow / Market Cap."""
    if fin is None:
        return None
    return safe_div(fin.get("operating_cash_flow"), market_cap)


def calc_ufcf_ev(fin, prior, market_cap, shares):
    """Unlevered FCF / EV = (OCF - Capex + 0.8*Interest) / EV."""
    if fin is None:
        return None
    ocf = fin.get("operating_cash_flow")
    capex = fin.get("capital_expenditure")
    interest = fin.get("interest_expense")
    if ocf is None:
        return None
    ev = _ev(market_cap, fin.get("total_debt"), fin.get("cash"))
    if ev is None:
        return None
    cap = abs(capex) if capex is not None else 0
    ie = abs(interest) if interest is not None else 0
    ufcf = ocf - cap + 0.8 * ie
    return ufcf / ev if ev != 0 else None


def calc_book_to_market(fin, prior, market_cap, shares):
    """Total Equity / Market Cap."""
    if fin is None:
        return None
    return safe_div(fin.get("total_equity"), market_cap)


def calc_dividend_yield(fin, prior, market_cap, shares):
    """abs(Dividends Paid) / Market Cap."""
    if fin is None:
        return None
    div = fin.get("dividends_paid")
    if div is None:
        return None
    return safe_div(abs(div), market_cap)


# =========================================================================
# QUALITY
# =========================================================================

def calc_operating_margin(fin, prior, market_cap, shares):
    """Operating Income / Revenue."""
    if fin is None:
        return None
    return safe_div(fin.get("operating_income"), fin.get("revenue"))


def calc_gross_margin(fin, prior, market_cap, shares):
    """Gross Profit / Revenue."""
    if fin is None:
        return None
    return safe_div(fin.get("gross_profit"), fin.get("revenue"))


def calc_roe(fin, prior, market_cap, shares):
    """Net Income / Total Equity."""
    if fin is None:
        return None
    return safe_div(fin.get("net_income"), fin.get("total_equity"))


def calc_roa(fin, prior, market_cap, shares):
    """Net Income / Total Assets."""
    if fin is None:
        return None
    return safe_div(fin.get("net_income"), fin.get("total_assets"))


def calc_gross_profit_assets(fin, prior, market_cap, shares):
    """Gross Profit / Total Assets (Novy-Marx)."""
    if fin is None:
        return None
    return safe_div(fin.get("gross_profit"), fin.get("total_assets"))


def calc_asset_turnover(fin, prior, market_cap, shares):
    """Revenue / Total Assets."""
    if fin is None:
        return None
    return safe_div(fin.get("revenue"), fin.get("total_assets"))


def calc_debt_to_equity(fin, prior, market_cap, shares):
    """Total Debt / Total Equity."""
    if fin is None:
        return None
    debt = fin.get("total_debt")
    if debt is None:
        debt = fin.get("total_liabilities")
    return safe_div(debt, fin.get("total_equity"))


def calc_interest_coverage(fin, prior, market_cap, shares):
    """EBITDA / abs(Interest Expense)."""
    if fin is None:
        return None
    ie = fin.get("interest_expense")
    if ie is None:
        return None
    return safe_div(fin.get("ebitda"), abs(ie) if ie != 0 else None)


# =========================================================================
# GROWTH
# =========================================================================

def calc_sales_growth_yoy(fin, prior, market_cap, shares):
    """YoY revenue growth."""
    if fin is None or prior is None:
        return None
    return _growth(fin.get("revenue"), prior.get("revenue"))


def calc_op_income_growth_yoy(fin, prior, market_cap, shares):
    """YoY operating income growth."""
    if fin is None or prior is None:
        return None
    return _growth(fin.get("operating_income"), prior.get("operating_income"))


def calc_eps_growth_yoy(fin, prior, market_cap, shares):
    """YoY EPS growth."""
    if fin is None or prior is None:
        return None
    return _growth(fin.get("eps"), prior.get("eps"))


def calc_net_income_growth_yoy(fin, prior, market_cap, shares):
    """YoY net income growth."""
    if fin is None or prior is None:
        return None
    return _growth(fin.get("net_income"), prior.get("net_income"))


def calc_ocf_growth_yoy(fin, prior, market_cap, shares):
    """YoY operating cash flow growth.

    OCF can be negative for both current and prior periods. _growth uses
    abs(prior) in the denominator so a sign-flip-to-positive yields a
    positive growth number, which is the desired behavior.
    """
    if fin is None or prior is None:
        return None
    return _growth(fin.get("operating_cash_flow"),
                    prior.get("operating_cash_flow"))


def calc_fcf_growth_yoy(fin, prior, market_cap, shares):
    """YoY free cash flow growth.

    FCF is often negative for growth-stage companies; same abs-denominator
    convention as OCF growth applies.
    """
    if fin is None or prior is None:
        return None
    return _growth(fin.get("free_cash_flow"),
                    prior.get("free_cash_flow"))


def calc_buyback_yield_yoy(fin, prior, market_cap, shares):
    """Buyback yield = (prior_shares - current_shares) / current_shares.

    Interpretation:
      positive = shares decreased = net buyback / treasury retirement
      zero     = unchanged
      negative = shares increased = issuance / dilution

    A clean factor for shareholder return; sibling of dividend yield.
    Especially relevant in Korea since the 2024 Value-Up policy push
    that's driven a wave of corporate buybacks.

    Caveat: stock splits / consolidations would show as huge spurious
    changes. Korean splits are rare; percentile-ranking will compress
    extreme values into the top/bottom buckets without affecting the
    rank order of the meaningful (single-digit-percent) buyback signals.

    `fin` and `prior` must both have `shares_outstanding`. Returns None
    if either is missing or zero.
    """
    if fin is None or prior is None:
        return None
    cur = fin.get("shares_outstanding")
    pri = prior.get("shares_outstanding")
    if cur is None or pri is None:
        return None
    if cur == 0:
        return None
    return (float(pri) - float(cur)) / float(cur)
