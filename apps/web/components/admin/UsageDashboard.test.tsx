/** The admin AI usage dashboard: numbers, charts, filters. */
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { parseFilters, summaryPath, type UsageSummary } from "@/lib/admin";
import { compact, percent, usd } from "@/lib/format";
import { ticks, UsageColumns } from "./UsageColumns";
import { BarList, fillDays, UsageDashboard, utcMinute, withFilters } from "./UsageDashboard";

afterEach(cleanup);

const ACME = "11111111-1111-1111-1111-111111111111";

const data: UsageSummary = {
  organization_id: null, user_id: null, date_from: "2026-11-15", date_to: "2026-11-21",
  totals: {
    cost_usd: "0.0381", requests: 42, attempts: 47, input_tokens: 812_000, output_tokens: 9_400,
    cached_tokens: 120_000, avg_cost_per_request: "0.000953", failed_requests: 1,
    failed_attempts: 5, fallback_requests: 3, fallback_rate: 0.0769, cache_hits: 2,
    blocked_requests: 1,
  },
  by_provider: [
    { key: "gemini", label: "gemini", cost_usd: "0.0300", requests: 36, input_tokens: 700_000, output_tokens: 8000, failed_attempts: 4 },
    { key: "groq", label: "groq", cost_usd: "0.0081", requests: 3, input_tokens: 112_000, output_tokens: 1400, failed_attempts: 0 },
  ],
  by_model: [{ key: "gemini:gemini-3.5-flash-lite", label: "gemini:gemini-3.5-flash-lite", cost_usd: "0.03", requests: 36, input_tokens: 1, output_tokens: 1, failed_attempts: 0 }],
  by_organization: [{ key: ACME, label: "Acme Capital", cost_usd: "0.0381", requests: 42, input_tokens: 1, output_tokens: 1, failed_attempts: 0 }],
  by_task: [{ key: "filing_summarization", label: "filing summarization", cost_usd: "0.0381", requests: 42, input_tokens: 1, output_tokens: 1, failed_attempts: 0 }],
  daily: [
    { period: "2026-11-15", cost_usd: "0.0100", requests: 10 },
    { period: "2026-11-18", cost_usd: "0.0281", requests: 32 },
  ],
  monthly: [{ period: "2026-11", cost_usd: "0.0381", requests: 42 }],
  recent_failures: [
    { created_at: "2026-11-21T10:02:00Z", request_id: "r1", organization: "Acme Capital",
      provider: "gemini", model: "gemini-3.5-flash-lite", task_type: "filing_summarization",
      status: "failed", error_message: "overloaded: 503: UNAVAILABLE" },
    { created_at: "2026-11-21T10:02:01Z", request_id: "r1", organization: "Acme Capital",
      provider: "groq", model: "openai/gpt-oss-120b", task_type: "filing_summarization",
      status: "fallback", error_message: null },
  ],
  facets: {
    providers: ["gemini", "groq"], models: ["gemini:gemini-3.5-flash-lite", "groq:openai/gpt-oss-120b"],
    organizations: [{ id: ACME, slug: "acme", name: "Acme Capital", is_internal: false }],
  },
};

describe("formats", () => {
  it("keeps the meaningful digits of small AI costs", () => {
    expect([usd("0"), usd("0.007050"), usd("0.0381"), usd("12.345"), usd("1234.5")])
      .toEqual(["$0", "$0.007", "$0.038", "$12.35", "$1,235"]);
  });

  it("compacts big counts and keeps small ones whole", () => {
    expect([compact(1284), compact(12_900), compact(4_200_000)]).toEqual(["1,284", "12.9K", "4.2M"]);
    expect([percent(0.0769), percent(0.004), percent(0)]).toEqual(["8%", "0.4%", "0%"]);
  });

  it("puts axis ticks on clean steps", () => {
    expect(ticks(0.0281)).toEqual([0, 0.01, 0.02, 0.03]);
    expect(ticks(37)).toEqual([0, 10, 20, 30, 40]);
    expect(ticks(0)).toEqual([0]);
  });
});

describe("filters", () => {
  it("accepts only well-formed values from the query string", () => {
    expect(parseFilters({ date_from: "2026-11-01", date_to: "'; drop", organization_id: "nope",
                          provider: "gemini", model: ["m1", "m2"] }))
      .toEqual({ date_from: "2026-11-01", provider: "gemini", model: "m1" });
    expect(summaryPath({ provider: "groq", organization_id: ACME }))
      .toBe(`/ai-usage/summary?provider=groq&organization_id=${ACME}`);
    expect(summaryPath({})).toBe("/ai-usage/summary");
  });

  it("a filter change keeps the others", () => {
    expect(withFilters({ date_from: "2026-11-01", provider: "groq" }, { organization_id: ACME }))
      .toBe(`?date_from=2026-11-01&provider=groq&organization_id=${ACME}`);
  });
});

