/**
 * APAAR Nexus — Landing Page & Interactive Tour Controller
 * Plain JavaScript (ES6+), zero external build step or CDN dependencies.
 */
(() => {
  "use strict";

  // State
  let currentStep = 1;
  const totalSteps = 4;
  let autoplayTimer = null;

  // Initialize once DOM is ready
  document.addEventListener("DOMContentLoaded", () => {
    initTour();
    initApaarSimulator();
    initLedgerSimulator();
    initRiskCalculator();
    initVerificationSandbox();
    initQuickLogin();
  });

  /* -------------------------------------------------------------
   * 1. Interactive System Tour
   * ------------------------------------------------------------- */
  function initTour() {
    const prevBtn = document.getElementById("tour-prev-btn");
    const nextBtn = document.getElementById("tour-next-btn");
    const autoplayBtn = document.getElementById("tour-autoplay-btn");
    const heroBtn = document.getElementById("hero-start-tour-btn");
    const tabBtns = document.querySelectorAll(".tour-tab-btn");

    if (prevBtn) prevBtn.addEventListener("click", () => setStep(currentStep - 1));
    if (nextBtn) nextBtn.addEventListener("click", () => setStep(currentStep + 1));

    if (autoplayBtn) {
      autoplayBtn.addEventListener("click", () => {
        if (autoplayTimer) {
          stopAutoplay();
        } else {
          startAutoplay();
        }
      });
    }

    if (heroBtn) {
      heroBtn.addEventListener("click", () => {
        setStep(1);
        const tourCard = document.getElementById("system-tour");
        if (tourCard) tourCard.scrollIntoView({ behavior: "smooth" });
      });
    }

    tabBtns.forEach(btn => {
      btn.addEventListener("click", () => {
        const step = parseInt(btn.dataset.step, 10);
        if (!isNaN(step)) {
          stopAutoplay();
          setStep(step);
        }
      });
    });

    updateStepUI();
  }

  function setStep(step) {
    if (step < 1) step = 1;
    if (step > totalSteps) step = 1; // loop on autoplay or next
    currentStep = step;
    updateStepUI();
  }

  function startAutoplay() {
    const autoplayBtn = document.getElementById("tour-autoplay-btn");
    if (autoplayBtn) {
      autoplayBtn.textContent = "Pause Auto Play";
      autoplayBtn.classList.remove("btn-secondary");
      autoplayBtn.classList.add("btn");
    }
    autoplayTimer = setInterval(() => {
      setStep(currentStep === totalSteps ? 1 : currentStep + 1);
    }, 5000);
  }

  function stopAutoplay() {
    if (autoplayTimer) {
      clearInterval(autoplayTimer);
      autoplayTimer = null;
    }
    const autoplayBtn = document.getElementById("tour-autoplay-btn");
    if (autoplayBtn) {
      autoplayBtn.textContent = "Auto Play";
      autoplayBtn.classList.add("btn-secondary");
      autoplayBtn.classList.remove("btn");
    }
  }

  function updateStepUI() {
    // Update badge
    const badge = document.getElementById("tour-indicator-badge");
    if (badge) badge.textContent = `Stage ${currentStep} of ${totalSteps}`;

    // Update buttons
    const prevBtn = document.getElementById("tour-prev-btn");
    const nextBtn = document.getElementById("tour-next-btn");
    if (prevBtn) prevBtn.disabled = currentStep === 1;
    if (nextBtn) nextBtn.textContent = currentStep === totalSteps ? "Restart Tour" : "Next Stage →";

    // Update tab styles
    const tabBtns = document.querySelectorAll(".tour-tab-btn");
    tabBtns.forEach(btn => {
      const step = parseInt(btn.dataset.step, 10);
      if (step === currentStep) {
        btn.classList.remove("btn-secondary");
        btn.classList.add("btn");
        btn.setAttribute("aria-selected", "true");
      } else {
        btn.classList.add("btn-secondary");
        btn.classList.remove("btn");
        btn.setAttribute("aria-selected", "false");
      }
    });

    // Toggle panels
    for (let i = 1; i <= totalSteps; i++) {
      const panel = document.getElementById(`tour-stage-${i}`);
      if (panel) {
        panel.style.display = i === currentStep ? "block" : "none";
      }
    }
  }

  /* -------------------------------------------------------------
   * 2. Stage 1: APAAR Format Simulator
   * ------------------------------------------------------------- */
  function initApaarSimulator() {
    const input = document.getElementById("sim-apaar-input");
    const checkBtn = document.getElementById("sim-apaar-check-btn");
    const badge = document.getElementById("sim-apaar-badge");
    const details = document.getElementById("sim-apaar-details");

    function validate() {
      if (!input || !badge || !details) return;
      const val = (input.value || "").trim();
      const is12Digits = /^\d{12}$/.test(val);

      if (is12Digits) {
        badge.className = "badge badge-ok";
        badge.textContent = "VALID FORMAT";
        const masked = "XXXXXXXX" + val.slice(8);
        details.innerHTML = `Format Check: <strong>Passed</strong> (12 Digits) &bull; Masked Display: <code>${masked}</code>`;
      } else {
        badge.className = "badge badge-danger";
        badge.textContent = "INVALID FORMAT";
        let reason = "Must contain exactly 12 numeric digits.";
        if (/[^0-9]/.test(val)) reason = "Contains non-numeric characters.";
        else if (val.length < 12) reason = `Too short (${val.length}/12 digits).`;
        else if (val.length > 12) reason = `Too long (${val.length}/12 digits).`;
        details.innerHTML = `Format Check: <strong>Failed</strong> &bull; Reason: <span style="color: var(--danger);">${reason}</span>`;
      }
    }

    if (checkBtn) checkBtn.addEventListener("click", validate);
    if (input) {
      input.addEventListener("input", validate);
      input.addEventListener("keydown", (e) => {
        if (e.key === "Enter") {
          e.preventDefault();
          validate();
        }
      });
    }
  }

  /* -------------------------------------------------------------
   * 3. Stage 2: Dynamic Seat Ledger Simulator
   * ------------------------------------------------------------- */
  function initLedgerSimulator() {
    const btn120 = document.getElementById("sim-ledger-btn-120");
    const btn121 = document.getElementById("sim-ledger-btn-121");
    const resetBtn = document.getElementById("sim-ledger-reset-btn");
    const countLabel = document.getElementById("sim-ledger-count-label");
    const progress = document.getElementById("sim-ledger-progress");
    const feedback = document.getElementById("sim-ledger-feedback");

    if (btn120) {
      btn120.addEventListener("click", () => {
        if (countLabel) countLabel.textContent = "120 / 120 (FULL)";
        if (progress) {
          progress.style.width = "100%";
          progress.style.background = "var(--navy)";
        }
        if (feedback) {
          feedback.innerHTML = `
            <span class="badge badge-ok" style="margin-bottom: 4px;">STATUS: ADMISSION ACCEPTED</span>
            <div style="color: var(--text); margin-top: 4px;">
              <strong>Student #120 enrolled in Regular quota.</strong><br>
              Regular capacity is now <strong>120/120 (100% Full)</strong>. Supernumerary remaining: <strong>12/12</strong>. Any further regular admission attempts will be blocked by the ledger.
            </div>
          `;
        }
      });
    }

    if (btn121) {
      btn121.addEventListener("click", () => {
        if (countLabel) countLabel.textContent = "120 / 120 (BLOCKED)";
        if (progress) {
          progress.style.width = "100%";
          progress.style.background = "var(--danger)";
        }
        if (feedback) {
          feedback.innerHTML = `
            <span class="badge badge-danger" style="margin-bottom: 4px;">BLOCKED: CAPACITY_EXCEEDED</span>
            <div style="color: var(--danger); margin-top: 4px; font-weight: 500;">
              <strong>Admission Rejected!</strong> Regular intake limit of 120 reached for programme Computer Engineering.<br>
              <span style="color: var(--text); font-size: 0.8rem;">
                Policy Violation: Regular admissions cannot be assigned to supernumerary slots (EWS/TFWS). An excess intake warning event has been recorded in the regulatory audit trail.
              </span>
            </div>
          `;
        }
      });
    }

    if (resetBtn) {
      resetBtn.addEventListener("click", () => {
        if (countLabel) countLabel.textContent = "119 / 120";
        if (progress) {
          progress.style.width = "99.1%";
          progress.style.background = "var(--navy)";
        }
        if (feedback) {
          feedback.innerHTML = `
            <span class="badge badge-info" style="margin-bottom: 4px;">STATUS: STANDBY</span>
            <div>Current status: 119 admitted, 1 regular seat remaining. Try admitting student #120 or testing excess student #121.</div>
          `;
        }
      });
    }
  }

  /* -------------------------------------------------------------
   * 4. Stage 3: Risk Score Calculator
   * ------------------------------------------------------------- */
  function initRiskCalculator() {
    window.updateTourRiskScore = function() {
      const r1 = document.getElementById("risk-rule-1");
      const r2 = document.getElementById("risk-rule-2");
      const r3 = document.getElementById("risk-rule-3");
      const label = document.getElementById("tour-risk-label");
      const badge = document.getElementById("tour-risk-badge");

      let score = 0;
      if (r1 && r1.checked) score += 40;
      if (r2 && r2.checked) score += 35;
      if (r3 && r3.checked) score += 10;
      score = Math.min(100, score);

      let severity = "LOW";
      let badgeKind = "badge-ok";
      let actionText = "ACTION: MONITOR";

      if (score >= 80) {
        severity = "CRITICAL SEVERITY";
        badgeKind = "badge-danger";
        actionText = "ACTION: IMMEDIATE AICTE ESCALATION";
      } else if (score >= 60) {
        severity = "HIGH SEVERITY";
        badgeKind = "badge-danger";
        actionText = "ACTION: ESCALATE TO AICTE";
      } else if (score >= 30) {
        severity = "MEDIUM SEVERITY";
        badgeKind = "badge-warn";
        actionText = "ACTION: CLARIFICATION REQUESTED";
      }

      if (label) label.textContent = `${score} / 100 • ${severity}`;
      if (badge) {
        badge.className = `badge ${badgeKind}`;
        badge.textContent = actionText;
      }
    };
  }

  /* -------------------------------------------------------------
   * 5. Verification Sandbox
   * ------------------------------------------------------------- */
  function initVerificationSandbox() {
    const select = document.getElementById("sandbox-sample-select");
    const preview = document.getElementById("sandbox-payload-preview");
    const verifyBtn = document.getElementById("sandbox-verify-btn");
    const statusBadge = document.getElementById("sandbox-status-badge");
    const detailsContent = document.getElementById("sandbox-details-content");

    const samples = {
      valid: {
        json: {
          credential_ref: "CRED-VESIT-2024-001",
          institution: "VESIT Mumbai",
          programme: "Bachelor of Technology (Computer Engineering)",
          student_apaar: "XXXXXXXX9012",
          student_name: "Tanmay Shelar",
          award_year: 2024,
          status: "ISSUED",
          signature: "ed25519:7a41...98ce"
        },
        badgeClass: "badge-ok",
        badgeText: "VERIFIED",
        html: `
          <div><strong>Issuer:</strong> VESIT Mumbai (AICTE ID: 1-1092384)</div>
          <div><strong>Student:</strong> Synthetic Student (Masked APAAR: <code>XXXXXXXX9012</code>)</div>
          <div><strong>Award:</strong> Bachelor of Technology (Computer Engineering) &bull; Class: First Class Dist.</div>
          <div><strong>Digital Signature:</strong> <span style="color: var(--ok-ink); font-weight: 600;">Match Valid (Ed25519 keypair #vesit-root)</span></div>
          <div style="color: var(--muted); font-size: 0.8rem; margin-top: 4px;">Proof valid. Tamper check passed. Status confirmed active on issuing ledger.</div>
        `
      },
      revoked: {
        json: {
          credential_ref: "CRED-COEP-2024-999",
          institution: "COEP Pune",
          programme: "Master of Technology (Data Science)",
          student_apaar: "XXXXXXXX4567",
          student_name: "Rohan Patil",
          award_year: 2024,
          status: "REVOKED",
          signature: "ed25519:b234...12cd"
        },
        badgeClass: "badge-danger",
        badgeText: "REVOKED",
        html: `
          <div><strong>Issuer:</strong> COEP Pune (AICTE ID: 1-2094182)</div>
          <div><strong>Student:</strong> Synthetic Student (Masked APAAR: <code>XXXXXXXX4567</code>)</div>
          <div><strong>Award:</strong> Master of Technology (Data Science)</div>
          <div><strong>Digital Signature:</strong> <span style="color: var(--ok-ink); font-weight: 600;">Signature Valid (Ed25519 keypair #coep-root)</span></div>
          <div style="color: var(--danger); font-size: 0.85rem; margin-top: 4px; font-weight: 600;">
            Revocation Notice: This credential was revoked by the issuing institution on 2026-09-15. Status: NOT VALID FOR EMPLOYMENT.
          </div>
        `
      },
      tampered: {
        json: {
          credential_ref: "CRED-TAMPERED-SAMPLE",
          institution: "VJTI Mumbai",
          programme: "Bachelor of Technology (Mechanical Engineering)",
          student_apaar: "XXXXXXXX3321",
          award_year: 2025,
          grade_altered: "Original: 7.2 CGPA -> Altered to: 9.8 CGPA",
          signature: "ed25519:invalid_hash"
        },
        badgeClass: "badge-danger",
        badgeText: "INVALID (TAMPERED)",
        html: `
          <div><strong>Cryptographic Verification Failed:</strong> Asymmetric signature mismatch.</div>
          <div><strong>Tamper Signal:</strong> Payload hash does not match issuer's public key certificate digest.</div>
          <div style="color: var(--danger); font-size: 0.85rem; margin-top: 4px; font-weight: 600;">
            Status: REJECTED. The credential payload has been modified post-issuance.
          </div>
        `
      }
    };

    function updatePreview() {
      if (!select || !preview) return;
      const key = select.value;
      const sample = samples[key] || samples.valid;
      preview.textContent = JSON.stringify(sample.json, null, 2);
    }

    if (select) {
      select.addEventListener("change", updatePreview);
      updatePreview();
    }

    if (verifyBtn) {
      verifyBtn.addEventListener("click", () => {
        const key = select ? select.value : "valid";
        const sample = samples[key] || samples.valid;
        if (statusBadge) {
          statusBadge.className = `badge ${sample.badgeClass}`;
          statusBadge.textContent = sample.badgeText;
        }
        if (detailsContent) {
          detailsContent.innerHTML = sample.html;
        }
        if (window.Nexus && typeof window.Nexus.toast === "function") {
          window.Nexus.toast(`Verification executed: Result ${sample.badgeText}`, sample.badgeClass === "badge-ok" ? "ok" : "warn");
        }
      });
    }
  }

  /* -------------------------------------------------------------
   * 6. Quick Demo Login Buttons
   * ------------------------------------------------------------- */
  function initQuickLogin() {
    const buttons = document.querySelectorAll(".quick-login-btn");
    buttons.forEach(btn => {
      btn.addEventListener("click", async () => {
        const username = btn.dataset.username;
        const password = btn.dataset.password;
        if (!username || !password) return;

        const originalText = btn.textContent;
        btn.disabled = true;
        btn.textContent = "Signing in...";

        try {
          const csrfToken = document.querySelector('meta[name="csrf-token"]')?.getAttribute("content") || "";
          const response = await fetch("/api/auth/login", {
            method: "POST",
            headers: {
              "Content-Type": "application/json",
              "X-CSRFToken": csrfToken
            },
            body: JSON.stringify({ username, password })
          });

          const result = await response.json();
          if (result.ok) {
            if (window.Nexus && typeof window.Nexus.toast === "function") {
              window.Nexus.toast(`Signed in as ${username}`, "ok");
            }
            window.location.href = result.data?.redirect_url || "/dashboard";
          } else {
            alert(result.error?.message || "Sign in failed");
            btn.disabled = false;
            btn.textContent = originalText;
          }
        } catch (err) {
          alert("Error connecting to server. Please try again.");
          btn.disabled = false;
          btn.textContent = originalText;
        }
      });
    });
  }
})();
