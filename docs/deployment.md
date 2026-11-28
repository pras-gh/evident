# Deploying Parallx

Parallx in production is six hosted pieces. None of them runs on a laptop.

```
reader ──► ll.ax ............ Vercel ── landing page (apps/marketing)
       ──► app.ll.ax ........ Vercel ── the app (apps/web, Next.js)
                 │ /api/* is proxied to the API: the browser only talks to app.ll.ax,
                 │ and the session cookie belongs to it
                 ▼
           api.ll.ax ........ Railway ─ `api`: FastAPI, plus a process per filing fetch
           (no domain) ...... Railway ─ `sync`: the S&P 500, kept current from EDGAR
                 │
                 ├──► Supabase ......... Postgres 17 with pgvector
                 └──► Cloudflare R2 .... filings, PDFs, page images
Stripe ── webhooks ──► api.ll.ax/v1/billing/webhook
Firebase Authentication ◄── the browser signs in; the API checks the ID token
```

The two Railway services run the same image (the root `Dockerfile`) in
different roles (`tools/serve.sh api|sync|migrate|check`). Pushing to `main`
deploys everything; CI (`.github/workflows/ci.yml`) runs on every pull request.

## What it costs to start

| Service | Plan | Why that plan |
|---|---|---|
| Vercel | Pro, about $20/month | Hobby is for non-commercial use only; charging customers needs Pro |
| Railway | usage-based, on top of a monthly plan | the API uses up to ~2 GB while it parses a 10-K |
| Supabase | Pro, from $25/month | Free pauses after a week without activity and has no backups |
| Cloudflare R2 | free up to 10 GB | no egress fees |
| Firebase Authentication | free for email and Google | SMS codes need the pay-as-you-go Blaze plan |
| Stripe | per-transaction fees | |

Check each provider's pricing page before you sign up; these were current
when this was written.

Put every server region in the US East: Supabase `us-east-1`, Railway
`us-east4` (Virginia) and Vercel's functions in `iad1` (Washington, D.C.).
Every page makes several API calls, and each crosses the distance between
them.

## Order of work

1. Supabase, then Cloudflare (R2 and DNS), Firebase and Stripe. Each gives
   you values to put in Railway and Vercel.
2. Railway, then Vercel.
3. DNS records for `api`, `app` and the apex.
4. First run: check readiness, sign up, subscribe with a test card.

Keep the values in a password manager as you go. They go into each host's
variable settings, never into a file in this repository.

## 1. Supabase: the database

1. Create a project named `parallx` in **us-east-1**, with a long generated
   database password.
2. Open **Connect** and copy the **Session pooler** connection string:

   ```
   postgresql://postgres.<project ref>:<password>@aws-<n>-us-east-1.pooler.supabase.com:5432/postgres
   ```

   Add `?sslmode=require`, and percent-encode any reserved character in the
   password. This is `DATABASE_URL` for both Railway services.

   Use the session pooler for two reasons. Supabase's direct host is
   IPv6-only, and Railway's outbound IPv6 is off unless you enable it per
   service. Session mode also behaves like a direct connection, so prepared
   statements and the migrations work unchanged.
3. The first deploy creates the schema: the API's pre-deploy command runs the
   migrations, and the first one runs `CREATE EXTENSION IF NOT EXISTS vector`.
   If Supabase refuses it, enable **vector** under Database → Extensions and
   redeploy.
4. Move to the Pro plan before launch.

If connections run short, the session pooler is the first thing to check:
every API process, the sync worker and each filing fetch hold their own pool.
Lower `DB_POOL_SIZE` and `DB_MAX_OVERFLOW`, or point `DATABASE_URL` at the
transaction pooler on port `6543` with `DB_PREPARED_STATEMENTS=off`. Keep the
migrations on session mode with `MIGRATION_DATABASE_URL`.

## 2. Cloudflare: file storage and DNS

**DNS.** `ll.ax` is registered but has no nameservers yet.

1. Add `ll.ax` to Cloudflare on the Free plan.
2. Set the two nameservers Cloudflare gives you at the `.ax` registrar.
3. Wait for Cloudflare to show the domain as active.

**R2.**

1. Create a bucket named `parallx-filings`, with location hint Eastern North
   America. Leave public access off: readers get files through the API, after
   its access check.
2. Under **R2 → Manage API tokens**, create an **Account API token** with
   **Object Read & Write** on that bucket. Copy the Access Key ID and the
   Secret Access Key; the secret is shown once. Also copy the S3 endpoint:
   `https://<account id>.r2.cloudflarestorage.com`.
3. These become:

   ```
   FILING_STORE=s3://parallx-filings/prod
   S3_ENDPOINT_URL=https://<account id>.r2.cloudflarestorage.com
   S3_ACCESS_KEY_ID=…
   S3_SECRET_ACCESS_KEY=…
   S3_REGION=auto
   ```

## 3. Firebase: sign-in

