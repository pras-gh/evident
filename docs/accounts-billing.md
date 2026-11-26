# Accounts, research credits and billing

The journey:

1. A reader searches a company.
2. They sign in or create an account. The search is not lost: it continues as
   soon as they are in.
3. They get two free company research credits.
4. When the credits run out, they upgrade to Iix or IIx, through Stripe.

Everything that limits or grants access is enforced by the API. The pages only
show it.

## How it fits together

```
browser ── Firebase JS SDK ── Google (OAuth, passwords, SMS codes)
   │            │ ID token (JWT, signed by Google)
   │            ▼
   │   POST /v1/auth/session ──► verify (Google's keys, audience, issuer, expiry)
   │                             find or create the account
   │◄── Set-Cookie: parallx_session (httpOnly, SameSite=Lax, 30 days / 7 idle)
   │
   ├── /api/* (same origin, proxied to the API) — cookie travels, origin checked
   └── server components → API with the cookie forwarded
```

### Signing in: Firebase Authentication

Firebase handles everything that should not be custom code:

- Google OAuth, with its state checks;
- password hashing;
- email verification links and password reset links;
- SMS one-time codes, their expiry, resend limits, and reCAPTCHA abuse
  protection.

No password and no OTP ever reaches Parallx's database.

The API verifies the Firebase ID token itself (`apps/api/identity.py`). It
checks the token's signature against Google's published certificates, then its
audience (`FIREBASE_PROJECT_ID`), issuer, expiry and subject. After that it
issues its own session (`apps/api/viewer.py`):

- **The token:** 32 random bytes, stored only as their SHA-256.
- **Expiry:** 30 days absolute, or 7 days idle, whichever comes first.
- **Sign-out:** revokes the session.

### One person, one account

An account is matched in this order:

1. by the provider's uid;
2. by its normalised email, for verified emails only. Normalising lower-cases
   the address and drops Gmail's dots and any `+tag`, so
   `First.Last+x@googlemail.com` is the same person as `firstlast@gmail.com`;
3. by its phone number, in E.164 form.

A new account is created only when none of these match. This is what stops
trivial variations of one address from collecting more free credits. IP
address is never used to refuse anyone.

### CSRF

The cookie is `SameSite=Lax`. On top of that, a state-changing request that
was authenticated by the cookie must carry an `Origin` matching `APP_URL` or
`CORS_ORIGINS`, or it is refused.

## Research credits and plans

| | Free | Iix | IIx |
|---|---:|---:|---:|
| Price | — | $19/month · $190/year | $39/month · $390/year |
| Unique companies | 2 credits, for good | 10 at a time | 50 at a time |
| AI research queries | 10, once (`FREE_AI_QUERIES`) | 100 a billing period | 500 a billing period |
| SEC filings, timelines, statements, evidence viewer | yes | yes | yes |
| Promise Tracker, alerts, monitoring, comparisons, exports, transcripts | — | coming | coming |

Features that do not work yet are marked "Coming soon" on the pricing page and
in `/v1/plans`. They are never shown as included.

### How a credit is spent

The rules live in `parallx_db/accounts.py`:

- **When a credit is spent.** Only the first time a person researches a
  company. That happens through `POST /v1/research/access` (which the company
  pages call) or through a search that names the company.
- **What is free.** Searching the company again, refreshing, and coming back
  never cost anything.
- **Companies being fetched.** A company whose filings must first be fetched
  is held as `pending`. If the fetch fails, the credit is refunded
  (`fetch_worker` → `settle_pending`).
- **Concurrency.** Every deduction locks the user's row
  (`SELECT … FOR UPDATE`), so simultaneous requests are processed one at a
  time and cannot spend the same credit twice.
- **Audit.** Every deduction and refund is recorded in `usage_ledger`, which
  is only ever appended to. An idempotency key on a request makes a retried
  request free.
- **Paid plans.** A paid plan counts the distinct companies a person can open,
  including the two bought with free credits.
- **AI queries.** These are counted within the subscription's current billing
  period, so they reset when the period does. A `period_reset` ledger row
  records each new period.
- **Lapsed plans.** A plan that lapses never takes away what was researched.
  Every company researched stays open, on any plan. What the lapse stops is
  researching *new* companies beyond the free credits.
- **Failed payments.** The plan keeps working through `BILLING_GRACE_DAYS`
  (7 days) past the period's end while Stripe retries the card. After that,
  the account becomes Free.

