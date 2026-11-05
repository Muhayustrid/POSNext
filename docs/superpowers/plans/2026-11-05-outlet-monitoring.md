# Outlet Monitoring v1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. Eksekusi yang dipilih user: **dynamic Workflow** (lihat bagian "Peta eksekusi Workflow").

**Goal:** Kasir mengisi checklist foto/video dari POS. HQ menilai tiap checkpoint di Desk dan melihat semua bukti di Gallery. Semua nilai operasional bisa diatur lewat POS Monitoring Settings.

**Architecture:** Ada tujuh DocType baru (3 master, 1 Single settings, 1 Report submittable) plus child table-nya.
- Kewajiban dibuat server sebagai Report Draft. Pembuatannya lazy dari `get_my_tasks` dan dari cron `*/5`, idempoten lewat index unik `(obligation_key, revision)`.
- Media diunggah multipart ke `upload_file` sebagai File private yang menempel ke Report. Izin baca file didelegasikan Frappe ke Report, yang dijaga oleh `has_permission` + `permission_query_conditions`.
- Kasir memakai satu view baru di POSMenuDialog. HQ memakai form native + Desk Page Gallery.

**Tech Stack:** Frappe/ERPNext v16 (image 16.52.0), Python 3.14, Vue 3.5 + Pinia + Vitest, Desk JS (frappe.ui), node:test untuk helper Desk.

**Spec:** `docs/superpowers/specs/2026-11-05-outlet-monitoring-design.md` (audit: `docs/superpowers/specs/2026-11-05-outlet-monitoring-handoff-audit.md`). Pelaksana wajib membaca spec; plan ini tidak mengulang isinya.

## Global Constraints

- Basis: `origin/main` 2c25ee49. Branch kerja `feat/outlet-monitoring`.
- Module DocType `Pos Next`, prefix nama `POS Monitoring`. Indentasi tab, mengikuti file di sekitarnya.
- Tanpa dependensi baru, dan tanpa upgrade dependensi (npm maupun pip).
- Teks Python memakai `_()`, teks JS/Vue memakai `__()`. String UI ditulis dalam bahasa Inggris di kode; terjemahan Indonesia hanya di `pos_next/translations/id.csv`, dan file itu hanya diedit di Task 9. Pertahankan byte/CRLF id.csv; validasi dengan `python3 scripts/validate_id_csv.py`.
- Ikon hanya FeatherIcon/SVG, **tanpa emoji**. UI mengikuti `docs/UI_GUIDELINES.md` (statusTone/StatusBadge, useToast, target sentuh 44/40 px).
- Server tidak pernah mempercaya `company`/`warehouse` dari klien; company diturunkan dari POS Profile/Report.
- Foto selalu JPEG hasil re-encode. Video hanya MP4 (brand bukan `qt  `), dan WebM hanya bila `allow_webm`. HEIC dan MOV ditolak. `make_thumbnail` dilarang karena menulis ke public.
- Controller POS Opening Shift / POS Closing Shift **tidak diubah**.
- Tidak ada direktori patch baru, karena `test_release_hygiene` menuntut versi app (2.16.0) >= seri patch terbaru (v2_36_0). Patch baru masuk ke `pos_next/patches/v2_36_0/`.
- Tes backend dijalankan **serial** (paralel = deadlock 1213) di fix stack:
  `docker exec -e PN_SITE=fix.localhost -e FRAPPE_STREAM_LOGGING=1 -w /home/frappe/frappe-bench erpnext-fix-backend-1 ./env/bin/python apps/pos_next/pos_next/_pn_run_tests.py <module>`
- Frontend: `cd POS && npx vitest run <file>`. Desk helper: `node pos_next/tests/js/<file>.test.cjs` (daftarkan di `pos_next/tests/js/index.js`).
- Fix stack bind-mount checkout ini: **jangan** `docker cp` ke `erpnext-fix-backend-1`.
- **Tidak ada commit/push otomatis.** Setiap langkah "Checkpoint" hanya `git status --short` + `git diff --stat`. Commit hanya setelah user menyetujui pesan commit yang persis.
- Dev/prod berisi data produksi. Tidak menyentuh prod. Dev hanya di Task 10, setelah backup.

## Review Focus

1. **Kasir yang ditugaskan ke dua company**: `has_permission` harus memakai company dari Report, bukan profile aktif. Kasir boleh membaca Report company mana pun tempat ia POS Profile User, dan hanya itu. → tes `test_cashier_two_profiles_scope` (Task 2).
2. **Shift ditutup sebelum kasir submit**: Report Per Shift menjadi Missed pada cron berikutnya. Kasir yang membuka view setelahnya tidak melihatnya lagi di Tugas Saya; Report itu muncul di Riwayat. → `test_closed_shift_draft_becomes_missed` (Task 3), `test_my_tasks_excludes_missed` (Task 4).
3. **Template diubah atau checkpoint di-disable setelah Report dibuat**: Report lama tetap memakai snapshot; submit tetap memakai aturan snapshot, bukan master. → `test_snapshot_survives_template_edit` (Task 3).
4. **Upload sukses tetapi `register_media` gagal (jaringan/validasi)**: File private yatim tidak boleh menumpuk. Server menghapus File saat validasi gagal; klien memanggil `remove_orphan_file` bila register gagal. → `test_register_failure_deletes_file` (Task 4), `deletes orphan upload when register fails` (Task 6).
5. **Media yang sudah dipurge retensi**: Gallery dan form tidak boleh menampilkan gambar rusak. Baris `purged=1` dilewati Gallery, dan form menampilkan teks "Media removed by retention". → `test_gallery_skips_purged` (Task 5), node test `mediaPreviewHtml purged` (Task 8).

---

## Peta file

