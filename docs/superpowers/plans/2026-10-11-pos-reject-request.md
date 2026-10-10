# POS Reject Request Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Kru outlet melaporkan barang reject (ga laku / gagal produksi) ke HO; HO memutus beban (pabrik/kru) atau menolak; barang dimusnahkan dengan bukti foto.

**Architecture:** Satu DocType submittable `POS Reject Request` (+ child item) dan single `POS Reject Settings`. Semua logika di `pos_next/services/reject_request.py`; endpoint tipis di `pos_next/api/reject_request.py` (pola sama dengan `api/production.py`). Stok pakai Stock Entry native (Material Transfer ke gudang reject, Material Issue saat pemusnahan). HO bekerja di Desk; kru di dialog Vue POS.

**Tech Stack:** ERPNext v16 / Frappe (Python), Vue 3 + vitest.

**Spec:** `docs/superpowers/specs/2026-10-11-pos-reject-request-design.md` (D1–D15)

## Global Constraints

- Semua perintah bench/python lewat container: `docker exec erpnext16_dev-frappe-1 bash -lc "cd /workspace/development/frappe-bench && <cmd>"`.
- Test BE serial saja: `./env/bin/python apps/pos_next/pos_next/_pn_run_tests.py <modul>`. Jangan dua proses paralel.
- Test FE: `cd POS && npx vitest run <file>`.
- Role yang ada: `POSNext Cashier`, `POSNext Manager`. Role baru: `POSNext Reject Approver` (tambahkan ke `fixtures/role.json` dan filter `fixtures` di `hooks.py`).
- Nilai `purpose`: `Unsold`, `Production Loss`. Nilai `status`: `Draft`, `Pending`, `Approved`, `Rejected`, `Closed`, `Cancelled`. Nilai `decision`: `Write Off`, `Charge Crew`, `Rejected`. Nilai `late_mode`: `Lenient`, `Strict`. Literal Inggris kanonik (UI menerjemahkan via `__()`).
- Foto bukti: 1–5 File terlampir dengan `attached_to_field = "evidence"`. Foto pemusnahan: ≥1 File dengan `attached_to_field = "destruction"`. Link video harus diawali `http://` atau `https://`.
- Semua tulis stok `flags.ignore_permissions = True` setelah gate akses (pola `services/production.py`).
- Akses outlet: `_assert_profile_access(pos_profile)` dari `pos_next.api.packages`.
- Deploy butuh `bench migrate`.

## Review Focus

1. Approver yang juga owner dokumen (mis. manager yang punya role approver) mencoba memutus pengajuannya sendiri → ditolak. Test di Task 3.
2. Stok di gudang outlet kurang dari qty yang diajukan (barang sudah terjual) → submit gagal sebelum ada Stock Entry. Test di Task 2.
3. Work Order yang sama selesai/ditutup dua kali jalur (finish lalu close) → tetap satu pengajuan Production Loss. Test di Task 4.
4. Kru mengubah qty item pada pengajuan Production Loss buatan sistem → ditolak saat validate. Test di Task 4.
5. Keputusan dipanggil dua kali (klik ganda di Desk) → panggilan kedua gagal, tanpa Stock Entry ganda. Test di Task 3.

---

### Task 1: Skema — DocType, settings, role, gudang reject

**Files:**
- Create: `pos_next/pos_next/doctype/pos_reject_settings/{__init__.py,pos_reject_settings.json,pos_reject_settings.py}`
- Create: `pos_next/pos_next/doctype/pos_reject_request/{__init__.py,pos_reject_request.json,pos_reject_request.py,pos_reject_request.js}`
- Create: `pos_next/pos_next/doctype/pos_reject_request_item/{__init__.py,pos_reject_request_item.json,pos_reject_request_item.py}`
- Modify: `pos_next/pos_next/doctype/pos_settings/pos_settings.json` (field `reject_warehouse`, Link Warehouse, di section produksi)
- Modify: `pos_next/fixtures/role.json`, `pos_next/hooks.py` (fixtures filter)
- Test: `pos_next/api/test_reject_request.py`

