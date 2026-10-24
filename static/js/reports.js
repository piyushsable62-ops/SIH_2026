/* Adds Member B's anomaly report to Member A's existing enrolment page. */
(() => {
  "use strict";
  const form = document.getElementById("anomaly-filters");
  if (!form) return;
  const N = window.Nexus;
  const get = id => document.getElementById(id);
  const values = () => Object.fromEntries([...new FormData(form)].filter(([, value]) => value !== ""));
  const filename = "audit_log_SYNTHETIC.json";

  /** Populate academic years and programmes from scoped institution records. */
  async function options() {
    const { data } = await N.api("/api/institutions", { params: { per_page: 100 } });
    const years = new Set();
    for (const institution of data) {
      const [programmes, capacities] = await Promise.all([
        N.api(`/api/institutions/${encodeURIComponent(institution.id)}/programmes`),
        N.api(`/api/institutions/${encodeURIComponent(institution.id)}/capacities`)
      ]);
      programmes.data.forEach(row => get("anomaly-programme").append(N.el("option", { value: row.id }, `${institution.code} ${row.name}`)));
      capacities.data.forEach(row => years.add(row.academic_year));
    }
    [...years].sort().reverse().forEach(year => get("anomaly-year").append(N.el("option", { value: year }, year)));
  }

  /** Preview scoped evidence and keep the CSV link aligned with every filter. */
  async function preview() {
    const params = values();
    get("anomaly-download").href = `/api/reports/anomalies.csv?${new URLSearchParams(params)}`;
    get("anomaly-status").textContent = "Loading anomaly report...";
    try {
      const { data } = await N.api("/api/reports/anomalies", { params });
      N.renderTable(get("anomaly-rows"), { rows: data.rows, emptyText: "No investigation evidence matches the filters.", columns: [
        { key: "case_reference", label: "Case", render: (value, row) => N.el("a", { href: `/risk-cases/${encodeURIComponent(row.case_id)}` }, value) },
        { key: "severity", label: "Severity", render: N.statusBadge }, { key: "institution_code", label: "Institution" },
        { key: "rule_key", label: "Rule" }, { key: "points", label: "Points" },
        { key: "explanation", label: "Evidence" }, { key: "masked_apaar", label: "Masked APAAR" }
      ] });
      get("anomaly-status").textContent = `${data.rows.length} evidence rows.`;
    } catch (error) { get("anomaly-status").textContent = error.message; N.toast(error.message, "danger"); }
  }

  /** Read Member A's audit API and show only its export events. */
  async function exportsList() {
    try {
      const { data } = await N.api("/api/audit-logs", { params: { per_page: 100 } });
      const rows = data.filter(row => row.action.startsWith("EXPORT_")).slice(0, 10);
      N.renderTable(get("recent-exports"), { rows, emptyText: "No recent exports.", columns: [
        { key: "created_at", label: "Time", render: N.fmtDateTime }, { key: "actor_id", label: "Actor" },
        { key: "action", label: "Export" }, { key: "institution_id", label: "Scope", render: value => value || "All institutions" }
      ] });
    } catch (error) { get("recent-exports").textContent = error.message; }
  }

  /** Export the authorized audit JSON response without changing Member A's API. */
  get("audit-download").addEventListener("click", () => void N.withButtonLock(get("audit-download"), async () => {
    try {
      const response = await N.api("/api/audit-logs", { params: { per_page: 100, institution_id: get("anomaly-institution")?.value || "" } });
      const blob = new Blob([JSON.stringify(response.data, null, 2)], { type: "application/json" });
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = filename;
      // A blob URL is generated locally from the authorized API response only.
      document.body.append(link);
      link.click();
      link.remove();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
      N.toast("Audit JSON downloaded.", "ok");
    } catch (error) { N.toast(error.message, "danger"); }
  }));
  form.addEventListener("submit", event => { event.preventDefault(); void preview(); });
  form.addEventListener("change", () => { get("anomaly-download").href = `/api/reports/anomalies.csv?${new URLSearchParams(values())}`; });
  void (async () => { try { await options(); await preview(); await exportsList(); } catch (error) { get("anomaly-status").textContent = error.message; N.toast(error.message, "danger"); } })();
})();
