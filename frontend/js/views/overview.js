import { extractList } from "../api.js";
import { alertBanner, clear, el, formatDateTime, statusBadge, todayUtcDay } from "../ui.js";

function apptStatusVariant(status) {
  if (status === "COMPLETED") return "success";
  if (status === "CANCELLED") return "warn";
  return "info";
}

function apptStatusLabel(status) {
  const s = String(status || "BOOKED").toUpperCase();
  if (s === "COMPLETED") return "Completed";
  if (s === "CANCELLED") return "Cancelled";
  return "Scheduled";
}

function visitTypeLabel(visitType) {
  const v = String(visitType || "consultation").toLowerCase();
  if (v === "follow_up") return "Follow-up";
  if (v === "lab_visit") return "Lab Visit";
  return "Consultation";
}

function fileStatusVariant(status) {
  const s = String(status || "").toUpperCase();
  if (s === "CLEAN") return "success";
  if (s === "QUARANTINED" || s === "INFECTED") return "danger";
  return "warn";
}

function fileStatusLabel(status) {
  const s = String(status || "PENDING_SCAN").toUpperCase();
  if (s === "CLEAN") return "Verified";
  if (s === "QUARANTINED" || s === "INFECTED") return "Blocked";
  return "Processing";
}

function kpiCard(label, value, sub, actionBtn = null) {
  const children = [
    el("div", { className: "kpi-label", text: label }),
    el("div", { className: "kpi-value tabular-nums", text: value }),
    el("div", { className: "kpi-sub", text: sub })
  ];
  if (actionBtn) {
    children.push(el("div", { style: "margin-top:0.75rem;" }, [actionBtn]));
  }
  return el("div", { className: "kpi-card" }, children);
}