| File | Tanggung jawab | Task |
|---|---|---|
| `pos_next/pos_next/doctype/pos_monitoring_category/` | master kategori + label | 1 |
| `pos_next/pos_next/doctype/pos_monitoring_checkpoint/` | master checkpoint + validasi count | 1 |
| `pos_next/pos_next/doctype/pos_monitoring_template/` + child `pos_monitoring_template_checkpoint`, `_company`, `_shift_type`, `_pos_profile` | jadwal + target | 1 |
| `pos_next/pos_next/doctype/pos_monitoring_settings/` | Single settings §9 + validate | 1 |
| `pos_next/outlet_monitoring_setup.py` | role reviewer + DocPerm idempoten | 1 |
| `pos_next/pos_next/doctype/pos_monitoring_report/` + child `pos_monitoring_report_checkpoint`, `_media` | Report, index unik, status | 2 |
| `pos_next/services/outlet_monitoring_access.py` | scope user, hooks izin | 2 |
| `pos_next/services/outlet_monitoring.py` | settings, kewajiban, Missed, retensi, evidence | 3 |
| `pos_next/services/outlet_monitoring_media.py` | magic bytes + validasi File | 4 |
| `pos_next/api/outlet_monitoring.py` | endpoint kasir | 4 |
| `pos_next/api/outlet_monitoring_review.py` | endpoint reviewer + notifikasi/realtime | 5 |
| `POS/src/utils/outletMonitoringMedia.js` | kompres foto, cek video, upload | 6 |
| `POS/src/stores/outletMonitoring.js` | state tugas/badge/config + realtime | 6 |
| `POS/src/components/pos/OutletMonitoringDialog.vue` | view kasir | 6 |
| `POS/src/components/pos/managementMenu.js`, `POSMenuDialog.vue`, `POSHeader.vue`, `POS/src/pages/POSSale.vue` | menu, badge, prop | 6 |
| `POS/src/components/ShiftClosingDialog.vue` (cek path) | peringatan tutup shift | 7 |
| `pos_next/pos_next/doctype/pos_monitoring_report/pos_monitoring_report.js` | tombol review + preview media | 8 |
| `pos_next/public/js/outlet_monitoring_utils.js` | helper murni Desk (periode, kartu, preview) | 8 |
| `pos_next/pos_next/page/outlet_monitoring_gallery/` | Desk Page Gallery | 8 |
| `pos_next/pos_next/workspace/posnext/posnext.json`, `pos_next/patches/v2_36_0/outlet_monitoring_workspace.py` | kartu workspace | 8 |
| `pos_next/translations/id.csv`, `docs/OUTLET_MONITORING.md` | terjemahan + dok operator | 9 |
| `docs/proofs/2026-11-05-outlet-monitoring.md` | bukti dev | 10 |

Penyimpangan kecil dari spec §6, demi isolasi file agar agen paralel tidak bentrok:
- Endpoint reviewer ada di `api/outlet_monitoring_review.py`, bukan di file kasir.
- `get_my_tasks` **tidak melempar** saat fitur off. Ia mengembalikan `enabled: false` agar polling badge tidak menghasilkan error. Endpoint kasir lain tetap menolak.

---

### Task 0: Gerbang persiapan (sesi utama, bukan agen)

- [ ] **Step 1:** Konfirmasi ke user cara membuat branch `feat/outlet-monitoring` dari `origin/main` 2c25ee49. Branch saat ini punya edit backdate yang belum di-commit (sudah ada di main) plus spec/plan yang belum di-commit. Bawa spec + plan ke branch baru.
- [ ] **Step 2:** Dengan izin user, buat ulang fix stack `erpnext-fix` (volume masih ada). Verifikasi dengan `docker exec erpnext-fix-backend-1 ls apps/pos_next/pos_next/hooks.py`.
- [ ] **Step 3: Baseline.** Jalankan `pos_next.tests.test_release_hygiene pos_next.tests.test_uninstall_coverage pos_next.api.test_reject_request`, `npx vitest run src/components/pos`, dan `node pos_next/tests/js/index.js`, lalu catat yang sudah gagal. Contoh: periksa apakah `test_app_version_not_below_latest_patch_series` sudah merah dari awal (2.16.0 < 2.36.0). Kegagalan baseline tidak boleh diatribusikan ke fitur ini.

---

### Task 1: Master, Settings, role reviewer

**Files:**
- Create: 4 DocType master + 4 child (lihat Peta file), masing-masing `__init__.py`, `<name>.json`, `<name>.py`
- Create: `pos_next/pos_next/doctype/pos_monitoring_settings/` (`issingle: 1`, pola `pos_reject_settings`)
- Create: `pos_next/outlet_monitoring_setup.py`
- Modify: `pos_next/install.py` (`after_migrate`, sekitar :617-649; tambahkan panggilan setelah `ensure_reject_purposes()`)
- Modify: `pos_next/uninstall.py:35-37` dan `pos_next/fixtures/role.json`
- Test: `pos_next/tests/test_outlet_monitoring_setup.py`

**Interfaces:**
- Produces:
  - `REVIEWER_ROLE = "POSNext Monitoring Reviewer"` (di `outlet_monitoring_setup.py`).
  - `ensure_outlet_monitoring_setup() -> None`: membuat role (desk_access 1) bila belum ada, lalu DocPerm idempoten. Dipanggil dari `after_migrate`.
  - DocType dengan field persis seperti spec §4.1-4.3 dan §9.1. Nama child: `POS Monitoring Template Checkpoint` (`checkpoint` Link, `required` Check default 1), `POS Monitoring Template Company` (`company`), `POS Monitoring Template Shift Type` (`shift_type` Link Shift Type), `POS Monitoring Template POS Profile` (`pos_profile`).
  - Field Template: `due_minutes_after_open` (Int, depends_on Per Shift), `due_time` (Time, depends_on Per Hari).

**Izin master/settings (DocPerm di JSON):**

| Role | Master | Settings |
|---|---|---|
| System Manager | penuh | read/write |
| Reviewer | penuh | read/write |
| POSNext Cashier | read | read |

- [ ] **Step 1: Tulis tes yang gagal**

