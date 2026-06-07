import psycopg2, os
from dotenv import load_dotenv
load_dotenv()
c = psycopg2.connect(os.getenv("DATABASE_URL2") or os.getenv("DATABASE_URL"))
cur = c.cursor()
cur.execute("""
    SELECT industry, count(*) AS n
    FROM stocks
    WHERE industry IS NOT NULL
    GROUP BY industry
    ORDER BY n DESC
""")
for row in cur.fetchall():
    print(f"{row[1]:5} | {row[0]}")
