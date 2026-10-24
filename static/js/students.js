/**
 * Student Repository Client for APAAR Nexus.
 * Task A7: Repository table, search, filters, pagination, CSV export, and add student modal.
 */
(() => {
  "use strict";

  let currentPage = 1;
  const perPage = 25;

  document.addEventListener("DOMContentLoaded", () => {
    const filterForm = document.getElementById("students-filter-form");
    const resetBtn = document.getElementById("reset-filters-btn");
    const exportBtn = document.getElementById("export-csv-btn");
    const openAddBtn = document.getElementById("open-add-student-btn");
    const addDialog = document.getElementById("add-student-dialog");
    const addForm = document.getElementById("add-student-form");
    const instSelect = document.getElementById("add-student-institution");
    const progSelect = document.getElementById("add-student-programme");

    // Close button on dialog
    document.querySelectorAll(".dialog-cancel-btn").forEach(btn => {
      btn.addEventListener("click", (e) => {
        const dialog = e.target.closest("dialog");
        if (dialog) dialog.close();
      });
    });

    // Dynamic programme filtering by institution in Add modal
    if (instSelect && progSelect && instSelect.tagName === "SELECT") {
      instSelect.addEventListener("change", () => {
        const selectedInst = instSelect.value;
        Array.from(progSelect.options).forEach(opt => {
          if (!opt.value) return;
          const instMatch = opt.dataset.institution === selectedInst;
          opt.style.display = (!selectedInst || instMatch) ? "" : "none";
        });
        progSelect.value = "";
      });
    }

    // Filter Form submit
    if (filterForm) {
      filterForm.addEventListener("submit", (e) => {
        e.preventDefault();
        currentPage = 1;
        loadStudents();
      });
    }

    // Reset filters
    if (resetBtn) {
      resetBtn.addEventListener("click", () => {
        if (filterForm) filterForm.reset();
        currentPage = 1;
        loadStudents();
      });
    }

    // Export CSV
    if (exportBtn) {
      exportBtn.addEventListener("click", () => {
        const params = getFilterParams();
        const queryStr = new URLSearchParams(params).toString();
        window.location.assign(`/api/students/export?${queryStr}`);
      });
    }

    // Open Add Student Modal
    if (openAddBtn && addDialog) {
      openAddBtn.addEventListener("click", () => {
        const errBox = document.getElementById("add-student-error");
        if (errBox) errBox.style.display = "none";
        if (addForm) {
          addForm.reset();
          const dateInput = document.getElementById("add-student-admission-date");
          if (dateInput) dateInput.value = new Date().toISOString().split("T")[0];
          const yearInput = document.getElementById("add-student-academic-year");
          if (yearInput) yearInput.value = "2026-27";
        }
        addDialog.showModal();
      });
    }

    // Submit Add Student Form
    if (addForm) {
      addForm.addEventListener("submit", async (e) => {
        e.preventDefault();
        const errBox = document.getElementById("add-student-error");
        const errMsg = document.getElementById("add-student-error-msg");
        const errList = document.getElementById("add-student-error-list");
        if (errBox) errBox.style.display = "none";
        if (errList) errList.innerHTML = "";

        const submitBtn = document.getElementById("submit-add-student-btn");
        const originalText = submitBtn.textContent;
        submitBtn.disabled = true;
        submitBtn.textContent = "Saving...";

        const payload = {
          institution_student_id: document.getElementById("add-student-id").value.trim(),
          full_name: document.getElementById("add-student-name").value.trim(),
          apaar_ref: document.getElementById("add-student-apaar").value.trim(),
          academic_year: document.getElementById("add-student-academic-year").value.trim(),
          institution_id: document.getElementById("add-student-institution").value,
          programme_id: document.getElementById("add-student-programme").value,
          admission_category: document.getElementById("add-student-category").value,
          classification: document.getElementById("add-student-classification").value,
          admission_date: document.getElementById("add-student-admission-date").value,
          enrolment_status: document.getElementById("add-student-enrolment-status").value,
          is_nri: document.getElementById("add-student-nri").checked,
          is_oci: document.getElementById("add-student-oci").checked,
          is_foreign_national: document.getElementById("add-student-fn").checked,
          contact_ref: document.getElementById("add-student-contact").value.trim() || null,
          fee_payer_ref: document.getElementById("add-student-fee-payer").value.trim() || null
        };

        try {
          const res = await window.Nexus.api("/api/students", {
            method: "POST",
            body: payload
          });

          if (res && res.ok) {
            window.Nexus.toast("Student record created successfully.", "ok");
            if (addDialog) addDialog.close();
            currentPage = 1;
            loadStudents();
          } else {
            throw new Error(res?.error?.message || "Failed to create student.");
          }
        } catch (err) {
          if (errBox) {
            errBox.style.display = "block";
            errMsg.textContent = err.message || "Validation failed.";
            if (err.details && err.details.errors && Array.isArray(err.details.errors)) {
              err.details.errors.forEach(item => {
                const li = document.createElement("li");
                li.textContent = `${item.field || 'Field'}: ${item.message} (${item.code})`;
                errList.appendChild(li);
              });
            }
          }
          window.Nexus.toast(err.message || "Failed to save student.", "danger");
        } finally {
          submitBtn.disabled = false;
          submitBtn.textContent = originalText;
        }
      });
    }

    // Initial Load
    loadStudents();
  });

  function getFilterParams() {
    const params = { page: currentPage, per_page: perPage };
    const q = document.getElementById("filter-q")?.value.trim();
    if (q) params.q = q;

    const year = document.getElementById("filter-year")?.value;
    if (year) params.academic_year = year;

    const inst = document.getElementById("filter-institution")?.value;
    if (inst) params.institution_id = inst;

    const prog = document.getElementById("filter-programme")?.value;
    if (prog) params.programme_id = prog;

    const cat = document.getElementById("filter-category")?.value;
    if (cat) params.category = cat;

    const valStatus = document.getElementById("filter-val-status")?.value;
    if (valStatus) params.validation_status = valStatus;

    return params;
  }

  async function loadStudents() {
    const tbody = document.getElementById("students-table-body");
    const info = document.getElementById("pagination-info");
    const controls = document.getElementById("pagination-controls");

    if (tbody) {
      tbody.innerHTML = `<tr><td colspan="10" style="text-align: center; color: var(--muted); padding: 24px;">Loading student records...</td></tr>`;
    }

    try {
      const params = getFilterParams();
      const res = await window.Nexus.api("/api/students", { params });

      if (!res || !res.ok) {
        throw new Error(res?.error?.message || "Failed loading students.");
      }

      const rows = res.data || [];
      const meta = res.meta || { page: 1, per_page: perPage, total: 0, pages: 1 };

      if (!rows.length) {
        tbody.innerHTML = `<tr><td colspan="10" style="text-align: center; color: var(--muted); padding: 24px;">No student records found matching criteria.</td></tr>`;
        if (info) info.textContent = "Showing 0 records";
        if (controls) controls.innerHTML = "";
        return;
      }

      tbody.innerHTML = "";
      rows.forEach(st => {
        const tr = document.createElement("tr");

        // Status badge variants
        const valVariant = st.validation_status === "VALID" ? "ok" : (st.validation_status === "FLAGGED" ? "warn" : (st.validation_status === "INVALID" ? "danger" : "muted"));
        const enrolVariant = st.enrolment_status === "ACTIVE" ? "ok" : (st.enrolment_status === "GRADUATED" ? "info" : "warn");

        tr.innerHTML = `
          <td><strong><a href="/students/${st.id}" style="color: var(--navy); text-decoration: underline;">${window.Nexus.escapeHTML(st.institution_student_id)}</a></strong></td>
          <td>${window.Nexus.escapeHTML(st.full_name)}</td>
          <td><code style="font-size: 12px; background: #edf2f7; padding: 2px 6px; border-radius: 4px;">${window.Nexus.escapeHTML(st.apaar_ref || '—')}</code></td>
          <td><span title="${window.Nexus.escapeHTML(st.institution_name)}">${window.Nexus.escapeHTML(st.institution_code || '—')}</span></td>
          <td><span title="${window.Nexus.escapeHTML(st.programme_name)}">${window.Nexus.escapeHTML(st.programme_code || '—')}</span></td>
          <td>${window.Nexus.escapeHTML(st.academic_year)}</td>
          <td><span class="badge badge-muted">${window.Nexus.escapeHTML(st.admission_category)} (${window.Nexus.escapeHTML(st.classification === 'REGULAR' ? 'REG' : 'SUP')})</span></td>
          <td><span class="badge badge-${valVariant}">${window.Nexus.escapeHTML(st.validation_status)}</span></td>
          <td><span class="badge badge-${enrolVariant}">${window.Nexus.escapeHTML(st.enrolment_status)}</span></td>
          <td style="text-align: right;">
            <a href="/students/${st.id}" class="btn btn-secondary" style="padding: 4px 10px; font-size: 12px;">View</a>
          </td>
        `;
        tbody.appendChild(tr);
      });

      const start = (meta.page - 1) * meta.per_page + 1;
      const end = Math.min(meta.page * meta.per_page, meta.total);
      if (info) {
        info.textContent = `Showing ${start}–${end} of ${meta.total} records`;
      }

      renderPagination(meta, controls);

    } catch (err) {
      if (tbody) {
        tbody.innerHTML = `<tr><td colspan="10" style="text-align: center; color: var(--danger); padding: 24px;">${window.Nexus.escapeHTML(err.message || "Failed loading records.")}</td></tr>`;
      }
      window.Nexus.toast(err.message || "Failed loading student records.", "danger");
    }
  }

  function renderPagination(meta, container) {
    if (!container) return;
    container.innerHTML = "";

    const prevBtn = document.createElement("button");
    prevBtn.type = "button";
    prevBtn.className = "btn btn-secondary";
    prevBtn.style.padding = "4px 10px";
    prevBtn.style.fontSize = "12px";
    prevBtn.textContent = "Previous";
    prevBtn.disabled = meta.page <= 1;
    prevBtn.addEventListener("click", () => {
      if (currentPage > 1) {
        currentPage--;
        loadStudents();
      }
    });

    const pageIndicator = document.createElement("span");
    pageIndicator.style.fontSize = "13px";
    pageIndicator.style.padding = "0 8px";
    pageIndicator.style.color = "var(--muted)";
    pageIndicator.textContent = `Page ${meta.page} of ${meta.pages}`;

    const nextBtn = document.createElement("button");
    nextBtn.type = "button";
    nextBtn.className = "btn btn-secondary";
    nextBtn.style.padding = "4px 10px";
    nextBtn.style.fontSize = "12px";
    nextBtn.textContent = "Next";
    nextBtn.disabled = meta.page >= meta.pages;
    nextBtn.addEventListener("click", () => {
      if (currentPage < meta.pages) {
        currentPage++;
        loadStudents();
      }
    });

    container.appendChild(prevBtn);
    container.appendChild(pageIndicator);
    container.appendChild(nextBtn);
  }
})();
