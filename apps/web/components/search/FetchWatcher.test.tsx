/**
 * Waiting for an on-demand fetch: poll the fetch, refresh once it's over.
 */
import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const refresh = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ refresh }) }));

import { FetchWatcher } from "./FetchWatcher";

function answers(...statuses: string[]) {
  const fetch = vi.fn(async () => {
    const status = statuses.length > 1 ? statuses.shift()! : statuses[0];
    return new Response(JSON.stringify({ id: 7, status }), { status: 200 });
  });
  vi.stubGlobal("fetch", fetch);
  return fetch;
}

beforeEach(() => { vi.useFakeTimers(); refresh.mockReset(); });
afterEach(() => { cleanup(); vi.useRealTimers(); vi.unstubAllGlobals(); });

describe("FetchWatcher", () => {
  it("polls the fetch, not the search, and refreshes when it is done", async () => {
    const fetch = answers("queued", "running", "done");
    render(<FetchWatcher ids={[7]} every={1000} />);
    await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
    expect(fetch).toHaveBeenCalledWith("/api/fetches/7", { cache: "no-store" });
    expect(refresh).not.toHaveBeenCalled();
    await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
    expect(screen.getByText(/Downloading and indexing… 2s/)).toBeTruthy();
    await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
    expect(refresh).toHaveBeenCalledTimes(1);
    await act(async () => { await vi.advanceTimersByTimeAsync(5000); });
    expect(refresh).toHaveBeenCalledTimes(1);
    expect(fetch).toHaveBeenCalledTimes(3);
  });

  it("refreshes on failure too, so the page can say what went wrong", async () => {
    answers("failed");
    render(<FetchWatcher ids={[7]} every={1000} />);
    await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
    expect(refresh).toHaveBeenCalledTimes(1);
  });
});
