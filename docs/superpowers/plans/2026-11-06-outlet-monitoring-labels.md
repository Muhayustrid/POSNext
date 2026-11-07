# Outlet Monitoring Labels, Scope, Manual & Jendela Waktu — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. Eksekusi yang dipilih user: **dynamic Workflow** (lihat "Peta eksekusi Workflow").

**Goal:** Menambahkan ke Outlet Monitoring:
- label kustom (nama + warna) yang menggantikan penilaian Pass/Fail/N/A; kasir mengusulkan label, HQ memutuskan;
- scope All/Selected pada Template;
- cadence Manual (unboxing) dengan referensi DN/PR;
- jendela waktu kapan report mulai bisa diisi.

**Architecture:**
- **Label:** daftar label di-snapshot ke `label_options` (JSON) per baris checkpoint saat report dibuat oleh server (`build_report`). Semua tampilan dan validasi membaca snapshot ini, bukan master.
- **Guard pembuatan dan perubahan report:** hanya server yang boleh membuat report (flag `internal_report_build`). Perubahan label oleh kasir hanya lewat endpoint yang memakai flag sempit (`internal_cashier_label_change`).
- **Cadence Manual:** logikanya ada di service baru `services/outlet_monitoring_manual.py`, termasuk normalisasi kiriman DN/PR. Endpoint-nya ditambahkan ke `api/outlet_monitoring.py`.

**Tech Stack:** Frappe/ERPNext v16 (image 16.52.0/16.51.1), Python 3.14, Vue 3.5 + Pinia + Vitest, Desk JS, node:test.

**Spec:** `docs/superpowers/specs/2026-11-06-outlet-monitoring-labels-design.md` (rev 5, disetujui Opus). Pelaksana **wajib** membaca spec. Plan ini tidak mengulang aturan spec; nomor § merujuk ke spec. Spec induk: `docs/superpowers/specs/2026-11-05-outlet-monitoring-design.md`.

## Global Constraints

- **Basis:** branch `feat/outlet-monitoring` @ 779364ab (sama dengan origin). Kerjakan di working tree ini, tanpa worktree baru.
- **Konvensi kode:** module DocType `Pos Next`, indentasi tab.
- **Dependensi:** tidak menambah atau meng-upgrade dependensi apa pun.
- **Teks UI:**
  - Python memakai `_()`, JS/Vue memakai `__()`. Teks sumber dalam bahasa Inggris.
  - Terjemahan Indonesia hanya ditulis di `pos_next/translations/id.csv`, dan hanya di Task 9.
  - Byte/CRLF id.csv harus dipertahankan; cek dengan `python3 scripts/validate_id_csv.py`.
- **Ikon:** hanya FeatherIcon/SVG, tanpa emoji. UI mengikuti `docs/UI_GUIDELINES.md`.
- **Data dari klien:** server tidak pernah mempercayai `company`, `label_options`, `obligation_key`, `opens_at`, atau referensi dari klien. Semuanya diturunkan dari DB.
- **Patch:** patch baru masuk `pos_next/patches/v2_36_0/` (aturan `test_release_hygiene`), di bagian `[post_model_sync]` `patches.txt`, setelah `pos_next.patches.v2_36_0.outlet_monitoring_workspace`.
- **Test backend:** dijalankan **serial** (paralel menyebabkan deadlock 1213) di fix stack:
  `docker exec -e PN_SITE=fix.localhost -e FRAPPE_STREAM_LOGGING=1 -w /home/frappe/frappe-bench erpnext-fix-backend-1 ./env/bin/python apps/pos_next/pos_next/_pn_run_tests.py <module>`
  (selanjutnya disingkat `RUN <module>`).
- **Migrate fix site:** `docker exec -w /home/frappe/frappe-bench erpnext-fix-backend-1 bench --site fix.localhost migrate`.
- **Test frontend:**
  - Vitest dijalankan **di host**: `cd POS && npx vitest run <file>` (container tidak punya binary rollup linux).
  - Helper Desk: `node pos_next/tests/js/index.js`.
- **Fix stack:** bind-mount checkout ini. **Jangan** `docker cp` ke `erpnext-fix-backend-1`.
- **Data test:**
  - Setiap test harus memulihkan Settings yang diubahnya ke nilai sebelumnya, bukan ke default.
  - Helper Settings yang ada (`get_singles_dict` → hapus `tabSingles` → kembalikan) **tidak** mencakup child table. Task 1 memperluas setiap helper itu: snapshot + restore baris `default_labels` (beserta urutan `idx`) dari `tabPOS Monitoring Label Item` `parenttype='POS Monitoring Settings'`, lalu `frappe.clear_document_cache("POS Monitoring Settings")`. Helper ada di `test_outlet_monitoring_setup.py:12-24` dan pola yang sama di suite lain.
  - Label test dibersihkan dengan SQL DB-only (`DELETE FROM tabPOS Monitoring Label` / `Label Item` untuk nama fixture), setelah referensinya dihapus. `on_trash` produksi tidak dilemahkan.
  - Fix site harus berakhir `enabled=0`, tanpa Report/Label/File test.
