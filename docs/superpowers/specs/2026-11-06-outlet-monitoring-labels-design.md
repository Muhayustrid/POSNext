# Outlet Monitoring — Label Kustom, Scope Template, Laporan Manual & Jendela Waktu

Status: revisi 5 (2026-11-06): temuan review Opus ke-3 + Sonnet diterapkan
(§3.5 guard controller, §4.8 Manual diperketat + unboxing referensi DN atau PR, kunci kiriman dinormalisasi, §4.10
gallery); menunggu review.
Basis: branch `feat/outlet-monitoring` (779364ab), spec induk
`docs/superpowers/specs/2026-11-05-outlet-monitoring-design.md`. Bagian spec
induk yang digantikan: penilaian Pass/Fail/N/A, `review_result`, label per
Category, dan aturan scope Template (tabel kosong).

## 1. Tujuan

HQ ingin mengumpulkan data lapangan lewat **label yang dibuat sendiri**
(nama + warna), bukan tiga slot Pass/Fail/N/A yang hardcode. Kasir memilih
label per checkpoint saat mengisi task (bila diizinkan), HQ memutuskan label
final saat review. Selain itu Template mendapat opsi **semua / beberapa** untuk
Company, POS Profile dan Shift Type; cadence **Manual** untuk laporan berbasis
kejadian (mis. unboxing barang datang) dengan referensi Purchase Receipt /
Delivery Note; dan **jendela waktu isi** (mulai boleh diisi) untuk semua cadence.

Di luar cakupan: redesign UI Outlet Monitoring di SPA kasir (sub-proyek
terpisah). SPA hanya mendapat pemilih label di dialog yang ada.

## 2. Keputusan user

| Topik | Keputusan |
|---|---|
| Peran label | Kasir mengusulkan, HQ memutuskan; keduanya tersimpan |
| Arti label | Label bebas: teks + warna, **tanpa** arti Lulus/Gagal |
| Pass/Fail/N/A lama | **Diganti** label sepenuhnya |
| Sumber daftar label | Per Checkpoint (bila diisi) → per Category → default Settings |
| Saklar kasir | Per Category, opsional (tidak wajib diisi untuk submit) |
| Warna | Palet tetap, bukan color picker |
| Scope Template | All / Selected untuk Company, POS Profile, Shift Type |
| Unboxing | Cadence `Manual`: kasir memulai laporan sendiri |
| Referensi Manual | Purchase Receipt / Delivery Note outlet, **wajib**, satu dokumen sekali per template |
| Jendela waktu | Manual: batas X jam setelah mulai; Per Shift/Per Hari: jam mulai boleh isi |
| Tutup shift | Draft Manual ikut diperingatkan saat tutup shift (tidak otomatis Missed) |

## 3. Model data

### 3.1 DocType baru `POS Monitoring Label`

| Field | Tipe | Catatan |
|---|---|---|
| `label_name` | Data, reqd, unique | autoname `field:label_name`; **rename dilarang** (`allow_rename = 0`) agar ID di snapshot tetap valid |
| `color` | Select, reqd | `Green\nRed\nYellow\nOrange\nBlue\nGray\nPurple`, default `Gray` |
| `disabled` | Check | label disabled tidak ikut snapshot baru; snapshot lama tetap memakainya |

Permission: `System Manager` dan `POSNext Monitoring Reviewer`
(`REVIEWER_ROLE` di `pos_next/outlet_monitoring_setup.py`) →
create/write (tanpa delete); `All` → read (dibutuhkan Link).
**Label tidak bisa dihapus**: `on_trash` selalu menolak (server-side), karena
snapshot `label_options` merujuk label lewat JSON yang tidak dicek link-check
Frappe. Pensiunkan label dengan `disabled`.
Menonaktifkan label ditolak bila Settings `enabled = 1` dan label itu satu-satunya
label aktif di `default_labels` (validasi di controller Label).

### 3.2 Child table baru `POS Monitoring Label Item` (istable)

| Field | Tipe |
|---|---|
| `label` | Link POS Monitoring Label, reqd, in_list_view |

Dipakai oleh Category, Checkpoint dan Settings. Urutan baris = urutan tampil.
Duplikat label dalam satu tabel ditolak di `validate` induknya.

### 3.3 Perubahan DocType yang ada

