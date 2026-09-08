# HQ Sales Monitoring (Pemantauan Penjualan Pusat)

Halaman Desk untuk memantau penjualan seluruh outlet POS dari sisi head office.

- Rute halaman: `/app/hq-sales-monitoring`
- Shortcut: workspace **POSNext** → "Your Shortcuts" → **HQ Sales Monitoring**
  (juga tersedia di kartu **Reports**).
- API: `pos_next.api.hq_monitoring.get_sales_monitoring` (whitelisted, read-only,
  satu panggilan untuk seluruh panel — tanpa N+1).
- Doctype target: **POS Monthly Target**.

## Lingkup Data (Sumber Kebenaran)

- Hanya transaksi POS: `Sales Invoice` dengan `docstatus = 1` dan `is_pos = 1`.
  Ledger non-POS tidak pernah ikut terhitung.
- Semua angka uang memakai mata uang perusahaan (field `base_*`). Perusahaan
  dengan mata uang dasar berbeda **tidak pernah dijumlahkan mentah**; setiap
  metrik uang dikembalikan per mata uang (`by_currency`) plus satu nilai default
  yang diberi label mata uang secara eksplisit.
- Uang kotor vs retur dipisah. Faktur retur (`is_return = 1`) tidak pernah
  dihitung sebagai pesanan baru (order/TC).
- Komponen paket (`Sales Invoice Item.pos_package_role = 'Package Item'`)
  dikeluarkan dari kuantitas dan jumlah item, jadi pendapatan bundle tidak
  dihitung ganda (pendapatan hanya ada di baris induk paket).
- Kuantitas bertanda: retur menambah qty negatif.
- Angka kategori/produk memakai nilai item **sebelum pajak** (`base_net_amount`),
  jadi sengaja tidak akan rekonsiliasi 1:1 dengan total yang termasuk pajak.

## Panel dan Rumus

### Monthly Monitoring (MTD, bulan dari tanggal "To Date")

| Metrik | Rumus |
| --- | --- |
| Sales (MTD, net incl. tax) | `SUM(base_grand_total)` bertanda (retur mengurangi) |
| TC (Transactions) | jumlah faktur non-retur |
| APC | Sales MTD ÷ TC (per mata uang) |
| Achievement % | Sales MTD ÷ Target Sales × 100 |
| Surplus / Deficit | Sales MTD − Target Sales |
| Monthly Projection | Sales MTD ÷ hari-berjalan × hari-dalam-bulan |
| Projected Achievement | Projection ÷ Target Sales |

Catatan target:

- Proyeksi memakai **hari berjalan** (hari ini ikut dihitung), tidak menghitung
  hari masa depan; APC tidak diekstrapolasi secara terpisah.
- Target transaksi (TC) bebas mata uang; target penjualan mengikuti mata uang
  default perusahaan.
- Target harian **bukan** angka yang diset terpisah — adalah target bulanan
  dibagi jumlah hari dalam bulan (pro-rata) dan ditampilkan dengan label itu.
- Jika ada satu saja perusahaan dalam lingkup yang belum punya target pada
  bulan tersebut, achievement ditampilkan **tidak tersedia (N/A)** — bukan
  angka parsial yang menyesatkan.

### Daily Monitoring

- Hari yang dipilih (default hari ini). Bila hari ini: cutoff pada jam server
  saat itu; bila hari lampau: satu hari penuh.
- Pembanding: *prior weekday* (hari kerja sebelumnya, Sabtu/Minggu dilewati) dan
  *hari yang sama minggu lalu* (−7 hari). Untuk "hari ini" kedua pembanding
  dipotong pada jam yang sama (same elapsed cutoff); untuk hari lampau keduanya
  hari penuh.
- Growth = (nilai − pembanding) ÷ pembanding × 100. Jika pembanding 0,
  growth **N/A** — tidak pernah dianggap 0%.

### Turnover Bulan Ini

- Net turnover MTD, perubahan vs periode pembanding (bulan lalu dengan jumlah
  hari berjalan yang sama, hari penuh karena bulan lalu sudah selesai).
- Refunds (nilai absolut retur + jumlah faktur retur), net pre-tax
  (`base_net_total`), pajak & biaya (`base_total_taxes_and_charges`), keduanya
  bertanda sehingga retur sudah mengurangi.

### Highlight, Jam, dan Ranking

