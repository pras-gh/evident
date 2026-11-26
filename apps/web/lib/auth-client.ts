/**
 * Sign-in in the browser. Firebase Authentication does the parts that must
 * not be ours — Google OAuth (with its state checks), passwords, email
 * verification and reset links, SMS codes and their expiry, resend limits and
 * abuse protection — and hands back an ID token. That token goes to the API
 * once (`createSession`), which verifies it and sets Parallx's own session.
 *
 *   NEXT_PUBLIC_FIREBASE_API_KEY / _AUTH_DOMAIN / _PROJECT_ID / _APP_ID
 *     — public by design: they name the project, they grant nothing
 *   NEXT_PUBLIC_AUTH_PROVIDER=dev
 *     — local development without a Firebase project; the API must run with
 *       AUTH_PROVIDER=dev too, which it refuses in production
 */
"use client";

export interface AuthClient {
  /** Google: a popup, then an ID token. */
  google(): Promise<string>;
  /** A new account: made, and a verification link sent. Not signed in yet. */
  emailSignUp(email: string, password: string): Promise<void>;
  /** An existing account: a token, or null when its email is not verified. */
  emailSignIn(email: string, password: string): Promise<string | null>;
  resendVerification(): Promise<void>;
  /** After clicking the link: a token once Firebase says it is verified. */
  checkVerified(): Promise<string | null>;
  resetPassword(email: string): Promise<void>;
  /** Send an SMS code to an E.164 number. */
  phoneStart(e164: string): Promise<void>;
  phoneVerify(code: string): Promise<string>;
  /** Drop the provider's own session once Parallx has one. */
  signOut(): Promise<void>;
}

/** A failure in words a person can act on. `code` is the provider's. */
export class AuthFailure extends Error {
  constructor(public code: string, message: string) {
    super(message);
  }
}

const MESSAGES: Record<string, string> = {
  "auth/email-already-in-use": "An account already uses this email. Sign in instead.",
  "auth/invalid-credential": "That email and password don't match an account.",
  "auth/wrong-password": "That email and password don't match an account.",
  "auth/user-not-found": "That email and password don't match an account.",
  "auth/invalid-email": "Enter a valid email address.",
  "auth/weak-password": "Use at least 8 characters for your password.",
  "auth/too-many-requests": "Too many attempts. Wait a few minutes, then try again.",
  "auth/invalid-phone-number": "Enter a valid phone number.",
  "auth/invalid-verification-code": "That code isn't right. Check it and try again.",
  "auth/code-expired": "That code has expired. Send a new one.",
  "auth/missing-verification-code": "Enter the 6-digit code.",
  "auth/quota-exceeded": "We can't send more codes right now. Try again later.",
  "auth/network-request-failed": "You seem to be offline. Check your connection.",
  "auth/popup-blocked": "Your browser blocked the Google window. Allow pop-ups and try again.",
  "auth/account-exists-with-different-credential":
    "This email already has an account. Sign in the way you did before.",
};

export function failure(e: unknown): AuthFailure {
  const code = (e as { code?: string })?.code ?? "unknown";
  if (e instanceof AuthFailure) return e;
  return new AuthFailure(code, MESSAGES[code] ?? "Something went wrong. Try again.");
}

export { safeNext } from "./next-path";

// ---------------------------------------------------------------- firebase