- **Gerbang test bertahap:** Task 2–4 hanya mewajibkan PASS untuk test **baru/yang diubah oleh task itu**. Jalankan dengan `RUN <module>.<Class>.<test_name>` (bila runner mendukungnya); bila tidak, catat test lama yang masih merah karena bergantung pada task berikutnya. Kelima modul lama wajib PASS penuh pertama kali di Task 5 Step 4. Pemilik pembaruan test lama:
  - `test_outlet_monitoring_setup`: Task 1.
  - `test_outlet_monitoring_service` dan `test_outlet_monitoring_access`: Task 2 (fixture/import `resolve_labels`, insert report langsung → lewat `build_report` atau set `flags.internal_report_build`).
  - `api/test_outlet_monitoring`: Task 3.
  - `api/test_outlet_monitoring_review`: Task 5.
  - Agen tidak mengedit kode milik task berikutnya hanya agar gerbang hijau.
- **Commit/push:** tidak ada commit/push otomatis. Langkah "Checkpoint" hanya `git status --short` + `git diff --stat`.
- **Dev/prod:** tidak menyentuh prod. Dev hanya disentuh di Task 10, setelah backup.
- **Baseline merah yang sudah ada:** `test_release_hygiene` 2F, `test_reject_request` 1F, Biome 306 error. Kegagalan ini tidak boleh diatribusikan ke fitur ini.

## Review Focus

1. **Kasir menekan chip label dua kali atau jaringan mengulang request.** `set_cashier_label` dengan label yang sama harus sukses tanpa error dan tanpa perubahan ganda. → `test_set_cashier_label_same_value_idempotent` (Task 3).
2. **Template diubah dari All ke Selected di tengah hari.** Report yang sudah dibuat tidak berubah, dan `ensure_obligations` berikutnya memakai scope baru. → `test_scope_change_keeps_existing_reports` (Task 2).
3. **Kasir mengisi tepat pada `opens_at`.** Batasnya inklusif: `now == opens_at` sudah boleh. Waktu dibandingkan dengan `now_datetime()` site. → `test_open_window_boundary_inclusive` (Task 3).
4. **Revisi report Manual setelah dokumen referensinya lebih dari 7 hari.** `create_revision` tidak mengecek ulang umur atau keunikan referensi. Revisi tetap memakai `obligation_key` yang sama dengan revision+1. → `test_manual_revision_ignores_reference_age` (Task 4).
5. **Teks legacy berbeda huruf besar/kecil atau spasi** (`"Sesuai SOP "` dan `"sesuai sop"`). Patch hanya membuat satu Label, dan semua baris menunjuk ke Label itu. → `test_patch_case_insensitive_dedupe` (Task 6).

---

## Peta file

| File | Tanggung jawab | Task |
|---|---|---|
| `pos_next/pos_next/doctype/pos_monitoring_label/` (baru) | master label, guard hapus/disable | 1 |
| `pos_next/pos_next/doctype/pos_monitoring_label_item/` (baru, istable) | baris label | 1 |
| `.../pos_monitoring_category/`, `.../pos_monitoring_checkpoint/`, `.../pos_monitoring_settings/` | field `labels`/`default_labels`, validasi duplikat | 1 |
| `.../pos_monitoring_template/` (+ `pos_monitoring_template.js` baru) | scope, cadence Manual, jendela, validasi | 1 |
| `.../pos_monitoring_report/pos_monitoring_report.json`, `.../pos_monitoring_report_checkpoint/*.json` | field baru/hapus | 1 |
| `pos_next/outlet_monitoring_setup.py` | label default §6.1 | 1 |
| `pos_next/pos_next/sidebar/posnext/posnext.json`, `pos_next/workspace_sidebar/posnext.json`, `pos_next/pos_next/workspace/posnext/posnext.json` | link "Monitoring Label" | 1 |
| `.../pos_monitoring_report/pos_monitoring_report.py` | guard §3.5, snapshot, helper review | 2, 5 |
| `pos_next/services/outlet_monitoring.py` | resolusi label, `build_report`, scope, `opens_at`, `mark_missed` | 2, 4 |
| `pos_next/services/outlet_monitoring_access.py` | cabang write Manual | 2 |
| `pos_next/api/outlet_monitoring.py` | endpoint kasir + Manual | 3, 4 |
| `pos_next/services/outlet_monitoring_manual.py` (baru) | aturan scope/referensi Manual, normalisasi kiriman | 4 |
| `pos_next/api/outlet_monitoring_review.py` | finalize, `label_options_query`, gallery | 5 |
| `pos_next/patches/v2_36_0/outlet_monitoring_labels.py` (baru), `pos_next/patches.txt` | migrasi §6.2 | 6 |
| `POS/src/components/pos/OutletMonitoringDialog.vue`, `POS/src/stores/outletMonitoring.js`, `POS/src/utils/statusTone.js`, `POS/src/components/ShiftClosingDialog.vue` | SPA kasir | 7 |
| `.../pos_monitoring_report/pos_monitoring_report.js`, `pos_next/pos_next/page/outlet_monitoring_gallery/*`, `pos_next/public/js/outlet_monitoring_utils.js` | Desk | 8 |
| `pos_next/translations/id.csv`, `docs/OUTLET_MONITORING.md` | terjemahan + dok operator | 9 |
| test: `pos_next/tests/test_outlet_monitoring_setup.py`, `test_outlet_monitoring_service.py`, `test_outlet_monitoring_access.py`, `pos_next/api/test_outlet_monitoring.py`, `pos_next/api/test_outlet_monitoring_review.py`, baru `pos_next/tests/test_outlet_monitoring_manual.py`, `pos_next/tests/test_outlet_monitoring_labels_patch.py` | | per task |

---

### Task 0: Gerbang persiapan (sesi utama)

