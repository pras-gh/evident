import type { Metadata } from "next";

export const metadata: Metadata = { title: "Admin sign in — Evident" };

const ERRORS: Record<string, string> = {
  missing: "Enter an API key.",
  invalid: "That key is not valid, or it has been revoked.",
  forbidden: "That key does not belong to a platform admin.",
  expired: "Your session ended. Sign in again.",
};

export default async function AdminLogin({
  searchParams,
}: {
  searchParams: Promise<{ error?: string }>;
}) {
  const { error } = await searchParams;
  return (
    <main className="adm-login">
      <form method="post" action="/admin/session" className="adm-login-card">
        <p className="ev-kicker">Evident · Internal</p>
        <h1 className="ev-title">AI usage</h1>
        <label htmlFor="key" className="adm-label">Platform admin API key</label>
        <input id="key" name="key" type="password" autoComplete="off" spellCheck={false}
          placeholder="evk_…" className="adm-input" required />
        {error && ERRORS[error] && (
          <p className="adm-error" role="alert">{ERRORS[error]}</p>
        )}
        <button type="submit" className="adm-btn">Sign in</button>
        <p className="adm-hint">
          Create one with <code>python tools/admin.py bootstrap-admin you@example.com</code>.
          The key is kept in an httpOnly cookie for this browser only.
        </p>
      </form>
    </main>
  );
}
