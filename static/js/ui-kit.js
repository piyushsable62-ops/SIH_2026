/* Development-only interactions. Shared Nexus helpers are implemented in B1. */
(() => {
  "use strict";

  const toastContainer = document.getElementById("toast-container");
  function showToast(message, kind) {
    const toast = document.createElement("div");
    toast.className = `toast toast-${kind}`;
    toast.setAttribute("role", "status");
    const content = document.createElement("span");
    content.textContent = message;
    const close = document.createElement("button");
    close.type = "button";
    close.className = "toast-close";
    close.textContent = "Dismiss";
    close.setAttribute("aria-label", `Dismiss ${kind} notification`);
    close.addEventListener("click", () => {
      toast.remove();
      document.querySelector(`[data-toast="${kind}"]`)?.focus();
    });
    toast.append(content, close);
    toastContainer.append(toast);
  }
  document.querySelectorAll("[data-toast]").forEach(button => {
    button.addEventListener("click", () => showToast(`Example ${button.dataset.toast} notification. No records changed.`, button.dataset.toast));
  });

  const rows = Array.from(document.querySelectorAll("#preview-rows tr"));
  const filters = document.getElementById("table-filter");
  let page = 1;
  let filtered = rows;
  const pageSize = 3;
  const previous = document.getElementById("previous-page");
  const next = document.getElementById("next-page");
  function renderRows() {
    const pages = Math.max(1, Math.ceil(filtered.length / pageSize));
    rows.forEach(row => { row.hidden = true; });
    filtered.slice((page - 1) * pageSize, page * pageSize).forEach(row => { row.hidden = false; });
    document.getElementById("table-empty").hidden = filtered.length > 0;
    document.getElementById("page-status").textContent = `Page ${page} of ${pages} (${filtered.length} records)`;
    previous.disabled = page <= 1;
    next.disabled = page >= pages;
  }
  function applyFilters() {
    const query = filters.elements.query.value.trim().toLowerCase();
    const status = filters.elements.status.value;
    filtered = rows.filter(row => row.textContent.toLowerCase().includes(query) && (!status || row.dataset.status === status));
    page = 1;
    renderRows();
  }
  filters.addEventListener("submit", event => { event.preventDefault(); applyFilters(); });
  filters.addEventListener("reset", () => {
    filtered = rows;
    page = 1;
    renderRows();
  });
  previous.addEventListener("click", () => { page -= 1; renderRows(); });
  next.addEventListener("click", () => { page += 1; renderRows(); });
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
    showToast("Synthetic values previewed locally. Nothing was saved.", "ok");
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

  const dialog = document.getElementById("example-dialog");
  document.getElementById("open-example-dialog").addEventListener("click", () => {
    dialog.returnValue = "cancel";
    dialog.showModal();
  });
  dialog.addEventListener("close", () => {
    document.getElementById("dialog-result").textContent = dialog.returnValue === "confirm" ? "Example confirmed. No records were changed." : "Example cancelled. No records were changed.";
  });

  // Local libraries are opt-in on this page; no network or API requests occur.
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
