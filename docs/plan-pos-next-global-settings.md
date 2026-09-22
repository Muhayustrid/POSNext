# Rencana: Pisahkan Pengaturan Global vs Per POS Profile (opsi B, varian A)

> **Status**: RECHECK SELESAI 22 Sep 2026. Arah B (doctype Single + POS Settings per-profil
> bersih) disetujui user 22 Sep; varian final dikunci user 22 Sep: **5 field global pindah
> semua ke single** (termasuk `allow_negative_stock`). Dokumen ini adalah versi terkoreksi
> hasil recheck terhadap codebase (semua fakta diverifikasi ulang, file:line dirujuk).
> **Eksekusi kode DITAHAN sampai user memulai**; tidak ada commit/push tanpa perintah
> eksplisit.
>
> Repo: `~/ERPNext-Project/development/frappe-bench/apps/pos_next` (branch main).
> Bench hidup di container Docker `erpnext16_dev-frappe-1` (path in-container
> `/workspace/development/frappe-bench`). Situs uji: `roti-posnext-test.localhost`
> (http://localhost:8001). Situs kedua: `posnext.localhost` (**migrate-nya RUSAK
> pre-existing** — "No module named production_app"; jangan andalkan situs ini).

## 1. Latar & masalah

POS Settings milik pos_next adalah doctype **non-single, satu baris per POS Profile**
(duluan di-reclaim dari ERPNext oleh `install.py::reclaim_pos_settings_doctype`; ERPNext
core sendiri menyimpan Single `POS Settings` module Accounts berisi hanya
`invoice_fields`/`pos_search_fields`). Di dalamnya tercampur dua kelas pengaturan:

- **Global** (5 field; nilainya disinkronkan paksa ke semua baris oleh controller, atau
  dibaca dari "baris mana pun / baris enabled pertama").
- **Per POS Profile** (100 field: enabled, harga beli PO, allow backdate, printer, struk,
  shift, dsb.) — mayoritas.

Akibat campur aduk ini user bingung: untuk mengubah pengaturan global (mis. basis target)
harus membuka baris profil tertentu, dan nilainya "tidur" duplikat di semua baris. Arah
yang disetujui: satu doctype Single untuk global, POS Settings per-profil tetap tapi
bersih. Alternatif yang DITOLAK: (A) sekadar tab visual di form lama — N baris tetap
memegang nilai global, keanehan tetap; (C) satu form Single + child table per profil —
rombakan terlalu besar.

## 2. Fakta lapangan (terverifikasi ulang 22 Sep 2026, termasuk sweep menyeluruh)

- Total field POS Settings: **105** (`pos_next/pos_next/doctype/pos_settings/pos_settings.json`).
  Doctype non-single (`issingle` tidak diset).
- 5 field global + buktinya:
  1. `invoice_type` — sync antar-baris: `sync_invoice_type()`
     (`pos_settings.py:50`); dibaca global: `invoice_type.py:25`
     (`frappe.db.get_value("POS Settings", {}, "invoice_type")`), cache `frappe.local`,
     fallback `POS Invoice`.
  2. `monthly_target_basis` dan 3. `overall_target_basis` — `sync_target_bases()`
     (`pos_settings.py:65`); dibaca: `target_basis.py:38`. (Fitur basis target landed di
     commit `a039b59`+`c7a48a9`; default "Net Sales".)
  4. `allow_negative_stock` — `sync_negative_stock_setting()` (`pos_settings.py:81`)
     jembatan ke Stock Settings core. **CATATAN PENTING: field ini TIDAK murni global** —
     juga dibaca per-profil di jalur penjualan: `api/invoices.py:612` (`_should_block`) dan
     di-prefetch (nilainya tak terpakai) di `api/invoices.py:975-980`; masuk payload SPA via
     `api/constants.py:33` (`POS_SETTINGS_FIELDS`) dan dipakai store/computed SPA. Keputusan
     user: **tetap pindah ke single**, semua konsumen ditangani (lihat §3.4).
  5. `allowed_locales` — dibaca dari baris enabled pertama: `api/localization.py:55-79`
     (`get_allowed_locales_from_settings`); child doctype `POS Allowed Locale`
     (`pos_allowed_locale.json`, 1 field `language` Link Language).
