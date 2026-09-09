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

Tata letak mengikuti laporan HQ lama: tabel **Monthly Monitoring** (lebar
penuh) → tabel **Daily Monitoring** (lebar penuh) → 4 kartu ringkas →
hero 2×2 (Total Sales, Total Transactions, Avg per Transaction, Peak Hour) →
grid grafik 2×2 (dua kartu **Top Selling [dropdown kategori]** berisi donat
produk + legenda nilai, lalu donat **Top Selling Categories** dan
**Top Outlets**) → **Product Ranking** dan
**Outlet Ranking** berdampingan → catatan "Currency & basis notes" →
catatan sumber data.

### Dua periode yang berbeda (dilabel eksplisit)

- **MTD** (month-to-date bulan dari "To Date"): tabel Monthly Monitoring,
  turnover, dan target. MTD **tidak** bergeser walau rentang dippersempit.
- **Rentang terpilih** (`from_date .. to_date`): hero cards, Peak Hour,
  ranking produk/outlet, dan donat. Preset: Today, Yesterday, Last 7 Days,
  This Month, Custom (API menerima tanggal hasil preset apa adanya).
- Hari ini dipotong pada jam server saat itu; hari lampau hari penuh.

### Monthly Monitoring (MTD) — baris Sales / TC / APC

| Kolom | Rumus |
| --- | --- |
| Target | POS Monthly Target (sales per mata uang; TC bebas mata uang) |
| MTD | `SUM(base_grand_total)` bertanda (retur mengurangi); TC = faktur non-retur; APC = MTD ÷ TC |
| Achievement % | MTD ÷ Target × 100 |
| Over / (Deficit) | MTD − Target |
| Monthlyized (proyeksi nilai) | Sales & TC: MTD ÷ hari-berjalan × hari-dalam-bulan. **APC tidak diekstrapolasi** — proyeksinya = APC MTD (rata-rata) |
| Projected Ach. % | Proyeksi ÷ Target |
| Projected Surplus | Proyeksi − Target |

Catatan target:

- Target harian **bukan** angka yang diset terpisah — adalah target bulanan
  dibagi jumlah hari dalam bulan (pro-rata) dan ditampilkan dengan label itu.
- Jika ada satu saja perusahaan dalam lingkup yang belum punya target pada
  bulan tersebut, seluruh kolom target ditampilkan **N/A** — bukan angka
  parsial atau nol rekaan.

### Daily Monitoring — hari terpilih vs hari yang sama minggu lalu

- Header bergrup (mengikuti laporan HQ lama): [tanggal terpilih: Daily
  Target, Result, Ach. %] vs [**hari yang sama minggu lalu** (−7 hari):
  Result, Growth vs LW]. Pembanding dipotong pada jam yang sama (same
  elapsed cutoff) ketika hari terpilih adalah hari ini.
- Pertumbuhan vs *prior weekday* (hari kerja sebelumnya, Sabtu/Minggu
  dilewati) tetap tersedia sebagai baris catatan sekunder di bawah tabel.
- Daily target = target bulanan pro-rata (sales ÷ hari; TC ÷ hari; APC =
  daily sales target ÷ daily TC target).
- Growth = (nilai − pembanding) ÷ pembanding × 100; pembanding 0 → **N/A**,
  tidak pernah dianggap 0%.

### Kartu ringkas

Turnover MTD (+ perubahan vs bulan lalu dengan hari berjalan setara),
Outlet terbesar (share %, dalam satu mata uang), **Outlet dengan transaksi
terbanyak**, dan Produk favorit (qty tertinggi bertanda, komponen paket
dikecualikan).

### Hero 2×2 (rentang terpilih)

- **Total Sales**: net incl. tax ↔ pre-tax (tombol toggle pada kartu);
  pajak & biaya serta refunds terlihat di sub-teks.
- **Total Transactions**: jumlah faktur non-retur. **Pax selalu N/A** —
  tidak ada field sumber pax, tidak direka.
