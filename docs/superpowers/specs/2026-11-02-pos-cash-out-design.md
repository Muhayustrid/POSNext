# Pembelian Lokal dan Pengeluaran Outlet (POS Spend) — Desain

Tanggal: 2026-11-02
Status: disetujui user 2026-11-02. Belum ada implementasi.
Baseline: `local/production-products-opname` @ 4c4cd68d.
Nama file tetap `pos-cash-out` sesuai permintaan; nama fitur dan doctype di bawah
memakai "Spend" (lihat §11).

## 1. Tujuan

Kasir/manager outlet mencatat pembelian di luar PO dan pengeluaran operasional
langsung dari SPA POSNext. Hasilnya dokumen native ERPNext (Purchase Invoice,
Journal Entry, Payment Entry) dengan GL dan Stock Ledger yang benar per company.
Jika uangnya keluar dari laci yang direkonsiliasi shift, closing shift ikut
berkurang tepat satu kali.

Kriteria sukses:
- Satu UI dan satu riwayat di POSNext, dokumen di belakangnya bisa PI, JE, atau PE.
- Tidak ada akun, kategori, company, atau aturan dokumen yang di-hardcode; semua
  dari template HQ dan konfigurasi native.
- Akun selalu milik company transaksi dan lolos validasi tipe; konfigurasi yang
  tidak lengkap ditolak dengan pesan jelas, tidak pernah fallback diam-diam.
- Kas yang diharapkan saat closing = kas versi lama − (keluar − masuk) pada akun
  sumber laci untuk dokumen yang ditandai ke shift itu. Tidak ada GL ganda,
  tidak ada hitungan ganda saat retry, cancel, atau koreksi.
- Closing yang sudah submit tidak pernah berubah diam-diam.

Di luar cakupan: offline (fitur wajib online), aset tetap, mata uang selain mata
uang company, uang muka/kasbon karyawan.

## 2. Bukti dasar

### 2.1 Codebase POSNext
- Shift memakai doctype POSNext `POS Opening Shift` / `POS Closing Shift`.
  Closing tidak membuat GL. Kas diharapkan dibangun di
  `make_closing_shift_from_opening` (`pos_closing_shift.py:773`): opening +
  pembayaran invoice − kembalian + PE Receive (`get_payments_entries`,
  `pos_closing_shift.py:591`). `_submit_closing` menghitung ulang di server dan
  mengunci opening shift `FOR UPDATE` (`pos_closing_shift.py:909`).
- Serah-terima kas (`get_cash_handover`, `api/shifts.py:40`) dan Backdate Entry
  (`_submit_closing` lalu `_move_handover`) memakai `expected_amount` closing,
  jadi otomatis ikut bila closing benar.
- Slot "Total Expense" sudah ada tetapi kosong: `pos_closing_print.py:391`
  (hardcode 0), `sales_recap.py:537` (`expense: None`), `receipt_text.js:478`,
  `SessionSummary.vue:640`, `salesRecap.js:210`.
- Pola yang dipakai ulang: scoping company/profile PO/PR
  (`_purchase_company_options`, `_scoped_company`, `_purchase_profile_for` di
  `api/purchase_orders.py`), lampiran (`attach_purchase_files`,
  `ATTACHMENT_MAX_BYTES`, `compressImage`, `PurchaseAttachments.vue`), otorisasi
  (`utils/authz.py`), menu (`managementMenu.js`, `POSMenuDialog.vue`), pesan error
  (`error_reason`, `humanizeError`).

### 2.2 Source ERPNext v16
- PI `is_paid=1`: `cash_bank_account` wajib, GL Dr item / Cr `credit_to`, lalu
  Dr `credit_to` / Cr kas di dokumen yang sama (`purchase_invoice.py:939-1698`).
  `update_stock=1` membuat SLE. Supplier dan `credit_to` wajib.
- Akun item non-stok: Item Default per company → Item Group default → brand →
  default company (`get_item_details.py:483-597`).
- JE: `validate_stock_accounts` (`journal_entry.py:360`) **hanya** jalan bila
  perpetual inventory aktif, dan hanya menolak akun stok yang saldonya sedang
  sama dengan nilai stok. Jadi JE tidak boleh diandalkan untuk menolak akun
  stok; POSNext harus menolaknya sendiri.
- Party hanya wajib untuk akun Payable/Receivable di JE (`journal_entry.py:597`).
- Sinkronisasi akun induk→anak (`account.py:251-442`): akun pada company anak
  hanya boleh dibuat bila `allow_account_creation_against_child_company`; selain
  itu harus dibuat di root company lalu disalin ke semua turunan. Pencocokan
  parent dan akun anak memakai `account_name` (+ `account_number`), dan
  melempar error bila satu turunan tidak punya parent yang cocok.
