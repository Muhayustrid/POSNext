# Audit Handoff "POSNext Outlet Monitoring v2.0" terhadap repo

Tanggal: 2026-11-05
Status: audit selesai; menunggu keputusan user (bagian 6) sebelum spec v1 ditulis
Sumber yang diaudit: `POSNext_Outlet_Monitoring_Agent_Handoff_v2.0/` (PRD, design lock, API, QA)
Basis kode: `origin/main` 2c25ee49; Frappe di image Docker 16.52.0

Dokumen ini membandingkan handoff dengan kode nyata dan praktik Frappe v16.
Setiap klaim tentang repo disertai file:baris. Klaim yang belum terbukti
ditandai **BELUM DIVERIFIKASI**.

## 1. Ringkasan

Arah produk handoff **masuk akal dan sejalan**: satu menu kasir, HQ di Desk
native, foto private, penilaian per checkpoint terpisah dari kelengkapan bukti,
revisi tidak menimpa. Tetapi handoff ditulis tanpa melihat repo, sehingga:

- 6 asumsi teknis **bertentangan** dengan kode/framework (bagian 3);
- ~15 DocType + ~25 endpoint + reminder engine + antrean offline media adalah
  **over-engineering** untuk volume nyata (bagian 4);
- ada beberapa risiko yang **tidak disebut** handoff (bagian 5).

Volume nyata (data dev = restore prod, 30 hari terakhir): 123 opening shift,
6 company aktif, 8 POS Profile aktif, rata-rata 2,3 shift per company per hari
(maks 8), 0 profile dengan dua shift terbuka sekaligus. Per shift = ~14
laporan/hari, ~50 foto/hari. Data dev memuat shift QA, angka nyata bisa lebih
kecil. Beban review HQ manual masih realistis.

## 2. Yang sudah sejalan (pakai apa adanya)

| Kebutuhan handoff | Titik integrasi nyata |
|---|---|
| Satu entri menu kasir | Registry `POS/src/components/pos/managementMenu.js:15`; view disematkan via rantai `v-if` di `POSMenuDialog.vue:119-230`; filter `requires*` di `POSMenuDialog.vue:291-302` |
| Pola list -> detail di dalam menu | `StockOpnameDialog.vue:933` (`view = list/doc`), `RejectRequestDialog.vue:1209` |
| Upload foto private | `compressImage` (`POS/src/utils/compressImage.js:12`) + base64 JSON -> `pos_next/api/stock_opname.py:907` (`attach_opname_row_photo`, `is_private: 1`, whitelist ekstensi `:903`, batas 10 MB `:920`) |
| Tampilan foto private | `<img :src="file_url">` langsung (`StockOpnameDialog.vue:430`); Frappe melayani `/private/files` dengan cek izin (bagian 3.3) |
| Status pill, toast, empty state | `POS/src/utils/statusTone.js`, `components/common/StatusBadge.vue`, `composables/useToast.js:54`; aturan `docs/UI_GUIDELINES.md` |
| Desk Page HQ | `pos_next/pos_next/page/hq_sales_monitoring/` (`make_app_page`, `page.add_field`, `frappe.call`) dan `page/outlet_targets/` |
| Scope company HQ | `pos_next/hq_scope.py` (`get_permitted_companies:19`, `resolve_company_scope:34` menolak company palsu) |
| Akses kasir ke POS Profile | `pos_next/api/packages.py:61` `_assert_profile_access` (dipakai stock_opname & reject_request) |
| Cashier tidak bisa masuk Desk | `utils/desk_guard.block_desk_for_cashiers` (hooks.py:307) |
| Scheduler | cron `*/5` sudah ada (`pos_next/hooks.py:259-264`) |
| Realtime ke outlet | `pos_next/realtime_events.py:15` `publish_to_profile`, room `pos_profile:<name>`, `after_commit` |
| Peringatan tutup shift | `ShiftClosingDialog.vue` sudah punya blok peringatan (`:36-52`, `:172-190`, `:753-760`) |
| Feature gate per outlet | `POS Settings` per POS Profile + `POS Next Global Settings` single, resolver `api/settings_resolver.py:90` |

## 3. Asumsi handoff yang bertentangan dengan repo/framework

### 3.1 Model shift
Handoff berasumsi "HRMS Shift Type + shift instance" dari scheduler.
Nyata: shift POSNext = **POS Opening Shift** (submittable), **satu shift
terbuka per POS Profile** (`pos_opening_shift.py:81`
`validate_single_open_shift_per_profile`; `api/shifts.py` mengunci baris
profile). `shift_slot` hanya Data read-only (`pos_opening_shift.json:101`),
berasal dari Shift Group (`POS Profile Group`) yang slotnya Link ke Shift Type
nama saja; hanya 51 dari 123 shift 30 hari terakhir yang punya slot. Tidak ada
integrasi HRMS Shift Assignment/Employee.

