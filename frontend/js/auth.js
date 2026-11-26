/**
 * Cognito Hosted UI Authorization Code + PKCE Authentication.
 * Tokens are stored exclusively in module closure memory—never in browser storage.
 */
import { CONFIG } from "./config.js";

const PKCE_VERIFIER_KEY = "sc_pkce_verifier";
const PKCE_STATE_KEY = "sc_pkce_state";
const ROLE_PRIORITY = ["admin", "doctor", "staff", "patient"];

/** @type {{ idToken: string, claims: Record<string, any>, role: string, groups: string[], expiresAtMs: number } | null} */
let activeSession = null;

function base64UrlEncode(bytes) {
  let binary = "";
  for (let i = 0; i < bytes.length; i += 1) {
    binary += String.fromCharCode(bytes[i]);
  }
  return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

function randomUrlSafeString(byteLength = 48) {
  const bytes = new Uint8Array(byteLength);
  window.crypto.getRandomValues(bytes);
  return base64UrlEncode(bytes);
}

async function sha256Challenge(verifier) {
  const data = new TextEncoder().encode(verifier);
  const digest = await window.crypto.subtle.digest("SHA-256", data);
  return base64UrlEncode(new Uint8Array(digest));
}

export function decodeJwtPayload(token) {
  if (typeof token !== "string") {
    throw new Error("Invalid token format.");
  }
  const parts = token.split(".");
  if (parts.length !== 3) {
    throw new Error("Malformed JWT payload.");
  }
  const base64 = parts[1].replace(/-/g, "+").replace(/_/g, "/");
  const padded = base64 + "=".repeat((4 - (base64.length % 4)) % 4);
  const binary = atob(padded);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i += 1) {
    bytes[i] = binary.charCodeAt(i);
  }
  const json = new TextDecoder().decode(bytes);
  return JSON.parse(json);
}

export function resolveRoleFromClaims(claims) {
  const rawGroups = Array.isArray(claims?.["cognito:groups"]) ? claims["cognito:groups"] : [];
  const normalized = rawGroups.map((g) => String(g).trim().toLowerCase()).filter(Boolean);
  for (const candidate of ROLE_PRIORITY) {
    if (normalized.includes(candidate)) {
      return { role: candidate, groups: normalized };
    }
  }
  return { role: "unassigned", groups: normalized };
}

export async function buildAuthUrl(endpointPath = "/login", overrides = {}) {
  const verifier = randomUrlSafeString(48);
  const state = randomUrlSafeString(24);
  const challenge = await sha256Challenge(verifier);

  sessionStorage.setItem(PKCE_VERIFIER_KEY, verifier);
  sessionStorage.setItem(PKCE_STATE_KEY, state);

  const cfg = { ...CONFIG, ...overrides };
  const params = new URLSearchParams({
    client_id: cfg.clientId,
    response_type: "code",
    scope: cfg.scopes,
    redirect_uri: overrides.redirectUri || CONFIG.redirectUri,
    code_challenge_method: "S256",
    code_challenge: challenge,
    state
  });

  return `${cfg.cognitoDomain}${endpointPath}?${params.toString()}`;
}

export async function startLogin() {
  const url = await buildAuthUrl("/login");
  window.location.assign(url);
}

export async function startSignup() {
  const url = await buildAuthUrl("/signup");
  window.location.assign(url);
}

export function clearMemorySession() {
  activeSession = null;
}

export function signOut() {
  clearMemorySession();
  sessionStorage.removeItem(PKCE_VERIFIER_KEY);
  sessionStorage.removeItem(PKCE_STATE_KEY);

  const params = new URLSearchParams({
    client_id: CONFIG.clientId,
    logout_uri: CONFIG.logoutUri
  });
  window.location.assign(`${CONFIG.cognitoDomain}/logout?${params.toString()}`);
}

export function setSessionFromToken(idToken) {
  const claims = decodeJwtPayload(idToken);
  const expSeconds = Number(claims.exp || 0);
  const expiresAtMs = expSeconds * 1000;
  if (!expSeconds || Date.now() >= expiresAtMs) {
    clearMemorySession();
    throw new Error("Your session token has expired.");
  }
  const { role, groups } = resolveRoleFromClaims(claims);
  activeSession = {
    idToken,
    claims,
    role,
    groups,
    expiresAtMs
  };
  return activeSession;
}

export function getSession() {
  if (!activeSession) return null;
  if (Date.now() >= activeSession.expiresAtMs) {
    activeSession = null;
    return null;
  }
  return activeSession;
}

export function getIdToken() {
  const session = getSession();
  return session ? session.idToken : null;
}

export function getRemainingSeconds() {
  const session = getSession();
  if (!session) return 0;
  return Math.max(0, Math.floor((session.expiresAtMs - Date.now()) / 1000));
}

export function isExpiringSoon() {
  const remaining = getRemainingSeconds();
  return remaining > 0 && remaining <= CONFIG.sessionWarningSeconds;
}

export async function handleCallbackIfPresent(fetchImpl = window.fetch.bind(window)) {
  const params = new URLSearchParams(window.location.search);
  const authError = params.get("error");
  if (authError) {
    const desc = params.get("error_description") || "Authentication could not be completed.";
    window.history.replaceState({}, document.title, window.location.pathname);
    throw new Error(desc);
  }

  const code = params.get("code");
  const returnedState = params.get("state");
  if (!code) {
    return { handled: false, session: getSession() };
  }

  const expectedState = sessionStorage.getItem(PKCE_STATE_KEY);
  const verifier = sessionStorage.getItem(PKCE_VERIFIER_KEY);
  sessionStorage.removeItem(PKCE_STATE_KEY);
  sessionStorage.removeItem(PKCE_VERIFIER_KEY);
  window.history.replaceState({}, document.title, window.location.pathname);

  if (!expectedState || !verifier || returnedState !== expectedState) {
    throw new Error("Sign-in verification failed. Please try signing in again.");
  }

  const body = new URLSearchParams({
    grant_type: "authorization_code",
    client_id: CONFIG.clientId,
    code,
    redirect_uri: CONFIG.redirectUri,
    code_verifier: verifier
  });

  const resp = await fetchImpl(`${CONFIG.cognitoDomain}/oauth2/token`, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: body.toString()
  });

  if (!resp.ok) {
    throw new Error("Unable to complete sign-in with the identity provider.");
  }

  const tokenData = await resp.json();
  if (!tokenData?.id_token) {
    throw new Error("Identity token was not returned.");
  }

  const session = setSessionFromToken(tokenData.id_token);
  return { handled: true, session };
}
