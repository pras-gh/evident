# AI gateway

Every AI request in Evident goes through one gateway. Business logic names a
**task**; configuration decides which provider and model run it. Every
request is limited, retried or rerouted when a provider fails, costed, and
logged against the organization (and user) it was for.

```
business logic ──► gateway.complete(LLMRequest(task=…), ctx)
                     │ 1 size limits      refuse: 429 usage_limit
                     │ 2 response cache   hit: free, logged "cached"
                     │ 3 quota limits     refuse: 429 usage_limit
                     │ 4 route            primary ─► fallback
                     │     circuit open? skip · spend check at this model's worst case
                     │     timeout · retry with backoff / retry-after · classify error
                     ▼
              ai_usage_logs (every attempt) · structured JSON logs · cost
```

Code: `packages/ai/evident_ai/gateway/`. Config: `config/ai.toml`.

## Switching providers

One line in `config/ai.toml`, or one environment variable:

```bash
AI_ROUTE_FILING_SUMMARIZATION="anthropic:claude-sonnet-5-5,gemini:gemini-3.5-flash-lite"
```

No code changes. `tests/test_ai_gateway.py::Config::test_one_environment_variable_moves_a_task_from_gemini_to_claude`
and `tests/test_ai_extraction_gateway.py` hold that to account. A route that
names a model without a price fails at startup: spend that cannot be priced
cannot be capped.

## Routing (config/ai.toml)

| Task | Primary | Fallback |
|---|---|---|
| `filing_summarization` | `gemini:gemini-3.5-flash-lite` | `groq:openai/gpt-oss-120b` |
| `financial_extraction` | `gemini:gemini-3.5-flash-lite` | `groq:openai/gpt-oss-120b` |
| `classification` | `groq:openai/gpt-oss-20b` | `gemini:gemini-3.5-flash-lite` |
| `complex_financial_reasoning` | `anthropic:claude-sonnet-5-5` | `gemini:gemini-3.6-flash` |
| `research_synthesis` | `gemini:gemini-3.6-flash` | `anthropic:claude-sonnet-5-5` |

Entity extraction (`evident_ai.extract`) is `financial_extraction`. Filing
summaries (`POST /v1/document/{id}/summary`) are `filing_summarization`. The
other three tasks are routed and priced, ready for the features that need them.

### Models and prices, verified 2026-11-21

Read from each provider's documentation on 2026-11-21. USD per 1M tokens.

| Model | Status | Input | Cached input | Output |
|---|---|---|---|---|
| `gemini-3.5-flash-lite` | stable | 0.30 | 0.03 | 2.50 |
| `gemini-3.6-flash` | stable | 0.75 → **1.50 from 2027-01-01** | 0.075 → 0.15 | 3.75 → 7.50 |
| `openai/gpt-oss-20b` (Groq) | production | 0.075 | 0.0375 | 0.30 |
| `openai/gpt-oss-120b` (Groq) | production | 0.15 | 0.075 | 0.60 |
| `claude-sonnet-5-5` | latest | 2.00 | 0.10 (write 2.50) | 10.00 |
| `claude-haiku-5-5` | latest | 0.10 (0.50 above 100k prompt) | 0.01 | 0.50 (2.50) |

Sources: ai.google.dev/gemini-api/docs/{models,pricing} (now served from
aistudio.google.com), console.groq.com/docs/{models,prompt-caching}, and
platform.claude.com/docs/en/about-claude/{models/overview,pricing}.

What changed since earlier work in this repo:
- `gemini-2.5-*` shuts down on 2026-12-01.
- Groq's Llama models shut down on 2026-08-16.
- Extraction's old hardcoded `claude-opus-5` is no longer the latest Opus.

Gemini 3.6 Flash's January price rise is a dated entry in the config, so cost
estimates change on the right day without anyone remembering to edit it.

**Worth knowing:** at these prices Claude Haiku 5.5 is cheaper than Gemini 3.5
Flash-Lite for typical filing work: about $0.0028 against $0.0098 for 20k
tokens in and 1.5k out. The routing table above is as specified; moving a task
to Haiku is one line.

## Usage logging

`ai_usage_logs` holds one row per **provider attempt**, plus one row for each
request answered from cache and each one refused by a limit. Attempts of one
request share `request_id`.

| status | meaning |
|---|---|
| `success` | the primary provider answered |
| `failed` | this attempt failed: timeout, rate limit, 5xx, auth… |
| `fallback` | a provider other than the primary answered (also when the primary was skipped because its circuit was open) |
| `cached` | answered from the response cache: no provider called, $0 |
| `blocked` | refused by a usage limit: no provider called |

`cached` and `blocked` extend the three statuses in the spec, because both are
requests a user made and the dashboard has to count them. `input_tokens`
counts every input token, cached ones included; `cached_tokens` is the part
read from the provider's prompt cache.

Background work (ingest, extraction) is attributed to the internal
organization `evident-internal`, with no user. Its spend is capped like a
customer's.

