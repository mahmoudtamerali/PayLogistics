import { alertBanner, clear, createIcon, el, emptyState, openModal, showToast, statusBadge } from "../ui.js";

function downloadJsonBlob(filename, data) {
  const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

export async function renderPrivacyView(container, ctx) {
  clear(container);
  const { api, session } = ctx;

  if (session.role !== "patient") {
    container.appendChild(
      el("section", { className: "panel" }, [
        emptyState("Patient Records & Privacy", "Personal health record exports and deletion requests are available for patient accounts.", "shield")
      ])
    );
    return;
  }

  const grid = el("div", { className: "split-equal" });
  const profilePanel = el("section", { className: "panel" });
  const rightsPanel = el("section", { className: "panel" });
  grid.appendChild(profilePanel);
  grid.appendChild(rightsPanel);
  container.appendChild(grid);

  let profileData = {
    fullName: session.claims.name || "",
    email: session.claims.email || "",
    phone: session.claims.phone_number || ""
  };

  try {
    const res = await api.getMyProfile();
    const p = res.data?.patient || res.data || {};
    profileData = {
      fullName: p.fullName || p.full_name || profileData.fullName,
      email: p.email || profileData.email,
      phone: p.phone || p.phone_number || profileData.phone
    };
  } catch (_err) {
    // Allow form render with token claims fallback.
  }

  function renderProfilePanel() {
    clear(profilePanel);
    profilePanel.appendChild(el("div", { className: "panel-header" }, [
      el("div", {}, [
        el("h3", { className: "panel-title", text: "Personal Contact Profile" }),
        el("p", { className: "panel-sub", text: "Keep your contact details accurate for visit reminders and care coordination" })
      ])
    ]));

    const msgSlot = el("div");
    const nameInput = el("input", { id: "profile-full-name", name: "fullName", type: "text", className: "input", maxlength: "150", value: profileData.fullName });
    const emailInput = el("input", { id: "profile-email", name: "email", type: "email", className: "input", maxlength: "254", value: profileData.email });
    const phoneInput = el("input", { id: "profile-phone", name: "phone", type: "tel", className: "input tabular-nums", maxlength: "25", value: profileData.phone });
    const saveBtn = el("button", { type: "submit", className: "btn btn-primary", text: "Save Profile" });

    profilePanel.appendChild(el("form", {
      className: "panel-body",
      style: "display:flex;flex-direction:column;gap:1rem;",
      onSubmit: async (e) => {
        e.preventDefault();
        clear(msgSlot);
        saveBtn.disabled = true;
        try {
          await api.updateMyProfile({
            fullName: nameInput.value,
            email: emailInput.value,
            phone: phoneInput.value
          });
          profileData = { fullName: nameInput.value, email: emailInput.value, phone: phoneInput.value };
          showToast("Profile updated.");
        } catch (err) {
          msgSlot.appendChild(alertBanner(err.message, "error"));
        } finally {
          saveBtn.disabled = false;
        }
      }
    }, [
      msgSlot,
      el("div", { className: "field" }, [el("label", { className: "field-label", for: "profile-full-name", text: "Full Name" }), nameInput]),
      el("div", { className: "field" }, [el("label", { className: "field-label", for: "profile-email", text: "Email Address" }), emailInput]),
      el("div", { className: "field" }, [el("label", { className: "field-label", for: "profile-phone", text: "Phone Number" }), phoneInput]),
      el("div", { style: "display:flex;justify-content:flex-end;" }, [saveBtn])
    ]));
  }

  const trackedStatusBox = el("div");

  async function lookupRequestStatus(requestId) {
    clear(trackedStatusBox);
    try {
      const res = await api.getRequestStatus(requestId);
      const item = res.data?.request || res.data || {};
      trackedStatusBox.appendChild(el("div", {
        style: "padding:0.875rem;border:1px solid var(--sc-border);border-radius:var(--sc-radius-md);background:var(--sc-surface-alt);display:flex;align-items:center;justify-content:space-between;gap:0.75rem;"
      }, [
        el("div", {}, [
          el("div", { style: "font-weight:600;font-size:0.875rem;", text: `Request ${item.request_id || item.requestId || requestId}` }),
          el("div", { className: "field-hint", text: item.type || item.statusMessage || "Request recorded" })
        ]),
        statusBadge(String(item.status || "SUBMITTED"), "info")
      ]));
    } catch (err) {
      trackedStatusBox.appendChild(alertBanner(err.message, "error"));
    }
  }

  function renderRightsPanel() {
    clear(rightsPanel);
    rightsPanel.appendChild(el("div", { className: "panel-header" }, [
      el("div", {}, [
        el("h3", { className: "panel-title", text: "Health Records & Privacy" }),
        el("p", { className: "panel-sub", text: "Download a copy of your health records or manage account deletion" })
      ])
    ]));

    const body = el("div", { className: "panel-body", style: "display:flex;flex-direction:column;gap:1.125rem;" });
    const exportStatus = el("div");
    const exportBtn = el("button", {
      type: "button",
      className: "btn btn-primary btn-sm",
      onClick: async () => {
        clear(exportStatus);
        exportBtn.disabled = true;
        try {
          const res = await api.exportData("json");
          if (res.status === 202 && (res.data?.request_id || res.data?.requestId)) {
            const rid = res.data.request_id || res.data.requestId;
            showToast("Data export request queued.");
            await lookupRequestStatus(rid);
          } else {
            downloadJsonBlob(`securecare-health-export-${new Date().toISOString().slice(0, 10)}.json`, res.data || {});
            showToast("Health record archive downloaded.");
          }
        } catch (err) {
          exportStatus.appendChild(alertBanner(err.message, "error"));
        } finally {
          exportBtn.disabled = false;
        }
      }
    }, [createIcon("download", 14), "Export Records (JSON)"]);

    body.appendChild(el("div", {
      style: "padding:1rem;border:1px solid var(--sc-border);border-radius:var(--sc-radius-md);background:var(--sc-surface-alt);display:flex;flex-wrap:wrap;align-items:center;justify-content:space-between;gap:0.75rem;"
    }, [
      el("div", {}, [
        el("div", { style: "font-weight:600;font-size:0.875rem;", text: "Download My Health Records" }),
        el("div", { className: "field-hint", text: "Includes your profile, scheduled visits, consultation notes, and lab document list." })
      ]),
      exportBtn
    ]));
    body.appendChild(exportStatus);

    const eraseBtn = el("button", {
      type: "button",
      className: "btn btn-danger btn-sm",
      text: "Request Data Erasure",
      onClick: () => {
        const errSlot = el("div");
        const reasonInput = el("textarea", {
          id: "erasure-reason",
          name: "reason",
          className: "textarea",
          rows: "3",
          maxlength: "500",
          placeholder: "Explain why you are requesting account deletion (5–500 characters)..."
        });
        const confirmChk = el("input", { id: "erasure-confirm", name: "confirm", type: "checkbox" });
        const confirmLabel = el("label", {
          for: "erasure-confirm",
          style: "display:flex;align-items:flex-start;gap:0.5rem;font-size:0.8125rem;cursor:pointer;"
        }, [
          confirmChk,
          el("span", { text: "I understand this requests permanent deletion of my account and records where permitted by clinical retention requirements." })
        ]);

        openModal({
          title: "Confirm Erasure Request",
          body: [
            errSlot,
            el("div", { className: "field" }, [el("label", { className: "field-label", for: "erasure-reason", text: "Reason for Request" }), reasonInput]),
            confirmLabel
          ],
          footerButtons: [
            { label: "Cancel", className: "btn btn-secondary", onClick: (close) => close() },
            {
              label: "Submit Erasure Request",
              className: "btn btn-danger-solid",
              onClick: async (close) => {
                clear(errSlot);
                try {
                  const res = await api.submitEraseRequest({
                    confirm: confirmChk.checked,
                    reason: reasonInput.value
                  });
                  close();
                  showToast("Erasure request submitted.");
                  const rid = res.data?.request_id || res.data?.requestId;
                  if (rid) await lookupRequestStatus(rid);
                } catch (err) {
                  errSlot.appendChild(alertBanner(err.message, "error"));
                }
              }
            }
          ]
        });
      }
    });

    body.appendChild(el("div", {
      style: "padding:1rem;border-radius:var(--sc-radius-md);background:var(--sc-danger-bg);display:flex;flex-direction:column;gap:0.625rem;"
    }, [
      el("div", { style: "font-weight:600;font-size:0.875rem;color:var(--sc-danger-text);", text: "Delete Account & Health Records" }),
      el("div", { style: "font-size:0.8125rem;color:var(--sc-danger-text);", text: "Request permanent deletion of your portal account and personal data." }),
      el("div", {}, [eraseBtn])
    ]));

    // Request status lookup
    const reqInput = el("input", { id: "privacy-request-id", name: "request_id", type: "text", className: "input", placeholder: "Request ID" });
    body.appendChild(el("div", { style: "padding-top:0.5rem;border-top:1px solid var(--sc-border);display:flex;flex-direction:column;gap:0.625rem;" }, [
      el("label", { className: "field-label", for: "privacy-request-id", text: "Check Existing Request Status" }),
      el("div", { style: "display:flex;gap:0.5rem;" }, [
        reqInput,
        el("button", {
          type: "button",
          className: "btn btn-secondary btn-sm",
          text: "Check Request Status",
          onClick: () => {
            if (reqInput.value.trim()) lookupRequestStatus(reqInput.value.trim());
          }
        })
      ]),
      trackedStatusBox
    ]));

    rightsPanel.appendChild(body);
  }

  renderProfilePanel();
  renderRightsPanel();
}