```python
class TestOutletMonitoringSetup(FrappeTestCase):
	def test_reviewer_role_created_idempotently(self):
		ensure_outlet_monitoring_setup(); ensure_outlet_monitoring_setup()
		self.assertEqual(frappe.db.count("Role", {"name": REVIEWER_ROLE}), 1)
	def test_settings_defaults(self):
		s = frappe.get_single("POS Monitoring Settings")
		self.assertEqual((s.enabled, s.max_revisions, s.shift_missed_grace_hours, s.video_max_mb, s.video_max_seconds, s.media_retention_days, s.gallery_page_size), (0, 3, 12, 20, 30, 90, 40))
		self.assertEqual(str(s.day_missed_next_day_time), "2:00:00")
		self.assertEqual(s.default_pass_label, "Sesuai SOP")
	def test_settings_rejects_video_above_site_limit(self):  # max_file_size from frappe.core.api.file.get_max_file_size()
	def test_settings_rejects_thumbnail_not_smaller_than_photo(self):
	def test_settings_rejects_gallery_page_size_out_of_10_200(self):
	def test_checkpoint_rejects_max_below_min(self):
	def test_template_rejects_duplicate_checkpoint_and_empty_checkpoints(self):
	def test_template_per_shift_requires_due_minutes_per_day_requires_due_time(self):
```

  Juga di `pos_next/tests/test_uninstall_coverage.py`: assert `REVIEWER_ROLE in UNINSTALL_FIXTURE_ROLES`.
- [ ] **Step 2:** Jalankan `... _pn_run_tests.py pos_next.tests.test_outlet_monitoring_setup`. Diharapkan FAIL: ImportError/DoesNotExist.
- [ ] **Step 3:** Implementasikan DocType JSON + controller `validate`, `outlet_monitoring_setup.py`, panggilan `after_migrate`, `UNINSTALL_FIXTURE_ROLES += (REVIEWER_ROLE,)`, dan entri role di `fixtures/role.json` + filter `hooks.py:101-104`. Role reviewer **tidak** masuk `UNINSTALL_ROLES`, karena set itu khusus Custom DocPerm. Default Settings diambil dari spec §9.1. Lalu jalankan `bench --site fix.localhost migrate` di fix stack.
- [ ] **Step 4:** Jalankan modul tes yang sama + `pos_next.tests.test_uninstall_coverage`. Diharapkan PASS.
- [ ] **Step 5: Checkpoint** (tanpa commit).

---

### Task 2: Report DocType dan kontrol akses

**Files:**
- Create: `pos_next/pos_next/doctype/pos_monitoring_report/` (json, py), child `pos_monitoring_report_checkpoint/`, `pos_monitoring_report_media/`
- Create: `pos_next/services/outlet_monitoring_access.py`
- Modify: `pos_next/hooks.py:152-158` (tambah `"POS Monitoring Report"` di kedua dict)
- Test: `pos_next/tests/test_outlet_monitoring_access.py`

**Interfaces:**
- Consumes: `REVIEWER_ROLE` (Task 1), `pos_next.hq_scope.get_permitted_companies(user) -> list | None`.
- Produces:
  - Report: field persis spec §4.4, `is_submittable: 1`, `naming_rule` autoname `format:OMR-{YY}{MM}{DD}-{####}`, `track_changes: 1`. Field penilaian `permlevel: 1, allow_on_submit: 1`. `status` read-only, default Draft. Status `Missed` dan `Waived` juga docstatus 1.
  - `POSMonitoringReport.on_doctype_update()` → `frappe.db.add_unique("POS Monitoring Report", ["obligation_key", "revision"], constraint_name="unique_obligation_revision")`.
  - DocPerm:

    | Role | permlevel 0 | permlevel 1 |
    |---|---|---|
    | Cashier | read, write, create | read |
    | Reviewer | read, write, submit | read, write |
    | System Manager | semua | semua |

    Kasir tidak dapat izin submit: submit dilakukan endpoint setelah cek eksplisit.
  - `outlet_monitoring_access.cashier_profiles(user) -> list[str]`: POS Profile yang usernya ada di `POS Profile User`.
  - `outlet_monitoring_access.cashier_companies(user) -> list[str]`: company dari profile tersebut.
  - `is_reviewer(user) -> bool`: Administrator, System Manager, atau REVIEWER_ROLE.
  - `reviewer_companies(user) -> list[str] | None`: None berarti tanpa batas.
  - `get_permission_query_conditions(user) -> str` dan `has_permission(doc, ptype=None, user=None) -> bool`:
    - Reviewer: company dalam scope.
    - Kasir: read bila `doc.company in cashier_companies`.
    - Kasir write hanya bila `doc.docstatus == 0`, status Draft, dan salah satu dari:
      - Per Shift: `doc.pos_profile in cashier_profiles`;
      - Per Hari: company cocok.
    - Selain itu `False`. Query kosong untuk kasir tanpa profile → `"1=0"`.

- [ ] **Step 1: Tulis tes yang gagal** (fixture: buat 2 company/profile dari data site; skip bila < 2 profile beda company, mengikuti pola `RejectRequestTest.setUp`. Buat user tes dengan role Cashier sebagai POS Profile User profile A.)

```python
def test_unique_obligation_revision(self):  # insert same key+revision twice -> frappe.UniqueValidationError
def test_cashier_reads_own_company_only(self):  # has_permission read A True, B False; get_list returns no B
def test_cashier_two_profiles_scope(self):  # user on A and B profiles: reads both, not C
def test_cashier_cannot_write_submitted(self):  # docstatus 1 -> write False
def test_cashier_cannot_set_permlevel1(self):  # as cashier save draft with assessment="Pass" -> value stays Pending
def test_reviewer_scoped_by_user_permission(self):  # User Permission Company X -> read Y False, gallery later
def test_private_file_follows_report(self):  # File attached to B report, is_private: frappe.has_permission("File", doc=f, user=cashierA) False
```

