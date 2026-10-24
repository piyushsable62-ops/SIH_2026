# APAAR Nexus — Demonstration Script & Recipes (PS 26263)

## Detect and Prove synthetic examples

After `python seed.py --reset`, Member B's seed hook runs the identity scan and
risk evaluation, records the two rejected capacity attempts, and creates four
fixed credential references. Open `/verify-console` to show `CRED-DEMO01`
(valid), `CRED-DEMO02` (revoked), `CRED-DEMO03` (expired), and `CRED-DEMO04`
(deliberately altered after signing). These are development examples only.

The hook also creates active, expired, and revoked sharing sessions. The active
session shares **only** degree authenticity and award year for 72 hours. Its
demo-only bearer token is printed once in the seed summary. Copy that token
then and open `/public/verify/<token>` in a private window. The token is stored
only as a SHA-256 digest in the database; do not reuse this development secret
or the development signing key in production.

Current Member A seed scenarios produce 11 cases across LOW, MEDIUM and
CRITICAL. A distinct HIGH case scenario is needed in `seed.py` for the full
four-band demonstration; the Member B seed hook reports this gap instead of
fabricating a case.

This document provides step-by-step instructions and recipes for live demonstrations of the APAAR Nexus prototype.

---

## Pillar 1: Prevent — Dynamic Seat Ledger & Intake Enforcement (Task A10)

### Background Context
- **Institution:** Vivekanand Education Society's Institute of Technology (`VESIT`)
- **Programme:** B.Tech Information Technology (`BTECH-IT`)
- **Academic Year:** `2026-27`
- **Approved Regular Intake:** `120`
- **Supernumerary Capacity:** `10`
- **Baseline Seeded State:**
  - Regular Admitted: `116` / `120` (Remaining: `4`, Utilisation: `96.7%`, Warning: `90% amber badge`)
  - Supernumerary Admitted: `4` / `10` (Remaining: `6`, Utilisation: `40.0%`)

---

### Demo Recipe: Filling the Remaining 4 Seats and Blocking the 121st Regular Admission

This recipe demonstrates the **Prevent Engine** in real-time on the `/seat-ledger` page.

#### Step 1: Login as Institution Administrator
1. Navigate to `/login`.
2. Login with credentials:
   - **Username:** `VESIT`
   - **Password:** `Demo@12345`
3. Click **Seat Ledger** in the sidebar navigation or browse to `/seat-ledger`.
4. Verify the top summary bar shows:
   - Approved Regular: **120**
   - Admitted Regular: **116**
   - Remaining Regular: **4**
   - Utilisation Bar: **96.7%** with an amber **Warning: High Utilisation (90%)** badge.

---

#### Step 2: Use the Demo Autofill Helper to Admit Seats 117 to 120

In the **Admission Pre-check & Verification** panel on the right:
1. Click the small link: **"Fill demo values (VESIT IT 116+4)"**.
   - Notice the form pre-fills with Student 117:
     - Name: `Candidate 117 (Demo Fill)`
     - Student ID: `V-2026-117`
     - APAAR: `987654320117`
     - Category: `OPEN`
     - Classification: `REGULAR`
2. Click **Run Pre-check Simulation**:
   - The result panel immediately shows a green box:
     > **Pre-check Result: ALLOWED**  
     > *Eligible for REGULAR admission (4 seats remaining).*
   - Below it shows the simulated post-admission status:
     - Admitted: `117` | Remaining: `3`
3. Click **Commit Admission**:
   - Success toast appears: *"Admission committed successfully for V-2026-117"*.
   - Stat cards and the utilisation bar update automatically: Admitted is now **117**, Remaining is **3**.
   - The event appears at the top of the **Admission Events & Historical Attempts** ledger as `ACCEPTED` with reason `APPROVED`.

