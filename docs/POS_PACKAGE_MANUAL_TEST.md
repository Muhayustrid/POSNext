# Panduan Uji Manual E2E — Alokasi Harga Paket POS

Panduan langkah-demi-langkah untuk memverifikasi
`enable_pos_package_allocation` (POS Next Global Settings) di situs uji.
Toggle default **OFF** — semua perilaku lama tidak berubah sampai dinyalakan.

Semua angka contoh memakai paket 23.000 (Roti 20.000 + Teh 10.000) pada site
berpresisi IDR 0 desimal. Kalau site uji memakai presisi lain, angka mengikuti
aturan: rate_i = bobot_i / total_bobot × harga_paket, dibulatkan ke presisi
site, sisa pembulatan ke baris terakhir — invariant akhir tetap
`Σ(rate × qty) == harga_paket × qty paket`.

## 0. Persiapan

1. Situs uji (mis. `roti-posnext-test.localhost:8001`), login kasir, buka POS,
   buka shift.
2. Pastikan **POS Next Global Settings → Allocate Package Price to
   Components = OFF** (Desk → cari "POS Next Global Settings").
3. Siapkan data bila belum ada:
   - Item **Roti** (stok, sales item) dengan Item Price 20.000 pada selling
     price list profil; item **Teh** (stok, sales item) harga 10.000.
   - POS Package "Paket Roti Teh": parent item non-stok, base price 23.000,
     komponen Roti qty 1 + Teh qty 1, outlet = Company/Warehouse profil.
4. Catat stok awal Roti dan Teh (Bin / Stock Ledger) untuk pembanding.
5. Cek presisi site: `System Settings → currency_precision = 0` dan number
   format IDR `#.###`. Tanpa ini, 15333/7667 menjadi pecahan presisi site.

## (a) Toggle ON — jual paket 23k

1. Desk → POS Next Global Settings → centang **Allocate Package Price to
   Components** → Save. Tunggu ±10 detik (cache frontend) atau reload POS.
2. Di POS, tambah "Paket Roti Teh" ke cart (harga tampil 23.000).
3. Checkout tunai 23.000 → submit.
4. Verifikasi invoice (Desk → POS Invoice / Sales Invoice, sesuai
   `invoice_type`):
   - Baris parent (Paket Roti Teh): **rate 0**, qty 1.
   - Baris Roti: **rate 15.333**; baris Teh: **rate 7.667**.
   - `Σ(rate × qty) = 23.000` = `net_total`; `grand_total` (tanpa pajak)
     = 23.000.
   - Snapshot parent memuat `"allocation": {"mode": "proportional",
     "precision": 0}`.
5. Verifikasi stok: Stock Ledger Entry untuk invoice ini — **satu SLE per
   komponen** (Roti dan Teh masing-masing qty 1 keluar), **tanpa SLE untuk
   baris parent**.
6. Verifikasi laporan per item: ERPNext **Item-wise Sales Register** /
   **Sales Register** periode hari ini — pendapatan 20.000 tertulis di Roti
   dan 10.000 di Teh (bukan 23.000 di parent). Paket parent tidak muncul
   sebagai penjualan item.
7. Print/preview struk: baris parent tampil sebagai **header grup tanpa
   harga** (nama paket bold, kolom kanan kosong); Roti 15.333 dan Teh 7.667
   tampil sebagai baris berharga. Cek juga "Test Print" dari dialog print.

## (b) Retur penuh dan sebagian

1. Penuh: POS → **Return Invoice** → pilih invoice (a) → kembalikan semua
   baris → submit.
   - Ekspektasi: rate baris Roti = 15.333, Teh = 7.667, parent = 0
     (mengikuti invoice asal — bukan harga hari ini).
   - Stok Roti dan Teh kembali +1 masing-masing.
2. Sebagian: jual lagi paket 23k, lalu Return Invoice dengan **qty parent
   -1, komponen Roti/Teh masing-masing -1** (proporsi penuh). Ubah salah satu
   qty komponen (mis. Teh -0.5) → submit **harus ditolak** dengan pesan
   "return ... to match the package being returned".
3. Retur yang menghapus salah satu baris komponen → ditolak.
   Retur tanpa `return_against` (buat dari Desk) → ditolak.

## (c) Mode offline lalu sync

1. Pada POS, matikan koneksi (DevTools → Network → Offline, atau cabut
   network). Banner offline muncul.
2. Tambah paket 23k → checkout. Invoice masuk antrean lokal
   (Offline Invoices, badge pending bertambah). Preview cart memakai
   mirror alokasi lokal: Roti 15.333 + Teh 7.667 (bila cache harga
   komponen tersedia; bila tidak, fallback bobot qty).
3. Nyalakan kembali koneksi → sync otomatis (atau klik antrean → sync).
4. Buka invoice hasil sync di Desk: rate **tetap 15.333/7.667, parent 0** —
   server selalu me-re-quote dari snapshot; angka preview offline tidak
   pernah mengikat.
5. Ubah rate komponen di payload antrean (uji negatif, opsional): sync harus
   gagal dengan pesan "component rates total ... but the package price is".
   Tambah komponen asing / hapus komponen → "contents do not match its
   definition".

## (d) Toggle OFF — perilaku lama

1. Matikan toggle di POS Next Global Settings, reload POS.
2. Jual paket 23k lagi → invoice harus kembali ke bentuk lama:
   parent rate **23.000**, komponen Roti/Teh rate **0**, `net_total` 23.000.
3. Snapshot tidak memuat marker `allocation` (mode legacy).
4. Invoice yang sudah tersimpan dalam mode alokasi **tetap** dialokasi saat
   dibuka/di-save ulang (marker di snapshot menang), dan retur atas invoice
   lama tetap memakai rate komponen aslinya.

## (e) Fail-closed: pajak "On Item Quantity"

1. Buat Sales Taxes and Charges Template dengan satu baris
   **Charge Type = On Item Quantity** (mis. 5 per unit), lalu set template
   itu ke POS Profile (field "Taxes and Charges Template").
2. Dengan toggle alokasi ON, coba jual paket 23k → checkout.
3. Ekspektasi: **error, bukan angka**. Toast/pesan memuat
   "cannot be priced with allocation while tax ... is charged On Item
   Quantity — the package header would be taxed as an extra unit."
   Invoice tidak terbentuk dan stok tidak bergerak.
4. Ganti charge type ke **On Net Total** (value-based) → checkout sukses;
   parent tetap 0 dan tidak kena pajak; komponen yang menanggung pajak.
5. Hapus/lepaskan kembali template pajak dari profil setelah selesai.

## Verifikasi print (header grup tanpa harga)

- Invoice mode alokasi → Print → pilih format **POS Next Receipt**.
- Parent harus tercetak sebagai nama paket **bold tanpa angka** (kolom kanan
  kosong); komponen tampil bernilai. Ini berasal dari kondisi print format
  `pos_package_role == "Package" and not item.rate` + marker `allocation` di
  snapshot.
- Invoice mode legacy → parent tampil dengan harga 23.000 seperti biasa.

## Sisa risiko sebelum E2E

- Uji ini memakai `currency_precision=0`; simpan/restore bila site campuran.
- Item Price komponen dipakai sebagai bobot alokasi — buat/ubah harga komponen
  sebelum uji, dan catat bahwa paket dengan semua komponen berharga 0 jatuh ke
  bobot qty (harga per-unit berbeda).
- Diskon header (manual/coupon) membagi diskon proporsional ke komponen;
  basis alokasi (rate komponen) tidak berubah.