- [ ] **Step 1:** Minta izin user lalu buat ulang fix stack (volume masih ada). Dari `/Users/rotiropi/ERPNext-Project`:
  `docker compose --env-file docker/.env.fix -f docker/compose.fix.yaml -f docker/compose.fix.mount.yaml up -d --no-build`.
  Verifikasi: `docker exec erpnext-fix-backend-1 ls apps/pos_next/pos_next/hooks.py`, dan bootstrap exit 0.
- [ ] **Step 2: Baseline.** Jalankan lima modul test outlet monitoring yang ada, ditambah `pos_next.tests.test_release_hygiene` dan `pos_next.api.test_reject_request`. Jalankan juga `cd POS && npx vitest run`, `node pos_next/tests/js/index.js`, dan `cd POS && npx biome lint src 2>&1 | tail -3`. Catat hasilnya di `.superpowers/sdd/2026-11-06-outlet-monitoring-labels/baseline.md`.
- [ ] **Step 3:** Verifikasi fix site: `enabled=0`, Report = 0.

---

### Task 1: Skema, master, setup, sidebar

**Files:**
- Create: `pos_next/pos_next/doctype/pos_monitoring_label/{__init__.py,pos_monitoring_label.json,pos_monitoring_label.py}`, `pos_next/pos_next/doctype/pos_monitoring_label_item/{__init__.py,pos_monitoring_label_item.json,pos_monitoring_label_item.py}`, `pos_next/pos_next/doctype/pos_monitoring_template/pos_monitoring_template.js`
- Modify: JSON + `.py` Category, Checkpoint, Settings, Template; JSON Report dan Report Checkpoint; `pos_next/outlet_monitoring_setup.py`; tiga file sidebar/workspace di Peta file
- Test: `pos_next/tests/test_outlet_monitoring_setup.py`

**Interfaces:**
- Produces:
  - DocType `POS Monitoring Label` (`label_name`, `color`, `disabled`; §3.1) dan `POS Monitoring Label Item` (`label`; §3.2).
  - Field `Category.labels`, `Category.cashier_can_label`, `Checkpoint.labels`, `Settings.default_labels`.
  - Field Template: `company_scope`/`profile_scope`/`shift_type_scope` (Select `All\nSelected`, tanpa default/reqd di JSON), `submit_within_hours`, `open_after_minutes`, `open_time`; `cadence` mendapat opsi `Manual`.
  - Field Report: `opens_at`, `reference_doctype`, `reference_name`; `cadence` mendapat `Manual`; `review_result` dihapus.
  - Field Report Checkpoint: `label_options`, `cashier_can_label`, `cashier_label`, `label` (permlevel 1, allow_on_submit); `pass_label`/`fail_label`/`na_label`/`assessment` dihapus.
  - Field Settings yang dihapus: `default_pass_label`, `default_fail_label`, `default_na_label`, `allow_na`, `require_note_on_fail`, `require_note_on_na`.
  - `outlet_monitoring_setup.DEFAULT_LABELS = (("Sesuai SOP", "Green"), ("Tidak Sesuai SOP", "Red"), ("Tidak Berlaku", "Gray"))`.
  - `outlet_monitoring_setup.ensure_default_labels() -> list[str]`: membuat label yang belum ada (cek case-insensitive). Bila `default_labels` kosong, tabel diisi. Mengembalikan nama label.
  - `POSMonitoringTemplate.before_validate()`: mengisi scope yang kosong (§3.3).

- [ ] **Step 1: Tulis test yang gagal** di `test_outlet_monitoring_setup.py`:

```python
def test_label_delete_always_rejected(self):  # §3.1: frappe.delete_doc -> ValidationError, label still exists
def test_label_rename_disabled(self):  # meta allow_rename == 0
def test_disable_last_active_default_label_rejected_when_enabled(self):
def test_settings_enabled_requires_active_default_label(self):
def test_duplicate_label_in_table_rejected(self):  # Category, Checkpoint, Settings
def test_settings_restore_helper_restores_custom_default_labels(self):
	# non-standard default_labels order/content -> after helper restore, rows and idx equal
def test_ensure_default_labels_idempotent_and_case_insensitive(self):
	# existing Label "sesuai sop" -> no second label created; Settings.default_labels filled once
def test_template_scope_defaults(self):
	# empty scopes: company -> Selected; profile/shift -> Selected if table filled else All
def test_template_selected_scope_requires_rows(self):  # profile/shift only for Per Shift + Manual
def test_template_per_hari_ignores_profile_shift_scope(self):
def test_template_manual_requires_submit_within_hours_gt_0(self):
def test_template_open_window_must_be_before_due(self):
	# Per Shift open_after_minutes >= due_minutes_after_open -> reject; Per Hari open_time >= due_time -> reject
```

  Ganti assertion lama `s.default_pass_label == "Sesuai SOP"` dengan `[r.label for r in s.default_labels] == ["Sesuai SOP", "Tidak Sesuai SOP", "Tidak Berlaku"]` (setelah `ensure_default_labels()`).
