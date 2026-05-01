"""Quick diagnostic: find the working pykrx API patterns."""
import sys

try:
    from pykrx import stock
    print("pykrx imported OK")
except ImportError:
    print("ERROR: pykrx not installed")
    sys.exit(1)

import pykrx
print("pykrx version:", getattr(pykrx, '__version__', 'unknown'))

# Test 1: ticker list variations
print("\n--- Test 1a: get_market_ticker_list('20241227', market='KOSPI') ---")
try:
    t = stock.get_market_ticker_list("20241227", market="KOSPI")
    print("  Got {} tickers".format(len(t)))
    if t: print("  First 5:", t[:5])
except Exception as e:
    print("  ERROR:", e)

print("\n--- Test 1b: get_market_ticker_list('20241227') [no market] ---")
try:
    t = stock.get_market_ticker_list("20241227")
    print("  Got {} tickers".format(len(t)))
    if t: print("  First 5:", t[:5])
except Exception as e:
    print("  ERROR:", e)

print("\n--- Test 1c: get_market_ticker_list(market='KOSPI') [no date] ---")
try:
    t = stock.get_market_ticker_list(market="KOSPI")
    print("  Got {} tickers".format(len(t)))
    if t: print("  First 5:", t[:5])
except Exception as e:
    print("  ERROR:", e)

print("\n--- Test 1d: get_market_ticker_list() [no args] ---")
try:
    t = stock.get_market_ticker_list()
    print("  Got {} tickers".format(len(t)))
    if t: print("  First 5:", t[:5])
except Exception as e:
    print("  ERROR:", e)

# Test 2: per-ticker OHLCV (known working)
print("\n--- Test 2: Per-ticker OHLCV 005930 (5 days) ---")
try:
    df = stock.get_market_ohlcv("20241223", "20241227", "005930")
    print("  Shape:", df.shape)
    print("  Columns:", list(df.columns))
    print(df)
except Exception as e:
    print("  ERROR:", e)

# Test 3: per-ticker market cap
print("\n--- Test 3a: get_market_cap('20241223', '20241227', '005930') ---")
try:
    df = stock.get_market_cap("20241223", "20241227", "005930")
    print("  Shape:", df.shape)
    print("  Columns:", list(df.columns))
    print(df)
except Exception as e:
    print("  ERROR:", e)

print("\n--- Test 3b: get_market_cap_by_ticker('20241227', market='KOSPI') ---")
try:
    df = stock.get_market_cap_by_ticker("20241227", market="KOSPI")
    print("  Shape:", df.shape)
    print("  Columns:", list(df.columns))
    if not df.empty:
        print("  005930 in index?", "005930" in df.index)
        if "005930" in df.index:
            print("  005930:", df.loc["005930"].to_dict())
except Exception as e:
    print("  ERROR:", e)

# Test 4: per-ticker fundamentals
print("\n--- Test 4a: get_market_fundamental('20241223', '20241227', '005930') ---")
try:
    df = stock.get_market_fundamental("20241223", "20241227", "005930")
    print("  Shape:", df.shape)
    print("  Columns:", list(df.columns))
    print(df)
except Exception as e:
    print("  ERROR:", e)

print("\n--- Test 4b: get_market_fundamental_by_ticker('20241227', market='KOSPI') ---")
try:
    df = stock.get_market_fundamental_by_ticker("20241227", market="KOSPI")
    print("  Shape:", df.shape)
    print("  Columns:", list(df.columns))
    if not df.empty:
        print("  First 3 rows:")
        print(df.head(3))
except Exception as e:
    print("  ERROR:", e)

# Test 5: ticker name
print("\n--- Test 5: get_market_ticker_name('005930') ---")
try:
    name = stock.get_market_ticker_name("005930")
    print("  Name:", name)
except Exception as e:
    print("  ERROR:", e)

# Test 6: short selling
print("\n--- Test 6a: get_shorting_volume_by_ticker('20250401', market='KOSPI') ---")
try:
    df = stock.get_shorting_volume_by_ticker("20250401", market="KOSPI")
    print("  Shape:", df.shape)
    print("  Empty?", df is None or df.empty)
    if df is not None and not df.empty:
        print("  Columns:", list(df.columns))
except Exception as e:
    print("  ERROR:", e)

print("\n--- Done ---")
