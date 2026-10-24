/* Development-only examples. Reusable behaviour lives in window.Nexus. */
(() => {
  "use strict";

  const { api, toast, confirmDialog, renderTable, paginate, filterBar, statusBadge, withButtonLock, loadFilters } = window.Nexus;
  document.querySelectorAll("[data-toast]").forEach(button => {
    button.addEventListener("click", () => toast(`Example ${button.dataset.toast} notification. No records changed.`, button.dataset.toast));
  });

  const rows = Array.from(document.querySelectorAll("#preview-rows tr"), row => ({
    reference: row.cells[0].textContent, name: row.cells[1].textContent,
    institution: row.cells[2].textContent, status: row.dataset.status
  }));
  let page = 1;
  let filtered = rows;
  const pageSize = 3;
  function renderRows() {
    const table = renderTable("#example-table", {
      columns: [{ key: "reference", label: "Reference" }, { key: "name", label: "Student" },
        { key: "institution", label: "Institution" }, { key: "status", label: "Status", render: value => value === "Needs review" ? window.Nexus.badge(value, "warn") : statusBadge(value) }],
      rows: filtered.slice((page - 1) * pageSize, page * pageSize),
      emptyText: "No matching examples. Clear the filters to see the synthetic rows again."
    });
    table.classList.add("preview-table");
    table.prepend(window.Nexus.el("caption", {}, "Synthetic records for component testing; no student data is loaded."));
    paginate("#table-pagination", { page, per_page: pageSize, total: filtered.length }, nextPage => {
      page = nextPage;
      renderRows();
    });
  }
  filterBar("#table-filter", [
    { name: "query", label: "Search synthetic rows", type: "text", placeholder: "Example student or institution" },
    { name: "status", label: "Status", type: "select", placeholder: "All statuses", options: ["Verified", "Needs review", "Invalid"] }
  ], ({ query, status }) => {
    filtered = rows.filter(row => `${row.reference} ${row.name} ${row.institution}`.toLowerCase().includes(query.trim().toLowerCase()) && (!status || row.status === status));
    page = 1;
    renderRows();
  });
  renderRows();

  const exampleForm = document.getElementById("example-form");
  const name = document.getElementById("example-name");
  const nameError = document.getElementById("name-error");
  const formResult = document.getElementById("form-result");
  exampleForm.addEventListener("submit", event => {
    event.preventDefault();
    const valid = name.value.trim().length > 0 && name.value.length <= 60;
    nameError.hidden = valid;
    name.setAttribute("aria-invalid", String(!valid));
    if (!valid) { name.focus(); return; }
    formResult.textContent = `Preview: ${name.value.trim()} / ${exampleForm.elements.programme.value}. Nothing was saved.`;
    toast("Synthetic values previewed locally. Nothing was saved.", "ok");
  });
  exampleForm.addEventListener("reset", () => {
    name.removeAttribute("aria-invalid");
    nameError.hidden = true;
    formResult.textContent = "No values submitted.";
  });

  const tabs = Array.from(document.querySelectorAll('[role="tab"]'));
  function selectTab(tab, focus = false) {
    tabs.forEach(item => {
      const active = item === tab;
      item.setAttribute("aria-selected", String(active));
      item.tabIndex = active ? 0 : -1;
      document.getElementById(item.getAttribute("aria-controls")).hidden = !active;
    });
    if (focus) tab.focus();
  }
  tabs.forEach((tab, index) => {
    tab.addEventListener("click", () => selectTab(tab));
    tab.addEventListener("keydown", event => {
      const key = event.key;
      if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(key)) return;
      event.preventDefault();
      const target = key === "Home" ? 0 : key === "End" ? tabs.length - 1 : (index + (key === "ArrowRight" ? 1 : -1) + tabs.length) % tabs.length;
      selectTab(tabs[target], true);
    });
  });

  document.getElementById("open-example-dialog").addEventListener("click", async () => {
    const confirmed = await confirmDialog({ title: "Confirm example action", message: "This demonstrates a destructive-action confirmation. No records will be changed.", confirmText: "Confirm example", danger: true });
    document.getElementById("dialog-result").textContent = confirmed ? "Example confirmed. No records were changed." : "Example cancelled. No records were changed.";
  });
  document.getElementById("check-api-health").addEventListener("click", event => {
    void withButtonLock(event.currentTarget, async () => {
      try {
        const { data } = await api("/api/health");
        document.getElementById("api-result").textContent = `API status: ${data.status}. No records were changed.`;
      } catch (error) { toast(error.message, "danger"); }
    });
  });
  document.getElementById("load-filter-options").addEventListener("click", event => {
    void withButtonLock(event.currentTarget, async () => {
      const options = await loadFilters.institutions();
      document.getElementById("api-result").textContent = options.length ? `${options.length} authorized institution options loaded.` : "No institution options loaded. See the notification if the module is unavailable.";
    });
  });

  // These vendor demonstrations use local files and in-memory synthetic data.
  if (typeof Chart !== "undefined") {
    new Chart(document.getElementById("example-chart"), {
      type: "bar",
      data: { labels: ["VESIT", "COEP", "VJTI"], datasets: [{ label: "Synthetic rows", data: [2, 2, 2], backgroundColor: "#14315c" }] },
      options: {
        responsive: true, maintainAspectRatio: false, animation: false,
        plugins: { legend: { display: false }, title: { display: true, text: "Synthetic records by institution" } },
        scales: { x: { title: { display: true, text: "Institution" } }, y: { beginAtZero: true, ticks: { stepSize: 1 }, title: { display: true, text: "Record count" } } }
      }
    });
    document.getElementById("chart-status").textContent = `Chart.js ${Chart.version} loaded locally.`;
  } else {
    document.getElementById("chart-status").textContent = "Local Chart.js could not load. Synthetic counts: VESIT 2, COEP 2, VJTI 2.";
  }
  if (typeof cytoscape !== "undefined") {
    const graph = cytoscape({
      container: document.getElementById("example-graph"),
      elements: [
        { data: { id: "student", label: "Synthetic student" }, position: { x: 100, y: 90 } },
        { data: { id: "institution", label: "VESIT" }, position: { x: 320, y: 90 } },
        { data: { id: "example-link", source: "student", target: "institution" } }
      ],
      layout: { name: "preset", padding: 48 },
      style: [
        { selector: "node", style: { "background-color": "#14315c", label: "data(label)", color: "#1d2733", "font-size": 14, "text-valign": "bottom", "text-halign": "center", "text-margin-y": 12 } },
        { selector: "#institution", style: { shape: "rectangle", "background-color": "#5b6776" } },
        { selector: "edge", style: { width: 2, "line-color": "#5b6776" } }
      ],
      userZoomingEnabled: false, userPanningEnabled: false, autoungrabify: true,
      boxSelectionEnabled: false, autounselectify: true
    });
    // The browser can resize the page after this graph is created.
    window.addEventListener("resize", () => { graph.resize(); graph.fit(undefined, 48); });
    document.getElementById("graph-status").textContent = `Cytoscape.js ${cytoscape.version} loaded locally.`;
  } else {
    document.getElementById("graph-status").textContent = "Local Cytoscape.js could not load. Synthetic student is connected to VESIT.";
  }
})();