- [ ] **Step 2:** `RUN pos_next.tests.test_outlet_monitoring_setup` → FAIL (DocType tidak ada / assertion).
- [ ] **Step 3:** Implementasikan JSON + controller sesuai §3.1–3.4.
  - Label: `on_trash` selalu throw. `validate` menolak disable label aktif terakhir di `default_labels` bila Settings `enabled=1`.
  - DocPerm Label: System Manager + `REVIEWER_ROLE` create/read/write tanpa delete; `All` read.
  - `pos_monitoring_template.js`: `onload` mengisi scope kosong seperti `before_validate`; `depends_on` untuk tabel/field per cadence.
  - Setup memanggil `ensure_default_labels()`, menggantikan pengecekan `default_pass_label`.
  - Sidebar: tambah link "Monitoring Label" di section Outlet Monitoring. Mirror workspace mengikuti pola commit c8e0790a.
  - Jalankan migrate fix site.
- [ ] **Step 4:** `RUN pos_next.tests.test_outlet_monitoring_setup` → PASS. Modul lain boleh merah sampai Task 2–5.
- [ ] **Step 5: Checkpoint.**

---

### Task 2: Snapshot label, guard Report, scope, jendela, akses Manual

**Files:**
- Modify: `pos_next/services/outlet_monitoring.py` (`resolve_labels`, `build_report`, `ensure_obligations`, `client_config` bila perlu), `pos_next/pos_next/doctype/pos_monitoring_report/pos_monitoring_report.py`, `pos_next/services/outlet_monitoring_access.py`
- Test: `pos_next/tests/test_outlet_monitoring_service.py`, `pos_next/tests/test_outlet_monitoring_access.py`

**Interfaces:**
- Consumes: skema Task 1.
- Produces (di `services/outlet_monitoring.py`):
  - `resolve_label_options(checkpoint_doc) -> list[dict]`: `[{"label": str, "color": str}]` dengan urutan Checkpoint → Category → Settings; label disabled dilewati (§4.1). **Menggantikan** `resolve_labels`.
  - `label_color(label_options: str | list, label: str | None) -> str | None`: warna dari snapshot.
  - `build_report(template_doc, cadence, **ctx) -> Document`. Kunci ctx baru: `opens_at`, `reference_doctype`, `reference_name`.
    - Mengisi `label_options` (JSON), `cashier_can_label` = `Category.cashier_can_label`, dan `doc.flags.internal_report_build = True`.
    - Melempar `frappe.ValidationError` bila satu baris punya `label_options` kosong.
  - `template_companies(t_doc) -> list[str]`: `All` = semua company dengan POS Profile `disabled=0`.
  - `profile_in_scope(t_doc, pos_profile) -> bool`, `shift_in_scope(t_doc, shift_slot: str | None) -> bool`.
  - `compute_opens_at(t_doc, *, period_start=None, operational_date=None) -> datetime | None` (§4.9).
  - `assert_report_open(doc) -> None`: `ValidationError` "Report can be filled from {0}" bila `now_datetime() < opens_at`.
- Produces (controller): `IMMUTABLE_REPORT_FIELDS += ("opens_at", "reference_doctype", "reference_name")`; insert tanpa flag `internal_report_build` → `frappe.PermissionError` (§3.5).

- [ ] **Step 1: Tulis test yang gagal.**

```python
# test_outlet_monitoring_service.py
def test_label_resolution_checkpoint_over_category_over_settings(self):
def test_disabled_labels_skipped_in_snapshot(self):
def test_snapshot_unchanged_after_master_edit_or_disable(self):
def test_build_report_empty_labels_raises_and_other_templates_continue(self):
	# template A resolves [] -> ensure_obligations still creates template B; Error Log row exists
def test_scope_all_and_selected_three_dimensions(self):
def test_scope_all_ignores_table_rows(self):
def test_scope_change_keeps_existing_reports(self):  # Review Focus 2
def test_opens_at_per_shift_and_per_hari(self):
	# period_start + open_after_minutes; operational_date + open_time; empty fields -> None
def test_ensure_obligations_never_creates_manual(self):
# test_outlet_monitoring_access.py
def test_native_insert_by_cashier_rejected(self):
	# frappe.set_user(cashier); new_doc Report (Per Hari / Manual w/o ref / forged obligation_key).insert() -> PermissionError
def test_cashier_cannot_insert_with_label_or_cashier_label(self):
def test_new_immutable_fields_locked(self):  # opens_at, reference_doctype, reference_name
def test_cashier_write_manual_only_own_profile(self):
```

- [ ] **Step 2:** `RUN pos_next.tests.test_outlet_monitoring_service`, lalu `RUN pos_next.tests.test_outlet_monitoring_access` → FAIL.
- [ ] **Step 3: Implementasi.**
  - Controller:
    - Guard insert §3.5.
    - Aturan non-reviewer di `validate` (:83-93) diganti dari `assessment` ke `label`/`cashier_label`. `cashier_label` dikecualikan bila `flags.internal_cashier_label_change`.
    - `_validate_checkpoint_snapshot` tidak menganggap `cashier_label` mutable kecuali dengan flag itu.
    - `review_result` dihapus dari `REVIEW_HEADER_FIELDS`.
  - `ensure_obligations`:
    - Pakai `template_companies`/`profile_in_scope`/`shift_in_scope`.
    - Isi `opens_at` dari `compute_opens_at`.
    - Bungkus `build_report` + insert per target dalam `try/except Exception` → `frappe.log_error(title="Outlet Monitoring obligation")` + rollback savepoint. Duplikat tetap ditangani diam-diam seperti sekarang.
  - Access: tambah cabang `elif cadence == "Manual": return bool(profile and profile in cashier_profiles(user))`.
  - Migrate.