- [ ] **Step 2:** Jalankan `pos_next.tests.test_outlet_monitoring_access`. Diharapkan FAIL.
- [ ] **Step 3:** Implementasikan DocType, hooks, dan access module (pola `overrides/stock_reconciliation.py:369-389`; escape dengan `frappe.db.escape`). Lalu migrate fix.
- [ ] **Step 4:** Jalankan modul tes yang sama. Diharapkan PASS.
- [ ] **Step 5: Checkpoint.**

---

### Task 3: Service kewajiban, Missed, retensi

**Files:**
- Create: `pos_next/services/outlet_monitoring.py`
- Create: `pos_next/tasks/outlet_monitoring.py` (`run_cron()`, `run_daily()`)
- Modify: `pos_next/hooks.py:259-268` (cron `*/5` + daily)
- Test: `pos_next/tests/test_outlet_monitoring_service.py`

**Interfaces:**
- Consumes: Task 1-2 DocType; `outlet_monitoring_access.*`.
- Produces:
  - `get_settings() -> frappe._dict`: dict hasil `frappe.get_cached_doc("POS Monitoring Settings").as_dict()`. Label default di-resolve per kategori oleh `resolve_labels(category) -> tuple[str, str, str]`.
  - `client_config() -> dict` dengan kunci persis: `photo_max_edge, thumbnail_max_edge, photo_quality, photo_max_mb, video_max_mb, video_max_seconds, allow_webm, show_menu_badge, warn_on_shift_close, allow_late_submission`.
  - `obligation_key(template, cadence, *, opening_shift=None, company=None, operational_date=None) -> str`, format spec §4.4.
  - `ensure_obligations(pos_profile=None, now=None) -> list[str]` (nama Report baru). No-op bila `enabled=0`. Insert memakai `ignore_permissions`. `frappe.UniqueValidationError`/`DuplicateEntryError` ditelan karena berarti sudah dibuat proses lain (savepoint, bukan rollback penuh).
    - Per Shift: shift `status="Open"`, company ada di target, `pos_profiles` kosong atau berisi profile itu, `shift_types` kosong atau berisi `shift_slot`. `operational_date = posting_date`, `due_at = period_start_date + due_minutes_after_open`.
    - Per Hari: setiap company target, untuk `getdate(now)`, `due_at = date + due_time`.
    - Template `enabled=0` dilewati.
  - `build_report(template_doc, cadence, **ctx) -> Document`: snapshot checkpoint aktif + label ter-resolve. Checkpoint `disabled=1` di master saat snapshot dilewati. `video_max_seconds` 0 → nilai settings.
  - `recompute_evidence(doc) -> None`: `evidence_complete = count(media of row, purged=0) >= min_count`.
  - `mark_missed(now=None) -> list[str]`: Draft yang jendelanya habis (spec §5.2) diberi `status="Missed"` lalu disubmit dengan `ignore_permissions` dan `flags.ignore_mandatory`.
  - `purge_expired_media(today=None) -> int`: bila `media_retention_days > 0`, baris media pada Report berstatus Reviewed/Missed/Waived dengan `uploaded_at < today - N` dihapus Filenya (`frappe.delete_doc("File", ..., ignore_permissions=True)`), lalu `purged=1`, lewat `db_set` per baris child. Mengembalikan jumlah yang dihapus.
  - `run_cron()` = `ensure_obligations(); mark_missed()`. `run_daily()` = `purge_expired_media()`.

- [ ] **Step 1: Tulis tes yang gagal** (shift tes: insert POS Opening Shift Open langsung dengan `db_insert`/`ignore_permissions` pada profile tes, pola `test_auto_close_shifts.py`; set `enabled=1` di setUp, kembalikan di cleanup)

```python
def test_ensure_twice_creates_one_report_per_key(self):  # len(names2)==0, count==1
def test_per_day_not_duplicated_across_shifts(self):  # open shift 1, ensure, close, open shift 2, ensure -> 1 day report, 2 shift reports
def test_shift_type_filter(self):  # template shift_types=[X]; shift_slot Y -> no report
def test_empty_companies_creates_nothing(self):
def test_disabled_flag_creates_nothing(self):
def test_due_at_per_shift_and_per_day(self):  # period_start 08:00 + 30 -> 08:30; due_time 10:00
def test_snapshot_survives_template_edit(self):  # edit checkpoint min_count & disable it -> report row unchanged
def test_closed_shift_draft_becomes_missed(self):  # set shift status Closed -> mark_missed -> status Missed, docstatus 1
def test_per_day_missed_next_day(self):  # now = date+1 02:01 -> Missed; 01:59 -> Draft
def test_shift_missed_after_grace(self):  # due_at + 12h + 1m -> Missed
def test_purge_keeps_report_deletes_file(self):  # Reviewed, uploaded 91 days ago -> File gone, row purged=1, report exists; Draft untouched
def test_retention_zero_keeps_forever(self):
def test_task_module_imports_cold(self):  # subprocess import pos_next.tasks.outlet_monitoring (pola test_auto_close_shifts)
```

- [ ] **Step 2:** Jalankan modulnya. Diharapkan FAIL.
- [ ] **Step 3:** Implementasikan service + tasks + hooks.
- [ ] **Step 4:** Jalankan modul ini + `pos_next.tests.test_auto_close_shifts` (regresi cron). Diharapkan PASS.
- [ ] **Step 5: Checkpoint.**

---

### Task 4: API kasir + validasi media

**Files:**
- Create: `pos_next/services/outlet_monitoring_media.py`, `pos_next/api/outlet_monitoring.py`
- Test: `pos_next/api/test_outlet_monitoring.py`

**Interfaces:**
- Consumes: Task 3 (`ensure_obligations`, `client_config`, `recompute_evidence`, `get_settings`), `pos_next.api.packages._assert_profile_access`, access module.
- Produces (media):
  - `sniff_media(head: bytes) -> str | None`: `"jpeg"`, `"mp4"`, `"webm"`, atau None. MP4 = `head[4:8]==b"ftyp"` dengan `head[8:12] != b"qt  "`; HEIC (`ftypheic/heix/mif1`) mengembalikan None.
  - `validate_media_file(file_doc, report_name, expected: str, settings) -> str`: mengembalikan "Photo"/"Video", atau throw `frappe.ValidationError`. `expected` berisi "Photo", "Video", atau "Photo or Video". Batas ukuran dari `file_size`.
