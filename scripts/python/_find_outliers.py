import psycopg2, os
from dotenv import load_dotenv
load_dotenv()
c = psycopg2.connect(os.getenv("DATABASE_URL2") or os.getenv("DATABASE_URL"))
cur = c.cursor()
cur.execute("""
    SELECT bfr.snapshot_date, bfr.ticker, s.name, s.name_en,
           bfr.forward_return * 100 AS ret_pct,
           bfr.start_close, bfr.end_close
    FROM backtest_forward_returns bfr
    LEFT JOIN stocks s ON s.ticker = bfr.ticker
    WHERE bfr.horizon_days = 30
      AND ABS(bfr.forward_return) > 1.0
    ORDER BY ABS(bfr.forward_return) DESC
    LIMIT 30
""")
print(f"{'date':<12}{'ticker':<8}{'name':<25}{'return':<12}{'start':<10}{'end':<10}")
for r in cur.fetchall():
    nm = (r[3] or r[2] or "")[:24]
    print(f"{str(r[0]):<12}{r[1]:<8}{nm:<25}{r[4]:>+9.0f}%   {r[5]:<10.0f}{r[6]:<10.0f}")
