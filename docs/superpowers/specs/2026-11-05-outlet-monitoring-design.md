# Spec: Outlet Monitoring v1

Tanggal: 2026-11-05
Status: DRAFT, menunggu review user
Dasar: handoff `POSNext_Outlet_Monitoring_Agent_Handoff_v2.0/` (produk) dan audit
`docs/superpowers/specs/2026-11-05-outlet-monitoring-handoff-audit.md` (kesesuaian repo).
Basis kode: `origin/main` 2c25ee49.

Spec ini **menggantikan** bagian teknis handoff (DOCTYPE checklist, API contract,
scheduler/reminder, offline sync) untuk v1. Aturan UI handoff
(`03_UX_AND_DESIGN_LOCK/UI_PRESERVATION_CONTRACT.md`) tetap berlaku, kecuali bagian
yang ditunda di §11.

## 1. Tujuan

HQ menugaskan checklist foto/video (mis. Cleaning: Meja Kerja, Mesin Kopi, Oven) ke
outlet. Kasir mengisinya dari POS, lalu HQ menilai setiap checkpoint
(Sesuai/Tidak Sesuai SOP) dari Desk dan melihat semua bukti di Gallery.

Keputusan user (2026-11-05): v1 online saja, foto + video, semua laporan dinilai HQ,
jadwal Per Shift + Per Hari.

## 2. Istilah

| Istilah | Arti di repo |
|---|---|
| Outlet | Company (`patches/v2_35_0/outlets_by_company.py`) |
| Shift | POS Opening Shift; satu yang terbuka per POS Profile (`pos_opening_shift.py:81`) |
| Kewajiban | Satu POS Monitoring Report berstatus Draft, dibuat server |
| Checkpoint | Satu objek yang difoto/divideo, dengan aturan bukti |
| Bukti lengkap | Jumlah media tiap checkpoint wajib >= minimum. Ini **bukan** Pass |
| Penilaian | Pending / Pass / Fail / N/A per checkpoint, diisi HQ |

## 3. Peran

| Peran | Hak |
|---|---|
| `POSNext Cashier` (sudah ada) | Melihat dan mengisi Report untuk POS Profile tempat ia menjadi POS Profile User; submit; membuat revisi bila diminta |
| `POSNext Monitoring Reviewer` (**baru**) | Mengelola master (Category, Checkpoint, Template); membaca semua Report dalam company yang diizinkan; mengisi penilaian; menyelesaikan review; waive |
| System Manager | Semua; menetapkan siapa reviewer |

- Role baru ikut dibuat idempotent di `install.after_migrate`, mengikuti pola
  `POSNext System Stock Reader` (`stock_opname_setup.py:34`).
- Scope company reviewer memakai `hq_scope.get_permitted_companies`
  (`hq_scope.py:19`). Tanpa User Permission, scope-nya tidak dibatasi; ini perilaku
  yang sama dengan HQ Sales Monitoring.
- Role HQ lain yang sudah ada (`hq_monitoring.py:97`, `authz.py:8`,
  `stock_opname_setup.py:31`) **tidak** otomatis mendapat akses.

## 4. Model data

Semua DocType berada di module `POS Next`. Penamaan memakai prefix `POS Monitoring`.

### 4.1 POS Monitoring Category (master)
`category_name` (unik), `description`, `pass_label` (default "Sesuai SOP"),
`fail_label`, `na_label` (kosong = default dari POS Monitoring Settings §9), `disabled`.

Label tampilan per kategori (mis. Bersih/Kotor) disimpan di kategori, bukan di tiap
checkpoint, agar tidak diulang-ulang.

### 4.2 POS Monitoring Checkpoint (master)
`checkpoint_name`, `category` (Link), `instruction` (Small Text),
`media_type` (Select: Photo / Video / Photo or Video), `min_count` (Int, default 1),
`max_count` (Int, default 3), `reference_image` (Attach Image, opsional, public:
foto contoh dari HQ, bukan bukti), `video_max_seconds` (Int, 0 = pakai settings), `disabled`.