**Interfaces:**
- Produces: DocType `POS Reject Settings` (single): `deadline_days` Int default 3, `production_loss_deadline_days` Int default 0 (0 = pakai global), `late_mode` Select `Lenient\nStrict` default `Lenient`, `monthly_limit_amount` Currency default 0 (0 = off), `notify_emails` Small Text (satu email per baris).
- Produces: DocType `POS Reject Request` (submittable, autoname `format:PRJ-{YY}{MM}-{#####}`, `track_changes: 1`). Fields: `purpose` Select (reqd), `pos_profile` Link (reqd), `company` Link (read_only), `incident_date` Date (reqd), `deadline_date` Date (read_only), `reason` Small Text (reqd), `video_link` Data, `work_order` Link Work Order (read_only), `is_system_generated` Check (read_only), `items` Table `POS Reject Request Item` (reqd), `total_amount` Currency (read_only), `is_late` Check (read_only), `over_limit` Check (read_only), `status` Select (read_only, allow_on_submit, default `Draft`, in_standard_filter), `decision` Select (read_only, allow_on_submit), `decision_notes` Small Text (read_only, allow_on_submit), `decided_by` Link User (read_only, allow_on_submit), `decided_on` Datetime (read_only, allow_on_submit), `transfer_entry` / `issue_entry` Link Stock Entry (read_only, allow_on_submit), `closed_on` Datetime (read_only, allow_on_submit).
- Produces: child `POS Reject Request Item`: `item_code` Link Item (reqd), `item_name` Data (fetch), `qty` Float (reqd), `uom` Link UOM (read_only), `valuation_rate` Currency (read_only), `amount` Currency (read_only).
- Perms `POS Reject Request`: System Manager full; `POSNext Manager` & `POSNext Cashier` read/create/write/submit; `POSNext Reject Approver` read/report (keputusan lewat endpoint, bukan write). `POS Reject Settings`: System Manager + `POSNext Reject Approver` read/write.

- [ ] **Step 1: Tulis test gagal** `TestRejectSchema.test_doctypes_and_fields_exist`: untuk tiap doctype/field di atas `assertTrue(frappe.get_meta(dt).has_field(f))`; plus `POS Settings.reject_warehouse`; plus `frappe.db.exists("Role", "POSNext Reject Approver")`.
- [ ] **Step 2: Run** `_pn_run_tests.py pos_next.api.test_reject_request` → FAIL (DocType not found).
- [ ] **Step 3: Buat JSON doctype + role fixture + filter hooks** sesuai Interfaces. Controller `.py` kosong (`class X(Document): pass`) dulu; `.js` dibuat di Task 5.
- [ ] **Step 4: Run** `bench --site erpnext16.localhost migrate` lalu test → PASS.
- [ ] **Step 5: Commit** `feat(reject): doctype POS Reject Request + settings + role approver`

---

### Task 2: Pengajuan Unsold — validasi, tenggat, batas, transfer stok, notifikasi

**Files:**
- Create: `pos_next/services/reject_request.py`
- Modify: `pos_next/pos_next/doctype/pos_reject_request/pos_reject_request.py`
- Test: `pos_next/api/test_reject_request.py`

**Interfaces:**
- Consumes: Task 1 doctypes; `_resolve_profile` dari `pos_next.services.production`; `get_effective_pos_setting(pos_profile, "reject_warehouse")`.
- Produces (service):
  - `validate_request(doc) -> None` — dipanggil dari `validate` controller.
  - `on_submit_request(doc) -> None`, `on_cancel_request(doc) -> None`, `before_cancel_request(doc) -> None`.
  - `notify(users: list[str], subject: str, doc) -> None` — Notification Log (`enqueue_create_notification`) + `frappe.sendmail` ke `notify_emails` ketika `users` adalah approver.
  - `approver_users() -> list[str]` — user aktif dengan role `POSNext Reject Approver`.
  - `TRANSFER_REMARK_PREFIX = "POS Reject transfer: "`

`validate_request` (urutan):
1. `company` dari `_resolve_profile`. Item qty > 0 dan finite; `uom` = stock_uom; `valuation_rate` dari `Bin` gudang outlet (fallback `Item.valuation_rate`); `amount = qty * valuation_rate`; `total_amount` = jumlah.
2. `incident_date` tidak boleh di masa depan.
3. `deadline_date = incident_date + N` (N = `production_loss_deadline_days` jika purpose Production Loss dan > 0, else `deadline_days`).
4. `video_link` kosong atau diawali `http(s)://`.
5. Hanya saat submit (`doc._action == "submit"`): 1–5 File `attached_to_field="evidence"`; `is_late = today() > deadline_date`; `over_limit = monthly_limit_amount > 0 and (total_amount + total non-cancelled bulan incident_date di outlet ini) > monthly_limit_amount`.

