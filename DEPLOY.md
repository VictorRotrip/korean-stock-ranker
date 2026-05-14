# Deploying to Vercel

The webapp is a vanilla Next.js 15 App Router project. It only talks to
Supabase Postgres over the standard pooled connection, so Vercel deploy
is mostly "push to GitHub, click Import, paste env vars".

## What gets cached

Pages use `export const revalidate = 1800` or `3600` instead of
`force-dynamic`. Vercel re-renders each page at most once every 30-60
minutes, then serves the static HTML to subsequent visitors. Daily
Python pipeline updates land in the cache within an hour.

## One-time setup

1. **Create a GitHub repo** (public — that's what the user asked for):
   ```bash
   # if you don't have one yet
   gh repo create korean-stock-ranker --public --source=. --remote=origin
   # or manually: create on github.com, then:
   #   git remote add origin git@github.com:<you>/korean-stock-ranker.git
   ```

2. **Commit and push**:
   ```bash
   git add -A
   git commit -m "feat: backtest page + PIT universe filter + Vercel config"
   git push -u origin master
   ```

3. **Connect Vercel**:
   - Go to https://vercel.com/new
   - Pick the GitHub repo
   - Framework preset auto-detects as "Next.js"
   - Leave build/output commands at defaults (`next build`, `.next`)
   - Click "Deploy" — it will fail on the first attempt because env
     vars aren't set yet. That's fine.

4. **Add environment variables** in Vercel → Project → Settings →
   Environment Variables:

   | Name           | Value                                           | Environment |
   |---------------:|-------------------------------------------------|:-----------:|
   | `DATABASE_URL` | (your `.env.local` Supabase pooler URL)         | All         |

   Then click "Redeploy" on the failed deployment.

5. After a green deploy, visit the assigned `*.vercel.app` URL and
   verify `/`, `/ranking`, `/backtest`, `/universe`, and one stock
   detail page render with live data.

## Optional but recommended

### Read-only DB role for Vercel

The default `DATABASE_URL` uses an admin user. For a public site, create
a read-only role and use *that* connection string for Vercel:

```sql
-- Run in Supabase SQL Editor
CREATE ROLE webapp_read LOGIN PASSWORD '<long-random-password>';
GRANT CONNECT ON DATABASE postgres TO webapp_read;
GRANT USAGE ON SCHEMA public TO webapp_read;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO webapp_read;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
  GRANT SELECT ON TABLES TO webapp_read;
```

Then build a connection string for `webapp_read`. The pooler URL looks
like:

```
postgres://webapp_read.<project-ref>:<password>@aws-0-eu-west-1.pooler.supabase.com:6543/postgres
```

(Note the `webapp_read.<project-ref>` username form Supabase uses for
the connection pooler. Test it with `psql "<url>" -c "SELECT 1"`.)

Swap `DATABASE_URL` in Vercel to this new value and redeploy.

### Region

`vercel.json` already pins functions to `fra1` (Frankfurt), which is
nearest to Supabase's `eu-west-1`. This keeps DB latency under ~10ms.

### Custom domain

In Vercel → Project → Domains, add your CNAME. SSL is automatic.

## Smoke test after deploy

From a fresh browser:

- `/` should show "Dashboard" with non-zero stock counts.
- `/ranking` should list the most recent ranking snapshot.
- `/backtest` should render the cumulative chart with default weights.
- Sliders on `/backtest` should re-render the chart without a page
  reload (all client-side).
- `/stocks/005930` (Samsung Electronics) should show real financials.
