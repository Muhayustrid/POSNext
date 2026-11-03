# Integrasi SR UOM dan kerahasiaan Stock Opname POS

Tanggal: 2026-11-03
Status: Desain tertulis disetujui pengguna pada 2026-11-03 melalui konfirmasi langsung; penyusunan rencana implementasi diizinkan, implementasi belum diizinkan.

## 1. Tujuan dan batas akses

Mengintegrasikan Stock Opname POS dengan konsolidasi UOM `warehouse_app`, tanpa membocorkan harga/valuasi persediaan atau jumlah stok sistem kepada pengguna outlet.

Pengguna outlet mencakup pengguna POSNext Cashier/POSNext Manager tanpa kewenangan baca ledger menurut kebijakan opname yang sudah ada. Jangan menyamakan semua pengguna non-HQ dengan outlet: pengguna gudang native juga harus dipetakan. Pengguna dengan role HQ tetap mengikuti akses HQ yang berlaku; perubahan ini tidak mendefinisikan ulang role organisasi. Sebelum menetapkan gate, rencana wajib mencatat matriks izin existing untuk outlet-only, Stock User-only, Stock Manager-only, HQ, Cashier+Manager, outlet+Stock User, outlet+Stock Manager, dan outlet+HQ, termasuk scope dokumen dan akses ledger. Hak multi-role harus mengikuti kebijakan existing yang terverifikasi; bila kebijakannya ambigu, minta keputusan pengguna sebelum implementasi. Dalam alur opname, outlet boleh membaca identitas barang, UOM/faktor konversi, batch/serial yang memang diizinkan alur existing, foto, dan jumlah hitungan yang mereka masukkan. Outlet tidak boleh menerima valuasi, amount, jumlah sebelum/sistem, atau selisih yang memungkinkan jumlah sebelum dihitung kembali. Harga jual pada transaksi POS bukan cakupan perubahan ini.

Larangan berlaku pada respons server, bukan hanya kolom UI: API POS, endpoint kalkulasi UOM, pembacaan dokumen native yang dapat diakses outlet, serta print/export terkait Stock Reconciliation. Kebijakan juga berlaku saat outlet membaca ulang dokumen yang telah diproses HQ. Jumlah hasil hitungan yang dikonversi ke stock UOM bukan jumlah stok sistem dan tetap boleh tersedia.

## 2. Bukti dan kondisi awal

- `pos_next/overrides/stock_reconciliation.py`: kolom `custom_qty_before` dan `custom_qty_difference_per_uom` dikosongkan sebelum `super().validate()`. Blok setelah validasi sudah mengosongkan sebagian kolom native pada draft.
- `pos_next/api/stock_opname.py::_validated_rows`: menghitung native qty dan faktor UOM sendiri. Helper ini juga dipakai import untuk validasi per baris.
- `_fill_per_uom_valuation_rate` menduplikasi konversi valuasi yang menjadi tanggung jawab warehouse_app.
- Eksplorasi menemukan payload POS menggunakan allowlist dan `get_system_qty` memiliki pembatas HQ. Ini bukan bukti bahwa semua jalur native aman.
- Fixture `POSNext Cashier` dan `POSNext Manager` memberi read/write, print, export, dan report pada Stock Reconciliation. Scope warehouse hanya membatasi dokumen, bukan kerahasiaan field.
- Worktree `warehouse_app-sr-uom` pada commit `0f6fbb0` menjalankan konversi melalui extension `validate_posting_time()` dan menyediakan endpoint `compute_row`. Endpoint memeriksa izin write Stock Reconciliation, membaca ledger, lalu mengembalikan kolom before/difference. Izin write outlet membuat pemeriksaan ini tidak memadai.
- Saat eksplorasi, checkout `apps/warehouse_app` sumber build masih pada `1f40bd6`. Status tersebut harus diperiksa ulang sebelum integrasi/rilis.

## 3. Pendekatan yang dipilih

