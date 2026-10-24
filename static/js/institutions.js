/**
 * Institutions Directory & Management Client.
 * Task A5: Handles institution listing, filtering, detail panel, programmes, and intake capacities.
 */
(() => {
  "use strict";

  let currentPage = 1;
  let selectedInstitutionId = null;
  let currentDetailData = null;

  document.addEventListener("DOMContentLoaded", () => {
    const filterForm = document.getElementById("institutions-filter-form");
    const resetBtn = document.getElementById("reset-filters-btn");
    const openAddInstBtn = document.getElementById("open-add-institution-btn");
    const addInstDialog = document.getElementById("add-institution-dialog");
    const editInstDialog = document.getElementById("edit-institution-dialog");
    const addProgDialog = document.getElementById("add-programme-dialog");
    const capDialog = document.getElementById("capacity-dialog");
    const detailPanel = document.getElementById("institution-detail-panel");
    const detailCloseBtn = document.getElementById("detail-close-btn");

    // Close buttons on all native dialogs
    document.querySelectorAll(".dialog-cancel-btn").forEach(btn => {
      btn.addEventListener("click", (e) => {
        const dialog = e.target.closest("dialog");
        if (dialog) dialog.close();
      });
    });

    if (detailCloseBtn) {
      detailCloseBtn.addEventListener("click", () => {
        if (detailPanel) detailPanel.style.display = "none";
        selectedInstitutionId = null;
      });
    }

    // Filter Form
    if (filterForm) {
      filterForm.addEventListener("submit", (e) => {
        e.preventDefault();
        currentPage = 1;
        loadInstitutions();
      });
    }

    if (resetBtn) {
      resetBtn.addEventListener("click", () => {
        if (filterForm) filterForm.reset();
        currentPage = 1;
        loadInstitutions();
      });
    }

    // Add Institution
    if (openAddInstBtn && addInstDialog) {
      openAddInstBtn.addEventListener("click", () => {
        const err = document.getElementById("add-inst-error");
        if (err) { err.style.display = "none"; err.textContent = ""; }
        const form = document.getElementById("add-institution-form");
        if (form) form.reset();
        addInstDialog.showModal();
      });
    }

    const addInstForm = document.getElementById("add-institution-form");
    if (addInstForm) {
      addInstForm.addEventListener("submit", async (e) => {
        e.preventDefault();
        const errBox = document.getElementById("add-inst-error");
        if (errBox) errBox.style.display = "none";

        const payload = {
          code: document.getElementById("add-inst-code").value.trim(),
          name: document.getElementById("add-inst-name").value.trim(),
          city: document.getElementById("add-inst-city").value.trim(),
          state: document.getElementById("add-inst-state").value.trim(),
          status: document.getElementById("add-inst-status").value
        };

        try {
          await window.Nexus.api("/api/institutions", {
            method: "POST",
            body: payload
          });
          window.Nexus.toast("Institution created successfully.", "ok");
          addInstDialog.close();
          loadInstitutions();
        } catch (err) {
          if (errBox) {
            errBox.textContent = err.message || "Failed to create institution.";
            errBox.style.display = "block";
          }
          window.Nexus.toast(err.message, "danger");
        }
      });
    }

    // Edit Institution
    const editInstBtn = document.getElementById("detail-edit-inst-btn");
    if (editInstBtn && editInstDialog) {
      editInstBtn.addEventListener("click", () => {
        if (!currentDetailData?.institution) return;
        const inst = currentDetailData.institution;
        document.getElementById("edit-inst-id").value = inst.id;
        document.getElementById("edit-inst-code").value = inst.code;
        document.getElementById("edit-inst-name").value = inst.name;
        document.getElementById("edit-inst-city").value = inst.city;
        document.getElementById("edit-inst-state").value = inst.state;
        document.getElementById("edit-inst-status").value = inst.status;

        const errBox = document.getElementById("edit-inst-error");
        if (errBox) { errBox.style.display = "none"; errBox.textContent = ""; }
        editInstDialog.showModal();
      });
    }

    const editInstForm = document.getElementById("edit-institution-form");
    if (editInstForm) {
      editInstForm.addEventListener("submit", async (e) => {
        e.preventDefault();
        const instId = document.getElementById("edit-inst-id").value;
        const errBox = document.getElementById("edit-inst-error");
        if (errBox) errBox.style.display = "none";

        const payload = {
          code: document.getElementById("edit-inst-code").value.trim(),
          name: document.getElementById("edit-inst-name").value.trim(),
          city: document.getElementById("edit-inst-city").value.trim(),
          state: document.getElementById("edit-inst-state").value.trim(),
          status: document.getElementById("edit-inst-status").value
        };

        try {
          await window.Nexus.api(`/api/institutions/${instId}`, {
            method: "PATCH",
            body: payload
          });
          window.Nexus.toast("Institution updated successfully.", "ok");
          editInstDialog.close();
          loadInstitutions();
          loadInstitutionDetail(instId);
        } catch (err) {
          if (errBox) {
            errBox.textContent = err.message || "Failed to update institution.";
            errBox.style.display = "block";
          }
          window.Nexus.toast(err.message, "danger");
        }
      });
    }

    // Add Programme
    const addProgBtn = document.getElementById("detail-add-prog-btn");
    if (addProgBtn && addProgDialog) {
      addProgBtn.addEventListener("click", () => {
        if (!selectedInstitutionId) return;
        document.getElementById("add-prog-inst-id").value = selectedInstitutionId;
        const form = document.getElementById("add-programme-form");
        if (form) form.reset();
        const errBox = document.getElementById("add-prog-error");
        if (errBox) { errBox.style.display = "none"; errBox.textContent = ""; }
        addProgDialog.showModal();
      });
    }

    const addProgForm = document.getElementById("add-programme-form");
    if (addProgForm) {
      addProgForm.addEventListener("submit", async (e) => {
        e.preventDefault();
        const instId = document.getElementById("add-prog-inst-id").value;
        const errBox = document.getElementById("add-prog-error");
        if (errBox) errBox.style.display = "none";

        const payload = {
          code: document.getElementById("add-prog-code").value.trim(),
          name: document.getElementById("add-prog-name").value.trim(),
          level: document.getElementById("add-prog-level").value
        };

        try {
          await window.Nexus.api(`/api/institutions/${instId}/programmes`, {
            method: "POST",
            body: payload
          });
          window.Nexus.toast("Programme added successfully.", "ok");
          addProgDialog.close();
          loadInstitutionDetail(instId);
        } catch (err) {
          if (errBox) {
            errBox.textContent = err.message || "Failed to add programme.";
            errBox.style.display = "block";
          }
          window.Nexus.toast(err.message, "danger");
        }
      });
    }

    // Add / Edit Capacity
    const addCapBtn = document.getElementById("detail-add-cap-btn");
    if (addCapBtn && capDialog) {
      addCapBtn.addEventListener("click", () => {
        if (!selectedInstitutionId || !currentDetailData) return;
        openCapacityDialog(null);
      });
    }

    const capForm = document.getElementById("capacity-form");
    if (capForm) {
      capForm.addEventListener("submit", async (e) => {
        e.preventDefault();
        const instId = document.getElementById("cap-inst-id").value;
        const capId = document.getElementById("cap-id").value;
        const errBox = document.getElementById("cap-error");
        if (errBox) errBox.style.display = "none";

        const payload = {
          academic_year: document.getElementById("cap-academic-year").value.trim(),
          approved_regular_intake: parseInt(document.getElementById("cap-reg-intake").value, 10),
          supernumerary_capacity: parseInt(document.getElementById("cap-super-intake").value, 10)
        };

        const progSelect = document.getElementById("cap-programme-id");
        if (!capId && progSelect) {
          payload.programme_id = progSelect.value;
        }

        try {
          if (capId) {
            await window.Nexus.api(`/api/institutions/${instId}/capacities/${capId}`, {
              method: "PATCH",
              body: payload
            });
            window.Nexus.toast("Capacity updated successfully.", "ok");
          } else {
            await window.Nexus.api(`/api/institutions/${instId}/capacities`, {
              method: "POST",
              body: payload
            });
            window.Nexus.toast("Capacity configured successfully.", "ok");
          }
          capDialog.close();
          loadInstitutionDetail(instId);
        } catch (err) {
          if (errBox) {
            errBox.textContent = err.message || "Failed to save capacity.";
            errBox.style.display = "block";
          }
          window.Nexus.toast(err.message, "danger");
        }
      });
    }

    // Initial load
    loadInstitutions();
  });

  /** Load institutions list from API with active filters */
  async function loadInstitutions() {
    const tbody = document.getElementById("institutions-tbody");
    const countHelper = document.getElementById("institutions-count-helper");
    const paginationContainer = document.getElementById("institutions-pagination");

    const q = document.getElementById("filter-q")?.value.trim() || "";
    const state = document.getElementById("filter-state")?.value || "";
    const status = document.getElementById("filter-status")?.value || "";

    try {
      const res = await window.Nexus.api("/api/institutions", {
        params: { q, state, status, page: currentPage, per_page: 25 }
      });

      const institutions = res?.data || [];
      const meta = res?.meta || { total: institutions.length, page: 1, per_page: 25 };

      if (countHelper) {
        countHelper.textContent = `Showing ${institutions.length} of ${meta.total} participating institutions.`;
      }

      if (!tbody) return;
      tbody.innerHTML = "";

      if (institutions.length === 0) {
        tbody.innerHTML = `<tr><td colspan="6" class="muted" style="text-align: center; padding: 24px;">No institutions found matching criteria.</td></tr>`;
        if (paginationContainer) paginationContainer.innerHTML = "";
        return;
      }

      institutions.forEach(inst => {
        const tr = document.createElement("tr");
        tr.style.cursor = "pointer";
        if (inst.id === selectedInstitutionId) {
          tr.style.background = "var(--info-bg)";
        }

        const statusKind = inst.status === "ACTIVE" ? "ok" : "muted";

        tr.innerHTML = `
          <td><strong>${escapeHtml(inst.code)}</strong></td>
          <td>${escapeHtml(inst.name)}</td>
          <td>${escapeHtml(inst.city)}, ${escapeHtml(inst.state)}</td>
          <td style="text-align: right;">${inst.programmes_count || 0}</td>
          <td><span class="badge badge-${statusKind}">${escapeHtml(inst.status)}</span></td>
          <td>
            <button type="button" class="btn btn-secondary view-inst-btn" data-id="${inst.id}" style="min-height: 28px; padding: 2px 8px; font-size: 0.85rem;">
              View
            </button>
          </td>
        `;

        tr.addEventListener("click", (e) => {
          if (e.target.tagName !== "BUTTON") {
            loadInstitutionDetail(inst.id);
          }
        });

        const viewBtn = tr.querySelector(".view-inst-btn");
        if (viewBtn) {
          viewBtn.addEventListener("click", () => loadInstitutionDetail(inst.id));
        }

        tbody.appendChild(tr);
      });

      // Render pagination
      if (paginationContainer && meta.total > meta.per_page) {
        window.Nexus.pagination(paginationContainer, meta, (newPage) => {
          currentPage = newPage;
          loadInstitutions();
        });
      } else if (paginationContainer) {
        paginationContainer.innerHTML = "";
      }

    } catch (err) {
      if (tbody) {
        tbody.innerHTML = `<tr><td colspan="6" class="field-error" style="text-align: center; padding: 20px;">Error loading institutions: ${escapeHtml(err.message)}</td></tr>`;
      }
      window.Nexus.toast(err.message, "danger");
    }
  }

  /** Load detailed data for a specific institution into the side panel */
  async function loadInstitutionDetail(instId) {
    selectedInstitutionId = instId;
    const panel = document.getElementById("institution-detail-panel");
    if (!panel) return;

    panel.style.display = "block";
    panel.scrollIntoView({ behavior: "smooth", block: "nearest" });

    // Highlight row in table
    document.querySelectorAll("#institutions-tbody tr").forEach(row => {
      row.style.background = "";
    });

    try {
      const res = await window.Nexus.api(`/api/institutions/${instId}`);
      currentDetailData = res?.data;

      const inst = currentDetailData.institution;
      const programmes = currentDetailData.programmes || [];
      const summary = currentDetailData.intake_summary || {};

      document.getElementById("detail-code").textContent = inst.code;
      document.getElementById("detail-name").textContent = inst.name;
      document.getElementById("detail-location").textContent = `${inst.city}, ${inst.state} (${inst.status})`;

      document.getElementById("detail-reg-capacity").textContent = summary.total_approved_regular || 0;
      document.getElementById("detail-super-capacity").textContent = summary.total_supernumerary || 0;

      const progContainer = document.getElementById("detail-programmes-list");
      if (!progContainer) return;

      if (programmes.length === 0) {
        progContainer.innerHTML = `<p class="muted" style="padding: 12px; background: var(--bg); border-radius: var(--radius);">No programmes configured yet.</p>`;
        return;
      }

      progContainer.innerHTML = "";
      programmes.forEach(prog => {
        const progCard = document.createElement("div");
        progCard.className = "card";
        progCard.style.padding = "14px";
        progCard.style.background = "var(--bg)";

        const capacities = prog.capacities || [];
        let capTableHtml = "";

        if (capacities.length > 0) {
          capTableHtml = `
            <table class="table" style="margin-top: 8px; font-size: 0.85rem;">
              <thead>
                <tr>
                  <th>Academic Year</th>
                  <th style="text-align: right;">Regular Intake</th>
                  <th style="text-align: right;">Supernumerary</th>
                  <th>Action</th>
                </tr>
              </thead>
              <tbody>
                ${capacities.map(c => `
                  <tr>
                    <td><strong>${escapeHtml(c.academic_year)}</strong></td>
                    <td style="text-align: right;">${c.approved_regular_intake}</td>
                    <td style="text-align: right;">${c.supernumerary_capacity}</td>
                    <td>
                      <button type="button" class="btn btn-secondary edit-cap-btn" data-cap-id="${c.id}" data-prog-id="${prog.id}" data-year="${c.academic_year}" data-reg="${c.approved_regular_intake}" data-super="${c.supernumerary_capacity}" style="min-height: 24px; padding: 1px 6px; font-size: 0.8rem;">
                        Edit
                      </button>
                    </td>
                  </tr>
                `).join("")}
              </tbody>
            </table>
          `;
        } else {
          capTableHtml = `<p class="muted" style="font-size: 0.85rem; margin-top: 6px;">No annual capacity configured.</p>`;
        }

        progCard.innerHTML = `
          <div style="display: flex; justify-content: space-between; align-items: center;">
            <div>
              <strong>${escapeHtml(prog.code)}</strong> &mdash; ${escapeHtml(prog.name)}
              <span class="badge badge-info" style="margin-left: 6px;">${escapeHtml(prog.level)}</span>
            </div>
          </div>
          ${capTableHtml}
        `;

        // Wire Edit Capacity buttons
        progCard.querySelectorAll(".edit-cap-btn").forEach(btn => {
          btn.addEventListener("click", () => {
            openCapacityDialog({
              id: btn.dataset.capId,
              programme_id: btn.dataset.progId,
              academic_year: btn.dataset.year,
              approved_regular_intake: btn.dataset.reg,
              supernumerary_capacity: btn.dataset.super
            });
          });
        });

        progContainer.appendChild(progCard);
      });

    } catch (err) {
      window.Nexus.toast(err.message, "danger");
    }
  }

  /** Helper to open and populate the capacity dialog for Add or Edit */
  function openCapacityDialog(capData) {
    const dialog = document.getElementById("capacity-dialog");
    if (!dialog) return;

    const title = document.getElementById("capacity-dialog-title");
    const instIdInput = document.getElementById("cap-inst-id");
    const capIdInput = document.getElementById("cap-id");
    const progSelectRow = document.getElementById("cap-programme-select-row");
    const progSelect = document.getElementById("cap-programme-id");
    const yearInput = document.getElementById("cap-academic-year");
    const regInput = document.getElementById("cap-reg-intake");
    const superInput = document.getElementById("cap-super-intake");
    const errBox = document.getElementById("cap-error");

    if (errBox) { errBox.style.display = "none"; errBox.textContent = ""; }
    instIdInput.value = selectedInstitutionId;

    if (capData) {
      // Edit mode
      title.textContent = `Edit Capacity (${capData.academic_year})`;
      capIdInput.value = capData.id;
      progSelectRow.style.display = "none";
      yearInput.value = capData.academic_year;
      yearInput.readOnly = true;
      regInput.value = capData.approved_regular_intake;
      superInput.value = capData.supernumerary_capacity;
    } else {
      // Create mode
      title.textContent = "Configure Intake Capacity";
      capIdInput.value = "";
      progSelectRow.style.display = "block";
      yearInput.value = "2026-27";
      yearInput.readOnly = false;
      regInput.value = "120";
      superInput.value = "10";

      // Populate programmes dropdown
      if (progSelect && currentDetailData?.programmes) {
        progSelect.innerHTML = currentDetailData.programmes.map(p => `
          <option value="${p.id}">${escapeHtml(p.code)} - ${escapeHtml(p.name)}</option>
        `).join("");
      }
    }

    dialog.showModal();
  }

  function escapeHtml(str) {
    if (!str) return "";
    return String(str).replace(/[&<>"']/g, c => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
    })[c]);
  }
})();