[accounts-billing.md → Firebase](accounts-billing.md#firebase) has the details.

1. Create a project named `parallx`.
2. Under **Authentication → Sign-in method**, enable Email/Password, Google
   and, once you're on Blaze, Phone. For Phone, also set the SMS region
   policy.
3. Under **Authentication → Settings → Authorized domains**, add
   `app.ll.ax`.
4. Under **Project settings → Your apps**, add a Web app and copy its config:
   - `apiKey` → `NEXT_PUBLIC_FIREBASE_API_KEY`
   - `authDomain` → `NEXT_PUBLIC_FIREBASE_AUTH_DOMAIN`
   - `projectId` → `NEXT_PUBLIC_FIREBASE_PROJECT_ID`, and `FIREBASE_PROJECT_ID`
     for the API
   - `appId` → `NEXT_PUBLIC_FIREBASE_APP_ID`

   These values are public by design. The API needs no service account:
   it checks ID tokens against Google's public certificates.

## 4. Stripe: billing

Start in test mode. [accounts-billing.md → Stripe](accounts-billing.md#stripe-test-mode-first)
has the details.

1. **Prices.** Create two products, each with two recurring USD prices.
   Copy all four price IDs:
   - Iix: $19/month and $190/year.
   - IIx: $39/month and $390/year.
2. **Customer Portal.** Enable it: payment-method updates, invoices,
   cancelling at the period end, and switching between the four prices.
3. **Webhook.** Add an endpoint at `https://api.ll.ax/v1/billing/webhook`
   for these seven events:
   - `checkout.session.completed`
   - `customer.subscription.created`, `customer.subscription.updated`,
     `customer.subscription.deleted`
   - `invoice.paid`, `invoice.payment_failed`,
     `invoice.payment_action_required`

   Copy its signing secret (`whsec_…`).
4. After Railway is set up, check the prices from your machine, with the
   API's variables:

   ```bash
   railway run --service api .venv/bin/python tools/billing_check.py
   ```

Going live means new live prices, a live webhook endpoint with its own
secret, and `sk_live_…`. Use a Stripe account in your own country with your
real business details: the checkout honours Stripe's regional rules rather
than working around them, and `BILLING_UNSUPPORTED_COUNTRIES=IN` stays until
an INR flow is verified.

## 5. Railway: the API and the sync worker

1. Create a project with **Deploy from GitHub repo**, choose
   `pras-gh/evident`, and let Railway's GitHub app see it. Railway finds the
   `Dockerfile` at the root and builds it.
2. Name the first service **`api`** and set it up:
   - **Settings:**
     - Start command `sh tools/serve.sh api`
     - Pre-deploy command `sh tools/serve.sh migrate`
     - Healthcheck path `/ready`
     - Region US East (Virginia)
   - **Resources:** give it 2 GB of memory or more. A filing fetch parses a
     10-K in a subprocess, and rendering it runs Chromium.
   - **Networking:** add the custom domain `api.ll.ax`, and note the CNAME
     target Railway shows.
   - **Deploys:** if the service settings offer to wait for GitHub's checks,
     turn it on, so a red CI never deploys.
3. Add a second service from the same repository, named **`sync`**:
   - Start command `sh tools/serve.sh sync`.
   - Restart policy Always.
   - No domain and no healthcheck: it serves nothing.

   It backfills the S&P 500's filing lists and financial statements (hours,
   the first time), then checks EDGAR every 10 minutes. Annual reports are
   read on demand, when someone researches a company.
4. Set the variables. Use Railway's **shared variables** for whatever both
   services read, and reference them from each service.

   | Variable | `api` | `sync` | Value |
   |---|:-:|:-:|---|
   | `DATABASE_URL` | ✓ | ✓ | the Supabase session pooler URL, `?sslmode=require` |
   | `DB_POOL_SIZE` / `DB_MAX_OVERFLOW` | 3 / 2 | 2 / 1 | within the pooler's connection cap |
   | `FILING_STORE`, `S3_ENDPOINT_URL`, `S3_ACCESS_KEY_ID`, `S3_SECRET_ACCESS_KEY`, `S3_REGION` | ✓ | ✓ | from R2 |
   | `SEC_USER_AGENT` | ✓ | ✓ | `Parallx <a contact email>`; EDGAR refuses requests without one |
   | `EMBEDDING_PROVIDER`, `EMBEDDING_MODEL`, `VOYAGE_API_KEY` | ✓ | ✓ | `voyage`, `voyage-4-large`, the key |
   | `GEMINI_API_KEY` (and/or `GROQ_API_KEY`, `ANTHROPIC_API_KEY`) | ✓ | ✓ | AI answers and extraction |
   | `APP_URL`, `CORS_ORIGINS` | ✓ | | `https://app.ll.ax` |
   | `AUTH_PROVIDER`, `FIREBASE_PROJECT_ID` | ✓ | | `firebase`, the project ID |
   | `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET` | ✓ | | from Stripe |
   | `STRIPE_PRICE_IIX_MONTHLY`, `…_IIX_ANNUAL`, `…_IIX_ANALYST_MONTHLY`, `…_IIX_ANALYST_ANNUAL` | ✓ | | the four price IDs |
   | `BILLING_UNSUPPORTED_COUNTRIES` | ✓ | | `IN` |
   | `WEB_CONCURRENCY` | 2 | | API processes in the container |

   `APP_ENV=production` is the image's default. With it, the API checks its
   settings as it starts (`apps/api/production.py`). If any are missing or
   unsafe, it stops with a sentence per problem, and because `/ready` never
   answers, the deploy never goes live. Dev sign-in, a local file store and a
   plain-http `APP_URL` are all refused. Missing Stripe or AI keys are only
   warnings: the API starts with billing or answers off and says so.
5. Optionally, keep the setup as code: `railway config pull` writes it to
   `.railway/railway.ts` for review. Railway doesn't read that file on push;
   `railway config apply` applies it.

## 6. Vercel: the app and the landing page

**The app.**

1. Add a new project from `pras-gh/evident` with root directory `apps/web`.
   Vercel detects Next.js.
2. Add these production environment variables:

   ```
   API_URL=https://api.ll.ax/v1
   APP_URL=https://app.ll.ax
   NEXT_PUBLIC_AUTH_PROVIDER=firebase
   NEXT_PUBLIC_FIREBASE_API_KEY=…
   NEXT_PUBLIC_FIREBASE_AUTH_DOMAIN=…
   NEXT_PUBLIC_FIREBASE_PROJECT_ID=…
   NEXT_PUBLIC_FIREBASE_APP_ID=…
   ```

   A production build with any of them missing, with dev sign-in, or with a
   non-https address stops with a list of what to set (`next.config.mjs`).
   Preview deployments are not checked. Leave their `API_URL` unset unless
   you want previews to use production data, and add their domain to
   Firebase's authorized domains if you want to sign in on them.
3. Set the functions' region to `iad1`, and add the domain `app.ll.ax`.
4. Move to Pro before the first paying customer.

**The landing page.** It stays on the existing project, which serves
`apps/marketing` from the repository root (`vercel.json`). Add the domains
`ll.ax` and `www.ll.ax` to it. The page's links go to `https://app.ll.ax`;
served from localhost they go to `http://localhost:3000` instead.

## 7. DNS

In Cloudflare, set every record to **DNS only** (the grey cloud): Vercel and
Railway each issue and renew their own certificates.

| Name | Type | Target |
|---|---|---|
| `ll.ax` | A | the address Vercel shows for the apex |
| `www` | CNAME | the target Vercel shows |
| `app` | CNAME | the target Vercel shows |
| `api` | CNAME | the target Railway shows |

## 8. First run

1. Merge to `main`. The deploys run in this order:
   - CI runs.
   - Railway builds the image.
   - The API's pre-deploy command migrates the empty database (this also
     seeds the plans).
   - `/ready` passes, and the new version goes live.
   - Vercel builds the app.