- Produces (endpoint, semua `@frappe.whitelist(methods=["POST"])` kecuali getter `GET`):
  - `get_my_tasks(pos_profile) -> {"enabled": bool, "config": dict, "count": int, "tasks": [{"name","template","cadence","company","pos_profile","operational_date","due_at","late": bool,"required_total": int,"required_done": int}]}`.
    - Bila off: `{"enabled": False, "config": {}, "count": 0, "tasks": []}`.
    - Tasks = Draft Per Shift dari shift Open profile itu + Draft Per Hari untuk company profile itu. `count = len(tasks)` (kewajiban, bukan checkpoint).
  - `get_history(pos_profile, start=0, page_length=20) -> {"items": [...], "has_more": bool}`: non-Draft untuk company profile, `modified desc`. Item memuat `status, review_result, correction_requested, review_note, revision`.
  - `get_report(name) -> dict`: header + `checkpoints` (tanpa `assessment*` bila status bukan Reviewed) + `media` (`purged=0`). `frappe.has_permission(..., "read", throw=True)`.
  - `register_media(report, checkpoint_row, file_name, thumbnail_file_name=None) -> dict` (= `get_report`). Bila gagal, kedua File dihapus sebelum throw. Thumbnail wajib JPEG.
  - `remove_media(report, media_row) -> dict`: Draft + write.
  - `remove_orphan_file(file_name) -> None`: hanya File milik `session.user`, `attached_to_doctype` Report Draft, dan tidak dirujuk baris media mana pun.
  - `submit_report(name) -> dict`:
    - Bila ada checkpoint `required` yang belum lengkap → throw.
    - Bila lewat `due_at` dan `allow_late_submission=0` → throw.
    - Set `submitted_at=now`, `submitted_by=session.user`, `timeliness`, `status="Submitted"`, lalu submit dengan `ignore_permissions` setelah cek write.
    - Panggil `pos_next.api.outlet_monitoring_review.on_report_submitted(doc)` (impor lazy). Task 4 membuat file itu berisi stub no-op; Task 5 mengisinya. Jangan pakai `try/except ImportError`.
  - `create_revision(name) -> dict`:
    - Syarat: `status=="Reviewed"`, `correction_requested`, `allow_correction`, `revision < 1 + max_revisions`, dan belum ada revisi lebih baru.
    - Report baru: Draft, `revision+1`, `correction_of=name`, salin snapshot checkpoint dengan `assessment=Pending` dan tanpa media.
  - `get_shift_tasks(opening_shift) -> {"items": [{"name","template","required_total","required_done"}]}`: kasir pemilik shift atau profile user.
  - Semua endpoint kecuali `get_my_tasks` melempar `frappe.ValidationError(_("Outlet Monitoring is disabled"))` saat off.

- [ ] **Step 1: Tulis tes yang gagal** (File tes dibuat dengan `frappe.get_doc({"doctype":"File","file_name":"x.jpg","content":bytes,"is_private":1,"attached_to_doctype":...,"attached_to_name":...}).insert(ignore_permissions=True)`; jalankan sebagai kasir dengan `frappe.set_user`)

```python
def test_sniff_media(self):  # jpeg FF D8 FF; mp4 isom; qt -> None; heic -> None; webm 1A45DFA3
def test_my_tasks_counts_obligations_not_checkpoints(self):  # 1 report, 3 checkpoints -> count 1
def test_my_tasks_disabled_returns_flag(self):
def test_my_tasks_excludes_missed(self):
def test_register_rejects_fake_jpeg(self):  # name .jpg, content b"GIF89a..." -> ValidationError, File deleted
def test_register_rejects_heic_and_mov(self):
def test_register_rejects_oversize(self):  # photo_max_mb=0.001
def test_register_rejects_wrong_type_for_checkpoint(self):  # video on Photo checkpoint
def test_register_rejects_over_max_count(self):
def test_register_rejects_file_of_other_doc(self):  # attached to another Report
def test_register_failure_deletes_file(self):
def test_webm_only_when_allowed(self):
def test_submit_requires_required_evidence(self):
def test_submit_after_due_is_late(self):  # and throws when allow_late_submission=0
def test_cashier_other_company_cannot_get_report(self):  # PermissionError
def test_revision_only_when_requested(self):  # not requested -> throw; requested -> new draft rev 2, old unchanged (modified equal)
def test_revision_limit(self):  # max_revisions=1 -> rev 3 rejected
def test_remove_orphan_file_only_own_unreferenced(self):
def test_disabled_endpoints_reject(self):
```

- [ ] **Step 2:** Jalankan `pos_next.api.test_outlet_monitoring`. Diharapkan FAIL.
- [ ] **Step 3:** Implementasikan. Baca header file dengan `file_doc.get_content()[:16]`; untuk video besar cukup baca 16 byte dari `file_doc.get_full_path()`.
- [ ] **Step 4:** Jalankan modul + Task 2-3. Diharapkan PASS.
- [ ] **Step 5: Checkpoint.**

---

### Task 5: API reviewer, notifikasi, realtime, gallery

**Files:**
- Create/replace stub: `pos_next/api/outlet_monitoring_review.py`
- Test: `pos_next/api/test_outlet_monitoring_review.py`