- `make_debit_note` (`purchase_invoice.py:2084`), `make_reverse_journal_entry`
  (`journal_entry.py:1779`), `get_payment_entry` (`payment_entry.py:2885`).

### 2.3 Uji native di dev (2026-11-02, company TRAINING, satu transaksi, rollback)
Backup dev diambil dulu. Semua dokumen di-rollback; dicek 0 PI `PI-TRNG-261102%`
dan 0 JE probe tersisa.

| Uji | Hasil |
|---|---|
| Buying Settings | `po_required=No`, `pr_required=No`: PI tanpa PO/PR diizinkan, tidak perlu diubah |
| Accounts Settings | `check_supplier_invoice_uniqueness=0`, `unlink_payment_on_cancellation_of_invoice=1` |
| PI stok, `is_paid=1`, `update_stock=1` | Submit OK, `Paid`, SLE +2 senilai 50.000. Di TRAINING debit jatuh ke `5150.001 HPP Penjualan` karena **perpetual inventory TRAINING = 0** (outlet riil = 1, debit ke persediaan) |
| Debit Note dari PI itu | `make_debit_note` menyalin `is_paid=1`, `update_stock=1`, `paid_amount=-50000`, akun kas sama: GL membalik kas (Dr kas), SLE −2 |
| Cancel PI stok | GL dan SLE dibalik, `is_cancelled=1` |
| PI non-stok + `expense_account` eksplisit | Debit tepat `5230.001 - Biaya PLN Kantor - TRNG` |
| PI non-stok tanpa akun | ERPNext memilih `5150.001 HPP Penjualan` (default company). **Fallback Item Default bisa salah diam-diam** |
| PI bayar nanti + PE dari `get_payment_entry` | PI tanpa baris kas; PE Dr hutang / Cr kas; PI `Paid`. Cancel PE: outstanding kembali 50.000 |
| JE Cash Entry + reverse | Dr beban / Cr kas; reverse JE punya `reversal_of`, GL terbalik |
| JE debit akun persediaan | Diterima (TRAINING non-perpetual) — konfirmasi butir 2.2 |

Temuan konfigurasi dev yang memengaruhi desain:
- `Lokal Supplier` tidak punya Party Account, jadi `credit_to` jatuh ke
  `default_payable_account`. TRAINING memakai `2141.000 Hutang Pajak` sebagai
  hutang default (salah konfigurasi data, bukan bug kode).
- Mode `Cash` di TRAINING dipetakan ke `1111.001 Kas Kecil`, di outlet riil ke
  `1111.003 Kas Kasir`. Tipe MoP tidak bisa dipakai untuk menebak laci.
- Nama akun bernomor sama berbeda antar-outlet (`5230.001` = "Biaya Listrik" di
  75 outlet, "Biaya PLN Kantor" di 97). Pencocokan berbasis nama tidak aman.
- Template pajak per company ada (`Purchase Tax - TRNG`).

## 3. Keputusan arsitektur

Hybrid, dokumen native, satu doctype konfigurasi, tanpa doctype transaksi.

| Kasus (ditentukan template) | Dokumen | GL | SLE |
|---|---|---|---|
| Beli barang stok, bayar langsung | PI `is_paid=1`, `update_stock=1` | Dr persediaan gudang / Cr sumber bayar | Ya |
| Beli non-stok/perlengkapan, bayar langsung | PI `is_paid=1` | Dr akun debit (§5) / Cr sumber bayar | Tidak |
| Tagihan supplier dibayar nanti | PI `is_paid=0` (+ PE saat dibayar, §8) | Dr persediaan/beban / Cr hutang supplier | Bila stok |
| Beban langsung tanpa supplier/hutang | JE `Cash Entry` (`Bank Entry` bila sumber bank) | Dr akun debit / Cr sumber bayar | Tidak |

Alasan:
- Material Receipt ditolak: tanpa supplier, pajak, dan sumber bayar; kredit kas
  lewat akun selisih menyalahgunakan akun penyesuaian stok.
- Expense Claim ditolak: Employee wajib dan modulnya belum dipakai.
- Doctype transaksi custom ditolak: data dobel dan risiko cancel tidak sinkron.
  Riwayat digabung dengan query atas field penanda (§10).