export async function renderOverviewView(container, ctx) {
  clear(container);
  const { api, session, navigate } = ctx;
  const role = session.role;

  container.appendChild(el("div", { className: "panel panel-body", text: "Loading your workspace..." }));

  let appointments = [];
  let files = [];
  let notes = [];
  let assignments = [];
  let auditEvents = [];

  let apptLoaded = false;
  let filesLoaded = false;
  let notesLoaded = false;
  let assignLoaded = false;
  let auditLoaded = false;
  let firstError = null;

  const trackErr = (err) => {
    if (!firstError) firstError = err.message;
  };

  const tasks = [
    api.listAppointments({ limit: 10 }).then((r) => {
      appointments = extractList(r.data, ["appointments"]);
      apptLoaded = true;
    }).catch(trackErr),
    api.listFiles({ limit: 10 }).then((r) => {
      files = extractList(r.data, ["files"]);
      filesLoaded = true;
    }).catch(trackErr)
  ];

  if (role === "patient" || role === "doctor") {
    tasks.push(
      api.listVisitNotes({ limit: 10 }).then((r) => {
        notes = extractList(r.data, ["notes", "visitNotes"]);
        notesLoaded = true;
      }).catch(trackErr)
    );
  }
  if (role === "doctor" || role === "admin") {
    tasks.push(
      api.listAssignments().then((r) => {
        assignments = extractList(r.data, ["assignments"]);
        assignLoaded = true;
      }).catch(trackErr)
    );
  }
  if (role === "admin") {
    tasks.push(
      api.searchAudit({ day: todayUtcDay(), limit: 20 }).then((r) => {
        auditEvents = extractList(r.data, ["events", "audit"]);
        auditLoaded = true;
      }).catch(trackErr)
    );
  }

  await Promise.all(tasks);
  clear(container);

  if (firstError) {
    const banner = alertBanner(firstError, "warn");
    banner.appendChild(el("button", {
      type: "button",
      className: "btn btn-secondary btn-sm",
      text: "Retry",
      onClick: () => renderOverviewView(container, ctx)
    }));
    container.appendChild(banner);
  }

  const upcomingBooked = appointments
    .filter((a) => (a.status || "BOOKED") === "BOOKED")
    .sort((a, b) => new Date(a.startsAt || a.starts_at || 0) - new Date(b.startsAt || b.starts_at || 0));

  const cleanCount = files.filter((f) => String(f.scanStatus || f.scan_status || f.status || "").toUpperCase() === "CLEAN").length;
  const pendingCount = files.filter((f) => {
    const st = String(f.scanStatus || f.scan_status || f.status || "").toUpperCase();
    return st && st !== "CLEAN" && st !== "QUARANTINED" && st !== "INFECTED";
  }).length;

  const kpiGrid = el("div", { className: "kpi-grid" });

  if (role === "patient") {
    const nextAppt = upcomingBooked[0];
    const nextVal = !apptLoaded
      ? "—"
      : nextAppt
        ? formatDateTime(nextAppt.startsAt || nextAppt.starts_at)
        : "None";
    const nextSub = !apptLoaded
      ? "Schedule unavailable"
      : nextAppt
        ? `${nextAppt.clinicianName || nextAppt.clinician_name || "Attending Physician"} · ${visitTypeLabel(nextAppt.visitType || nextAppt.visit_type)}`
        : "No upcoming visits booked";

    kpiGrid.appendChild(kpiCard("Next Appointment", nextVal, nextSub));
    kpiGrid.appendChild(kpiCard(
      "Upcoming Visits",
      apptLoaded ? String(upcomingBooked.length) : "—",
      apptLoaded ? "Confirmed appointments" : "Unavailable"
    ));
    kpiGrid.appendChild(kpiCard(
      "Visit Summaries",
      notesLoaded ? String(notes.length) : "—",
      notesLoaded ? "Physician consultation notes" : "Unavailable"
    ));
    kpiGrid.appendChild(kpiCard(
      "Lab Reports",
      filesLoaded ? String(cleanCount) : "—",
      !filesLoaded ? "Unavailable" : pendingCount > 0 ? `${pendingCount} processing` : "Verified diagnostic files"
    ));
  } else if (role === "doctor") {
    kpiGrid.appendChild(kpiCard("Assigned Patients", assignLoaded ? String(assignments.length) : "—", "Active care roster"));
    kpiGrid.appendChild(kpiCard("Upcoming Visits", apptLoaded ? String(upcomingBooked.length) : "—", "Scheduled consultations"));
    kpiGrid.appendChild(kpiCard("Clinical Notes", notesLoaded ? String(notes.length) : "—", "Recent encounter records"));
    kpiGrid.appendChild(kpiCard("Verified Lab Files", filesLoaded ? String(cleanCount) : "—", "Ready for clinical review"));
  } else if (role === "staff") {
    kpiGrid.appendChild(kpiCard("Scheduled Visits", apptLoaded ? String(upcomingBooked.length) : "—", "Active clinic queue"));
    kpiGrid.appendChild(kpiCard("Total Visits Loaded", apptLoaded ? String(appointments.length) : "—", "Recent appointments"));
    kpiGrid.appendChild(kpiCard("Verified Lab Files", filesLoaded ? String(cleanCount) : "—", "Cleared for care teams"));
    kpiGrid.appendChild(kpiCard("Processing Uploads", filesLoaded ? String(pendingCount) : "—", "Awaiting verification"));
  } else {
    const deniedCount = auditEvents.filter((ev) => String(ev.outcome || "").toLowerCase() === "denied").length;
    kpiGrid.appendChild(kpiCard("Activity Today", auditLoaded ? String(auditEvents.length) : "—", `UTC ${todayUtcDay()}`));
    kpiGrid.appendChild(kpiCard("Denied Access", auditLoaded ? String(deniedCount) : "—", "Refused record requests"));
    kpiGrid.appendChild(kpiCard("Care Assignments", assignLoaded ? String(assignments.length) : "—", "Physician–patient pairs"));
    kpiGrid.appendChild(kpiCard("Scheduled Visits", apptLoaded ? String(upcomingBooked.length) : "—", "Upcoming clinic bookings"));
  }

  container.appendChild(kpiGrid);

  const lower = el("div", { className: "split-2" });

  // Appointments Panel
  const apptPanel = el("section", { className: "panel" }, [
    el("div", { className: "panel-header" }, [
      el("div", {}, [
        el("h3", { className: "panel-title", text: "Appointments" }),
        el("p", { className: "panel-sub", text: "Upcoming consultations and visit history" })
      ]),
      el("button", {
        type: "button",
        className: "btn btn-secondary btn-sm",
        text: role === "patient" ? "Book or Manage" : "View All",
        onClick: () => navigate("appointments")
      })
    ])
  ]);

  if (!apptLoaded) {
    apptPanel.appendChild(el("div", { className: "panel-body", style: "color:var(--sc-muted);", text: "Appointments could not be loaded." }));
  } else if (appointments.length === 0) {
    apptPanel.appendChild(el("div", { className: "panel-body", style: "display:flex;align-items:center;justify-content:space-between;gap:1rem;" }, [
      el("span", { style: "color:var(--sc-muted);", text: "No appointments scheduled yet." }),
      el("button", {
        type: "button",
        className: "btn btn-primary btn-sm",
        text: "Schedule a Visit",
        onClick: () => navigate("appointments")
      })
    ]));
  } else {
    const tbody = el("tbody");
    for (const a of appointments.slice(0, 5)) {
      const status = a.status || "BOOKED";
      tbody.appendChild(el("tr", {}, [
        el("td", { className: "tabular-nums", text: formatDateTime(a.startsAt || a.starts_at) }),
        el("td", { text: a.clinicianName || a.clinician_name || a.clinician_id || "Clinician" }),
        el("td", { text: visitTypeLabel(a.visitType || a.visit_type) }),
        el("td", {}, [statusBadge(apptStatusLabel(status), apptStatusVariant(status))])
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

  // Secondary Panel: Lab Reports or Governance Actions
  const sidePanel = el("section", { className: "panel" }, [
    el("div", { className: "panel-header" }, [
      el("div", {}, [
        el("h3", { className: "panel-title", text: role === "admin" ? "Quick Actions" : "Diagnostic Reports" }),
        el("p", { className: "panel-sub", text: role === "admin" ? "Daily administration and care-team tools" : "Recent laboratory and imaging uploads" })
      ]),
      el("button", {
        type: "button",
        className: "btn btn-secondary btn-sm",
        text: role === "admin" ? "Open Ledger" : "All Files",
        onClick: () => navigate(role === "admin" ? "governance" : "files")
      })
    ])
  ]);

  if (role === "admin") {
    sidePanel.appendChild(el("div", { className: "panel-body", style: "display:flex;flex-direction:column;gap:0.75rem;" }, [
      el("button", {
        type: "button",
        className: "btn btn-primary",
        text: "Review Today's Access Log",
        onClick: () => navigate("governance")
      }),
      el("button", {
        type: "button",
        className: "btn btn-secondary",
        text: "Manage Care Assignments",
        onClick: () => navigate("governance")
      }),
      el("button", {
        type: "button",
        className: "btn btn-secondary",
        text: "View Clinic Schedule",
        onClick: () => navigate("appointments")
      })
    ]));
  } else if (!filesLoaded) {
    sidePanel.appendChild(el("div", { className: "panel-body", style: "color:var(--sc-muted);", text: "Diagnostic documents could not be loaded." }));
  } else if (files.length === 0) {
    sidePanel.appendChild(el("div", { className: "panel-body", style: "display:flex;align-items:center;justify-content:space-between;gap:1rem;" }, [
      el("span", { style: "color:var(--sc-muted);", text: "No diagnostic files uploaded yet." }),
      ...(role === "patient" || role === "staff"
        ? [el("button", { type: "button", className: "btn btn-secondary btn-sm", text: "Upload File", onClick: () => navigate("files") })]
        : [])
    ]));
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
        statusBadge(fileStatusLabel(fStatus), fileStatusVariant(fStatus))
      ]));
    }
    sidePanel.appendChild(stack);
  }
  lower.appendChild(sidePanel);

  container.appendChild(lower);
}
