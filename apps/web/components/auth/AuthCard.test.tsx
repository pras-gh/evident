/**
 * Signing in: Google, email (verification first), phone (+91 by default, a
 * code with a resend cooldown) — and landing where the reader was headed,
 * through onboarding the first time.
 */
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { setAuthClient, type AuthClient } from "@/lib/auth-client";
import { safeNext } from "@/lib/next-path";

const createSession = vi.fn();
vi.mock("@/lib/client-api", async (orig) => ({
  ...(await orig<typeof import("@/lib/client-api")>()),
  createSession: (t: string) => createSession(t),
}));

import { AuthCard, toE164 } from "./AuthCard";
import { SearchBox } from "./SearchBox";

const assign = vi.fn();
function fakeAuth(over: Partial<AuthClient> = {}): AuthClient {
  return {
    google: vi.fn(async () => "tok-google"),
    emailSignUp: vi.fn(async () => undefined),
    emailSignIn: vi.fn(async () => "tok-email"),
    resendVerification: vi.fn(async () => undefined),
    checkVerified: vi.fn(async () => "tok-verified"),
    resetPassword: vi.fn(async () => undefined),
    phoneStart: vi.fn(async () => undefined),
    phoneVerify: vi.fn(async () => "tok-phone"),
    signOut: vi.fn(async () => undefined),
    ...over,
  };
}

beforeEach(() => {
  Object.defineProperty(window, "location", {
    configurable: true, value: { ...window.location, assign, origin: "http://t", search: "" },
  });
  createSession.mockResolvedValue({ user: { user_id: "u1" }, is_new: true });
});
afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  vi.useRealTimers();
  setAuthClient(null);
});

const flush = () => act(async () => { await new Promise((r) => setTimeout(r, 0)); });