- [ ] **Step 4:** Test baru Task 2 → PASS (lihat "Gerbang test bertahap"). Test lama di kedua modul diperbarui; yang masih merah dicatat beserta task pemiliknya.
- [ ] **Step 5: Checkpoint.**

---

### Task 3: API kasir — label, prefill, jendela, baca, revisi

**Files:**
- Modify: `pos_next/api/outlet_monitoring.py` (`get_my_tasks`, `get_history`, `get_report`, `register_media`, `submit_report`, `create_revision`; tambah `set_cashier_label`)
- Test: `pos_next/api/test_outlet_monitoring.py`

**Interfaces:**
- Consumes: `assert_report_open`, `build_report`, flag Task 2.
- Produces:
  - `set_cashier_label(report: str, checkpoint_row: str, label: str | None = None) -> dict` (POST, urutan §4.3). Mengembalikan `get_report(report)`.
  - Field per checkpoint di `get_report`: `label_options` (list), `cashier_can_label`, `cashier_label`. `label`, `label_color`, dan `assessment_note` hanya ada bila status `Reviewed`.
  - Field tambahan per task di `get_my_tasks`: `opens_at`, `reference_doctype`, `reference_name`.
  - Field tambahan per item di `get_history`: `reference_name`. `status` tetap nilai tersimpan apa adanya, dan `correction_requested` tetap dikirim. `review_result` dihapus. Teks netral "Needs Correction" (§4.6) hanya label tampilan di SPA (Task 7), bukan nilai API.

- [ ] **Step 1: Tulis test yang gagal.**

```python
def test_set_cashier_label_set_and_clear(self):  # reload from DB
def test_set_cashier_label_same_value_idempotent(self):  # Review Focus 1
def test_set_cashier_label_rejections(self):
	# toggle off, label outside label_options, status not Draft, row of another report, other outlet user
def test_native_save_cannot_change_cashier_label_or_label(self):
def test_set_cashier_label_after_submit_or_missed_rejected(self):
def test_set_cashier_label_race_terminal_first(self):
	# for terminal in (submit_report, mark_missed): row starts cashier_label=A.
	# second DB conn (_get_second_db pattern in test_outlet_monitoring_review.py) performs the
	# terminal transition first; then set_cashier_label(B) -> rejected; reload -> cashier_label still A
def test_set_cashier_label_race_setter_first(self):
	# setter holds parent lock and sets B first; then submit -> label == B (prefill);
	# then mark_missed path: setter B first, mark_missed after -> cashier_label B kept, status Missed
def test_submit_prefills_label_from_cashier_label(self):  # DB label == cashier_label after submit
def test_submit_without_cashier_label_allowed(self):
def test_before_opens_at_upload_label_submit_rejected(self):
def test_open_window_boundary_inclusive(self):  # Review Focus 3: now == opens_at allowed
def test_revision_copies_snapshot_and_reference_clears_labels_no_opens_at(self):
def test_get_report_hides_label_until_reviewed(self):
```

  Ganti test lama yang memakai `assessment`/`pass_label`/`review_result` dengan padanan label.
- [ ] **Step 2:** `RUN pos_next.api.test_outlet_monitoring` → FAIL.
- [ ] **Step 3: Implementasi.**
  - `set_cashier_label` sesuai §4.3.
  - Prefill §4.4 dijalankan sebelum `doc.submit()`, dengan `ignore_permissions` tetap ada.
  - `assert_report_open` dipanggil di `register_media`, `set_cashier_label`, dan `submit_report`.
  - Peta field `create_revision` sesuai §4.2. Insert memakai `flags.internal_report_build`.
  - `get_my_tasks` mendapat query keempat untuk Draft Manual per `pos_profile`, dengan dedup per nama (§4.8).
- [ ] **Step 4:** Test baru Task 3 → PASS. Test lama yang memanggil `finalize_review` boleh tetap merah sampai Task 5; catat namanya.
- [ ] **Step 5: Checkpoint.**

---

### Task 4: Cadence Manual

**Files:**
- Create: `pos_next/services/outlet_monitoring_manual.py`
- Modify: `pos_next/api/outlet_monitoring.py` (3 endpoint + `get_shift_tasks`), `pos_next/services/outlet_monitoring.py` (`mark_missed`, `obligation_key`)
- Test: `pos_next/tests/test_outlet_monitoring_manual.py` (baru)

**Interfaces:**
- Consumes: `build_report`, `profile_in_scope`, `shift_in_scope`, `template_companies` (Task 2).
- Produces (service):
  - `manual_template_matches(t_doc, pos_profile: str) -> bool`: aturan scope §4.8, termasuk fail closed bila tidak ada shift terbuka.
  - `shipment_key(doctype: str, name: str, company: str, *, check_age: bool = True) -> tuple[str, str]`:
    - Memvalidasi aturan referensi §4.8.
    - Mengembalikan `(doctype, name)` hasil normalisasi: PR internal → `("Delivery Note", dn)`.
    - Gagal → `frappe.ValidationError`.
  - `manual_obligation_key(template: str, doctype: str, name: str) -> str` = `f"{template}|manual|{doctype}|{name}"`.
  - `reference_candidates(company: str, template: str, txt: str | None) -> list[dict]`:
    - Field per kandidat: `doctype`, `name`, `posting_date`, `sender`, `item_count`.
    - Maksimal 20 per tipe. Kiriman yang sudah dilaporkan tidak ikut.
