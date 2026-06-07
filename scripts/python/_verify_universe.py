import psycopg2, os
from dotenv import load_dotenv
load_dotenv()
c = psycopg2.connect(os.getenv("DATABASE_URL"))
cur = c.cursor()

# 1. How many delisted stocks are even in the historical universe?
cur.execute("""
    SELECT s.is_active, count(*) AS in_universe
    FROM universe_memberships um
    JOIN stocks s ON s.ticker = um.ticker
    WHERE um.universe_name = 'krx_all_historical'
    GROUP BY s.is_active
""")
print("=== krx_all_historical membership ===")
for r in cur.fetchall():
    print(f"  is_active={r[0]:<6} count={r[1]}")

# 2. For a few historical dates, who had prices that day?
print("\n=== Active vs delisted stocks with prices on specific dates ===")
print(f"{'date':<14}{'total_with_price':<20}{'currently_active':<20}{'currently_delisted'}")
for d in ["2015-01-30", "2017-06-30", "2020-05-29", "2024-12-30"]:
    cur.execute("""
        SELECT s.is_active, count(*)
        FROM daily_prices dp
        JOIN stocks s ON s.ticker = dp.ticker
        WHERE dp.date = %s
        GROUP BY s.is_active
    """, (d,))
    counts = dict(cur.fetchall())
    active = counts.get(True, 0)
    delisted = counts.get(False, 0)
    print(f"{d:<14}{active+delisted:<20}{active:<20}{delisted}")

# 3. Sanity check — of the 897 delisted stocks, what fraction had a price on each date?
print("\n=== Delisted-stock representation across dates ===")
for d in ["2015-01-30", "2017-06-30", "2020-05-29", "2024-12-30"]:
    cur.execute("""
        SELECT count(DISTINCT dp.ticker)
        FROM daily_prices dp
        JOIN stocks s ON s.ticker = dp.ticker
        WHERE dp.date = %s AND s.is_active = FALSE
    """, (d,))
    n = cur.fetchone()[0]
    print(f"  {d}:  {n} of 897 delisted stocks were trading that day")
