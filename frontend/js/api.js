/**
 * SecureCare API Gateway v4 Client + Strict Request Schema Validators.
 * Implements all 17 paths (22 business methods) from securecare-apigateway-v4.json.
 */
import { CONFIG } from "./config.js";
import { getIdToken } from "./auth.js";

export const ALLOWED_VISIT_TYPES = ["consultation", "follow_up", "lab_visit"];
export const ALLOWED_APPOINTMENT_STATUSES = ["BOOKED", "CANCELLED", "COMPLETED"];
export const ALLOWED_UPLOAD_TYPES = ["application/pdf", "image/png", "image/jpeg"];
export const MAX_UPLOAD_BYTES = 10485760; // 10 MB

export class ApiError extends Error {
  constructor(status, message, code = "api_error", requestId = null) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.requestId = requestId;
  }
}

export function buildQueryString(params = {}) {
  const qs = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== null && String(v).trim() !== "") {
      qs.set(k, String(v).trim());
    }
  }
  const str = qs.toString();
  return str ? `?${str}` : "";
}

function unwrapEnvelope(data) {
  if (
    data &&
    typeof data === "object" &&
    !Array.isArray(data) &&
    typeof data.success === "boolean" &&
    data.data !== undefined &&
    data.data !== null &&
    typeof data.data === "object"
  ) {
    return data.data;
  }
  return data;
}

export function extractList(data, candidateKeys = []) {
  const unwrapped = unwrapEnvelope(data);
  if (Array.isArray(unwrapped)) return unwrapped;
  if (!unwrapped || typeof unwrapped !== "object") return [];
  for (const key of [...candidateKeys, "items", "records", "data", "results"]) {
    if (Array.isArray(unwrapped[key])) return unwrapped[key];
  }
  return [];
}

export function extractNextToken(data) {
  const unwrapped = unwrapEnvelope(data);
  if (!unwrapped || typeof unwrapped !== "object") return null;
  return unwrapped.nextToken || unwrapped.next_token || null;
}

function humanizeError(status, payload, isJson = true) {
  const errObj = payload?.error && typeof payload.error === "object" ? payload.error : null;
  const serverMsg = errObj?.message || (typeof payload?.error === "string" ? payload.error : null) || payload?.message || payload?.detail;
  const requestId = errObj?.requestId || errObj?.request_id || payload?.requestId || payload?.request_id || null;
  const code = errObj?.code || (typeof payload?.error === "string" ? payload.error : `http_${status}`);

  if (status === 401) return new ApiError(status, "Your session has expired. Please sign in again.", code, requestId);
  if (status === 403) return new ApiError(status, "You do not have permission to perform this action.", code, requestId);
  if (status === 404) {
    if (!isJson) {
      return new ApiError(
        status,
        "Clinical API service is not connected on this static host (live /api/* routing runs on outship.me).",
        "api_unreachable",
        requestId
      );
    }
    return new ApiError(status, "The requested record was not found.", code, requestId);
  }
  if (status === 409) return new ApiError(status, serverMsg || "This resource cannot be modified or downloaded in its current state.", code, requestId);
  if (status === 429) return new ApiError(status, "Too many requests right now. Please wait a moment and try again.", code, requestId);
  if (status >= 500) return new ApiError(status, "The service is temporarily unavailable. Please try again shortly.", code, requestId);
  return new ApiError(status, serverMsg || "Please verify your input and try again.", code, requestId);
}

function assertId(value, label) {
  const s = String(value || "").trim();
  if (s.length < 1 || s.length > 128) {
    throw new Error(`${label} must be between 1 and 128 characters.`);
  }
  return s;
}

export function validateCreateAppointment(payload, { requirePatientId = false } = {}) {
  const out = {
    clinician_id: assertId(payload?.clinician_id, "Clinician"),
    startsAt: String(payload?.startsAt || "").trim(),
    visitType: String(payload?.visitType || "").trim()
  };
  if (out.startsAt.length < 20 || out.startsAt.length > 35) {
    throw new Error("Appointment start time must be a valid ISO timestamp.");
  }
  if (!ALLOWED_VISIT_TYPES.includes(out.visitType)) {
    throw new Error("Please select a valid visit type.");
  }
  const pid = payload?.patient_id ? String(payload.patient_id).trim() : "";
  if (requirePatientId && !pid) {
    throw new Error("Patient ID is required.");
  }
  if (pid) {
    out.patient_id = assertId(pid, "Patient ID");
  }
  return out;
}

