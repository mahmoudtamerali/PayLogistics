import { extractList, extractNextToken } from "../api.js";
import {
  alertBanner,
  clear,
  el,
  emptyState,
  formatDateTime,
  openModal,
  showToast,
  statusBadge,
  todayUtcDay
} from "../ui.js";

function outcomeVariant(outcome) {
  const o = String(outcome || "").toLowerCase();
  if (o === "allowed" || o === "allow") return "success";
  if (o === "denied" || o === "deny") return "danger";
  return "neutral";
}

export async function renderGovernanceView(container, ctx) {
  clear(container);
  const { api, session } = ctx;

  if (session.role !== "admin") {
    container.appendChild(
      el("section", { className: "panel" }, [
        emptyState("Governance Restricted", "Audit trails and care-team assignment controls are available to administrators only.", "shield")
      ])
    );
    return;
  }

  let day = todayUtcDay();
  let patientFilter = "";
  let auditEvents = [];
  let nextToken = null;
  let assignments = [];
  let clinicians = [];

  const grid = el("div", { className: "split-2" });
  const auditPanel = el("section", { className: "panel" });
  const sideCol = el("div", { style: "display:flex;flex-direction:column;gap:1.25rem;" });
  const assignPanel = el("section", { className: "panel" });
  const lookupPanel = el("section", { className: "panel" });
  sideCol.appendChild(assignPanel);
  sideCol.appendChild(lookupPanel);
  grid.appendChild(auditPanel);
  grid.appendChild(sideCol);
  container.appendChild(grid);

  async function loadAudit(append = false) {
    try {
      const res = await api.searchAudit({
        day,
        patient_id: patientFilter,
        limit: 25,
        nextToken: append ? nextToken : undefined
      });
      const rows = extractList(res.data, ["events", "audit", "logs"]);
      auditEvents = append ? [...auditEvents, ...rows] : rows;
      nextToken = extractNextToken(res.data);
      renderAuditPanel();
    } catch (err) {
      clear(auditPanel);
      auditPanel.appendChild(el("div", { className: "panel-body" }, [alertBanner(err.message, "error")]));
    }
  }

  async function loadAssignmentsAndClinicians() {
    try {
      const [aRes, cRes] = await Promise.all([api.listAssignments(), api.listClinicians().catch(() => ({ data: [] }))]);
      assignments = extractList(aRes.data, ["assignments"]);
      clinicians = extractList(cRes.data, ["clinicians"]);
      renderAssignmentsPanel();
    } catch (err) {
      clear(assignPanel);
      assignPanel.appendChild(el("div", { className: "panel-body" }, [alertBanner(err.message, "error")]));
    }
  }

  function inspectEvent(ev) {
    const fields = Object.entries(ev).map(([k, v]) =>
      el("div", { style: "display:flex;justify-content:space-between;gap:1rem;padding:0.35rem 0;border-bottom:1px solid var(--sc-border);" }, [
        el("span", { style: "font-weight:600;font-size:0.8125rem;", text: k }),
        el("span", { className: "mono", style: "word-break:break-all;text-align:right;", text: typeof v === "object" ? JSON.stringify(v) : String(v) })
      ])
    );
    openModal({
      title: "Audit Event Details",
      body: [el("div", {}, fields)],
      footerButtons: [{ label: "Close", className: "btn btn-secondary", onClick: (close) => close() }]
    });
  }

  function renderAuditPanel() {
    clear(auditPanel);

    const dayInput = el("input", {
      type: "date",
      className: "input tabular-nums",
      "aria-label": "Audit date (UTC)",
      value: day,
      onChange: (e) => {
        if (e.target.value) {
          day = e.target.value;
          loadAudit(false);
        }
      }
    });
    const pidInput = el("input", {
      type: "search",
      className: "input",
      placeholder: "Patient ID...",
      value: patientFilter,
      onChange: (e) => {
        patientFilter = e.target.value.trim();
        loadAudit(false);
      }
    });

    auditPanel.appendChild(el("div", { className: "panel-header" }, [
      el("div", {}, [
        el("h3", { className: "panel-title", text: "Access Audit Ledger" }),
        el("p", { className: "panel-sub", text: "Daily clinical record access history (UTC)" })
      ]),
      el("div", { className: "filter-bar" }, [dayInput, pidInput])
    ]));

    if (auditEvents.length === 0) {
      auditPanel.appendChild(emptyState("No audit events on this date", "Select another UTC date or clear the patient filter.", "shield"));
      return;
    }

    const tbody = el("tbody");
    for (const ev of auditEvents) {
      const outcome = ev.outcome || "unknown";
      const tr = el("tr", {
        className: "clickable",
        onClick: () => inspectEvent(ev)
      }, [
        el("td", { className: "tabular-nums", text: formatDateTime(ev.ts || ev.timestamp || ev.created_at) }),
        el("td", { text: `${ev.caller_id || ev.actor || "—"} (${ev.role || "—"})` }),
        el("td", { className: "mono", text: ev.action || "—" }),
        el("td", { className: "mono", text: ev.patient_id || ev.patientId || "—" }),
        el("td", {}, [statusBadge(outcome, outcomeVariant(outcome))])
      ]);
      tbody.appendChild(tr);
    }

    auditPanel.appendChild(el("div", { className: "table-wrap" }, [
      el("table", { className: "data-table" }, [
        el("thead", {}, [
          el("tr", {}, [
            el("th", { text: "Timestamp" }),
            el("th", { text: "Actor" }),
            el("th", { text: "Action" }),
            el("th", { text: "Patient" }),
            el("th", { text: "Outcome" })
          ])
        ]),
        tbody
      ])
    ]));

    if (nextToken) {
      auditPanel.appendChild(el("div", { className: "panel-body", style: "display:flex;justify-content:center;" }, [
        el("button", {
          type: "button",
          className: "btn btn-secondary btn-sm",
          text: "Load More Events",
          onClick: () => loadAudit(true)
        })
      ]));
    }
  }

  function renderAssignmentsPanel() {
    clear(assignPanel);
    assignPanel.appendChild(el("div", { className: "panel-header" }, [
      el("div", {}, [
        el("h3", { className: "panel-title", text: "Care-Team Assignments" }),
        el("p", { className: "panel-sub", text: "Authorize or remove clinician access to specific patients" })
      ])
    ]));

    const body = el("div", { className: "panel-body", style: "display:flex;flex-direction:column;gap:1rem;" });
    const errBox = el("div");
    const patientInput = el("input", { type: "text", className: "input", placeholder: "Patient ID" });
    const clinSelect = clinicians.length > 0
      ? el("select", { className: "select" }, clinicians.map((c) => {
          const cid = c.clinician_id || c.id;
          return el("option", { value: cid, text: c.fullName || c.name || cid });
        }))
      : el("input", { type: "text", className: "input", placeholder: "Clinician ID" });

    const form = el("form", {
      style: "display:flex;flex-direction:column;gap:0.75rem;",
      onSubmit: async (e) => {
        e.preventDefault();
        clear(errBox);
        try {
          await api.createAssignment({
            clinician_id: clinSelect.value,
            patient_id: patientInput.value
          });
          patientInput.value = "";
          showToast("Care assignment created.");
          await loadAssignmentsAndClinicians();
        } catch (err) {
          errBox.appendChild(alertBanner(err.message, "error"));
        }
      }
    }, [
      errBox,
      el("div", { className: "field" }, [el("label", { className: "field-label", text: "Patient ID" }), patientInput]),
      el("div", { className: "field" }, [el("label", { className: "field-label", text: "Clinician" }), clinSelect]),
      el("button", { type: "submit", className: "btn btn-primary btn-sm", text: "Assign Clinician" })
    ]);

    body.appendChild(form);

    if (assignments.length > 0) {
      const list = el("div", { className: "list-stack", style: "padding-top:0.5rem;border-top:1px solid var(--sc-border);" });
      for (const a of assignments) {
        const pid = a.patient_id || a.patientId;
        const cid = a.clinician_id || a.clinicianId;
        list.appendChild(el("div", {
          style: "display:flex;align-items:center;justify-content:space-between;gap:0.5rem;padding:0.65rem 0.75rem;border:1px solid var(--sc-border);border-radius:var(--sc-radius-md);background:var(--sc-surface-alt);"
        }, [
          el("div", {}, [
            el("div", { style: "font-weight:600;font-size:0.8125rem;", text: a.clinicianName || cid }),
            el("div", { className: "mono", style: "font-size:0.75rem;color:var(--sc-muted);", text: `Patient: ${pid}` })
          ]),
          el("button", {
            type: "button",
            className: "btn btn-danger btn-sm",
            text: "Revoke",
            onClick: async () => {
              try {
                await api.deleteAssignment(pid, cid);
                showToast("Assignment removed.");
                await loadAssignmentsAndClinicians();
              } catch (err) {
                showToast(err.message, "error");
              }
            }
          })
        ]));
      }
      body.appendChild(list);
    }

    assignPanel.appendChild(body);
  }

  function renderLookupPanel() {
    clear(lookupPanel);
    lookupPanel.appendChild(el("div", { className: "panel-header" }, [
      el("div", {}, [
        el("h3", { className: "panel-title", text: "Compliance Lookups" }),
        el("p", { className: "panel-sub", text: "Check privacy request status or operational patient details" })
      ])
    ]));

    const resultSlot = el("div");
    const reqInput = el("input", { type: "text", className: "input", placeholder: "Privacy Request ID" });
    const patInput = el("input", { type: "text", className: "input", placeholder: "Patient ID" });

    lookupPanel.appendChild(el("div", { className: "panel-body", style: "display:flex;flex-direction:column;gap:0.875rem;" }, [
      el("div", { style: "display:flex;gap:0.5rem;" }, [
        reqInput,
        el("button", {
          type: "button",
          className: "btn btn-secondary btn-sm",
          text: "Check Request",
          onClick: async () => {
            clear(resultSlot);
            if (!reqInput.value.trim()) return;
            try {
              const res = await api.getRequestStatus(reqInput.value.trim());
              resultSlot.appendChild(el("pre", {
                className: "mono",
                style: "margin:0;padding:0.75rem;background:var(--sc-surface-alt);border-radius:var(--sc-radius-md);overflow:auto;",
                text: JSON.stringify(res.data, null, 2)
              }));
            } catch (err) {
              resultSlot.appendChild(alertBanner(err.message, "error"));
            }
          }
        })
      ]),
      el("div", { style: "display:flex;gap:0.5rem;" }, [
        patInput,
        el("button", {
          type: "button",
          className: "btn btn-secondary btn-sm",
          text: "Lookup Patient",
          onClick: async () => {
            clear(resultSlot);
            if (!patInput.value.trim()) return;
            try {
              const res = await api.getPatient(patInput.value.trim());
              resultSlot.appendChild(el("pre", {
                className: "mono",
                style: "margin:0;padding:0.75rem;background:var(--sc-surface-alt);border-radius:var(--sc-radius-md);overflow:auto;",
                text: JSON.stringify(res.data, null, 2)
              }));
            } catch (err) {
              resultSlot.appendChild(alertBanner(err.message, "error"));
            }
          }
        })
      ]),
      resultSlot
    ]));
  }

  renderLookupPanel();
  await Promise.all([loadAudit(false), loadAssignmentsAndClinicians()]);
}
