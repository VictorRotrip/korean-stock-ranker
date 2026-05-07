"""Point-in-time-safe TTM (trailing twelve months) fundamentals.

DART quarterly filings report income statement and cash flow as cumulative
year-to-date values, NOT single-quarter values. This module:

  1) Loads PIT-safe rows from fundamental_snapshots
     (filtered: data_available_date <= as_of)
  2) Derives single-quarter values by subtraction:
        Q1 quarter = Q1 cumulative                       (raw)
        Q2 quarter = H1 cumulative  - Q1 cumulative      (derived)
        Q3 quarter = 9M cumulative  - H1 cumulative      (derived)
        Q4 quarter = annual         - 9M cumulative      (derived)
  3) Sums the trailing 4 single quarters when available, otherwise falls back
     to the latest annual report (with the source method recorded).

The output is a dict with three sections:

    {
      "income_ttm":  { revenue, gross_profit, operating_income, net_income,
                       operating_cash_flow, capex, free_cash_flow,
                       interest_expense, depreciation_amortization, eps },
      "balance":     { total_assets, total_equity, total_liabilities,
                       total_debt, cash_and_equivalents, inventory,
                       shares_outstanding },
      "meta": {
          "income_source":   ttm_quarterly | latest_annual | annual_fallback
                             | insufficient_quarterly_history | unavailable,
          "balance_source":  latest_quarterly | latest_annual | unavailable,
          "as_of":           ISO date string,
          "ttm_period_end":  date of the most recent single quarter used,
          "annual_period_end": fiscal year end of the annual fallback
                             (when used),
          "available_quarters": int (single-quarter slots actually derived),
      }
    }

PIT rule: every row read here passes data_available_date <= as_of. The TTM
window is built only from rows visible to the operator at as_of, so a
2025-12-30 ranking will never see an FY2025 annual report filed in 2026.
"""

from collections import defaultdict


# Single-quarter income-statement / cash-flow fields that ARE cumulative in
# DART interim filings and so need YTD subtraction to produce a per-quarter
# value. Balance-sheet items are point-in-time, NOT cumulative, and are
# handled separately.
INCOME_FIELDS = (
    "revenue",
    "gross_profit",
    "operating_income",
    "net_income",
    "operating_cash_flow",
    "capex",
    "free_cash_flow",
    "interest_expense",
    "depreciation_amortization",
)

# Per-share metrics — also cumulative YTD in interim filings.
PERSHARE_FIELDS = ("eps",)

# Balance-sheet stocks — never cumulative, just take the latest available row.
BALANCE_FIELDS = (
    "total_assets",
    "total_equity",
    "total_liabilities",
    "total_debt",
    "cash_and_equivalents",
    "inventory",
    "shares_outstanding",
)


# ---------------------------------------------------------------------------
# Loaders
# ---------------------------------------------------------------------------

def _load_pit_snapshots(conn, ticker, as_of):
    """Return PIT-safe rows from fundamental_snapshots for one ticker.

    Output is sorted by (period_end ASC, fiscal_quarter ASC NULLS LAST) so
    annual rows come last within a fiscal year (annuals have NULL quarter,
    treated as 'after' Q3).
    """
    cur = conn.cursor()
    cur.execute(
        """
        SELECT period_end, data_available_date,
               fiscal_year, fiscal_quarter, report_code,
               consolidated_or_separate,
               revenue, gross_profit, operating_income, net_income, eps,
               total_assets, total_equity, total_liabilities, total_debt,
               cash_and_equivalents, inventory,
               operating_cash_flow, capex, free_cash_flow,
               depreciation_amortization, interest_expense,
               ebitda,
               shares_outstanding, dividends_paid
          FROM fundamental_snapshots
         WHERE ticker = %s
           AND data_available_date <= %s
           AND consolidated_or_separate = 'consolidated'
         ORDER BY period_end ASC,
                  COALESCE(fiscal_quarter, 99) ASC
        """,
        (ticker, as_of),
    )
    cols = [d[0] for d in cur.description]
    rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    cur.close()
    return rows


