"use client";

import { useEffect, useRef, useState } from "react";
import { ParallxMark } from "@/components/brand/ParallxMark";
import { authClient, failure, safeNext } from "@/lib/auth-client";
import { createSession, RequestFailed } from "@/lib/client-api";

export const COUNTRIES = [
  { code: "IN", dial: "+91", name: "India" },
  { code: "US", dial: "+1", name: "United States" },
  { code: "GB", dial: "+44", name: "United Kingdom" },
  { code: "CA", dial: "+1", name: "Canada" },
  { code: "SG", dial: "+65", name: "Singapore" },
  { code: "AE", dial: "+971", name: "United Arab Emirates" },
  { code: "AU", dial: "+61", name: "Australia" },
  { code: "DE", dial: "+49", name: "Germany" },
  { code: "FR", dial: "+33", name: "France" },
  { code: "JP", dial: "+81", name: "Japan" },
  { code: "HK", dial: "+852", name: "Hong Kong" },
];

const EMAIL = /^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/;
const RESEND_EMAIL_S = 60;
const RESEND_CODE_S = 30;

/** "+91" and "098123 45678" → "+919812345678"; null when it can't be a number. */
export function toE164(dial: string, local: string): string | null {
  const digits = local.replace(/\D/g, "").replace(/^0+/, "");
  const out = `${dial}${digits}`;
  return /^\+[1-9]\d{6,14}$/.test(out) && digits.length >= 6 ? out : null;
}

/** A countdown in seconds; `start(n)` begins one. */
function useCooldown(): [number, (s: number) => void] {
  const [left, setLeft] = useState(0);
  useEffect(() => {
    if (left <= 0) return;
    const t = setTimeout(() => setLeft((n) => n - 1), 1000);
    return () => clearTimeout(t);
  }, [left]);
  return [left, setLeft];
}

type EmailView = "signin" | "signup" | "verify" | "reset" | "reset-sent";

/**
 * Sign in or create an account: Google, or email (with a password and a
 * verification link), or a phone number (an SMS code). On success the page
 * goes where the reader was headed — a search they typed before signing in
 * continues by itself — through onboarding the first time.
 */
