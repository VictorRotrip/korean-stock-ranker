import psycopg2, os
from dotenv import load_dotenv
load_dotenv()
c = psycopg2.connect(os.getenv("DATABASE_URL2") or os.getenv("DATABASE_URL"))
cur = c.cursor()
cur.execute("""
    SELECT count(*) total,
           count(name_en) has_name_en,
           count(*) FILTER (WHERE name_en IS NOT NULL AND name_en <> '') non_empty_en
    FROM stocks
""")
print("stocks:")
for k, v in zip(["total","has_name_en","non_empty_en"], cur.fetchone()):
    print(f"  {k:15} {v}")
cur.execute("SELECT ticker, name, name_en, industry FROM stocks WHERE name_en IS NOT NULL LIMIT 5")
print("\nExamples:")
for r in cur.fetchall():
    print(f"  {r[0]} | {r[1]:<12} | {r[2]:<30} | {r[3]}")
