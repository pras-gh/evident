#!/usr/bin/env python3
"""Check the Stripe configuration before taking a payment. Read-only.

    python tools/billing_check.py

For each plan and interval: the Price ID in the environment exists in the
Stripe account the secret key belongs to, is active, recurring at the right
interval, in USD, at the right amount — and the webhook secret is set. A
Price ID pasted into the wrong variable would sell IIx at Iix's price, or the
other way round; this says so before a customer finds out.

Exit status 0 when everything matches, 1 otherwise.
"""
from __future__ import annotations

import os
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps"))
sys.path.insert(0, str(ROOT))

#: what each variable must name: (plan, interval) → USD cents
EXPECTED = {("investor", "month"): 1900, ("investor", "year"): 19000,
            ("analyst", "month"): 3900, ("analyst", "year"): 39000}


def main() -> int:
    from tools.generate_embeddings import load_env
    load_env()
    from api.billing import PRICE_ENV
    key = os.environ.get("STRIPE_SECRET_KEY", "")
    problems = []
    if not key:
        print("STRIPE_SECRET_KEY is not set")
        return 1
    mode = "live" if key.startswith(("sk_live_", "rk_live_")) else "test"
    print(f"Stripe {mode} mode")
    if not os.environ.get("STRIPE_WEBHOOK_SECRET"):
        problems.append("STRIPE_WEBHOOK_SECRET is not set: webhooks would be refused, and no "
                        "subscription would ever activate")
    import stripe
    for (plan, interval), cents in EXPECTED.items():
        env = PRICE_ENV[(plan, interval)]
        pid = os.environ.get(env)
        if not pid:
            problems.append(f"{env} is not set")
            continue
        try:
            p = stripe.Price.retrieve(pid, api_key=key)
        except stripe.error.StripeError as exc:
            problems.append(f"{env}={pid}: {exc.user_message or exc}")
            continue
        rec = p.get("recurring") or {}
        got = (p.get("unit_amount"), p.get("currency"), rec.get("interval"), p.get("active"))
        want = (cents, "usd", interval, True)
        line = f"{env:34} {pid}  {got[0]} {got[1]} /{got[2]}"
        if got != want:
            problems.append(f"{line} — expected {cents} usd /{interval}, active")
        else:
            print(f"ok  {line}")
    for p in problems:
        print(f"!!  {p}")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
