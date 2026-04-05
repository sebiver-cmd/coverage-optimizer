# SB-Optima — Production Deployment Roadmap

> **Goal**: Get the SB-Optima web app live at **sboptima.dk** and **sboptima.com**.
>
> The application code (backend, frontend, database, billing) is fully built.
> This roadmap covers the **infrastructure and configuration** needed to go live.

---

## What's Already Done (app is ready)

Everything below is built, tested, and working locally:

- ✅ FastAPI backend with auth, billing, optimization, apply, admin APIs
- ✅ Next.js 16 frontend (login, signup, dashboard, optimizer, history, billing)
- ✅ PostgreSQL database with 6 Alembic migrations
- ✅ Redis + Arq background worker for async optimization jobs
- ✅ Stripe billing integration (checkout, webhooks, plans, usage metering)
- ✅ Docker Compose for local development (4 services)
- ✅ Dockerfiles for API and frontend
- ✅ GitHub Actions CI (runs tests on every push/PR)
- ✅ 1227+ backend tests, 22 frontend tests

---

## Phase 14 — Fix Docker Compose for Production

> Phases 0–13 (the application code) are complete — see `SAAS_ROADMAP.md`.
> This roadmap picks up at Phase 14 with infrastructure and deployment.
>
> These are small code changes to the repo that are needed before deploying.

### Task 14.1 — Add the Arq Worker to Docker Compose

**What**: The background worker that runs optimization jobs is not in
`docker-compose.yml`. Without it, jobs sit in the queue forever.

**Steps**:

1. Open `infra/docker-compose.yml`.
2. Add a new `worker` service after the `frontend` service:
   ```yaml
   # ---- Arq background worker ----
   worker:
     build:
       context: ..
       dockerfile: infra/Dockerfile.api
     command: ["arq", "backend.worker.WorkerSettings"]
     env_file:
       - ../.env
     environment:
       - DATABASE_URL=postgresql+psycopg2://sboptima:sboptima@postgres:5432/sboptima
       - REDIS_URL=redis://redis:6379/0
     depends_on:
       postgres:
         condition: service_healthy
       redis:
         condition: service_healthy
   ```
3. Save the file.
4. Test locally: `docker compose -f infra/docker-compose.yml up --build`
5. Verify the worker starts and connects to Redis (check logs for
   `"arq worker started"`).

**Done when**: `docker compose up` shows 5 running services (api, frontend,
postgres, redis, worker), and a submitted optimization job completes.

- [x] Task 14.1 — Add Arq worker to docker-compose

---

### Task 14.2 — Add Database Migration to API Startup

**What**: When you deploy fresh, the database tables don't exist yet.
The API needs to run `alembic upgrade head` automatically on startup.

**Steps**:

1. Open `infra/Dockerfile.api`.
2. Change the `CMD` line at the bottom to run migrations first:
   ```dockerfile
   CMD ["sh", "-c", "alembic upgrade head && python -m uvicorn backend.main:app --host 0.0.0.0 --port 8000"]
   ```
3. Save the file.
4. Test: delete your local Postgres volume (`docker compose down -v`), then
   `docker compose up --build`. The API should create all tables automatically.
5. Hit `http://localhost:8000/health` — it should show `"db": "ok"`.

**Done when**: A fresh `docker compose up` creates the database tables
automatically without manual `alembic` commands.

- [x] Task 14.2 — Auto-run migrations on API startup

---

## Phase 15 — Choose and Set Up Hosting

> You need a server to run your Docker containers. Smarty (your domain
> registrar) does NOT provide application hosting — it only manages DNS.

### Task 15.1 — Pick a Hosting Platform

**What**: Choose where to run the 5 Docker services.

**Options** (pick one):

| Option | Cost | Difficulty | Best for |
|--------|------|------------|----------|
| **Hetzner VPS** | ~€5-10/mo | Medium | Cheapest, full control |
| **DigitalOcean Droplet** | ~$12-24/mo | Medium | Good docs, easy UI |
| **Railway** | ~$5-20/mo | Easy | Auto-deploys from GitHub |
| **Fly.io** | ~$5-15/mo | Easy | Fast global deployment |
| **Render** | ~$7-25/mo | Easy | Simple, GitHub integration |

**Steps**:

1. **If you pick a VPS** (Hetzner / DigitalOcean):
   - Create an account.
   - Create a new server (Ubuntu 24.04, at least 2 GB RAM, 2 vCPU).
   - Note down the server's **IP address** (e.g., `123.45.67.89`).
   - SSH into the server: `ssh root@123.45.67.89`.
   - Install Docker:
     ```bash
     curl -fsSL https://get.docker.com | sh
     ```
   - Install Docker Compose:
     ```bash
     apt install docker-compose-plugin
     ```

