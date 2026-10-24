/* National and institution dashboards use only scoped server results. */
(() => {
  "use strict";
  const root = document.getElementById("governance-screen");
  if (!root) return;
  const N = window.Nexus;
  const get = id => document.getElementById(id);
  const national = root.dataset.mode === "governance";
  const filters = get("governance-filters");
  const charts = new Map();
  const colors = { navy: "#14315c", blue: "#6383a9", grey: "#a9b8c8", orange: "#b7791f", red: "#c0392b", green: "#1a7f4b" };
  let institutions = [];
  const current = () => Object.fromEntries([...new FormData(filters)].filter(([, value]) => value !== ""));
  const cell = (text, href) => href ? N.el("a", { href }, text) : N.el("span", {}, text);

  /** Replace one chart only after its current data request succeeds. */
  function chart(id, type, labels, datasets, options = {}) {
    const canvas = get(id);
    if (!canvas || !window.Chart) return;
    charts.get(id)?.destroy();
    charts.set(id, new Chart(canvas, { type, data: { labels, datasets }, options: { responsive: true, maintainAspectRatio: false,
      plugins: { legend: { display: datasets.length > 1 } }, scales: type === "doughnut" ? {} : {
        x: { title: { display: true, text: options.xTitle || "Category" }, stacked: !!options.stacked },
        y: { beginAtZero: true, title: { display: true, text: options.yTitle || "Records" }, stacked: !!options.stacked }
      }, ...options } }));
  }

  /** Assemble select options from real institution and capacity endpoints. */
  async function loadOptions() {
    const { data } = await N.api("/api/institutions", { params: { per_page: 100 } });
    institutions = data;
    const instSelect = get("dash-institution");
    if (instSelect) data.forEach(row => instSelect.append(N.el("option", { value: row.id }, `${row.code} — ${row.name}`)));
    const ids = national ? data.map(row => row.id) : [root.dataset.institution];
    const programmeSelect = get("dash-programme");
    const years = new Set();
    const seen = new Set();
    for (const id of ids) {
      const [programmes, capacities] = await Promise.allSettled([
        N.api(`/api/institutions/${encodeURIComponent(id)}/programmes`),
        N.api(`/api/institutions/${encodeURIComponent(id)}/capacities`)
      ]);
      if (programmes.status === "fulfilled") programmes.value.data.forEach(row => { if (!seen.has(row.id)) { seen.add(row.id); programmeSelect.append(N.el("option", { value: row.id }, `${institutions.find(i => i.id === id)?.code || ""} ${row.name}`)); } });
      if (capacities.status === "fulfilled") capacities.value.data.forEach(row => years.add(row.academic_year));
    }
    const orderedYears = [...years].sort().reverse();
    orderedYears.forEach(year => get("dash-year").append(N.el("option", { value: year }, year)));
    if (!national && orderedYears.length) get("dash-year").value = orderedYears[0];
  }

  /** Compact statistics remain text, including their current filtered counts. */
  function stats(entries) {
    get("governance-stats").replaceChildren(...entries.map(([label, value, hint]) => N.el("div", { class: "card stat" },
      N.el("div", { class: "stat-number" }, N.fmtNumber(value)), N.el("div", { class: "stat-label" }, label),
      N.el("div", { class: "stat-trend" }, hint || "Current filters"))));
  }
  function table(id, columns, rows, emptyText) { N.renderTable(get(id), { columns, rows, emptyText }); }

  /** National view uses shared colors, real drill-down links and accessible tables. */
  function renderNational(data) {
    const t = data.totals;
    stats([["Institutions", t.institutions], ["Student records", t.student_records], ["Validated", t.validated],
      ["Pending validation", t.pending_validations], ["Active enrolments", t.active_enrolments], ["Identity conflicts", t.identity_conflicts],
      ["Missing / invalid APAAR", t.missing_invalid_apaar], ["Capacity ≥ 80%", t.capacity_80],
      ["Capacity ≥ 90%", t.capacity_90], ["Capacity ≥ 100%", t.capacity_100], ["Excess attempts", t.excess_admission_attempts]]);
    const severity = ["LOW", "MEDIUM", "HIGH", "CRITICAL"];
    chart("chart-risk", "bar", severity, [{ label: "Cases", data: severity.map(key => data.risk_cases_by_severity[key] || 0), backgroundColor: [colors.green, colors.orange, colors.red, colors.navy] }],
      { xTitle: "Risk severity", yTitle: "Cases", onClick: (_, active) => { if (active.length) window.location.assign(`/risk-cases?severity=${encodeURIComponent(severity[active[0].index])}`); } });
    chart("chart-category", "bar", Object.keys(data.category_wise_enrolment), [{ label: "Students", data: Object.values(data.category_wise_enrolment), backgroundColor: colors.blue }], { xTitle: "Admission category", yTitle: "Students" });
    const years = [...new Set(data.institution_admission_trend.map(row => row.academic_year))].sort();
    const codes = [...new Set(data.institution_admission_trend.map(row => row.institution))].sort();
    chart("chart-trend", "line", years, codes.map((code, index) => ({ label: code,
      data: years.map(year => data.institution_admission_trend.find(row => row.academic_year === year && row.institution === code)?.admissions || 0),
      borderColor: [colors.navy, colors.orange, colors.green, colors.blue][index % 4], tension: 0 })), { xTitle: "Academic year", yTitle: "Admissions" });
    chart("chart-capacity", "bar", data.capacity_utilisation.map(row => row.code), [{ label: "Utilisation %", data: data.capacity_utilisation.map(row => row.percent),
      backgroundColor: data.capacity_utilisation.map(row => row.percent >= 100 ? colors.red : row.percent >= 90 ? colors.orange : colors.navy) }], { xTitle: "Institution", yTitle: "Utilisation %" });
    const events = data.credential_verification_activity;
    chart("chart-verification", "bar", events.map(row => row.date), [
      ["VERIFIED", colors.green], ["INVALID", colors.red], ["REVOKED", colors.orange], ["EXPIRED", colors.grey]
    ].map(([key, color]) => ({ label: key, data: events.map(row => row[key]), backgroundColor: color })),
    { xTitle: "Day (UTC)", yTitle: "Verifications", stacked: true });
    table("capacity-list", [{ key: "name", label: "Institution" }, { key: "percent", label: "Utilisation", render: value => N.badge(`${value}%`, value >= 100 ? "danger" : value >= 90 ? "warn" : "info") }],
      data.capacity_utilisation.filter(row => row.percent >= 80), "No institution has reached 80% of its selected capacity.");
    table("high-cases", [{ key: "reference", label: "Case", render: (value, row) => cell(value, `/risk-cases/${encodeURIComponent(row.id)}`) },
      { key: "severity", label: "Severity", render: N.statusBadge }, { key: "score", label: "Score" }, { key: "summary", label: "Summary" }],
      data.recent_high_risk_cases, "No high or critical cases match the filters.");
    table("recent-audit", [{ key: "time", label: "Time", render: N.fmtDateTime }, { key: "actor", label: "Actor" }, { key: "action", label: "Action" }],
      data.recent_audit_events, "No recent audit events.");
  }

  /** Institution metrics retain Member A's endpoint and current server scope. */
  function renderInstitution(data) {
    const s = data.seat_summary;
    const pending = data.pending_identity_checks;
    stats([["Approved intake", s.approved_regular_intake], ["Regular admissions", s.regular_admitted],
      ["Remaining seats", s.regular_remaining], ["Supernumerary admitted", s.supernumerary_admitted],
      ["Utilisation %", s.regular_utilisation_pct], ["Pending identity checks", pending.pending],
      ["Flagged checks", pending.flagged], ["Risk cases", data.risk_cases_by_severity.total],
      ["Credentials issued", data.credential_issuance_history.length]]);
    const categories = Object.values(s.category_capacity || {});
    chart("chart-category", "bar", categories.map(row => row.category), [{ label: "Admitted", data: categories.map(row => row.admitted), backgroundColor: colors.navy }],
      { xTitle: "Admission category", yTitle: "Admissions" });
    table("category-capacity", [{ key: "category", label: "Category" }, { key: "admitted", label: "Admitted" },
      { key: "capacity", label: "Capacity", render: value => value ?? "Policy not set" },
      { key: "utilisation_pct", label: "Utilisation", render: value => value == null ? "—" : `${value}%` }], categories, "No capacity categories.");
    const progress = N.el("div", { class: `progress ${s.regular_utilisation_pct >= 100 ? "progress-100" : s.regular_utilisation_pct >= 90 ? "progress-90" : s.regular_utilisation_pct >= 80 ? "progress-80" : ""}` }, N.el("span", { class: "progress-fill" }));
    progress.firstElementChild.style.setProperty("--value", `${Math.max(0, Math.min(100, s.regular_utilisation_pct))}%`);
    get("institution-utilisation").replaceChildren(N.el("p", {}, `${s.regular_admitted} of ${s.approved_regular_intake} regular seats filled`), progress,
      N.el("p", { class: "muted" }, `${s.regular_utilisation_pct}% regular utilisation; ${s.supernumerary_admitted}/${s.supernumerary_capacity} supernumerary seats`));
    table("submission-list", [{ key: "reference", label: "Submission" }, { key: "status", label: "Status", render: N.statusBadge }, { key: "total_rows", label: "Rows" }],
      data.submission_history, "No submissions match this institution.");
    table("validation-errors", [{ key: "code", label: "Error code" }, { key: "count", label: "Count" }],
      Object.entries(data.validation_error_counts).map(([code, count]) => ({ code, count })), "No validation errors.");
    table("identity-checks", [{ key: "status", label: "Status" }, { key: "count", label: "Records" }],
      [{ status: "PENDING", count: pending.pending }, { status: "FLAGGED", count: pending.flagged }], "No identity checks.");
    table("institution-risk", [{ key: "severity", label: "Severity", render: N.statusBadge }, { key: "count", label: "Cases" }],
      ["LOW", "MEDIUM", "HIGH", "CRITICAL"].map(severity => ({ severity, count: data.risk_cases_by_severity[severity] || 0 })), "No cases.");
    table("credential-history", [{ key: "reference", label: "Credential", render: (value, row) => cell(value, `/credentials/${encodeURIComponent(row.id)}`) },
      { key: "student_name", label: "Student" }, { key: "programme_name", label: "Programme" }, { key: "status", label: "Status", render: N.statusBadge }],
      data.credential_issuance_history, "No credentials have been issued.");
  }

  /** Filters reach the server on every refresh; no visual-only selection. */
  async function refresh() {
    get("governance-status").textContent = "Loading dashboard...";
    try {
      const { data } = await N.api(national ? "/api/dashboard/governance" : "/api/dashboard/institution", { params: current() });
      if (national) renderNational(data); else renderInstitution(data);
      get("governance-status").textContent = "Dashboard updated.";
    } catch (error) { get("governance-status").textContent = error.message; N.toast(error.message, "danger"); }
  }
  filters.addEventListener("submit", event => { event.preventDefault(); void refresh(); });
  get("dash-reset").addEventListener("click", () => { filters.reset(); void refresh(); });
  void (async () => { try { await loadOptions(); await refresh(); } catch (error) { get("governance-status").textContent = error.message; N.toast(error.message, "danger"); } })();
})();
