import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";
import { webcrypto } from "node:crypto";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const FRONTEND_DIR = path.resolve(__dirname, "../frontend");
const SWAGGER_PATH = path.resolve(__dirname, "../../securecare-apigateway-v4.json");

// Minimal browser globals for importing ES modules cleanly under Node 22.
function installBrowserEnv(origin = "http://localhost:8080", search = "") {
  const sessionStore = new Map();
  globalThis.window = {
    location: {
      origin,
      pathname: "/portal.html",
      search,
      assignedUrl: null,
      replacedUrl: null,
      assign(url) {
        this.assignedUrl = url;
      },
      replace(url) {
        this.replacedUrl = url;
      }
    },
    history: {
      replaceState(_s, _t, nextPath) {
        globalThis.window.location.pathname = nextPath;
        globalThis.window.location.search = "";
      }
    },
    crypto: webcrypto,
    fetch: () => {
      throw new Error("Unexpected unmocked fetch call");
    }
  };
  globalThis.document = { title: "SecureCare" };
  globalThis.sessionStorage = {
    getItem: (k) => (sessionStore.has(k) ? sessionStore.get(k) : null),
    setItem: (k, v) => sessionStore.set(k, String(v)),
    removeItem: (k) => sessionStore.delete(k)
  };
  return sessionStore;
}

function makeFakeJwt(payload) {
  const b64 = (obj) =>
    Buffer.from(JSON.stringify(obj))
      .toString("base64")
      .replace(/\+/g, "-")
      .replace(/\//g, "_")
      .replace(/=+$/, "");
  return `${b64({ alg: "RS256", typ: "JWT" })}.${b64(payload)}.fakesig`;
}

installBrowserEnv();
const { CONFIG } = await import("../frontend/js/config.js");
const auth = await import("../frontend/js/auth.js");
const apiMod = await import("../frontend/js/api.js");

test("static security & asset integrity across all frontend files", () => {
  const htmlFiles = fs.readdirSync(FRONTEND_DIR).filter((f) => f.endsWith(".html"));
  assert.ok(htmlFiles.includes("index.html"));
  assert.ok(htmlFiles.includes("portal.html"));
  assert.ok(htmlFiles.includes("about.html"));
  assert.ok(htmlFiles.includes("privacy.html"));
  assert.ok(htmlFiles.includes("security.html"));
  assert.ok(htmlFiles.includes("terms.html"));
  assert.ok(htmlFiles.includes("contact.html"));

  for (const file of htmlFiles) {
    const html = fs.readFileSync(path.join(FRONTEND_DIR, file), "utf8");
    // No inline <script> without src attribute
    const inlineScripts = [...html.matchAll(/<script\b([^>]*)>/gi)].filter((m) => !/\bsrc\s*=/.test(m[1]));
    assert.equal(inlineScripts.length, 0, `${file} must not contain inline scripts`);
    // No inline event handlers like onclick=
    assert.equal(/\son[a-z]+\s*=/i.test(html), false, `${file} must not contain inline event handlers`);

    // Verify all local href/src targets exist
    for (const m of html.matchAll(/\b(?:href|src)="([^"]+)"/g)) {
      const target = m[1];
      if (target.startsWith("http") || target.startsWith("mailto:") || target.startsWith("#")) continue;
      const resolved = path.resolve(FRONTEND_DIR, target);
      assert.equal(fs.existsSync(resolved), true, `${file} references missing file: ${target}`);
    }
  }

  // Verify JS files have no innerHTML, document.write, or localStorage
  const jsFiles = [];
  function walkJs(dir) {
    for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
      const full = path.join(dir, entry.name);
      if (entry.isDirectory()) walkJs(full);
      else if (entry.name.endsWith(".js")) jsFiles.push(full);
    }
  }
  walkJs(path.join(FRONTEND_DIR, "js"));
  assert.ok(jsFiles.length >= 10);

  for (const jf of jsFiles) {
    const src = fs.readFileSync(jf, "utf8");
    assert.equal(src.includes(".innerHTML"), false, `${jf} must not use innerHTML`);
    assert.equal(src.includes("insertAdjacentHTML"), false, `${jf} must not use insertAdjacentHTML`);
    assert.equal(src.includes("localStorage"), false, `${jf} must not use localStorage`);
  }
});