Gunakan satu implementasi matematika UOM di `warehouse_app/sr_uom.py`, pertahankan validasi khusus POS, dan perkuat batas baca data sensitif di server. Tidak membuat engine konversi kedua, endpoint POS baru tanpa kebutuhan, atau redesign UI.

Alternatif UI-only ditolak karena request API langsung tetap dapat membaca data. Mempertahankan konversi ganda ditolak karena risiko pembulatan dan hasil berbeda.

### 3.1 Alur tulis dan konversi

1. API POS memvalidasi item, batch/serial, jumlah hitungan, duplikasi, lampiran, dan scope menggunakan aturan existing.
2. Untuk custom UOM pada site yang mendukungnya, baris hanya membawa input `custom_uom` dan `custom_qty_after`, tanpa menghitung/menulis `qty` atau `custom_conversion_factor` di `_validated_rows`.
3. Validasi keberadaan/faktor UOM untuk error import per baris menggunakan resolver bersama warehouse_app, bukan query/rumus duplikat. Validasi dokumen tetap menjadi otoritas akhir; input client tidak dipercaya.
4. Extension warehouse_app menghitung qty pada posting timestamp final, sebelum proses batch/serial dan filter native.
5. Setelah `super().validate()`, blok draft non-submit mengosongkan `custom_qty_before` dan `custom_qty_difference_per_uom` bersama kolom blind-count existing. Draft tetap mempertahankan seluruh baris hitungan.
6. Hapus `_fill_per_uom_valuation_rate` dan pemanggilnya. POS tidak membutuhkan tampilan harga. Jika Desk pengguna berwenang memerlukan display rate, gunakan kemampuan warehouse_app yang sudah ada, bukan helper POS pengganti.
7. Saat submit, jangan menghapus nilai yang diperlukan ledger/valuasi; lindungi pembaca outlet pada batas baca, bukan merusak data akuntansi.

### 3.2 Kompatibilitas dan import

- Tanpa custom field/warehouse_app, hitungan stock UOM tetap bekerja; non-stock UOM ditolak dengan pesan jelas. Input yang secara eksplisit memilih stock UOM tetap diperlakukan sebagai hitungan native jika field custom tidak tersedia.
- Import warehouse_app hanya dilakukan secara lazy pada jalur yang membutuhkan dukungannya. Field custom yang tertinggal setelah aplikasi dilepas tidak boleh menyebabkan jumlah non-stock dianggap sebagai native secara diam-diam: gagalkan dengan pesan konfigurasi yang jelas.
- Import Excel tetap melaporkan unknown UOM dengan nomor baris dan item, tanpa mengubah kontrak pengumpulan error existing.
- Serial tetap dihitung dari serial, bukan custom UOM. Batch menggunakan identitas batch yang benar; perhitungan saldo per batch tetap milik warehouse_app.
- `ignore_validate=True` tidak didukung untuk konversi; jangan menambah jalur penyimpanan yang melewati validasi.

### 3.3 Kerahasiaan dan otorisasi

**Respons API POS:** pertahankan allowlist. Uji field sensitif pada respons create/save/read/import, bukan hanya pembacaan satu draft. Selisih dan total amount juga termasuk data sensitif.

**Endpoint UOM warehouse_app:** tambahkan pembatas server sebelum pembacaan ledger. Ketika pos_next terpasang, pengguna outlet tanpa kewenangan ledger ditolak berdasarkan matriks role existing yang telah diverifikasi; pengguna HQ/gudang yang berwenang masih wajib lolos izin native dan pembatas scope yang berlaku. Jangan menolak seluruh pengguna non-HQ secara membabi buta. Ketika pos_next tidak terpasang, pertahankan izin native warehouse_app. Integrasi opsional dilakukan lazy agar warehouse_app tidak memiliki ketergantungan instalasi wajib terhadap pos_next. Helper kalkulasi internal tetap dapat digunakan proses validasi server; pembatas diterapkan pada akses endpoint, bukan matematika murninya.