**POS Monitoring Category**
- Hapus `pass_label`, `fail_label`, `na_label`.
- Tambah `labels` (Table POS Monitoring Label Item) dan `cashier_can_label`
  (Check, default 0).

**POS Monitoring Checkpoint**
- Tambah `labels` (Table POS Monitoring Label Item), deskripsi "Kosongkan untuk
  memakai label Category".

**POS Monitoring Settings** (Single)
- Hapus `default_pass_label`, `default_fail_label`, `default_na_label`,
  `allow_na`, `require_note_on_fail`, `require_note_on_na`.
- Tambah `default_labels` (Table POS Monitoring Label Item).
- Validasi: bila `enabled = 1`, `default_labels` minimal berisi satu label
  aktif. Bersama larangan disable di §3.1, setiap report baru punya daftar
  label (lihat §4.1).

**POS Monitoring Report Checkpoint**
- Hapus `pass_label`, `fail_label`, `na_label`, `assessment`.
- Tambah:
  | Field | Tipe | Meta |
  |---|---|---|
  | `label_options` | Small Text (JSON `[{"label","color"}]`) | read_only, hidden; bagian snapshot |
  | `cashier_can_label` | Check | read_only; bagian snapshot |
  | `cashier_label` | Link POS Monitoring Label | read_only (permlevel 0) |
  | `label` | Link POS Monitoring Label | `permlevel 1`, `allow_on_submit 1`, in_list_view |
- `assessment_note` tetap (`permlevel 1`, `allow_on_submit 1`).
- Warna untuk tampilan **selalu** diambil dari `label_options` (snapshot),
  bukan dari master terbaru.

**POS Monitoring Report**
- Hapus `review_result` (juga dari `REVIEW_HEADER_FIELDS` di controller).

**POS Monitoring Template**
- Tambah `company_scope`, `profile_scope`, `shift_type_scope`: Select
  `All\nSelected`, **tanpa default di JSON dan tanpa `reqd` JSON** (agar kolom
  baru tetap kosong saat model sync dan patch bisa membedakan template lama).
  Controller `before_validate` mengisi nilai kosong: company → `Selected`;
  profile/shift → `Selected` bila tabelnya terisi, selain itu `All`. Form baru
  di Desk mengisi nilai yang sama lewat JS `onload`.
- Tabel `companies` / `pos_profiles` / `shift_types` tampil hanya bila scope
  `Selected` (`depends_on`). Bila scope `All`, isi tabel **diabaikan**.
- `company_scope` selalu tampil. `profile_scope` dan `shift_type_scope` tampil
  dan divalidasi untuk cadence **Per Shift dan Manual**; disembunyikan dan
  diabaikan untuk Per Hari.

### 3.4 Field untuk Manual dan jendela waktu

**POS Monitoring Template**
- `cadence` options: `Per Shift\nPer Hari\nManual`.
- `submit_within_hours` (Int, Manual saja, reqd bila Manual, > 0).
- `open_after_minutes` (Int, Per Shift saja, opsional, ≥ 0).
- `open_time` (Time, Per Hari saja, opsional).
- `due_minutes_after_open` / `due_time` tetap untuk Per Shift / Per Hari.

**POS Monitoring Report**
- `cadence` options ikut ditambah `Manual`.
- `opens_at` (Datetime, read_only): sebelum waktu ini report belum bisa diisi.
  Kosong = bisa diisi kapan saja.
- `reference_doctype` (Select `\nPurchase Receipt\nDelivery Note`, read_only).
- `reference_name` (Dynamic Link ke `reference_doctype`, read_only).
- Ketiganya ditambahkan ke `IMMUTABLE_REPORT_FIELDS`
  (`pos_monitoring_report.py:10`).

### 3.5 Guard controller Report (wajib, semua ber-RED test)
- **Report hanya dibuat oleh server.** Pada `is_new()`, insert ditolak
  (`PermissionError`) kecuali `flags.internal_report_build` aktif. Flag ini hanya
  diset oleh `build_report` caller di `ensure_obligations`,
  `start_manual_report` dan `create_revision` (tiga jalur insert yang ada:
  `services/outlet_monitoring.py:176,216`, `api/outlet_monitoring.py:491`).
  Dengan begitu native insert kasir (termasuk report Manual palsu, referensi
  outlet lain, atau `obligation_key` buatan untuk memblokir laporan sah)
  tidak mungkin. Guard `is_new` lama tetap sebagai lapisan kedua.
