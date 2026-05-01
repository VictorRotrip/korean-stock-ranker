"""Industry-level factor calculators.

These calculate aggregate metrics across stocks in the same industry/sector.
They are computed AFTER individual stock factors.
"""


def calc_industry_momentum(ticker, industry_map, all_price_returns, days=126):
    """Average return of stocks in the same industry over `days` trading days.

    Args:
        ticker: the stock to compute for
        industry_map: dict of ticker -> industry/sector string
        all_price_returns: dict of ticker -> return over `days` (precomputed)
        days: not used directly, returns are precomputed

    Returns: float (average return of industry peers, excluding self) or None
    """
    my_industry = industry_map.get(ticker)
    if not my_industry:
        return None

    peers = []
    for t, ind in industry_map.items():
        if ind == my_industry and t != ticker:
            ret = all_price_returns.get(t)
            if ret is not None:
                peers.append(ret)

    if len(peers) < 2:  # need at least 2 peers
        return None

    return sum(peers) / len(peers)
