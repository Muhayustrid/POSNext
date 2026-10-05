# Panduan Uji Manual E2E — Alokasi Harga Paket POS

Panduan langkah-demi-langkah untuk memverifikasi
`enable_pos_package_allocation` (POS Next Global Settings) di situs uji.
Toggle default **OFF** — semua perilaku lama tidak berubah sampai dinyalakan.

Semua angka contoh memakai paket 23.000 (Roti 20.000 + Teh 10.000) pada site
berpresisi IDR 0 desimal. Kalau site uji memakai presisi lain, angka mengikuti
aturan: rate_i = bobot_i / total_bobot × harga_paket, dibulatkan ke presisi
site, sisa pembulatan dibebankan ke baris **qty 1 dengan bobot terbesar di
atas 0** (carrier) — invariant akhir tetap `Σ(rate × qty) == harga_paket ×
qty paket`. Baris qty 1 yang bobotnya 0 (tanpa harga / tanpa price list)
**tidak** boleh jadi carrier (akan menghasilkan rate negatif) — paket
seperti itu gagal tertutup (fail-closed) di checkout, bukan menjual angka
salah. Saat toggle ON, POS Package yang tak bisa menghasilkan baris qty 1
(semua komponen qty > 1 dan tanpa opsi Qty Per Unit 1) ditolak saat Save.

## 0. Persiapan

1. Situs uji (mis. `roti-posnext-test.localhost:8001`), login kasir, buka POS,
   buka shift.
2. **Migrate semua site yang dipakai kasir**: site uji
   (`roti-posnext-test.localhost`) dan `posnext.localhost` (device IP masuk ke
   site ini, bukan site uji) — `bench --site <site> migrate`. Field toggle ada
   di DocType JSON; tanpa migrate `_package_allocation_enabled()` diam-diam
   False (toggle tidak muncul atau tidak berefek).
3. Pastikan **POS Next Global Settings → Allocate Package Price to
   Components = OFF** (Desk → cari "POS Next Global Settings").
4. Siapkan data bila belum ada:
   - Item **Roti** (stok, sales item) dengan Item Price 20.000 pada selling
     price list profil; item **Teh** (stok, sales item) harga 10.000.
   - POS Package "Paket Roti Teh": parent item non-stok, base price 23.000,
     komponen Roti qty 1 + Teh qty 1, outlet = Company/Warehouse profil.
5. Catat stok awal Roti dan Teh (Bin / Stock Ledger) untuk pembanding.
6. Cek presisi site: `System Settings → currency_precision = 0` dan number
   format IDR `#.###`. Tanpa ini, 15333/7667 menjadi pecahan presisi site.

## (a) Toggle ON — jual paket 23k

1. Desk → POS Next Global Settings → centang **Allocate Package Price to
   Components** → Save. **Reload POS** — setting dibaca saat bootstrap
   (`loadSettings`), tidak ada cache TTL.
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
   **Sales Register** periode hari ini — pendapatan 15.333 tertulis di Roti
   dan 7.667 di Teh (bukan 23.000 di parent). Paket parent tidak muncul
   sebagai penjualan item.
7. Print/preview struk: baris parent tampil sebagai **header grup tanpa
   harga** (nama paket bold, kolom kanan kosong); Roti 15.333 dan Teh 7.667
   tampil sebagai baris berharga. Cek juga "Test Print" dari dialog print.

## (b) Retur penuh dan sebagian

1. Penuh: POS → **Return Invoice** → pilih invoice (a) → kembalikan semua
   baris → submit.
   - Ekspektasi: rate baris Roti = 15.333, Teh = 7.667, parent = 0
     (mengikuti rate per baris invoice asal — bukan harga hari ini, apa pun
     posisi toggle saat retur).
   - Stok Roti dan Teh kembali +1 masing-masing.
2. Sebagian: jual paket dengan komponen ber-qty 2 (mis. Paket Roti Teh 2x:
   Roti qty 2 + Teh qty 2), lalu Return Invoice **qty parent -1, komponen
   Roti/Teh masing-masing -1** (proporsi setengah). Ubah salah satu qty
   komponen (mis. Teh -2 alih-alih -1) → submit **harus ditolak** dengan
   pesan "return ... to match the package being returned".
   Catatan: skenario lama "Teh -0.5" tidak valid — UOM Nos
   *must-be-whole-number* membuat ERPNext menolak lebih dulu, sebelum guard
   paket berjalan. Pakai komponen qty 2 lalu retur qty 1 agar guard-nya
   benar-benar diuji.
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
4. Draft atau antrean offline yang membawa marker `allocation` **di-reprice
   ke bentuk lama** saat toggle OFF dan invoice di-save/sync (server
   otoritas — marker di snapshot tidak mengikat). Retur atas invoice mode
   alokasi **tetap** memakai rate komponen aslinya (rate per baris asal),
   apa pun posisi toggle.

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