- JE untuk beban tunai tanpa hutang lebih jujur daripada PI dengan supplier
  generik dan hutang yang langsung nol. PI dipakai bila ada barang, tagihan
  supplier, pajak masukan, atau pembayaran tertunda.

## 4. Konfigurasi

### 4.1 Sumber pembayaran = Mode of Payment native
Kas Kasir, Kas Kecil, Kas Outlet, Bank dibuat sebagai Mode of Payment, masing-
masing dengan baris `Mode of Payment Account` per company. Akun aktual selalu
`get_bank_cash_account(mop, company)`. Tidak ada tabel akun sumber baru.

Validasi saat transaksi: akun hasil harus milik company, `is_group=0`, tidak
`disabled`, `account_type` ∈ {Cash, Bank}, mata uang = mata uang company. JE
memakai `Cash Entry` untuk akun Cash dan `Bank Entry` (cheque_no/date diisi
nomor nota/tanggal) untuk akun Bank.

### 4.2 Sumber yang masuk perhitungan shift (eksplisit per POS Profile)
Tabel baru `posa_shift_spend_sources` (child `POS Shift Spend Source`:
`mode_of_payment`) di **POS Settings** per profile. Tidak ada fallback global:
kosong berarti tidak ada sumber yang mengurangi kas shift.

Validasi saat POS Settings disimpan: setiap MoP harus ada di metode bayar POS
Profile, dan punya akun Cash/Bank untuk company profile.

Saat transaksi, sumber dianggap "laci shift" hanya bila MoP ada di tabel ini
**dan** user punya opening shift terbuka di profile itu. Akun aktual yang
dikredit dicap di dokumen (`posa_shift_account`), sehingga closing menghitung
akun yang benar-benar dipakai, bukan akun hasil konfigurasi yang mungkin sudah
berubah.

Sumber lain (Kas Kecil, Bank, dll.) tetap boleh dipakai tetapi tidak dicap ke
shift dan tidak menyentuh closing.

### 4.3 `POS Spend Template` (doctype konfigurasi, dikelola HQ)
Header:
- `title`, `category` (Data, untuk pengelompokan UI), `enabled`
- `document_type`: Purchase Invoice | Journal Entry
- `payment_timing`: Bayar sekarang | Bayar nanti (Bayar nanti hanya untuk PI)
- `update_stock` (hanya PI)
- `debit_account_strategy`: Item Default | Template Account (§5)
- `reference_debit_account` (Link Account, akun milik company referensi HQ;
  wajib bila strategi Template Account atau JE)
- `default_supplier`, `allow_supplier_change`, `require_vendor_name`
- `apply_purchase_tax` (hanya PI)
- `requires_attachment`, `max_amount`, `over_limit_policy` (§9)

Child tables:
- `items` (`POS Spend Template Item`): `item_code` atau `item_group` yang boleh
  dipilih (PI).
- `payment_sources` (`POS Spend Template Payment Source`): `mode_of_payment`,
  `is_default`.
- `companies` (`POS Spend Template Company`): `company` (boleh company grup =
  semua turunannya), `debit_account` override, `purchase_taxes_template`,
  `cost_center` override. Semua Link divalidasi milik company baris itu.
  Company grup hanya untuk cakupan; override hanya boleh di company leaf.
- `roles` (`POS Spend Template Role`): role yang boleh memakai. Kosong =
  POSNext Cashier + POSNext Manager.

Validasi saat template disimpan:
- JE: tanpa `items`, tanpa pajak, `payment_timing` = Bayar sekarang, strategi
  Template Account.
- `update_stock=1`: strategi Item Default (akun persediaan ditentukan ERPNext),
  item yang diizinkan wajib stock item.
- Bayar nanti: supplier tidak boleh supplier generik yang ditandai
  `posa_is_walk_in_supplier` (custom field Check di Supplier). Hutang harus jelas
  ke siapa.
- `reference_debit_account`: `root_type` Expense (atau Asset non-stok untuk
  perlengkapan bila diizinkan), `account_type` bukan Stock, Payable,
  Receivable, Cash, Bank, Tax.

## 5. Strategi akun debit

`debit_account_strategy` menentukan sumber kebenaran; tidak ada fallback silang.

**Template Account** (default untuk JE dan PI beban):
1. Override `companies.debit_account` untuk company itu.
2. Jika tidak ada: resolusi `reference_debit_account` ke company itu (§6).
3. Gagal = tolak. Item Default **tidak** pernah dipakai. Akun diset eksplisit ke
   setiap baris PI (`expense_account`) atau baris debit JE.