describe("the sign-in card", () => {
  it("says what it is for when a search sent them here", () => {
    render(<AuthCard next="/search?q=nvidia" query="nvidia" />);
    expect(screen.getByRole("heading").textContent).toBe("Sign in to research “nvidia”");
    expect(screen.getByRole("button", { name: /Continue with Google/ })).toBeTruthy();
    expect(screen.getByRole("link", { name: "Terms" }).getAttribute("href")).toBe("/terms");
  });

  it("Google: a new account goes through onboarding, then on to the search", async () => {
    const auth = fakeAuth();
    setAuthClient(auth);
    render(<AuthCard next="/search?q=nvidia" query="nvidia" />);
    fireEvent.click(screen.getByRole("button", { name: /Continue with Google/ }));
    await flush();
    expect(createSession).toHaveBeenCalledWith("tok-google");
    expect(auth.signOut).toHaveBeenCalled();
    expect(assign).toHaveBeenCalledWith("/welcome?next=%2Fsearch%3Fq%3Dnvidia");
  });

  it("an existing account goes straight where it was headed", async () => {
    setAuthClient(fakeAuth());
    createSession.mockResolvedValue({ user: {}, is_new: false });
    render(<AuthCard next="/memory/nvda" />);
    fireEvent.click(screen.getByRole("button", { name: /Continue with Google/ }));
    await flush();
    expect(assign).toHaveBeenCalledWith("/memory/nvda");
  });

  it("email: checks the address and password before asking anyone", () => {
    setAuthClient(fakeAuth());
    render(<AuthCard startWith="signup" />);
    fireEvent.change(screen.getByPlaceholderText("you@example.com"), { target: { value: "nope" } });
    fireEvent.click(screen.getByRole("button", { name: "Create account" }));
    expect(screen.getByRole("status").textContent).toMatch(/valid email/);
    fireEvent.change(screen.getByPlaceholderText("you@example.com"),
                     { target: { value: "ada@example.com" } });
    fireEvent.change(screen.getByPlaceholderText("At least 8 characters"),
                     { target: { value: "short" } });
    fireEvent.click(screen.getByRole("button", { name: "Create account" }));
    expect(screen.getByRole("status").textContent).toMatch(/at least 8/);
  });

  it("email sign-up waits for verification, with a resend cooldown", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const auth = fakeAuth();
    setAuthClient(auth);
    render(<AuthCard startWith="signup" />);
    fireEvent.change(screen.getByPlaceholderText("you@example.com"),
                     { target: { value: "ada@example.com" } });
    fireEvent.change(screen.getByPlaceholderText("At least 8 characters"),
                     { target: { value: "long-enough" } });
    fireEvent.click(screen.getByRole("button", { name: "Create account" }));
    await flush();
    expect(auth.emailSignUp).toHaveBeenCalledWith("ada@example.com", "long-enough");
    expect(screen.getByText("Check your inbox")).toBeTruthy();
    expect((screen.getByRole("button", { name: /Resend the link in/ }) as HTMLButtonElement)
      .disabled).toBe(true);
    expect(createSession).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "I've verified my email" }));
    await flush();
    expect(createSession).toHaveBeenCalledWith("tok-verified");
  });

  it("phone: India by default, a code, and a resend cooldown", async () => {
    const auth = fakeAuth();
    setAuthClient(auth);
    render(<AuthCard />);
    fireEvent.click(screen.getByRole("tab", { name: "Phone number" }));
    expect((screen.getByLabelText("Country code") as HTMLSelectElement).value).toBe("IN");
    fireEvent.change(screen.getByPlaceholderText("98123 45678"), { target: { value: "98123 45678" } });
    fireEvent.click(screen.getByRole("button", { name: "Send code" }));
    await flush();
    expect(auth.phoneStart).toHaveBeenCalledWith("+919812345678");
    expect(screen.getByRole("button", { name: /Resend code in 30s/ })).toBeTruthy();
    fireEvent.change(screen.getByPlaceholderText("••••••"), { target: { value: "12a3456" } });
    fireEvent.click(screen.getByRole("button", { name: "Verify and continue" }));
    await flush();
    expect(auth.phoneVerify).toHaveBeenCalledWith("123456");
    expect(createSession).toHaveBeenCalledWith("tok-phone");
  });

  it("a provider failure is said in words, and a closed popup not at all", async () => {
    setAuthClient(fakeAuth({ google: vi.fn(async () => { throw { code: "auth/too-many-requests" }; }) }));
    render(<AuthCard />);
    fireEvent.click(screen.getByRole("button", { name: /Continue with Google/ }));
    await flush();
    expect(screen.getByRole("status").textContent).toMatch(/Too many attempts/);
    cleanup();
    setAuthClient(fakeAuth({ google: vi.fn(async () => { throw { code: "auth/popup-closed-by-user" }; }) }));
    render(<AuthCard />);
    fireEvent.click(screen.getByRole("button", { name: /Continue with Google/ }));
    await flush();
    expect(screen.getByRole("status").textContent).toBe("");
  });
});

describe("numbers and paths", () => {
  it("phone numbers become E.164", () => {
    expect(toE164("+91", "098123-45678")).toBe("+919812345678");
    expect(toE164("+1", "(415) 555 0100")).toBe("+14155550100");
    expect(toE164("+91", "12")).toBeNull();
  });

  it("next never leaves the site", () => {
    expect(safeNext("/search?q=x")).toBe("/search?q=x");
    for (const bad of ["//evil.example", "https://evil.example", "/\\evil", "", null]) {
      expect(safeNext(bad as string, "/")).toBe("/");
    }
  });
});

describe("the search box", () => {
  it("signed out, it keeps the query and opens sign-in instead of searching", () => {
    render(<SearchBox signedIn={false} />);
    fireEvent.change(screen.getByLabelText("Search companies"), { target: { value: "nvidia" } });
    // fireEvent returns false when the submit was prevented
    expect(fireEvent.submit(screen.getByRole("search"))).toBe(false);
    expect(screen.getByRole("dialog", { name: "Sign in to search" })).toBeTruthy();
    expect(screen.getByRole("heading").textContent).toBe("Sign in to research “nvidia”");
  });

  it("signed in, it just searches", () => {
    render(<SearchBox signedIn />);
    fireEvent.change(screen.getByLabelText("Search companies"), { target: { value: "nvidia" } });
    expect(fireEvent.submit(screen.getByRole("search"))).toBe(true);
    expect(screen.queryByRole("dialog")).toBeNull();
  });
});
