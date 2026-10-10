# Deploying to Vercel

The app is built so a **fresh deploy is harmless**: until sign-in, a session secret and a database are configured, it runs as a clearly
labelled **controlled demo** (real matching plus a scripted fixture project; no OpenAI, no GitHub, nothing spent) and every real API stays
locked. Nobody can use your OpenAI key or GitHub token. Set the variables below and redeploy to switch to the real app.

## What runs where
| Piece | On Vercel |
|---|---|
| FastAPI app | One serverless function (`index.py` -> `backend/app.py`), 60 s max per request |
| Sessions | Encrypted cookie (`SECRET_KEY`); no server-side store. Sign-out and "delete my data" revoke it through a database epoch |
| State (jobs, profiles, budgets, reply state) | Postgres via `DATABASE_URL` (Neon recommended) |
| Spend limits | Per student per UTC day, stored in Postgres (`SESSION_BUDGET_USD`), plus a global daily cap (`GLOBAL_BUDGET_USD`) |
| Organisation catalogue | `mentor/catalogue_snapshot.json`, committed; refreshed weekly by a GitHub Action, which redeploys |
| Full-auto worker | Still runs on the **student's machine** (`backend/runner.py`), talking to your deployed URL. The pasted command downloads the runner into `~/.oss-mentor` on first use from `RUNNER_REPO_URL` (default: this project on GitHub), so that repo must contain the same runner version as your deployment |

## One-time setup
1. **GitHub OAuth App** (github.com/settings/developers -> New OAuth App)
   - Homepage URL: `https://<your-project>.vercel.app`
   - Callback URL: `https://<your-project>.vercel.app/auth/callback`
   - Keep the Client ID and generate a Client secret.
2. **Database**: create a Neon (or any Postgres) database and copy its pooled connection string.
3. **Environment variables** (Vercel project -> Settings -> Environment Variables, Production):

| Variable | Value |
|---|---|
| `GITHUB_CLIENT_ID`, `GITHUB_CLIENT_SECRET` | from step 1 |
| `BASE_URL` | `https://<your-project>.vercel.app` (no trailing slash) |
| `SECRET_KEY` | 32+ random characters (`python -c "import secrets; print(secrets.token_hex(32))"`) |
| `DATABASE_URL` | from step 2 |
| `OPENAI_API_KEY` | your key. **Monitor OpenAI usage and billing alongside app budgets.** |
| `SESSION_BUDGET_USD` | per student per day, default `0.50` |
| `GLOBAL_BUDGET_USD` | total per day across everyone, default `10.00` |
| `ADMIN_LOGINS` | your GitHub login |
4. Redeploy. `GET /api/health` should report `"configured": true`.

## Things to know
- **Cost exposure.** Anyone with a GitHub account can sign in. The app budgets estimate spending. Reservations are per process; concurrent server instances can race, so these are not a production-wide atomic spending guarantee. Monitor provider usage and billing.
- **Sign-out revokes everywhere.** Cookies are self-contained, so revocation is a database check cached for 10 s per instance.
- **60-second limit.** Scouting and repo tours usually finish well inside it; a very slow GitHub day could hit it.
- **Hobby plan** is for non-commercial use; check Vercel's terms if this becomes a product.
- **Runner**: after deploying, the command shown in Full-auto uses your deployed URL, and the student runs it from their own clone of this repo.