**Item Default** (PI barang):
- Stock item + `update_stock`: ERPNext memilih akun persediaan gudang. POSNext
  tidak mengisi akun.
- Non-stok: akun harus berasal dari Item Default atau Item Group Default
  **untuk company itu**. Jika hasilnya hanya fallback default company (bukti
  §2.3: jatuh ke HPP Penjualan), transaksi ditolak dan item dilaporkan "belum
  siap".

## 6. Resolusi akun lintas company

Struktur company (keputusan user 2026-11-02, dicek di dev):
- `JURI GROUP` = root polos, tidak dipakai.
- `OUTLET` (grup, anak `JURI GROUP`) = induk 172 outlet dan **company referensi
  CoA**. Punya 214 akun, 211 bernomor, contoh `5230.001 - Biaya Listrik -
  OUTLET`, `1111.003 - Kas Kasir - OUTLET`.
- `PT. JUARA ROTI INDONESIA` = pabrik, di luar cakupan default (bisa dimasukkan
  eksplisit lewat tabel `companies`).

`reference_debit_account` wajib akun milik `OUTLET` atau, umumnya, milik company
yang dipilih sebagai referensi template. Usul: tidak dibatasi ke `OUTLET` di
kode; field `reference_company` di template, default dari setting global
`spend_reference_company` (diisi `OUTLET`). Tidak ada nama company di kode.

Resolusi ke company target:
1. Cari `Account` dengan `company` = target, `account_number` = nomor referensi,
   `is_group=0`.
2. Wajib tepat satu hasil (nomor akun unik per company, `account.py:384`), lalu
   **diblokir** bila:
   - `root_type` berbeda dengan referensi, atau `account_type` berbeda;
   - `disabled=1` atau `freeze_account=Yes`;
   - `account_currency` ≠ mata uang company target.
3. Nomor akun induk **tidak memblokir**, hanya muncul sebagai peringatan di cek
   kesiapan. Bukti dev: `5230.001` ada di bawah `5220.000` di 37 outlet dan di
   bawah `5230.000` di 134 outlet. Pemblokiran berdasarkan induk akan mematikan
   sebagian besar outlet tanpa alasan akuntansi; root/account type sudah menjamin
   sifat akun.
4. Nama akun tidak dipakai untuk mencocokkan (nama `5230.001` = "Biaya Listrik"
   atau "Biaya PLN Kantor"), hanya ditampilkan.
5. Referensi tanpa `account_number` = tidak bisa diresolusi lintas company;
   setiap company lain wajib punya override.
6. Tidak ditemukan = Tidak siap. Bukti dev: `RS PKU MUHAMMADIYAH DELANGGU` tidak
   punya `5230.001`, jadi butuh override atau akun baru.

Override per company selalu menang dan divalidasi dengan aturan butir 2,
kecuali kecocokan nomor.

### 6.1 Cek kesiapan
Tombol "Check Readiness" di template dan endpoint
`get_spend_template_readiness(template)`. Hasilnya tabel per company dalam
cakupan, berisi:
- akun debit hasil resolusi;
- akun tiap sumber bayar;
- `credit_to` hasil resolusi (Party Account supplier → default payable company,
  wajib `account_type=Payable`);
- template pajak (bila `apply_purchase_tax`);
- cost center;
- perpetual inventory (bila `update_stock`);
- untuk Item Default: akun per item yang diizinkan.

Status per company: Siap / Tidak siap + alasan. Saat transaksi, aturan yang sama
dijalankan untuk satu company sehingga hasil cek dan hasil transaksi identik.
Laporan Desk ringkas "Spend Template Readiness" (template × company) untuk
seluruh outlet.

### 6.2 Penyediaan akun yang belum ada
Sinkronisasi native **tidak bisa dipakai** dari `OUTLET`:
- `validate_root_company_and_sync_account_to_children` (`account.py:251`) hanya
  menyalin ke turunan bila company **tidak punya ancestor**. Ancestor dicari lewat
  `get_root_company`, yaitu company paling atas (`account.py:651`).
- `OUTLET` punya ancestor `JURI GROUP` dan `allow_account_creation_against_child_company=1`.
  Jadi akun baru di `OUTLET` disimpan tanpa disalin ke 172 outlet.
- Sinkronisasi dari `JURI GROUP` akan menyalin ke **semua** turunan, termasuk
  pabrik. Penyalinan juga gagal bila satu turunan tidak punya parent dengan
  `account_name` sama. Ditolak.