- Posisi 5 field di form (penting untuk pembersihan): `invoice_type` + 2 basis ada di
  **paling atas form** (sebelum section apa pun); `allowed_locales` di section
  **"Localization"** (section hanya berisi field ini, jadi section break-nya ikut dihapus);
  `allow_negative_stock` di section **"Miscellaneous"** (idx ~94).
- Konsumen global (selain getter) yang terverifikasi lewat sweep menyeluruh:
  - Payload bootstrap/SPA: `api/bootstrap.py:212,232,238` (inject `invoice_type` dari
    getter), `api/constants.py` (`POS_SETTINGS_FIELDS`/`DEFAULT_POS_SETTINGS` memuat
    `allow_negative_stock`), `api/pos_profile.py:97-99` (`get_pos_settings_for_profile`),
    `pos_settings.py:148-169` (`get_pos_settings`).
  - Jalur invoice: `api/invoices.py:602-616` (`_should_block`), `api/invoices.py:975-980`
    (prefetch cache).
  - SPA: hanya `allow_negative_stock` yang dipakai dari 5 field
    (`POS/src/stores/posSettings.js:78,196,317,344,352`,
    `POS/src/components/settings/POSSettings.vue:303,1702,1845-2028`,
    `POS/src/stores/posEvents.js:228`, `POS/src/pages/POSSale.vue:1371-1377`);
    `PaymentDialog.vue` memakai `invoice_type` dari payload (sudah di-inject dari getter,
    aman); `useLocale.js:61` lewat RPC `get_allowed_locales` (aman). Key
    `_global_allow_negative_stock` **tidak dipakai frontend** (bisa dibuang).
  - Test: `test_invoice_type.py:27-37`, `tests/_posi_test_utils.py:38` (helper
    `_set_invoice_type`, dipakai `POSInvoiceModeMixin` → `tests/test_pos_invoice_reports.py:160`,
    `tests/test_pos_invoice_closing.py:24,27`), `api/test_pos_invoice_submit.py:37-49`
    (helper independen), `tests/test_hq_monitoring.py:1300-1335` (`TestTargetBasis`).
  - Skrip GUI-test (untracked): `gui-test-screenshots/basis_zerocost_probe.py:42`,
    `gui-test-screenshots/basis_gui_verify.py:77,83` (tulis/baca field basis langsung ke
    semua baris POS Settings).
- **Tidak ada** Custom Field/Property Setter nempel di POS Settings (DB dua situs bersih);
  folder `POS/public` (non-asset), `www/`, `templates/`, `custom/`, `fixtures/`, `config/`
  bersih dari referensi 5 field.
- Mekanika migrate (terverifikasi di source Frappe v16): mengubah JSON doctype →
  `import_file_by_path` → `import_doc` → delete+insert DocType (anak DocField/DocPerm
  ikut) → field yang dihapus **hilang dari form/meta**, kolom DB **tetap ada (inert,
  tidak di-drop)**. Konsekuensi: (a) data lama aman; (b) **permissions doctype baru WAJIB
  ada di JSON** (DocPerm dibuat ulang dari JSON saat re-import).
- Patch terbaru v2_10_0 (reindex) aman terhadap perubahan ini; tidak perlu patch reindex
  baru (re-import penuh mengikuti urutan JSON).
- Data situs uji `roti-posnext-test.localhost`: 8 baris POS Settings semuanya sepakat
  (`POS Invoice` / `Net Sales` / `Net Sales` / negative stock off); `tabPOS Allowed Locale`
  **kosong** (getter pakai fallback default {en, id}).

## 3. Desain yang disetujui (varian A: 5 field pindah)

### 3.1 Doctype Single baru: "POS Next Global Settings"

- File: `pos_next/pos_next/doctype/pos_next_global_settings/` (`__init__.py`,
  `pos_next_global_settings.json`, `.py`, `.js`). Pola mengikuti single existing
  `brainwise_branding`.
- `issingle: 1`, **module `"POS Next"`** (case persis seperti `modules.txt`; jangan
  "Pos Next"). Route otomatis: `/app/pos-next-global-settings`.
