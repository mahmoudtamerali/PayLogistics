import { ALLOWED_APPOINTMENT_STATUSES, ALLOWED_VISIT_TYPES, extractList, extractNextToken } from "../api.js";
import {
  alertBanner,
  clear,
  el,
  emptyState,
  formatDateTime,
  openModal,
  showToast,
  statusBadge
} from "../ui.js";

function statusVariant(status) {
  if (status === "COMPLETED") return "success";
  if (status === "CANCELLED") return "warn";
  return "info";
}

function statusLabel(status) {
  const s = String(status || "BOOKED").toUpperCase();
  if (s === "COMPLETED") return "Completed";
  if (s === "CANCELLED") return "Cancelled";
  return "Scheduled";
}

function visitLabel(visitType) {
  const v = String(visitType || "consultation").toLowerCase();
  if (v === "follow_up") return "Follow-up";
  if (v === "lab_visit") return "Lab Visit";
  return "Consultation";
}

function toIsoUtc(datetimeLocalValue) {
  const d = new Date(datetimeLocalValue);
  if (Number.isNaN(d.getTime())) return "";
  return d.toISOString().replace(/\.\d{3}Z$/, "Z");
}

function toLocalInputValue(isoString) {
  if (!isoString) return "";
  const d = new Date(isoString);
  if (Number.isNaN(d.getTime())) return "";
  const pad = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

export async function renderAppointmentsView(container, ctx) {
  clear(container);
  const { api, session } = ctx;
  const role = session.role;
  const needsPatientField = role === "staff" || role === "admin" || role === "doctor";

  let clinicians = [];
  let cliniciansLoadFailed = false;
  let appointments = [];
  let nextToken = null;
  let filterStatus = "";
  let filterDay = "";
  let filterPatient = "";

  const layout = el("div", { className: "split-2" });
  const listPanel = el("section", { className: "panel" });
  const formPanel = el("section", { className: "panel" });
  layout.appendChild(listPanel);
  layout.appendChild(formPanel);
  container.appendChild(layout);

  async function loadClinicians() {
    try {
      const res = await api.listClinicians();
      clinicians = extractList(res.data, ["clinicians"]);
      cliniciansLoadFailed = false;
    } catch (_err) {
      clinicians = [];
      cliniciansLoadFailed = true;
    }
  }

  function clinicianLabel(id) {
    const match = clinicians.find((c) => (c.clinician_id || c.id) === id);
    if (match) {
      const name = match.fullName || match.name || match.displayName || id;
      const spec = match.specialty || match.department || "";
      return spec ? `${name} — ${spec}` : name;
    }
    return id || "Clinician";
  }

  async function loadAppointments(append = false) {
    try {
      const res = await api.listAppointments({
        status: filterStatus,
        day: filterDay,
        patient_id: filterPatient,
        limit: 20,
        nextToken: append ? nextToken : undefined
      });
      const rows = extractList(res.data, ["appointments"]);
      appointments = append ? [...appointments, ...rows] : rows;
      nextToken = extractNextToken(res.data);
      renderList();
    } catch (err) {
      clear(listPanel);
      listPanel.appendChild(el("div", { className: "panel-body" }, [alertBanner(err.message, "error")]));
    }
  }

  function openRescheduleModal(appt) {
    const apptId = appt.appointment_id || appt.id;
    const startsInput = el("input", {
      id: "modal-appt-starts-at",
      name: "startsAt",
      type: "datetime-local",
      className: "input tabular-nums",
      value: toLocalInputValue(appt.startsAt || appt.starts_at)
    });
    const typeSelect = el("select", { id: "modal-appt-visit-type", name: "visitType", className: "select" }, ALLOWED_VISIT_TYPES.map((vt) =>
      el("option", { value: vt, selected: (appt.visitType || appt.visit_type) === vt, text: visitLabel(vt) })
    ));
    const statusSelect = el("select", { id: "modal-appt-status", name: "status", className: "select" }, ALLOWED_APPOINTMENT_STATUSES.map((st) =>
      el("option", { value: st, selected: (appt.status || "BOOKED") === st, text: statusLabel(st) })
    ));
    const errSlot = el("div");

    const fields = [
      errSlot,
      el("div", { className: "field" }, [el("label", { className: "field-label", for: "modal-appt-starts-at", text: "Date & Time" }), startsInput]),
      el("div", { className: "field" }, [el("label", { className: "field-label", for: "modal-appt-visit-type", text: "Visit Type" }), typeSelect])
    ];
    if (role !== "patient") {
      fields.push(el("div", { className: "field" }, [el("label", { className: "field-label", for: "modal-appt-status", text: "Status" }), statusSelect]));
    }

    openModal({
      title: "Update Appointment",
      body: fields,
      footerButtons: [
        { label: "Close", className: "btn btn-secondary", onClick: (close) => close() },
        {
          label: "Save Changes",
          className: "btn btn-primary",
          onClick: async (close) => {
            clear(errSlot);
            try {
              const payload = {
                startsAt: toIsoUtc(startsInput.value),
                visitType: typeSelect.value
              };
              if (role !== "patient") payload.status = statusSelect.value;
              await api.updateAppointment(apptId, payload);
              close();
              showToast("Appointment updated.");
              await loadAppointments(false);
            } catch (err) {
              errSlot.appendChild(alertBanner(err.message, "error"));
            }
          }
        }
      ]
    });
  }

  async function viewAppointmentDetails(apptId) {
    try {
      const res = await api.getAppointment(apptId);
      const item = res.data?.appointment || res.data || {};
      openModal({
        title: "Appointment Summary",
        body: [
          el("div", { className: "field" }, [el("span", { className: "field-label", text: "Clinician" }), el("div", { text: item.clinicianName || clinicianLabel(item.clinician_id) })]),
          el("div", { className: "field" }, [el("span", { className: "field-label", text: "Scheduled Time" }), el("div", { className: "tabular-nums", text: formatDateTime(item.startsAt || item.starts_at) })]),
          el("div", { className: "field" }, [el("span", { className: "field-label", text: "Visit Type" }), el("div", { text: visitLabel(item.visitType || item.visit_type) })]),
          el("div", { className: "field" }, [el("span", { className: "field-label", text: "Status" }), statusBadge(statusLabel(item.status || "BOOKED"), statusVariant(item.status || "BOOKED"))])
        ],
        footerButtons: [{ label: "Close", className: "btn btn-secondary", onClick: (close) => close() }]
      });
    } catch (err) {
      showToast(err.message, "error");
    }
  }

  function renderList() {
    clear(listPanel);

    const statusFilter = el("select", {
      id: "filter-appt-status",
      name: "filter_status",
      className: "select",
      "aria-label": "Filter by status",
      onChange: (e) => {
        filterStatus = e.target.value;
        loadAppointments(false);
      }
    }, [
      el("option", { value: "", selected: filterStatus === "", text: "All Statuses" }),
      ...ALLOWED_APPOINTMENT_STATUSES.map((s) => el("option", { value: s, selected: filterStatus === s, text: statusLabel(s) }))
    ]);

    const dayFilter = el("input", {
      id: "filter-appt-day",
      name: "filter_day",
      type: "date",
      className: "input tabular-nums",
      "aria-label": "Filter by date",
      value: filterDay,
      onChange: (e) => {
        filterDay = e.target.value;
        loadAppointments(false);
      }
    });

    const filterBar = el("div", { className: "filter-bar" }, [statusFilter, dayFilter]);

    if (role !== "patient") {
      const patientSearch = el("input", {
        id: "filter-appt-patient",
        name: "filter_patient",
        type: "search",
        className: "input",
        "aria-label": "Filter by patient",
        placeholder: "Patient ID...",
        value: filterPatient,
        onChange: (e) => {
          filterPatient = e.target.value.trim();
          loadAppointments(false);
        }
      });
      filterBar.appendChild(patientSearch);
    }

    listPanel.appendChild(el("div", { className: "panel-header" }, [
      el("div", {}, [
        el("h3", { className: "panel-title", text: "Scheduled Visits" }),
        el("p", { className: "panel-sub", text: "Manage upcoming appointments, rescheduling, and cancellations" })
      ]),
      filterBar
    ]));

    if (appointments.length === 0) {
      listPanel.appendChild(emptyState("No appointments found", "Adjust your filters or schedule a new appointment.", "calendar"));
      return;
    }

    const tbody = el("tbody");
    for (const appt of appointments) {
      const apptId = appt.appointment_id || appt.id;
      const status = appt.status || "BOOKED";
      const actions = el("div", { style: "display:flex;justify-content:flex-end;gap:0.4rem;" }, [
        el("button", {
          type: "button",
          className: "btn btn-ghost btn-sm",
          text: "Details",
          onClick: () => viewAppointmentDetails(apptId)
        })
      ]);

      if (status !== "CANCELLED") {
        actions.appendChild(el("button", {
          type: "button",
          className: "btn btn-secondary btn-sm",
          text: "Edit",
          onClick: () => openRescheduleModal(appt)
        }));
      }
      if (status === "BOOKED") {
        actions.appendChild(el("button", {
          type: "button",
          className: "btn btn-danger btn-sm",
          text: "Cancel",
          onClick: () => {
            openModal({
              title: "Cancel Appointment",
              body: [el("p", { style: "margin:0;", text: "Are you sure you want to cancel this scheduled appointment?" })],
              footerButtons: [
                { label: "Keep Appointment", className: "btn btn-secondary", onClick: (close) => close() },
                {
                  label: "Confirm Cancellation",
                  className: "btn btn-danger-solid",
                  onClick: async (close) => {
                    try {
                      await api.cancelAppointment(apptId);
                      close();
                      showToast("Appointment cancelled.");
                      await loadAppointments(false);
                    } catch (err) {
                      showToast(err.message, "error");
                    }
                  }
                }
              ]
            });
          }
        }));
      }

      const cells = [
        el("td", { className: "tabular-nums", text: formatDateTime(appt.startsAt || appt.starts_at) }),
        el("td", { text: appt.clinicianName || clinicianLabel(appt.clinician_id) })
      ];
      if (role !== "patient") {
        cells.push(el("td", { className: "mono", text: appt.patient_id || appt.patientId || "—" }));
      }
      cells.push(el("td", { text: visitLabel(appt.visitType || appt.visit_type) }));
      cells.push(el("td", {}, [statusBadge(statusLabel(status), statusVariant(status))]));
      cells.push(el("td", { style: "text-align:right;" }, [actions]));

      tbody.appendChild(el("tr", {}, cells));
    }

    const headers = [el("th", { text: "Date & Time" }), el("th", { text: "Clinician" })];
    if (role !== "patient") headers.push(el("th", { text: "Patient" }));
    headers.push(el("th", { text: "Visit Type" }), el("th", { text: "Status" }), el("th", { style: "text-align:right;", text: "Actions" }));

    listPanel.appendChild(el("div", { className: "table-wrap" }, [
      el("table", { className: "data-table" }, [
        el("thead", {}, [el("tr", {}, headers)]),
        tbody
      ])
    ]));

    if (nextToken) {
      listPanel.appendChild(el("div", { className: "panel-body", style: "display:flex;justify-content:center;" }, [
        el("button", {
          type: "button",
          className: "btn btn-secondary btn-sm",
          text: "Load More Visits",
          onClick: () => loadAppointments(true)
        })
      ]));
    }
  }

  function renderBookingForm() {
    clear(formPanel);
    formPanel.appendChild(el("div", { className: "panel-header" }, [
      el("div", {}, [
        el("h3", { className: "panel-title", text: "Schedule a New Visit" }),
        el("p", { className: "panel-sub", text: "Select an attending clinician, visit type, and preferred time" })
      ])
    ]));

    if (role === "patient" && clinicians.length === 0) {
      const retryBtn = el("button", {
        type: "button",
        className: "btn btn-secondary btn-sm",
        text: cliniciansLoadFailed ? "Retry" : "Refresh",
        onClick: async () => {
          retryBtn.disabled = true;
          await loadClinicians();
          renderBookingForm();
        }
      });
      const msg = cliniciansLoadFailed
        ? "Clinician availability could not be loaded. Please try again in a moment."
        : "No clinicians are currently available for booking. Please check back later or contact care@outship.me.";
      formPanel.appendChild(el("div", { className: "panel-body", style: "display:flex;flex-direction:column;gap:0.875rem;" }, [
        alertBanner(msg, cliniciansLoadFailed ? "warn" : "info"),
        el("div", {}, [retryBtn])
      ]));
      return;
    }

    const formErr = el("div");
    const patientInput = el("input", { id: "appt-patient-id", name: "patient_id", type: "text", className: "input", placeholder: "Patient identifier" });

    const clinicianInput = clinicians.length > 0
      ? el("select", { id: "appt-clinician-id", name: "clinician_id", className: "select" }, clinicians.map((c) => {
          const id = c.clinician_id || c.id;
          return el("option", { value: id, text: clinicianLabel(id) });
        }))
      : el("input", { id: "appt-clinician-id", name: "clinician_id", type: "text", className: "input", placeholder: "Clinician ID" });

    const visitTypeSelect = el("select", { id: "appt-visit-type", name: "visitType", className: "select" }, ALLOWED_VISIT_TYPES.map((vt) =>
      el("option", { value: vt, text: visitLabel(vt) })
    ));

    const tomorrow = new Date(Date.now() + 24 * 3600 * 1000);
    tomorrow.setMinutes(0, 0, 0);
    const dtInput = el("input", {
      id: "appt-starts-at",
      name: "startsAt",
      type: "datetime-local",
      className: "input tabular-nums",
      value: toLocalInputValue(tomorrow.toISOString())
    });

    const submitBtn = el("button", { type: "submit", className: "btn btn-primary", text: "Confirm Appointment" });

    const fields = [formErr];
    if (needsPatientField) {
      fields.push(el("div", { className: "field" }, [
        el("label", { className: "field-label", for: "appt-patient-id", text: "Patient ID" }),
        patientInput
      ]));
    }
    fields.push(
      el("div", { className: "field" }, [el("label", { className: "field-label", for: "appt-clinician-id", text: "Attending Clinician" }), clinicianInput]),
      el("div", { className: "field" }, [el("label", { className: "field-label", for: "appt-visit-type", text: "Visit Type" }), visitTypeSelect]),
      el("div", { className: "field" }, [el("label", { className: "field-label", for: "appt-starts-at", text: "Preferred Date & Time" }), dtInput]),
      submitBtn
    );

    const form = el("form", {
      className: "panel-body",
      style: "display:flex;flex-direction:column;gap:1rem;",
      onSubmit: async (e) => {
        e.preventDefault();
        clear(formErr);
        submitBtn.disabled = true;
        try {
          await api.createAppointment(
            {
              patient_id: needsPatientField ? patientInput.value : undefined,
              clinician_id: clinicianInput.value,
              startsAt: toIsoUtc(dtInput.value),
              visitType: visitTypeSelect.value
            },
            { requirePatientId: role === "staff" || role === "admin" }
          );
          showToast("Appointment scheduled.");
          await loadAppointments(false);
        } catch (err) {
          formErr.appendChild(alertBanner(err.message, "error"));
        } finally {
          submitBtn.disabled = false;
        }
      }
    }, fields);

    formPanel.appendChild(form);
  }

  await loadClinicians();
  renderBookingForm();
  await loadAppointments(false);
}