### Who checks what

- Every company-scoped endpoint resolves its company through
  `access.get_company`.
- Documents, evidence, search and ask check the companies they touch.
- Staff, meaning an API key of the internal organization or a platform admin,
  research without credits.

## Billing: Stripe

`apps/api/billing.py` defines `BillingProvider`, and `StripeProvider`
implements it. The subscription logic depends only on the interface, so
changing provider means writing a new implementation, not rewriting that logic.

### Checkout

1. The page sends the plan, the interval and the billing country.
2. The API checks the plan against an allowlist and looks up the Price ID in
   the environment. Amounts are never taken from the browser.
3. The API creates or reuses the Stripe Customer, then creates a
   subscription-mode Checkout Session.
4. Checkout redirects back to `/billing/success`.
5. That page waits. The plan switches on only when Stripe's signed webhook
   says the subscription is active. The redirect alone grants nothing.

A subscriber who chooses the other plan gets a plan change (Stripe prorates
it). It, too, takes effect when the webhook confirms it.

### Webhooks

`POST /v1/billing/webhook` verifies the `Stripe-Signature` header against
`STRIPE_WEBHOOK_SECRET`, with a 5-minute tolerance.

- **Idempotent:** each event id is recorded in `billing_events`, in the same
  transaction as its effects. A duplicate delivery is a no-op, and an event
  that fails part-way is left unrecorded so Stripe's retry applies it.
- **Order-proof:** subscription events carry Stripe's timestamp, and an older
  one than the last applied is ignored. Checkout and invoice events re-read the
  subscription from Stripe instead, so they are current whenever they arrive.

| Event | Effect |
|---|---|
| `checkout.session.completed` | links the Customer to the user; applies the subscription |
| `customer.subscription.created` / `.updated` | applies the plan, status, period and cancellation |
| `customer.subscription.deleted` | the plan ends; the account becomes Free |
| `invoice.paid` | applies the new period (AI queries count afresh) |
| `invoice.payment_failed` | `past_due`: the plan keeps working through the grace period |
| `invoice.payment_action_required` | flags it: the account page asks the customer to finish the payment |

### Customer Portal

`GET /v1/billing/portal` opens Stripe's portal, where the customer can:

- update their card;
- see their invoices;
- cancel;
- switch between the plan's prices.

`POST /v1/billing/cancel` cancels at the end of the period.

### India

A Stripe account can't be assumed to charge Indian-issued cards in USD, and
recurring card payments in India need e-mandates. So:

- **No USD checkout for India.** The API refuses checkout when the billing
  country is in `BILLING_UNSUPPORTED_COUNTRIES` (default `IN`).
- **The page says so.** Instead of a checkout button, it tells the reader that
  INR billing isn't open yet.
- **Free still works.** The free plan works in full.

**Before taking money from India:**

- add an INR flow with a provider whose recurring-payment compliance is
  confirmed;
- consider a Radar rule against Indian-issued cards on USD prices, since a
  reader can pick another country.

Do not route around the restriction through a foreign account.

## Setup

### Firebase

1. Create a Firebase project.
2. Under Authentication → Sign-in method, enable **Google**,
   **Email/Password** and **Phone**.
3. Under Authentication → Settings → Authorized domains, add the app's domain.
4. Under Phone → SMS region policy, allow India and every other country you
   serve.
5. Optional: under Templates, set the email action URL to `APP_URL/signin`.
6. Copy the web app's config into `NEXT_PUBLIC_FIREBASE_API_KEY`,
   `NEXT_PUBLIC_FIREBASE_AUTH_DOMAIN`, `NEXT_PUBLIC_FIREBASE_PROJECT_ID` and
   `NEXT_PUBLIC_FIREBASE_APP_ID`. These are public by design.
7. Set `FIREBASE_PROJECT_ID` for the API.

No service account is needed: ID tokens are verified with Google's public
certificates.

### Stripe (test mode first)

1. **Products and prices.** Create two Products, each with two recurring USD
   Prices:

   | Product | Monthly | Annual |
   |---|---|---|
   | Iix — Investor | $19/month → `STRIPE_PRICE_IIX_MONTHLY` | $190/year → `STRIPE_PRICE_IIX_ANNUAL` |
   | IIx — Analyst | $39/month → `STRIPE_PRICE_IIX_ANALYST_MONTHLY` | $390/year → `STRIPE_PRICE_IIX_ANALYST_ANNUAL` |

