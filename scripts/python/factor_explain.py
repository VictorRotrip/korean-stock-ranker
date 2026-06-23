"""Build a human-readable "worked calculation" for each factor.

Given the exact inputs the factor engine used (the TTM `fin` dict, prior-year
`prior`, market cap, shares) and the resulting raw value, produce a string like:

    "Revenue 3,046억 / Total assets 3,890억 = 0.7837"

so the dashboard can show the formula with real numbers filled in, ending in
the value we actually output for that factor. Fundamental (arithmetic) factors
get a filled formula; price/technical factors get a short method note, because
they're computed over a price series or peer rankings rather than a one-line
ratio.

This is display metadata only — the authoritative computation stays in the
factor calculators. The "= <result>" uses the engine's own output, so the shown
math always matches what we ranked on.
"""


def _fmt(v):
    """Compact KRW formatting (조/억) for large values, plain for small."""
    if v is None:
        return "n/a"
    try:
        v = float(v)
    except (TypeError, ValueError):
        return str(v)
    a = abs(v)
    sign = "-" if v < 0 else ""
    if a >= 1e12:
        return "{0}{1:.2f}조".format(sign, a / 1e12)
    if a >= 1e8:
        return "{0}{1:.0f}억".format(sign, a / 1e8)
    return "{:,.0f}".format(v)


def _num(v):
    if v is None:
        return "n/a"
    try:
        return "{:.4f}".format(float(v))
    except (TypeError, ValueError):
        return str(v)


def _ev(mc, debt, cash):
    if mc is None:
        return None
    return mc + (debt or 0) - (cash or 0)


def _g(fin, key):
    return fin.get(key) if fin else None


# Each builder takes (fin, prior, mc, sh) and returns the expression string with
# real numbers substituted (no "= result" — that's appended by build_explain).
def _ev_str(mc, debt, cash):
    return "EV {0} (= mktcap {1} + debt {2} − cash {3})".format(
        _fmt(_ev(mc, debt, cash)), _fmt(mc), _fmt(debt), _fmt(cash))