- Aturan non-reviewer pada insert/update (`pos_monitoring_report.py:83-93`)
  memeriksa `label`, `cashier_label` (kecuali flag
  `internal_cashier_label_change`) dan `assessment_note`, menggantikan
  `assessment`.
- `review_result` dihapus dari `REVIEW_HEADER_FIELDS` dan dari daftar field
  stored di `before_update_after_submit`.

## 4. Aturan

### 4.1 Resolusi label (hanya di `build_report`, saat report pertama dibuat)
1. Label aktif di `Checkpoint.labels`, bila minimal satu.
2. Selain itu label aktif di `Category.labels`, bila minimal satu.
3. Selain itu label aktif di `Settings.default_labels` (dijamin tidak kosong
   saat fitur aktif, §3.1 + §3.3).
4. Pengaman akhir: bila hasil tetap kosong, `build_report` melempar
   `ValidationError`. Di `ensure_obligations`, **panggilan `build_report` +
   insert** dibungkus per template/target (try/except saat ini hanya
   membungkus insert): error dicatat `frappe.log_error` lalu lanjut ke template
   lain. Di `start_manual_report` error diteruskan ke kasir. Report dengan
   `label_options` kosong tidak pernah disimpan.

`cashier_can_label` = `Category.cashier_can_label`.

### 4.2 Revisi (`create_revision`)
Tetap **menyalin** seluruh snapshot checkpoint report asal, seperti perilaku
sekarang. Daftar field eksplisit di `create_revision`
(`api/outlet_monitoring.py:458-492`) diperbarui: salin `label_options`,
`cashier_can_label`, `reference_doctype`, `reference_name`; **jangan** salin
`opens_at` (revisi tanpa jendela, §4.9). Yang dikosongkan: `cashier_label`,
`label`, `assessment_note`. Insert memakai `flags.internal_report_build`. Report asal tidak
berubah. Syarat revisi tidak berubah (Reviewed + `correction_requested`).

### 4.3 Kasir — endpoint `set_cashier_label(report, checkpoint_row, label=None)` (POST)
Urutan:
1. `_check_enabled()`; lock parent:
   `frappe.db.get_value("POS Monitoring Report", report, "name", for_update=True)`.
2. Load doc; `has_permission(write)`; status harus `Draft` dan `docstatus 0`.
3. `checkpoint_row` harus baris `checkpoints` milik report ini.
4. Baris harus `cashier_can_label = 1`.
5. `label` kosong (hapus pilihan) atau ada di `label_options` baris itu.
6. Set `cashier_label`, simpan dengan flag sempit
   `flags.internal_cashier_label_change` (`ignore_permissions`); tidak ada
   commit manual. Kembalikan `get_report(report)`.

Proteksi native save: `cashier_label` dan `label` **tidak** termasuk field
mutable kasir. `_validate_checkpoint_snapshot` hanya membolehkan
`cashier_label` berubah bila flag di atas aktif. Kasir tetap tidak boleh mengisi
`label` / `assessment_note` (aturan insert/update yang ada diganti dari
`assessment` ke `label`).

Submit tidak mewajibkan `cashier_label`.

### 4.4 Prefill label final saat submit
Di `submit_report`, **sebelum** `doc.submit()`: untuk setiap baris dengan
`cashier_label` dan `label` kosong, set `label = cashier_label`. Nilai ikut
tersimpan dalam transaksi submit yang sama. `doc.flags.ignore_permissions = True`
yang sudah ada (`api/outlet_monitoring.py:422`) **wajib dipertahankan**: tanpa
itu `validate_higher_perm_levels` Frappe mengosongkan `label` (permlevel 1)
milik kasir sebelum `validate`. `on_report_submitted` tidak menyentuh label.

### 4.5 Reviewer
- Native save pada report `Submitted` (tetap Submitted): boleh menyimpan label
  **parsial**; setiap `label` nonkosong harus ada di `label_options` barisnya.
  Hanya `label` dan `assessment_note` yang mutable; `cashier_label` dan semua
  field snapshot terkunci.