Keputusan:
- v1 tidak membuat akun otomatis. Cek kesiapan menunjukkan outlet yang kurang;
  akun dibuat manual atau diisi override. 170 dari 172 outlet sudah boleh
  membuat akun sendiri (`allow_account_creation_against_child_company=1`).
- Tahap 3: aksi "Provision missing accounts" untuk manager akun. Aksi ini
  membuat salinan akun referensi di outlet yang kurang, dengan aturan:
  - parent dipilih dengan nomor induk referensi; bila tidak ada di outlet itu,
    outlet dilewati dan dilaporkan;
  - nomor akun yang sudah terpakai tidak pernah ditimpa;
  - dijalankan per outlet dalam transaksi masing-masing, dengan laporan hasil;
  - `flags.ignore_root_company_validation` dipakai persis seperti penyalinan
    native ERPNext (`account.py:427`), hanya untuk company dalam cakupan template.

### 6.3 Pajak
Hanya PI. Mapping eksplisit `companies.purchase_taxes_template` per company,
divalidasi `template.company == company`. Tidak ada pencocokan judul. Nota tanpa
faktur pajak: `apply_purchase_tax=0`, dicatat bruto. Bila template mewajibkan
pajak dan company belum punya mapping = Tidak siap.

## 7. Alur transaksi

### 7.1 SPA
Menu "Pembelian & Pengeluaran" (`requiresOnline`). Kasir hanya melihat template
yang aktif untuk company shift-nya dan role-nya; manager memakai `CompanyPicker`.

Langkah:
1. Pilih template (dikelompokkan per kategori).
2. Isi baris item (qty, UOM pembelian, harga) atau deskripsi + nominal untuk JE.
3. Pilih sumber bayar dari daftar template; yang termasuk laci shift diberi
   label.
4. Isi supplier atau nama toko, nomor nota, dan foto.
5. Submit.

Klien membuat `client_request_id` (UUID) sekali per form; dipakai lagi pada
setiap retry.

### 7.2 Server: `api/spend.py submit_spend(payload)`
Satu request = satu transaksi DB (commit Frappe di akhir request, rollback bila
error).

1. Otorisasi: role template, company dalam cakupan user (pola
   `_purchase_company_options`), profile milik company.
2. Idempotensi: cari dokumen dengan `posa_client_request_id` di PI/JE (§12).
   Jika ada, kembalikan dokumen itu tanpa membuat baru.
3. Jika sumber = laci shift: kunci `POS Opening Shift` `FOR UPDATE` (lock yang
   sama dengan `_submit_closing`), pastikan masih Open dan milik user/profile.
   Ini menserialkan spend dan closing.
4. Resolusi semua akun dan pajak (§5, §6); gagal = throw dengan pesan
   terjemahan.
5. Bangun dokumen:
   - **PI**: `supplier`, `bill_no`/`bill_date` (nota), `remarks` + nama toko,
     `posting_date` hari ini, `set_warehouse` gudang profile bila stok,
     `cost_center`, `disable_rounded_total=1` (harus sama persis dengan nota),
     `taxes_and_charges`. Bila bayar sekarang: `is_paid=1`, `mode_of_payment`,
     `cash_bank_account` = akun hasil, `paid_amount` = `grand_total`
     (pembayaran sebagian tidak diizinkan).
   - **JE**: `voucher_type`, `mode_of_payment`, `bill_no`, `user_remark`; baris
     Dr akun debit, baris Cr akun sumber, `cost_center`.
6. Cap field penanda (§12), `insert`, `submit`, lampirkan foto ke dokumen
   (`attach_purchase_files`).
7. Kembalikan nama dokumen, ringkasan GL, dan dampak ke shift.

Hak akses dokumen: endpoint membuat dokumen dengan `ignore_permissions` setelah
pemeriksaan eksplisit di atas, tanpa memberi DocPerm PI/JE ke role kasir. Ini
menutup Desk/REST bagi kasir. Alternatif (DocPerm + User Permission Company)
dicatat di §15.

### 7.3 Ledger yang dihasilkan
- PI stok bayar sekarang: Dr persediaan gudang (perpetual) / Cr hutang lalu Dr
  hutang / Cr sumber; SLE +qty, valuasi = harga beli.
- PI non-stok bayar sekarang: Dr akun debit / Cr hutang, Dr hutang / Cr sumber.
- PI bayar nanti: Dr persediaan/beban / Cr hutang; sumber tidak tersentuh.
- JE: Dr akun debit / Cr sumber.

## 8. Pembayaran tertunda (PI `is_paid=0`)

