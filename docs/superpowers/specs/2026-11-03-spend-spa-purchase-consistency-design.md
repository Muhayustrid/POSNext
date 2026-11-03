# POS Spend SPA — Penyajian Konsisten dengan PO/PR

Tanggal: 2026-11-03
Status: Arah percakapan disetujui; spec tertulis menunggu peninjauan. Belum ada implementasi UI.

## Tujuan

Kasir mencatat pembelian lokal dan pengeluaran tanpa harus memahami kode Item atau ID Supplier. Penyajian Purchases & Expenses mengikuti Purchase Order/Purchase Receipt yang sudah dipakai dalam SPA, bukan menciptakan identitas visual baru.

User menyetujui halaman awal berupa riwayat dengan tombol Baru, lalu form terpisah. Nama barang menjadi informasi utama, kode sebagai pendamping, dengan pencarian melalui keduanya. Pengeluaran langsung dan pembelian barang tetap dibedakan oleh Template.

Mode: Operate; code-led, menggunakan dunia visual incumbent. Acuan: `docs/UI_GUIDELINES.md`, PurchaseOrderDialog, PurchaseReceiptDialog, PurchaseAttachments dan AutocompleteSelect. Tidak menambahkan dependency atau merombak tampilan global.

## Kondisi yang diperiksa

- SpendDialog saat ini membuka tab New, dengan riwayat di tab terpisah.
- `get_spend_templates` mengirim `items` berupa daftar kode, bukan metadata nama/satuan.
- PO memakai AutocompleteSelect dengan label nama barang dan pencarian supplier.
- `purchase_orders.search_suppliers` hanya mencari supplier internal; tidak sesuai untuk dipakai mentah pada pembelian lokal Spend.
- Spend menyimpan attempt transaksi pada parent melalui v-model. Respons hilang harus diulang memakai request dan payload asli.
- Pekerjaan Company Scope/refactor sebelumnya masih belum di-commit. Jangan menyatakan commit selesai atau mencampur otorisasi commit dengan pekerjaan ini.

## Alur dan navigasi

### Riwayat sebagai awal

Saat menu dibuka normal, tampilkan daftar riwayat company yang dipilih. Toolbar berisi pencarian, Refresh dan aksi utama Baru, mengikuti PO/PR. Pencarian lokal hanya pada hasil riwayat yang telah dimuat: nomor dokumen, nama Template, nama toko/nomor nota dan sumber bayar; jangan mengklaim pencarian seluruh arsip. Tidak menambahkan pagination/filter tanggal baru atau merombak kontrak riwayat dalam pekerjaan ini.

Desktop mengikuti urutan ID, tujuan/Template dan pihak, tanggal, nominal, status, aksi. Header dan baris memakai grid yang sama. Mobile menggunakan susunan kartu PO/PR: ID/status, keterangan, tanggal/nominal, lalu aksi. Lampiran dan pembatalan yang sudah didukung tetap tersedia. Tidak membuat tampilan detail baru hanya untuk meniru semua fungsi PO.

Sediakan loading, riwayat kosong, tidak ada hasil pencarian, error dan retry yang jelas. Jika company/profile belum tersedia, tampilkan petunjuk pilih company/profile sesuai konteks, bukan form kosong yang terlihat dapat digunakan.

### Form terpisah

Tombol Baru membuka form dengan tombol kembali, judul, dan aksi utama di header. Tidak ada aksi sticky footer baru. Pilih Template terlebih dahulu; sebelum terpilih tampilkan instruksi singkat tentang langkah berikutnya, bukan tabel kosong atau form yang belum bermakna.

Setelah Template dipilih, kelompokkan:
1. Jenis pencatatan/Template dan sumber pembayaran; penanda sumber kas shift tetap jelas.
2. Supplier (PI), nama toko/vendor, nomor dan tanggal nota sesuai aturan Template.
3. Tabel barang atau tabel pengeluaran.
4. Catatan dan bukti/lampiran.

Input wajib ditandai berdasarkan aturan nyata. Supplier tetap terlihat sebagai nilai nama ketika Template tidak mengizinkan perubahan. Jangan menjadikan nama toko sebagai pengganti Supplier akuntansi atau menambahkan pembuatan Supplier dari SPA.

Kembali dari form yang sudah diisi meminta konfirmasi buang perubahan; jika ditolak semua input tetap ada. Setelah submit berhasil, kembali ke daftar, muat ulang riwayat dan tampilkan keberhasilan dengan nomor dokumen. Tidak membuat konsep draft server baru.

## Input barang dan pengeluaran