- Transisi ke `Reviewed` (native save maupun `finalize_review`, satu helper
  validasi bersama yang menggantikan logika ganda di
  `outlet_monitoring_review.py:91-104` dan `pos_monitoring_report.py:274-287`;
  cek `allow_na`/`require_note_*` dan turunan `review_result` dihapus): semua baris wajib punya `label` yang ada di
  `label_options`; catatan opsional. Self-review, `allow_correction`,
  attribution server-side: tidak berubah.
- Report `Reviewed` / `Waived`: `label`, `assessment_note` terkunci (lock yang
  ada diperluas ke `label`).
- Waive dan Missed tidak berubah.
- Notifikasi finalize: subjek "POS Monitoring Report reviewed: {name}" tanpa
  hasil Pass/Fail.

### 4.6 Status tampilan setelah review
Tidak ada agregat lulus/gagal. Status report ditampilkan netral:
`Reviewed`, atau `Needs Correction` bila `correction_requested`. Tidak ada
turunan makna dari teks/warna label.

### 4.7 Scope Template (`ensure_obligations`)
- `company_scope = All` → semua Company yang punya POS Profile aktif
  (`disabled = 0`); `Selected` → isi tabel.
- Per Shift: `profile_scope`, `shift_type_scope` All → tanpa filter; Selected →
  isi tabel.
- Per Hari: hanya company scope; profile/shift tidak dipakai dan tidak
  divalidasi.
- Validasi Template: scope `Selected` wajib minimal satu baris (profile/shift
  hanya untuk Per Shift dan Manual).
- Mengubah scope tidak mengubah report yang sudah dibuat.

### 4.8 Cadence Manual (unboxing)
- `ensure_obligations` dan cron **tidak** membuat report Manual.

**Pencocokan scope Manual** (satu helper, dipakai ketiga endpoint di bawah):
company profile cocok dengan `company_scope`; `profile_scope` Selected →
`pos_profile` harus ada di tabel; `shift_type_scope` Selected → harus ada shift
terbuka untuk profile itu dan `shift_slot`-nya ada di tabel (tanpa shift
terbuka → template tidak cocok, fail closed); `shift_type_scope` All → boleh
tanpa shift terbuka.

**Alur unboxing (keputusan user):** unboxing tidak pernah mengunci PR dan boleh
dilakukan sebelum atau sesudah PR. Kasir memilih referensi **DN atau PR**.
- Kiriman HQ/antar company punya DN dan (nantinya) PR internal yang
  `inter_company_reference`-nya menunjuk DN itu. Keduanya mewakili satu
  kiriman, jadi kuncinya dinormalisasi ke DN: PR internal dengan
  `inter_company_reference` terisi memakai `obligation_key`
  `{template}|manual|Delivery Note|{DN}`. Akibatnya DN dan PR-nya tidak bisa
  dilaporkan dua kali, dan kandidat yang kiriman-nya sudah dilaporkan tidak
  muncul. `reference_doctype/name` tetap menyimpan dokumen yang dipilih kasir.
- PR supplier (`is_internal_supplier = 0`) memakai kunci PR-nya sendiri.
- PR internal **wajib** punya `inter_company_reference` (keputusan user). Alur PR
  di SPA sudah memaksanya (`api/purchase_receipts.py` `save_purchase_receipt`);
  PR internal tanpa referensi (dev: 4 PR lama buatan Desk, 15-27 Okt 2026, di
  luar jendela 7 hari) **bukan kandidat** dan ditolak `start_manual_report`;
  kasir memakai DN-nya. Memaksa referensi pada PR buatan Desk di luar scope
  spec ini.

**Aturan referensi** (satu helper, dipakai kandidat **dan** start):
- Purchase Receipt: `docstatus = 1`, `is_return = 0`, `company` = company
  profile. Bila `is_internal_supplier = 1`: `inter_company_reference` harus
  menunjuk DN yang lolos aturan DN di bawah (kecuali batas tanggal), **dan
  setiap item** PR punya `delivery_note_item` yang parent-nya DN yang sama.
  PR internal campuran (item dari beberapa DN) atau header tidak cocok bukan
  kandidat; kasir memakai DN masing-masing. Satu DN boleh punya beberapa PR
  parsial; semuanya berbagi kunci DN (sekali lapor per kiriman).