Akibat: "shift instance" = nama POS Opening Shift. Tidak perlu mesin penjadwal
shift sendiri. Kewajiban Per Shift dibuat untuk opening shift yang Open; Per Hari
untuk company + tanggal. Tidak ada `doc_events` untuk shift di hooks.py; hindari
menyentuh controller shift: buat kewajiban **lazy** (saat kasir membuka menu /
polling badge) + cron `*/5` untuk shift Open yang belum punya kewajiban.

### 3.2 Outlet = Company
`patches/v2_35_0/outlets_by_company.py`: "Outlets are companies". Satu company
bisa punya beberapa POS Profile. Handoff benar untuk tidak menyamakan Company =
POS Profile: Per Hari = per company, Per Shift = per opening shift (per profile).

### 3.3 Keamanan media (handoff meminta proxy/signed URL)
Frappe 16.52 `File.has_permission` (`frappe/core/doctype/file/file.py:1023-1055`
di image): Administrator lolos; file private boleh dibaca **owner**; selain itu
izin baca/tulis **dokumen tempat file menempel** (`attached_to_doctype/name`)
menentukan. Child table didelegasikan ke parent. Jadi cukup:
file private menempel ke dokumen Report + `has_permission` dan
`permission_query_conditions` pada Report (preseden: Stock Reconciliation,
`hooks.py:145-158`, `overrides/stock_reconciliation.py:369/379`). Endpoint proxy
media sendiri tidak perlu dan menambah permukaan serangan.

Catatan: daftar File di Desk untuk Desk user memfilter per doctype yang bisa
dibaca, bukan per dokumen (`file.py:1023-1037` versi lokal) -> HQ reviewer bisa
melihat **nama** file monitoring company lain di List File. Kecil, dicatat.

### 3.4 Thumbnail (handoff: thumbnail private dibuat server)
`File.make_thumbnail` **selalu menulis ke folder public**
(`file.py:580-581` di image). Dilarang dipakai untuk bukti private.
Alternatif tanpa dependensi baru: SPA membuat dua versi saat upload dengan
`compressImage` yang sudah ada (mis. maxEdge 1600 dan 400), keduanya File
private menempel ke Report. Gallery memuat versi kecil dengan `loading="lazy"`.

### 3.5 Upload & validasi media
- Frappe tidak melakukan MIME sniffing untuk Desk user; repo juga tidak
  (hanya ekstensi). Praktik baik: cek magic bytes JPEG/PNG/WebP di server
  (beberapa baris, tanpa library).
- **HEIC**: whitelist opname mengizinkan `.heic/.heif`, tetapi browser Desk
  (Chrome) tidak bisa menampilkan HEIC -> gallery rusak. Monitoring harus
  menerima hasil re-encode JPEG/WebP saja.
- `compressImage` mengembalikan file asli bila gagal (`compressImage.js`), yang
  berarti EXIF/GPS ikut terkirim dan format bisa HEIC. Untuk monitoring:
  gagal kompres = tolak dengan pesan, bukan kirim asli.
- Upload preseden memakai base64 dalam JSON (+33% ukuran, seluruh file di
  memori). Layak untuk foto 1600px (~300 KB); **tidak layak untuk video**.
  Video butuh multipart `/api/method/upload_file` (butuh izin write ke Report,
  `frappe/handler.py:174`).
- Batas default Frappe 25 MB (`frappe/core/api/file.py:84-90`); batas Frappe
  Cloud **BELUM DIVERIFIKASI**.
- Range request (HTTP 206) untuk video berfungsi lewat werkzeug
  `send_file(conditional=True)`; perilaku nginx X-Accel di Frappe Cloud
  **BELUM DIVERIFIKASI**.

### 3.6 Offline
Semua menu sejenis di repo online-only (`requiresOnline`, `managementMenu.js`).
Dexie `pos_next_offline` (`POS/src/utils/offline/db.js:37`) memakai versi
berbasis hash skema: menambah tabel ke `CURRENT_SCHEMA` men-*bump* versi DB yang
juga menyimpan `invoice_queue`. Tidak ada preseden menyimpan Blob; ekspor antrean
lewat `JSON.stringify` (`db.js:180-205`) tidak tahan Blob. Jika offline dibangun
kelak: **database Dexie terpisah** (bukan tabel baru di `pos_next_offline`).

### 3.7 Peran HQ
Repo punya tiga set "HQ" yang berbeda: `stock_opname_setup.py:31`,
`api/hq_monitoring.py:97`, `utils/authz.py:8`. `POSNext Manager` dihitung HQ di
hq_monitoring tetapi **outlet** di stock opname (`stock_opname_setup.py:30`).
Handoff menyebut "HQ Reviewer/Admin" tanpa pemetaan. Wajib role baru khusus,
mis. `POSNext Monitoring Reviewer`; konfigurasi oleh System Manager. Crew =
`POSNext Cashier`.

### 3.8 Reminder modal "safe point"
Tidak ada flag global "sedang mencetak": `activePrintCount` lokal di
`POSSale.vue:3750`. Sinyal yang ada: `uiStore.isAnyDialogOpen`
(`stores/posUI.js:48`), `cartStore.isSubmitting` (`stores/posCart.js:2366`).
Modal yang aman butuh mengangkat state print keluar dari POSSale = menyentuh
alur checkout/print. Risiko regresi kasir nyata.