- PI muncul di riwayat dengan status Unpaid/Overdue native.
- Pelunasan lewat **Payment Entry native** (`get_payment_entry("Purchase
  Invoice", name)`). Terbukti di §2.3: PE Dr hutang / Cr kas, PI menjadi Paid,
  cancel PE mengembalikan outstanding.
- Dari POSNext: aksi "Bayar" di riwayat memanggil `pay_spend_invoice(pi,
  mode_of_payment, amount, client_request_id)`. Endpoint ini memakai aturan
  sumber yang sama (§4.2): jika sumber laci shift, PE dicap ke shift yang
  terbuka **saat pembayaran terjadi**, bukan shift saat PI dibuat. Pembayaran
  sebagian diizinkan; tiap PE berdiri sendiri.
- PE yang dibuat dari Desk (HQ membayar via bank) tidak dicap dan tidak memengaruhi
  shift. Ini benar: uangnya tidak keluar dari laci.
- Tidak ada pelunasan otomatis.

## 9. Batas nominal: permintaan vs uang yang sudah keluar

`max_amount` per template dengan `over_limit_policy`:
- **Request first**: di atas batas, kasir tidak bisa submit pembayaran. Yang dibuat
  adalah dokumen **draft** (PI/JE `docstatus=0`, `posa_spend_status=Requested`).
  Draft tidak punya GL dan tidak memengaruhi shift. Manager menyetujui di
  POSNext. Saat itu manager memilih sumber bayar, dan endpoint `submit_spend`
  yang sama mensubmit draft. Dokumen dicap ke shift bila sumbernya laci shift dan
  shift masih terbuka; jika tidak, manager memilih sumber non-shift. Penolakan =
  draft dihapus, dengan Comment tercatat.
- **Record and flag**: untuk kasus uang fisik sudah keluar. Kasir tetap submit
  (GL dan shift tercatat), wajib mengisi alasan. Dokumen ditandai
  `posa_spend_status=Needs Review` dan manager diberi notifikasi (ToDo
  native). Transaksi nyata tidak pernah ditolak, sehingga kas tidak "hilang"
  saat closing.

Di form, kasir selalu memilih secara eksplisit "Uang sudah dibayarkan?" bila
nominal melewati batas. Template menentukan pilihan mana yang tersedia.

## 10. Riwayat terpadu

Endpoint `get_spend_history(company, pos_profile, from_date, to_date, status)`.
Endpoint ini menggabungkan PI, JE, dan PE yang punya `posa_spend_template`, dan
menampilkan:
- template, nominal, sumber, dokumen ERPNext;
- status native (Draft/Paid/Unpaid/Cancelled/Return) dan status spend
  (Requested/Needs Review);
- shift (bila dicap), dan koreksi yang terhubung (`return_against` /
  `reversal_of`).

Cakupan mengikuti aturan PO/PR: kasir hanya company shift-nya, manager company
yang diizinkan. Tidak ada doctype transaksi.

## 11. Penamaan

"Cash Out" menyesatkan karena fitur ini juga memakai bank dan pembayaran
tertunda. Usulan:
- Doctype: `POS Spend Template`, child `POS Spend Template *`,
  `POS Shift Spend Source`.
- API: `api/spend.py`.
- UI (id): "Pembelian & Pengeluaran".
- Field penanda: `posa_spend_*`.

"Spend" dipilih karena netral terhadap jenis dokumen dan cara bayar.
Alternatif `POS Purchase Expense Template` lebih panjang tanpa tambahan makna.

## 12. Konsistensi, idempotensi, dan anti-dobel

Custom field (via `install.py`, pola POSNext) di **Purchase Invoice, Journal
Entry, Payment Entry**, semuanya `no_copy=1` agar Amend/Debit Note/Reverse tidak
mewarisi cap:
- `posa_client_request_id` (Data, **unique=1** → unique index MariaDB; banyak
  NULL diizinkan)
- `posa_spend_template` (Link)
- `posa_pos_profile` (Link)
- `posa_pos_opening_shift` (Link, diisi hanya bila sumber laci shift)
- `posa_shift_account` (Link Account, akun laci yang dikredit/didebit)
- `posa_vendor_name` (Data)
- `posa_spend_status` (Select: kosong/Requested/Needs Review)
- `posa_correction_of` (Dynamic Link, untuk koreksi pasca-closing)

Idempotensi:
1. Cek awal berdasarkan `posa_client_request_id`.
2. Jika dua request lolos bersamaan, `insert` kedua gagal di unique index
   (`frappe.DuplicateEntryError`). Endpoint rollback, membaca dokumen pertama,
   dan mengembalikannya.