- Produces (API, POST/GET sesuai §4.8):
  - `get_manual_templates(pos_profile) -> list[dict]` (`name`, `template_name`).
  - `get_reference_candidates(pos_profile, template, txt=None) -> list[dict]`.
  - `start_manual_report(pos_profile, template, reference_doctype, reference_name) -> dict` (= `get_report`). Duplikat → `ValidationError` "This document has already been reported".
- `get_shift_tasks` tetap memfilter `opening_shift`, sehingga Draft Manual yang menyimpan shift itu ikut. Teks peringatan diubah di Task 7.

- [ ] **Step 1: Tulis test yang gagal** (`test_outlet_monitoring_manual.py`).
  - Fixture PR/DN dibuat sintetis tanpa ledger stok. `db_insert()` hanya menyimpan dokumen itu sendiri, bukan child-nya. Resepnya:
    1. `hdr = frappe.get_doc({"doctype": "Delivery Note", "name": ..., "docstatus": 1, ...}); hdr.db_insert()`.
    2. Untuk setiap item: `frappe.get_doc({"doctype": "Delivery Note Item", "name": ..., "parent": hdr.name, "parenttype": "Delivery Note", "parentfield": "items", "idx": n, "docstatus": 1, ...}).db_insert()`.
    3. Simpan nama DN Item untuk mengisi `delivery_note_item` di PR Item. Pola yang sama dipakai untuk Purchase Receipt / Purchase Receipt Item.
  - `tearDown` menghapus header + item lewat SQL `DELETE`. Jangan memanggil cancel/`delete_doc` pada transaksi sintetis.

```python
def test_start_manual_with_supplier_pr_and_internal_dn(self):
def test_reference_rejections(self):
	# PR other company, draft/cancelled, is_return, DN external customer, future date, older than 7 days
def test_internal_pr_keys_to_its_dn_no_double_report(self):  # DN reported -> PR of DN rejected, and reverse
def test_internal_pr_without_reference_or_mixed_items_rejected(self):
def test_new_pr_from_old_dn_still_candidate(self):  # age only on chosen doc
def test_partial_prs_of_one_dn_share_key(self):
def test_same_doc_other_manual_template_allowed(self):
def test_concurrent_double_start_one_report(self):  # second DB conn; exactly 1 report, friendly error
def test_scope_shift_selected_without_open_shift_fails_closed(self):
def test_template_non_manual_disabled_or_out_of_scope_rejected(self):
def test_candidates_no_money_fields_exclude_used_and_auth(self):
	# keys subset of {doctype,name,posting_date,sender,item_count}; Guest / other profile / out-of-scope template rejected
def test_manual_draft_in_my_tasks_once_and_in_shift_tasks(self):
def test_manual_missed_after_due_plus_grace(self):
def test_manual_revision_ignores_reference_age(self):  # Review Focus 4
```

- [ ] **Step 2:** `RUN pos_next.tests.test_outlet_monitoring_manual` → FAIL.
- [ ] **Step 3: Implementasi.**
  - Service dan endpoint sesuai §4.8.
  - `mark_missed` cabang Manual: Missed bila `now > due_at + shift_missed_grace_hours`.
  - Kandidat memakai `frappe.get_all` dengan allowlist field. `item_count` dihitung dengan satu query `count` per tipe (group by parent), bukan N+1.
- [ ] **Step 4:** Test baru Task 4 → PASS. Jalankan ulang test baru Task 2–3 → tetap PASS.
- [ ] **Step 5: Checkpoint.**

---

### Task 5: Reviewer, query label, gallery backend

**Files:**
- Modify: `pos_next/pos_next/doctype/pos_monitoring_report/pos_monitoring_report.py` (`before_update_after_submit`), `pos_next/api/outlet_monitoring_review.py`
- Test: `pos_next/api/test_outlet_monitoring_review.py`

**Interfaces:**
- Produces:
  - `pos_monitoring_report.assert_review_complete(doc) -> None`: setiap baris punya `label` yang ada di `label_options`-nya. Dipakai oleh native save dan `finalize_review` (§4.5).
  - `label_options_query(doctype, txt, searchfield, start, page_len, filters) -> list[tuple]` dengan decorator `@frappe.whitelist()` + `@frappe.validate_and_sanitize_search_inputs`:
    - `filters` = `{"report", "checkpoint_row"}`. Baris `new-…`/tidak ada → `[]`.
    - Mengembalikan `[(label, color)]` dari snapshot di DB.
  - `get_gallery`:
    - Filter `label` menggantikan `assessment`.
    - Setiap item mendapat `label` dan `color` (dari `label_color`; tanpa label → `None`).

- [ ] **Step 1: Tulis test yang gagal.**

```python
def test_reviewer_partial_label_save_on_submitted(self):
def test_label_outside_options_rejected(self):
def test_reviewed_transition_requires_all_labels_native_and_finalize(self):
def test_reviewed_and_waived_lock_label(self):
def test_disabled_label_in_snapshot_still_selectable(self):  # via label_options_query and save
def test_label_options_query_auth(self):
	# cashier rejected; reviewer outside company scope rejected; row of another report rejected; "new-row-1" -> []
def test_finalize_notification_subject_without_result(self):
def test_gallery_label_filter_and_color(self):
```

  Hapus/ganti test lama untuk `allow_na`, `require_note_*`, dan `review_result`.