`on_submit_request`:
- `status = "Pending"`.
- Purpose `Unsold`: gudang reject wajib ada (else throw "Set Reject Warehouse in POS Settings for {0}"); stok gudang outlet ≥ qty per item (else throw sebelum insert); Material Transfer outlet → reject (batch: FEFO via `_pick_batch` dari `services.production`), remark `TRANSFER_REMARK_PREFIX + doc.name`; simpan `transfer_entry`.
- Jika `is_late` dan `late_mode == "Strict"`: langsung `apply_decision(doc, "Charge Crew", "Auto: past deadline (Strict mode)", system=True)` (fungsi dari Task 3; pada Task 2 cukup panggil, test strict ada di Task 3).
- Selain itu `notify(approver_users(), "New reject request {name}", doc)` + komentar timeline.

`before_cancel_request`: throw kecuali `status == "Pending"`. `on_cancel_request`: cancel `transfer_entry` bila ada; `status = "Cancelled"`.

- [ ] **Step 1: Tulis test gagal** (`TestUnsoldSubmit`, fixture: item stok 10 di gudang outlet, `reject_warehouse` di-set, user cashier):
  - `test_submit_moves_stock_to_reject_warehouse`: qty 3 → Bin outlet turun 3, Bin reject naik 3, `status == "Pending"`, `transfer_entry` submitted.
  - `test_submit_requires_evidence_photo`: tanpa File evidence → `ValidationError`; 6 File → `ValidationError`.
  - `test_submit_fails_when_stock_short`: qty 11 → `ValidationError`, tidak ada Stock Entry baru dengan remark prefix.
  - `test_future_incident_date_rejected`, `test_bad_video_link_rejected` (`"drive.google.com/x"` gagal, `"https://drive.google.com/x"` lolos).
  - `test_deadline_and_late_flag`: `deadline_days=3`, incident 5 hari lalu → `is_late == 1`, `deadline_date == incident+3`.
  - `test_over_limit_flag`: `monthly_limit_amount` lebih kecil dari total → `over_limit == 1`; 0 → `0`.
  - `test_cancel_pending_returns_stock`: cancel → Bin outlet pulih, `transfer_entry` docstatus 2.
  - `test_approvers_notified`: setelah submit ada `Notification Log` untuk user approver dengan `document_name == doc.name`.
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** fungsi di atas; controller memanggil service di `validate`, `on_submit`, `before_cancel`, `on_cancel`.
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `feat(reject): submit Unsold — bukti, tenggat, batas bulanan, stok ke gudang reject`

---

### Task 3: Keputusan HO & penutupan dengan bukti pemusnahan

**Files:**
- Modify: `pos_next/services/reject_request.py`
- Test: `pos_next/api/test_reject_request.py`

**Interfaces:**
- Produces:
  - `apply_decision(doc, decision: str, notes: str | None, system: bool = False) -> dict` — doc payload.
  - `close_request(doc) -> dict`.
  - `ISSUE_REMARK_PREFIX = "POS Reject write-off: "`

`apply_decision`:
- Kunci baris (`frappe.db.get_value(..., for_update=True)`) lalu wajib `status == "Pending"` (klik ganda → throw).
- Jika bukan `system`: caller harus punya role `POSNext Reject Approver` (atau Administrator) → else `PermissionError`; caller ≠ `doc.owner` → else `PermissionError("You cannot decide your own request")`.
- `decision` ∈ 3 nilai; `notes` wajib untuk `Charge Crew` dan `Rejected`; `Rejected` hanya untuk `Unsold`.
- `Rejected`: Material Transfer balik reject → outlet (remark `TRANSFER_REMARK_PREFIX + name + " return"`), `status = "Rejected"`.
- `Write Off` / `Charge Crew`: `status = "Approved"`.
- Tulis `decision`, `decision_notes`, `decided_by` (`"Administrator"` bila system), `decided_on` via `db_set`; komentar timeline; `notify([doc.owner], "Reject request {name}: {decision}", doc)`.

`close_request`:
- Wajib `status == "Approved"`, ≥1 File `attached_to_field="destruction"`.
- `Unsold`: Material Issue dari gudang reject ke `Company.stock_adjustment_account` (pola `_make_material_issue`), remark `ISSUE_REMARK_PREFIX + name`; simpan `issue_entry`.
- `Production Loss`: tanpa stok.
- `status = "Closed"`, `closed_on = now()`.

