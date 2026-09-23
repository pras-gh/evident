/** Memory card tiles and the revision drawer, from responses shaped like
 *  GET /v1/companies/NVDA/cards over the three-year NVIDIA corpus. */
import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { MemoryCardTile } from "./MemoryCardTile";
import { RevisionDrawer } from "./RevisionDrawer";
import type { CardDetail, CardRevision, MemoryCard } from "@/lib/types";

const rev3: CardRevision = {
  revision: 3,
  as_of: "2026-02-25",
  summary: "Restated without change.",
  source_note: "FY2026 10-K · 0001045810-26-000021",
  is_material: false,
  facts: [
    { key: "risk:export_controls", label: "Export Controls", value: null, unit: null, period: null, status: null },
    { key: "risk:supply_chain", label: "Supply Chain", value: null, unit: null, period: null, status: null },
  ],
  delta: { added: [], removed: [], changed: [] },
  evidence: [
    {
      document_id: 1, accession: "0001045810-26-000021", form_type: "10-K", page_number: 16,
      paragraph_id: "16_1", quote: "…such as export controls…",
      section_path: ["Item 1A. Risk Factors"], chunk_id: 12, entity_slug: "export_controls",
    },
    {
      document_id: 1, accession: "0001045810-26-000021", form_type: "10-K", page_number: 25,
      paragraph_id: "25_1#2", quote: "…supply chain…",
      section_path: ["Item 1A. Risk Factors"], chunk_id: 30, entity_slug: "supply_chain",
    },
  ],
};

const risks: MemoryCard = {
  kind: "risks", title: "Risks", source_label: "Risk section", revision_count: 3,
  material_count: 2, last_updated_at: "2026-02-25", current: rev3, unavailable: null,
};

const revenue: MemoryCard = {
  kind: "revenue", title: "Revenue", source_label: "10-Q / 10-K", revision_count: 0,
  material_count: 0, last_updated_at: null, current: null,
  unavailable: "Needs reported metric values, and nothing extracts them yet.",
};

afterEach(cleanup);

describe("card tiles", () => {
  it("count named facts rather than showing a blank value", () => {
    render(<MemoryCardTile card={risks} onOpen={() => {}} />);
    const tile = screen.getByRole("button", { name: /Risks/ });
    expect(tile.querySelector(".val")?.textContent).toBe("2");
    expect(within(tile).getByText("risk factors disclosed")).toBeTruthy();
    expect(within(tile).getByText("Restated without change.")).toBeTruthy();
    expect(within(tile).getByText("Updated 2026-02-25")).toBeTruthy();
  });

  it("show a number when the card has one", () => {
    const capex: MemoryCard = {
      ...risks, kind: "capex", title: "CapEx",
      current: { ...rev3, facts: [{ key: "capex:FY2025", label: "CapEx FY2025", value: "3,236", unit: "USD m", period: "FY2025", status: null }] },
    };
    render(<MemoryCardTile card={capex} onOpen={() => {}} />);
    const tile = screen.getByRole("button", { name: /CapEx/ });
    expect(tile.querySelector(".val")?.textContent).toBe("3,236");
    expect(within(tile).getByText("FY2025")).toBeTruthy();
  });

  it("say why a card is empty instead of looking quiet", () => {
    render(<MemoryCardTile card={revenue} onOpen={() => {}} />);
    const tile = screen.getByRole("button", { name: /Revenue/ });
    expect(tile.querySelector(".val")).toBeNull();
    expect(within(tile).getByText(revenue.unavailable!)).toBeTruthy();
    expect(within(tile).getByText("No data yet")).toBeTruthy();
  });
});

describe("revision drawer", () => {
  const detail: CardDetail = { ...risks, history: [rev3] };

  it("links each fact's evidence to the paragraph in the viewer", () => {
    render(<RevisionDrawer card={detail} ticker="NVDA" onClose={() => {}} />);
    const links = screen.getAllByRole("link");
    expect(links.map((a) => [a.textContent, a.getAttribute("href")])).toEqual([
      ["10-K · p. 16 · Item 1A. Risk Factors", "/evidence/nvda/export_controls?cite=12%3A16_1"],
      ["10-K · p. 25 · Item 1A. Risk Factors", "/evidence/nvda/supply_chain?cite=30%3A25_1%232"],
    ]);
  });

  it("explains an empty card", () => {
    render(<RevisionDrawer card={{ ...revenue, history: [] }} ticker="NVDA" onClose={() => {}} />);
    expect(screen.getByText(revenue.unavailable!)).toBeTruthy();
  });
});