async function firebaseClient(): Promise<AuthClient> {
  const { initializeApp, getApps } = await import("firebase/app");
  const fb = await import("firebase/auth");
  const config = {
    apiKey: process.env.NEXT_PUBLIC_FIREBASE_API_KEY,
    authDomain: process.env.NEXT_PUBLIC_FIREBASE_AUTH_DOMAIN,
    projectId: process.env.NEXT_PUBLIC_FIREBASE_PROJECT_ID,
    appId: process.env.NEXT_PUBLIC_FIREBASE_APP_ID,
  };
  if (!config.apiKey || !config.projectId) {
    throw new AuthFailure("not-configured", "Sign-in isn't set up on this server yet.");
  }
  const app = getApps()[0] ?? initializeApp(config);
  const auth = fb.getAuth(app);
  let confirmation: import("firebase/auth").ConfirmationResult | null = null;
  let verifier: import("firebase/auth").RecaptchaVerifier | null = null;
  const verifyUrl = () => `${window.location.origin}/signin${window.location.search}`;

  return {
    async google() {
      const provider = new fb.GoogleAuthProvider();
      provider.setCustomParameters({ prompt: "select_account" });
      const r = await fb.signInWithPopup(auth, provider).catch((e) => { throw failure(e); });
      return r.user.getIdToken();
    },
    async emailSignUp(email, password) {
      const r = await fb.createUserWithEmailAndPassword(auth, email, password)
        .catch((e) => { throw failure(e); });
      await fb.sendEmailVerification(r.user, { url: verifyUrl() })
        .catch((e) => { throw failure(e); });
    },
    async emailSignIn(email, password) {
      const r = await fb.signInWithEmailAndPassword(auth, email, password)
        .catch((e) => { throw failure(e); });
      return r.user.emailVerified ? r.user.getIdToken() : null;
    },
    async resendVerification() {
      if (!auth.currentUser) throw new AuthFailure("no-user", "Sign in again to resend.");
      await fb.sendEmailVerification(auth.currentUser, { url: verifyUrl() })
        .catch((e) => { throw failure(e); });
    },
    async checkVerified() {
      if (!auth.currentUser) return null;
      await auth.currentUser.reload();
      // the token must say so too: a fresh one carries email_verified=true
      return auth.currentUser.emailVerified ? auth.currentUser.getIdToken(true) : null;
    },
    async resetPassword(email) {
      await fb.sendPasswordResetEmail(auth, email, { url: `${window.location.origin}/signin` })
        .catch((e) => { throw failure(e); });
    },
    async phoneStart(e164) {
      verifier ??= new fb.RecaptchaVerifier(auth, "recaptcha-slot", { size: "invisible" });
      confirmation = await fb.signInWithPhoneNumber(auth, e164, verifier)
        .catch((e) => { throw failure(e); });
    },
    async phoneVerify(code) {
      if (!confirmation) throw new AuthFailure("auth/code-expired", MESSAGES["auth/code-expired"]);
      const r = await confirmation.confirm(code).catch((e) => { throw failure(e); });
      return r.user.getIdToken();
    },
    async signOut() {
      await fb.signOut(auth).catch(() => undefined);
    },
  };
}

// --------------------------------------------------------------------- dev

/** Local development only: the API's AUTH_PROVIDER=dev accepts "dev:<who>".
 *  The phone code is always 123456. */
function devClient(): AuthClient {
  let pending: string | null = null;
  let phone: string | null = null;
  return {
    async google() {
      const who = window.prompt("Dev sign-in: which Google account?", "you@gmail.com");
      if (!who) throw new AuthFailure("auth/popup-closed-by-user", "");
      return `dev:${who}`;
    },
    async emailSignUp(email) { pending = email; },
    async emailSignIn(email) { return `dev:${email}`; },
    async resendVerification() {},
    async checkVerified() { return pending ? `dev:${pending}` : null; },
    async resetPassword() {},
    async phoneStart(e164) { phone = e164; },
    async phoneVerify(code) {
      if (code !== "123456") {
        throw new AuthFailure("auth/invalid-verification-code",
                              MESSAGES["auth/invalid-verification-code"]);
      }
      return `dev:${phone}`;
    },
    async signOut() {},
  };
}

let client: Promise<AuthClient> | null = null;

export function authClient(): Promise<AuthClient> {
  client ??= process.env.NEXT_PUBLIC_AUTH_PROVIDER === "dev"
    ? Promise.resolve(devClient()) : firebaseClient();
  return client;
}

/** Tests: an AuthClient of their own. */
export function setAuthClient(c: AuthClient | null) {
  client = c ? Promise.resolve(c) : null;
}
