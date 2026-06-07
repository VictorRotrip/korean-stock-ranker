import psycopg2, os
from dotenv import load_dotenv
load_dotenv()
c = psycopg2.connect(os.getenv("DATABASE_URL"))
cur = c.cursor()
cur.execute("""
    SELECT date FROM ranking_snapshots
    WHERE universe_name = 'krx_all_historical'
      AND ranking_system_id = 'p123-inspired'
    ORDER BY date
""")
dates = [r[0] for r in cur.fetchall()]
print(f"  Total snapshots: {len(dates)} of 137 expected")
if len(dates) > 1:
    print(f"  Range: {dates[0]} to {dates[-1]}")