### 4.3 POS Monitoring Template (master)
- `template_name`, `cadence` (Select: Per Shift / Per Hari), `enabled`.
- Batas waktu:
  - Per Shift: `due_minutes_after_open` (Int).
  - Per Hari: `due_time` (Time).
- Child `checkpoints` (POS Monitoring Template Checkpoint): `checkpoint`, `required`
  (Check). Urutan baris = urutan tampil.
- Child `companies` (POS Monitoring Template Company): `company`. Kalau kosong,
  template tidak berlaku di mana pun (aman secara default).
- Child opsional `shift_types` (Link Shift Type) dan `pos_profiles` (Link POS Profile) untuk mempersempit Per Shift (§9.2).
- Validasi: checkpoint tidak boleh duplikat; `max_count >= min_count`; minimal satu
  checkpoint.

Template boleh diubah kapan saja. Report menyimpan snapshot (§4.4), jadi perubahan
hanya berlaku untuk kewajiban baru. Karena itu tidak ada DocType versi.

### 4.4 POS Monitoring Report (submittable)

Header:
- `template`, `cadence`, `company`, `pos_profile` (kosong untuk Per Hari),
  `opening_shift` (Link POS Opening Shift, hanya Per Shift), `operational_date`.
- `due_at`, `submitted_at`, `submitted_by`, `timeliness` (On Time / Late).
- `status` (Select: Draft / Submitted / Reviewed / Missed / Waived),
  `obligation_key` (Data, unik bersama `revision`), `revision` (Int, mulai 1),
  `correction_of` (Link ke Report sebelumnya).
- Field penilaian HQ (permlevel 1, `allow_on_submit`): `review_result`
  (Pass / Fail, diturunkan), `correction_requested` (Check), `review_note`,
  `reviewed_by`, `reviewed_at`, `waive_reason`.

Child `checkpoints` (POS Monitoring Report Checkpoint) adalah snapshot dari template
saat dibuat:
- `checkpoint`, `checkpoint_name`, `category`, `instruction`, `media_type`,
  `min_count`, `max_count`, `video_max_seconds`, `required`, `pass_label`, `fail_label`, `na_label` (label sudah di-resolve saat snapshot).
- `evidence_complete` (Check, dihitung server).
- `assessment` (permlevel 1, `allow_on_submit`; Select: Pending / Pass / Fail / N/A,
  default Pending) dan `assessment_note` (permlevel 1, `allow_on_submit`).

Child `media` (POS Monitoring Report Media):
- `checkpoint_row` (nama baris checkpoint), `file_url` (private), `thumbnail_url`
  (private, opsional), `media_type` (Photo / Video), `file_size`, `uploaded_at`,
  `purged` (Check, untuk retensi §9).

`obligation_key`:
- Per Shift: `<template>|shift|<opening_shift>`
- Per Hari: `<template>|day|<company>|<operational_date>`

Index unik `(obligation_key, revision)` dibuat lewat hook controller native `on_doctype_update` (`frappe.db.add_unique`). Index ini mencegah kewajiban ganda ketika pembuatan lazy dan
cron berjalan bersamaan.

## 5. Siklus kewajiban

### 5.1 Pembuatan
`ensure_obligations(pos_profile=None)` bersifat idempoten dan dipanggil dari dua
tempat:
1. **Lazy**, saat kasir membuka menu/badge (`get_my_tasks`). Hanya untuk profile
   aktif kasir tersebut.
2. **Cron `*/5`** yang sudah ada (`hooks.py:259-264`, tambah satu entri). Berjalan
   untuk semua shift berstatus Open dan semua company target.

Aturannya:
- Per Shift: setiap POS Opening Shift ber-`status` Open dengan company yang menjadi
  target template mendapat satu Report.
  - `operational_date` = `posting_date` shift.
  - `due_at` = `period_start_date` + `due_minutes_after_open`.