describe("daily spend", () => {
  it("shows every day in the range, zero where nothing was spent", () => {
    const days = fillDays(data.daily, "2026-11-15", "2026-11-21");
    expect(days.map((d) => d.cost_usd)).toEqual(["0.0100", "0", "0", "0.0281", "0", "0", "0"]);
  });

  it("each column has a value-first tooltip on hover and on keyboard focus", () => {
    render(<UsageColumns label="Daily spend" columns={[
      { period: "a", label: "Nov 15", value: 0.01, requests: 10 },
      { period: "b", label: "Nov 18", value: 0.0281, requests: 32 }]} />);
    const cols = within(screen.getByRole("group", { name: "Daily spend" })).getAllByRole("button");
    expect(cols[1].getAttribute("aria-label")).toBe("Nov 18: $0.028, 32 requests");
    fireEvent.pointerEnter(cols[1]);
    const tip = screen.getByRole("status");
    expect(tip.querySelector("strong")?.textContent).toBe("$0.028");
    expect(tip.textContent).toContain("32 requests");
    fireEvent.blur(cols[1]);
    fireEvent.focus(cols[0]);
    expect(screen.getByRole("status").textContent).toContain("Nov 15");
  });

  it("scales columns against the top tick, not the largest value", () => {
    render(<UsageColumns label="x" columns={[{ period: "a", label: "a", value: 0.0281, requests: 1 }]} />);
    const bar = document.querySelector(".col-bar") as HTMLElement;
    expect(bar.style.height).toBe(`${(0.0281 / 0.03) * 100}%`);
  });
});

describe("dashboard", () => {
  function page(filters = {}) {
    return render(<UsageDashboard data={data} filters={filters} today="2026-11-21" />);
  }

  it("leads with total spend and shows every headline number", () => {
    page();
    const totals = screen.getByRole("region", { name: "Totals" });
    expect(totals.querySelector(".adm-hero-value")?.textContent).toBe("$0.038");
    for (const text of ["42", "812K", "9,400", "8%", "Refused by a limit"]) {
      expect(within(totals).getAllByText(text, { exact: false }).length).toBeGreaterThan(0);
    }
    expect(within(totals).getByText("$0.00095")).toBeTruthy();
    expect(within(totals).getByText("per answered request")).toBeTruthy();
  });

  it("shows event times in UTC whatever offset the API sent", () => {
    expect(utcMinute("2026-11-21T00:57:59+05:30")).toBe("2026-11-20 19:27");
    expect(utcMinute("2026-11-20T19:27:59Z")).toBe("2026-11-20 19:27");
  });

  it("flags failures, fallbacks and refusals by word and glyph, not colour alone", () => {
    page();
    const table = screen.getByRole("region", { name: "Failures, fallbacks and refusals" });
    expect(within(table).getByText("Failed call", { exact: false })).toBeTruthy();
    expect(within(table).getByText("Fallback answered", { exact: false })).toBeTruthy();
    expect(within(table).getByText("answered by this fallback")).toBeTruthy();
  });

  it("each breakdown row narrows the dashboard to it, keeping the date range", () => {
    page({ date_from: "2026-11-15" });
    const byCustomer = screen.getByRole("region", { name: "Cost by customer" });
    expect(within(byCustomer).getByRole("link").getAttribute("href"))
      .toBe(`?date_from=2026-11-15&organization_id=${ACME}`);
    const byModel = screen.getByRole("region", { name: "Cost by model" });
    expect(within(byModel).getByRole("link").getAttribute("href"))
      .toBe("?date_from=2026-11-15&model=gemini-3.5-flash-lite");
  });

  it("offers date presets, provider, model and customer filters in one row", () => {
    page();
    const form = screen.getByRole("form", { name: "Filters" });
    expect(within(form).getAllByRole("link").map((a) => a.textContent))
      .toEqual(["Last 7 days", "Last 30 days", "Last 90 days", "Reset"]);
    expect(within(form).getByText("Last 7 days").getAttribute("aria-current")).toBe("true");
    const selects = within(form).getAllByRole("combobox").map((s) => s.getAttribute("name"));
    expect(selects).toEqual(["provider", "model", "organization_id"]);
  });

  it("bars are ranked in one hue with the value at the tip", () => {
    render(<BarList rows={data.by_provider} />);
    const rows = document.querySelectorAll(".bars-row");
    expect([...rows].map((r) => r.querySelector(".bars-value")?.textContent)).toEqual(["$0.03", "$0.0081"]);
    const widths = [...document.querySelectorAll<HTMLElement>(".bars-bar")].map((b) => b.style.width);
    expect(widths).toEqual(["100%", `${(0.0081 / 0.03) * 100}%`]);
  });
});