- [ ] **Step 2:** `RUN pos_next.api.test_outlet_monitoring_review` → FAIL.
- [ ] **Step 3: Implementasi.** Setelah selesai, kelima modul lama + `test_outlet_monitoring_manual` wajib PASS penuh di Step 4.
  - Ganti logika ganda `outlet_monitoring_review.py:91-104` dan `pos_monitoring_report.py:274-287` dengan `assert_review_complete`.
  - Lock `Reviewed`/`Waived` diperluas ke `label`.
  - Subjek notifikasi: `_("POS Monitoring Report reviewed: {0}")`.
- [ ] **Step 4:** Jalankan serial keenam modul (setup, service, access, manual, api.test_outlet_monitoring, api.test_outlet_monitoring_review) → semua PASS penuh.
- [ ] **Step 5: Checkpoint.**

---

### Task 6: Patch migrasi

**Files:**
- Create: `pos_next/patches/v2_36_0/outlet_monitoring_labels.py`
- Modify: `pos_next/patches.txt` (setelah `outlet_monitoring_workspace`)
- Test: `pos_next/tests/test_outlet_monitoring_labels_patch.py` (baru)

**Interfaces:**
- Produces: `execute() -> None`, idempotent (§6.2). Kolom lama dibaca via SQL setelah `frappe.db.has_column`. Settings dibaca dari `tabSingles`.

- [ ] **Step 1: Tulis test yang gagal.** Fixture menulis nilai legacy langsung lewat SQL ke kolom lama yang masih ada di DB.

```python
def test_patch_builds_labels_and_snapshots_all_statuses(self):
	# Category + Settings(tabSingles) + 4 reports (Draft/Submitted/Reviewed/Missed) with snapshot text != master
def test_patch_assessment_maps_to_label_by_slot(self):
def test_patch_dedupes_same_text_two_slots(self):
def test_patch_case_insensitive_dedupe(self):  # Review Focus 5
def test_patch_template_scope_from_null_columns(self):  # profiles filled -> Selected, empty -> All, companies -> Selected
def test_patch_rerun_does_not_overwrite(self):  # after reviewer sets label and user changes scope
def test_patch_fresh_site_only_fills_settings(self):
```

- [ ] **Step 2:** `RUN pos_next.tests.test_outlet_monitoring_labels_patch` → FAIL.
- [ ] **Step 3: Implementasi** sesuai §6.2, langkah 1–6.
  - Report Checkpoint: bulk `UPDATE ... WHERE ifnull(label_options,'')=''`, dikelompokkan per kombinasi teks.
  - Insert Label didahului cek keberadaan dengan `lower(trim())`.
- [ ] **Step 4:** → PASS. Lalu migrate fix site dua kali; keduanya exit 0.
- [ ] **Step 5: Checkpoint.**

---

### Task 7: SPA kasir

**Files:**
- Modify: `POS/src/components/pos/OutletMonitoringDialog.vue`, `POS/src/stores/outletMonitoring.js`, `POS/src/utils/statusTone.js`, `POS/src/components/ShiftClosingDialog.vue`
- Test: `POS/src/components/pos/OutletMonitoringDialog.test.js`, `POS/src/stores/outletMonitoring.test.js`, `POS/src/components/ShiftClosingDialog.test.js`

**Interfaces:**
- Consumes: kontrak API Task 3 dan 4 (nama endpoint `pos_next.api.outlet_monitoring.<fn>`, field respons di Interfaces Task 3–4). Test memakai mock.
- Produces:
  - `STATUS_TONES.yellow`, `STATUS_TONES.purple`.
  - Map warna palet → tone: `Green→green`, `Red→red`, `Yellow→yellow`, `Orange→orange`, `Blue→blue`, `Gray→gray`, `Purple→purple`.

- [ ] **Step 1: Tulis test yang gagal.**

```js
it("shows label chips only when cashier_can_label and Draft")
it("tap chip calls set_cashier_label, tap again clears (label null)")
it("reviewed report shows label badge with snapshot color")
it("history uses neutral status, no Non-Compliant")
it("Reviewed + correction_requested shows 'Needs Correction' text and keeps revision button")
it("Mulai Laporan: template -> candidate search -> opens Draft report")
it("task before opens_at shows 'Starts {time}' and disables fill actions")
it("shift closing warning copy is neutral (not Per Shift)")  // ShiftClosingDialog.test.js
```

- [ ] **Step 2:** `cd POS && npx vitest run src/components/pos/OutletMonitoringDialog.test.js src/stores/outletMonitoring.test.js src/components/ShiftClosingDialog.test.js` → FAIL.
- [ ] **Step 3: Implementasi** di dialog yang ada (tanpa redesign, §1).
  - Chip memakai `StatusBadge`/`STATUS_PILL_BASE`, target sentuh 44 px.
  - Ganti pemakaian `pass_label`/`assessment`.
- [ ] **Step 4:** → PASS. Lalu `npx vitest run` penuh, `npm run build`, dan Biome. Jumlah error tidak boleh melebihi baseline.
- [ ] **Step 5: Checkpoint.**

---

### Task 8: Desk — form Report, gallery, helper

**Files:**
- Modify: `pos_next/pos_next/doctype/pos_monitoring_report/pos_monitoring_report.js`, `pos_next/pos_next/page/outlet_monitoring_gallery/{outlet_monitoring_gallery.js,outlet_monitoring_gallery.css}`, `pos_next/public/js/outlet_monitoring_utils.js`
- Test: `pos_next/tests/js/outlet_monitoring_utils.test.cjs`

**Interfaces:**
- Consumes: `label_options_query`, field gallery `label`/`color` (Task 5).
- Produces: `outlet_monitoring_utils.labelBadgeClass(color: string | null) -> string`; `null` → badge abu "Not assessed".