- Per Hari: setiap company target mendapat satu Report per tanggal site.
  - `due_at` = tanggal + `due_time`.
  - Tidak dibuat ulang saat shift berganti.
  - Kasir dari profile mana pun di company itu boleh mengisinya.

Controller POS Opening Shift dan POS Closing Shift **tidak diubah**. Alasan: tidak
ada `doc_events` untuk shift; alur buka dan tutup shift sensitif.

### 5.2 Status

```
Draft --submit--> Submitted --finalize_review--> Reviewed
  |                   |
  |                   +--waive (reviewer)--> Waived
  +--jendela habis (cron)--> Missed --waive--> Waived
Reviewed + correction_requested --> kasir buat revisi (Draft baru, revision+1)
```

Jendela habis:
- Per Shift: shift ditutup, atau `due_at` sudah lewat `shift_missed_grace_hours` (§9).
- Per Hari: H+1 pukul `day_missed_next_day_time`.

Sebelum jendela habis, kasir masih bisa submit setelah `due_at`; hasilnya
`timeliness = Late`.

Revisi: Report lama tetap utuh. Report baru menyalin snapshot checkpoint, **tanpa**
media dan penilaian.

## 6. API (whitelisted, `pos_next/api/outlet_monitoring.py`)

Semua endpoint menurunkan company dan warehouse dari server. Parameter `company`
dari klien tidak pernah dipercaya.

| Endpoint | Pemanggil | Isi |
|---|---|---|
| `get_my_tasks(pos_profile)` | kasir | `_assert_profile_access` (`api/packages.py:61`); `ensure_obligations`; daftar Draft (Per Shift untuk shift terbuka profile itu, dan Per Hari untuk company-nya) + jumlah untuk badge |
| `get_history(pos_profile, start, page_length)` | kasir | Report non-Draft milik company profile itu, terbaru dulu, termasuk hasil HQ |
| `get_report(name)` | kasir | detail + media; cek `has_permission` |
| `register_media(report, checkpoint_row, file_name, thumbnail_file_name=None)` | kasir | validasi §7.2; tambah baris media; hitung ulang `evidence_complete` |
| `remove_media(report, media_row)` | kasir | hanya saat Draft; hapus baris dan File |
| `submit_report(name)` | kasir | semua checkpoint `required` harus lengkap; set `submitted_*` dan `timeliness`; submit |
| `create_revision(name)` | kasir | hanya bila Reviewed + `correction_requested` |
| `get_shift_tasks(opening_shift)` | kasir | kewajiban Per Shift belum disubmit, untuk peringatan tutup shift |
| `finalize_review(name)` | reviewer | semua baris sudah dinilai; Fail/N/A wajib catatan; turunkan `review_result` (ada Fail = Fail); kunci baris dengan `for_update` agar dua reviewer tidak bisa menyelesaikan bersamaan |
| `waive(name, reason)` | reviewer | untuk Missed/Submitted; alasan wajib |
| `get_gallery(filters, start, page_length=40)` | reviewer | §8.2 |

Kasir tidak memakai Desk (`desk_guard`, `hooks.py:307`). Semua aksi kasir lewat
endpoint di atas.

## 7. Media

### 7.1 Alur upload (satu jalur untuk foto dan video)
1. SPA memilih file lewat `<input type="file" accept="image/*|video/*"
   capture="environment">`. Atribut `capture` baru dipakai di repo ini, jadi
   perilakunya perlu diuji di tablet iMin.
2. Foto: selalu di-encode ulang ke JPEG oleh `compressImage` (`utils/compressImage.js`) menghasilkan versi utama
   (maxEdge 1600) dan thumbnail (maxEdge 400). **Bila kompresi gagal, upload
   ditolak dengan pesan.** Fallback kirim-file-asli tidak dipakai karena EXIF/GPS
   dan HEIC akan ikut terkirim.
