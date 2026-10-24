/* Risk investigation screens: vanilla ES6, shared Nexus helpers, no build step. */
(() => {
  "use strict";
  const root = document.getElementById("risk-centre");
  if (!root) return;
  const N = window.Nexus;
  const byId = id => document.getElementById(id);
  const status = byId("risk-load-status");
  const labels = { UNDER_REVIEW: "Under Review", REQUEST_CLARIFICATION: "Request Institutional Clarification",
    CONFIRM_DATA_ERROR: "Confirm Data Error", DISMISS_FALSE_POSITIVE: "Dismiss False Positive",
    RESOLVE: "Resolve Case", INSTITUTION_RESPONSE: "Institution response" };
  const requiredNotes = new Set(["CONFIRM_DATA_ERROR", "DISMISS_FALSE_POSITIVE", "RESOLVE"]);

  /** Model timestamps are UTC; SQLite serializes them without an offset. */
  function when(value) {
    const iso = typeof value === "string" && value.includes("T") && !/(Z|[+-]\d{2}:\d{2})$/i.test(value) ? `${value}Z` : value;
    return N.fmtDateTime(iso);
  }

  /** Build a small readable definition list with text-safe values. */
  function facts(values) {
    const list = N.el("dl", { class: "kv" });
    Object.entries(values).forEach(([label, value]) => list.append(N.el("dt", {}, label), N.el("dd", {}, value ?? "—")));
    return list;
  }

  /** Retrieve only eligible reviewer summaries; the four-account seed has one. */
  async function reviewers() {
    const { data } = await N.api("/api/risk/reviewers");
    return data;
  }

  /** List data and summary facets share context but counts ignore severity/status. */
  function listScreen() {
    const form = byId("risk-filters");
    const cards = [...root.querySelectorAll("[data-filter]")];
    let page = 1;
    let request = 0;
    let timer;
    const values = () => Object.fromEntries(new FormData(form));
    const setValues = data => form.querySelectorAll("[name]").forEach(input => { input.value = data[input.name] ?? ""; });

    /** Fetch all summary counts and the selected page, discarding stale responses. */
    async function load(next = 1) {
      const ticket = ++request;
      const params = values();
      page = next;
      status.textContent = "Loading cases...";
      byId("risk-case-table").setAttribute("aria-busy", "true");
      cards.forEach(card => { card.disabled = true; });
      const url = new URL(window.location.href);
      url.search = "";
      Object.entries(params).forEach(([key, value]) => { if (value) url.searchParams.set(key, value); });
      if (page > 1) url.searchParams.set("page", String(page));
      window.history.replaceState(null, "", url);
      try {
        const [cases, summary] = await Promise.all([N.api("/api/risk/cases", { params: { ...params, page, per_page: 25 } }),
          N.api("/api/risk/summary", { params })]);
        if (ticket !== request) return;
        const last = Math.max(1, Math.ceil(cases.meta.total / cases.meta.per_page));
        if (page > last) { void load(last); return; }
        cards.forEach(card => {
          card.querySelector("[data-count]").textContent = N.fmtNumber(summary.data[card.dataset.filter][card.dataset.value] || 0);
          card.setAttribute("aria-pressed", String(params[card.dataset.filter] === card.dataset.value));
          card.disabled = false;
        });
        N.renderTable(byId("risk-case-table"), { columns: [
          { key: "reference", label: "Case ref", render: (value, row) => N.el("a", { href: `/risk-cases/${encodeURIComponent(row.id)}` }, value) },
          { key: "severity", label: "Severity", render: N.statusBadge }, { key: "score", label: "Score", align: "right" },
          { key: "status", label: "Status", render: N.statusBadge },
          { key: "institutions", label: "Institutions involved", render: rows => rows.map(row => row.code).join(", ") || "Not specified" },
          { key: "summary", label: "Summary" }, { key: "assigned_to", label: "Reviewer", render: value => value || "Unassigned" },
          { key: "updated_at", label: "Last updated", render: when }], rows: cases.data, emptyText: "No cases match the filters." });
        N.paginate(byId("risk-pagination"), cases.meta, load);
        status.textContent = `${N.fmtNumber(cases.meta.total)} matching cases. Sorted by score, highest first.`;
      } catch (error) {
        if (ticket !== request) return;
        status.textContent = error.message || "Unable to load cases. Apply filters to retry.";
        cards.forEach(card => { card.querySelector("[data-count]").textContent = "—"; });
        byId("risk-case-table").replaceChildren();
        byId("risk-pagination").replaceChildren();
        if (error.status !== 401) N.toast(error.message || "Unable to load cases.", "danger");
      } finally {
        if (ticket === request) byId("risk-case-table").setAttribute("aria-busy", "false");
      }
    }
    form.addEventListener("submit", event => { event.preventDefault(); clearTimeout(timer); void load(); });
    form.addEventListener("reset", event => { event.preventDefault(); clearTimeout(timer); setValues({}); void load(); });
    form.querySelectorAll("select").forEach(control => control.addEventListener("change", () => { clearTimeout(timer); void load(); }));
    byId("risk-search").addEventListener("input", () => { clearTimeout(timer); timer = setTimeout(() => void load(), 300); });
    cards.forEach(card => card.addEventListener("click", () => {
      const control = form.elements.namedItem(card.dataset.filter);
      control.value = control.value === card.dataset.value ? "" : card.dataset.value;
      clearTimeout(timer); void load();
    }));
    void reviewers().then(rows => rows.forEach(row => byId("risk-assigned").append(N.el("option", { value: row.username }, row.username))))
      .catch(error => { if (error.status !== 401) N.toast(error.message || "Unable to load reviewers.", "warn"); })
      .finally(() => {
        const initial = Object.fromEntries(new URLSearchParams(window.location.search));
        setValues(initial);
        void load(/^\d+$/.test(initial.page || "") && Number(initial.page) > 0 ? Number(initial.page) : 1);
      });
    window.addEventListener("pagehide", () => { clearTimeout(timer); request++; });
    window.addEventListener("pageshow", event => { if (event.persisted) void load(page); });
  }

  /** Render the case's evidence and human workflow without student-level mutations. */
  function detailScreen() {
    const id = root.dataset.caseId;
    const path = `/api/risk/cases/${encodeURIComponent(id)}`;
    const buttons = [...root.querySelectorAll("[data-action]")];
    let current = null;
    let request = 0;
    let eligible = null;
    let action = null;
    let mutating = false;
    const allowed = () => current?.allowed_actions || current?.allowed_next_actions || [];

    /** Synchronize mutation controls with the latest server permissions. */
    function permissions() {
      buttons.forEach(button => {
        button.disabled = mutating || !allowed().includes(button.dataset.action);
        button.title = allowed().includes(button.dataset.action) ? "" : `Not available from ${current?.status || "the current status"}`;
      });
      if (byId("risk-assign-submit")) byId("risk-assign-submit").disabled = mutating || !current || !eligible;
      if (byId("risk-response-submit")) byId("risk-response-submit").disabled = mutating || !allowed().includes("INSTITUTION_RESPONSE");
      if (byId("risk-action-submit")) byId("risk-action-submit").disabled = mutating || !allowed().includes(action);
    }

    /** Provide a working read-only record preview until Member A registers the detail page. */
    function recordLink(row) {
      let hasPage = false;
      try { hasPage = JSON.parse(document.querySelector('meta[name="nexus-api-routes"]')?.content || "[]").some(route => route.startsWith("/students/<")); }
      catch { /* A missing route inventory keeps the authorized record preview usable. */ }
      return N.el("a", { href: `/students/${encodeURIComponent(row.id)}`, on: { click: event => {
        if (!hasPage && !event.ctrlKey && !event.metaKey && !event.shiftKey && !event.altKey) {
          event.preventDefault();
          byId("risk-record-preview").replaceChildren(facts({ Name: row.full_name, "Masked APAAR": row.apaar_masked,
            Programme: row.programme_name, Year: row.academic_year, Status: row.enrolment_status }));
          N.openDialog("risk-record-dialog");
        }
      } } }, row.full_name);
    }

    /** Refresh the header, score, records, allowed actions and persisted history together. */
    function render(data) {
      current = data;
      const institutions = new Map(data.affected_institutions.map(row => [row.id, row.code]));
      const records = new Map(data.affected_records.map(row => [row.id, row.full_name]));
      byId("risk-case-header").replaceChildren(N.el("div", { class: "risk-header-facts" },
        N.statusBadge(data.severity), N.el("span", { class: "risk-score" }, `${data.score} / 100`), N.statusBadge(data.status),
        N.el("span", {}, `Reviewer: ${data.assigned_to || "Unassigned"}`),
        N.el("span", {}, `Opened: ${when(data.opened_at)}`), N.el("span", {}, `Updated: ${when(data.updated_at)}`)));
      const table = N.renderTable(byId("risk-score-table"), { columns: [
        { key: "rule_key", label: "Rule", render: value => value.replaceAll("_", " ") },
        { key: "points", label: "Points", align: "right" }, { key: "description", label: "Description" },
        { key: "related_student_ids", label: "Related records", render: ids => (ids || []).map(id_ => records.get(id_) || "Record unavailable").join(", ") || "Institution-level event" }], rows: data.evidence, emptyText: "No evidence rows." });
      table.append(N.el("tfoot", {}, N.el("tr", {}, N.el("th", { scope: "row" }, "Capped total"),
        N.el("td", { class: "align-right" }, `${data.score} / 100`), N.el("td", { colspan: 2 }, "Raw evidence points above are subject to the engine's safety cap."))));
      byId("risk-explanation").textContent = data.explanation;
      byId("risk-recommendation").textContent = data.recommended_action || "Review the evidence with the institution.";
      byId("risk-institutions").textContent = data.affected_institutions.map(row => `${row.code} — ${row.name}`).join("; ") || "No institutions specified.";
      N.renderTable(byId("risk-record-table"), { columns: [
        { key: "apaar_masked", label: "Masked APAAR" }, { key: "full_name", label: "Name / student detail", render: (value, row) => recordLink(row) },
        { key: "institution_id", label: "Institution", render: id_ => institutions.get(id_) || "Not specified" },
        { key: "programme_name", label: "Programme" }, { key: "academic_year", label: "Year" },
        { key: "enrolment_status", label: "Enrolment", render: N.statusBadge }], rows: data.affected_records, emptyText: "This is an institution-level case; no student record is attached." });
      byId("risk-history").replaceChildren(...data.history.map(item => N.el("li", {},
        N.el("strong", {}, item.note === "Case opened by system scan" ? item.note : labels[item.action] || item.action.replaceAll("_", " ")),
        N.el("p", { class: "muted" }, `${when(item.created_at)} · ${item.actor_id} · ${item.from_status} → ${item.to_status}`),
        item.note && item.note !== "Case opened by system scan" ? N.el("p", { class: "risk-history-note" }, item.note) : null)));
      if (!data.history.length) byId("risk-history").append(N.el("li", {}, "No investigation history recorded."));
      if (byId("risk-response-card")) byId("risk-response-card").hidden = !allowed().includes("INSTITUTION_RESPONSE");
      if (byId("risk-reviewer")) {
        const select = byId("risk-reviewer");
        select.replaceChildren(N.el("option", { value: "" }, "Unassigned"), ...(eligible || []).map(row => N.el("option", { value: row.username }, row.username)));
        if (data.assigned_to && !(eligible || []).some(row => row.username === data.assigned_to)) select.append(N.el("option", { value: data.assigned_to, disabled: true }, `${data.assigned_to} (not eligible)`));
        select.value = data.assigned_to || "";
        select.disabled = !eligible || mutating;
      }
      permissions();
    }

    /** Fetch current case data; keep any user-entered note draft intact on refresh. */
    async function load() {
      const ticket = ++request;
      status.textContent = "Loading case...";
      try {
        const { data } = await N.api(path);
        if (ticket !== request) return;
        render(data); status.textContent = "Case data is up to date.";
      } catch (error) {
        if (ticket !== request) return;
        current = null; permissions();
        status.textContent = error.message || "Unable to load the case. Use Refresh case to retry.";
        if (error.status !== 401) N.toast(error.message || "Unable to load the case.", "danger");
      }
    }

    /** Lock the request and every other mutation control until server persistence finishes. */
    function mutate(button, fn) {
      if (mutating) return Promise.resolve();
      return N.withButtonLock(button, async () => {
        mutating = true; permissions();
        await fn();
      }).finally(() => { mutating = false; permissions(); if (byId("risk-reviewer")) byId("risk-reviewer").disabled = !eligible; });
    }

    buttons.forEach(button => button.addEventListener("click", () => {
      if (!allowed().includes(button.dataset.action) || mutating) return;
      action = button.dataset.action;
      byId("risk-action-form").reset();
      byId("risk-action-title").textContent = labels[action];
      byId("risk-action-help").textContent = "Record a human decision and its reason. Student records and credentials will not change.";
      byId("risk-action-note").required = requiredNotes.has(action);
      byId("risk-action-note").setCustomValidity("");
      byId("risk-note-required").textContent = requiredNotes.has(action) ? "(required)" : "(optional)";
      byId("risk-action-error").textContent = "";
      permissions(); N.openDialog("risk-action-dialog");
    }));
    byId("risk-action-note")?.addEventListener("input", () => byId("risk-action-note").setCustomValidity(""));
    byId("risk-action-cancel")?.addEventListener("click", () => N.closeDialog("risk-action-dialog"));
    byId("risk-action-dialog")?.addEventListener("close", () => { action = null; });
    byId("risk-action-form")?.addEventListener("submit", event => {
      event.preventDefault();
      const submittedAction = action;
      if (mutating || !allowed().includes(submittedAction)) return;
      const note = byId("risk-action-note").value.trim();
      if (requiredNotes.has(submittedAction) && !note) {
        byId("risk-action-note").setCustomValidity("Enter a reason for this decision."); byId("risk-action-note").reportValidity(); return;
      }
      void mutate(byId("risk-action-submit"), async () => {
        const { data } = await N.api(`${path}/actions`, { method: "POST", body: { action: submittedAction, note } });
        request++; render(data); N.closeDialog("risk-action-dialog");
        status.textContent = "Case updated."; N.toast("Case action recorded.", "ok");
      }).catch(async error => {
        byId("risk-action-error").textContent = error.message;
        if (error.status === 409) await load();
        if (error.status !== 401) N.toast(error.message || "Unable to record action.", "danger");
      });
    });
    byId("risk-assign-form")?.addEventListener("submit", event => {
      event.preventDefault();
      void mutate(byId("risk-assign-submit"), async () => {
        const { data } = await N.api(path, { method: "PATCH", body: { assigned_to: byId("risk-reviewer").value || null } });
        request++; render(data); N.toast("Reviewer assignment saved.", "ok");
      }).catch(error => { if (error.status !== 401) N.toast(error.message || "Unable to assign reviewer.", "danger"); });
    });
    byId("risk-response-form")?.addEventListener("submit", event => {
      event.preventDefault();
      const note = byId("risk-response-note").value.trim();
      if (!note) { byId("risk-response-error").textContent = "A clarification note is required."; return; }
      void mutate(byId("risk-response-submit"), async () => {
        const { data } = await N.api(`${path}/actions`, { method: "POST", body: { action: "INSTITUTION_RESPONSE", note } });
        request++; render(data); byId("risk-response-note").value = "";
        byId("risk-response-error").textContent = ""; N.toast("Clarification recorded. Case status is unchanged.", "ok");
      }).catch(error => { byId("risk-response-error").textContent = error.message; if (error.status !== 401) N.toast(error.message || "Unable to submit clarification.", "danger"); });
    });
    byId("risk-refresh").addEventListener("click", () => {
      void load(); if (byId("risk-reviewer")) void loadReviewers();
    });
    /** Retry reviewer loading independently so a failed optional control cannot hide the case. */
    async function loadReviewers() {
      byId("risk-reviewer-status").textContent = "Loading reviewers...";
      try {
        eligible = await reviewers();
        byId("risk-reviewer-status").textContent = eligible.length ? `${eligible.length} eligible reviewer${eligible.length === 1 ? "" : "s"}.` : "No eligible reviewers available.";
        if (current) render(current);
      } catch (error) {
        eligible = null;
        byId("risk-reviewer-status").textContent = error.message || "Unable to load reviewers. Use Refresh case to retry.";
        permissions();
      }
    }
    if (byId("risk-reviewer")) void loadReviewers();
    void load();
    window.addEventListener("pagehide", () => { request++; });
    window.addEventListener("pageshow", event => { if (event.persisted) void load(); });
  }
  if (root.dataset.view === "list") listScreen(); else detailScreen();
})();