- **Avg per Transaction**: net incl. tax ÷ orders, per mata uang.
- **Peak Hour**: grafik batang pesanan per jam dengan label `HH:00` eksplisit
  (jam tanpa penjualan = batang nol; sumbu tidak pernah "…").

#### Filter jam Peak Hour (hanya kartu ini)

- Dua pilihan **From / To** di kartu mengatur jendela jam: **From inklusif,
  To eksklusif** — 05:00–18:00 berarti bin 05:00 sampai 17:00–18:00.
- **Overnight didukung**: To ≤ From berarti lewat tengah malam
  (22:00–06:00 = bin 22,23,0,1,2,3,4,5). Hari penuh = 00:00–24:00
  (24:00 = tengah malam berikutnya).
- **From = To ditolak** (jendela kosong) — muncul peringatan, pilihan
  dikembalikan ke nilai semula.
- Grafik dan label Peak (nilai uang + jumlah order terukur) dihitung ulang
  hanya dari jam yang terlihat; **metrik halaman lain tidak berubah**.
- Lebar minimum grafik proporsional terhadap jumlah bin (40px/bin), jadi
  jendela pendek pas di kartu tanpa scroll; jendela penuh tetap terbaca
  (scroll lokal bila perlu).
- Preferensi disimpan **per user** lewat mekanisme bawaan Frappe
  (`frappe.model.utils.user_settings`, tabel `__UserSettings`, kunci
  `HQ Sales Monitoring` → `hq_dashboard`): otomatis dipulihkan saat refresh
  atau login berikutnya. Yang disimpan hanya angka jam dan nama Item Group —
  tanpa nilai sensitif. Default kunjungan pertama: 00:00–24:00.

### Kartu "Top Selling [kategori]" (dua slot, saling bebas)

- Dua kartu serupa donat, masing-masing dengan dropdown **Item Group** sendiri
  di baris judul (`Top Selling <dropdown>`; pilihan berisi semua Item Group
  yang ada). Slot internal bernama `a`/`b` dan **tidak pernah tampil ke
  pengguna** — hanya label aksesibel ("First/Second category"). Grup terpilih
  **termasuk sub-group** (pohon `lft/rgt`).
- Isi kartu: **donat 5 item teratas** dalam kategori itu berdasarkan net
  revenue pre-tax + satu busur **"Other"** bila sisa kategori bernilai
  positif; legenda nilai milik halaman memuat nama, qty sekunder, nilai,
  dan **share % terhadap SELURUH kategori** — persis data yang digambar
  (dihitung ulang dari nilai yang sama, `categoryDonutRows`). Subtotal
  kategori tercantum eksplisit.
- Metode sama dengan donat kategori: **positif saja** (item net ≤ 0
  dilaporkan di catatan, tidak digambar — tidak ada busur/arc
  negatif) dan **satu mata uang** (perusahaan ber-mata-uang lain dicantumkan,
  tidak pernah dijumlahkan).
- Belum memilih kategori / kategori tidak ada lagi / tanpa penjualan
  positif → **placeholder cincin putus-putus netral** dengan penjelasan
  singkat (tanpa busur palsu); dropdown selalu terlihat.
- Query agregat berjalan di atas **dataset penuh** kategori (GROUP BY
  item_code, bukan Product Ranking yang terpaginasi); tepat **satu query per
  kartu terisi (maks dua)** — tanpa N+1. Kategori yang sudah tidak ada
  dilaporkan (`invalid`), lalu dibersihkan dari preferensi dan kartu kembali
  ke "pilih kategori".
- Kedua kartu **independen**: memilih kategori di kartu A tidak memengaruhi
  kartu B maupun filter kategori global Product Ranking (dan sebaliknya —
  filter kategori global hanya berlaku pada Product Ranking).
- Preferensi kategori A/B disimpan bersama filter jam pada preferensi user
  yang sama (lihat di atas) dan divalidasi ulang setiap load.