export function validateUpdateAppointment(payload) {
  const out = {};
  if (payload?.startsAt !== undefined && String(payload.startsAt).trim() !== "") {
    const s = String(payload.startsAt).trim();
    if (s.length < 20 || s.length > 35) throw new Error("Appointment start time must be a valid ISO timestamp.");
    out.startsAt = s;
  }
  if (payload?.visitType !== undefined && String(payload.visitType).trim() !== "") {
    if (!ALLOWED_VISIT_TYPES.includes(payload.visitType)) throw new Error("Invalid visit type.");
    out.visitType = payload.visitType;
  }
  if (payload?.status !== undefined && String(payload.status).trim() !== "") {
    if (!ALLOWED_APPOINTMENT_STATUSES.includes(payload.status)) throw new Error("Invalid appointment status.");
    out.status = payload.status;
  }
  if (Object.keys(out).length < 1) {
    throw new Error("Provide at least one change to update the appointment.");
  }
  return out;
}

export function validateCreateVisitNote(payload) {
  const title = String(payload?.title || "").trim();
  const note = String(payload?.note || "").trim();
  if (title.length < 1 || title.length > 120) {
    throw new Error("Title must be between 1 and 120 characters.");
  }
  if (note.length < 1 || note.length > 5000) {
    throw new Error("Clinical note must be between 1 and 5,000 characters.");
  }
  return { title, note };
}

export function validateUpdateProfile(payload) {
  const out = {};
  if (payload?.fullName !== undefined && String(payload.fullName).trim() !== "") {
    const v = String(payload.fullName).trim();
    if (v.length < 1 || v.length > 150) throw new Error("Full name must be between 1 and 150 characters.");
    out.fullName = v;
  }
  if (payload?.email !== undefined && String(payload.email).trim() !== "") {
    const v = String(payload.email).trim();
    if (v.length < 3 || v.length > 254) throw new Error("Email must be between 3 and 254 characters.");
    out.email = v;
  }
  if (payload?.phone !== undefined && String(payload.phone).trim() !== "") {
    const v = String(payload.phone).trim();
    if (v.length < 7 || v.length > 25) throw new Error("Phone number must be between 7 and 25 characters.");
    out.phone = v;
  }
  if (Object.keys(out).length < 1) {
    throw new Error("Please update at least one profile field.");
  }
  return out;
}

export function validateCreateAssignment(payload) {
  return {
    clinician_id: assertId(payload?.clinician_id, "Clinician ID"),
    patient_id: assertId(payload?.patient_id, "Patient ID")
  };
}

export function validateRequestUpload(payload) {
  const patient_id = assertId(payload?.patient_id, "Patient ID");
  const fileName = String(payload?.fileName || "").trim();
  const contentType = String(payload?.contentType || "").trim();
  const sizeBytes = Number(payload?.sizeBytes);

  if (fileName.length < 1 || fileName.length > 100) {
    throw new Error("File name must be between 1 and 100 characters.");
  }
  if (!ALLOWED_UPLOAD_TYPES.includes(contentType)) {
    throw new Error("Only PDF, PNG, and JPEG files are supported.");
  }
  if (!Number.isInteger(sizeBytes) || sizeBytes < 1 || sizeBytes > MAX_UPLOAD_BYTES) {
    throw new Error("File size must be greater than 0 bytes and no larger than 10 MB.");
  }
  return { patient_id, fileName, contentType, sizeBytes };
}

export function validateEraseRequest(payload) {
  const confirm = payload?.confirm === true;
  const reason = String(payload?.reason || "").trim();
  if (!confirm) {
    throw new Error("Please confirm the erasure acknowledgement checkbox.");
  }
  if (reason.length < 5 || reason.length > 500) {
    throw new Error("Please provide a reason between 5 and 500 characters.");
  }
  return { confirm: true, reason };
}

