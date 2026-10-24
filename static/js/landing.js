/**
 * APAAR Nexus — Simple, Linear Landing Page & Tour Controller
 * ES6 Vanilla JavaScript. No external build step or dependencies.
 */
(() => {
  "use strict";

  let step = 1;
  const maxSteps = 4;

  document.addEventListener("DOMContentLoaded", () => {
    initTour();
    initSandbox();
    initDemoLogin();
  });

  function initTour() {
    const prevBtn = document.getElementById("tour-prev-btn");
    const nextBtn = document.getElementById("tour-next-btn");
    const tabs = document.querySelectorAll(".tour-tab");

    if (prevBtn) prevBtn.addEventListener("click", () => goStep(step - 1));
    if (nextBtn) nextBtn.addEventListener("click", () => goStep(step === maxSteps ? 1 : step + 1));

    tabs.forEach(tab => {
      tab.addEventListener("click", () => {
        const target = parseInt(tab.dataset.step, 10);
        if (!isNaN(target)) goStep(target);
      });
    });

    renderStep();
  }

  function goStep(target) {
    if (target < 1) target = 1;
    if (target > maxSteps) target = maxSteps;
    step = target;
    renderStep();
  }

  function renderStep() {
    const prevBtn = document.getElementById("tour-prev-btn");
    const nextBtn = document.getElementById("tour-next-btn");
    const badge = document.getElementById("tour-step-badge");
    const tabs = document.querySelectorAll(".tour-tab");

    if (badge) badge.textContent = `Step ${step} of ${maxSteps}`;
    if (prevBtn) prevBtn.disabled = step === 1;
    if (nextBtn) nextBtn.textContent = step === maxSteps ? "Restart Tour" : "Next \u2192";

    // Update tab styling
    tabs.forEach(tab => {
      const s = parseInt(tab.dataset.step, 10);
      if (s === step) {
        tab.classList.remove("btn-secondary");
        tab.classList.add("btn");
      } else {
        tab.classList.add("btn-secondary");
        tab.classList.remove("btn");
      }
    });

    // Show active panel, hide others
    for (let i = 1; i <= maxSteps; i++) {
      const panel = document.getElementById(`step-panel-${i}`);
      if (panel) {
        panel.style.display = i === step ? "block" : "none";
      }
    }
  }

  function initSandbox() {
    const select = document.getElementById("sandbox-select");
    const btn = document.getElementById("sandbox-btn");
    const badge = document.getElementById("sandbox-badge");
    const msg = document.getElementById("sandbox-msg");

    const scenarios = {
      valid: {
        badge: "VERIFIED",
        cls: "badge-ok",
        text: "Issuer: <strong>VESIT Mumbai (AICTE ID: 1-1092384)</strong> &bull; Degree: <strong>B.Tech Computer Engineering</strong> &bull; Ed25519 signature valid. Credential active."
      },
      revoked: {
        badge: "REVOKED",
        cls: "badge-danger",
        text: "Issuer: <strong>COEP Pune (AICTE ID: 1-2094182)</strong> &bull; Degree: <strong>M.Tech Data Science</strong> &bull; Credential revoked by institution on 2026-09-15. Status: Not valid."
      },
      tampered: {
        badge: "INVALID",
        cls: "badge-danger",
        text: "<strong>Verification Failed:</strong> Ed25519 signature digest mismatch. Degree payload has been altered post-issuance."
      }
    };

    if (btn) {
      btn.addEventListener("click", () => {
        const val = select ? select.value : "valid";
        const item = scenarios[val] || scenarios.valid;
        if (badge) {
          badge.className = `badge ${item.cls}`;
          badge.textContent = item.badge;
        }
        if (msg) {
          msg.innerHTML = item.text;
        }
        if (window.Nexus && typeof window.Nexus.toast === "function") {
          window.Nexus.toast(`Verification outcome: ${item.badge}`, item.cls === "badge-ok" ? "ok" : "warn");
        }
      });
    }
  }

  function initDemoLogin() {
    const buttons = document.querySelectorAll(".demo-btn");
    buttons.forEach(b => {
      b.addEventListener("click", async () => {
        const username = b.dataset.user;
        const password = b.dataset.pwd;
        if (!username || !password) return;

        const original = b.textContent;
        b.disabled = true;
        b.textContent = "Logging in...";

        try {
          const csrf = document.querySelector('meta[name="csrf-token"]')?.getAttribute("content") || "";
          const res = await fetch("/api/auth/login", {
            method: "POST",
            headers: {
              "Content-Type": "application/json",
              "X-CSRFToken": csrf
            },
            body: JSON.stringify({ username, password })
          });
          const data = await res.json();
          if (data.ok) {
            window.location.href = data.data?.redirect_url || "/dashboard";
          } else {
            alert(data.error?.message || "Sign in failed");
            b.disabled = false;
            b.textContent = original;
          }
        } catch (e) {
          alert("Network error. Please try again.");
          b.disabled = false;
          b.textContent = original;
        }
      });
    });
  }
})();
