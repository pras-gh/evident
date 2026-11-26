/**
 * The home page's directory: hundreds of companies by sector, a filter that
 * forgives how names are typed, and a way on when nothing matches.
 */
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { CompanyDirectory } from "./CompanyDirectory";
import type { CompanyListItem } from "@/lib/types";

afterEach(cleanup);

const co = (ticker: string, display_name: string, sector: string | null,
            searchable = true, document_count = 1): CompanyListItem => ({
  ticker, display_name, name: display_name.toUpperCase(), cik: ticker.padStart(10, "0"),
  document_count, latest_filing: "2026-02-25", sector, searchable,
});

const ALL = [
  co("NVDA", "Nvidia", "Information Technology"),
  co("AAPL", "Apple Inc.", "Information Technology", false),
  co("MCO", "Moody's Corporation", "Financials", false, 0),
  co("WCC", "Wesco International", null),
];

const sectors = () => screen.getAllByRole("heading", { level: 2 }).map((h) => h.textContent);

describe("the company directory", () => {
  it("groups companies by sector, A to Z, with the unlisted last", () => {
    render(<CompanyDirectory companies={ALL} />);
    expect(sectors()).toEqual(["Financials 1", "Information Technology 2", "Other companies 1"]);
    const it = screen.getByRole("region", { name: /Information Technology/ });
    expect(within(it).getAllByRole("link").map((a) => a.textContent))
      .toEqual(["AAPLApple Inc.", "NVDANvidia"]);
    expect(screen.getByRole("link", { name: /Nvidia/ }).getAttribute("href")).toBe("/memory/nvda");
  });

  it("marks a company still being indexed", () => {
    render(<CompanyDirectory companies={ALL} />);
    expect(within(screen.getByRole("link", { name: /Apple/ })).getByLabelText("indexing"))
      .toBeTruthy();
    expect(within(screen.getByRole("link", { name: /Nvidia/ })).queryByLabelText("indexing"))
      .toBeNull();
    // indexed from EDGAR, text not downloaded: waiting to be asked about, not indexing
    expect(within(screen.getByRole("link", { name: /Moody/ })).queryByLabelText("indexing"))
      .toBeNull();
  });

  it("filters by ticker, or by name however it is typed", () => {
    render(<CompanyDirectory companies={ALL} />);
    const input = screen.getByRole("searchbox", { name: "Filter companies" });
    fireEvent.change(input, { target: { value: "$nv" } });
    expect(screen.getAllByRole("link").map((a) => a.textContent)).toEqual(["NVDANvidia"]);
    fireEvent.change(input, { target: { value: "moodys" } });
    expect(screen.getAllByRole("link").map((a) => a.textContent)).toEqual(["MCOMoody's Corporation"]);
    expect(screen.getByText("1 of 4")).toBeTruthy();
  });

  it("with no match, offers to search, which fetches the company", () => {
    render(<CompanyDirectory companies={ALL} />);
    fireEvent.change(screen.getByRole("searchbox"), { target: { value: "Acme Rockets" } });
    expect(screen.queryAllByRole("heading", { level: 2 })).toEqual([]);
    expect(screen.getByRole("link", { name: /Search for it/ }).getAttribute("href"))
      .toBe("/search?q=Acme%20Rockets");
  });
});