### Pembelian (Purchase Invoice)

Gunakan AutocompleteSelect yang sudah ada dengan opsi hanya dari item yang diizinkan Template. Nama barang utama; kode dan stock UOM sebagai informasi pendamping. Cari nama atau kode. Nama yang sama tetap bisa dibedakan dengan kode. Label input harus terhubung dengan input aktual, bukan sekadar teks visual.

Tabel desktop: No., Barang, Jumlah, Harga Satuan, Subtotal, Hapus. Stock UOM ditampilkan sebagai konteks jumlah, bukan dropdown konversi baru. Subtotal hanya baca; angka rata kanan dan tabular. Tambah Baris di footer kiri, total di kanan. Tidak menambahkan bulk-delete atau pemilihan baris bila tidak dibutuhkan.

Mobile menjaga label setiap input dan total baris terbaca, tanpa scroll horizontal seluruh layar. Dropdown tidak ditempatkan dalam ancestor overflow yang memotong opsi. Tombol minimal 44px pada ponsel dan 40px pada tablet.

### Pengeluaran (Journal Entry)

Tabel berisi No., Keterangan, Nominal, Hapus. Tidak menampilkan barang, jumlah atau harga satuan. Total tetap jelas dan memakai format uang incumbent.

Nilai uang/qty yang dikirim tetap mengikuti kontrak lama; nama dan satuan adalah metadata tampilan. Jangan menambahkan harga otomatis, konversi UOM, pajak atau pembelian stok.

## Data nama barang dan supplier

### Metadata Template

Pertahankan `items: list[str]` agar pemanggil lama tidak rusak. Tambahkan `item_details` berisi `item_code`, `item_name`, `stock_uom` untuk kode yang sudah diizinkan Template, serta `default_supplier_name` untuk supplier default bila ada. Ambil metadata item secara batch, bukan query tambahan per baris.

Penambahan metadata tidak melonggarkan daftar item. Submit tetap memeriksa kode terhadap `allowed_items`; label dari klien tidak dipercaya. Jika metadata tidak tersedia, kode tetap menjadi fallback yang jujur, bukan nama rekaan. Jangan mengambil katalog global untuk mengisi dropdown ini.

### Pencarian Supplier Spend

Tambahkan pencarian terbatas di `pos_next.api.spend` dengan company, pos_profile, Template, search_term dan hasil maksimum tetap 20. Jalankan scope pengguna, otorisasi Template, dan pastikan Template PI serta mengizinkan perubahan Supplier. Cari Supplier aktif berdasarkan nama atau ID; tidak dibatasi internal karena Spend membeli secara lokal. Jangan mengubah pencarian Supplier PO.

Hak akses Supplier untuk kasir tidak perlu diperluas di DocPerm. Endpoint khusus hanya mengembalikan identitas nama/ID Supplier setelah otorisasi Spend; tanpa rekening, saldo, kontak, alamat atau data lain. Ini mengikuti kemampuan submit Spend yang sudah mengizinkan pilihan Supplier saat Template mengizinkannya. Nama default diprime agar field langsung menampilkan nama sebelum pengguna mengetik.

Pencarian debounce mengikuti pola PO, dengan pengabaian respons basi saat company/profile/Template/term berganti dan pembatalan timer ketika unmount. Kegagalan pencarian terlihat dan bisa dicoba ulang. Perubahan supplier dilarang pada form terkunci karena attempt belum terselesaikan.

## Retry, pergantian konteks, dan keamanan

Pertahankan client_request_id dan payload attempt asli. Jika respons submit hilang, tampilkan form pemulihan attempt, bukan membiarkan kasir membuat transaksi Baru yang terlihat independen. Nama/metadata untuk tampilan tidak mengubah payload frozen.

Attempt belum terselesaikan tidak boleh dihapus melalui Kembali, Reset atau tombol Baru. Jika company berganti, tampilkan konteks attempt asal dan cegah submit atas nama company baru; retry hanya memakai konteks asal sesuai perlindungan yang sudah ada. Jangan mengklaim persistensi lintas page reload baru.

Saat belum ada attempt dan submit tidak berlangsung, navigasi form mengikuti konfirmasi buang input. Guard respons basi riwayat/Template tetap dipertahankan. Offline menonaktifkan submit dan pencarian remote dengan pesan yang jelas; tidak membuat antrean offline Spend.

Tidak mengubah otorisasi submit/cancel, urutan lock, atomisitas lampiran, GL, closing atau batas nominal. Pencarian dan metadata wajib tetap berada di scope yang diotorisasi.

## Berkas dan interface terdampak