- **Permissions (WAJIB, mengikuti pola POS Settings/brainwise_branding)**: System Manager
  full; Sales Manager, Nexus POS Manager, Sales User read-only.
- Field (definisi disalin verbatim dari pos_settings.json; deskripsi `allowed_locales`
  dikoreksi "defaults to English and Arabic" menjadi "defaults to English and Indonesian"
  karena default sebenarnya {en, id}):
  | field | tipe | section |
  |---|---|---|
  | `invoice_type` | Select `Sales Invoice\nPOS Invoice`, default POS Invoice, label "Invoice Type Created via POS Screen" | "Invoice & Target" |
  | `monthly_target_basis` | Select `Net Sales\nGross Profit\nNet Profit`, default Net Sales, label "Monthly Target Basis" | "Invoice & Target" |
  | `overall_target_basis` | idem, label "Overall (Payback) Target Basis" | "Invoice & Target" |
  | `allow_negative_stock` | Check, default 0, label "Allow Negative Stock" | "Negative Stock" |
  | `allowed_locales` | Table MultiSelect → `POS Allowed Locale`, label "Allowed Languages" | "Localization" |
- Controller `.py`:
  - `validate`: panggil `validate_invoice_type_change(self)` (dari `pos_next/invoice_type.py`,
    kontrak tak berubah; guard shift-terbuka/offline pending/GL tetap jalan) +
    `validate_target_bases(self)` (dari `pos_next/target_basis.py`). Keduanya menerima
    `doc`, jalan apa adanya pada single (`get_doc_before_save()` bekerja di single).
  - `on_update`: bridge `allow_negative_stock` → Stock Settings **versi sederhana**:
    bandingkan dengan nilai Stock Settings saat ini (`frappe.db.get_single_value`);
    nyalakan/matikan langsung via `frappe.db.set_single_value("Stock Settings",
    "allow_negative_stock", ...)` + `frappe.msgprint` (dibungkus `_()`); **hapus logika
    "hitung baris lain"** (tak ada baris lain di single).
- `.js`: pindahkan `confirm_target_basis_change` + baseline `__target_basis_prev` dari
  `pos_settings.js` (konfirmasi saat basis berubah; cancel mengembalikan nilai semula).

### 3.2 POS Settings (per-profil) dibersihkan

- `pos_settings.json`: hapus 5 field + `section_break_localization` dari `fields` DAN
  `field_order`.
- `pos_settings.py`: buang `sync_invoice_type`, `sync_target_bases`,
  `sync_negative_stock_setting`, dan method `on_update` (tak ada sisa pekerjaan); buang
  import `validate_invoice_type_change`/`validate_target_bases` dari file ini. Validasi
  lain (max_discount, search_limit, use_exact_amount) dan fungsi
  `get_pos_settings`/`update_pos_settings`/`create_default_settings` tidak berubah.
- Di `get_pos_settings` (`pos_settings.py:156`): ganti injeksi
  `_global_allow_negative_stock` dengan
  `settings["allow_negative_stock"] = frappe.db.get_single_value("POS Next Global Settings", "allow_negative_stock")`
  (pola sama seperti injeksi `invoice_type` baris 167).
- `pos_settings.js`: buang handler `monthly_target_basis`/`overall_target_basis`,
  `confirm_target_basis_change`, baseline `__target_basis_prev` (sudah pindah).
- Kolom DB lama di `tabPOS Settings` dibiarkan (inert). Patch reorder v2_7_0–v2_10_0
  tetap aman.

### 3.3 Getter ganti sumber (kontrak output TIDAK berubah)

- `pos_next/invoice_type.py::get_pos_invoice_doctype()` →
  `frappe.db.get_single_value("POS Next Global Settings", "invoice_type") or POS_INVOICE`;
  cache `frappe.local` & fallback tetap; docstring diperbarui.
- `pos_next/target_basis.py::get_target_basis(which)` → idem per field (`get_single_value`,
  fallback Net Sales); docstring diperbarui.