- Delivery Note: `docstatus = 1`, `is_return = 0`, `is_internal_customer = 1`,
  `represents_company` = company profile, `company` ≠ company profile
  (kiriman antar company ke outlet). Pengirim = `DN.company`.
- `today − 7 ≤ posting_date ≤ today` untuk **dokumen yang dipilih** saja; DN
  hasil normalisasi PR tidak dibatasi umur (contoh dev: PR 2026-11-03 dari DN
  2026-10-13 tetap kandidat).
- Belum dipakai oleh template yang sama (cek `obligation_key` revisi 1).

**Endpoint** (semua: `_check_enabled()`, `_assert_profile_access(pos_profile)`,
parameter scalar, company diturunkan dari profile di server):
- `get_manual_templates(pos_profile)` (GET): template Manual aktif yang lolos
  pencocokan scope.
- `get_reference_candidates(pos_profile, template, txt=None)` (GET): template
  harus Manual, aktif, dan lolos pencocokan scope. Query `frappe.get_all`
  (tanpa izin baca PR/DN) dengan **allowlist field**: nama, tipe,
  `posting_date`, pengirim (PR `supplier_name`, DN `company`), jumlah baris
  item (`count` child). Tanpa harga/total. `txt` difilter pada nama/pengirim;
  maksimal 20 per tipe.
- `start_manual_report(pos_profile, template, reference_doctype,
  reference_name)` (POST):
  1. Pemeriksaan umum di atas; template Manual, aktif, lolos scope.
  2. Referensi lolos aturan referensi (dibaca ulang dari DB).
  3. `obligation_key = f"{template}|manual|{doctype}|{name}"` dengan
   `(doctype, name)` hasil normalisasi kiriman di atas,
     `revision = 1`; unique constraint yang ada menolak duplikat (termasuk dua
     klik bersamaan) → tangkap `UniqueValidationError`/`DuplicateEntryError` →
     "Dokumen ini sudah dilaporkan".
  4. `build_report` dengan `company`, `pos_profile`, `opening_shift` (shift
     terbuka bila ada, boleh kosong), `operational_date = today`,
     `opens_at = None`, `due_at = now + submit_within_hours`, referensi; insert
     dengan `flags.internal_report_build`.
  5. Kembalikan `get_report(name)`.

**Akses & daftar**
- `outlet_monitoring_access.has_permission` write (`:117-124`) mendapat cabang
  `elif cadence == "Manual": return bool(profile and profile in cashier_profiles(user))`.
- `get_my_tasks`: query keempat untuk Draft Manual dengan `pos_profile` itu;
  hasil dideduplikasi per nama (Manual yang menyimpan `opening_shift` juga cocok
  dengan query Per Shift).
