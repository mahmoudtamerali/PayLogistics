import { extractList, extractNextToken } from "../api.js";
import { alertBanner, clear, el, emptyState, formatDateTime, showToast } from "../ui.js";

export async function renderNotesView(container, ctx) {
  clear(container);
  const { api, session } = ctx;
  const role = session.role;

  if (role !== "patient" && role !== "doctor") {
    container.appendChild(
      el("section", { className: "panel" }, [
        emptyState(
          "Clinical Notes Restricted",
          "Clinical encounter notes are visible solely to the patient and their assigned physicians.",
          "shield"
        )
      ])
    );
    return;
  }

  let selectedPatientId = "";
  let assignments = [];
  let notes = [];
  let selectedNote = null;
  let nextToken = null;

  const masterDetail = el("div", { className: "split-master-detail" });
  const listCol = el("section", { className: "panel" });
  const detailCol = el("div", { style: "display:flex;flex-direction:column;gap:1.25rem;" });
  masterDetail.appendChild(listCol);
  masterDetail.appendChild(detailCol);
  container.appendChild(masterDetail);

  async function loadPatientContext() {
    if (role !== "doctor") return;
    try {
      const res = await api.listAssignments();
      assignments = extractList(res.data, ["assignments"]);
      if (assignments.length > 0 && !selectedPatientId) {
        selectedPatientId = assignments[0].patient_id || assignments[0].patientId || "";
      }
    } catch (_err) {
      assignments = [];
    }
  }

  async function loadNotes(append = false) {
    try {
      const res = await api.listVisitNotes({
        patient_id: role === "doctor" ? selectedPatientId : undefined,
        limit: 25,
        nextToken: append ? nextToken : undefined
      });
      const rows = extractList(res.data, ["notes", "visitNotes"]);
      notes = append ? [...notes, ...rows] : rows;
      nextToken = extractNextToken(res.data);
      if (!selectedNote && notes.length > 0) {
        selectedNote = notes[0];
      }
      renderListColumn();
      renderDetailColumn();
    } catch (err) {
      clear(listCol);
      listCol.appendChild(el("div", { className: "panel-body" }, [alertBanner(err.message, "error")]));
      renderDetailColumn();
    }
  }

  async function selectNote(noteSummary) {
    const pid = noteSummary.patient_id || noteSummary.patientId || selectedPatientId || session.claims.sub;
    const nid = noteSummary.note_id || noteSummary.noteId || noteSummary.id;
    if (pid && nid) {
      try {
        const res = await api.getVisitNote(pid, nid);
        selectedNote = res.data?.note && typeof res.data.note === "object" ? res.data.note : (res.data || noteSummary);
      } catch (_err) {
        selectedNote = noteSummary;
      }
    } else {
      selectedNote = noteSummary;
    }
    renderListColumn();
    renderDetailColumn();
  }

  function renderListColumn() {
    clear(listCol);
    const headerChildren = [
      el("div", {}, [
        el("h3", { className: "panel-title", text: "Consultation Notes" }),
        el("p", { className: "panel-sub", text: role === "doctor" ? "Select a patient to view or add notes" : "Summaries signed by your doctor" })
      ])
    ];
    listCol.appendChild(el("div", { className: "panel-header" }, headerChildren));

    const body = el("div", { className: "panel-body", style: "display:flex;flex-direction:column;gap:0.875rem;" });

    if (role === "doctor") {
      if (assignments.length > 0) {
        const sel = el("select", {
          id: "notes-patient-select",
          name: "patient_id",
          className: "select",
          onChange: (e) => {
            selectedPatientId = e.target.value;
            selectedNote = null;
            loadNotes(false);
          }
        }, assignments.map((a) => {
          const pid = a.patient_id || a.patientId;
          const label = a.patientName || a.fullName || pid;
          return el("option", { value: pid, selected: pid === selectedPatientId, text: label });
        }));
        body.appendChild(el("div", { className: "field" }, [
          el("label", { className: "field-label", for: "notes-patient-select", text: "Patient" }),
          sel
        ]));
      } else {
        const pidInput = el("input", {
          id: "notes-patient-id",
          name: "patient_id",
          type: "text",
          className: "input",
          placeholder: "Patient ID",
          value: selectedPatientId,
          onChange: (e) => {
            selectedPatientId = e.target.value.trim();
            selectedNote = null;
            loadNotes(false);
          }
        });
        body.appendChild(el("div", { className: "field" }, [
          el("label", { className: "field-label", for: "notes-patient-id", text: "Patient ID" }),
          pidInput
        ]));
      }
    }

    if (notes.length === 0) {
      body.appendChild(emptyState("No visit notes yet", "Consultation summaries will appear here after your appointment.", "notes"));
    } else {
      const stack = el("div", { className: "list-stack" });
      for (const n of notes) {
        const nid = n.note_id || n.noteId || n.id;
        const activeId = selectedNote ? (selectedNote.note_id || selectedNote.noteId || selectedNote.id) : null;
        const isSel = activeId && nid === activeId;
        stack.appendChild(el("button", {
          type: "button",
          className: `list-item-btn ${isSel ? "selected" : ""}`,
          onClick: () => selectNote(n)
        }, [
          el("div", { style: "font-weight:600;font-size:0.875rem;", text: n.title || "Visit Summary" }),
          el("div", {
            className: "tabular-nums",
            style: "font-size:0.75rem;color:var(--sc-muted);margin-top:0.25rem;",
            text: formatDateTime(n.created_at || n.createdAt || n.visit_date)
          })
        ]));
      }
      body.appendChild(stack);
      if (nextToken) {
        body.appendChild(el("button", {
          type: "button",
          className: "btn btn-secondary btn-sm",
          text: "Load Older Notes",
          onClick: () => loadNotes(true)
        }));
      }
    }

    listCol.appendChild(body);
  }

  function renderDetailColumn() {
    clear(detailCol);

    const readerPanel = el("section", { className: "panel" });
    if (!selectedNote) {
      readerPanel.appendChild(emptyState("Select a Visit Note", "Choose a consultation summary from the list to read the full record.", "notes"));
    } else {
      const noteText = selectedNote.note || selectedNote.text || "";
      const author = selectedNote.clinicianName || selectedNote.author_name || selectedNote.author_id || "Attending Physician";
      readerPanel.appendChild(el("div", { className: "panel-header" }, [
        el("div", {}, [
          el("h3", { className: "panel-title serif-heading", style: "font-size:1.25rem;", text: selectedNote.title || "Clinical Visit Note" }),
          el("p", {
            className: "panel-sub tabular-nums",
            text: `${author} · ${formatDateTime(selectedNote.created_at || selectedNote.createdAt || selectedNote.visit_date)}`
          })
        ])
      ]));
      readerPanel.appendChild(el("div", { className: "panel-body" }, [
        el("p", { style: "margin:0;white-space:pre-wrap;line-height:1.7;color:var(--sc-text-secondary);", text: noteText })
      ]));
    }
    detailCol.appendChild(readerPanel);

    if (role === "doctor") {
      const composerPanel = el("section", { className: "panel" }, [
        el("div", { className: "panel-header" }, [
          el("div", {}, [
            el("h3", { className: "panel-title", text: "New Consultation Note" }),
            el("p", { className: "panel-sub", text: "Add consultation findings and follow-up instructions for this patient" })
          ])
        ])
      ]);

      const errBox = el("div");
      const titleInput = el("input", { id: "note-title", name: "title", type: "text", className: "input", maxlength: "120", placeholder: "Visit summary title" });
      const bodyInput = el("textarea", { id: "note-body", name: "note", className: "textarea", rows: "5", maxlength: "5000", placeholder: "Enter clinical observations and follow-up recommendations..." });
      const countHint = el("span", { className: "field-hint tabular-nums", text: "0 / 5000 characters" });
      bodyInput.addEventListener("input", () => {
        countHint.textContent = `${bodyInput.value.length} / 5000 characters`;
      });

      const submitBtn = el("button", { type: "submit", className: "btn btn-primary", text: "Save Clinical Note" });

      composerPanel.appendChild(el("form", {
        className: "panel-body",
        style: "display:flex;flex-direction:column;gap:1rem;",
        onSubmit: async (e) => {
          e.preventDefault();
          clear(errBox);
          if (!selectedPatientId) {
            errBox.appendChild(alertBanner("Select or enter an assigned patient ID first.", "warn"));
            return;
          }
          submitBtn.disabled = true;
          try {
            await api.createVisitNote(selectedPatientId, {
              title: titleInput.value,
              note: bodyInput.value
            });
            titleInput.value = "";
            bodyInput.value = "";
            countHint.textContent = "0 / 5000 characters";
            showToast("Clinical note saved.");
            await loadNotes(false);
          } catch (err) {
            errBox.appendChild(alertBanner(err.message, "error"));
          } finally {
            submitBtn.disabled = false;
          }
        }
      }, [
        errBox,
        el("div", { className: "field" }, [el("label", { className: "field-label", for: "note-title", text: "Title" }), titleInput]),
        el("div", { className: "field" }, [el("label", { className: "field-label", for: "note-body", text: "Clinical Observations" }), bodyInput, countHint]),
        el("div", { style: "display:flex;justify-content:flex-end;" }, [submitBtn])
      ]));
      detailCol.appendChild(composerPanel);
    }
  }

  await loadPatientContext();
  await loadNotes(false);
}
