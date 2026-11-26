/**
 * The plans and the upgrade: two paid plans and no third, annual at two
 * months free, nothing claimed that the product does not do, no USD checkout
 * for India, and checkout that starts on the server.
 */
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Paywall, Plan, Subscription } from "@/lib/types";

const startCheckout = vi.fn();
const getMySubscription = vi.fn();
vi.mock("@/lib/client-api", async (orig) => ({
  ...(await orig<typeof import("@/lib/client-api")>()),
  startCheckout: (...a: unknown[]) => startCheckout(...a),
  getMySubscription: () => getMySubscription(),
}));

import { PlanCards } from "./PlanCards";
import { UpgradeModal } from "./UpgradeModal";
import { UsageMeter, usageLine } from "./UsageMeter";
import BillingSuccess from "@/app/billing/success/page";
import { Continue } from "@/components/onboarding/Continue";

const plan = (id: Plan["id"], name: string, m: number, y: number, rec = false): Plan => ({
  id, name, audience: id === "analyst" ? "For analysts" : "For investors", description: "",
  monthly_price: m, annual_price: y, currency: "usd", max_companies: 10, monthly_ai_queries: 100,
  recommended: rec, entitlements: {}, purchasable: true,
  features: [{ label: "Track up to 10 companies", available: true },
             { label: "Management Promise Tracker", available: false }],
});
const PLANS = [plan("free", "Free", 0, 0), plan("investor", "Iix", 1900, 19000),
               plan("analyst", "IIx", 3900, 39000, true)];

const assign = vi.fn();
beforeEach(() => {
  Object.defineProperty(window, "location", {
    configurable: true, value: { ...window.location, assign, search: "" },
  });
  Object.defineProperty(navigator, "language", { configurable: true, value: "en-US" });
  try { window.localStorage?.removeItem?.("parallx.billing.country"); } catch { /* no storage */ }
});
afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  vi.useRealTimers();
});
const flush = () => act(async () => { await new Promise((r) => setTimeout(r, 0)); });

