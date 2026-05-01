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