2. **Customer Portal.** Under Settings → Billing → Customer portal, allow:
   - payment method updates;
   - invoice history;
   - cancellation at the end of the billing period;
   - subscription updates between the four prices above, with proration.
3. **Webhook endpoint.** Point it at `https://<api>/v1/billing/webhook`,
   subscribed to the six event types in the table above. Put its signing
   secret in `STRIPE_WEBHOOK_SECRET`.
4. **Secret key.** Set `STRIPE_SECRET_KEY` (`sk_test_…`).
5. **Check.** Run `python tools/billing_check.py`. It reads each Price back
   from Stripe and checks its amount, currency and interval.

### Testing with Stripe

Forward webhooks to your local API:

```bash
stripe listen --forward-to localhost:8000/v1/billing/webhook   # prints a whsec_ to use locally
```

| Scenario | Use |
|---|---|
| Successful subscription | card `4242 4242 4242 4242`, any future date and CVC |
| Authentication required | card `4000 0027 6000 3184` |
| Failed renewal | card `4000 0000 0000 0341` (attaches, then declines) |
| Duplicate delivery | `stripe events resend evt_…` |
| Cancellation, plan change | the Customer Portal, or `POST /v1/billing/cancel` |
| Expiry | Stripe Dashboard → subscription → cancel immediately |

`tests/test_accounts_billing.py` covers all of these without Stripe:

- the 22-step journey, signed webhook events, duplicates, out-of-order
  delivery, bad signatures;
- grace, retries, cancellation and the portal;
- concurrency and idempotency.

## API

| Endpoint | |
|---|---|
| `POST /v1/auth/session` | a Firebase ID token → a session cookie |
| `POST /v1/auth/logout` | revoke it |
| `GET /v1/me` | the caller (session or API key) |
| `GET /v1/me/usage` | companies and AI queries used and left; the companies researched |
| `GET /v1/me/subscription` | plan, status, period, grace, cancellation, whether the portal can open |
| `GET /v1/plans` | Free, Iix, IIx (public) |
| `POST /v1/research/access` | start researching a company: 402 with the usage when the plan has no room |
| `POST /v1/billing/checkout` | Checkout (or a plan change); 409 `market_unavailable` for India |
| `POST /v1/billing/webhook` | Stripe |
| `POST /v1/billing/cancel` | cancel at period end |
| `GET /v1/billing/portal` | the Customer Portal's URL |

## Deployment checklist

**Before launch:**

- [ ] `APP_ENV=production`, with `AUTH_PROVIDER=firebase`. The API refuses
      `AUTH_PROVIDER=dev` in production.
- [ ] `APP_URL` and `CORS_ORIGINS` are the app's https origin. The cookie is
      then `Secure`.
- [ ] Firebase is set up: providers, authorized domains, SMS region policy.
      Sign in once with each method.
- [ ] Database migrated to `0018`: `cd db && alembic upgrade head`. This seeds
      the plans.
- [ ] Stripe in test mode: prices created, `tools/billing_check.py` passes, the
      portal configured, the webhook endpoint subscribed to the six events, and
      a test subscription completed end to end with `stripe listen`.
- [ ] Switch to live: new live Prices, a live webhook endpoint with its own
      secret, `sk_live_…`, then run `tools/billing_check.py` again.
- [ ] Confirm with Stripe that the account's jurisdiction can take recurring
      international USD card payments. Keep `BILLING_UNSUPPORTED_COUNTRIES=IN`
      until an INR flow is verified.
- [ ] Replace `/terms` and `/privacy` with counsel-reviewed text. Today they
      describe what the system does, and say they are summaries.
- [ ] Point the landing page's links (`apps/marketing/index.html`, currently
      `http://localhost:3000`) at the app's domain.
- [ ] Monitoring: alert on webhook responses other than 200 (Stripe's
      dashboard shows them), and on `billing_events` going quiet while
      subscriptions exist.
- [ ] Secrets live in the host's secret store: `STRIPE_SECRET_KEY`,
      `STRIPE_WEBHOOK_SECRET`, `VOYAGE_API_KEY`, `GEMINI_API_KEY`. No
      `NEXT_PUBLIC_` variable holds a secret.
