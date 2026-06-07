import psycopg2, os
from dotenv import load_dotenv
load_dotenv()

# Sample of tickers from the "missing on 2015-01-30" list in the log
SAMPLE = [
    "002690", "002800", "003380", "004440", "006620",
    "007680", "009900", "010400", "0013V0", "00279K",
]

c = psycopg2.connect(os.getenv("DATABASE_URL"))
cur = c.cursor()
cur.execute("""
    SELECT ticker,
           min(date) AS first_seen,
           max(date) AS last_seen,
           count(*)  AS trading_days,
           count(market_cap) AS days_with_mcap
    FROM daily_prices
    WHERE ticker = ANY(%s)
    GROUP BY ticker
    ORDER BY first_seen
""", (SAMPLE,))
print(f"{'ticker':<8}{'first_seen':<14}{'last_seen':<14}{'days':<8}{'days_with_mcap':<16}{'verdict'}")
for row in cur.fetchall():
    ticker, first_seen, last_seen, n, n_mcap = row
    if first_seen and str(first_seen) > "2015-01-30":
        verdict = "IPO'd after 2015-01-30 — correctly excluded"
    elif last_seen and str(last_seen) < "2015-01-30":
        verdict = "delisted before 2015-01-30 — correctly excluded"
    elif n_mcap < n:
        verdict = "trading but marcap data gap"
    else:
        verdict = "should have been included??"
    print(f"{ticker:<8}{str(first_seen):<14}{str(last_seen):<14}{n:<8}{n_mcap:<16}{verdict}")