**Cost** is computed from the provider's own token counts and the price in
effect that day. Cached input, Anthropic cache writes and long-context tiers
are each priced at their own rate. Thinking tokens are billed as output, so
they count as output. This is an estimate for budgeting, not an invoice.

## Usage limits

Defaults are in `config/ai.toml [limits]`. Override them per organization with
`tools/admin.py set-limits`, or globally with `AI_LIMIT_*`.

| Limit | Default | Window |
|---|---|---|
| requests per user | 300 | UTC day |
| tokens per organization | 30M | UTC calendar month |
| spend per organization | $50 | UTC calendar month |
| input tokens per request | 200k | — |
| output tokens per request | 16k | — |

Rules:
- **Spend is checked against the request's worst case:** every input token
  uncached and every output token used. It is checked again for each fallback,
  at that fallback's price. The cap holds rather than being overshot by one
  expensive request.
- **A limit refuses; it never reroutes.** If the primary is unaffordable, the
  request is refused, even when a cheaper fallback would fit. Nothing switches
  model silently. The response is `429`:
  `{"error":"usage_limit","limit":…,"allowed":…,"current":…,"resets_at":…,"request_id":…}`,
  with `Retry-After`.
- **Cache hits are free, and served even over a limit.** Requests a user's
  daily limit counts are ones that reached a provider.
- **Input size is estimated** at ~4 characters per token before sending. The
  log records the provider's real count.

Concurrent requests can overshoot a monthly limit by at most the cost of the
requests in flight, since each is checked before it runs and logged after. At
5–25 customers that bound is cents.

## Caching

Filing analyses opt in with `CacheSpec(accession, prompt_version, shared)`. The
key is a SHA-256 over:
- the accession, task, provider, model and prompt version;
- a hash of the input: system prompt, user message, schema, max tokens and temperature.

So a prompt change, a model change or new input is a miss.

On lookup, each model on the route is tried in order. A fallback's cached
answer is reused rather than paid for again once the primary recovers.

Only complete answers are cached; truncated or refused ones are not.

`shared=True` serves every organization. Use it only for public filing text
and our own prompt, as the summary endpoint and entity extraction do. Anything carrying a
customer's own words must leave it off, which scopes the entry to their
organization.

Entity extraction is cached per chunk under the filing's accession and the
extraction prompt's version (`workers/memory_builder.py`). Rebuilding a
filing's memory after a change to resolution or storage answers every chunk
from the cache for $0. A new prompt version or model pays again, as it should.
`tools/benchmark.py` passes `use_cache=False`, because a replayed answer
measures nothing.

Entries expire after `cache_ttl_days` (90).

## Reliability

- **Timeouts:** `timeout_s` per attempt (90).
- **Retries:** up to `max_attempts` per provider (3), only for retryable kinds
  (rate limited, overloaded, 5xx, timeout, connection). Backoff is exponential
  with jitter (0.5s, 1s, 2s… capped at 8s). A `retry-after`/`RetryInfo` delay is
  honoured up to `max_retry_wait_s` (20s) when someone is waiting; a longer one
  falls back immediately.
- **Background work waits longer.** Requests made under `system_context()`
  (extraction, backfills) are marked `background` and wait out a delay up to
  `background_max_wait_s` (120s). A per-minute limit says when it lifts. Falling
  back instead fails every following chunk until it does. Before this, the
  first live seed on Gemini's free tier extracted only 19 of its 94 chunks.
- **Not retried:** bad request, auth, model not found, too large, quota
  exhausted. Each falls back once.
- **Circuit breaker:** per provider and model. It opens after
  `breaker_failures` (5) consecutive failures, for `breaker_cooldown_s` (60s),
  then lets one probe through. Quota, auth and not-found errors open it at
  once. Bad requests don't count. State is per process, which suits one API
  instance and a few workers. A request refused because every circuit on its
  route is open calls no provider, but it's still logged as `failed` against
  the primary.
- **Refusals** (safety blocks, `refusal` stop reasons) are returned, not
  retried or rerouted.
- **Errors** are classified per provider:
  - Gemini `RESOURCE_EXHAUSTED` with a per-day quota violation, an Anthropic 429
    without `retry-after` (a spend cap), and a Groq 429 with a long wait are all
    quota exhaustion.
  - Gemini's per-minute limit is a rate limit. Seen live on the free tier: a
    `QuotaFailure` with quotaId
    `GenerateRequestsPerMinutePerProjectPerModel-FreeTier`, plus
    `RetryInfo.retryDelay: "54s"`, and no `retry-after` header.
  - Anthropic 529 and Groq 498 are overloaded.

## Security

- **API keys live only in the backend environment.** The web app never sees a
  provider key. Its own admin key sits in an httpOnly, same-site-strict cookie
  that page JavaScript cannot read.