- [ ] **Step 1: Tulis test gagal** (`TestDecision`, pakai helper submit dari Task 2):
  - `test_owner_cannot_decide_own_request`: owner yang diberi role approver → `PermissionError`.
  - `test_non_approver_cannot_decide` → `PermissionError`.
  - `test_reject_returns_stock_and_needs_notes`: tanpa notes → `ValidationError`; dengan notes → Bin outlet pulih, `status == "Rejected"`.
  - `test_double_decision_fails`: keputusan kedua → `ValidationError`; jumlah Stock Entry dengan remark prefix tidak bertambah.
  - `test_charge_crew_requires_notes`.
  - `test_strict_late_auto_charges_crew`: `late_mode="Strict"`, incident lewat tenggat → setelah submit `status == "Approved"`, `decision == "Charge Crew"`.
  - `test_close_requires_destruction_photo`: tanpa File destruction → `ValidationError`.
  - `test_close_writes_off_reject_stock`: Approved Write Off + File destruction → Bin reject turun ke 0, `issue_entry` submitted, `status == "Closed"`.
  - `test_owner_notified_of_decision`: Notification Log untuk owner.
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `feat(reject): keputusan HO (pemisahan tugas) + close dengan bukti pemusnahan`

---

### Task 4: Production Loss otomatis (anti-manipulasi)

**Files:**
- Modify: `pos_next/services/reject_request.py`, `pos_next/services/production.py` (`finish_production`, `close_production`)
- Create: `pos_next/tasks/reject_overdue.py`
- Modify: `pos_next/hooks.py` (`scheduler_events["daily"]`)
- Test: `pos_next/api/test_reject_request.py`

**Interfaces:**
- Produces:
  - `ensure_production_loss_request(wo) -> str | None` — nama draft atau None bila tidak ada loss.
  - `pos_next.tasks.reject_overdue.notify_overdue_drafts() -> int` — jumlah draft yang dilaporkan.

`ensure_production_loss_request(wo)`:
- `loss = flt(wo.qty) - flt(wo.produced_qty)`; dipanggil di akhir `finish_production` hanya jika `wo.status == "Completed"`, dan di akhir `close_production` (setelah status Closed). Jika `loss <= 0` → None.
- Idempoten: jika ada `POS Reject Request` dengan `work_order == wo.name` dan `docstatus != 2` → kembalikan namanya.
- Insert draft (`ignore_permissions`): purpose `Production Loss`, `pos_profile = wo.posa_pos_profile`, `incident_date = today()`, `reason = "Auto: production loss on {wo}"` (kru wajib mengganti — lihat validate), `work_order`, `is_system_generated = 1`, satu item `production_item` qty `loss`, `owner = wo.posa_operator`.
- Tambahan di `validate_request` untuk `is_system_generated`: item & qty harus sama persis dengan `wo.qty - wo.produced_qty` saat ini (kru tidak bisa mengecilkan), dan saat submit `reason` tidak boleh masih teks otomatis.

`notify_overdue_drafts()`: draft `is_system_generated=1` dengan `deadline_date < today()` → satu notifikasi "Unreported production loss" per draft ke approver, ditandai komentar agar tidak dikirim ulang setiap hari.

- [ ] **Step 1: Tulis test gagal** (`TestProductionLoss`, pakai fixture resep dari `test_production.py`):
  - `test_finish_with_loss_creates_draft`: produksi qty 10, good 8, loss 2 → satu draft, item qty 2, owner operator.
  - `test_finish_without_loss_creates_nothing`.
  - `test_close_after_partial_creates_single_request`: finish parsial lalu close → tetap satu request, qty = qty - produced.
  - `test_crew_cannot_shrink_system_qty`: ubah qty item jadi 1 lalu save → `ValidationError`.
  - `test_submit_requires_own_reason`: submit dengan reason otomatis → `ValidationError`.
  - `test_overdue_draft_notified_once`: draft lewat tenggat → `notify_overdue_drafts()` == 1, panggilan kedua == 0.
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** + daftarkan `pos_next.tasks.reject_overdue.notify_overdue_drafts` di scheduler daily.
- [ ] **Step 4: Run** `test_reject_request` dan `pos_next.api.test_production` → PASS (production tidak regresi).
- [ ] **Step 5: Commit** `feat(reject): draft Production Loss otomatis dari Work Order + notifikasi draft terlambat`

---

### Task 5: Endpoint API + Desk HO

**Files:**
- Create: `pos_next/api/reject_request.py`
- Modify: `pos_next/pos_next/doctype/pos_reject_request/pos_reject_request.js`
- Test: `pos_next/api/test_reject_request.py`