describe("the plan cards", () => {
  it("are Iix and IIx only, IIx recommended, unbuilt features marked", () => {
    render(<PlanCards plans={PLANS} signedIn />);
    const cards = screen.getAllByRole("article");
    expect(cards.map((c) => c.getAttribute("aria-label")))
      .toEqual(["Iix, For investors", "IIx, For analysts"]);
    expect(within(cards[1]).getByText("Recommended")).toBeTruthy();
    expect(within(cards[0]).getByText("Coming soon")).toBeTruthy();
    expect(within(cards[0]).getByText("$19")).toBeTruthy();
  });

  it("annual is the year's price, two months free", () => {
    render(<PlanCards plans={PLANS} signedIn />);
    fireEvent.click(screen.getByRole("radio", { name: /Annual/ }));
    const [iix, iixa] = screen.getAllByRole("article");
    expect(within(iix).getByText("$190")).toBeTruthy();
    expect(within(iix).getByText("$15.83/month, billed yearly")).toBeTruthy();
    expect(within(iixa).getByText("$390")).toBeTruthy();
  });

  it("checkout starts on the server and goes to Stripe", async () => {
    startCheckout.mockResolvedValue({ mode: "checkout", url: "https://checkout.stripe.test/x" });
    render(<PlanCards plans={PLANS} signedIn />);
    fireEvent.click(screen.getByRole("button", { name: "Get IIx" }));
    await flush();
    const [planId, interval, country, key] = startCheckout.mock.calls[0];
    expect([planId, interval, country]).toEqual(["analyst", "month", "US"]);
    expect(key).toBeTruthy();
    expect(assign).toHaveBeenCalledWith("https://checkout.stripe.test/x");
  });

  it("India: no USD checkout, and says why", () => {
    render(<PlanCards plans={PLANS} signedIn />);
    fireEvent.change(screen.getByRole("combobox"), { target: { value: "IN" } });
    expect(screen.getByRole("note").textContent).toMatch(/rupees/);
    expect((screen.getByRole("button", { name: "Get Iix" }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("signed out, choosing a plan signs up first and comes back", () => {
    render(<PlanCards plans={PLANS} signedIn={false} />);
    fireEvent.click(screen.getByRole("button", { name: "Get Iix" }));
    expect(assign).toHaveBeenCalledWith(
      "/signin?mode=signup&next=%2Fpricing%3Fplan%3Dinvestor%26interval%3Dmonth");
    expect(startCheckout).not.toHaveBeenCalled();
  });
});

const usage = (used: number) => ({
  plan_id: "free", plan_name: "Free", source: "free", companies_used: used,
  companies_limit: 2, companies_left: 2 - used, free_credits_used: used, free_credits_total: 2,
  ai_queries_used: 0, ai_queries_limit: 10, ai_queries_left: 10, period_end: null,
  companies: [{ ticker: "NVDA", name: "NVIDIA CORP", first_researched_at: "", last_accessed_at: "",
                status: "active", access_source: "free_credit" }],
}) as Paywall["usage"] & object;

describe("the upgrade", () => {
  it("says what the spec says, offers both plans, and keeps research one click away", () => {
    const paywall: Paywall = { reason: "free_credits_used", message: "", usage: usage(2),
                               companies: ["Apple Inc."] };
    render(<UpgradeModal plans={PLANS} paywall={paywall} returnTo="/memory/aapl" />);
    const dialog = screen.getByRole("dialog", { name: "Upgrade Parallx" });
    expect(within(dialog).getByRole("heading", { level: 2 }).textContent)
      .toBe("Your next research starts with Parallx.");
    expect(within(dialog).getByText(/used your 2 free company research credits/)).toBeTruthy();
    expect(within(dialog).getAllByRole("article")).toHaveLength(2);
    expect(within(dialog).getByRole("link", { name: "NVDA" }).getAttribute("href"))
      .toBe("/memory/nvda");
    fireEvent.click(within(dialog).getByRole("button", { name: "Close" }));
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(screen.getByRole("button", { name: "See plans" })).toBeTruthy();
  });

  it("usage reads as the spec writes it", () => {
    render(<UsageMeter usage={usage(2) as never} />);
    expect(usageLine(usage(2) as never)).toBe("2 of 2 free companies used");
    expect(screen.getByRole("meter").getAttribute("aria-valuenow")).toBe("2");
  });
});

const sub = (over: Partial<Subscription>): Subscription => ({
  plan_id: "free", plan_name: "Free", source: "free", status: null, billing_interval: null,
  current_period_end: null, cancel_at_period_end: false, grace_until: null,
  requires_action: false, features: {}, has_billing_account: false, ...over,
});

describe("after checkout", () => {
  it("waits for the webhook, not the redirect", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    getMySubscription
      .mockResolvedValueOnce(sub({}))
      .mockResolvedValueOnce(sub({}))
      .mockResolvedValue(sub({ plan_id: "investor", plan_name: "Iix", source: "subscription",
                               status: "active" }));
    render(<BillingSuccess />);
    expect(screen.getByRole("heading").textContent).toMatch(/Confirming your subscription/);
    await act(async () => { await vi.advanceTimersByTimeAsync(4500); });
    expect(screen.getByRole("heading").textContent).toBe("You’re on Iix.");
    expect(getMySubscription).toHaveBeenCalledTimes(3);
  });
});

describe("onboarding", () => {
  it("continues the interrupted search by itself, unless they stay", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    render(<Continue href="/search?q=nvidia" label="Continue to “nvidia”" seconds={2} />);
    expect(screen.getByRole("status").textContent).toMatch(/Continuing in 2s/);
    // a tick a second, each rendered before the next is set
    for (let i = 0; i < 3; i++) await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
    expect(assign).toHaveBeenCalledWith("/search?q=nvidia");
    cleanup();
    assign.mockClear();
    render(<Continue href="/search?q=x" label="Go" seconds={2} />);
    fireEvent.click(screen.getByRole("button", { name: "Stay here" }));
    for (let i = 0; i < 3; i++) await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
    expect(assign).not.toHaveBeenCalled();
  });
});
