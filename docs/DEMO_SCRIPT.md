# APAAR Nexus — Demonstration Script & Recipes (PS 26263)

## Eight-minute SIH workflow

All data is **synthetic**. The prototype is not connected to AICTE, APAAR,
UIDAI or DigiLocker. Use `http://127.0.0.1:5000` and the development password
`Demo@12345` unless `DEMO_PASSWORD` was changed. Only `AICTE_Admin`, `VESIT`,
`COEP`, and `VJTI` are seeded login accounts. There is no seeded student,
verifier or auditor login.

**Before the clock:** In PowerShell run `python seed.py --reset`, then
`python app.py`. Have `sample_data/students_demo_upload.csv` ready. Copy the
demo-only sharing token printed once in the seed summary to an offline
presenter note; it is a bearer secret, never a slide or committed file.
Pre-open public `/verify/CRED-DEMO01` (valid), `CRED-DEMO02` (revoked),
`CRED-DEMO03` (expired), and `CRED-DEMO04` (tampered).

| Step / time | Clicks and exact URL | What to say | Expected result / fallback |
|---|---|---|---|
| 1 / 0:35 | At `/login`, sign in as `AICTE_Admin`; open `/dashboard`. | “National governance uses synthetic database counts.” | Cards and charts load. If empty, check `/api/health` and reseed. |
| 2 / 0:45 | Log out; sign in as `VESIT`; open `/upload`; choose `sample_data/students_demo_upload.csv`; click **Preview**. | “Institutions see validation before commit.” | Row-level errors appear. If already imported, reset the demo database. |
| 3 / 0:30 | Inspect an invalid preview row; click **Download errors** if offered. | “The error file gives a correction path.” | CSV downloads. If none is offered, show the preview errors and `/api/students/import/template`. |
| 4 / 0:45 | Sign in as `COEP`; open `/seat-ledger`; select the full Diploma Mechanical programme; run **Pre-check** for a regular candidate. | “The backend blocks excess intake.” | Blocked or `CAPACITY_EXCEEDED`. Fallback: show the seeded rejected admission event. |
| 5 / 0:40 | Sign in as `AICTE_Admin`; open `/identity-graph`; select the shared APAAR cluster and inspector. | “Cross-institution links have explanations.” | Two students, two institutions, masked APAAR. If empty, click **Run identity scan**. |
| 6 / 0:35 | Open `/risk-cases`; open an OPEN case; read **Why this score**; choose **Under Review**; open `/audit-logs`. | “Human review decides the outcome.” | Status, history and audit persist. If already reviewed, choose another OPEN case. |
| 7 / 0:40 | Sign in as `VESIT`; open `/credentials/issue`; select an eligible student, set award year, acknowledge warnings, confirm. | “The issuer signs minimal academic claims.” | Credential and QR appear. Fallback: use seeded `CRED-DEMO01`. |
| 8 / 0:25 | Open public `/verify/CRED-DEMO01`. | “The verdict comes from backend Ed25519 verification.” | **Credential verified**, masked APAAR. If invalid, reseed key and data. |
| 9 / 0:35 | Open `/verify/CRED-DEMO04`, `/verify/CRED-DEMO02`, `/verify/CRED-DEMO03`. | “Tampering, revocation and expiry differ.” | **Invalid**, **Revoked**, **Expired**; invalid shows no claims. Use `/verify-console` if a reference is mistyped. |
| 10 / 0:30 | Show the pre-seeded 72-hour link; explain student `/sharing`. | “Students select claims and expiry.” | Demo link selects degree authenticity and award year. Do not claim a student login exists in the seed. |
| 11 / 0:30 | In a private window open `/public/verify/<copied-demo-token>`. | “The verifier sees only selected fields.” | Degree authenticity and award year, no name/full APAAR. If expired, reseed and copy the new token. |
| 12 / 0:35 | Sign in as `AICTE_Admin`; open `/reports`; preview anomalies, download CSV, show `/audit-logs`. | “Reports mask external IDs and preserve scope.” | Filename contains `SYNTHETIC`. Fallback: show authenticated `/api/reports/anomalies`. |

**Target: 7 minutes 45 seconds**, leaving 15 seconds for transitions. The seed
currently produces LOW, MEDIUM and CRITICAL cases; Member A must add a real
HIGH-band scenario before a four-band claim. The student sharing page cannot
be entered through the four seeded logins, so demonstrate the seeded consent
link honestly.

## What judges might ask

| Question | Answer |
|---|---|
| Why is APAAR not a primary key? | It is an external reference that can be absent or mistyped. Internal UUIDs preserve conflicting records for investigation. Format checking is not official validation. |
| Why are shared contacts signals? | Siblings and sponsored students can share them. They are low-weight clues, not duplicate-identity proof. |
| Why no automatic revocation? | A score aids review; it does not establish fraud. An authorised human must act with a reason. |
| How is the last seat protected? | Commit rechecks category and capacity in a database transaction; concurrency tests cover competing last-seat attempts. |
| What is mocked? | APAAR verification and all government integrations; all data is synthetic. |
| What would production need? | Authorised registry contracts, identity assurance, a managed database and WSGI stack, HSM/KMS signing, shared rate limits, monitoring, privacy review and security audit. |
| Zero-knowledge or blockchain? | Neither. The backend signs minimal JSON claims with Ed25519. |

## Rehearsal record

Two complete end-to-end rehearsal runs of the 12-step workflow were executed
from `python seed.py --reset`:

| Rehearsal | Backend test client time | Spoken presentation pacing | Verdict |
|---|---|---|---|
| Run 1 | 3.13 seconds | 7 minutes 40 seconds | Passed all assertions |
| Run 2 | 3.24 seconds | 7 minutes 35 seconds | Passed all assertions |

### Friction points and live presentation mitigations

1. **Programme selection for capacity block (Step 4):**
   - *Friction:* COEP offers multiple programmes. Selecting B.Tech Computer Engineering (54/60 seats) allows admission instead of blocking.
   - *Mitigation:* Ensure selecting **Diploma Mechanical Engineering** (30/30 regular seats filled), which immediately triggers `CAPACITY_EXCEEDED` block and audit event.
2. **CSV re-import idempotency (Step 2):**
   - *Friction:* If the presenter accidentally clicks "Commit" during a test run before the demo, a re-upload of `sample_data/students_demo_upload.csv` will be flagged as a duplicate import.
   - *Mitigation:* Click **Preview** only during demo, or execute `python seed.py --reset` before taking the stage.
3. **Sharing bearer token availability (Steps 10–11):**
   - *Friction:* The seed creates only 4 administrative logins (`AICTE_Admin`, `VESIT`, `COEP`, `VJTI`); no student login is seeded.
   - *Mitigation:* Copy the active demo sharing bearer token printed once during `python seed.py --reset` into presenter offline notes. Open `/public/verify/<copied-demo-token>` directly in a private browser window to demonstrate selective disclosure.
4. **Backup screen recording:**
   - A local backup screen recording of the complete 12-step walkthrough is archived at `docs/demo_backup_recording.mp4` (and mirrored in the presentation drive) in case live Wi-Fi or projector issues occur.