3. Video:
   - SPA membaca `duration` lewat elemen `<video>` (metadata) dan menolak bila
     melebihi `video_max_seconds` (checkpoint override atau settings).
   - SPA mencoba membuat poster frame lewat canvas sebagai thumbnail (best effort).
     Bila gagal, tampilkan placeholder ikon play.
4. Upload multipart ke `/api/method/upload_file` dengan `is_private=1`,
   `doctype=POS Monitoring Report`, `docname`. Polanya sama dengan
   `StockOpnameDialog.vue:1900-1912`. Frappe meminta izin write ke Report; ini
   didapat dari `has_permission` saat Draft.
5. `register_media` dipanggil dengan nama File.
6. Preview langsung memakai objectURL (pola `PurchaseAttachments.vue:170-175`),
   dengan label "Belum terunggah" sampai langkah 5 berhasil.

Base64 JSON (pola opname) **tidak** dipakai di sini, karena video akan memakan
memori dan ukuran bertambah 33%.

### 7.2 Validasi server di `register_media`
- File harus `attached_to_doctype/name` = Report ini dan `is_private = 1`.
- Report berstatus Draft, dan pemanggil punya izin write.
- Jenis media harus cocok dengan `media_type` checkpoint. Jumlah media tidak boleh
  melebihi `max_count`.
- Whitelist dicek lewat **magic bytes**, bukan hanya ekstensi:
  - JPEG `FF D8 FF`
  - MP4 `....ftyp` (brand selain `qt  `)
  - WebM `1A 45 DF A3`
- HEIC dan MOV ditolak di v1, karena Chrome di Desk tidak bisa menampilkannya.
- Ukuran: foto <= `photo_max_mb`, video <= `video_max_mb`; format sesuai flag allow_* (§9).
- Bila validasi gagal, File dihapus dan muncul error yang jelas.

Shortcut: durasi video hanya dicek di klien karena server tidak punya ffmpeg. Ukuran
file tetap dicek di server sebagai batas keras.

### 7.3 Akses
- Semua File private menempel ke Report. Frappe `File.has_permission` lalu
  menyerahkan izinnya ke Report (diverifikasi di image 16.52).
- `has_permission` dan `permission_query_conditions` untuk Report didaftarkan di
  `hooks.py:152-158`, mengikuti pola Stock Reconciliation:
  - kasir hanya Report dari company tempat ia menjadi POS Profile User;
  - reviewer sesuai `get_permitted_companies`.
- **Tidak** ada endpoint proxy media dan **tidak** dipakai `make_thumbnail`, karena
  fungsi itu menulis ke folder public.
- Batas yang diketahui:
  - Owner File selalu bisa membaca file-nya (perilaku Frappe).
  - List File di Desk memperlihatkan nama file ke Desk user yang boleh membaca
    doctype Report.

## 8. UI

### 8.1 Kasir (SPA)
- Satu item `outlet_monitoring` di `MANAGEMENT_MENU` (`managementMenu.js`), grup
  `system` atau `sales` (ditentukan saat plan), `requiresOnline`.
  - View disematkan di rantai `v-if` `POSMenuDialog.vue:119-230`.
  - Komponen baru: `OutletMonitoringDialog.vue`, mengikuti pola
    `StockOpnameDialog.vue` (view list/doc, `DialogHost`, `call` dari
    `utils/apiWrapper`).
- Tab di dalam view: **Tugas Saya** dan **Riwayat**. Tab "Draft & Sinkronisasi"
  dihapus karena v1 online saja. Draft server sudah muncul di Tugas Saya.
- Tugas Saya:
  - Kartu dikelompokkan Per Shift / Harian.
  - Kartu menampilkan due time, sisa checkpoint wajib, dan tag Terlambat.
  - Aksi utama: "Isi Laporan".
  - Empty state: "Tidak ada tugas".
