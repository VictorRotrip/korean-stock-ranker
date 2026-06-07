import psycopg2, os
from dotenv import load_dotenv
load_dotenv()
c = psycopg2.connect(os.getenv("DATABASE_URL"))
cur = c.cursor()
cur.execute("""
    UPDATE stocks p
    SET industry = parent.industry,
        sector   = parent.sector,
        updated_at = NOW()
    FROM stocks parent
    WHERE p.industry IS NULL
      AND parent.industry IS NOT NULL
      AND substring(p.ticker FROM 1 FOR 5) = substring(parent.ticker FROM 1 FOR 5)
      AND p.ticker <> parent.ticker
""")
n = cur.rowcount
c.commit()
print(f"  Inherited industry from parent for {n} preferred shares")