- `POS/src/components/spend/SpendDialog.vue`: alur daftar/form, input, tabel, state navigasi, reuse AutocompleteSelect/RefreshButton/ConfirmDialog sesuai interface yang sudah diverifikasi.
- `POS/src/components/spend/SpendDialog.test.js`: alur baru, nama barang, pencarian, payload dan retry.
- `POS/src/components/pos/POSMenuDialog.vue` dan tesnya: hanya jika wiring attempt atau perilaku navigasi menuntut penyesuaian; jangan mengubah shell global.
- `pos_next/api/spend.py` dan `pos_next/api/test_spend.py`: metadata additive dan pencarian Supplier yang diotorisasi.
- `pos_next/translations/id.csv`: string baru append-only, byte lama dipertahankan.
- Bukti visual dan hasil tes di docs/proofs setelah implementasi.

Tidak perlu schema atau migrasi tambahan untuk UI ini; deployment tetap melalui backup/rebuild dev dan cache sesuai runbook. Company Scope yang dikerjakan sebelumnya tetap membutuhkan migrasinya sendiri.

## Kriteria penerimaan dan verifikasi

1. Buka menu: daftar riwayat, Refresh/Baru dan state kosong/loading/error konsisten dengan PO/PR.
2. Baru/Kembali/konfirmasi discard bekerja; form tidak menghilang diam-diam. Submit sukses kembali ke riwayat.
3. Item dengan nama berbeda dari kode ditampilkan dan dicari lewat nama; kode sekunder dan stock UOM terlihat; payload tetap kode/qty/rate.
4. Item dengan nama sama dibedakan oleh kode; item di luar Template tetap ditolak server.
5. Supplier default tampil sebagai nama; pencarian mencakup supplier eksternal aktif, tidak supplier disabled. Company/role/Template asing ditolak, dan Template yang tidak mengizinkan perubahan tidak dapat memakai pencarian ini.
6. Tabel JE tidak memuat kolom barang; tabel PI memiliki subtotal dan total benar setelah tambah/hapus/edit baris.
7. Respons lookup yang terlambat tidak mengganti opsi konteks baru. Timer dibersihkan. Attempt retry tetap identik setelah respons hilang dan navigasi/reopen menu.
8. Uji backend terfokus secara serial pada fix, tes SpendDialog/POSMenuDialog/PO/PR, full frontend suite dan build sesuai toolchain container. Laporkan baseline secara terpisah jika ada.
9. Pemeriksaan render pada 1280 desktop, 820 tablet, 390 ponsel dan lebar pengguna 2000: daftar kosong/berisi, form sebelum Template, PI, JE, dropdown terbuka, error, lampiran, cancel, serta pemulihan retry. Gunakan harness berdata sintetis bila sesi backend tidak dapat menjalankan state tanpa transaksi; jangan mengklaim harness sebagai E2E backend.
10. Jalankan detector Impeccable sekali sesudah UI selesai; finish review independen memakai screenshot yang benar. Jangan membuat reviewer worktree yang mencoba membaca Git checkout lain untuk melewati pembatasan.

## Direction contract

THESIS: Make Spend feel like the existing purchase workflow, with names people recognize instead of identifiers as the task's main language.

OWN-WORLD: Inherit PO/PR neutral white/gray surfaces, existing solid primary actions, bordered rounded tables, status pills and shared purchase controls; no new palette, typeface or global shell.

STORY: Start from recorded spending, create a new entry, choose its purpose, enter the right human-readable fields, inspect the total and record it once.

FIRST VIEWPORT: A max-w-6xl purchase-style list with compact toolbar and New action; the form replaces the list, with Back and primary action in its header, grouped transaction fields above the editable table. No oversized blank title region or dashboard KPIs. Signature interaction is name-first item/supplier lookup with visible secondary identifiers; motion follows existing controls without decorative animation.

FORM: Precisely user-pinned PO/PR convention; no concept tournament. seed: none (user-pinned; no roll required).

FINISH: unreviewed and undocumented is unfinished; this build ends with the finish review, the verdict, DESIGN.md, and every shipping raster carrying its provenance

For this incumbent extension, preserve existing system artifacts; no new visual-world DESIGN.md or shipping raster is required merely because a file is absent. Record the surface contract with the Impeccable surface-brief command before UI edits.

## Gerbang berikutnya

Peninjauan dan persetujuan spec tertulis ini, lalu rencana implementasi dan metode eksekusi. Commit sebelumnya tetap menunggu persetujuan pesan yang tepat; tidak ada otorisasi commit/push baru dari persetujuan desain UI.