- Detail laporan:
  - Header read-only (company, profile/shift, tanggal, due, progres).
  - Per checkpoint: instruksi, foto contoh, aturan jumlah, tombol ambil
    foto/video, thumbnail/player, tanda centang "Bukti lengkap".
  - Warna Pass baru muncul setelah dinilai HQ.
  - Submit aktif bila semua checkpoint wajib lengkap.
- Riwayat:
  - Status label: Menunggu Penilaian / Sesuai SOP / Tidak Sesuai SOP /
    Perlu Perbaikan / Terlewat / Dikecualikan.
  - Catatan HQ ditampilkan.
  - Tombol "Buat Revisi" bila diminta HQ.
- Badge:
  - Jumlah kewajiban Draft yang dapat diisi, pada item menu dan tombol menu header.
  - Menambah badge ke item menu adalah perubahan aditif di `POSMenuDialog.vue:54-94`
    (pola `POSHeader.vue:118-121`).
  - Badge diperbarui saat menu dibuka, setelah buka shift, dan lewat event realtime
    `outlet_monitoring_updated` via `publish_to_profile` (`realtime_events.py:15`).
- Peringatan tutup shift:
  - Blok peringatan tambahan di `ShiftClosingDialog.vue` (pola `:36-52`), dimuat di
    `loadClosingData`.
  - Isinya kewajiban Per Shift yang belum disubmit.
  - **Tidak memblokir** penutupan shift.
- UI mengikuti `docs/UI_GUIDELINES.md`:
  - `statusTone`/`StatusBadge`, `useToast`, `FeatherIcon` (tanpa emoji).
  - Target sentuh 44/40 px.
  - Semua string lewat `__()` dengan baris di `id.csv` (pertahankan CRLF, cek dengan
    `scripts/validate_id_csv.py`).

### 8.2 HQ (Desk)
- **Form Report native:**
  - Reviewer mengisi `assessment` dan `assessment_note` per baris, lalu menekan
    tombol "Selesaikan Penilaian", yang memanggil `finalize_review`.
  - Tombol "Kecualikan" memanggil `waive`.
  - Preview media di form lewat HTML field yang merender `<img>`/`<video>` dari
    `file_url` private.
- **Desk Page `outlet_monitoring_gallery`:**
  - Polanya mengikuti `page/hq_sales_monitoring/` (`make_app_page`,
    `page.add_field`, `frappe.call`).
  - Filter: Company (dalam scope), POS Profile, Periode (Today, Yesterday,
    This Week, Last Week, This Month, Last Month, Custom; berdasarkan
    `operational_date`), Category, Checkpoint, Penilaian, Jenis Media.
  - Grid kartu:
    - Isi kartu: thumbnail (`<img loading="lazy">` dari `thumbnail_url`, fallback
      `file_url`), pill penilaian, nama checkpoint, company, tanggal/jam, pelapor.
    - Video di grid hanya tampil sebagai poster/placeholder; grid **tidak pernah**
      memuat `<video>`.
    - Paginasi server-side dengan tombol "Muat lagi".
  - Klik kartu membuka `frappe.ui.Dialog`: foto ukuran penuh atau
    `<video controls preload="metadata">`, metadata, dan link ke form Report untuk
    menilai.
  - `get_gallery` memfilter lewat scope company yang sama dengan
    `permission_query_conditions`. Satu Report dengan 3 media menghasilkan 3 kartu.
- **Workspace POSNext:**
  - Satu kartu "Outlet Monitoring" berisi link ke Gallery, Report, Template,
    Checkpoint, Category.
  - Butuh patch reinstall workspace (pola `patches/v1_7_0/reinstall_workspace.py`).

## 9. Pengaturan (semua yang relevan dapat dikonfigurasi)

Prinsip:
- Nilai **operasional** bisa diatur dari UI.
- Hal yang menyangkut **keamanan** dibatasi oleh server, dan pengaturan hanya bisa
  mempersempitnya, tidak memperluas. Contoh: format file hanya bisa dipilih dari
  daftar aman; batas ukuran dibatasi oleh `max_file_size` Frappe.