**Interfaces:**
- Produces (semua `@frappe.whitelist()`, gate `_assert_profile_access` untuk endpoint kru):
  - `get_reject_requests(pos_profile, status=None, limit=30, offset=0) -> {"requests": [...], "has_more": bool}` — tiap baris: `name, purpose, status, incident_date, deadline_date, total_amount, is_late, decision, work_order, evidence_count, destruction_count`.
  - `get_reject_request(name) -> dict` — doc + `items` + `evidence` + `destruction` (pakai `_attachments_summary` dari `api/purchase_orders.py`, difilter per `attached_to_field`).
  - `save_reject_request(data: str(JSON), submit=0) -> dict` — insert/update draft (`purpose`, `pos_profile`, `incident_date`, `reason`, `video_link`, `items[{item_code, qty}]`); submit bila `submit=1`.
  - `attach_reject_files(name, kind: "evidence" | "destruction", files: str(JSON [{file_name, filedata}])) -> dict` — File private dengan `attached_to_field = kind`. `evidence` hanya saat Draft; `destruction` hanya saat Approved.
  - `cancel_reject_request(name) -> dict`, `close_reject_request(name) -> dict`.
  - `decide_reject_request(name, decision, notes=None) -> dict` — HO, tanpa profile gate; role dicek di `apply_decision`.
- Desk JS: saat `status == "Pending"` dan user punya role approver, tombol grup "Decide" → `Write Off` / `Charge Crew` / `Rejected` membuka `frappe.prompt` notes (reqd untuk dua terakhir) → panggil `decide_reject_request` → `frm.reload_doc()`. Indicator list: Pending=orange, Approved=blue, Rejected=red, Closed=green, Draft=grey; tampilkan badge `Late` / `Over Limit`.

- [ ] **Step 1: Tulis test gagal** (`TestRejectApi`): `test_endpoints_gate_foreign_profile` (user tanpa akses outlet → `PermissionError` untuk `get_reject_requests`, `save_reject_request`); `test_save_submit_roundtrip` (save draft → attach evidence → submit → list memuat baris `status == "Pending"`, `evidence_count == 1`); `test_destruction_attach_only_when_approved`.
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** endpoint + JS Desk.
- [ ] **Step 4: Run** → PASS. Cek manual Desk: buka request Pending sebagai approver, tombol muncul; sebagai owner, keputusan ditolak.
- [ ] **Step 5: Commit** `feat(reject): endpoint POS + aksi keputusan di Desk`

---

### Task 6: UI POS — dialog pengajuan & bukti pemusnahan

**Files:**
- Create: `POS/src/components/pos/RejectRequestDialog.vue`, `POS/src/components/pos/RejectRequestDialog.test.js`
- Modify: `POS/src/components/pos/managementMenu.js` (item `{ id: "reject-request", icon: "alert-triangle", label: __("Reject Requests"), group: "stock", requiresOnline: true }`), `POS/src/components/pos/POSMenuDialog.vue` (host view baru, pola sama dengan `production`)

**Interfaces:**
- Consumes: endpoint Task 5.
- Dialog: tab daftar (filter status, badge Late), form baru (purpose `Unsold` saja — Production Loss hanya dari draft sistem; pilih item + qty, tanggal kejadian, alasan, link video, unggah 1–5 foto via `PurchaseAttachments.vue`), detail (draft Production Loss: qty read-only, isi alasan + foto lalu submit; Approved: unggah foto pemusnahan lalu tombol "Close").

- [ ] **Step 1: Tulis test gagal** (mock `call` seperti `ProductionDialog.test.js`):
  - `submit disabled until reason and 1 photo present`.
  - `submit sends save_reject_request with submit=1 after attach`.
  - `production loss draft shows qty read-only`.
  - `close button only for Approved and requires destruction photo`.
- [ ] **Step 2: Run** `npx vitest run src/components/pos/RejectRequestDialog.test.js` → FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** test + `npx vitest run src/components/pos/POSMenuDialog.test.js` + `npm run build` → PASS.
- [ ] **Step 5: Commit** `feat(pos): dialog pengajuan reject + bukti pemusnahan`

---

### Task 7: Verifikasi akhir

- [ ] Full BE serial: `_pn_run_tests.py pos_next.api.test_reject_request pos_next.api.test_production pos_next.api.test_docperm_fixture pos_next.tests.test_uninstall_coverage` → PASS (dua test terakhir mungkin perlu didaftarkan doctype/role baru — perbaiki bila gagal).
- [ ] Full FE: `cd POS && npx vitest run` → PASS.
- [ ] Alur manual end-to-end di site dev: kasir ajukan Unsold → approver (user lain) Write Off → kasir unggah foto pemusnahan → Close; cek Stock Ledger outlet/reject.
