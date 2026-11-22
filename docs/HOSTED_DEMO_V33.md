# V33 — hosting and restricted access (demo only)

The API and continuous background worker require a runtime and PostgreSQL.
**Firebase Hosting alone cannot run this project.** Firebase Hosting can serve
static files, but FastAPI + worker must run in Cloud Run or another server and
PostgreSQL needs separate hosting. Firebase App Hosting also requires billing.

## Option A — off-laptop demo: Render Free + Neon Free

1. Push the patched repository to GitHub (keep `.env` out of Git).
2. Create a Neon Free PostgreSQL instance with the `vector` extension enabled.
   Obtain its PostgreSQL connection string and change only the scheme to
   `postgresql+asyncpg://`. Use a direct (non-pooler) URL for migrations if needed.
   Never paste DB credentials into chat or commit them.
3. For your GitHub deployment checkout, copy `docs/render_v33.example.yaml` to the repository root as `render.yaml` and commit that file to the deployment branch. In Render, create a Blueprint from `render.yaml`; set the secret environment
   variables: `DATABASE_URL`, `OPENROUTER_API_KEY`, `DEMO_BASIC_USER`,
   `DEMO_BASIC_PASSWORD` (random 20+ characters).
4. Visit the assigned `https://...onrender.com` address. A browser-native Basic
   login challenge protects every page, API route and static asset. Distribute
   credentials separately from the link. `GET /healthz` is intentionally public
   for Render health checks.
5. Test a short source claim and inspect server logs. The service sleeps after
   prolonged inactivity; a request that wakes it can be slow.

**Caveats:** Render Free suspends after 15 minutes idle, so jobs do not run
while asleep. A single container running both worker and web is DEMO-ONLY.
No strong per-user isolation, shared report privacy, or per-user quotas.
External OpenRouter requests can cost money even if hosting is free.
Do not submit secrets or private documents to this demo. Add account-specific
limits and separate workers before wider public distribution.

## Option B — temporary laptop demo: protected Cloudflare Quick Tunnel

Run `bash scripts/share_demo_v33.sh email1@example.com email2@example.com`
after installing current `cloudflared` and starting local services. Visitors
need their own emailed one-time PIN. Cloudflare routes outbound from your
laptop; no router ports are opened, but your laptop **is** serving the users.
Stop the tunnel to revoke the link. This is not off-laptop hosting.

## Security notes

- Basic credentials MUST be transmitted over HTTPS only. Render provides HTTPS.
- One shared Basic user/password is hackathon access control, not a full user
  account system. Rotate it after the event.
- Do not put `.env`, OpenRouter keys, or DB passwords in the repository.
- Keep Render at one instance (avoids duplicate cron/indexers and queue races).
- Keep the API private unless credentials, job rate limits and provider budget
  controls are correctly configured.
