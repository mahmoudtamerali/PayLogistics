import {
  ALLOWED_UPLOAD_TYPES,
  MAX_UPLOAD_BYTES,
  extractList,
  extractNextToken,
  uploadFileToPresignedUrl
} from "../api.js";
import {
  alertBanner,
  clear,
  createIcon,
  el,
  emptyState,
  formatBytes,
  formatDateTime,
  showToast,
  statusBadge
} from "../ui.js";

function scanStatusVariant(status) {
  const s = String(status || "").toUpperCase();
  if (s === "CLEAN") return "success";
  if (s === "QUARANTINED" || s === "INFECTED") return "danger";
  return "warn";
}

function scanStatusLabel(status) {
  const s = String(status || "PENDING_SCAN").toUpperCase();
  if (s === "CLEAN") return "Clean";
  if (s === "QUARANTINED" || s === "INFECTED") return "Quarantined";
  return "Pending Scan";
}

export async function renderFilesView(container, ctx) {
  clear(container);
  const { api, session } = ctx;
  const role = session.role;
  const canUpload = role === "patient" || role === "staff";
  const canDownload = role !== "admin";

  let ownPatientId = session.claims.sub || "";
  if (role === "patient") {
    try {
      const me = await api.getMyProfile();
      const profile = me.data?.patient || me.data || {};
      ownPatientId = profile.patient_id || profile.patientId || profile.id || ownPatientId;
    } catch (_err) {
      // Fall back to Cognito sub when profile has not been initialized yet.
    }
  }

  let files = [];
  let nextToken = null;
  let filterPatient = "";

  const stack = el("div", { style: "display:flex;flex-direction:column;gap:1.25rem;" });
  const uploadPanel = el("section", { className: "panel" });
  const listPanel = el("section", { className: "panel" });

  if (canUpload) stack.appendChild(uploadPanel);
  stack.appendChild(listPanel);
  container.appendChild(stack);

  function renderUploadPanel() {
    if (!canUpload) return;
    clear(uploadPanel);

    uploadPanel.appendChild(el("div", { className: "panel-header" }, [
      el("div", {}, [
        el("h3", { className: "panel-title", text: "Upload Diagnostic Document" }),
        el("p", { className: "panel-sub", text: "Accepted formats: PDF, PNG, or JPEG up to 10 MB. Files are screened before clinical release." })
      ])
    ]));

    const body = el("div", { className: "panel-body", style: "display:flex;flex-direction:column;gap:1rem;" });
    const statusBox = el("div");
    const progressWrap = el("div", { style: "display:none;flex-direction:column;gap:0.4rem;" });
    const progressLabel = el("div", { className: "field-hint tabular-nums", text: "Preparing upload..." });
    const progressFill = el("div", { className: "progress-fill" });
    progressWrap.appendChild(progressLabel);
    progressWrap.appendChild(el("div", { className: "progress-track" }, [progressFill]));

    const staffPatientInput = el("input", {
      type: "text",
      className: "input",
      placeholder: "Enter target Patient ID"
    });
    if (role === "staff") {
      body.appendChild(el("div", { className: "field", style: "max-width:340px;" }, [
        el("label", { className: "field-label", text: "Patient ID" }),
        staffPatientInput
      ]));
    }

    const fileInput = el("input", {
      id: "lab-file-upload",
      name: "document",
      type: "file",
      accept: ".pdf,.png,.jpg,.jpeg,application/pdf,image/png,image/jpeg",
      className: "sr-only",
      "aria-label": "Choose document to upload"
    });

    const selectBtn = el("button", {
      type: "button",
      className: "btn btn-primary",
      text: "Choose Document",
      onClick: () => fileInput.click()
    });

    const dropzone = el("div", { className: "dropzone" }, [
      el("div", { style: "display:flex;align-items:center;gap:0.875rem;" }, [
        el("div", { className: "icon-box" }, [createIcon("files", 20)]),
        el("div", {}, [
          el("div", { style: "font-weight:600;font-size:0.9375rem;", text: "Drag and drop a file here, or browse from your device" }),
          el("div", { className: "field-hint", text: "PDF, PNG, or JPEG · Maximum 10 MB" })
        ])
      ]),
      selectBtn,
      fileInput
    ]);

    async function handleFile(file) {
      clear(statusBox);
      if (!file) return;

      const targetPatient = role === "staff" ? staffPatientInput.value.trim() : ownPatientId;
      if (!targetPatient) {
        statusBox.appendChild(alertBanner("Please enter the Patient ID before uploading.", "warn"));
        return;
      }
      if (!ALLOWED_UPLOAD_TYPES.includes(file.type)) {
        statusBox.appendChild(alertBanner("Only PDF, PNG, and JPEG files are accepted.", "error"));
        return;
      }
      if (file.size < 1 || file.size > MAX_UPLOAD_BYTES) {
        statusBox.appendChild(alertBanner("Document must be between 1 byte and 10 MB.", "error"));
        return;
      }
      const cleanName = file.name.trim().slice(0, 100);

      selectBtn.disabled = true;
      progressWrap.style.display = "flex";
      progressLabel.textContent = "Authorizing upload...";
      progressFill.style.width = "10%";

      try {
        const res = await api.requestUploadUrl({
          patient_id: targetPatient,
          fileName: cleanName,
          contentType: file.type,
          sizeBytes: file.size
        });
        const uploadUrl = res.data?.uploadUrl || res.data?.upload_url || res.data?.url;
        const uploadHeaders = res.data?.headers || {};
        if (!uploadUrl) {
          throw new Error("Upload destination was not provided by the server.");
        }

        progressLabel.textContent = "Transferring encrypted document...";
        await uploadFileToPresignedUrl(uploadUrl, file, uploadHeaders, (pct) => {
          progressFill.style.width = `${pct}%`;
          progressLabel.textContent = `Uploading ${cleanName} (${pct}%)`;
        });

        showToast("Document uploaded and queued for automated screening.");
        await loadFiles(false);
      } catch (err) {
        statusBox.appendChild(alertBanner(err.message, "error"));
      } finally {
        selectBtn.disabled = false;
        fileInput.value = "";
        window.setTimeout(() => {
          progressWrap.style.display = "none";
          progressFill.style.width = "0%";
        }, 1200);
      }
    }

    fileInput.addEventListener("change", () => {
      if (fileInput.files?.[0]) handleFile(fileInput.files[0]);
    });

    dropzone.addEventListener("dragover", (e) => {
      e.preventDefault();
      dropzone.classList.add("dragover");
    });
    dropzone.addEventListener("dragleave", () => dropzone.classList.remove("dragover"));
    dropzone.addEventListener("drop", (e) => {
      e.preventDefault();
      dropzone.classList.remove("dragover");
      if (e.dataTransfer?.files?.[0]) handleFile(e.dataTransfer.files[0]);
    });

    body.appendChild(statusBox);
    body.appendChild(dropzone);
    body.appendChild(progressWrap);
    uploadPanel.appendChild(body);
  }

  async function loadFiles(append = false) {
    try {
      const res = await api.listFiles({
        patient_id: filterPatient,
        limit: 25,
        nextToken: append ? nextToken : undefined
      });
      const rows = extractList(res.data, ["files"]);
      files = append ? [...files, ...rows] : rows;
      nextToken = extractNextToken(res.data);
      renderList();
    } catch (err) {
      clear(listPanel);
      listPanel.appendChild(el("div", { className: "panel-body" }, [alertBanner(err.message, "error")]));
    }
  }

  async function handleDownload(fileRecord, btn) {
    const fileId = fileRecord.fileId || fileRecord.file_id || fileRecord.id;
    btn.disabled = true;
    try {
      const res = await api.getDownloadUrl(fileId);
      const downloadUrl = res.data?.downloadUrl || res.data?.download_url || res.data?.url;
      if (!downloadUrl) {
        throw new Error("Download link unavailable.");
      }
      window.open(downloadUrl, "_blank", "noopener,noreferrer");
    } catch (err) {
      showToast(err.message, "error");
    } finally {
      btn.disabled = false;
    }
  }

  function renderList() {
    clear(listPanel);

    const actionsBar = el("div", { className: "filter-bar" });
    if (role !== "patient") {
      actionsBar.appendChild(el("input", {
        type: "search",
        className: "input",
        placeholder: "Filter by Patient ID...",
        value: filterPatient,
        onChange: (e) => {
          filterPatient = e.target.value.trim();
          loadFiles(false);
        }
      }));
    }
    actionsBar.appendChild(el("button", {
      type: "button",
      className: "btn btn-secondary btn-sm",
      onClick: () => loadFiles(false)
    }, [createIcon("refresh", 14), "Refresh Status"]));

    listPanel.appendChild(el("div", { className: "panel-header" }, [
      el("div", {}, [
        el("h3", { className: "panel-title", text: "Laboratory & Diagnostic Documents" }),
        el("p", { className: "panel-sub", text: role === "admin" ? "Document metadata overview (downloads restricted for admin role)" : "Clean documents are available for immediate download" })
      ]),
      actionsBar
    ]));

    if (files.length === 0) {
      listPanel.appendChild(emptyState("No laboratory documents", "Uploaded PDF referrals and diagnostic images will appear here.", "files"));
      return;
    }

    const tbody = el("tbody");
    for (const f of files) {
      const rawStatus = String(f.scanStatus || f.scan_status || f.status || "PENDING_SCAN").toUpperCase();
      const isClean = rawStatus === "CLEAN";

      let actionCell;
      if (!canDownload) {
        actionCell = el("span", { style: "font-size:0.8125rem;color:var(--sc-muted);", text: "Metadata only" });
      } else if (isClean) {
        const dlBtn = el("button", {
          type: "button",
          className: "btn btn-secondary btn-sm",
          onClick: (e) => handleDownload(f, e.currentTarget)
        }, [createIcon("download", 14), "Download"]);
        actionCell = dlBtn;
      } else if (rawStatus === "QUARANTINED" || rawStatus === "INFECTED") {
        actionCell = el("span", { style: "font-size:0.8125rem;color:var(--sc-danger-text);font-weight:600;", text: "Blocked" });
      } else {
        actionCell = el("span", { style: "font-size:0.8125rem;color:var(--sc-muted);", text: "Awaiting scan" });
      }

      const cells = [
        el("td", { style: "font-weight:600;", text: f.fileName || f.file_name || "Document" }),
        el("td", { className: "mono", text: f.contentType || f.content_type || "—" }),
        el("td", { className: "tabular-nums", text: formatBytes(f.sizeBytes || f.size_bytes) })
      ];
      if (role !== "patient") {
        cells.push(el("td", { className: "mono", text: f.patient_id || f.patientId || "—" }));
      }
      cells.push(
        el("td", { className: "tabular-nums", text: formatDateTime(f.uploadedAt || f.uploaded_at || f.createdAt || f.created_at) }),
        el("td", {}, [statusBadge(scanStatusLabel(rawStatus), scanStatusVariant(rawStatus))]),
        el("td", { style: "text-align:right;" }, [actionCell])
      );

      tbody.appendChild(el("tr", {}, cells));
    }

    const heads = [el("th", { text: "Document" }), el("th", { text: "Type" }), el("th", { text: "Size" })];
    if (role !== "patient") heads.push(el("th", { text: "Patient" }));
    heads.push(el("th", { text: "Uploaded" }), el("th", { text: "Screening" }), el("th", { style: "text-align:right;", text: "Download" }));

    listPanel.appendChild(el("div", { className: "table-wrap" }, [
      el("table", { className: "data-table" }, [
        el("thead", {}, [el("tr", {}, heads)]),
        tbody
      ])
    ]));

    if (nextToken) {
      listPanel.appendChild(el("div", { className: "panel-body", style: "display:flex;justify-content:center;" }, [
        el("button", {
          type: "button",
          className: "btn btn-secondary btn-sm",
          text: "Load More Documents",
          onClick: () => loadFiles(true)
        })
      ]));
    }
  }

  renderUploadPanel();
  await loadFiles(false);
}
