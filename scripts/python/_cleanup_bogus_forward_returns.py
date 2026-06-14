import psycopg2, os
from dotenv import load_dotenv
load_dotenv()
c = psycopg2.connect(os.getenv("DATABASE_URL2") or os.getenv("DATABASE_URL"))
cur = c.cursor()
cur.execute("SELECT MAX(date) FROM daily_prices")
max_data = cur.fetchone()[0]
print(f"max daily_prices date: {max_data}")
cur.execute("""
    DELETE FROM backtest_forward_returns
    WHERE (snapshot_date + (horizon_days * INTERVAL '1 day'))::date > %s
""", (max_data,))
n = cur.rowcount
c.commit()
print(f"deleted {n:,} bogus forward-return rows where target_end > max_data_date")