- Klien membaca konfigurasi dari respons `get_my_tasks`. Server tetap memvalidasi
  ulang semuanya.

### 9.1 POS Monitoring Settings (Single baru, pola `POS Reject Settings`)
Izin: read/write untuk System Manager dan `POSNext Monitoring Reviewer`.

| Field | Tipe | Default | Fungsi |
|---|---|---|---|
| `enabled` | Check | 0 | Saklar utama: menu, cron, endpoint kasir |
| **Penilaian** | | | |
| `default_pass_label` / `default_fail_label` / `default_na_label` | Data | Sesuai SOP / Tidak Sesuai SOP / Tidak Berlaku | Label bawaan; Category boleh override |
| `allow_na` | Check | 1 | N/A boleh dipilih reviewer |
| `require_note_on_fail` / `require_note_on_na` | Check | 1 / 1 | Catatan wajib |
| `prevent_self_review` | Check | 1 | Reviewer tidak boleh menilai laporan yang ia submit |
| `allow_correction` | Check | 1 | Reviewer boleh meminta revisi |
| `max_revisions` | Int | 3 | Batas rantai revisi |
| **Waktu** | | | |
| `allow_late_submission` | Check | 1 | Submit setelah due masih diterima (Late) |
| `shift_missed_grace_hours` | Int | 12 | Per Shift: Missed setelah due + N jam (atau shift ditutup) |
| `day_missed_next_day_time` | Time | 02:00 | Per Hari: Missed pada H+1 jam ini |
| **Foto** | | | |
| `photo_max_edge` / `thumbnail_max_edge` | Int | 1600 / 400 | Ukuran kompresi di klien |
| `photo_quality` | Percent | 80 | Kualitas JPEG |
| `photo_max_mb` | Float | 5 | Batas server |
| **Video** | | | |
| `video_max_mb` | Float | 20 | Batas server (<= `max_file_size` site) |
| `video_max_seconds` | Int | 30 | Dicek di klien |
| `allow_webm` | Check | 0 | MP4 selalu diizinkan; MOV/HEVC tidak. Frappe menolak WebM untuk Website User (`handler.py` ALLOWED_MIMETYPES); kasir System User lolos |
| **Retensi** | | | |
| `media_retention_days` | Int | 90 | 0 = selamanya; job harian menghapus File, Report tetap; hanya Report berstatus Reviewed/Missed/Waived |
| **Pengingat & notifikasi** | | | |
| `show_menu_badge` | Check | 1 | Badge jumlah tugas |
| `warn_on_shift_close` | Check | 1 | Blok peringatan di ShiftClosingDialog (tidak memblokir) |
| `notify_reviewers_on_submit` | Check | 1 | Notification Log ke reviewer dalam scope |
| `notify_reporter_on_review` | Check | 1 | Notification Log + event realtime ke kasir |
| **Gallery** | | | |
| `gallery_page_size` | Int | 40 | Kartu per halaman (dibatasi 10..200) |
| `gallery_default_period` | Select | Today | Periode default |

Validasi server di `POS Monitoring Settings.validate`: angka harus positif dan
`video_max_mb` <= `max_file_size` site; `thumbnail_max_edge` < `photo_max_edge`.

### 9.2 Konfigurasi per master
- **Category:** label pass/fail/N/A (kosong = default settings).
- **Checkpoint:** `media_type`, `min_count`/`max_count`, `instruction`,
  `reference_image`, serta override opsional `video_max_seconds` (0 = pakai
  settings).
- **Template:**
  - cadence, due, `enabled`;
  - company target;
  - opsional `shift_types` (child Link Shift Type) untuk Per Shift. Bila diisi,
    hanya shift yang `shift_slot`-nya cocok yang mendapat kewajiban; bila kosong,
    semua shift. Batasan: `shift_slot` hanya terisi sekitar 40%;
  - opsional `pos_profiles` (child) untuk mempersempit Per Shift ke profile tertentu.

