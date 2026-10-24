/* Student consent controls. Bearer tokens stay only in the current result panel. */
(() => {
  "use strict";
  if (!document.getElementById("sharing-screen")) return;
  const N = window.Nexus;
  const get = id => document.getElementById(id);
  const labels = { DEGREE_AUTHENTICITY: "Degree authenticity", INSTITUTION: "Institution", PROGRAMME: "Programme",
    AWARD_YEAR: "Award year", STUDENT_NAME: "Student name", APAAR_MASKED: "Masked APAAR reference" };
  let selected = null;
  let sessionPage = 1;

  /** Erase the once-only result on page departure, including back-forward caching. */
  function clearResult() {
    get("sharing-link").value = "";
    get("sharing-qr").replaceChildren();
    get("sharing-result").hidden = true;
    get("sharing-copy-status").textContent = "";
  }
  window.addEventListener("pagehide", clearResult);

  /** List current backend results; invalid, revoked and expired credentials cannot be chosen. */
  async function loadCredentials(page = 1) {
    get("sharing-credentials-status").textContent = "Loading credentials...";
    try {
      const response = await N.api("/api/credentials", { params: { page } });
      N.renderTable(get("sharing-credentials"), { rows: response.data, emptyText: "No credentials are available. Ask your institution to issue one.", columns: [
        { key: "reference", label: "Credential" }, { key: "programme_name", label: "Programme" },
        { key: "award_year", label: "Award year" }, { key: "result", label: "Status", render: N.statusBadge },
        { key: "id", label: "Select", render: (_, row) => {
          const input = N.el("input", { type: "radio", name: "credential_id", value: row.id, "aria-label": `Select ${row.reference}` });
          input.disabled = row.result !== "VERIFIED";
          input.checked = selected?.id === row.id;
          input.addEventListener("change", () => { selected = row; get("sharing-create").disabled = false; });
          return input;
        } }
      ] });
      N.paginate(get("sharing-credential-pages"), response.meta, loadCredentials);
    } catch (error) { N.toast(error.message, "danger"); }
    finally { get("sharing-credentials-status").textContent = ""; }
  }

  /** Revoke only the chosen session, then reload computed status and counts. */
  async function revoke(row, button) {
    await N.withButtonLock(button, async () => {
      if (!await N.confirmDialog({ title: "Revoke sharing link", message: `Revoke link ${row.token_prefix}…? Future requests will no longer show claims.`, confirmText: "Revoke link", danger: true })) return;
      try {
        await N.api(`/api/sharing-sessions/${encodeURIComponent(row.id)}/revoke`, { method: "POST", body: {} });
        clearResult();
        N.toast("Sharing link revoked.", "ok");
        await loadSessions(sessionPage);
      } catch (error) { N.toast(error.message, "danger"); }
    });
  }

  /** Metadata never contains recoverable bearer links; paging remains server-scoped. */
  async function loadSessions(page = 1) {
    sessionPage = page;
    get("sharing-sessions-status").textContent = "Loading sharing sessions...";
    try {
      const response = await N.api("/api/sharing-sessions", { params: { page } });
      N.renderTable(get("sharing-sessions"), { rows: response.data, emptyText: "You have not created a sharing session.", columns: [
        { key: "created_at", label: "Created", render: (_, row) => N.el("span", {}, N.fmtDateTime(row.created_at)) },
        { key: "credential_reference", label: "Credential" },
        { key: "claims", label: "Claims", render: (_, row) => N.el("span", {}, row.claims.map(c => labels[c] || c).join(", ")) },
        { key: "expires_at", label: "Expires", render: (_, row) => N.el("span", {}, N.fmtDateTime(row.expires_at)) },
        { key: "status", label: "Status", render: N.statusBadge },
        { key: "access_count", label: "Access count", align: "right" }, { key: "token_prefix", label: "Token prefix" },
        { key: "id", label: "Action", render: (_, row) => {
          if (row.status !== "ACTIVE") return N.el("span", { class: "muted" }, "No action");
          const button = N.el("button", { type: "button", class: "btn btn-danger" }, "Revoke");
          button.addEventListener("click", () => void revoke(row, button));
          return button;
        } }
      ] });
      N.paginate(get("sharing-session-pages"), response.meta, loadSessions);
    } catch (error) { N.toast(error.message, "danger"); }
    finally { get("sharing-sessions-status").textContent = ""; }
  }

  get("sharing-form").addEventListener("submit", event => {
    event.preventDefault();
    void N.withButtonLock(get("sharing-create"), async () => {
      get("sharing-error").textContent = "";
      const claims = [...document.querySelectorAll('input[name="claim"]:checked')].map(input => input.value);
      const hours = Number(document.querySelector('input[name="expiry_hours"]:checked')?.value);
      if (!selected || !claims.length || !hours) { get("sharing-error").textContent = "Select a verified credential, at least one claim and an expiry."; return; }
      if (!await N.confirmDialog({ title: "Create sharing link", message: `${selected.reference}: share ${claims.map(c => labels[c]).join(", ")} for ${hours === 168 ? "7 days" : `${hours} hours`}. Anyone with the link may read these details.`, confirmText: "Create link" })) return;
      clearResult();
      try {
        const explicit_authorisation = Object.fromEntries(claims.filter(c => ["STUDENT_NAME", "APAAR_MASKED"].includes(c)).map(c => [c, true]));
        const { data } = await N.api("/api/sharing-sessions", { method: "POST", body: { credential_id: selected.id, claims, expiry_hours: hours, explicit_authorisation } });
        get("sharing-link").value = data.share_url;
        if (/^data:image\/png;base64,[A-Za-z0-9+/]+=*$/.test(data.qr_data_uri)) {
          const image = N.el("img", { alt: "Sharing link QR code", class: "credential-qr", width: 200, height: 200 });
          image.src = data.qr_data_uri;
          get("sharing-qr").append(image);
        }
        get("sharing-expiry").textContent = `Expires ${N.fmtDateTime(data.expires_at)}.`;
        get("sharing-result").hidden = false;
        get("sharing-link").focus();
        N.toast("Sharing link created. Copy it now.", "ok");
        await loadSessions();
      } catch (error) { get("sharing-error").textContent = error.message; N.toast(error.message, "danger"); }
    });
  });

  get("sharing-copy").addEventListener("click", async () => {
    const input = get("sharing-link");
    if (!input.value) return;
    try {
      if (!navigator.clipboard?.writeText) throw new Error("Clipboard unavailable");
      await navigator.clipboard.writeText(input.value);
      get("sharing-copy-status").textContent = "Link copied.";
    } catch {
      input.focus();
      input.select();
      get("sharing-copy-status").textContent = "Automatic copy is unavailable. The link is selected; press Ctrl+C to copy.";
    }
  });
  void loadCredentials();
  void loadSessions();
})();