**Interfaces:**
- Consumes: access module (`is_reviewer`, `reviewer_companies`), service, `pos_next.realtime_events.publish_to_profile(event, message, pos_profile)`, `frappe.desk.doctype.notification_log.notification_log.make_notification_logs` (pola `services/reject_request.notify:680`).
- Produces:
  - `on_report_submitted(doc) -> None`: bila `notify_reviewers_on_submit`, kirim Notification Log ke user aktif yang punya REVIEWER_ROLE dengan scope mencakup `doc.company`. Lalu `_publish(doc)`.
  - `_publish(doc) -> None`: event `outlet_monitoring_updated`, payload `{"report": doc.name, "status": doc.status}`. Dikirim ke `doc.pos_profile`, atau untuk Per Hari ke semua POS Profile aktif di `doc.company`.
  - `finalize_review(name) -> dict`, whitelisted POST, khusus reviewer:
    - `frappe.db.get_value(..., for_update=True)`, lalu reload.
    - Wajib `status=="Submitted"`.
    - Bila `prevent_self_review` dan `submitted_by==session.user` → throw.
    - Throw bila: ada baris Pending; ada N/A saat `allow_na=0`; ada Fail/N/A tanpa catatan sesuai flag; `correction_requested` saat `allow_correction=0`.
    - `review_result = "Fail" if any Fail else "Pass"`. Set `reviewed_by/at`, `status="Reviewed"`, lalu `doc.save()` (update after submit).
    - Bila `notify_reporter_on_review`, beri notifikasi ke `submitted_by`, lalu `_publish`.
  - `waive(name, reason) -> dict`: reviewer; status Submitted atau Missed; `reason.strip()` wajib; set `waive_reason`, `status="Waived"`.
  - `get_gallery(filters=None, start=0, page_length=None) -> {"items": [...], "has_more": bool}`:
    - filters: `company, pos_profile, from_date, to_date, category, checkpoint, assessment, media_type`.
    - Scope company dari `pos_next.hq_scope.resolve_company_scope` (menolak company palsu).
    - `page_length` default `gallery_page_size`, clamp 10..200.
    - Join Report ⋈ Report Media ⋈ Report Checkpoint; `purged=0`, `docstatus=1`; urut `uploaded_at desc`.
    - Item: `report, media_row, file_url, thumbnail_url, media_type, assessment, checkpoint_name, category, company, pos_profile, operational_date, uploaded_at, submitted_by`.
    - Ambil `page_length+1` untuk menentukan `has_more`.

- [ ] **Step 1: Tulis tes yang gagal**

```python
def test_finalize_rejects_pending(self):
def test_finalize_requires_note_for_fail_and_na(self):
def test_finalize_rejects_na_when_disallowed(self):
def test_finalize_result_fail_if_any_fail(self):
def test_prevent_self_review(self):
def test_finalize_twice_second_fails(self):  # second call: status already Reviewed -> ValidationError
def test_cashier_cannot_finalize_or_waive(self):  # PermissionError
def test_waive_requires_reason_and_state(self):
def test_notify_reviewer_in_scope_only(self):  # reviewer X gets Notification Log, reviewer Y (perm company other) not
def test_publish_per_day_hits_all_company_profiles(self):  # patch publish_to_profile, assert called per profile
def test_gallery_scope_and_filters(self):  # reviewer X sees no Y items; forged company -> PermissionError
def test_gallery_pagination_has_more(self):
def test_gallery_skips_purged(self):
```

- [ ] **Step 2:** Jalankan modulnya. Diharapkan FAIL.
- [ ] **Step 3:** Implementasikan. Query gallery memakai `frappe.qb` atau SQL berparameter (tanpa interpolasi nilai).
- [ ] **Step 4:** Jalankan modul + `pos_next.api.test_outlet_monitoring` + `pos_next.tests.test_realtime_rooms`. Diharapkan PASS.
- [ ] **Step 5: Checkpoint.**

---

### Task 6: SPA kasir (menu, badge, view, media)

**Files:**
- Create: `POS/src/utils/outletMonitoringMedia.js` (+ `.test.js`), `POS/src/stores/outletMonitoring.js` (+ `.test.js`), `POS/src/components/pos/OutletMonitoringDialog.vue` (+ `.test.js`)
- Modify: `POS/src/components/pos/managementMenu.js`, `POSMenuDialog.vue` (:54-94 render item, :119-230 rantai v-if, :273 props, :291-302 filter), `POSHeader.vue` (badge tombol menu, pola :118-121), `POS/src/pages/POSSale.vue` (prop + refresh setelah buka shift)
- Test: `POS/src/components/pos/POSMenuDialog.test.js` (tambah kasus)

**Interfaces:**
- Consumes: kontrak endpoint Task 4 (path `pos_next.api.outlet_monitoring.<fn>`), `call` dari `utils/apiWrapper`, `compressImage(file, {maxEdge, quality})` (`utils/compressImage.js`; **mengembalikan file asli bila gagal**).
- Produces:
  - `preparePhoto(file, config) -> Promise<{main: File, thumb: File}>`: throw `Error(__("Photo could not be processed"))` bila hasil `=== file` atau `type !== "image/jpeg"`. `quality = config.photo_quality / 100`.
  - `checkVideo(file, config, maxSeconds) -> Promise<{file: File, poster: File | null}>`:
    - Type harus `video/mp4`, atau `video/webm` bila `allow_webm`.
    - Bila `duration > maxSeconds` → throw.
    - Bila `size > video_max_mb` → throw.
    - Poster dibuat best effort, null bila gagal.
  - `uploadPrivate(file, report) -> Promise<{name: string, file_url: string}>`: FormData `file, is_private=1, doctype="POS Monitoring Report", docname=report`. Pola `StockOpnameDialog.vue:1900-1912` + header CSRF.
  - Store `useOutletMonitoringStore`:
    - state: `enabled, config, tasks, count, loading`;
    - `refresh(posProfile)`;
    - `bindRealtime()`: `frappe.realtime.on("outlet_monitoring_updated", ...)` dengan refresh ber-debounce 1 s; `unbind`.
  - Menu item `{ id: "outlet-monitoring", icon: "camera", label: __("Outlet Monitoring"), group: "system", requiresOnline: true, requiresOutletMonitoring: true }`, plus prop `canOutletMonitoring` (enabled && `usePermissionCheck("POS Monitoring Report","write")`).
  - Item menu boleh punya `badge` (angka > 0) yang dirender di item dan tombol header, hanya bila `config.show_menu_badge`.
  - `OutletMonitoringDialog.vue` props `{ posProfile: String }`. View `list|doc`, tab `tasks|history`, string sesuai spec §8.1.
  - Alur upload per file: prepare → upload main (+ thumb) → `register_media`. Bila register gagal, panggil `remove_orphan_file` untuk tiap File yang terunggah, lalu tampilkan toast error.