- `get_shift_tasks` (peringatan tutup shift) **tetap** menyertakan Draft Manual
  shift itu (keputusan user); teks peringatan dibuat netral ("laporan monitoring
  belum selesai"), tidak menyebut Per Shift.
- `mark_missed`: Manual Missed bila `now > due_at + shift_missed_grace_hours`,
  tidak bergantung status shift. Revisi Manual memakai aturan revisi yang ada
  (`due_at = now + grace`, Missed setelah `due_at + grace`).
- Submit terlambat mengikuti `allow_late_submission` yang ada.

### 4.9 Jendela waktu isi (`opens_at`)
- Per Shift: `opens_at = period_start_date + open_after_minutes` bila diisi.
- Per Hari: `opens_at = operational_date + open_time` bila diisi.
- Manual dan revisi: `opens_at` kosong.
- Validasi Template: Per Shift `open_after_minutes < due_minutes_after_open`;
  Per Hari `open_time < due_time` (bila keduanya diisi).
- Sebelum `opens_at`, server **menolak** `register_media`, `set_cashier_label`
  dan `submit_report` ("Laporan baru bisa diisi mulai {jam}"). `remove_media`
  tidak perlu (belum ada media). `get_my_tasks` tetap menampilkan task beserta
  `opens_at`.
- Waktu dibandingkan dengan `now_datetime()` server (zona waktu site).

### 4.10 Gallery
- `get_gallery` (`outlet_monitoring_review.py:155-244`) mengembalikan per item
  `label` dan `color` yang di-resolve server dari `label_options` baris itu;
  filter `assessment` diganti `label` (`c.label = %(label)s`).
- Opsi filter Label diisi dari `frappe.db.get_list("POS Monitoring Label")`
  (termasuk disabled); tidak perlu endpoint baru.
- Item tanpa `label` (Submitted) → "Belum dinilai" abu.
- CSS gallery menambah `badge-blue`, `badge-yellow`, `badge-purple`.

## 5. Tampilan & API baca

- **`get_report`**: per checkpoint kembalikan `label_options` (parsed),
  `cashier_can_label`, `cashier_label`; setelah Reviewed juga `label` +
  `assessment_note`. Hapus `pass/fail/na_label` dan `assessment`.
- **`get_my_tasks`**: tetap ringkasan, tidak membawa opsi label; tambah
  `opens_at`, `reference_doctype`, `reference_name`.
- **SPA kasir — Manual**: tombol "Mulai Laporan" di dialog bila
  `get_manual_templates` tidak kosong → pilih template → pilih dokumen dari
  `get_reference_candidates` (daftar dengan pencarian) → report Draft terbuka.
  Task belum dibuka tampil "Mulai {jam}" dengan aksi isi dinonaktifkan.
- **Desk report form/list**: tampilkan referensi (klik ke dokumen bagi user yang
  punya izin) dan `opens_at`; filter list per cadence Manual.
- **`get_history`**: hapus `review_result`.
- **SPA kasir** (`OutletMonitoringDialog.vue` + store): di checkpoint Draft
  dengan `cashier_can_label`, chip label berwarna (pilih satu, tap lagi untuk
  batal) memanggil `set_cashier_label`. Setelah Reviewed, badge `label` dengan
  warna snapshot. Riwayat memakai status netral §4.6 (hapus "Non-Compliant").
- **Desk report form**: grid checkpoint menampilkan `cashier_label` dan
  `label`. Pemilih `label` memakai `set_query` dengan **query server kustom**
  (`pos_next.api.outlet_monitoring_review.label_options_query`) yang
  mengembalikan isi `label_options` baris itu, termasuk label yang sudah
  disabled (pencarian Link standar Frappe menyembunyikan `disabled = 1`).
  Grid set_query bersifat per-tabel, jadi baris diambil dari callback:
  `frm.set_query("label", "checkpoints", (doc, cdt, cdn) => ({query: "pos_next.api.outlet_monitoring_review.label_options_query", filters: {report: doc.name, checkpoint_row: cdn}}))`.
  Endpoint `@frappe.whitelist()` + `@frappe.validate_and_sanitize_search_inputs`
  (preseden `api/backdate_invoices.py:456`); baris `new-…`/tidak ada → `[]`.
  Kontrak query: argumen standar `search_link` + filter scalar `report` dan
  `checkpoint_row`; wajib `_assert_reviewer_or_manager()`,
  `has_permission(report, "write")` (scope company reviewer), `checkpoint_row`
  harus child report itu; opsi dibaca dari snapshot di DB, bukan dari klien;
  `txt` difilter, hasil dibatasi `page_len`.
- **Gallery**: lihat §4.10.
- Palet → `STATUS_TONES` di SPA (`utils/statusTone.js`) ditambah `yellow` dan
  `purple`.
- **`get_history`**: tambah `reference_name` untuk Manual.
- **Sidebar/workspace**: tambah link "Monitoring Label" di section Outlet
  Monitoring (`sidebar/posnext/posnext.json`, `workspace_sidebar/posnext.json`,
  workspace patch yang ada).
- **Translasi**: string baru masuk `pos_next/translations/id.csv` (byte/CRLF
  dipertahankan).

## 6. Setup & migrasi

### 6.1 `outlet_monitoring_setup.py`
Ganti pengisian `default_pass_label`: bila `default_labels` kosong, buat (bila
belum ada) label `Sesuai SOP` (Green), `Tidak Sesuai SOP` (Red),
`Tidak Berlaku` (Gray) dan isi `default_labels`.

### 6.2 Patch `post_model_sync` `pos_next.patches.v2_36_0.outlet_monitoring_labels`
Berjalan setelah DocType baru ada. Kolom lama tidak di-drop Frappe saat field
dihapus dari JSON, jadi dibaca via SQL bila `frappe.db.has_column` true.

1. **Kumpulkan teks legacy** dari tiga sumber:
   - Category: kolom `pass_label`, `fail_label`, `na_label` (tabel biasa).
   - Settings (Single): baris `tabSingles` dengan
     `doctype = 'POS Monitoring Settings'` dan field `default_*_label`
     (bukan `has_column`).
   - Report Checkpoint **semua status**: kolom `pass_label`, `fail_label`,
     `na_label`.
2. **Buat master Label** untuk setiap teks unik nonkosong (trim). Warna
   ditentukan deterministik oleh slot pertama yang memakai teks itu dengan
   prioritas pass → Green, fail → Red, na → Gray. Label yang sudah ada tidak
   diubah (rerun aman).
   Semua daftar (tabel dan `label_options`) **dideduplikasi** per ID label
   setelah trim, mempertahankan urutan kemunculan pertama.
3. **Isi tabel**: Category `labels` dan Settings `default_labels` dari teks
   legacy-nya (urutan pass, fail, na), **hanya bila tabel masih kosong**. Bila
   Settings tidak punya teks legacy, pakai default §6.1.
4. **Report Checkpoint (semua status)**: bila `label_options` kosong, isi dari
   teks legacy baris itu; bila baris tidak punya teks legacy, pakai label
   Category saat ini, lalu default Settings. Kolom lama `assessment`
   Pass/Fail/N/A → `label` sesuai slot (dev: 0 Reviewed, jadi nyaris tanpa efek).
   Konversi `assessment → label` dan pengisian `label_options` terjadi **hanya
   pada baris yang `label_options`-nya masih kosong** (belum pernah dimigrasi);
   `label` dan `assessment_note` yang sudah terisi tidak pernah ditimpa.
   Ditulis via bulk `UPDATE` SQL per kelompok teks yang sama (bypass snapshot
   guard; skala bisa puluhan ribu baris). Insert Label didahului cek
   keberadaan case-insensitive (collation utf8mb4).
5. **Template** (hanya baris yang field scope-nya masih NULL/kosong, karena
   kolom baru tanpa default, §3.3): `companies` kosong → `Selected` (tetap tidak berlaku sampai
   diisi); terisi → `Selected`. `pos_profiles`/`shift_types` kosong → `All`,
   terisi → `Selected`. Ditulis via `frappe.db.set_value`. Scope yang sudah
   dipilih user tidak pernah ditimpa pada rerun.
6. Idempotent. Pada site tanpa data monitoring (prod) hanya langkah 3 Settings
   yang berjalan.

Batas yang diketahui: bila kolom lama sudah hilang (mis. `bench trim-tables`),
langkah 4 mengisi dari Category/Settings saat ini, sehingga teks historis report
lama tidak dipertahankan.

### 6.3 Migrasi rev 4
Tidak ada konversi data: field baru kosong berarti perilaku lama (tanpa jendela,
tanpa referensi). Opsi `Manual` hanya ditambahkan.

## 7. Pengujian (reload dari DB, bukan hanya respons endpoint)

- Service: resolusi label (checkpoint > category > settings; disabled
  dilewati); snapshot tidak berubah saat master diedit/disabled; Settings
  enabled tanpa default label ditolak.
- `set_cashier_label`: set/hapus; ditolak bila saklar mati, label di luar
  daftar, status bukan Draft, baris milik report lain, user outlet lain; native
  save kasir tidak bisa mengubah `cashier_label`/`label`; setter setelah report
  di-submit atau ditandai Missed (oleh `mark_missed`) ditolak.
- Label: delete selalu ditolak; disable default aktif terakhir ditolak saat
  fitur enabled; `build_report` dengan daftar kosong tidak menyimpan report
  dan tidak menghentikan template lain.
- `label_options_query`: kasir ditolak, reviewer di luar scope company ditolak,
  `checkpoint_row` milik report lain ditolak, label disabled dalam snapshot
  muncul.
- Submit: prefill `label` dari `cashier_label` tersimpan di DB.
- Reviewer: simpan parsial OK; label di luar daftar ditolak; transisi Reviewed
  (native + finalize) ditolak bila ada baris tanpa label; Reviewed/Waived
  terkunci; label disabled dalam snapshot tetap bisa dipilih lewat query kustom.
- Revisi: snapshot tersalin, `cashier_label`/`label`/note kosong.
- Scope: All/Selected ketiga dimensi; Selected kosong ditolak; Per Hari
  mengabaikan profile/shift; isi tabel diabaikan saat All.
- Patch: fixture Category + Settings (Singles) + report empat status dengan teks
  snapshot berbeda dari master + teks sama di dua slot (dedup) + template lama
  dengan tabel profile/shift terisi (hasil `Selected`, diuji pada kolom scope
  NULL seperti setelah model sync); rerun setelah reviewer mengubah `label` dan
  user mengubah scope tidak menimpa apa pun;
  fresh install.
- Vitest: chip label (pilih/batal/tanpa saklar), badge Reviewed berwarna,
  riwayat status netral.
- Node: util gallery warna label.
- Regresi: suite outlet monitoring yang ada disesuaikan (assessment → label).
- Guard: native insert Report oleh kasir ditolak (Per Hari, Manual tanpa
  referensi, referensi outlet lain, `obligation_key` buatan); insert dengan
  `cashier_label`/`label` terisi oleh kasir ditolak; field immutable baru tidak
  bisa diubah; template tanpa label di `ensure_obligations` tidak menghentikan
  template lain.
- `label_options_query` dengan baris `new-…` → `[]`.
- Konkurensi: dua `set_cashier_label` bersamaan dengan `submit_report` /
  `mark_missed` (lock parent → salah satu ditolak, tidak ada label tersimpan pada
  report non-Draft).
- Manual: `start_manual_report` sukses untuk PR company outlet dan DN dengan
  `represents_company` outlet; ditolak untuk PR company lain, draft/cancelled,
  retur (`is_return`), DN ke customer eksternal, DN lalu PR-nya (kiriman sama) ditolak sebagai
  duplikat, PR internal tanpa `inter_company_reference`, PR internal campuran /
  header tidak cocok dengan item, PR baru dari DN lama tetap kandidat, `posting_date` di masa depan,
  lebih tua dari 7 hari, shift scope Selected tanpa shift terbuka, template non-Manual/nonaktif/di luar scope, profile
  yang tidak boleh diakses kasir; dokumen sama dua kali → ditolak, dokumen sama
  untuk template Manual lain → boleh; dua `start_manual_report` bersamaan →
  satu report + pesan ramah; `get_reference_candidates` tanpa harga,
  mengecualikan dokumen terpakai, menolak Guest/profile lain/template di luar
  scope; kasir bisa menulis report Manual profile-nya, tidak untuk profile lain;
  Draft Manual muncul di `get_my_tasks` sekali dan di `get_shift_tasks`; Missed setelah `due_at + grace`; revisi
  menyalin referensi.
- Jendela waktu: `opens_at` dihitung benar untuk Per Shift/Per Hari; sebelum
  `opens_at` upload/label/submit ditolak, sesudahnya boleh; validasi template
  open < due; field kosong = perilaku lama.
- Vitest: alur Mulai Laporan (template → dokumen → report), task belum dibuka.

## 8. Risiko yang diterima
- Label tidak bisa dihapus atau di-rename; label salah ketik hanya bisa
  di-disable (tetap tampil di report lama).
- Report Manual yang terlewat tidak bisa dibuat ulang untuk dokumen yang sama;
  koreksi lewat revisi atau Waive.

## Lampiran A. Urutan task (masukan untuk writing-plans)
- T0 DocType JSON baru/berubah (Label, Label Item, field Category/Checkpoint/
  Template/Settings/Report) + sidebar.
- T1 Controller master (Label guard, Settings default, Template scope +
  validasi jendela) + `template.js`.
- T2 Snapshot & guard Report (§3.5, `build_report`, akses Manual, setup).
- T3 Alur kasir (`set_cashier_label`, prefill submit, `opens_at`,
  `get_my_tasks`, `get_history`).
- T4 Cadence Manual (tiga endpoint, `mark_missed`, `get_shift_tasks`).
- T5 Reviewer & gallery (helper validasi, `label_options_query`, warna).
- T6 SPA kasir (pilih label, Mulai Laporan, tombol nonaktif sebelum
  `opens_at`).
- T7 Patch migrasi + translasi CSV.
- T8 Suite test regresi + konkurensi.