Pilot per outlet: isi company target di Template.

## 10. Uji penerimaan

Backend (fix stack, `_pn_run_tests.py`):
1. `ensure_obligations` dipanggil dua kali, atau lazy dan cron bersamaan, tetap
   menghasilkan 1 Report per kunci.
2. Per Hari tidak terduplikasi saat shift berganti. Per Shift mengikuti shift baru.
3. Kasir profile A tidak bisa membaca Report atau File company B
   (`frappe.has_permission`, `get_list`, dan GET `/private/files/...`).
4. Reviewer dengan User Permission company X tidak melihat company Y di form, list,
   maupun gallery.
5. `register_media` menolak:
   - ekstensi .jpg dengan isi bukan JPEG;
   - HEIC dan MOV;
   - ukuran berlebih;
   - jenis yang salah untuk checkpoint;
   - jumlah melebihi `max_count`;
   - File yang menempel ke dokumen lain.
6. Submit ditolak bila ada checkpoint wajib yang belum lengkap. Hasilnya `Late` bila
   lewat `due_at`.
7. Kasir tidak bisa mengubah field permlevel 1. `finalize_review` menolak baris yang
   masih Pending, dan menolak Fail/N/A tanpa catatan. Dua finalize bersamaan hanya
   satu yang berhasil.
8. Revisi hanya bisa dibuat bila diminta. Report lama tidak berubah.
9. Cron menandai Missed setelah jendela habis. Waive wajib menyertakan alasan.
10. Retensi menghapus File lama tanpa menghapus Report.
11. Flag off: tidak ada kewajiban baru, endpoint kasir menolak.

Frontend (Vitest): badge menghitung kewajiban, bukan checkpoint. Centang bukti
lengkap tidak berarti Pass. Submit nonaktif sampai lengkap. Kompresi gagal berarti
upload ditolak. Video yang terlalu panjang ditolak. Item menu tersembunyi saat
offline.

Regresi: checkout, pembayaran, print, buka shift dan tutup shift tetap berjalan.
Peringatan di tutup shift tidak memblokir.

Bukti di dev (setelah backup, rebuild, migrate): screenshot menu kasir, Tugas Saya,
pengisian checkpoint foto + video di tablet, Riwayat, peringatan tutup shift,
Workspace, Gallery, dialog detail, dan form penilaian. Disimpan di
`docs/proofs/`.

## 11. Ditunda (bukan v1)

- Offline: draft dan antrean media lokal, di database Dexie terpisah dari
  `pos_next_offline`.
- Modal reminder di safe point, snooze dan eskalasi. Prasyaratnya: flag global
  "sedang mencetak" diangkat keluar dari `POSSale.vue:3750`.
- Hard gate penutupan shift.
- Template Version/hash, Reminder Policy/Log, Exception DocType, dashboard KPI dan
  skor berbobot.
- Poster video dari server (butuh ffmpeg), dukungan HEIC/MOV.
- Penilaian "Needs Review" sebagai status akhir; AI klasifikasi.

## 12. Risiko & belum diverifikasi

- Batas upload dan range request video di **Frappe Cloud**: belum diverifikasi;
  wajib dicek sebelum rilis prod.
- Format rekaman kamera tablet iMin (diasumsikan MP4/H.264) dan atribut `capture`:
  belum diuji di device.
- Pertumbuhan storage. Contoh: 14 laporan/hari x 1 video 20 MB sudah sekitar
  280 MB/hari. Retensi wajib aktif.
- Angka volume dari data dev bercampur shift QA.
- Tes backend butuh stack `erpnext-fix` dibuat ulang (saat ini kontainernya
  dihapus, volume tetap ada).

## 13. Default

Semua angka di §9 bisa diubah dari UI. Default awal: retensi 90 hari, video 20 MB /
30 detik, Missed Per Shift = due + 12 jam, Missed Per Hari = H+1 02:00.
Reviewer ikut mengelola master data.