4. Repeat for Student 118, 119, and 120:
   - **Student 118:** Name `Candidate 118`, ID `V-2026-118`, APAAR `987654320118`, Category `OBC`. Click Pre-check -> Allowed (2 remaining) -> Commit.
   - **Student 119:** Name `Candidate 119`, ID `V-2026-119`, APAAR `987654320119`, Category `SC`. Click Pre-check -> Allowed (1 remaining) -> Commit.
   - **Student 120:** Name `Candidate 120`, ID `V-2026-120`, APAAR `987654320120`, Category `OPEN`. Click Pre-check -> Allowed (0 remaining) -> Commit.
5. After Student 120 is committed:
   - Admitted Regular: **120 / 120** (100.0% utilisation).
   - Remaining Regular: **0**.
   - Utilisation Bar turns red with a **Critical: Capacity Reached (100%)** badge.

---

#### Step 3: Attempt the 121st Regular Admission (The Hard Block)

Now demonstrate that APAAR Nexus **strictly blocks** admissions beyond approved AICTE intake:

1. Fill the form for the 121st student:
   - **Full Name:** `Excess Applicant`
   - **Student ID:** `V-2026-121`
   - **APAAR Reference:** `987654320121`
   - **Category:** `OPEN`
   - **Classification:** `REGULAR`
2. Click **Run Pre-check Simulation**:
   - The result panel turns red:
     > **Pre-check Result: BLOCKED**  
     > *Approved regular intake is 120 and 120 regular admissions already exist; admission was blocked.*
   - The **Commit Admission** button is disabled.
   - An audit log event (`ADMISSION_PRECHECK_REJECTED`) is written.
3. If attempted directly via the API (`POST /api/admissions`):
   ```bash
   curl -X POST http://127.0.0.1:5000/api/admissions \
     -H "Content-Type: application/json" \
     -d '{
       "institution_id": "<vesit_id>",
       "programme_id": "<vesit_it_id>",
       "academic_year": "2026-27",
       "institution_student_id": "V-2026-121",
       "full_name": "Excess Applicant",
       "apaar_ref": "987654320121",
       "category": "OPEN",
       "classification": "REGULAR"
     }'
   ```
   - Returns HTTP `409 CONFLICT`:
     ```json
     {
       "ok": false,
       "error": {
         "code": "CAPACITY_EXCEEDED",
         "message": "Approved regular intake is 120 and 120 regular admissions already exist; admission 121 was blocked.",
         "details": {
           "approved_regular_intake": 120,
           "current_regular_admissions": 120,
           "classification": "REGULAR"
         }
       }
     }
     ```
4. Verify in the UI:
   - In the **Admission Events & Historical Attempts** table, the blocked attempt appears with:
     - Result badge: `REJECTED` (red badge)
     - Reason Code: `CAPACITY_EXCEEDED`
     - Explanation: *"Approved regular intake is 120 and 120 regular admissions already exist; admission 121 was blocked."*

---

#### Step 4: Supernumerary Strict Separation

Demonstrate that international/NRI quota cannot leak into regular intake:
1. Try admitting an `NRI` student under `REGULAR` classification:
   - Name: `Foreign Applicant`
   - ID: `V-2026-NRI-01`
   - APAAR: `987654320199`
   - Category: `NRI`
   - Classification: `REGULAR`
2. Click **Run Pre-check Simulation**:
   - Result: **BLOCKED** with reason `CATEGORY_NOT_ELIGIBLE`:
     > *Category 'NRI' is not eligible for REGULAR seats under configured intake rules.*
3. Now switch classification to `SUPERNUMERARY`:
   - Click **Run Pre-check Simulation**:
   - Result: **ALLOWED** (Supernumerary pool has 6 remaining seats).

---

## Summary of Verification Guarantees
1. **Intake Cap Invariance:** Admissions strictly halt when `admitted == approved`.
2. **Quota Segregation:** Regular and Supernumerary pools never cross-subsidize.
3. **Auditability:** Every rejection is recorded in `admission_events` with an explanation.
4. **Multi-tenant Security:** Institutions cannot view or admit students for peer institutions.