- `pos_next/api/localization.py::get_allowed_locales_from_settings()` → baca child rows
  `allowed_locales` dari single; hapus logika "baris enabled pertama"; fallback `{"en","id"}`
  tetap. Nama fungsi & return (set) tidak berubah → `change_user_language` (baris 112) dan
  `get_allowed_locales` (baris 51) aman.

### 3.4 Konsumen payload & jalur invoice (`allow_negative_stock` pindah)

- `api/constants.py`: buang `allow_negative_stock` dari `POS_SETTINGS_FIELDS` (baris 33)
  dan `DEFAULT_POS_SETTINGS` (baris 66).
- `api/bootstrap.py::_get_pos_settings`: setelah query, inject
  `settings["allow_negative_stock"] = <nilai dari single>` (pola sama seperti baris 232
  untuk `invoice_type`). **Key payload tetap ada** → store/computed SPA tidak berubah.
- `api/pos_profile.py::get_pos_settings_for_profile`: injeksi yang sama.
- `api/invoices.py`:
  - `_should_block` (602-625): buang cabang per-profil (baris 609-616); cek global Stock
    Settings di awal tetap.
  - Baris 975-980: buang `FIELD_ALLOW_NEGATIVE_STOCK` dari daftar pre-fetch (nilainya
    tidak pernah dibaca).
  - Baris 33: buang konstanta `FIELD_ALLOW_NEGATIVE_STOCK`.

### 3.5 Migrasi nilai (sekali jalan)

- Patch baru **`pos_next/patches/v2_11_0/copy_global_settings_to_single.py`** (CATATAN:
  path yang benar `pos_next/patches/v2_11_0/`, bukan `pos_next/pos_next/patches/...` seperti
  draf lama); daftarkan di `pos_next/patches.txt` seksi `[post_model_sync]` (**file
  patches.txt tidak berakhir newline — edit via tool, jangan append shell `>>`**):
  1. Buat/ambil dokumen single "POS Next Global Settings".
  2. Salin `invoice_type` + kedua basis dari baris mana pun
     (`frappe.db.get_value("POS Settings", {}, fieldname)`).
  3. `allow_negative_stock`: dari baris mana pun; fallback ke nilai Stock Settings bila
     tidak ada baris.
  4. `allowed_locales`: salin child rows dari baris `enabled=1` pertama (meniru cara baca
     lama).
  5. Idempoten: field single yang sudah terisi tidak ditimpa.
- Setelah migrate + clear-cache, halaman/SPA membaca sumber baru.

### 3.6 SPA (perubahan minimal; key payload dipertahankan)

- `POS/src/components/settings/POSSettings.vue` (stock tab): checkbox
  `allow_negative_stock` → tampilan read-only (checkbox disabled atau baris teks) dengan
  keterangan dikelola di "Pengaturan Global" (Desk); buang field itu dari payload save;
  buang tracking `originalAllowNegativeStock` + logika reload-on-change (baris ~1845-2028).
- `stores/posSettings.js`, `stores/posCart.js`, `components/sale/ItemsSelector.vue`,
  `pages/POSSale.vue`, `stores/posEvents.js`: **tidak berubah** (semua membaca key payload
  yang tetap ada).
- Rebuild bundle di host: `npm --prefix POS run build` (output ke
  `pos_next/public/pos/assets/`); JANGAN `bench build` di container dev.

### 3.7 Test yang disesuaikan

- `test_invoice_type.py` + `tests/_posi_test_utils.py` (`POSInvoiceModeMixin`, dipakai
  `test_pos_invoice_reports.py` & `test_pos_invoice_closing.py`) +
  `api/test_pos_invoice_submit.py` (helper independen): `_set_invoice_type` → set single,
  bukan baris POS Settings.
- `tests/test_hq_monitoring.py::TestTargetBasis` (baris ~1300-1335): set single + asersi
  `get_target_basis`; hapus asersi sync antar-baris; validasi nilai invalid tetap diuji.
- Tambah 1 test kecil untuk single: simpan single → Stock Settings ikut nyala/mati
  (bridge).
- Test per-profil lain (gate backdate `allow_change_posting_date`, `po_default_price_list`,
  dsb.) TIDAK boleh tersentuh/berubah hasilnya.

