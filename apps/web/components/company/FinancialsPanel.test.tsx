/**
 * Statements from XBRL: figures as a reader expects them, each a link to the
 * filing that reported it, and a derived Q4 that says it was derived.
 */
import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { FinancialsPanel } from "./FinancialsPanel";
import { FilingsRail } from "./FilingsRail";
import { JustFiled } from "@/components/home/JustFiled";
import { formatValue } from "@/lib/format";
import type { Filing, Financials } from "@/lib/types";

afterEach(cleanup);

const cell = (value: number, derived = false) => ({
  value, concept: "us-gaap:Revenues", accession: "0001045810-25-000023", form: "10-K",
  filed: "2025-02-26", derived, url: "https://www.sec.gov/Archives/edgar/data/1045810/x.htm",
});

const quarterly: Financials = {
  ticker: "NVDA", period: "quarterly", loaded_at: "2026-11-26T10:00:00Z",
  columns: [
    { label: "Q3 FY2025", period_start: "2024-07-29", period_end: "2024-10-27" },
    { label: "Q4 FY2025", period_start: null, period_end: "2025-01-26" },
  ],
  rows: [
    { key: "revenue", label: "Revenue", unit: "USD", cells: [cell(35e9), cell(39.3e9, true)] },
    { key: "eps_diluted", label: "Diluted EPS", unit: "USD/shares", cells: [cell(0.78), null] },
  ],
};

describe("figures", () => {
  it("read as a statement reader expects", () => {
    expect(formatValue(130_497_000_000, "USD")).toBe("$130B");
    expect(formatValue(39_331_000_000, "USD")).toBe("$39.3B");
    expect(formatValue(-1_200_000_000, "USD")).toBe("−$1.2B");
    expect(formatValue(2.9, "USD/shares")).toBe("$2.90");
    expect(formatValue(24_400_000_000, "shares")).toBe("24.4B");
    expect(formatValue(1_250_000_000_000, "USD")).toBe("$1.25T");
  });
});

describe("the financial statements panel", () => {
  it("is a table with a column per period, each figure linked to its filing", () => {
    render(<FinancialsPanel ticker="NVDA" data={quarterly} />);
    const table = screen.getByRole("table");
    expect(within(table).getAllByRole("columnheader").map((h) => h.textContent))
      .toEqual(["Line", "Q4 FY2025", "Q3 FY2025"]);   // newest first, as a 10-K prints
    const rev = within(table).getByRole("row", { name: /Revenue/ });
    const link = within(rev).getByRole("link", { name: "$35.0B" });
    expect(link.getAttribute("href")).toContain("sec.gov");
    expect(link.getAttribute("title")).toBe("us-gaap:Revenues · 10-K filed 2025-02-26");
  });

  it("marks a derived Q4, and says what that means", () => {
    render(<FinancialsPanel ticker="NVDA" data={quarterly} />);
    const q4 = screen.getByRole("link", { name: /\$39\.3B/ });
    expect(q4.getAttribute("title")).toMatch(/derived: the year less its first three quarters/);
    expect(screen.getByText(/Fiscal Q4 is not filed on its own/)).toBeTruthy();
    expect(screen.getByLabelText("not filed")).toBeTruthy();
  });

  it("toggles between annual and quarterly by link", () => {
    render(<FinancialsPanel ticker="NVDA" data={quarterly} />);
    const nav = screen.getByRole("navigation", { name: "Period" });
    expect(within(nav).getByRole("link", { name: "Quarterly" }).getAttribute("aria-current"))
      .toBe("page");
    expect(within(nav).getByRole("link", { name: "Annual" }).getAttribute("href"))
      .toBe("/memory/nvda");
  });

  it("says why there is nothing", () => {
    render(<FinancialsPanel ticker="NVDA" data={null} />);
    expect(screen.getByText(/not yet for this one/)).toBeTruthy();
    cleanup();
    render(<FinancialsPanel ticker="NVDA"
                            data={{ ...quarterly, columns: [], rows: [] }} />);
    expect(screen.getByText(/has not filed these lines in XBRL/)).toBeTruthy();
  });
});

const filing = (over: Partial<Filing>): Filing => ({
  ticker: "NVDA", company: "NVIDIA CORP", cik: "0001045810", accession: "a",
  form_type: "8-K", filed_date: "2026-11-19", accepted_at: "2026-11-19T21:20:00Z",
  report_date: null, items: "2.02,9.01", about: "results of operations", description: null,
  url: "https://www.sec.gov/x", alerted: true, minutes_after_filing: 6, loaded: false, ...over,
});

describe("filings", () => {
  it("in the rail: form, date, what it is about, and whether Parallx has its text", () => {
    render(<FilingsRail filings={[filing({}), filing({ accession: "b", form_type: "10-Q",
                                                       about: null, loaded: true })]} />);
    const items = screen.getAllByRole("listitem");
    expect(items[0].textContent).toContain("results of operations");
    expect(within(items[1]).getByText("in Parallx")).toBeTruthy();
  });

  it("on the home page, newest first, each opening its company", () => {
    render(<JustFiled filings={[filing({})]} />);
    const row = screen.getByRole("link");
    expect(row.getAttribute("href")).toBe("/memory/nvda");
    expect(row.textContent).toContain("results of operations");
  });

  it("with none, the home page shows no empty section", () => {
    const { container } = render(<JustFiled filings={[]} />);
    expect(container.textContent).toBe("");
  });
});