export function createApiClient({
  baseUrl = CONFIG.apiBaseUrl,
  getToken = getIdToken,
  onUnauthorized = () => {},
  fetchImpl = window.fetch.bind(window)
} = {}) {
  async function request(path, { method = "GET", query, body } = {}) {
    const token = getToken();
    if (!token) {
      onUnauthorized();
      throw new ApiError(401, "Please sign in to continue.", "missing_token");
    }

    const headers = {
      Authorization: token
    };
    const init = {
      method,
      headers,
      cache: "no-store"
    };

    if (body !== undefined) {
      headers["Content-Type"] = "application/json";
      init.body = JSON.stringify(body);
    }

    const url = `${baseUrl}${path}${buildQueryString(query)}`;
    const resp = await fetchImpl(url, init);
    const text = await resp.text();
    let payload = null;
    let isJson = false;
    if (text) {
      try {
        payload = JSON.parse(text);
        isJson = true;
      } catch (_err) {
        payload = { message: text };
      }
    }

    if (!resp.ok) {
      if (resp.status === 401) {
        onUnauthorized();
      }
      throw humanizeError(resp.status, payload, isJson);
    }

    return { status: resp.status, data: unwrapEnvelope(payload), meta: payload };
  }

  return {
    // Patients
    getMyProfile: () => request("/api/patients/me"),
    updateMyProfile: (body) => request("/api/patients/me", { method: "PATCH", body: validateUpdateProfile(body) }),
    getPatient: (patientId) => request(`/api/patients/${encodeURIComponent(assertId(patientId, "Patient ID"))}`),

    // Clinicians
    listClinicians: () => request("/api/clinicians"),

    // Assignments
    listAssignments: (query = {}) => request("/api/assignments", { query }),
    createAssignment: (body) => request("/api/assignments", { method: "POST", body: validateCreateAssignment(body) }),
    deleteAssignment: (patientId, clinicianId) =>
      request(`/api/assignments/${encodeURIComponent(assertId(patientId, "Patient ID"))}`, {
        method: "DELETE",
        query: clinicianId ? { clinician_id: clinicianId } : undefined
      }),

    // Appointments
    listAppointments: (query = {}) => request("/api/appointments", { query }),
    createAppointment: (body, opts) =>
      request("/api/appointments", { method: "POST", body: validateCreateAppointment(body, opts) }),
    getAppointment: (appointmentId) =>
      request(`/api/appointments/${encodeURIComponent(assertId(appointmentId, "Appointment ID"))}`),
    updateAppointment: (appointmentId, body) =>
      request(`/api/appointments/${encodeURIComponent(assertId(appointmentId, "Appointment ID"))}`, {
        method: "PATCH",
        body: validateUpdateAppointment(body)
      }),
    cancelAppointment: (appointmentId) =>
      request(`/api/appointments/${encodeURIComponent(assertId(appointmentId, "Appointment ID"))}`, {
        method: "DELETE"
      }),

    // Visit Notes
    listVisitNotes: (query = {}) => request("/api/visit-notes", { query }),
    createVisitNote: (patientId, body) =>
      request(`/api/visit-notes/${encodeURIComponent(assertId(patientId, "Patient ID"))}`, {
        method: "POST",
        body: validateCreateVisitNote(body)
      }),
    getVisitNote: (patientId, noteId) =>
      request(
        `/api/visit-notes/${encodeURIComponent(assertId(patientId, "Patient ID"))}/${encodeURIComponent(assertId(noteId, "Note ID"))}`
      ),

    // Files
    listFiles: (query = {}) => request("/api/files", { query }),
    requestUploadUrl: (body) =>
      request("/api/files/upload-url", { method: "POST", body: validateRequestUpload(body) }),
    getDownloadUrl: (fileId) =>
      request(`/api/files/${encodeURIComponent(assertId(fileId, "File ID"))}/download-url`),

    // Privacy & Governance
    exportData: (format = "json") => request("/api/export", { query: { format } }),
    submitEraseRequest: (body) =>
      request("/api/erase", { method: "POST", body: validateEraseRequest(body) }),
    getRequestStatus: (requestId) =>
      request(`/api/requests/${encodeURIComponent(assertId(requestId, "Request ID"))}`),
    searchAudit: (query = {}) => request("/api/audit", { query })
  };
}

/**
 * Direct browser PUT upload to pre-signed S3 URL with progress reporting.
 */
export function uploadFileToPresignedUrl(uploadUrl, file, headers = {}, onProgress = () => {}) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("PUT", uploadUrl, true);
    xhr.setRequestHeader("Content-Type", file.type);
    for (const [k, v] of Object.entries(headers || {})) {
      if (k.toLowerCase() !== "host" && k.toLowerCase() !== "content-length" && k.toLowerCase() !== "content-type") {
        xhr.setRequestHeader(k, String(v));
      }
    }
    xhr.upload.onprogress = (evt) => {
      if (evt.lengthComputable) {
        onProgress(Math.round((evt.loaded / evt.total) * 100));
      }
    };
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        onProgress(100);
        resolve();
      } else {
        reject(new Error(`Upload transfer failed (status ${xhr.status}).`));
      }
    };
    xhr.onerror = () => reject(new Error("Network error while uploading the document."));
    xhr.send(file);
  });
}