- **Redaction** runs before any error or log line is stored. It masks:
  - every key format (`sk-ant-…`, `gsk_…`, `AIza…`, `evk_…`);
  - `Bearer` tokens, `key=` query parameters and API-key headers;
  - the literal value of every `*_API_KEY`/`*_TOKEN`/`*SECRET` in the environment.
- **Prompts and filing text are never logged by default.** `AI_LOG_PROMPTS=1`
  adds a redacted 500-character preview, for debugging.
- **Evident API keys** are 256-bit random, shown once and stored as SHA-256.
- **Usage data is scoped by role in one function** (`apps/api/auth.py::usage_scope`):
  - a member sees their own usage;
  - an org admin sees their organization's;
  - a platform admin, who must be in the internal organization, sees everyone's.

  Asking for someone else's usage is a 403, not an empty result.

## API

| | |
|---|---|
| `POST /v1/document/{id}/summary` | key required. A cited summary, cached by filing. 429 on a limit, 503 when no provider answers (with the request id, no provider detail), 502 when the answer is unusable |
| `GET /v1/me` | the caller's user, role and organization |
| `GET /v1/ai-usage/summary` | totals, breakdowns by provider, model, customer and task, daily and monthly series, recent failures, filter facets. Filters: `date_from`, `date_to`, `provider`, `model`, `task_type`, `organization_id`, `user_id` |
| `GET /v1/ai-usage/limits` | each limit, how much is used, when it resets |

Read-only data endpoints stay public, as before. Keys are required only where
money is spent or usage is shown.

## Admin dashboard

`/admin/ai-usage` in the web app, for platform admins.

- **Header:** total spend leads, then requests, average cost, input and output
  tokens, failed requests, fallback rate, cache hits and refusals.
- **Charts:** daily and monthly spend, each with a table view, and cost by
  customer, provider, model and task. Each breakdown row narrows the page to it.
- **Failures table:** recent failures, fallbacks and refusals, with the
  redacted error.
- **Filters:** date (presets plus a range), provider, model and customer, in one
  row above everything they scope.

Times are UTC.

```bash
python tools/admin.py bootstrap-admin you@example.com      # prints a key once
open http://localhost:3000/admin/ai-usage                  # sign in with it
```

## Operating it

```bash
python tools/admin.py create-org acme "Acme Capital" --monthly-spend 25
python tools/admin.py create-user acme analyst@acme.com --role org_admin
python tools/admin.py create-key analyst@acme.com --name laptop
python tools/admin.py set-limits acme --daily-requests 100
python tools/admin.py revoke-key evk_AbCdEfGh
python tools/admin.py list
```

## Economics at 5–25 customers

Measured on Gemini 3.5 Flash-Lite, 2026-11-21:

| Call | Tokens in / out | Cost | Latency |
| --- | --- | --- | --- |
| Summary of the demo NVIDIA 10-K excerpt (32 chunks), via `POST /v1/document/1/summary` | 9,636 / 803 | $0.0049 | 3.6 s |
| The same summary again | — (cache) | $0 | 16 ms |
| Extraction, 3 real chunks (`tools/extract_live.py --seed --limit 3`) | 3,454 / 1,818 | $0.0056 | 1.8–4.6 s each |
| The same extraction again | — (cache) | $0 | — |

Output dominates: it is priced at 8× input, and extraction returns as many
tokens as it reads. From those, estimated:

- **A filing summary:** a full 10-K section of about 20k tokens in and up to
  1,500 out comes to about $0.01. It's paid once per filing and prompt version
  across all customers, because the cache is shared.
- **Full extraction of a 10-K:** about 470 chunks of roughly 2k tokens in and
  600 out, about $1 per filing. It runs once per filing, as internal work, and
  a rebuild is free from the cache.
- **The default $50/month cap** buys roughly 5,000 summaries at those sizes. A
  customer's real ceiling is set per organization.

## Known limits

- **Gemini is verified live; Groq and Anthropic are not.** Confirmed with a
  Gemini key on 2026-11-21:
  - both routed models are listed;
  - `responseJsonSchema` structured output works;
  - a cited summary works through the API;
  - extraction works on real chunks;
  - repeats are answered from the cache, all logged and costed.

  Gemini's per-minute rate-limit error has also been seen live (shape above).
  Groq and Anthropic adapters are tested only against each provider's
  documented wire format over mocked HTTP. Not yet seen live:
  - a Gemini per-day quota error;
  - Groq's `max_completion_tokens` field.

  The `anthropic-version` sent is the one the official SDK sends.
- **Groq's developer-plan rate limits** (8K tokens per minute on the GPT-OSS
  models, per its docs) are below one large summarization request. As a
  summarization fallback, Groq will need a paid tier.
- **Embeddings** (Voyage, OpenAI) still go through `EmbeddingProvider`, outside
  the gateway, and are not logged here. They have never been run either.
- **The Anthropic Batch API** (`extract.submit_batch`) needs an explicit client
  and is not routed or logged. Nothing calls it today.
- **Circuit-breaker state and limit checks are per process.** A fleet would
  want shared state.