**Seluruh jalur baca terkait opname:** inventarisasi endpoint native maupun override `get_items`, download template stock-count, validasi/import stock-count, endpoint kalkulasi, dan semua respons save/import yang dapat mengandung stok atau rate. Uji pemanggilan langsung dengan kredensial outlet nyata pada data sintetis, bukan hanya pemanggilan dari UI atau mock permission.

**Pembacaan native:** sebelum implementasi perlindungan, petakan permission level field native/custom dan perilaku serialization, print, export, serta report pada versi Frappe terpasang. Gunakan mekanisme permission native yang tersedia untuk menolak akses field/jalur sensitif bagi outlet. Jangan menganggap redirect Desk atau pembatas gudang sebagai kontrol field. Jangan membuat scrubber global atau mengubah core Frappe/ERPNext.

Rencana implementasi wajib menetapkan mekanisme native yang konkret berdasarkan pemeriksaan tersebut. Jika mekanisme native tidak dapat menjamin kerahasiaan tanpa perubahan kontrak akses yang lebih besar, hentikan tahap itu dan ajukan revisi desain; jangan mengklaim kontrol sudah selesai hanya karena UI aman. Pengurangan izin print/export/report yang benar-benar diperlukan untuk menutup jalur sensitif harus dibatasi pada Stock Reconciliation outlet, dengan akses HQ/gudang tetap dipertahankan.

Dokumen existing juga harus aman melalui batas baca yang sama. Tidak melakukan mass-update/penghapusan nilai historis atau perubahan ledger.

## 4. Cakupan perubahan

- `pos_next/api/stock_opname.py`: kontrak baris UOM, resolver validasi, kompatibilitas dan import.
- `pos_next/overrides/stock_reconciliation.py`: urutan blanking dan penghapusan helper valuasi duplikat.
- Setup/fixtures/permission hooks opname yang terbukti diperlukan untuk kerahasiaan native; lokasi tepat ditetapkan setelah pemeriksaan metadata dan framework.
- Tes `pos_next/api/test_stock_opname.py`, `pos_next/tests/test_stock_opname_override.py`, serta tes permission/setup yang relevan.
- Endpoint `warehouse_app/warehouse_app/stock_reconciliation.py::compute_row` beserta tes aksesnya; perubahan terkoordinasi di repo warehouse_app.
- Tes frontend opname hanya bila perlu menegaskan kontrak/UI; tidak merombak komponen.
- Spec, rencana, bukti verifikasi, dan local project state.

Tidak mengubah core, role HQ organisasi, alur penjualan, harga jual, ID offline, data produksi, atau menambah dependency. Tidak mengganti override pos_next menjadi extension maupun menambah override kedua.

## 5. Kriteria penerimaan dan pengujian

### Konversi dan integritas

- Dua Pack dengan faktor 12 tersimpan sebagai qty native 24 melalui validasi dokumen, bukan `_validated_rows`.
- Save/reload berulang tidak menyebabkan drift qty atau valuasi; gunakan kasus 10 stock units versus 0.833 Pack dan rate presisi tinggi pada lapisan integrasi yang relevan.
- Perubahan hitungan benar-benar menghasilkan qty baru; nilai nol sah mengikuti aturan existing.
- Batch yang berbeda tidak memakai total warehouse sebagai saldo batch.
- UOM invalid, serial dengan custom UOM, dan input jumlah invalid tetap ditolak; error import tetap per baris.
- Site tanpa dukungan custom UOM tetap memproses native qty, menolak konversi yang tidak didukung.
- Native valuation/ledger saat submit tetap benar, termasuk alur approval existing.

### Kerahasiaan

