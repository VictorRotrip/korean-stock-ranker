"""Technical factor calculators.

All functions operate on price_history: list of (date, open, high, low, close, volume, trading_value, market_cap, shares_outstanding) tuples, sorted oldest-first.

Return float or None (None = missing data).
"""
import math
from statistics import median


def _closes(prices):
    """Extract close prices."""
    return [p[4] for p in prices if p[4] is not None and p[4] > 0]


def _volumes(prices):
    """Extract volumes."""
    return [p[5] for p in prices if p[5] is not None]


def _trading_values(prices):
    """Extract trading values (close * volume as proxy if not available)."""
    result = []
    for p in prices:
        tv = p[6]  # trading_value
        if tv is not None and tv > 0:
            result.append(tv)
        elif p[4] is not None and p[5] is not None and p[4] > 0 and p[5] > 0:
            result.append(p[4] * p[5])  # close * volume
        else:
            result.append(None)
    return result


def calc_price_change(prices, days=120, skip=0):
    """Price return over `days` trading days, optionally skipping recent `skip` days."""
    closes = _closes(prices)
    if len(closes) < days + skip + 1:
        return None
    end_idx = len(closes) - 1 - skip
    start_idx = end_idx - days
    if start_idx < 0:
        return None
    if closes[start_idx] == 0:
        return None
    return (closes[end_idx] - closes[start_idx]) / closes[start_idx]


def calc_momentum_12_1(prices):
    """12-month return skipping most recent month (Jegadeesh-Titman)."""
    return calc_price_change(prices, days=231, skip=21)


def calc_up_down_ratio(prices, days=20):
    """Ratio of up days to down days."""
    closes = _closes(prices)
    if len(closes) < days + 1:
        return None
    recent = closes[-(days + 1):]
    up = 0
    down = 0
    for i in range(1, len(recent)):
        if recent[i] > recent[i-1]:
            up += 1
        elif recent[i] < recent[i-1]:
            down += 1
    if down == 0:
        return 100.0 if up > 0 else 50.0  # all up or flat
    return up / down


def calc_rsi(prices, period=200):
    """Relative Strength Index."""
    closes = _closes(prices)
    if len(closes) < period + 1:
        return None
    recent = closes[-(period + 1):]
    gains = []
    losses = []
    for i in range(1, len(recent)):
        diff = recent[i] - recent[i-1]
        if diff > 0:
            gains.append(diff)
            losses.append(0)
        else:
            gains.append(0)
            losses.append(abs(diff))

    avg_gain = sum(gains) / len(gains)
    avg_loss = sum(losses) / len(losses)

    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def calc_volatility(prices, window=252):
    """Annualized volatility of daily returns."""
    closes = _closes(prices)
    if len(closes) < min(window, 30) + 1:
        return None
    recent = closes[-(window + 1):]
    returns = []
    for i in range(1, len(recent)):
        if recent[i-1] > 0:
            returns.append((recent[i] - recent[i-1]) / recent[i-1])
    if len(returns) < 20:
        return None
    mean_r = sum(returns) / len(returns)
    var = sum((r - mean_r) ** 2 for r in returns) / (len(returns) - 1)
    return math.sqrt(var) * math.sqrt(252)


def calc_max_drawdown(prices, window=252):
    """Maximum drawdown over window."""
    closes = _closes(prices)
    if len(closes) < min(window, 30):
        return None
    recent = closes[-window:]
    peak = recent[0]
    max_dd = 0.0
    for c in recent:
        if c > peak:
            peak = c
        dd = (peak - c) / peak if peak > 0 else 0
        if dd > max_dd:
            max_dd = dd
    return max_dd  # positive number, 0.0 = no drawdown


def calc_market_cap(prices):
    """Latest market cap."""
    for p in reversed(prices):
        if p[7] is not None and p[7] > 0:  # market_cap
            return float(p[7])
    return None


def calc_log_market_cap(prices):
    """Natural log of market cap."""
    mcap = calc_market_cap(prices)
    if mcap is None or mcap <= 0:
        return None
    return math.log(mcap)


def calc_avg_trading_value(prices, days=60):
    """Average daily trading value."""
    tvs = _trading_values(prices)
    recent = [v for v in tvs[-days:] if v is not None and v > 0]
    if len(recent) < 10:
        return None
    return sum(recent) / len(recent)


def calc_share_turnover(prices, days=65):
    """Median volume / shares outstanding."""
    if len(prices) < 10:
        return None
    recent = prices[-days:]
    volumes = [p[5] for p in recent if p[5] is not None and p[5] > 0]
    if len(volumes) < 10:
        return None
    med_vol = median(volumes)
    # Get latest shares outstanding
    shares = None
    for p in reversed(recent):
        if p[8] is not None and p[8] > 0:  # shares_outstanding
            shares = p[8]
            break
    if shares is None or shares == 0:
        return None
    return med_vol / shares


def calc_volume_increase(prices):
    """Avg volume last 13 days / Avg volume days 13-30."""
    if len(prices) < 30:
        return None
    recent_13 = [p[5] for p in prices[-13:] if p[5] is not None and p[5] > 0]
    older_17 = [p[5] for p in prices[-30:-13] if p[5] is not None and p[5] > 0]
    if len(recent_13) < 5 or len(older_17) < 5:
        return None
    avg_recent = sum(recent_13) / len(recent_13)
    avg_older = sum(older_17) / len(older_17)
    if avg_older == 0:
        return None
    return avg_recent / avg_older


def calc_dist_52w_high(prices):
    """Current price / 52-week high."""
    closes = _closes(prices)
    if len(closes) < 20:
        return None
    high_52w = max(closes[-252:]) if len(closes) >= 252 else max(closes)
    if high_52w <= 0:
        return None
    return closes[-1] / high_52w