def _bucket_by_fy_quarter(rows):
    """Bucket snapshots as {(fy, slot): row} where slot is one of:
        1 -> Q1 cumulative (report_code 11013)
        2 -> H1 cumulative (report_code 11012)
        3 -> 9M cumulative (report_code 11014)
        4 -> annual        (report_code 11011)
    Returns dict; later rows overwrite earlier within the same key (which is
    fine because the unique index on fundamental_snapshots only allows one
    row per (ticker, period_end, fiscal_quarter, consolidated_or_separate)).
    """
    rc_to_slot = {"11013": 1, "11012": 2, "11014": 3, "11011": 4}
    out = {}
    for r in rows:
        rc = r.get("report_code")
        slot = rc_to_slot.get(rc)
        if slot is None:
            # Try to infer from fiscal_quarter / period_end month if report_code
            # is missing (legacy rows).
            fq = r.get("fiscal_quarter")
            if fq in (1, 2, 3):
                slot = fq
            else:
                slot = 4  # treat unknowns as annual
        fy = r.get("fiscal_year")
        if fy is None:
            continue
        out[(fy, slot)] = r
    return out


# ---------------------------------------------------------------------------
# Quarterly derivation (cumulative -> single quarter)
# ---------------------------------------------------------------------------

def _per_quarter_values(buckets, fy, slot):
    """Return (per_quarter_dict, method) for the requested (fy, slot).

    method is 'raw' for Q1, 'derived' for Q2/Q3/Q4 (subtraction of YTD), and
    'unavailable' when prerequisites are missing.

    per_quarter_dict has only INCOME_FIELDS + PERSHARE_FIELDS keys (the ones
    that need YTD subtraction). Missing inputs yield None entries.
    """
    cur = buckets.get((fy, slot))
    if cur is None:
        return None, "unavailable"

    if slot == 1:
        # Q1: cumulative-YTD == single-quarter
        out = {f: cur.get(f) for f in INCOME_FIELDS + PERSHARE_FIELDS}
        return out, "raw"

    # For Q2, Q3, Q4 we need the previous YTD report.
    prev = buckets.get((fy, slot - 1))
    if prev is None:
        # Without the prior cumulative we can't derive the single-quarter
        # value. Mark unavailable rather than silently returning the
        # cumulative number (which would over-state the quarter).
        return None, "unavailable"

    out = {}
    for f in INCOME_FIELDS + PERSHARE_FIELDS:
        a = cur.get(f)
        b = prev.get(f)
        if a is None or b is None:
            out[f] = None
        else:
            out[f] = a - b
    return out, "derived"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_pit_fundamentals(conn, ticker, as_of):
    """Return PIT-safe fundamentals for `ticker` viewed from `as_of`.

    See module docstring for the output shape. Always returns a dict; if no
    rows exist the dict is populated with Nones and meta source is
    'unavailable'.
    """
    rows = _load_pit_snapshots(conn, ticker, as_of)

    income_ttm = {f: None for f in INCOME_FIELDS + PERSHARE_FIELDS}
    balance = {f: None for f in BALANCE_FIELDS}
    meta = {
        "income_source": "unavailable",
        "balance_source": "unavailable",
        "as_of": as_of,
        "ttm_period_end": None,
        "annual_period_end": None,
        "available_quarters": 0,
    }

    if not rows:
        return {"income_ttm": income_ttm, "balance": balance, "meta": meta}

    # Latest balance sheet: use the most recent row of any kind.
    latest_row = rows[-1]
    for f in BALANCE_FIELDS:
        balance[f] = latest_row.get(f)
    if latest_row.get("report_code") == "11011":
        meta["balance_source"] = "latest_annual"
    else:
        meta["balance_source"] = "latest_quarterly"

    # Build per-quarter buckets across fiscal years.
    buckets = _bucket_by_fy_quarter(rows)

    # Build a chronologically ordered sequence of single-quarter slots
    # (fy, slot=1..4). Then walk it backwards collecting up to 4 derivable
    # quarters that all sit on or before the latest cumulative report we
    # actually have.
    slot_keys = sorted(buckets.keys())  # list of (fy, slot)
    if not slot_keys:
        return {"income_ttm": income_ttm, "balance": balance, "meta": meta}

    # Compute per-quarter values for every (fy, slot) we have a cumulative
    # row for. The Q1 slot derivation never needs the previous slot, so it's
    # always available when the bucket exists; Q2/Q3/Q4 require slot-1.
    per_quarter = {}
    for key in slot_keys:
        fy, slot = key
        vals, method = _per_quarter_values(buckets, fy, slot)
        if vals is not None:
            per_quarter[(fy, slot)] = (vals, method)

    # Pick the trailing 4 single-quarter slots ending at the latest available
    # slot. Iterate descending across all (fy, slot) keys we successfully
    # derived, take the first 4 in reverse-chronological order.
    derivable = sorted(per_quarter.keys())  # ascending
    if derivable:
        last4 = derivable[-4:]
    else:
        last4 = []

    if len(last4) == 4:
        # Sum each field across the 4 quarters; if ANY of the 4 quarters has
        # None for a field, that TTM field stays None (no silent imputation).
        meta["available_quarters"] = 4
        meta["income_source"] = "ttm_quarterly"
        for f in INCOME_FIELDS + PERSHARE_FIELDS:
            vals = [per_quarter[k][0].get(f) for k in last4]
            if any(v is None for v in vals):
                income_ttm[f] = None
            else:
                income_ttm[f] = sum(vals)
        # The end of the TTM window is the period_end of the last cumulative
        # report that closes the window. Map (fy, slot) back to a row.
        last_fy, last_slot = last4[-1]
        last_row = buckets.get((last_fy, last_slot))
        if last_row is not None:
            meta["ttm_period_end"] = last_row.get("period_end")
    else:
        # Insufficient quarterly history. Try the latest annual fallback.
        annual_keys = [k for k in slot_keys if k[1] == 4]
        if annual_keys:
            ann_fy, _ = annual_keys[-1]
            ann_row = buckets[(ann_fy, 4)]
            meta["available_quarters"] = len(last4)
            if len(last4) >= 1:
                meta["income_source"] = "annual_fallback"
            else:
                meta["income_source"] = "latest_annual"
            for f in INCOME_FIELDS + PERSHARE_FIELDS:
                income_ttm[f] = ann_row.get(f)
            meta["annual_period_end"] = ann_row.get("period_end")
        else:
            meta["available_quarters"] = len(last4)
            meta["income_source"] = "insufficient_quarterly_history"

    return {"income_ttm": income_ttm, "balance": balance, "meta": meta}


