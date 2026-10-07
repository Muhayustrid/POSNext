# POS Package — Dokumentasi Fitur

Harga paket dialokasikan proporsional ke baris item komponen, bukan di-nol-kan di
baris parent. Total invoice selalu sama persis dengan harga paket.

Status: branch `feature/pos-package` (PR [#5](https://github.com/Muhayustrid/POSNext/pull/5)),
toggle **default OFF** — perilaku lama tidak berubah sampai dinyalakan.

Dokumen terkait: [Panduan Uji Manual E2E](POS_PACKAGE_MANUAL_TEST.md) ·
[Review internal](POS_PACKAGE_REVIEW.md)

---

## 1. Konsep

Paket dijual dengan satu harga (mis. "Paket Hemat" 25.000 berisi Roti + Teh).
Dua masalah di mode lama:

- Omzet semua nyangkut di baris parent; komponen terjual Rp 0 → laporan per
  produk menyesatkan.
- Roti tercatat pendapatan 0 tetapi HPP-nya tetap terjadi → margin per item
  terlihat negatif.

Mode alokasi membagi harga paket ke komponen menurut **bobot harga jual
satuannya** (price list), sehingga pendapatan dan HPP ada di baris yang sama.

Contoh (presisi 0): Roti 20.000 + Teh 10.000, paket 23.000 →
Roti **15.333** + Teh **7.667** = 23.000. Sisa pembulatan dibebankan ke baris
qty-1 berbobot terbesar.

### Penilaian desain: best practice

Per penilaian akuntansi (tetap konfirmasi ke akuntan Anda):

- **Sesuai standar**: PSAK 72 (setara IFRS 15) meminta harga transaksi paket
  dialokasikan ke tiap barang menurut proporsi harga jual satuannya — persis
  metode yang dipakai (bobot = price list komponen).
- **Pola umum POS lain**: combo product Odoo dan bundle Shopify juga membagi
  harga ke baris komponen.
- **Pengaman desain**: server selalu menghitung ulang harga dari snapshot
  (rate dari klien tidak pernah mengikat), kasus yang tidak bisa dibagi pas
  ditolak (fail-closed), toggle default OFF, invoice lama tidak tersentuh.

Kesimpulan: konsepnya sudah best practice; risikonya ada di **operasional**
(bagian 5), bukan di desain.

---

## 2. Model data di invoice

| Baris | Mode lama | Mode alokasi |
|---|---|---|
| Parent (mis. `PKG-HEMAT-PARENT`) | Bawa seluruh harga | Header **rate 0**, qty 1, non-stock |
| Komponen | rate 0 | rate = bagian alokasi |
| `price_list_rate` baris komponen | 0 | = rate alokasi |
| `discount_amount` / `discount_percentage` | 0 | **0 juga** |

Penting: alokasi **bukan** modeling "harga normal + diskon". Harga normal di
price list hanya dipakai sebagai bobot pembagian; yang tersimpan di invoice
adalah rate hasil alokasi dengan discount 0. Konsekuensinya penghematan paket
**tidak tercatat sebagai diskon** di mana pun (lihat risiko R2).

Pengelompok baris memakai field `pos_package` (nama paket),
`pos_package_instance` (id grup per transaksi), `pos_package_role`
(`Package` header / `Package Item` komponen), dan `pos_package_snapshot`
(JSON quote: base_price, total, selections, marker
`allocation: {mode: "proportional", precision}`).

Snapshot marker inilah yang membedakan invoice baru vs lama: tanpa marker =
mode lama, dibuka/diretur apa adanya tanpa reprice.

---

## 3. Aturan alokasi (single source of truth)

Implementasi: `pos_next/api/packages.py:allocate_package_rates` (server,
otoritas akhir) + mirror identik `POS/src/utils/packageAllocation.js`
(preview offline). Keduanya dipin ke tabel ekspektasi bersama
`POS/src/utils/packageAllocation.cases.json` yang dibaca dua-duanya — jaga
tabel itu bila rumus diubah.

1. Bobot komponen = price list rate × qty per paket (selling price list POS
   Profile, per `posting_date` untuk invoice; quote live = hari ini).
2. Rate per unit = bobot / total bobot × harga paket, dibulatkan ke presisi
   currency (`flt`).
3. Sisa pembulatan dibebankan ke **baris qty-1 dengan bobot terbesar di atas
   0** (yang terakhir bila seri). Baris qty-1 berbobot 0 tidak boleh menjadi
   carrier (akan menghasilkan rate negatif).
4. Tanpa baris carrier yang memenuhi syarat: sisa jatuh ke baris terakhir;
   bila tidak habis dibagi, invoice **ditolak** dengan pesan jelas
   (fail-closed) — bukan dijual dengan angka meleset. Definisi paket yang
   mustahil punya carrier ditolak saat **Save** doctype (toggle ON).
5. Fallback bila komponen tanpa harga: bobot qty → bobot rata, dengan warning
   di log (tidak crash).
6. Qty paket > 1: per-unit rate tetap, total uang linear.
7. Server menghitung ulang seluruh quote dari snapshot saat
   `update_invoice`/`submit_invoice` (termasuk replay offline) dan memverifikasi
   `Σ(rate × qty komponen) == harga paket × qty paket`.

---

## 4. Perilaku fitur

- **Toggle**: `enable_pos_package_allocation` (Check, default 0). Kedua mode dipilih per invoice saat di-save; snapshot legacy tidak pernah direprice ke bentuk alokasi.

### Lokasi & operasi toggle di Desk

- **Halaman**: Desk → **POS Next Global Settings** (Single). URL langsung:
  `/app/pos-next-global-settings#tab_details` — ganti hostname sesuai site
  (mis. `roti-posnext-test.localhost:8001`).
- **Posisi field**: tab **Rincian** → section **Harga & Tampilan** (label
  section ter-terjemahkan; di JSON bernama "Pricing & Display"). Checkbox-nya:
  **"Allocate Package Price to Components"**, satu baris setelah **Tax
  Inclusive**, sebelum section Pengaturan Pelanggan.
- **Jebakan UI**: section di tab ini bisa **ter-collapse** — kalau checkbox
  tidak terlihat, klik header "Harga & Tampilan" untuk membukanya (section
  ter-collapse merender field-nya sebagai tersembunyi, jadi pencarian teks
  di halaman pun bisa gagal kalau belum di-expand).
- **Akses**: hanya admin/manager. Kasir (POSNext Cashier) tidak punya read
  permission ke Single ini — nilai toggle sampai ke kasir lewat feed settings
  POS (bootstrap / `get_pos_settings`), bukan lewat akses langsung.
- **Setelah mengubah nilai**: **reload halaman POS**. Setting dibaca saat
  bootstrap (`loadSettings`), tanpa TTL — POS yang sudah terbuka tidak ikut
  berubah sampai di-refresh. Invoice yang di-save tetap mengikuti toggle saat
  itu (server otoritas).
- **Arti nilai**: dicentang = alokasi proporsional (header Rp 0, komponen
  bawa omzet); tidak dicentang = perilaku lama (harga penuh di header,
  komponen Rp 0). Untuk kebijakan go-live sekali jalan, lihat risiko R3 dan
  bagian 6.
- **Pilihan (choice) + item wajib**: grup pilihan (`min_qty`/`max_qty`,
  bisa "pilih tepat 1"), opsi dengan `price_adjustment` (+/- harga), item
  wajib di child `items`. Dialog kasir menampilkan keduanya; total paket
  bergerak mengikuti pilihan. Contoh teruji: Paket Roti Pilih = Teh x1 wajib +
  pilih 1 dari Roti Coklat/Keju/Butter (+0/+2.000/+1.000) → Keju teralokasi
  22.345 + Teh 4.655 = 27.000.
- **Stok**: tetap terpotong per komponen saja; parent header non-stock (0 SLE).
- **Offer/coupon**: baris paket **dikeluarkan dari offer level item** (tahap
  1) secara eksplisit — klaim offer di baris paket ditolak, engine dilaporkan
  ke kasir lewat flag `package_rows_excluded`. Diskon level invoice (manual/
  kupon) dibagi proporsional oleh ERPNext berdasarkan net amount; parent 0
  mendapat bagian 0.
- **Pajak**: total akhir tetap = harga paket. Perubahan semantik: pajak
  mengikuti **Item Tax Template komponen**, bukan parent. Pajak charge type
  **On Item Quantity** + alokasi = **ditolak fail-closed** (header qty-1 akan
  kena pajak sebagai unit ekstra → overcharge senyap); pakai On Net Total atau
  matikan alokasi.
- **Retur**: wajib `return_against`; retur penuh mengembalikan semua komponen
  dengan **rate per baris asal** (komponen duplikat dengan rate beda tetap
  akurat); retur parsial hanya diperbolehkan bila proporsinya cocok dengan isi
  paket (guard menolak dengan pesan jelas). Retur invoice legacy apa adanya.
- **Offline**: mirror JS dipakai untuk preview saat offline; saat sync server
  menghitung ulang — hasil akhir selalu hitungan server.
- **Laporan**: EOD, Sales Recap, dan HQ Monitoring memakai filter "sisi uang"
  (baris tanpa role atau dengan amount ≠ 0) sehingga mode alokasi menampilkan
  omzet di komponen dan mode lama tetap menampilkan parent — breakdown selalu
  sama dengan total header.
- **Print**: parent alokasi tercetak sebagai header grup tanpa harga; struk
  legacy tidak berubah.

---

## 5. Risiko operasional yang perlu dikelola

1. **Price list harus terawat** (R1). Bobot pembagian diambil dari Item Price.
   Harga salah/kosong/usang → total tetap benar tetapi pembagian per item
   melenceng; komponen tanpa harga sama sekali jatuh ke bobot qty. Rapikan
   Item Price komponen sebelum go-live.
2. **Laporan per item terlihat seperti diskon** (R2). Roti bisa terjual
   15.333 bukan 20.000; harga rata-rata turun karena penjualan satuan dan
   lewat paket tercampur. Beri tahu tim pembaca laporan; bila perlu, buat
   filter/kolom pemisah memakai `pos_package_role` (field sudah ada).
3. **Jangan bolak-balik toggle** (R3). Invoice sebelum/sesudah tanggal
   aktivasi memakai bentuk berbeda; laporan yang melewati tanggal itu akan
   terlihat melonjak. Perlakukan sebagai keputusan sekali jalan dengan tanggal
   go-live jelas (mis. awal bulan/periode akuntansi), lalu jangan dimatikan.
4. **Pajak per item bisa berubah** (R4). Bila Item Tax Template komponen ≠
   template paket, total pajak bisa berubah hanya karena toggle ON. Cek master
   item produksi; perubahan semantik ini juga berlaku sebaliknya saat retur.
5. **Hal yang dihitung per item ikut bergeser** (R5). Komisi kasir per produk,
   target outlet per item, poin loyalitas per item — semuanya kini melihat
   nilai komponen, bukan nilai paket.
6. **Aturan retur lebih kaku** (R6). Pelanggan tidak bisa mengembalikan satu
   komponen saja bila proporsinya tidak sama dengan isi paket. Disengaja;
   masukkan ke SOP outlet.
7. **Dua implementasi harus sinkron** (R7). Python (server) dan JS (offline
   preview) dipin oleh `packageAllocation.cases.json`; jika rumus diubah,
   ubah py + js + cases.json bersamaan. Dampak terburuk drift hanya preview
   beda dari server, karena keputusan akhir tetap server.
8. **Temuan kecil struk (pre-existing)** (R8). "Total Qty" di print format
   menjumlahkan semua baris termasuk header paket → 1 paket berisi 3 barang
   tercetak "Total Qty: 4". Sudah ada sejak mode lama, mudah diperbaiki
   (kecualikan baris `pos_package_role = 'Package'` dari penjumlahan) bila
   mengganggu.

---

## 6. Syarat go-live

Fitur layak dinyalakan dengan tiga syarat:

1. Item Price semua komponen dirapikan dulu (R1).
2. Toggle dinyalakan di awal periode, lalu tidak dimatikan lagi (R3).
3. Tim pembaca laporan diberi tahu harga per item sudah termasuk penjualan
   lewat paket (R2), termasuk perubahan semantik pajak per item (R4).

Langkah teknis deploy: `bench migrate` di semua site yang dipakai kasir (field
toggle + print format), build bundle SPA, lalu nyalakan toggle di POS Next
Global Settings. Uji dulu memakai [panduan manual](POS_PACKAGE_MANUAL_TEST.md).

---

## 7. Rollback

- Toggle OFF = seluruh jalur legacy byte-for-byte (tertes). Invoice baru kembali
  ke bentuk lama; invoice alokasi yang sudah terbit tetap dibaca/diretur
  benar (marker snapshot membedakan).
- Batalkan total: `git checkout main && git branch -D feature/pos-package`
  (atau revert per commit). Satu-satunya perubahan skema adalah field Single +
  bump print format — revert file + `bench migrate` mengembalikan; tidak ada
  backfill data.