## (f) Skenario lanjutan (wajib)

1. **Komponen qty 2 sebagai baris terakhir (B1).** Paket "Paket Hemat" base
   25.000, baris Roti qty 1 @ Item Price 20.000 lalu baris Teh qty 2 @ Item
   Price 5.000. Toggle ON, jual.
   - Ekspektasi: Roti rate **16.666**, Teh rate **4.167**; Σ = 16.666 +
     4.167×2 = **25.000** = `net_total` (sisa pembulatan dibebankan ke baris
     qty 1 berbobot terbesar, bukan baris terakhir). Urutan dibalik (Teh dulu, Roti terakhir)
     → 4.167 dan 16.666, Σ tetap 25.000.
   - Uji definisi: paket dengan **semua** baris qty > 1 dan tanpa opsi Qty
     Per Unit 1 → **Save ditolak** oleh validate POS Package dengan pesan
     butuh baris qty 1; ini dicegah di definisi, bukan saat kasir checkout.
   - Edge: opsi ber-Qty Per Unit 1 yang dipilih qty > 1 (mis. max 2) bisa
     menghabiskan carrier — bila sisa tidak habis dibagi, checkout ditolak
     dengan pesan "component rates total ... cannot be split exactly"
     (fail-closed, bukan angka salah).
2. **EOD + Sales Recap + HQ Monitoring setelah jual paket (B2).** Pakai
   penjualan (a), lalu buka EOD report, Sales Recap, dan HQ Monitoring.
   - Ekspektasi: daftar item menampilkan **komponen** Roti 15.333 dan Teh
     7.667 (masuk daftar *items*, bukan *packages*); baris parent bernilai 0
     **hilang**; Σ breakdown item = total header 23.000. Kategori ikut
     rekonsiliasi.
   - Mode legacy (toggle OFF): kebalikannya yang benar — parent 23.000
     muncul di daftar *packages*, komponen 0 tersembunyi. Keduanya harus
     konsisten dengan total header.
3. **Komponen duplikat + retur penuh (H2).** Paket "Paket Teh Dobel" base
   6.667, dua baris komponen **item sama** (Teh qty 1) @ Item Price 10.000.
   Toggle ON, jual → rate kedua baris berbeda satu rupiah (mis. **3.334 dan
   3.333**, Σ 6.667, karena dibulatkan per baris).
   - Retur penuh → refund **-6.667** pas = -`grand_total` asal (bukan
     -6.668 hasil rata-rata), stok Teh +2. Rate retur mengikuti rate baris
     asal, apa pun posisi toggle.
4. **Pricing Rule + Item Tax pada komponen (H3).**
   - Buat Pricing Rule diskon 10% untuk item **Roti**. Toggle ON, jual paket
     23k + 1 baris Roti standalone.
     Ekspektasi: baris Roti **di dalam paket tidak kena diskon** (rate tetap
     15.333, Σ paket tetap 23.000); baris Roti standalone tetap kena (20.000
     → 18.000). Hapus rule setelah uji.
   - Item Tax Template berbeda parent vs komponen (mis. parent 0%, komponen
     11%): checkout paket → pajak mengikuti **template komponen**; baris
     parent (rate 0) tidak membawa pajak.
   - **Perubahan semantik (Item Tax).** Dulu uang ada di baris parent
     sehingga template parent yang berlaku; di mode alokasi uang ada di
     komponen sehingga template komponen yang berlaku. Kalau tarif kedua
     template berbeda, total pajak berubah hanya karena toggle — ini
     disengaja, bukan bug; Σ item tetap rekonsiliasi dengan header.
5. **Diskon header pada cart berisi paket.** Toggle ON, jual paket 23k +
   diskon manual header 3.000 (kupon senilai sama juga boleh).
   - Ekspektasi: `grand_total` 20.000; diskon terbagi proporsional ke baris
     **komponen** (parent tetap 0 dan tanpa diskon — mis. ±2.000 Roti /
     ±1.000 Teh); Σ (rate×qty − diskon) komponen = 20.000 = `grand_total`.
     Basis alokasi (rate komponen) tidak berubah.
6. **Toggle diubah saat ada invoice di antrean offline (H1).** Antre saat
   toggle ON (marker `allocation`, preview 15.333/7.667), lalu matikan
   toggle **sebelum sync**.
   - Ekspektasi (server otoritas): invoice tersimpan dalam **bentuk lama** —
     parent 23.000, komponen 0. Marker di snapshot tidak mengikat saat
     toggle OFF.
   - Kebalikannya: antre saat toggle OFF (tanpa marker), nyalakan toggle
     sebelum sync → tetap bentuk lama; tanpa marker selalu jalur legacy.

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