3. Seluruh insert + submit + lampiran berada di satu transaksi DB, sehingga GL
   tidak pernah setengah jadi.
4. File foto di disk bisa yatim bila rollback; diterima (tidak memengaruhi
   akuntansi).

Pengaruh ke shift dihitung dari **GL Entry native**, bukan dari field dokumen:

```
spend_out(shift, account) = Σ (credit_in_account_currency − debit_in_account_currency)
  dari GL Entry dengan is_cancelled = 0, account = posa_shift_account,
  dan voucher (PI/JE/PE) docstatus = 1 dengan posa_pos_opening_shift = shift
```

Dengan cara ini:
- JE dihitung dari kredit pada akun sumber, bukan `total_debit` (permintaan
  user);
- Debit Note dengan refund kas otomatis negatif (uang masuk);
- dokumen cancel otomatis keluar (`is_cancelled`);
- retry tidak menambah dokumen.

Closing memetakan `posa_shift_account` ke MoP shift lewat cap MoP yang disimpan
di dokumen (`mode_of_payment`).

## 13. Integrasi shift dan closing

- `make_closing_shift_from_opening`: setelah invoice dan PE Receive, ambil
  `spend_out` per (MoP, akun) untuk shift. Kurangkan dari `expected_amount` baris
  MoP itu; tambahkan baris bila MoP belum ada (opening 0).
- Closing menyimpan snapshot: child table baru `POS Closing Shift Spend`
  (`voucher_type`, `voucher_no`, `mode_of_payment`, `amount`), dibangun di
  `_submit_closing` seperti transaksi lain. Closing submit = arsip final yang bisa
  diaudit.
- `pos_closing_print.py` `total_expense` dan `sales_recap.py` `expense` /
  `expense_supported=True` diisi dari snapshot/fungsi yang sama;
  `cash_in_hand` = kas setelah pengeluaran. iMin, Jinja, dan SPA sudah punya
  slotnya.
- Handover (`get_cash_handover`) dan Backdate Entry otomatis benar karena
  memakai `expected_amount` hasil fungsi yang sama.

## 14. Pembatalan dan koreksi

Hook `before_cancel` POSNext pada PI/JE/PE yang punya `posa_pos_opening_shift`:
- **Shift masih terbuka**: cancel native diizinkan dari POSNext (pembuat atau
  manager; role dicek di endpoint `cancel_spend`). GL/SLE dibalik, closing nanti
  tidak menghitungnya.
- **Shift sudah punya closing submit**: cancel **ditolak**, juga dari Desk. Koreksi
  harus memakai dokumen baru bertanggal hari ini, sehingga closing lama tetap
  utuh.

Dokumen tanpa cap shift (sumber non-laci): cancel native biasa oleh manager.

Koreksi pasca-closing per jenis (endpoint `correct_spend`, manager):

| Kasus | Mekanisme native | Efek |
|---|---|---|
| PI barang/beban dibatalkan atau dikembalikan | Debit Note (`make_debit_note`): penuh atau sebagian, `is_paid` dan `update_stock` terbawa | Stok keluar, kas masuk ke sumber yang dipilih. Bila sumbernya laci shift, dicap ke shift yang **terbuka sekarang** (masuk sebagai pengurang spend). `return_against` tercatat |
| PI salah akun beban, uang benar | JE reklasifikasi (Dr akun benar / Cr akun salah), tanpa akun kas | Shift tidak tersentuh |
| JE beban salah/batal | Reverse JE (`make_reverse_journal_entry`, `reversal_of`) bertanggal hari ini | Bila sumber laci dan uang benar-benar kembali, dicap ke shift terbuka; bila tidak, tanpa cap |
| PE pelunasan salah | Cancel PE bila shift-nya masih terbuka; jika tidak, PE balik (Receive dari supplier) | Sama dengan aturan di atas |
| Seluruh closing harus diulang | Backdate Entry yang sudah ada (closing lama dibatalkan dan dibuat ulang, otomatis menghitung spend) | Opsi terakhir, bukan jalur utama |

Semua dokumen koreksi diisi `posa_correction_of` dan Comment pada dokumen asal.
Versi dokumen native mencatat siapa dan kapan.

Batas yang diterima: PI stok yang barangnya sudah terpakai tidak bisa dibuat
Debit Note-nya bila stok menjadi negatif. ERPNext menolak (`allow_negative_stock`
= 0 di dev); pesannya diterjemahkan untuk user.

## 15. Edge case, risiko, dan pertanyaan terbuka