### Donat (metode diberi label)

- **Top Selling Categories**: 5 grup item teratas berdasarkan **net revenue
  pre-tax, positif saja** — grup dengan net ≤ 0 (dominasi retur) dilaporkan
  sebagai dikecualikan, tidak digambar. Mata uang = mata uang default
  lingkup; perusahaan ber-mata-uang lain dicantumkan sebagai dikecualikan
  (tidak pernah dijumlahkan lintas mata uang). Grup diambil dari data
  aktual (dinamis), bukan Food/Beverage/Rental paksaan. Legenda nilai adalah
  daftar aksesibel milik halaman (legenda bawaan frappe-charts dimatikan via
  `showLegend: false` agar tidak dobel/terpotong).
- **Top Outlets**: 5 perusahaan teratas, net incl. tax positif, satu mata
  uang. Legenda di samping donat memuat nilai + persentase (tidak bergantung
  warna saja).

### Ranking (rentang terpilih)

- **Product Ranking**: paginasi (10/halaman, maks 50), filter kategori
  Item Group di kartu (pilihan mencakup sub-group; **filter ini hanya
  berlaku pada Product Ranking**, tidak pada kartu Top Selling [kategori]),
  Export CSV. Share % terhadap total net sales lingkup filter aktif.
  Urutan stabil: net desc, qty desc, kode item.
- **Outlet Ranking**: **outlet = perusahaan** (bukan POS Profile; profil POS
  dicantumkan di bawah nama perusahaan agar tidak ada angka tersembunyi).
  Kolom: net sales per mata uang, TC, avg ticket, share % terhadap total satu
  mata uang. Export CSV.

### Bagian yang Sengaja Tidak Dikarang

- **Sales Channel**: `Sales Invoice` tidak punya field channel — halaman
  menampilkan keterangan "tidak tersedia" dan mengarah ke Outlet Ranking.
  Tidak ada "Take Away" rekaan.
- **Pax/tamu**: tidak ada field sumber pax pada invoice POS — tidak dihitung,
  tidak diperkirakan.

## Filter & Waktu

- **Time Range preset**: Today (default), Yesterday, Last 7 Days, This Month,
  Custom (dengan input tanggal). Preset menghitung `from_date`/`to_date` di
  klien dan mengirimkannya apa adanya — bagian MTD tetap month-to-date di
  server (tidak ada label periode yang bohong).
- Company (semua yang terlihat user, atau satu perusahaan), termasuk opsi
  **Include Subsidiaries** (menambahkan perusahaan anak lewat pohon Company;
  anak di luar izin user tetap dikecualikan).
- Rentang tanggal maksimum **366 hari**; tanggal di masa depan dipotong ke
  hari ini. Semua waktu memakai zona waktu server.

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

- Backend: `pos_next/tests/test_hq_monitoring.py` — 24 test (metrik, retur,
  komponen paket, urutan & tie ranking, paginasi, filter kategori, mata uang
  campuran, ranking outlet level perusahaan, donat kategori positif-saja,
  kartu Top Selling per kategori: isolasi A/B + share kategori penuh,
  sub-group, isolasi mata uang, kategori invalid/tanpa data, kemandirian
  dari filter kategori global, rentang terpilih vs MTD, target
  tersedia/hilang + proyeksi TC, penolakan tanpa peran, perusahaan palsu,
  lingkup non-pemilik, filter company report, validasi doctype).
  Jalankan di dalam container:
  `./env/bin/python apps/pos_next/pos_next/_pn_run_tests.py pos_next.tests.test_hq_monitoring`
- Helper JS murni: `node pos_next/tests/js/hq_monitoring_utils.test.cjs`
  (12 grup: format, hour bins, **jendela jam** valid/overnight/batas 24/
  From=To ditolak, **recompute peak** dalam jendela, lebar grafik
  proporsional, **sanitasi preferensi** termasuk storage korup).
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
