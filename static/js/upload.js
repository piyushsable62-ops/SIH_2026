/**
 * CSV Upload Wizard & Ingestion Pipeline Client.
 * Task A8: 3-step wizard (File select, Preview & Validation, Commit & Results) + Ingestion history.
 */
(() => {
  "use strict";

  let currentSubmissionId = null;
  let currentFile = null;

  document.addEventListener("DOMContentLoaded", () => {
    const previewForm = document.getElementById("csv-preview-form");
    const previewBtn = document.getElementById("preview-upload-btn");
    const backBtn = document.getElementById("back-to-step1-btn");
    const commitBtn = document.getElementById("confirm-commit-btn");
    const uploadAnotherBtn = document.getElementById("upload-another-btn");
    const refreshHistoryBtn = document.getElementById("refresh-history-btn");

    // CSRF token helper
    const getCsrfToken = () => document.querySelector('meta[name="csrf-token"]')?.content || "";

    // Step 1: Submit File for Preview
    if (previewForm) {
      previewForm.addEventListener("submit", async (e) => {
        e.preventDefault();
        const fileInput = document.getElementById("csv-file-input");
        const instInput = document.getElementById("upload-institution");

        if (!fileInput.files.length) {
          window.Nexus.toast("Please select a CSV file.", "warn");
          return;
        }

        const file = fileInput.files[0];
        currentFile = file;

        previewBtn.disabled = true;
        previewBtn.textContent = "Parsing & Validating...";

        const formData = new FormData();
        formData.append("mode", "preview");
        formData.append("file", file);
        if (instInput && instInput.value) {
          formData.append("institution_id", instInput.value);
        }

        try {
          const resp = await fetch("/api/students/import", {
            method: "POST",
            headers: {
              "X-CSRFToken": getCsrfToken(),
              "Accept": "application/json"
            },
            body: formData
          });

          const data = await resp.json();
          if (!resp.ok || !data.ok) {
            throw new Error(data?.error?.message || "Preview failed.");
          }

          const res = data.data;

          // Check if already imported (Idempotency)
          if (res.already_imported) {
            window.Nexus.toast("This exact file was already committed for this institution.", "warn");
            showStep3({
              reference: res.reference,
              status: res.status,
              inserted_count: res.valid_rows,
              updated_count: 0,
              error_count: res.error_rows,
              submission_id: res.submission_id,
              is_duplicate: true
            });
            loadHistory();
            return;
          }

          currentSubmissionId = res.submission_id;

          // Populate Step 2 preview
          showStep2(res, file.name);
          window.Nexus.toast("CSV parsed and validated successfully.", "ok");

        } catch (err) {
          window.Nexus.toast(err.message || "Failed to parse CSV preview.", "danger");
        } finally {
          previewBtn.disabled = false;
          previewBtn.textContent = "Preview & Validate File →";
        }
      });
    }

    // Step 2: Back Button
    if (backBtn) {
      backBtn.addEventListener("click", () => {
        setWizardStep(1);
      });
    }

    // Step 2: Confirm Commit
    if (commitBtn) {
      commitBtn.addEventListener("click", async () => {
        if (!currentSubmissionId) {
          window.Nexus.toast("No active submission to commit.", "danger");
          return;
        }

        const updateExisting = document.getElementById("update-existing-checkbox")?.checked || false;

        commitBtn.disabled = true;
        commitBtn.textContent = "Committing Records...";

        try {
          const instInput = document.getElementById("upload-institution");
          const payload = {
            mode: "commit",
            submission_id: currentSubmissionId,
            update_existing: updateExisting
          };
          if (instInput && instInput.value) {
            payload.institution_id = instInput.value;
          }

          const resp = await fetch("/api/students/import", {
            method: "POST",
            headers: {
              "X-CSRFToken": getCsrfToken(),
              "Content-Type": "application/json",
              "Accept": "application/json"
            },
            body: JSON.stringify(payload)
          });

          const data = await resp.json();
          if (!resp.ok || !data.ok) {
            throw new Error(data?.error?.message || "Commit failed.");
          }

          const res = data.data;
          window.Nexus.toast(`Ingestion finished: ${res.status}.`, res.status === "REJECTED" ? "danger" : "ok");

          showStep3(res);
          loadHistory();

        } catch (err) {
          window.Nexus.toast(err.message || "Failed to commit submission.", "danger");
        } finally {
          commitBtn.disabled = false;
          commitBtn.textContent = "Confirm & Commit Ingestion";
        }
      });
    }

    // Step 3: Upload Another
    if (uploadAnotherBtn) {
      uploadAnotherBtn.addEventListener("click", () => {
        if (previewForm) previewForm.reset();
        currentSubmissionId = null;
        currentFile = null;
        setWizardStep(1);
      });
    }

    // Refresh history
    if (refreshHistoryBtn) {
      refreshHistoryBtn.addEventListener("click", () => {
        loadHistory();
      });
    }

    // Initial load of history table
    loadHistory();
  });

  function setWizardStep(stepNum) {
    const p1 = document.getElementById("wizard-panel-1");
    const p2 = document.getElementById("wizard-panel-2");
    const p3 = document.getElementById("wizard-panel-3");

    const ind1 = document.getElementById("step-ind-1");
    const ind2 = document.getElementById("step-ind-2");
    const ind3 = document.getElementById("step-ind-3");

    if (p1) p1.style.display = (stepNum === 1) ? "block" : "none";
    if (p2) p2.style.display = (stepNum === 2) ? "block" : "none";
    if (p3) p3.style.display = (stepNum === 3) ? "block" : "none";

    [ind1, ind2, ind3].forEach((ind, idx) => {
      if (!ind) return;
      const isCur = (idx + 1 === stepNum);
      const isPast = (idx + 1 < stepNum);
      ind.style.opacity = (isCur || isPast) ? "1" : "0.5";
      const badge = ind.querySelector(".badge");
      if (badge) {
        badge.className = isCur ? "badge badge-info" : (isPast ? "badge badge-ok" : "badge badge-muted");
      }
    });
  }

  function showStep2(previewData, filename) {
    const fnText = document.getElementById("preview-filename-text");
    if (fnText) fnText.textContent = `File: ${filename} (SHA-256: ${previewData.submission_id ? previewData.submission_id.substring(0, 8) : ''}...)`;

    const summary = previewData.summary || {};
    document.getElementById("stat-total").textContent = summary.total_rows || 0;
    document.getElementById("stat-valid").textContent = (summary.valid_rows || 0) + (summary.update_candidate_rows || 0);
    document.getElementById("stat-errors").textContent = summary.error_rows || 0;
    document.getElementById("stat-warnings").textContent = summary.warning_rows || 0;

    const tbody = document.getElementById("preview-table-body");
    if (!tbody) return;

    tbody.innerHTML = "";
    const rows = previewData.preview_rows || [];

    if (!rows.length) {
      tbody.innerHTML = `<tr><td colspan="9" style="text-align: center; padding: 20px;">No preview records found.</td></tr>`;
      setWizardStep(2);
      return;
    }

    rows.forEach(r => {
      const raw = r.raw_row || {};
      const tr = document.createElement("tr");

      let badgeClass = "badge-ok";
      let statusLabel = r.status;
      if (r.status === "INVALID") {
        badgeClass = "badge-danger";
      } else if (r.status === "UPDATE_CANDIDATE") {
        badgeClass = "badge-info";
        statusLabel = "UPDATE";
      } else if (r.status === "UNCHANGED") {
        badgeClass = "badge-muted";
      }

      // Collect errors/warnings messages
      let msgs = [];
      (r.errors || []).forEach(e => msgs.push(`<span style="color: var(--danger);">[${window.Nexus.escapeHTML(e.code)}] ${window.Nexus.escapeHTML(e.message)}</span>`));
      (r.warnings || []).forEach(w => msgs.push(`<span style="color: #b7791f;">[${window.Nexus.escapeHTML(w.code)}] ${window.Nexus.escapeHTML(w.message)}</span>`));
      const msgHtml = msgs.length ? msgs.join("<br>") : `<span style="color: var(--ok);">✓ Valid</span>`;

      tr.innerHTML = `
        <td>${r.row_number}</td>
        <td><span class="badge ${badgeClass}">${statusLabel}</span></td>
        <td><strong>${window.Nexus.escapeHTML(raw.institution_student_id || '—')}</strong></td>
        <td>${window.Nexus.escapeHTML(raw.full_name || '—')}</td>
        <td><code>${window.Nexus.escapeHTML(raw.apaar_ref ? ('XXXXXXXX' + raw.apaar_ref.slice(-4)) : '—')}</code></td>
        <td>${window.Nexus.escapeHTML(raw.programme_code || '—')}</td>
        <td>${window.Nexus.escapeHTML(raw.academic_year || '—')}</td>
        <td>${window.Nexus.escapeHTML(raw.admission_category || '—')}</td>
        <td style="font-size: 12px;">${msgHtml}</td>
      `;
      tbody.appendChild(tr);
    });

    setWizardStep(2);
  }

  function showStep3(commitData) {
    const title = document.getElementById("result-title");
    const desc = document.getElementById("result-desc");
    const ref = document.getElementById("result-ref");
    const statusBadge = document.getElementById("result-status-badge");
    const errReportBtn = document.getElementById("download-error-report-btn");

    if (commitData.is_duplicate) {
      if (title) title.textContent = "File Already Imported";
      if (desc) desc.textContent = "An identical file has already been ingested previously for this institution.";
    } else {
      if (title) title.textContent = commitData.status === "REJECTED" ? "Ingestion Rejected" : "Ingestion Completed";
      if (desc) desc.textContent = commitData.status === "COMMITTED"
        ? "All records successfully processed and committed."
        : (commitData.status === "PARTIAL" ? "Some records were committed; rows with errors were rejected." : "All rows failed validation and were rejected.");
    }

    if (ref) ref.textContent = commitData.reference || "—";

    if (statusBadge) {
      statusBadge.textContent = commitData.status || "COMMITTED";
      const s = commitData.status;
      statusBadge.className = `badge ${s === 'COMMITTED' ? 'badge-ok' : (s === 'PARTIAL' ? 'badge-warn' : 'badge-danger')}`;
    }

    document.getElementById("result-inserted").textContent = commitData.inserted_count || 0;
    document.getElementById("result-updated").textContent = commitData.updated_count || 0;
    document.getElementById("result-errors").textContent = commitData.error_count || 0;

    const errorCount = commitData.error_count || 0;
    if (errorCount > 0 && errReportBtn && commitData.submission_id) {
      errReportBtn.style.display = "inline-block";
      errReportBtn.href = `/api/students/import/${commitData.submission_id}/errors.csv`;
    } else if (errReportBtn) {
      errReportBtn.style.display = "none";
    }

    setWizardStep(3);
  }

  async function loadHistory() {
    const tbody = document.getElementById("history-table-body");
    if (!tbody) return;

    try {
      const resp = await fetch("/api/students/import/history");
      const data = await resp.json();

      if (!resp.ok || !data.ok) {
        tbody.innerHTML = `<tr><td colspan="10" style="text-align: center; color: var(--muted); padding: 20px;">Failed to load submission history.</td></tr>`;
        return;
      }

      const rows = data.data || [];
      if (!rows.length) {
        tbody.innerHTML = `<tr><td colspan="10" style="text-align: center; color: var(--muted); padding: 20px;">No previous batch submissions found.</td></tr>`;
        return;
      }

      tbody.innerHTML = "";
      rows.forEach(s => {
        const tr = document.createElement("tr");

        let badgeClass = "badge-ok";
        if (s.status === "PARTIAL") badgeClass = "badge-warn";
        else if (s.status === "REJECTED") badgeClass = "badge-danger";
        else if (s.status === "PREVIEWED") badgeClass = "badge-muted";

        const dt = s.created_at ? new Date(s.created_at).toLocaleString('en-IN') : '—';
        const hasErrors = (s.error_rows > 0);
        const actionHtml = hasErrors
          ? `<a href="/api/students/import/${s.id}/errors.csv" class="btn btn-secondary" style="padding: 2px 8px; font-size: 11px;">Error CSV</a>`
          : `<span style="color: var(--muted); font-size: 12px;">None</span>`;

        tr.innerHTML = `
          <td><strong>${window.Nexus.escapeHTML(s.reference)}</strong></td>
          <td>${window.Nexus.escapeHTML(s.institution_code || '—')}</td>
          <td><span title="${window.Nexus.escapeHTML(s.filename)}">${window.Nexus.escapeHTML(s.filename || '—')}</span></td>
          <td><span class="badge ${badgeClass}">${window.Nexus.escapeHTML(s.status)}</span></td>
          <td>${s.total_rows}</td>
          <td style="color: var(--ok); font-weight: 500;">${s.valid_rows}</td>
          <td style="color: ${hasErrors ? 'var(--danger)' : 'var(--muted)'}; font-weight: ${hasErrors ? 'bold' : 'normal'};">${s.error_rows}</td>
          <td>${window.Nexus.escapeHTML(s.uploaded_by)}</td>
          <td style="font-size: 12px; color: var(--muted);">${window.Nexus.escapeHTML(dt)}</td>
          <td style="text-align: right;">${actionHtml}</td>
        `;
        tbody.appendChild(tr);
      });

    } catch (err) {
      tbody.innerHTML = `<tr><td colspan="10" style="text-align: center; color: var(--danger); padding: 20px;">Error loading history: ${window.Nexus.escapeHTML(err.message)}</td></tr>`;
    }
  }
})();
