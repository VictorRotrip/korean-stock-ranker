"""Sentiment factor calculators.

Most sentiment factors require paid estimate data (FnGuide, Bloomberg, etc.)
and are marked as unavailable. This module contains stubs and any
implementable proxies.
"""


def calc_short_interest_pct(short_data, shares_outstanding):
    """Short balance as percentage of shares outstanding.

    Args:
        short_data: dict with short_balance, short_ratio, etc.
        shares_outstanding: float

    Returns: float (ratio, 0-1) or None
    """
    if short_data is None:
        return None

    # Try short_balance / shares_outstanding first
    sb = short_data.get("short_balance")
    if sb is not None and shares_outstanding is not None and shares_outstanding > 0:
        return sb / shares_outstanding

    # Fallback to short_ratio if available (already a percentage)
    sr = short_data.get("short_ratio")
    if sr is not None:
        return sr / 100.0

    return None


def calc_insider_net_buying_90d(insider_data, market_cap, shares_outstanding):
    """Net insider net-buying signal — trailing 90 days.

    Quant interpretation: corporate insiders (executives, board members,
    10%+ shareholders) have informational advantages about their companies'
    near-term prospects. Net buying that they're voluntarily putting their
    own capital into the stock is a positive sentiment signal; net selling
    is negative.

    Formula:
        net_dollars = net_shares × shares_to_dollar_proxy
        signal = net_dollars / market_cap

    Where shares_to_dollar_proxy ≈ market_cap / shares_outstanding (= price
    per share). So in practice:
        signal = (net_shares × price_per_share) / market_cap
              ≈ net_shares / shares_outstanding

    Output range: typically -0.02 to +0.02 (i.e. -2% to +2% of outstanding
    shares). Higher = more bullish.

    Args:
        insider_data: dict from get_insider_transactions_window() with
                      net_shares / buy_shares / sell_shares / etc. May be
                      None if the ticker has no `insider_transactions` rows
                      at all, or {} if no activity in window.
        market_cap: latest market cap (KRW). Not strictly needed for the
                    shares-normalized version, but accepted for API
                    consistency.
        shares_outstanding: total shares outstanding (used as denominator).

    Returns: float (signed ratio) or None when inputs aren't usable.
    """
    if insider_data is None:
        return None
    if shares_outstanding is None or shares_outstanding <= 0:
        return None
    net = insider_data.get("net_shares", 0)
    n_filings = insider_data.get("n_filings", 0)
    # If there were no filings in the window the signal is meaningfully
    # "neutral" (0.0) rather than missing. Return 0 so the stock can still
    # be ranked rather than dropping out of the Sentiment category.
    if n_filings == 0:
        return 0.0
    return net / shares_outstanding


# =========================================================================
# UNAVAILABLE — require paid estimate provider
# =========================================================================
# These functions are stubs. They will return None until a real data source
# (FnGuide, QuantiWise, Bloomberg, FactSet, Refinitiv/LSEG) is connected.

def calc_eps_revision_fy(*args, **kwargs):
    """UNAVAILABLE: Requires consensus estimate data."""
    return None

def calc_eps_surprise_q1(*args, **kwargs):
    """UNAVAILABLE: Requires consensus estimate data."""
    return None

def calc_avg_recommendation(*args, **kwargs):
    """UNAVAILABLE: Requires analyst recommendation data."""
    return None