- [ ] **Step 1: Tulis test yang gagal** (node): `labelBadgeClass` untuk 7 warna + `null`. Hapus test lama yang berbasis assessment.
- [ ] **Step 2:** `node pos_next/tests/js/index.js` → FAIL.
- [ ] **Step 3: Implementasi.**
  - `frm.set_query("label", "checkpoints", (doc, cdt, cdn) => ({query: "pos_next.api.outlet_monitoring_review.label_options_query", filters: {report: doc.name, checkpoint_row: cdn}}))`.
  - Form menampilkan `cashier_label`, `label`, referensi (link), dan `opens_at`.
  - Gallery: filter Label diisi dari `frappe.db.get_list("POS Monitoring Label", {fields: ["name"], limit: 0})`, badge memakai `labelBadgeClass`, CSS `badge-blue`/`badge-yellow`/`badge-purple`.
  - List view Report: filter cadence Manual (standard filter).
- [ ] **Step 4:** → PASS.
- [ ] **Step 5: Checkpoint.**

---

### Task 9: Terjemahan dan dok operator

**Files:** `pos_next/translations/id.csv`, `docs/OUTLET_MONITORING.md`

- [ ] **Step 1:** Kumpulkan string baru dari diff (`_()`/`__()`, label JSON, deskripsi). Tambahkan ke id.csv dengan line ending CRLF yang sudah ada.
- [ ] **Step 2:** `python3 scripts/validate_id_csv.py` → OK. `git diff --check` dengan `core.whitespace=cr-at-eol` → bersih.
- [ ] **Step 3:** Perbarui `docs/OUTLET_MONITORING.md`: master Label, scope, Manual/unboxing (DN atau PR, satu kiriman sekali), dan jendela waktu.
- [ ] **Step 4: Checkpoint.**

---

### Task 10: Verifikasi penuh + dev (sesi utama)

- [ ] **Step 1 (fase C, sebelum review D):** Jalankan serial semua modul test outlet monitoring (7 modul), ditambah `test_uninstall_coverage`, `test_release_hygiene`, `test_auto_close_shifts`, `test_closing_shift_race`, `api.test_reject_request`. Lalu vitest penuh, build, node, ruff, dan Biome. Bandingkan dengan baseline Task 0.
- [ ] **Step 2 (= fase D):** Review whole-branch oleh agen #9 (opus-xhigh); tidak ada reviewer kedua. Temuan penting diperbaiki oleh sesi utama atau satu agen sonnet-max lanjutan, dengan persetujuan user bila melebihi 9 agen.
- [ ] **Step 3:** Ajukan pesan commit yang persis ke user. Commit hanya setelah disetujui; push hanya bila diminta terpisah.
- [ ] **Step 4:** Setelah commit disetujui, deploy dev:
  - `docker/erp backup dev` → `docker/erp rebuild dev` → migrate → clear-cache.
  - Verifikasi di dev: label default ada, Category lama punya `labels`, scope template terisi, aset SPA memuat chip label.
  - Beri tahu user untuk cek di http://dev.localhost:8000. Prod tidak disentuh.
- [ ] **Step 5:** Tulis bukti di `docs/proofs/2026-11-06-outlet-monitoring-labels.md`.

---

## Peta eksekusi Workflow (dynamic, 9 agen)

Test backend wajib serial. Karena itu Task 1–6 dikerjakan berantai di fix stack. SPA dan Desk berjalan paralel dengan rantai backend karena memakai mock/node dan file yang terpisah. id.csv hanya disentuh di fase C.

| Fase | Agen | Isi | Bergantung |
|---|---|---|---|
| 0 | sesi utama | Task 0 (izin fix stack, baseline) | - |
| A | `sonnet-max` #1 | Task 1 → 2 | 0 |
| A-review | `opus-xhigh` #2 | skema + guard + akses (Review Focus 2, §3.5) | A |
| B1 | `sonnet-max` #3 | Task 3 → 4 | A-review |
| B2 | `sonnet-max` #4 | Task 5 → 6 | B1 (fix stack serial) |
| B-FE | `sonnet-max` #5 | Task 7 (SPA) | A-review |
| B-Desk | `sonnet-max` #6 | Task 8 (Desk) | A-review |
| B-review | `opus-xhigh` #7 | review backend + FE + Desk (Review Focus 1, 3–5) | B2, B-FE, B-Desk |
| C | `sonnet-max` #8 | perbaikan temuan + Task 9 + Task 10 Step 1 | B-review |
| D | `opus-xhigh` #9 | review whole-branch final (= Task 10 Step 2) | C |
| E | sesi utama | Task 10 Step 3–5 (commit setelah persetujuan, deploy dev, bukti) | D |

Routing model (keputusan user):
- Kode ditulis oleh `sonnet-max`; `opus-xhigh` hanya me-review.
- Opus menjadi pengoding hanya bila Sonnet gagal dua kali pada task yang sama, dan atas persetujuan user.

Setiap prompt agen wajib memuat:
- path spec + plan + nomor task;
- daftar file milik task itu (dilarang mengedit file milik task paralel);
- larangan commit/push/prod/`docker cp`/hapus volume;
- aturan RED dulu baru GREEN;
- laporan yang berisi perintah test + output ringkas, ditulis ke `.superpowers/sdd/2026-11-06-outlet-monitoring-labels/<fase>.md`.