- [ ] **Step 1: Tulis tes yang gagal** (Vitest, mock `call`/`fetch`/`compressImage`)

```js
it("preparePhoto rejects when compressImage returns the original file")
it("preparePhoto returns jpeg main and thumb with configured edges")
it("checkVideo rejects too long video and disallowed webm")
it("store count equals number of tasks, not checkpoints")
it("store disabled keeps menu hidden")
it("menu hides outlet-monitoring when offline or not permitted")
it("menu item shows badge only when show_menu_badge and count > 0")
it("evidence complete tick does not render Pass styling before review")
it("submit button disabled until all required checkpoints complete")
it("deletes orphan upload when register fails")
it("history shows Needs Correction and Create Revision when correction_requested")
```

- [ ] **Step 2:** `cd POS && npx vitest run src/utils/outletMonitoringMedia.test.js src/stores/outletMonitoring.test.js src/components/pos/OutletMonitoringDialog.test.js src/components/pos/POSMenuDialog.test.js`. Diharapkan FAIL.
- [ ] **Step 3:** Implementasikan. Input file: `<input type="file" :accept="..." capture="environment">` per checkpoint; objectURL dicabut saat unmount. Status pill memakai `statusTone`.
- [ ] **Step 4:** Jalankan tes yang sama + `npx vitest run src/components/pos` + `npm run lint` (Biome; bandingkan dengan baseline Task 0). Diharapkan PASS, tanpa temuan lint baru.
- [ ] **Step 5: Checkpoint.**

---

### Task 7: Peringatan di ShiftClosingDialog

**Files:**
- Modify: `ShiftClosingDialog.vue` (cari dengan `git ls-files | grep ShiftClosingDialog.vue`; blok peringatan pola :36-52, `loadClosingData` sekitar :912)
- Test: test ShiftClosingDialog yang sudah ada (cari `ShiftClosingDialog*.test.js`). Bila belum ada, buat di sebelahnya.

**Interfaces:**
- Consumes: `get_shift_tasks(opening_shift)` (Task 4), `useOutletMonitoringStore().config.warn_on_shift_close` (Task 6).
- Produces: blok peringatan "{0} outlet monitoring report(s) not submitted for this shift", daftar template. Tombol tutup shift **tetap aktif**. Error API menyembunyikan blok tanpa memblokir.

- [ ] **Step 1: Tulis tes yang gagal**

```js
it("shows outlet monitoring warning when shift has unsubmitted reports")
it("close button stays enabled while the warning is shown")
it("hides warning when warn_on_shift_close is off or API fails")
```

- [ ] **Step 2:** Jalankan file tes itu. Diharapkan FAIL.
- [ ] **Step 3:** Implementasikan. Panggilan dilakukan paralel dengan data closing; jangan menunda `loadClosingData`.
- [ ] **Step 4:** Jalankan file tes + semua tes ShiftClosing. Diharapkan PASS.
- [ ] **Step 5: Checkpoint.**

---

### Task 8: Desk (form Report, Gallery, workspace)

**Files:**
- Create: `pos_next/public/js/outlet_monitoring_utils.js` (UMD, pola `public/js/hq_monitoring_utils.js:9-11`), `pos_next/tests/js/outlet_monitoring_utils.test.cjs` (+ daftarkan di `index.js`)
- Create: `pos_next/pos_next/doctype/pos_monitoring_report/pos_monitoring_report.js`
- Create: `pos_next/pos_next/page/outlet_monitoring_gallery/` (`__init__.py`, `.json` dengan roles System Manager + reviewer, `.js` dengan `{% include "pos_next/public/js/outlet_monitoring_utils.js" %}`, `.css`)
- Modify: `pos_next/pos_next/workspace/posnext/posnext.json` (kartu "Outlet Monitoring")
- Create: `pos_next/patches/v2_36_0/outlet_monitoring_workspace.py` + baris di `patches.txt` `[post_model_sync]`. Isinya memanggil `pos_next.patches.v1_7_0.reinstall_workspace.execute()`.

**Interfaces:**
- Consumes: `finalize_review`, `waive`, `get_gallery` (Task 5), dan field Report (Task 2).
- Produces (helper murni, diekspor dari UMD):
  - `resolvePeriod(period, today) -> {from_date, to_date}` (ISO, Senin awal minggu) untuk Today/Yesterday/This Week/Last Week/This Month/Last Month. `Custom` mengembalikan null.
  - `galleryCardHtml(item) -> string`: escape semua teks; foto → `<img loading="lazy" src=thumbnail_url||file_url>`, video → placeholder ikon play SVG; **tidak pernah** `<video>`.
  - `mediaPreviewHtml(mediaRows) -> string`: untuk form; `purged` → teks "Media removed by retention"; video → `<video controls preload="metadata">`.
  - `assessmentTone(value) -> "green"|"red"|"gray"|"orange"`.
- Perilaku form: tombol "Finalize Review" (Submitted, reviewer) dan "Waive" (Submitted/Missed) dengan `frappe.prompt` untuk alasan. Setelahnya `frm.reload_doc()`. Field HTML `media_preview` di-render pada `refresh`.
- Gallery: filter `page.add_field` (company, pos_profile, period default dari settings `gallery_default_period`, from/to bila Custom, category, checkpoint, assessment, media_type), tombol "Load more" (append, `start += items.length`), dan klik kartu membuka `frappe.ui.Dialog` dengan preview penuh + link `/app/pos-monitoring-report/<name>`.

- [ ] **Step 1: Tulis tes yang gagal** (node:test)

