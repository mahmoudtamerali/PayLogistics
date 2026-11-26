import { extractList } from "../api.js";
import { alertBanner, clear, el, formatDateTime, statusBadge, todayUtcDay } from "../ui.js";

function apptStatusVariant(status) {
  if (status === "COMPLETED") return "success";
  if (status === "CANCELLED") return "warn";
  return "info";
}

function fileStatusVariant(status) {
  const s = String(status || "").toUpperCase();
  if (s === "CLEAN") return "success";
  if (s === "QUARANTINED" || s === "INFECTED") return "danger";
  return "warn";
}

function kpiCard(label, value, sub) {
  return el("div", { className: "kpi-card" }, [
    el("div", { className: "kpi-label", text: label }),
    el("div", { className: "kpi-value tabular-nums", text: value }),
    el("div", { className: "kpi-sub", text: sub })
  ]);
}

export async function renderOverviewView(container, ctx) {
  clear(container);
  const { api, session, navigate } = ctx;
  const role = session.role;

  const loading = el("div", { className: "panel panel-body", text: "Loading workspace..." });
  container.appendChild(loading);

  const errors = [];
  let appointments = [];
  let files = [];
  let notes = [];
  let assignments = [];
  let auditEvents = [];

  const tasks = [
    api.listAppointments({ limit: 5 }).then((r) => { appointments = extractList(r.data, ["appointments"]); }).catch((e) => errors.push(e.message)),
    api.listFiles({ limit: 5 }).then((r) => { files = extractList(r.data, ["files"]); }).catch((e) => errors.push(e.message))
  ];

  if (role === "patient" || role === "doctor") {
    tasks.push(api.listVisitNotes({ limit: 5 }).then((r) => { notes = extractList(r.data, ["notes", "visitNotes"]); }).catch(() => {}));
  }
  if (role === "doctor" || role === "admin") {
    tasks.push(api.listAssignments().then((r) => { assignments = extractList(r.data, ["assignments"]); }).catch(() => {}));
  }
  if (role === "admin") {
    tasks.push(api.searchAudit({ day: todayUtcDay(), limit: 10 }).then((r) => { auditEvents = extractList(r.data, ["events", "audit"]); }).catch(() => {}));
  }

  await Promise.all(tasks);
  clear(container);

  if (errors.length > 0) {
    container.appendChild(alertBanner(errors[0], "warn"));
  }

  const bookedCount = appointments.filter((a) => (a.status || "BOOKED") === "BOOKED").length;
  const cleanCount = files.filter((f) => String(f.scanStatus || f.scan_status || f.status || "").toUpperCase() === "CLEAN").length;
  const pendingCount = files.filter((f) => {
    const st = String(f.scanStatus || f.scan_status || f.status || "").toUpperCase();
    return st && st !== "CLEAN" && st !== "QUARANTINED" && st !== "INFECTED";
  }).length;

  const kpiGrid = el("div", { className: "kpi-grid" });
  if (role === "patient") {
    kpiGrid.appendChild(kpiCard("Booked Visits", String(bookedCount), "Upcoming scheduled consultations"));
    kpiGrid.appendChild(kpiCard("Visit Summaries", String(notes.length), "Signed clinical encounter notes"));
    kpiGrid.appendChild(kpiCard("Lab Documents", String(cleanCount), `${pendingCount} awaiting screening`));
    kpiGrid.appendChild(kpiCard("Privacy Controls", "Active", "Profile, export, and erasure access"));
  } else if (role === "doctor") {
    kpiGrid.appendChild(kpiCard("Assigned Patients", String(assignments.length), "Active care-team roster"));
    kpiGrid.appendChild(kpiCard("Scheduled Visits", String(bookedCount), "Upcoming consultations"));
    kpiGrid.appendChild(kpiCard("Encounter Notes", String(notes.length), "Recent patient visit notes"));
    kpiGrid.appendChild(kpiCard("Lab Documents", String(cleanCount), "Verified diagnostic files"));
  } else if (role === "staff") {
    kpiGrid.appendChild(kpiCard("Active Bookings", String(bookedCount), "Clinic schedule queue"));
    kpiGrid.appendChild(kpiCard("Total Appointments", String(appointments.length), "Recent operational records"));
    kpiGrid.appendChild(kpiCard("Verified Lab Files", String(cleanCount), "Released to care teams"));
    kpiGrid.appendChild(kpiCard("Screening Queue", String(pendingCount), "Uploads awaiting scan verdict"));
  } else {
    const deniedCount = auditEvents.filter((ev) => String(ev.outcome || "").toLowerCase() === "denied").length;
    kpiGrid.appendChild(kpiCard("Audit Events Today", String(auditEvents.length), `UTC Date ${todayUtcDay()}`));
    kpiGrid.appendChild(kpiCard("Denied Requests", String(deniedCount), "Access checks refused today"));
    kpiGrid.appendChild(kpiCard("Care Assignments", String(assignments.length), "Clinician–patient pairings"));
    kpiGrid.appendChild(kpiCard("Clinic Visits", String(appointments.length), "Recent scheduled activity"));
  }
  container.appendChild(kpiGrid);

  const lower = el("div", { className: "split-2" });

  // Recent Appointments panel
  const apptPanel = el("section", { className: "panel" }, [
    el("div", { className: "panel-header" }, [
      el("div", {}, [
        el("h3", { className: "panel-title", text: "Recent Appointments" }),
        el("p", { className: "panel-sub", text: "Latest scheduled visits and consultation activity" })
      ]),
      el("button", {
        type: "button",
        className: "btn btn-secondary btn-sm",
        text: "Manage All",
        onClick: () => navigate("appointments")
      })
    ])
  ]);

  if (appointments.length === 0) {
    apptPanel.appendChild(el("div", { className: "panel-body", text: "No appointments scheduled yet." }));
  } else {
    const tbody = el("tbody");
    for (const a of appointments.slice(0, 5)) {
      const status = a.status || "BOOKED";
      tbody.appendChild(el("tr", {}, [
        el("td", { className: "tabular-nums", text: formatDateTime(a.startsAt || a.starts_at) }),
        el("td", { text: a.clinicianName || a.clinician_name || a.clinician_id || "Clinician" }),
        el("td", { text: String(a.visitType || a.visit_type || "consultation").replace("_", " ") }),
        el("td", {}, [statusBadge(status, apptStatusVariant(status))])
      ]));
    }
    apptPanel.appendChild(el("div", { className: "table-wrap" }, [
      el("table", { className: "data-table" }, [
        el("thead", {}, [
          el("tr", {}, [
            el("th", { text: "When" }),
            el("th", { text: "Clinician" }),
            el("th", { text: "Visit Type" }),
            el("th", { text: "Status" })
          ])
        ]),
        tbody
      ])
    ]));
  }
  lower.appendChild(apptPanel);

  // Recent Lab Documents / Governance Quick Actions
  const sidePanel = el("section", { className: "panel" }, [
    el("div", { className: "panel-header" }, [
      el("div", {}, [
        el("h3", { className: "panel-title", text: role === "admin" ? "Governance Quick Actions" : "Recent Lab Documents" }),
        el("p", { className: "panel-sub", text: role === "admin" ? "Daily oversight and care-team management" : "Latest uploaded diagnostic attachments" })
      ]),
      el("button", {
        type: "button",
        className: "btn btn-secondary btn-sm",
        text: role === "admin" ? "Open Ledger" : "Open Files",
        onClick: () => navigate(role === "admin" ? "governance" : "files")
      })
    ])
  ]);

  if (role === "admin") {
    sidePanel.appendChild(el("div", { className: "panel-body", style: "display:flex;flex-direction:column;gap:0.75rem;" }, [
      el("button", {
        type: "button",
        className: "btn btn-primary",
        text: "Review Today's Access Audit Ledger",
        onClick: () => navigate("governance")
      }),
      el("button", {
        type: "button",
        className: "btn btn-secondary",
        text: "Manage Care-Team Assignments",
        onClick: () => navigate("governance")
      }),
      el("button", {
        type: "button",
        className: "btn btn-secondary",
        text: "Inspect Clinic Schedule",
        onClick: () => navigate("appointments")
      })
    ]));
  } else if (files.length === 0) {
    sidePanel.appendChild(el("div", { className: "panel-body", text: "No diagnostic files uploaded yet." }));
  } else {
    const stack = el("div", { className: "panel-body list-stack" });
    for (const f of files.slice(0, 4)) {
      const fStatus = String(f.scanStatus || f.scan_status || f.status || "PENDING_SCAN").toUpperCase();
      stack.appendChild(el("div", {
        style: "display:flex;align-items:center;justify-content:space-between;gap:0.75rem;padding:0.75rem;border:1px solid var(--sc-border);border-radius:var(--sc-radius-md);background:var(--sc-surface-alt);"
      }, [
        el("div", { style: "min-width:0;" }, [
          el("div", { style: "font-weight:600;font-size:0.875rem;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;", text: f.fileName || f.file_name || "Document" }),
          el("div", { style: "font-size:0.75rem;color:var(--sc-muted);", text: f.contentType || f.content_type || "" })
        ]),
        statusBadge(fStatus, fileStatusVariant(fStatus))
      ]));
    }
    sidePanel.appendChild(stack);
  }
  lower.appendChild(sidePanel);

  container.appendChild(lower);
}