2. **If you pick a PaaS** (Railway / Fly.io / Render):
   - Create an account.
   - Connect your GitHub repository (`sebiver-cmd/coverage-optimizer`).
   - Follow their guide to deploy a Docker Compose project.

**Done when**: You have a server with Docker installed, or a PaaS account
connected to your GitHub repo.

- [ ] Task 15.1 — Pick and set up hosting platform

---

### Task 15.2 — Set Up a Production Database (PostgreSQL)

**What**: Your production database should be reliable with automatic backups.
You can use the Docker Compose Postgres container on a VPS, or a managed
database service.

**Option A — Use Docker Compose Postgres (simplest)**:
- The Postgres container in `docker-compose.yml` already works.
- Make sure the `pgdata` volume is on a persistent disk.
- Set up a cron job for backups (see `docs/ops/backups.md`).

**Option B — Use a managed database (recommended for production)**:

| Service | Free tier | Paid |
|---------|-----------|------|
| **Neon** | 0.5 GB free | $19/mo |
| **Supabase** | 500 MB free | $25/mo |
| **DigitalOcean Managed DB** | — | $15/mo |

Steps for managed DB:
1. Create a Postgres database on your chosen provider.
2. Copy the connection string (looks like:
   `postgresql+psycopg2://user:password@host:5432/dbname`).
3. Save it — you'll use it as `DATABASE_URL` in Task 16.1.

**Done when**: You have a working Postgres database (either Docker or managed)
with a connection string ready.

- [ ] Task 15.2 — Set up production PostgreSQL

---

### Task 15.3 — Set Up Production Redis

**What**: Redis is used for background job queues and caching.

**Option A — Use Docker Compose Redis (simplest)**:
- The Redis container in `docker-compose.yml` already works.
- Fine for a VPS deployment.

**Option B — Use a managed Redis**:

| Service | Free tier | Paid |
|---------|-----------|------|
| **Upstash** | 10K cmds/day free | $10/mo |
| **Redis Cloud** | 30 MB free | $7/mo |

Steps for managed Redis:
1. Create a Redis instance on your chosen provider.
2. Copy the connection string (looks like: `redis://default:password@host:6379`).
3. Save it — you'll use it as `REDIS_URL` in Task 16.1.

**Done when**: You have a working Redis instance with a connection string ready.

- [ ] Task 15.3 — Set up production Redis

---

## Phase 16 — Production Configuration

### Task 16.1 — Generate and Set Production Secrets

**What**: The app needs several secret values to run securely in production.
In dev mode it uses defaults, but in production these **must** be set.

**Steps**:

1. **Generate a JWT secret** (used to sign login tokens):
   ```bash
   python3 -c "import secrets; print(secrets.token_urlsafe(64))"
   ```
   Copy the output — this is your `JWT_SECRET`.

2. **Generate a Fernet encryption key** (used to encrypt stored credentials):
   ```bash
   python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
   ```
   Copy the output — this is your `ENCRYPTION_KEY`.

3. **Create your production `.env` file** (or set these as environment
   variables on your hosting platform):

   ```env
   # ---- Required for production ----
   SBOPTIMA_ENV=prod
   SBOPTIMA_AUTH_REQUIRED=true

   DATABASE_URL=postgresql+psycopg2://USER:PASSWORD@HOST:5432/sboptima
   REDIS_URL=redis://HOST:6379/0

   JWT_SECRET=paste-your-jwt-secret-here
   ENCRYPTION_KEY=paste-your-fernet-key-here

   # ---- Domain / CORS ----
   CORS_ALLOWED_ORIGINS=https://sboptima.dk,https://www.sboptima.dk,https://sboptima.com,https://www.sboptima.com
   SECURITY_HEADERS_ENABLED=true
   HSTS_ENABLED=true

   # ---- Frontend → Backend URL ----
   NEXT_PUBLIC_API_URL=https://api.sboptima.dk

   # ---- Apply safety (keep false until you're ready to push prices) ----
   SB_OPTIMA_ENABLE_APPLY=false
   ```

4. **⚠️ NEVER commit the `.env` file to Git.** It's already in `.gitignore`.

**Done when**: You have a `.env` file (or hosting env vars) with all
required production values filled in.

- [ ] Task 16.1 — Generate and set production secrets

