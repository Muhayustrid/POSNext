# Ringkasan Sesi Berjalan (Riwayat Invoice)

Laporan terstruktur pra-penutupan untuk shift yang sedang aktif, tampil di
dialog **Riwayat Invoice** (tab **Ringkasan Sesi** di samping tab
**Transaksi**). Dibangun dari dataset yang sama persis dengan POS Closing
Shift, hanya baca-saja — tidak ada perubahan finansial atau penutupan
otomatis.

## Struktur Laporan (urutan tetap)

1. **Info Sesi** — nama shift (grup saat ini selama shift terbuka, jika tidak
   ada: nama Profil POS + ID opening shift), nama lengkap kasir, waktu buka
   aktual, waktu tutup: **aktual** (dari POS Closing Shift terlink) /
   **estimasi** (dari `pos_schedule_deadline` yang dibekukan saat shift
   dibuka) / **"—" + "Belum ditutup"**. Tidak pernah menebak waktu.
2. **Ringkasan Penjualan** — Total Penjualan (`net_sales` = Σ
   `base_grand_total` invoice terlink, **setelah retur, termasuk pajak**),
   Total Order (`sales_count` = jumlah invoice penjualan terkirim saja, retur
   tidak dihitung), Rata-rata/Order = Total Penjualan ÷ Total Order.
   Sekunder: Penjualan Kotor (sebelum retur), piutang tempo, rincian per mata
   uang bila lebih dari satu (tidak pernah dijumlahkan lintas mata uang).
3. **Ringkasan Kas** — Saldo Awal (semua baris opening shift bertipe kas),
   Penerimaan Kas (total bersih metode bertipe kas termasuk retur tunai dan
   setelah pengurangan kembalian), Pengeluaran, Kas di Tangan = Saldo Awal +
   Penerimaan Kas. **Belum ada sumber data pengeluaran per shift di aplikasi
   ini** (cetak closing juga masih 0): Pengeluaran tampil **"Belum
   tercatat"**, dan Kas di Tangan diberi keterangan **"saldo sebelum
   pengeluaran"** — tidak ada angka palsu.
4. **Metode Pembayaran** — **semua** metode yang terdaftar di Profil POS
   (termasuk yang belum terpakai, tampil 0), ditambah metode yang terpakai
   tapi sudah tidak terdaftar (diberi label). Klasifikasi tunai/non-tunai
   dari **`Mode of Payment.type`, bukan nama metode**. Total Tunai, Total
   Non-Tunai, Grand Total (semuanya penerimaan bersih, tanpa saldo awal,
   dalam mata uang perusahaan).
5. **Biaya & Potongan** — baris biaya lain (mis. service charge) ditampilkan
   **per nama akunnya**; Pajak/PPN hanya dari akun bertipe `Tax` di baris
   `Sales Taxes and Charges` invoice (klasifikasi mengikuti **tipe akun**,
   bukan kata kunci nama). Diskon = diskon level item
   ((`price_list_rate` − `rate`) × qty) + diskon level invoice
   (`base_discount_amount`); keduanya sudah termasuk dalam net total, tidak
   dihitung ganda.
6. **Refund** — total uang kembali (ABS Σ `base_grand_total` invoice retur
   yang uangnya benar-benar lewat laci; retur kredit tanpa baris pembayaran
   tidak dihitung karena tidak menggerakkan uang — aturan sama dengan
   closing).
7. **Penjualan per Kategori** — tabel utama: grup item snapshot di baris item
   invoice (fallback `Item.item_group`), komponen paket dikecualikan
   (pendapatan paket di baris induknya), retur tampil negatif, nilai bersih
   **tanpa pajak**. Dibatasi 20 kategori teratas, sisanya diberi catatan;
   total sesi selalu dihitung dari seluruh data, bukan dari tabel.
8. **Detail Item & Paket** — sekunder, bisa dilipat: tabel paket dan item
   terjual (maks 100 baris teratas per pendapatan, dengan catatan bila
   terpotong).

## Fungsi

- **Endpoint**: `pos_next.api.shifts.get_session_summary(opening_shift)`.
  Hanya pemilik shift (atau pengguna dengan izin baca POS Opening Shift per
  dokumen) yang bisa melihat; shift menentukan perusahaan/profil.
- **Dataset**: Sales Invoice `docstatus = 1` tertaut ke shift
  (`posa_pos_opening_shift`), sama dengan closing. Draft/void tidak
  dihitung. Semua agregasi dalam mata uang perusahaan; payment entry
  pelunasan menyusul ikut terhitung.
- Endpoint melakukan ~18 query agregat tetap (tanpa N+1) dan tidak menulis
  apa pun.
- Data diambil saat tab dibuka dan lewat tombol refresh manual; tidak ada
  polling. Saat offline/gagal refresh, snapshot terakhir tetap tampil dengan
  catatan kuning "data dari pembaruan terakhir" — tidak ada angka palsu.

## Batasan (jujur ke pengguna)

- **Pengeluaran kas**: belum ada doctype/sumber otoritatif pengeluaran per
  shift → tampil "Belum tercatat", bukan 0, dan Kas di Tangan dilabeli
  estimasi sebelum pengeluaran. Tidak menjumlahkan expense perusahaan
  sembarangan.
- **Service charge vs pajak**: dipisah hanya bila akunnya bertipe `Tax`
  (masuk Pajak/PPN) atau bukan (masuk baris "Biaya lainnya" per akun). Bila
  situs tidak menyetel tipe akun, biaya tampil dengan nama akunnya — tidak
  ditebak dari kata kunci nama.
- **Waktu tutup**: tanpa closing shift dan tanpa jadwal, tampil "—"/"Belum
  ditutup", tanpa waktu rekaan.
- Nama grup pada Info Sesi hanya tampil selama shift terbuka (grup terbaca
  dari profil saat ini, jadi tidak diklaim sebagai sejarah shift lama).

## Uji

- Backend: `pos_next/api/test_session_summary.py` — aritmetika (silang-cek
  dengan builder closing), retur, paket/komponen, pelunasan Payment Entry,
  kembalian kas, multi-mata uang, izin, shift kosong/tidak ada, info shift
  (grup/kasir/tutup aktual & estimasi), pengeluaran tidak didukung, semua
  metode pembayaran (nol + tak terdaftar + total tunai/non-tunai), klasifikasi
  biaya per tipe akun, diskon item+invoice, kategori (grup, retur negatif).
- Frontend: `POS/src/components/sale/SessionSummary.test.js` — urutan
  seluruh seksi, info shift, caption penjualan, kas ("Belum tercatat"),
  metode pembayaran (nol/tak terdaftar/total), biaya & diskon, refund,
  kategori (termasuk "Tanpa Kategori" dan catatan batas tabel), detail
  collapsible, responsive & tabular-nums, loading/error/stale/refresh,
  tab dialog.