- Draft custom UOM yang disimpan outlet memiliki `custom_qty_before == 0` dan `custom_qty_difference_per_uom == 0` di DB setelah validasi aktual.
- Respons save/reload/import outlet tidak memuat field stok sebelum, harga/valuasi, amount, atau selisih; uji juga dokumen setelah diproses HQ.
- Outlet tidak dapat memperoleh ledger melalui endpoint `compute_row`; penolakan terjadi sebelum ledger dibaca.
- Outlet tidak dapat memperoleh data sensitif melalui read native, print/export/report yang tersedia. Tes menggunakan role outlet nyata dan dokumen dalam scope, sehingga tidak lolos hanya karena dokumen di luar scope.
- HQ yang berizin tetap dapat membaca data yang dibutuhkan, dan pengguna gudang sah tidak kehilangan alur native yang diperlukan.
- Draft dengan hitungan sama dengan sistem tidak kehilangan baris atau mengungkap kondisi tersebut.

Tes backend dijalankan serial dalam lingkungan Docker uji yang diverifikasi aman, bukan menulis fixture ke dev/prod hasil restore. Unit mock tidak menggantikan bukti MRO/validasi/permission pada runtime kedua aplikasi. Status tes historis upstream bukan bukti integrasi terbaru.

## 6. Tahapan dan pembagian subagent

1. Persetujuan spec tertulis ini.
2. Susun rencana implementasi konkret: petakan native permission/serialization dan runtime test; tentukan file, tes RED/GREEN, integrasi kedua repo, dan urutan pekerjaan.
3. Pengguna meninjau rencana; metode yang diinginkan adalah eksekusi subagent.
4. `sonnet-max` mengerjakan implementasi/test dalam paket tugas terfokus. Perubahan pada file yang sama tidak dikerjakan paralel. Sesi utama memverifikasi hasil dan integrasi.
5. `opus-xhigh` melakukan review independen khusus correctness, kerahasiaan, dan kompatibilitas kedua aplikasi.
6. Verifikasi ulang temuan/fix, catat bukti dan batas pemeriksaan.

Koordinasi ownership dikonfirmasi oleh sesi `warehouse-app-58`: sesi ini memiliki perubahan pos_next; sesi warehouse_app memiliki endpoint dan access tests di worktree `warehouse_app-sr-uom`, branch `feat/sr-uom-consolidation`, dengan follow-up berbasis `0f6fbb05ddd564532bf1834a4bf63559021a7180`. Menurut sesi tersebut commit sudah pushed tetapi belum merged/deployed; status remote/integrasi harus diverifikasi sebelum rilis. Jangan mengedit checkout/worktree milik sesi lain. Setelah rencana disetujui, bagikan matriks role/access dan kontrak endpoint konkret sebelum implementasi warehouse_app. Kesepakatan ownership bukan izin implementasi atau deployment.

Tidak ada commit atau push otomatis; pesan commit tetap memerlukan persetujuan tersendiri.

## 7. Gerbang deployment

- Jangan deploy salah satu perubahan sendirian.
- Pastikan perubahan warehouse_app dari worktree sudah masuk sumber build yang benar melalui integrasi Git yang disetujui; jangan menimpa checkout atau perubahan pengguna.
- Sebelum rollout, buktikan pengujian kedua aplikasi dengan kombinasi versi yang akan dibangun dan verifikasi bahwa endpoint baru terlindungi.
- Backup sebelum rebuild dev; ikuti `docker/erp` dan verifikasi migration/cache serta runtime permission sesudah rollout terkoordinasi.
- Rollout tidak boleh membuka jeda ketika endpoint baru sudah aktif tetapi proteksi/metadata belum diterapkan; rencana operasional harus mengatur urutan tersebut.
- Prod memerlukan izin terpisah. Data historis, volume, password, dan konfigurasi produksi tidak diubah untuk smoke test.

## 8. Batas verifikasi saat penulisan

Dokumen didasarkan pada pembacaan kode, fixture, handoff dan eksplorasi read-only. Belum ada pengujian runtime, perubahan kode produk, atau bukti bahwa permission aktif pada site sama dengan fixtures. Mekanisme field-level native dan seluruh jalur print/export/report harus diverifikasi pada tahap perencanaan sebelum pekerjaan implementasinya dapat dijalankan.