---

### Task 16.2 — Set Up Stripe for Billing

**What**: Stripe handles payments. You need to create products and prices
in Stripe, then connect them to the app.

**Steps**:

1. Go to [dashboard.stripe.com](https://dashboard.stripe.com) and log in
   (or create an account).

2. **Create two subscription products**:
   - Go to **Products** → **Add product**.
   - Product 1: Name = "SB-Optima Pro", Price = your monthly price,
     Recurring = Monthly. Click **Save**.
   - Product 2: Name = "SB-Optima Enterprise", Price = your monthly price,
     Recurring = Monthly. Click **Save**.

3. **Copy the Price IDs**:
   - Click on each product → scroll to **Pricing** → copy the Price ID
     (starts with `price_`).
   - These become `STRIPE_PRICE_ID_PRO` and `STRIPE_PRICE_ID_ENTERPRISE`.

4. **Get your API keys**:
   - Go to **Developers** → **API keys**.
   - Copy the **Secret key** (starts with `sk_live_` or `sk_test_`).
   - This becomes `STRIPE_SECRET_KEY`.

5. **Set up the webhook**:
   - Go to **Developers** → **Webhooks** → **Add endpoint**.
   - URL: `https://api.sboptima.dk/billing/webhook`
   - Events to listen for:
     - `checkout.session.completed`
     - `customer.subscription.created`
     - `customer.subscription.updated`
     - `customer.subscription.deleted`
   - Click **Add endpoint**.
   - Copy the **Signing secret** (starts with `whsec_`).
   - This becomes `STRIPE_WEBHOOK_SECRET`.

6. **Add to your `.env` / hosting env vars**:
   ```env
   STRIPE_SECRET_KEY=sk_live_…
   STRIPE_WEBHOOK_SECRET=whsec_…
   STRIPE_PRICE_ID_PRO=price_…
   STRIPE_PRICE_ID_ENTERPRISE=price_…
   BILLING_ENABLED=true
   ```

7. **Test first with Stripe test mode** (use `sk_test_` keys) before
   switching to live keys.

**Done when**: Stripe products exist, webhook is configured, and all 4
Stripe env vars are set.

- [ ] Task 16.2 — Set up Stripe for billing

---

## Phase 17 — Reverse Proxy & SSL

> A reverse proxy sits in front of your app to handle HTTPS and route
> traffic to the right service.

### Task 17.1 — Add Caddy as Reverse Proxy (automatic HTTPS)

**What**: Caddy is the easiest reverse proxy — it automatically gets
SSL certificates from Let's Encrypt. No manual cert management needed.

**Steps**:

1. Create a new file `infra/Caddyfile`:
   ```
   # SB-Optima — Production reverse proxy
   #
   # Caddy automatically obtains and renews SSL certificates.

   sboptima.dk, www.sboptima.dk {
       # Frontend (Next.js)
       reverse_proxy frontend:3000
   }

   api.sboptima.dk {
       # Backend API (FastAPI)
       reverse_proxy api:8000
   }

   sboptima.com, www.sboptima.com {
       # Redirect .com to .dk
       redir https://sboptima.dk{uri} permanent
   }
   ```

2. Add the Caddy service to `infra/docker-compose.yml`:
   ```yaml
   # ---- Caddy reverse proxy (automatic HTTPS) ----
   caddy:
     image: caddy:2
     ports:
       - "80:80"
       - "443:443"
     volumes:
       - ./Caddyfile:/etc/caddy/Caddyfile
       - caddy_data:/data
       - caddy_config:/config
     depends_on:
       - api
       - frontend
   ```

3. Add the Caddy volumes to the `volumes:` section at the bottom:
   ```yaml
   volumes:
     pgdata:
     redisdata:
     caddy_data:
     caddy_config:
   ```

4. **Update the frontend environment variable** in docker-compose.yml:
   ```yaml
   frontend:
     environment:
       - NEXT_PUBLIC_API_URL=https://api.sboptima.dk
   ```

5. Save all files and commit to Git.

**Done when**: `infra/Caddyfile` exists, and docker-compose.yml includes
the Caddy service with ports 80 and 443.

- [x] Task 17.1 — Add Caddy reverse proxy with automatic HTTPS

---

## Phase 18 — DNS Configuration

> DNS tells the internet where to find sboptima.dk. You do this in Smarty
> (your domain registrar).

### Task 18.1 — Point sboptima.dk to Your Server

**What**: Create DNS records so your domains point to your hosting server.

**Steps**:

1. Log in to **Smarty** (your domain registrar).

2. Go to **DNS settings** for `sboptima.dk`.

3. **Add these DNS records** (replace `123.45.67.89` with your real
   server IP from Task 15.1):

   | Type | Name | Value | TTL |
   |------|------|-------|-----|
   | A | `@` | `123.45.67.89` | 300 |
   | A | `www` | `123.45.67.89` | 300 |
   | A | `api` | `123.45.67.89` | 300 |

4. Go to **DNS settings** for `sboptima.com`.

5. **Add these DNS records**:

   | Type | Name | Value | TTL |
   |------|------|-------|-----|
   | A | `@` | `123.45.67.89` | 300 |
   | A | `www` | `123.45.67.89` | 300 |

6. **Wait 5–30 minutes** for DNS to update (can take up to 48 hours in
   rare cases).

7. **Test** from your terminal:
   ```bash
   nslookup sboptima.dk
   nslookup api.sboptima.dk
   nslookup sboptima.com
   ```
   Each should return your server's IP address.

> **If you're using a PaaS** (Railway, Render, Fly.io): follow their
> custom domain instructions instead — they may use CNAME records.

**Done when**: `nslookup sboptima.dk` returns your server's IP address.

- [ ] Task 18.1 — Point DNS to your server

---

## Phase 19 — Deploy and Launch

### Task 19.1 — Deploy to Your Server

**What**: Upload the code and start all services on your production server.

**For a VPS (Hetzner / DigitalOcean)**:

1. SSH into your server:
   ```bash
   ssh root@123.45.67.89
   ```

2. Clone the repository:
   ```bash
   git clone https://github.com/sebiver-cmd/coverage-optimizer.git
   cd coverage-optimizer
   ```

3. Create the production `.env` file:
   ```bash
   nano .env
   ```
   Paste in all the values from Task 16.1 and 16.2. Save and exit
   (Ctrl+O, Enter, Ctrl+X).

4. Start all services:
   ```bash
   docker compose -f infra/docker-compose.yml up -d --build
   ```
   The `-d` flag runs everything in the background.

5. Check that all services are running:
   ```bash
   docker compose -f infra/docker-compose.yml ps
   ```
   You should see 6 services: `api`, `frontend`, `postgres`, `redis`,
   `worker`, `caddy` — all with status "Up".

6. Check the API health:
   ```bash
   curl http://localhost:8000/health
   ```
   Should return `{"status":"ok","db":"ok"}`.

7. **Wait 1–2 minutes** for Caddy to automatically get SSL certificates.

8. Open your browser and go to **https://sboptima.dk** — you should see
   the login page! 🎉

**For a PaaS (Railway / Render / Fly.io)**:

1. Push your code to GitHub (the PaaS auto-deploys).
2. Set environment variables in the PaaS dashboard.
3. The platform handles SSL and deployment automatically.

**Done when**: https://sboptima.dk loads the login page, and
https://api.sboptima.dk/health returns `{"status":"ok","db":"ok"}`.

- [ ] Task 19.1 — Deploy to production server

---

### Task 19.2 — Verify Everything Works

**What**: Go through a checklist to make sure every feature works.

**Steps**:

1. **Sign up**: Go to https://sboptima.dk/signup. Create a new account.
   ✅ You should see the dashboard.

2. **Log in / log out**: Log out, then log back in.
   ✅ You should land on the dashboard again.

3. **Add credentials**: Go to Dashboard → add your DanDomain SOAP
   credentials (API username + password).
   ✅ Credentials should appear in the list.

4. **Run an optimization**: Go to the Optimizer page → select a brand →
   click Optimize.
   ✅ Job should start, show progress, and display results.

5. **Dry run**: Click "Dry Run" on optimization results.
   ✅ Should show the batch of proposed price changes.

6. **History**: Go to the History page.
   ✅ Your job and batch should appear in the list.

7. **Billing**: Go to the Billing page.
   ✅ Should show your current plan and upgrade options.

8. **HTTPS**: Check the browser shows a 🔒 lock icon next to the URL.
   ✅ Connection is secure.

9. **Redirect**: Go to http://sboptima.com.
   ✅ Should redirect to https://sboptima.dk.

10. **API health**: Visit https://api.sboptima.dk/health.
    ✅ Should return JSON with `"status": "ok"`.

**Done when**: All 10 checks pass. The app is live! 🚀

- [ ] Task 19.2 — Verify everything works end-to-end

---

## Phase 20 — Continuous Deployment (Optional but Recommended)

### Task 20.1 — Auto-Deploy from GitHub

**What**: Every time you push code to the `main` branch, the server
automatically pulls the new code and restarts the services.

**For a VPS — use a GitHub Actions deploy workflow**:

1. **On your server**, create a deploy script:
   ```bash
   nano /root/deploy.sh
   ```
   ```bash
   #!/bin/bash
   cd /root/coverage-optimizer
   git pull origin main
   docker compose -f infra/docker-compose.yml up -d --build
   ```
   ```bash
   chmod +x /root/deploy.sh
   ```

2. **On your server**, set up an SSH key for GitHub Actions:
   ```bash
   ssh-keygen -t ed25519 -f /root/.ssh/deploy_key -N ""
   cat /root/.ssh/deploy_key.pub >> /root/.ssh/authorized_keys
   cat /root/.ssh/deploy_key  # Copy this private key
   ```

3. **In GitHub** → your repo → **Settings** → **Secrets and variables**
   → **Actions** → **New repository secret**:
   - `DEPLOY_SSH_KEY` = paste the private key from step 2
   - `DEPLOY_HOST` = your server IP (e.g., `123.45.67.89`)
   - `DEPLOY_USER` = `root` (or your deploy user)

4. **Create** `.github/workflows/deploy.yml`:
   ```yaml
   name: Deploy

   on:
     push:
       branches: [main]

   jobs:
     deploy:
       name: Deploy to Production
       runs-on: ubuntu-latest
       if: github.ref == 'refs/heads/main'

       steps:
         - name: Deploy via SSH
           uses: appleboy/ssh-action@v1
           with:
             host: ${{ secrets.DEPLOY_HOST }}
             username: ${{ secrets.DEPLOY_USER }}
             key: ${{ secrets.DEPLOY_SSH_KEY }}
             script: /root/deploy.sh
   ```

5. Push to `main` and watch the deploy happen automatically in the
   **Actions** tab on GitHub.

**For a PaaS**: This usually works out of the box — Railway, Render,
and Fly.io auto-deploy on push to `main`.

**Done when**: Pushing to `main` automatically deploys to production.

- [x] Task 20.1 — Set up automatic deployment from GitHub

---

## Quick Reference — All Environment Variables for Production

| Variable | Required | Example value |
|----------|----------|---------------|
| `SBOPTIMA_ENV` | ✅ | `prod` |
| `SBOPTIMA_AUTH_REQUIRED` | ✅ | `true` |
| `DATABASE_URL` | ✅ | `postgresql+psycopg2://user:pass@host:5432/sboptima` |
| `REDIS_URL` | ✅ | `redis://host:6379/0` |
| `JWT_SECRET` | ✅ | (generated in Task 16.1) |
| `ENCRYPTION_KEY` | ✅ | (generated in Task 16.1) |
| `CORS_ALLOWED_ORIGINS` | ✅ | `https://sboptima.dk,https://www.sboptima.dk,https://sboptima.com,https://www.sboptima.com` |
| `SECURITY_HEADERS_ENABLED` | ✅ | `true` |
| `HSTS_ENABLED` | ✅ | `true` |
| `NEXT_PUBLIC_API_URL` | ✅ | `https://api.sboptima.dk` |
| `SB_OPTIMA_ENABLE_APPLY` | ✅ | `false` (change to `true` when ready) |
| `STRIPE_SECRET_KEY` | For billing | `sk_live_…` |
| `STRIPE_WEBHOOK_SECRET` | For billing | `whsec_…` |
| `STRIPE_PRICE_ID_PRO` | For billing | `price_…` |
| `STRIPE_PRICE_ID_ENTERPRISE` | For billing | `price_…` |
| `BILLING_ENABLED` | For billing | `true` |
| `OPENAI_API_KEY` | For LLM features | `sk-…` |

---

## Summary

| Phase | What | Tasks |
|-------|------|-------|
| **14** | Fix Docker Compose | 14.1 Add worker, 14.2 Auto-migrate |
| **15** | Choose hosting | 15.1 Platform, 15.2 Postgres, 15.3 Redis |
| **16** | Production config | 16.1 Secrets, 16.2 Stripe |
| **17** | HTTPS & routing | 17.1 Caddy reverse proxy |
| **18** | DNS | 18.1 Point domains to server |
| **19** | Deploy & verify | 19.1 Deploy, 19.2 Test everything |
| **20** | Auto-deploy | 20.1 CI/CD pipeline |

**Total**: 10 tasks across 7 phases. Do them in order, top to bottom.