Edge case yang ditangani:
- Template berubah setelah transaksi: dokumen lama tidak berubah; akun hanya
  diresolusi saat submit. Template dinonaktifkan: tidak tampil, riwayat tetap.
- Mapping MoP berubah: closing memakai `posa_shift_account` yang dicap, bukan
  mapping terbaru.
- Konfigurasi tidak lengkap: ditolak dengan alasan per komponen (sama dengan cek
  kesiapan).
- UOM nota ≠ UOM stok: PI menyimpan `uom` + `conversion_factor` dari Item UOM;
  UOM tanpa konversi ditolak.
- Budget ERPNext (`BudgetError`) dan fiscal year tertutup: diteruskan sebagai
  pesan yang bisa dibaca.
- Zona waktu: `posting_date` dari server; jebakan zona waktu situs yang sudah
  dikenal berlaku (cek seperti NegativeStockError).
- Manager tanpa shift: hanya sumber non-shift.

Risiko:
- Data dev: perpetual inventory TRAINING mati dan hutang default-nya Hutang
  Pajak. Cek kesiapan harus melaporkan keduanya; E2E di TRAINING tidak mewakili
  jurnal stok outlet riil.
- `Lokal Supplier` tanpa Party Account mewarisi hutang default company;
  disarankan menambah Party Account bila HQ ingin akun hutang khusus.
- `check_supplier_invoice_uniqueness=0`: nota ganda tidak dicegah ERPNext.
  Mengaktifkannya global akan menolak `bill_no` sama untuk supplier generik yang
  kebetulan sama; tidak diaktifkan. Peringatan duplikat (supplier + bill_no +
  nominal dalam 7 hari) ditampilkan di form.
- `ignore_permissions` di endpoint memindahkan beban keamanan ke pemeriksaan
  POSNext; wajib dites per role.

Pertanyaan terbuka (diputuskan sebelum/selama plan):
1. ~~CoA root~~ Diputuskan: `JURI GROUP` polos dan tidak dipakai; sinkronisasi
   native tidak dipakai (§6.2).
2. ~~Company referensi~~ Diputuskan: `OUTLET`, lewat setting
   `spend_reference_company` (§6). Pabrik `PT. JUARA ROTI INDONESIA` hanya ikut
   bila dimasukkan eksplisit ke tabel `companies`.
3. ~~Hak akses~~ Diputuskan (user setuju usulan, 2026-11-02): endpoint POSNext
   memeriksa role/company/shift lalu membuat dokumen dengan `ignore_permissions`;
   kasir tidak diberi DocPerm PI/JE/PE.

## 16. Pengujian

BE (serial, `_pn_run_tests.py`), test merah dulu:
- resolusi akun: nomor sama tipe beda, parent beda, mata uang, disabled, dua
  hasil, override salah company;
- strategi debit: Template Account tidak pernah memakai Item Default; Item
  Default menolak fallback default company;
- submit PI stok/non-stok/bayar nanti dan JE: GL dan SLE sesuai tabel §7.3;
- idempotensi: request ganda berurutan dan duplikat unique index → satu dokumen;
- closing: spend laci mengurangi expected, sumber non-laci tidak; Debit Note dan
  reverse JE di shift berikut menambah kas; cancel di shift terbuka keluar dari
  hitungan;
- cancel ditolak setelah closing (endpoint dan Desk);
- PE pelunasan dicap ke shift saat bayar;
- batas nominal: Requested tanpa GL, Needs Review dengan GL;
- otorisasi: kasir lain company, template di luar role.

FE: form per jenis template, retry memakai `client_request_id` yang sama,
riwayat, Total Expense di Sales Recap.

E2E dev (setelah backup + rebuild): satu outlet dengan perpetual aktif, buka
shift → JE beban kas laci → PI stok bayar laci → PI bayar nanti → PE dari laci →
closing (expected benar, EOD Total Expense) → Debit Note di shift berikut.

## 17. Tahapan

1. **Fondasi + beban**: custom field + unique index, `POS Spend Template`,
   resolusi akun, cek kesiapan, `POS Shift Spend Source`, JE bayar sekarang, PI
   non-stok bayar sekarang, integrasi closing/EOD/Recap, riwayat, cancel di shift
   terbuka + guard pasca-closing.
2. **Barang dan pajak**: PI `update_stock`, UOM, pajak per company, Debit Note,
   reverse JE, reklasifikasi.
3. **Tertunda dan kontrol**: PI bayar nanti, PE dari POSNext, batas nominal
   (Request first / Record and flag), laporan HQ per template × outlet,
   bantuan penyediaan akun.