def get_pit_fundamentals_prior_year(conn, ticker, as_of, ttm_period_end):
    """Return TTM fundamentals for the same trailing-window one year earlier.

    Used to compute YoY same-period growth. Implemented by simply requesting
    PIT fundamentals as_of (ttm_period_end - 1 year). If the resulting window
    is also a 4-quarter TTM, growth is comparable; if it's an annual fallback
    the caller can decide whether to compute growth at all.
    """
    if ttm_period_end is None:
        return None
    try:
        # ttm_period_end may be a date or a string
        if hasattr(ttm_period_end, "isoformat"):
            iso = ttm_period_end.isoformat()
        else:
            iso = str(ttm_period_end)
        year, month, day = iso[:10].split("-")
        prior_iso = "{0:04d}-{1}-{2}".format(int(year) - 1, month, day)
    except Exception:
        return None
    return get_pit_fundamentals(conn, ticker, prior_iso)


def yoy_quarter_growth(current_q, prior_q):
    """YoY growth rate for one field given two single-quarter numbers."""
    if current_q is None or prior_q is None or prior_q == 0:
        return None
    return (float(current_q) - float(prior_q)) / abs(float(prior_q))


def get_quarterly_history(conn, ticker, as_of, n=8):
    """Return the most recent n single-quarter values (income + EPS) ending
    at the latest derivable quarter as of `as_of`.

    Useful for same-quarter YoY growth and for diagnostics.

    Output is a list ordered oldest-first:
        [{"fiscal_year": int, "fiscal_quarter": 1..4,
          "period_end": date, "method": raw|derived,
          "values": {field: number}}, ...]
    """
    rows = _load_pit_snapshots(conn, ticker, as_of)
    if not rows:
        return []
    buckets = _bucket_by_fy_quarter(rows)

    derivable = []
    for key in sorted(buckets.keys()):
        fy, slot = key
        vals, method = _per_quarter_values(buckets, fy, slot)
        if vals is None:
            continue
        # period_end of this single quarter is the period_end of the
        # cumulative report at this slot
        cur_row = buckets[(fy, slot)]
        derivable.append({
            "fiscal_year": fy,
            "fiscal_quarter": slot,
            "period_end": cur_row.get("period_end"),
            "method": method,
            "values": vals,
        })
    return derivable[-n:]