EXPR_BUILDERS = {
    "asset_turnover_ttm": lambda f, p, mc, sh:
        "Revenue {0} / Total assets {1}".format(_fmt(_g(f, "revenue")), _fmt(_g(f, "total_assets"))),
    "cash_to_assets": lambda f, p, mc, sh:
        "Cash {0} / Total assets {1}".format(
            _fmt(_g(f, "cash_and_equivalents") if _g(f, "cash_and_equivalents") is not None else _g(f, "cash")),
            _fmt(_g(f, "total_assets"))),
    "debt_to_equity": lambda f, p, mc, sh:
        "Total debt {0} / Total equity {1}".format(_fmt(_g(f, "total_debt")), _fmt(_g(f, "total_equity"))),
    "dividend_yield": lambda f, p, mc, sh:
        "|Dividends paid {0}| / Market cap {1}".format(_fmt(_g(f, "dividends_paid")), _fmt(mc)),
    "ebitda_ev": lambda f, p, mc, sh:
        "EBITDA {0} / {1}".format(_fmt(_g(f, "ebitda")), _ev_str(mc, _g(f, "total_debt"), _g(f, "cash"))),
    "ev_sales_ttm_inv": lambda f, p, mc, sh:
        "Revenue {0} / {1}".format(_fmt(_g(f, "revenue")), _ev_str(mc, _g(f, "total_debt"), _g(f, "cash"))),
    "gross_profit_ev": lambda f, p, mc, sh:
        "Gross profit {0} / {1}".format(_fmt(_g(f, "gross_profit")), _ev_str(mc, _g(f, "total_debt"), _g(f, "cash"))),
    "ufcf_ev": lambda f, p, mc, sh:
        "(OCF {0} − Capex {1} + 0.8 × Interest {2}) / {3}".format(
            _fmt(_g(f, "operating_cash_flow")), _fmt(_g(f, "capital_expenditure")),
            _fmt(_g(f, "interest_expense")), _ev_str(mc, _g(f, "total_debt"), _g(f, "cash"))),
    "fcf_mcap": lambda f, p, mc, sh:
        "Free cash flow {0} / Market cap {1}".format(_fmt(_g(f, "free_cash_flow")), _fmt(mc)),
    "ocf_mcap": lambda f, p, mc, sh:
        "Operating cash flow {0} / Market cap {1}".format(_fmt(_g(f, "operating_cash_flow")), _fmt(mc)),
    "fcf_to_assets": lambda f, p, mc, sh:
        "Free cash flow {0} / Total assets {1}".format(_fmt(_g(f, "free_cash_flow")), _fmt(_g(f, "total_assets"))),
    "gross_margin_ttm": lambda f, p, mc, sh:
        "Gross profit {0} / Revenue {1}".format(_fmt(_g(f, "gross_profit")), _fmt(_g(f, "revenue"))),
    "gross_profit_assets": lambda f, p, mc, sh:
        "Gross profit {0} / Total assets {1}".format(_fmt(_g(f, "gross_profit")), _fmt(_g(f, "total_assets"))),
    "operating_margin_ttm": lambda f, p, mc, sh:
        "Operating income {0} / Revenue {1}".format(_fmt(_g(f, "operating_income")), _fmt(_g(f, "revenue"))),
    "interest_coverage_ttm": lambda f, p, mc, sh:
        "EBITDA {0} / |Interest expense {1}|".format(_fmt(_g(f, "ebitda")), _fmt(_g(f, "interest_expense"))),
    "pe_ttm_inv": lambda f, p, mc, sh:
        "Net income {0} / Market cap {1}".format(_fmt(_g(f, "net_income")), _fmt(mc)),
    "price_sales_ttm_inv": lambda f, p, mc, sh:
        "Revenue {0} / Market cap {1}".format(_fmt(_g(f, "revenue")), _fmt(mc)),
    "price_book": lambda f, p, mc, sh:
        "Total equity {0} / Market cap {1}".format(_fmt(_g(f, "total_equity")), _fmt(mc)),
    "roa_ttm": lambda f, p, mc, sh:
        "Net income {0} / Total assets {1}".format(_fmt(_g(f, "net_income")), _fmt(_g(f, "total_assets"))),
    "roe_ttm": lambda f, p, mc, sh:
        "Net income {0} / Total equity {1}".format(_fmt(_g(f, "net_income")), _fmt(_g(f, "total_equity"))),
    "log_market_cap": lambda f, p, mc, sh:
        "ln(Market cap {0})".format(_fmt(mc)),
    "market_cap": lambda f, p, mc, sh:
        "Market cap {0}".format(_fmt(mc)),
    # Growth factors: (current − prior) / |prior|
    "sales_growth_yoy": lambda f, p, mc, sh:
        "(Revenue {0} − prior {1}) / |prior {1}|".format(_fmt(_g(f, "revenue")), _fmt(_g(p, "revenue"))),
    "op_income_growth_yoy": lambda f, p, mc, sh:
        "(Operating income {0} − prior {1}) / |prior {1}|".format(_fmt(_g(f, "operating_income")), _fmt(_g(p, "operating_income"))),
    "net_income_growth_yoy": lambda f, p, mc, sh:
        "(Net income {0} − prior {1}) / |prior {1}|".format(_fmt(_g(f, "net_income")), _fmt(_g(p, "net_income"))),
    "ocf_growth_yoy": lambda f, p, mc, sh:
        "(OCF {0} − prior {1}) / |prior {1}|".format(_fmt(_g(f, "operating_cash_flow")), _fmt(_g(p, "operating_cash_flow"))),
    "fcf_growth_yoy": lambda f, p, mc, sh:
        "(FCF {0} − prior {1}) / |prior {1}|".format(_fmt(_g(f, "free_cash_flow")), _fmt(_g(p, "free_cash_flow"))),
    "eps_growth_yoy": lambda f, p, mc, sh:
        "(EPS {0} − prior EPS {1}) / |prior EPS {1}|".format(_num(_g(f, "eps")), _num(_g(p, "eps"))),
    "buyback_yield_yoy": lambda f, p, mc, sh:
        "(prior shares {0} − shares {1}) / shares {1}".format(_fmt(_g(p, "shares_outstanding")), _fmt(_g(f, "shares_outstanding"))),
}


def build_explain(factor_id, fin, prior, market_cap, shares, raw, description=None):
    """Return a worked-calculation string for one factor."""
    builder = EXPR_BUILDERS.get(factor_id)
    if builder is not None and fin is not None:
        try:
            expr = builder(fin, prior or {}, market_cap, shares)
        except Exception:
            expr = None
        if expr:
            return "{0} = {1}".format(expr, _num(raw) if raw is not None else "n/a")
    # Price / technical / peer-ranked factors: state the method instead.
    if description:
        return "Method: {0}".format(description)
    return None