### 3.9 Badge di menu
Item menu tidak mendukung badge (`POSMenuDialog.vue:54-94`). Preseden badge ada
di header (`POSHeader.vue:118-121`). Menambah badge = perubahan kecil aditif di
render item + tombol menu header; perlu disebut eksplisit di spec.

### 3.10 Workspace
Mengedit `workspace/posnext/posnext.json` saja tidak memperbarui workspace yang
sudah ada di site; perlu patch reinstall (preseden
`patches/v1_7_0/reinstall_workspace.py`).

## 4. Over-engineering: usulan penyederhanaan

| Handoff | Usulan v1 | Alasan |
|---|---|---|
| Category, Checkpoint | Tetap | Inti fitur |
| Template + Template Version (hash, publish) | Template + child checkpoint; **tanpa** DocType versi | Snapshot teks/aturan checkpoint disalin ke baris Report saat dibuat; perubahan template otomatis hanya berlaku ke depan |
| Schedule + 3 child target | Field di Template: cadence (Per Shift/Per Hari), child company target, batas waktu (menit setelah shift dibuka / jam tetap) | Satu template = satu jadwal cukup untuk 6-8 outlet |
| Assignment terpisah | Report draft dibuat server = kewajiban | Satu dokumen, satu status; `obligation_key` + revisi unik |
| Evidence DocType | Child table foto di Report (baris checkpoint + file_url) | File menempel ke Report -> izin native; gallery = query child table |
| Review + Review Item submittable | Field HQ di Report: per baris `assessment` (permlevel 1, allow_on_submit), header `review_decision`, `reviewed_by`, `reviewed_at` | Konflik dua reviewer ditangani native oleh cek `modified` Frappe (TimestampMismatchError) |
| Reminder Policy + Stage + Log + snooze | Badge + banner; peringatan di ShiftClosingDialog | Modal safe-point = fase 2 (3.8) |
| Exception/Waiver DocType | Status `Waived` + alasan wajib di Report, hanya reviewer | Jejak audit via Version log native |
| Outlet Monitoring Settings single | Flag `enable_outlet_monitoring` di POS Settings (resolver per profile) | Pilot per outlet gratis; pola repo |
| Timezone setting sendiri | Timezone site | Satu sumber waktu; jebakan TZ site sudah dikenal |
| Dashboard 9 KPI | Kolom status di List native + filter; Script Report bila diminta | |
| Offline media, video, AI | Fase berikutnya | 3.5, 3.6 |

Hasil: ~4 DocType + 3 child (Category, Checkpoint, Template [+Template
Checkpoint, +Template Company], Report [+Report Checkpoint, +Report Photo]),
~8 endpoint, 1 Desk Page Gallery, 1 view SPA.

## 5. Risiko yang tidak disebut handoff

1. **Pertumbuhan storage**: ~50 foto x ~300 KB (+thumbnail) = ~17 MB/hari,
   ~6 GB/tahun. Perlu kebijakan retensi file (hapus File lama, simpan Report).
2. **Inkonsistensi internal handoff**: nama endpoint berbeda antara PRD §8 dan
   `API_CONTRACT.md` (`get_my_assignments` vs `list_assignments`,
   `finalize_review` vs `submit_review`); status timeliness `Excused` vs
   `Waived`; decision `Rejected` tidak didefinisikan; setting
   `prevent_modal_during_payment` yang "selalu true" tidak bermakna.
3. **`Needs Review` sebagai hasil akhir HQ** membingungkan; usul v1:
   Pass/Fail/N/A (+Pending default).
4. **Owner selalu bisa membaca file-nya** (3.3): kasir lain di outlet yang sama
   perlu izin baca Report untuk melihat foto rekan; diatur lewat
   `has_permission` Report berbasis POS Profile User.
5. **Tes backend** butuh stack `erpnext-fix` yang sedang dihapus (PROJECT_STATE);
   harus dibuat ulang sebelum implementasi.

## 6. Keputusan user (2026-11-05)

1. **Online saja** di v1 (offline = fase berikutnya, Dexie terpisah).
2. **Foto + video** di v1. Konsekuensi yang wajib masuk spec v1:
   - video lewat multipart `upload_file` ke Report draft (bukan base64);
   - batas durasi/ukuran video dikonfigurasi, default konservatif;
   - tanpa poster server (tidak ada ffmpeg): placeholder ikon play di gallery;
   - whitelist + magic bytes MP4/WebM di server;
   - verifikasi batas upload dan range request di Frappe Cloud sebelum rilis;
   - storage tumbuh jauh lebih cepat -> retensi file wajib di v1.
3. **Semua laporan dinilai** HQ (Pending -> Pass/Fail/N/A per checkpoint).
4. Cadence **Per Shift + Per Hari**.

Langkah berikutnya: spec v1 (brainstorming -> spec -> plan -> TDD).