### 3.8 Sidebar, workspace, terjemahan

- `pos_next/workspace_sidebar/posnext.json`: tambah link "Global Settings" di seksi
  Settings (dekat link "POS Settings").
- `pos_next/pos_next/workspace/posnext/posnext.json`: tambah link yang sama.
- Sinkron ke situs: `docker exec ... python apps/pos_next/pos_next/_pn_sync_ws.py roti-posnext-test.localhost`
  (dan posnext.localhost bila memungkinkan). DUA JEBAKAN: (1) on_trash Workspace/Sidebar +
  `developer_mode` DI-PROSES menghapus file app — matikan developer_mode in-process sebelum
  sync; (2) row DB yang pernah diedit via UI jadi `standard:0` dan tak tertimpa file —
  bila perlu hapus row lalu `sync_for("pos_next", force=1)` + clear-cache.
- `pos_next/translations/id.csv` (tambah yang belum ada; format `"english","indonesia",""`):
  - `"Global Settings","Pengaturan Global"`
  - `"Invoice & Target","Faktur & Target"`
  - `"Invoice Type Created via POS Screen","Tipe Faktur Dibuat via Layar POS"`
  - `"Allowed Languages","Bahasa yang Diizinkan"`
  - `"Stock Settings 'Allow Negative Stock' has been automatically enabled.","Stok Negatif pada Stock Settings otomatis dinyalakan."`
  - `"Stock Settings 'Allow Negative Stock' has been automatically disabled.","Stok Negatif pada Stock Settings otomatis dimatikan."`
  - Cek/tambah `"Localization"` bila belum ada.
  - Sudah ada (jangan diubah): "Monthly Target Basis", "Overall (Payback) Target Basis",
    "Allow Negative Stock", "Existing target numbers are reinterpreted on the new basis,
    not migrated. Keep this change?"

### 3.9 Skrip GUI-test yang menulis field langsung

- `gui-test-screenshots/basis_zerocost_probe.py:42` dan
  `gui-test-screenshots/basis_gui_verify.py:77,83` (untracked): ubah target tulis/baca dari
  baris POS Settings ke single.

## 4. Prosedur verifikasi (urutan wajib)

1. **Backend**: `docker exec -w /workspace/development/frappe-bench -e PN_SITE=roti-posnext-test.localhost erpnext16_dev-frappe-1 env/bin/python apps/pos_next/pos_next/_pn_run_tests.py <modul>` — serial saja, JANGAN `bench run-tests` (mati di bootstrap). Modul: `test_invoice_type`, `tests.test_hq_monitoring`, `api.test_pos_invoice_submit`, `tests.test_pos_invoice_reports`, `tests.test_pos_invoice_closing`, test doctype baru.
2. **Migrate + cache**: `docker exec -w /workspace/development/frappe-bench erpnext16_dev-frappe-1 bench --site roti-posnext-test.localhost migrate` lalu `bench --site roti-posnext-test.localhost clear-cache` (terjemahan baru ikut butuh clear-cache). Jalankan migrate dua kali untuk bukti idempoten patch v2_11_0.
3. **Restart serve** bila backend .py diubah saat server hidup: proses `bench serve --port 8000 --noreload` di container melayani kode basi — kill PID lalu jalankan ulang detached dari `sites/`:
   `docker exec -d erpnext16_dev-frappe-1 bash -c "cd /workspace/development/frappe-bench/sites && ../env/bin/python -m frappe.utils.bench_helper frappe --site roti-posnext-test.localhost serve --port 8000 --noreload > /tmp/web-pos.log 2>&1"`