2. Check that `https://api.ll.ax/ready` returns
   `{"status": "ready", "database": true, "migration": true, "store": true}`.
3. Sign up on `https://app.ll.ax`. Check the verification email arrives, then
   research a company: the fetch's progress shows in the `api` logs.
4. Make yourself staff. This runs from your machine with the API's variables,
   and prints an API key once:

   ```bash
   railway run --service api .venv/bin/python tools/admin.py bootstrap-admin you@example.com
   ```

5. Subscribe with test card `4242 4242 4242 4242`. The success page waits for
   the webhook, not the redirect. Check that Stripe's webhook log shows 200s.

## 9. Running it

- **Deploys:** a push to `main` deploys. A pull request gets CI and a Vercel
  preview.
- **Rollbacks:** on Railway, redeploy an earlier deployment; on Vercel,
  promote an earlier deployment. Migrations only go forward: a rollback past
  one needs a new migration that undoes it.
- **Health:**
  - `/ready` is the deploy check: it requires the database, this code's
    migration and the bucket, and answers 503 otherwise.
  - `/health` is a liveness probe.
- **Logs:**
  - Railway: the API, each filing fetch, and the sync worker.
  - Vercel: the app's server functions.
  - Supabase: queries and connections.
- **Backups:**
  - Supabase Pro keeps daily backups for 7 days.
  - Everything in R2 can be rebuilt from EDGAR by re-ingesting and
    re-rendering.
- **Secrets:** rotate a key at its provider, change the variable, and
  redeploy. `.env` is for local development only and is never committed.
- **Costs to watch:**
  - Voyage embeddings, during backfills.
  - AI tokens; `AI_LIMIT_*` caps them.
  - R2 storage.
  - The API's memory on Railway.

## Security, in one place

- Dev sign-in is refused twice: by the API in production, and by Vercel's
  production build.
- The session cookie is httpOnly, `Secure`, `SameSite=Lax`, and scoped to
  `app.ll.ax` only.
- Requests that change something with the cookie must come from
  `CORS_ORIGINS`.
- The bucket is private. Files are served only through access-checked API
  routes, with `Cache-Control: private`, so no shared cache keeps them.
- Every app response carries HSTS, `nosniff`, `SAMEORIGIN` framing, a
  referrer policy and a permissions policy.
- The API's interactive docs are off in production (`API_DOCS=1` turns them
  on).
- Stripe webhooks are verified by signature and applied once each.
- The container never runs as root.