test("Cognito PKCE config and URL generation match registered AWS client", async () => {
  const store = installBrowserEnv("http://localhost:8080");
  assert.equal(CONFIG.clientId, "265sf66rn74fpel2s798v26nd4");
  assert.equal(CONFIG.cognitoDomain, "https://eu-west-1s51fcyw5c.auth.eu-west-1.amazoncognito.com");
  assert.equal(CONFIG.scopes, "email openid phone");

  const loginUrl = new URL(await auth.buildAuthUrl("/login"));
  assert.equal(loginUrl.origin, "https://eu-west-1s51fcyw5c.auth.eu-west-1.amazoncognito.com");
  assert.equal(loginUrl.pathname, "/login");
  assert.equal(loginUrl.searchParams.get("client_id"), "265sf66rn74fpel2s798v26nd4");
  assert.equal(loginUrl.searchParams.get("response_type"), "code");
  assert.equal(loginUrl.searchParams.get("scope"), "email openid phone");
  assert.equal(loginUrl.searchParams.get("redirect_uri"), "http://localhost:8080/portal.html");
  assert.equal(loginUrl.searchParams.get("code_challenge_method"), "S256");
  assert.ok(loginUrl.searchParams.get("code_challenge"));
  assert.ok(store.get("sc_pkce_verifier"));
  assert.ok(store.get("sc_pkce_state"));

  const signupUrl = new URL(await auth.buildAuthUrl("/signup"));
  assert.equal(signupUrl.pathname, "/signup");
});

test("PKCE callback exchanges code, keeps token strictly in memory, and clears sessionStorage", async () => {
  const store = installBrowserEnv("https://outship.me");
  const authUrl = new URL(await auth.buildAuthUrl("/login"));
  const state = authUrl.searchParams.get("state");
  globalThis.window.location.search = `?code=auth-code-123&state=${encodeURIComponent(state)}`;

  const futureExp = Math.floor(Date.now() / 1000) + 3600;
  const fakeIdToken = makeFakeJwt({
    sub: "pat-sub-001",
    email: "patient@outship.me",
    "cognito:groups": ["patient"],
    exp: futureExp
  });

  const mockFetch = async (url, init) => {
    assert.equal(url, "https://eu-west-1s51fcyw5c.auth.eu-west-1.amazoncognito.com/oauth2/token");
    assert.equal(init.method, "POST");
    return {
      ok: true,
      json: async () => ({ id_token: fakeIdToken })
    };
  };

  const res = await auth.handleCallbackIfPresent(mockFetch);
  assert.equal(res.handled, true);
  assert.equal(res.session.role, "patient");
  assert.equal(auth.getIdToken(), fakeIdToken);
  assert.equal(store.has("sc_pkce_verifier"), false);
  assert.equal(store.has("sc_pkce_state"), false);
  auth.clearMemorySession();
  assert.equal(auth.getIdToken(), null);
});