4. **Frontend statis**: `npm --prefix POS run test:run`; syntax `node --check` (untuk page JS yang mengandung jinja: `grep -v '{% include' <file> | node --check /dev/stdin`); build bundle (`npm --prefix POS run build`).
5. **GUI (playwright, host)**: context `service_workers="block"` (SW menyimpan JS basi), login `/login` (`#login_email`/`#login_password`/`.btn-login`), tunggu ±3s. QA user: `hq-monitor-qa@posnext.local` / `HqMonQa!2026x` (role System Manager; enable/disable via `bench --site roti-posnext-test.localhost execute frappe.db.set_value --args '["User","<email>","enabled",1]'` dalam try/finally). Cek:
   - sidebar menampilkan "Global Settings", form single terbuka dan 5 field ada;
   - ubah `monthly_target_basis` → "Gross Profit" → halaman `/app/hq-sales-monitoring` (preset month) label/angka hero & tabel berubah basis → kembalikan "Net Sales";
   - form POS Settings lama TIDAK lagi memuat 5 field global;
   - SPA settings (stock tab): `allow_negative_stock` tampil read-only, tidak mengubah nilai per-profil;
   - 0 `pageerror` di semua halaman; screenshot disimpan di `gui-test-screenshots/`.
   - **Password Administrator tidak diketahui** — jangan asumsikan kredensial lain.

## 5. Deployment (nanti, produksi Frappe Cloud)

1. `git pull` + `bench migrate` (**WAJIB** — doctype baru + patch v2_11_0) + `bench build` (di CI/host yang cukup memori; **JANGAN bench build di container dev — OOM membunuh MariaDB**) + clear-cache.
2. Sinkron sidebar/workspace di produksi (`_pn_sync_ws.py` atau migrate dengan `modified` file dinaikkan — Desk membaca workspace dari DB).
3. Cek nilai hasil migrasi single vs nilai lama di beberapa baris POS Settings.

## 6. Aturan keras sesi eksekusi (dari user & memori proyek)

1. **JANGAN `git commit`/`git push` tanpa perintah eksplisit user di sesi berjalan.** Selesaikan + verifikasi, LAPORKAN, tunggu perintah.
2. JANGAN `bench run-tests`; WAJIB runner `_pn_run_tests.py` seperti di §4.1, serial.
3. JANGAN `bench build` di container dev.
4. Tanpa em dash pada string UI baru; terjemahan tambah ke `pos_next/translations/id.csv` + clear-cache.
5. Patch `patches.txt`: edit, jangan append shell (tanpa trailing newline).
6. Bahasa kerja user: Indonesia.

## 7. Catatan perilaku (bukan bug, perlu disadari)

1. Setelah pindah, mematikan Stock Settings langsung dari ERPNext tidak lagi di-override oleh checkbox per-profil (dulu bisa via `_should_block`); sekarang satu sumber: single → Stock Settings.
2. Jendela singkat deploy code→migrate: getter membaca single yang belum ada → fallback default (`POS Invoice`/`Net Sales`/`{en,id}`); mitigate dengan migrate tepat setelah pull.
3. Kolom DB 5 field lama di `tabPOS Settings` tetap ada (inert, tidak di-drop); nilai historis tidak hilang.
4. Desk mendapat dua pintu di seksi Settings sidebar: "Global Settings" (single) + "POS Settings" (per-profil). Ini realisasi "tab general vs tab pos profil" tanpa masalah otoritas nilai.

## 8. Checklist acceptance

- [ ] Doctype Single "POS Next Global Settings" ada (module "POS Next", permissions lengkap), 5 field + validasi jalan.
- [ ] `get_pos_invoice_doctype` / `get_target_basis` / `get_allowed_locales` membaca single; nama fungsi & output kontrak sama.
- [ ] POS Settings kehilangan 5 field global (+ section Localization kosong); perilaku per-profil tak berubah.
- [ ] Payload bootstrap/`get_pos_settings`/`get_pos_settings_for_profile` mengirim `allow_negative_stock` dari single; SPA store/validasi stok berfungsi tanpa perubahan.
- [ ] Patch migrasi idempoten (migrate dua kali); nilai lama tersalin benar (cek 1-2 outlet).
- [ ] Sidebar + workspace menampilkan "Global Settings" (dua situs dev bila memungkinkan); terjemahan id.csv lengkap.
- [ ] Semua test backend hijau (modul terkait + regression hq monitoring).
- [ ] `npm --prefix POS run test:run` hijau; bundle di-build; GUI pass tanpa page error; basis switch terverifikasi E2E dan dikembalikan ke "Net Sales".
- [ ] TIDAK ada commit/push yang dilakukan tanpa perintah user.
