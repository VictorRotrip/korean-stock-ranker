import psycopg2, os
from dotenv import load_dotenv
load_dotenv()
c = psycopg2.connect(os.getenv("DATABASE_URL"))
cur = c.cursor()
cur.execute("""
    SELECT is_active,
           count(*) AS total,
           count(sector) AS has_sector,
           count(industry) AS has_industry
    FROM stocks
    GROUP BY is_active
    ORDER BY is_active DESC
""")
print("  is_active | total | sector | industry")
for r in cur.fetchall():
    print("  {:9} | {:5} | {:6} | {:8}".format(str(r[0]), r[1], r[2], r[3]))