test("API client covers every non-OPTIONS endpoint in securecare-apigateway-v4.json", async () => {
  const swagger = JSON.parse(fs.readFileSync(SWAGGER_PATH, "utf8"));
  const expectedOps = new Set();
  for (const [p, methods] of Object.entries(swagger.paths)) {
    for (const m of Object.keys(methods)) {
      if (m !== "options") expectedOps.add(`${m.toUpperCase()} ${p}`);
    }
  }
  assert.equal(expectedOps.size, 22);

  const calledOps = [];
  const token = "raw.jwt.token";
  const mockFetch = async (url, init) => {
    assert.equal(init.headers.Authorization, token);
    const u = new URL(url, "https://outship.me");
    const normPath = u.pathname
      .replace("/api/patients/pat-1", "/api/patients/{patient_id}")
      .replace("/api/assignments/pat-1", "/api/assignments/{patient_id}")
      .replace("/api/appointments/apt-1", "/api/appointments/{appointment_id}")
      .replace("/api/visit-notes/pat-1/note-1", "/api/visit-notes/{patient_id}/{note_id}")
      .replace("/api/visit-notes/pat-1", "/api/visit-notes/{patient_id}")
      .replace("/api/files/file-1/download-url", "/api/files/{fileId}/download-url")
      .replace("/api/requests/req-1", "/api/requests/{request_id}");
    calledOps.push(`${init.method} ${normPath}`);
    return { ok: true, status: 200, text: async () => "{}" };
  };

  const api = apiMod.createApiClient({
    baseUrl: "",
    getToken: () => token,
    fetchImpl: mockFetch
  });

  await api.getMyProfile();
  await api.updateMyProfile({ fullName: "Hana Abdelrahman" });
  await api.getPatient("pat-1");
  await api.listAssignments({ patient_id: "pat-1" });
  await api.createAssignment({ clinician_id: "doc-1", patient_id: "pat-1" });
  await api.deleteAssignment("pat-1", "doc-1");
  await api.listAppointments({ status: "BOOKED", day: "2026-11-28" });
  await api.createAppointment({ clinician_id: "doc-1", startsAt: "2026-11-28T10:30:00Z", visitType: "consultation" });
  await api.getAppointment("apt-1");
  await api.updateAppointment("apt-1", { status: "COMPLETED" });
  await api.cancelAppointment("apt-1");
  await api.listVisitNotes({ patient_id: "pat-1" });
  await api.createVisitNote("pat-1", { title: "Follow-up", note: "Stable vitals." });
  await api.getVisitNote("pat-1", "note-1");
  await api.listFiles({ patient_id: "pat-1" });
  await api.requestUploadUrl({ patient_id: "pat-1", fileName: "lab.pdf", contentType: "application/pdf", sizeBytes: 1024 });
  await api.getDownloadUrl("file-1");
  await api.exportData("json");
  await api.submitEraseRequest({ confirm: true, reason: "Account closure requested." });
  await api.getRequestStatus("req-1");
  await api.searchAudit({ day: "2026-11-26" });
  await api.listClinicians();

  for (const op of expectedOps) {
    assert.ok(calledOps.includes(op), `Missing API client operation: ${op}`);
  }
});

test("Schema validators reject out-of-spec payloads and normalize API errors", async () => {
  assert.throws(() => apiMod.validateRequestUpload({ patient_id: "p1", fileName: "a.exe", contentType: "application/octet-stream", sizeBytes: 100 }));
  assert.throws(() => apiMod.validateRequestUpload({ patient_id: "p1", fileName: "big.pdf", contentType: "application/pdf", sizeBytes: 10485761 }));
  assert.throws(() => apiMod.validateEraseRequest({ confirm: false, reason: "valid reason" }));
  assert.throws(() => apiMod.validateEraseRequest({ confirm: true, reason: "no" }));
  assert.throws(() => apiMod.validateCreateVisitNote({ title: "Ok", note: "" }));
  assert.throws(() => apiMod.validateUpdateProfile({}));

  let unauthorizedTriggered = false;
  const api = apiMod.createApiClient({
    baseUrl: "",
    getToken: () => "tok",
    onUnauthorized: () => { unauthorizedTriggered = true; },
    fetchImpl: async () => ({
      ok: false,
      status: 401,
      text: async () => JSON.stringify({ error: { code: "UNAUTHORIZED", message: "Expired" } })
    })
  });
  await assert.rejects(() => api.getMyProfile(), (err) => err.status === 401);
  assert.equal(unauthorizedTriggered, true);

  const static404Api = apiMod.createApiClient({
    baseUrl: "",
    getToken: () => "tok",
    fetchImpl: async () => ({
      ok: false,
      status: 404,
      text: async () => "<html><body>404 File not found</body></html>"
    })
  });
  await assert.rejects(
    () => static404Api.listAppointments(),
    (err) => err.status === 404 && err.code === "api_unreachable"
  );
});