```js
test("resolvePeriod This Week starts Monday", ...)  // today 2026-11-05 (Thu) -> 2026-11-02..2026-11-05
test("resolvePeriod Last Month", ...)               // -> 2026-10-01..2026-10-31
test("galleryCardHtml never emits <video> and escapes names", ...)  // name '<b>' -> '&lt;b&gt;'
test("galleryCardHtml video uses play placeholder", ...)
test("mediaPreviewHtml purged", ...)
```

- [ ] **Step 2:** `node pos_next/tests/js/outlet_monitoring_utils.test.cjs`. Diharapkan FAIL.
- [ ] **Step 3:** Implementasikan helper, form JS, page, workspace, patch.
- [ ] **Step 4:** Jalankan `node pos_next/tests/js/index.js`, lalu `bench --site fix.localhost migrate`. Verifikasi dengan `frappe.db.exists("Page","outlet-monitoring-gallery")` dan kartu workspace via `frappe.get_doc("Workspace","POSNext")` di fix. Diharapkan PASS dan kartu ada. Jalankan migrate kedua kali: patch tidak dijalankan ulang dan tidak error.
- [ ] **Step 5: Checkpoint.**

---

### Task 9: Terjemahan dan dokumen operator

**Files:**
- Modify: `pos_next/translations/id.csv`
- Create: `docs/OUTLET_MONITORING.md`

- [ ] **Step 1:** Kumpulkan string baru dari diff: `git diff origin/main -- POS/src pos_next | grep -oE "__\(\"[^\"]+\"|_\(\"[^\"]+\"" | sort -u`.
- [ ] **Step 2:** Tambahkan baris ke id.csv sesuai label spec §8.1/§9.1. Contoh: "Needs Correction" → "Perlu Perbaikan", "Waived" → "Dikecualikan", "Missed" → "Terlewat".
  - Pertahankan CRLF dan tanpa duplikat kunci.
  - Jalankan `python3 scripts/validate_id_csv.py` + `pos_next.tests.test_release_hygiene` (`test_translation_file_has_no_duplicate_keys`). Diharapkan PASS.
- [ ] **Step 3:** Tulis `docs/OUTLET_MONITORING.md` dengan isi:
  - setup (aktifkan Settings, beri role reviewer, buat Category/Checkpoint/Template, isi company target);
  - arti status;
  - retensi;
  - batas yang diketahui (spec §7.3, §12);
  - checklist verifikasi Frappe Cloud (batas upload, range request video) sebelum prod.
- [ ] **Step 4: Checkpoint.**

---

### Task 10: Verifikasi penuh + deploy dev (sesi utama)

- [ ] **Step 1:** Jalankan semua modul tes backend fitur secara serial, ditambah regresi:
  - `test_auto_close_shifts`, `test_closing_shift_race`, `test_realtime_rooms`, `test_uninstall_coverage`, `test_release_hygiene`, `api.test_reject_request`.

  Jalankan juga `cd POS && npx vitest run` dan `npm run build`, serta `node pos_next/tests/js/index.js`. Bandingkan dengan baseline Task 0.
- [ ] **Step 2:** Lakukan review whole-branch (opus-xhigh) dan perbaiki temuan penting.
- [ ] **Step 3:** Ajukan pesan commit ke user. Commit hanya setelah disetujui, dan push hanya bila diminta terpisah.
- [ ] **Step 4:** Deploy dev:
  - `docker/erp backup dev`, `docker/erp rebuild dev`, migrate, clear-cache;
  - verifikasi aset yang disajikan memuat `OutletMonitoringDialog`;
  - beri tahu user untuk cek di http://dev.localhost:8000.

  `enabled` tetap 0 sampai user meminta pilot.
- [ ] **Step 5:** Kumpulkan bukti dev (screenshot spec §10) di `docs/proofs/2026-11-05-outlet-monitoring.md`. Uji di tablet iMin (`capture`, format rekaman) dilakukan oleh user.

---

## Peta eksekusi Workflow (dynamic, <= 9 agen)

Tes backend wajib serial, jadi semua task backend dikerjakan **satu rantai agen berurutan**. Frontend dan Desk boleh paralel karena memakai mock/node dan menyentuh file yang berbeda. id.csv hanya disentuh Task 9.

| Fase | Agen | Isi | Bergantung |
|---|---|---|---|
| 0 | sesi utama | Task 0 (gerbang user: branch, fix stack, baseline) | - |
| A | `sonnet-max` #1 | Task 1 lalu Task 2 | 0 |
| A-review | `opus-xhigh` #2 | review skema + akses (Review Focus 1) | A |
| B | `sonnet-max` #3 | Task 3 → 4 → 5 (backend, serial) | A-review |
| B | `sonnet-max` #4 | Task 6 → 7 (SPA, kontrak dari Interfaces Task 4) | A-review |
| B | `sonnet-max` #5 | Task 8 (Desk, kontrak Task 5) | A-review |
| B-review | `opus-xhigh` #6 | backend (Task 3-5, Review Focus 2-5) | B#3 |
| B-review | `opus-xhigh` #7 | SPA + Desk (Task 6-8) | B#4, B#5 |
| C | `sonnet-max` #8 | Task 9 + perbaikan temuan review | B-review |
| D | `opus-xhigh` #9 | review whole-branch akhir | C |
| E | sesi utama | Task 10 (commit hanya dengan persetujuan, deploy dev) | D |

Routing model (keputusan user 2026-11-05):
- Semua kode ditulis `sonnet-max`, sedangkan `opus-xhigh` hanya me-review.
- Eskalasi ke `opus-xhigh` sebagai pengoding hanya bila Sonnet gagal dua kali pada task yang sama: tes tetap merah, atau perbaikan review ditolak lagi.
- Perbaikan temuan review dikerjakan kembali oleh Sonnet.

Aturan untuk setiap prompt agen:
- sertakan path spec + plan + nomor task;
- sebutkan file milik task itu (agen dilarang mengedit file milik task paralel);
- larangan commit/push/prod/`docker cp`/hapus volume;
- tes harus RED dulu baru GREEN;
- laporan wajib memuat perintah tes + output ringkas.

Agen yang butuh fix stack (A, B#3) harus menunggu gerbang Task 0.