- Outlet terbesar, outlet dengan transaksi terbanyak, produk favorit (qty
  tertinggi bertanda, komponen paket dikecualikan).
- Jam sibuk / 3 jam tertinggi / 3 jam terendah (hanya jam dengan order) +
  grafik batang memakai `frappe.Chart` bawaan Desk (tanpa library baru).
- Product Ranking (MTD): paginasi (10 baris/halaman, maks 50), filter kategori
  Item Group — kategori yang dipilih otomatis mencakup sub-group (pohon
  Item Group), bukan hardcoded Food/Beverage/Retail. Share % dihitung terhadap
  total net sales lingkup filter yang sedang aktif. Urutan stabil: net desc,
  qty desc, kode item.
- Outlet Ranking (MTD): net sales per mata uang, TC, avg ticket, share % terhadap
  total satu mata uang.

### Bagian yang Sengaja Tidak Dikarang

- **Sales Channel**: `Sales Invoice` tidak punya field channel — halaman
  menampilkan keterangan "tidak tersedia" dan mengarah ke Outlet Ranking.
  Tidak ada "Take Away" rekaan.
- **Pax/tamu**: tidak ada field sumber pax pada invoice POS — tidak dihitung,
  tidak diperkirakan.

## Filter & Waktu

- Company (semua yang terlihat user, atau satu perusahaan), termasuk opsi
  **Include Subsidiaries** (menambahkan perusahaan anak lewat pohon Company;
  anak di luar izin user tetap dikecualikan).
- Rentang tanggal maksimum **366 hari**; tanggal di masa depan dipotong ke
  hari ini. Semua waktu memakai zona waktu server.
- Perintah "MTD hari ini" dipotong pada jam server saat ini.

## Perizinan

- Endpoint hanya untuk peran **System Manager, Accounts Manager, Sales Manager,
  Nexus POS Manager**.
- Lingkup perusahaan mengikuti **User Permission** doctype Company
  (`pos_next.hq_scope`). Perusahaan yang dipalsukan di luar izin → PermissionError,
  bukan diam-diam difilter; tanpa filter perusahaan, hasil otomatis dibatasi ke
  perusahaan yang diizinkan.
- Lingkup POS Profile mengikuti **User Permission** doctype POS Profile.
- Report **Sales vs Shifts Report** memakai mekanisme yang sama: filter Company
  baru (frontend + server, berlaku untuk tabel, summary, dan semua chart), dan
  perusahaan di luar izin user ditolak meski filter dikosongkan.

## Doctype POS Monthly Target

- Field: `company`, `month_start` (wajib tanggal 1), `target_sales`
  (Currency mengikuti mata uang perusahaan, non-negatif), `target_transactions`
  (Int non-negatif), `notes`.
- Unik per (company, bulan) lewat autoname `POS-TGT-{company}-{month_start}`.
- Hak akses: tulis hanya **System Manager** & **Accounts Manager**;
  **Sales Manager** & **Nexus POS Manager** hanya baca.

## Uji & Verifikasi Lokal

- Backend: `pos_next/tests/test_hq_monitoring.py` — 15 test (metrik, retur,
  komponen paket, urutan & tie ranking, paginasi, filter kategori, mata uang
  campuran, target tersedia/hilang, penolakan tanpa peran, perusahaan palsu,
  lingkup non-pemilik, filter company report, validasi doctype).
  Jalankan di dalam container:
  `./env/bin/python apps/pos_next/pos_next/_pn_run_tests.py pos_next.tests.test_hq_monitoring`
- Helper JS murni: `node pos_next/tests/js/hq_monitoring_utils.test.cjs`.
- Print/Export: tombol **Print** (CSS khusus cetak) dan **Export CSV**
  (ranking produk + outlet).

## Deployment & Keterbatasan

- Migrasi lokal sudah dijalankan (`bench --site posnext.localhost migrate`);
  doctype baru, halaman, dan shortcut workspace tersinkron otomatis oleh
  framework (tanpa patch, tanpa perubahan schema destruktif).
- **Cloud belum diubah** — deployment ke `rotiropi.j.frappe.cloud` menunggu
  push/merge oleh pemilik proyek.
- Page dimuat langsung dari folder modul (JS/CSS standard page), jadi tidak
  perlu `bench build` untuk halaman ini.
- Kinerja: seluruh panel memakai beberapa query agregat ber-GROUP BY dengan
  batas halaman/outlet (LIMIT) — tidak ada query per baris (N+1).