export function AuthCard({ next, query, startWith = "signin" }: {
  /** where to go after: a path on this site */
  next?: string | null;
  /** the search they typed, for the heading */
  query?: string | null;
  startWith?: "signin" | "signup";
}) {
  const [method, setMethod] = useState<"email" | "phone">("email");
  const [view, setView] = useState<EmailView>(startWith);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [country, setCountry] = useState("IN");
  const [phone, setPhone] = useState("");
  const [code, setCode] = useState("");
  const [codeSent, setCodeSent] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [emailWait, startEmailWait] = useCooldown();
  const [codeWait, startCodeWait] = useCooldown();
  const codeRef = useRef<HTMLInputElement>(null);

  const dial = COUNTRIES.find((c) => c.code === country)?.dial ?? "+91";
  const creating = view === "signup";

  async function run(label: string, fn: () => Promise<void>) {
    setBusy(label);
    setError(null);
    setNotice(null);
    try {
      await fn();
    } catch (e) {
      if (e instanceof RequestFailed) {
        if (e.error === "email_unverified") setView("verify");
        setError(e.message);
      } else {
        const f = failure(e);
        if (f.code !== "auth/popup-closed-by-user" && f.code !== "auth/cancelled-popup-request") {
          setError(f.message);
        }
        if (f.code === "auth/email-already-in-use") setView("signin");
      }
    } finally {
      setBusy(null);
    }
  }

  /** A verified token → a Parallx session → onward. */
  async function finish(token: string) {
    const { is_new } = await createSession(token);
    await (await authClient()).signOut();
    const dest = safeNext(next, "/");
    window.location.assign(is_new ? `/welcome?next=${encodeURIComponent(dest)}` : dest);
  }

  const google = () => run("google", async () => finish(await (await authClient()).google()));

  function submitEmail(e: React.FormEvent) {
    e.preventDefault();
    if (!EMAIL.test(email.trim())) return setError("Enter a valid email address.");
    if (view === "reset") {
      return run("reset", async () => {
        await (await authClient()).resetPassword(email.trim());
        setView("reset-sent");
      });
    }
    if (password.length < 8) return setError("Use at least 8 characters for your password.");
    if (creating) {
      return run("email", async () => {
        await (await authClient()).emailSignUp(email.trim(), password);
        setView("verify");
        startEmailWait(RESEND_EMAIL_S);
      });
    }
    run("email", async () => {
      const token = await (await authClient()).emailSignIn(email.trim(), password);
      if (token) return finish(token);
      setView("verify");
    });
  }

  const resendEmail = () => run("resend", async () => {
    await (await authClient()).resendVerification();
    startEmailWait(RESEND_EMAIL_S);
    setNotice("We sent another link.");
  });

  const verified = () => run("verified", async () => {
    const token = await (await authClient()).checkVerified();
    if (!token) {
      setError("Not verified yet. Open the link in the email, then try again.");
      return;
    }
    await finish(token);
  });

  function sendCode(e?: React.FormEvent) {
    e?.preventDefault();
    const e164 = toE164(dial, phone);
    if (!e164) return setError("Enter a valid phone number.");
    run("code", async () => {
      await (await authClient()).phoneStart(e164);
      setCodeSent(true);
      startCodeWait(RESEND_CODE_S);
      setNotice(`We sent a 6-digit code to ${e164}.`);
      setTimeout(() => codeRef.current?.focus(), 0);
    });
  }

  function verifyCode(e: React.FormEvent) {
    e.preventDefault();
    if (!/^\d{6}$/.test(code)) return setError("Enter the 6-digit code.");
    run("verify", async () => finish(await (await authClient()).phoneVerify(code)));
  }

  const heading = query
    ? `Sign in to research “${query}”`
    : creating ? "Create your Parallx account" : "Sign in to Parallx";

  return (
    <div className="auth-card">
      <div className="auth-head">
        <span className="auth-mark" aria-hidden><ParallxMark size={22} /></span>
        <h1 className="auth-title">{heading}</h1>
        <p className="auth-sub">
          {query ? "Your search continues as soon as you're in. "
                 : "Evidence-backed research on public companies. "}
          Two companies free, no card needed.
        </p>
      </div>

      <button type="button" className="auth-google" onClick={google} disabled={!!busy}>
        <svg viewBox="0 0 18 18" width="18" height="18" aria-hidden>
          <path fill="#4285F4" d="M17.64 9.2c0-.64-.06-1.25-.16-1.84H9v3.48h4.84a4.14 4.14 0 0 1-1.8 2.72v2.26h2.92c1.7-1.57 2.68-3.88 2.68-6.62z"/>
          <path fill="#34A853" d="M9 18c2.43 0 4.47-.8 5.96-2.18l-2.92-2.26c-.8.54-1.84.86-3.04.86-2.34 0-4.32-1.58-5.03-3.7H.96v2.33A9 9 0 0 0 9 18z"/>
          <path fill="#FBBC05" d="M3.97 10.72A5.4 5.4 0 0 1 3.68 9c0-.6.1-1.18.29-1.72V4.95H.96A9 9 0 0 0 0 9c0 1.45.35 2.83.96 4.05l3.01-2.33z"/>
          <path fill="#EA4335" d="M9 3.58c1.32 0 2.5.45 3.44 1.35l2.58-2.58A9 9 0 0 0 .96 4.95l3.01 2.33C4.68 5.16 6.66 3.58 9 3.58z"/>
        </svg>
        {busy === "google" ? "Opening Google…" : "Continue with Google"}
      </button>

      <div className="auth-or" role="separator"><span>or</span></div>

      <div className="auth-switch" role="tablist" aria-label="Sign in with">
        {(["email", "phone"] as const).map((m) => (
          <button key={m} type="button" role="tab" aria-selected={method === m}
                  className={method === m ? "is-on" : undefined}
                  onClick={() => { setMethod(m); setError(null); setNotice(null); }}>
            {m === "email" ? "Email" : "Phone number"}
          </button>
        ))}
      </div>

      {method === "email" && (view === "verify" ? (
        <div className="auth-step">
          <p className="auth-step-title">Check your inbox</p>
          <p className="auth-step-body">
            We sent a verification link to <b>{email || "your email"}</b>. Open it, then come
            back here.
          </p>
          <button type="button" className="btn-primary auth-submit" onClick={verified}
                  disabled={!!busy}>
            {busy === "verified" ? "Checking…" : "I've verified my email"}
          </button>
          <button type="button" className="auth-link" onClick={resendEmail}
                  disabled={!!busy || emailWait > 0}>
            {emailWait > 0 ? `Resend the link in ${emailWait}s` : "Resend the link"}
          </button>
        </div>
      ) : view === "reset-sent" ? (
        <div className="auth-step">
          <p className="auth-step-title">Check your inbox</p>
          <p className="auth-step-body">
            If an account uses <b>{email}</b>, a link to reset its password is on its way.
          </p>
          <button type="button" className="auth-link" onClick={() => setView("signin")}>
            Back to sign in
          </button>
        </div>
      ) : (
        <form className="auth-form" onSubmit={submitEmail} noValidate>
          <label className="auth-field">
            <span>Email</span>
            <input type="email" autoComplete="email" inputMode="email" value={email}
                   onChange={(e) => setEmail(e.target.value)} placeholder="you@example.com"
                   required aria-invalid={!!error && !EMAIL.test(email.trim())} />
          </label>
          {view !== "reset" && (
            <label className="auth-field">
              <span>
                Password
                {view === "signin" && (
                  <button type="button" className="auth-inline" onClick={() => {
                    setView("reset"); setError(null); }}>
                    Forgot password?
                  </button>
                )}
              </span>
              <span className="auth-pass">
                <input type={showPassword ? "text" : "password"} value={password}
                       autoComplete={creating ? "new-password" : "current-password"}
                       onChange={(e) => setPassword(e.target.value)} required minLength={8}
                       placeholder={creating ? "At least 8 characters" : ""} />
                <button type="button" className="auth-eye" onClick={() => setShowPassword((v) => !v)}
                        aria-label={showPassword ? "Hide password" : "Show password"}>
                  {showPassword ? "Hide" : "Show"}
                </button>
              </span>
            </label>
          )}
          <button type="submit" className="btn-primary auth-submit" disabled={!!busy}>
            {busy ? "One moment…" : view === "reset" ? "Send reset link"
              : creating ? "Create account" : "Sign in"}
          </button>
          {view === "reset" ? (
            <button type="button" className="auth-link" onClick={() => setView("signin")}>
              Back to sign in
            </button>
          ) : (
            <p className="auth-alt">
              {creating ? "Have an account? " : "New to Parallx? "}
              <button type="button" className="auth-link" onClick={() => {
                setView(creating ? "signin" : "signup"); setError(null); }}>
                {creating ? "Sign in" : "Create an account"}
              </button>
            </p>
          )}
        </form>
      ))}

      {method === "phone" && (
        <form className="auth-form" onSubmit={codeSent ? verifyCode : sendCode} noValidate>
          <div className="auth-field">
            <span id="phone-label">Phone number</span>
            <div className="auth-phone">
              <select aria-label="Country code" value={country} disabled={codeSent}
                      onChange={(e) => setCountry(e.target.value)}>
                {COUNTRIES.map((c) => (
                  <option key={c.code} value={c.code}>{c.code} {c.dial}</option>
                ))}
              </select>
              <input type="tel" inputMode="tel" autoComplete="tel-national" value={phone}
                     aria-labelledby="phone-label" disabled={codeSent}
                     onChange={(e) => setPhone(e.target.value)} placeholder="98123 45678" />
            </div>
          </div>
          {codeSent && (
            <label className="auth-field">
              <span>Verification code</span>
              <input ref={codeRef} className="auth-code" inputMode="numeric"
                     autoComplete="one-time-code" maxLength={6} value={code}
                     onChange={(e) => setCode(e.target.value.replace(/\D/g, ""))}
                     placeholder="••••••" />
            </label>
          )}
          <button type="submit" className="btn-primary auth-submit" disabled={!!busy}>
            {busy === "code" ? "Sending…" : busy === "verify" ? "Verifying…"
              : codeSent ? "Verify and continue" : "Send code"}
          </button>
          {codeSent && (
            <div className="auth-row">
              <button type="button" className="auth-link" onClick={() => sendCode()}
                      disabled={!!busy || codeWait > 0}>
                {codeWait > 0 ? `Resend code in ${codeWait}s` : "Resend code"}
              </button>
              <button type="button" className="auth-link" onClick={() => {
                setCodeSent(false); setCode(""); setNotice(null); }}>
                Change number
              </button>
            </div>
          )}
          <div id="recaptcha-slot" />
        </form>
      )}

      <p className="auth-msg" role="status" aria-live="polite">
        {error ? <span className="auth-error">{error}</span> : notice}
      </p>

      <p className="auth-legal">
        By continuing you agree to the <a href="/terms">Terms</a> and{" "}
        <a href="/privacy">Privacy Policy</a>.
      </p>
    </div>
  );
}
