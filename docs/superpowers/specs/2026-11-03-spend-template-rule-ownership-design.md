# POS Spend Template — Kepemilikan Aturan

Tanggal: 2026-11-03
Status: Spec dan rencana implementasi disetujui user 2026-11-03; implementasi Native di checkout saat ini diotorisasi. Commit/push belum diotorisasi.

## Tujuan dan cakupan

Hilangkan ketergantungan dua arah antara module POS Spend Template dan module resolusi akun dengan perubahan terkecil, tanpa mengubah perilaku pengguna atau interface transaksi. Manfaat utama adalah locality: aturan dasar Template mempunyai pemilik yang jelas. Perubahan ini tidak diklaim sebagai peningkatan depth yang besar dengan sendirinya.

User memilih refactor tanpa perubahan perilaku pada aturan yang ada, tanpa module atau adapter baru. Pengisian default `reference_company` ketika readiness memanggil validasi tetap dipertahankan.

Tambahan cakupan yang disetujui user: dropdown eksplisit `Selected Companies` / `All Companies`. Ini merupakan satu perubahan perilaku yang disengaja; tabel Companies kosong tidak otomatis berarti semua. Template lama mempertahankan cakupan sebelumnya.

## Bukti dan keputusan

Pada baseline `1f145cc9`:
- Controller `pos_spend_template.py` memiliki `template_companies` dan `validate_debit_account`.
- `POSSpendTemplate.validate` mengimpor `allowed_items` dari `spend_accounts.py` secara lokal.
- `spend_accounts.py` mengimpor dua aturan dari controller, sementara `check_readiness` memanggil `template.validate()`.
- `allowed_items` memperluas Item Group, menyaring item hasil ekspansi, menghapus duplikasi, dan mengurutkan hasil.

Arah yang dipilih: pindahkan implementation `allowed_items` ke module POS Spend Template yang sudah ada. Module resolusi akun menggunakan aturan Template; Template tidak lagi bergantung pada module resolusi akun.

Alternatif yang tidak dipilih:
- Memindahkan seluruh aturan ke module resolusi akun: lebih luas dan tidak dengan sendirinya menghapus hubungan readiness ke validasi dokumen.
- Membuat module aturan baru: menambah seam dan berkas untuk kebutuhan yang dapat dipenuhi pemilik yang sudah ada.

## Struktur dan interface

Module POS Spend Template memiliki aturan cakupan company, kelayakan dasar akun debit, dan ekspansi item yang diizinkan. Controller tetap menjalankan validasi struktural dan default sebagaimana saat ini.

Module resolusi akun tetap memiliki resolusi akun lintas company dan penyusunan hasil readiness. Readiness tetap memanggil validasi Template. Module pencatatan Spend tetap menjalankan otorisasi serta membangun dan mensubmit dokumen native.

Seam pemanggil tetap memakai fungsi yang sudah ada, bukan interface baru. `allowed_items(template)` mempertahankan nama, parameter, hasil, urutan, dan error. Import lama `spend_accounts.allowed_items` tetap tersedia sebagai binding import langsung, tanpa fungsi penerus tambahan. Implementation hanya ada satu. Pemanggil internal di Spend memakai pemilik aturan yang baru secara eksplisit.

Tidak diperlukan adapter baru; akses Frappe yang sudah ada tetap dipakai.

## Dropdown cakupan company

Tambahkan Select `company_scope`, label `Company Scope`, tepat sebelum tabel Companies:
- `Selected Companies` (default): cakupan berasal dari tabel Companies dengan ekspansi company grup yang sudah ada. Tabel kosong menghasilkan cakupan kosong.
- `All Companies`: cakupan adalah seluruh Company dengan `is_group=0`, termasuk company leaf yang dibuat setelah Template disimpan. Company grup tidak menjadi target transaksi.

Nilai kosong pada dokumen lama diperlakukan sebagai `Selected Companies`, baik saat resolusi maupun saat penyimpanan. Nilai di luar kedua pilihan ditolak di server. Deployment membutuhkan migrasi schema DocType; pastikan dokumen lama tidak berubah menjadi All. Bila migrasi memerlukan backfill, hanya isi nilai kosong menjadi Selected, tanpa mengubah pilihan yang sudah tersimpan.

`template_companies` tetap menjadi satu sumber cakupan untuk daftar Template, otorisasi submit, dan readiness. Pada All, hasil tetap unik dan terurut. Ini tidak melewati izin company/profile pengguna, role Template, enabled, atau pemeriksaan kesiapan akun.

Tabel Companies tetap tersedia pada kedua pilihan. Pada All, barisnya opsional dan hanya memberi override company leaf; baris company grup tetap tidak boleh membawa override. Mengganti pilihan tidak menghapus baris tabel. Tambahkan keterangan form sesuai mode agar fungsi tabel tidak ambigu; override yang lama tetap divalidasi. Pemilihan Selected kembali membuat baris yang tersimpan menentukan cakupan lagi.

Aturan Template Account tanpa reference debit account tetap berlaku: setiap company dalam cakupan harus punya override eksplisit. Pada All, hal ini berarti seluruh company leaf; jangan menambahkan fallback akun. Readiness menampilkan ketidaksiapan sesuai aturan yang sudah ada.

Readiness All dapat memeriksa lebih banyak company daripada Selected. Hindari pemanggilan ekspansi seluruh company berulang dalam satu validasi bila hasil yang sama dapat digunakan ulang; jangan menambahkan cache lintas request atau mengubah semantik readiness dalam pekerjaan ini.

## Perilaku yang wajib identik, selain cakupan All eksplisit

- Item yang dipilih langsung tetap dimasukkan dengan aturan saat ini; jangan menambahkan filter baru.
- Ekspansi Item Group mencakup grup itu sendiri dan turunannya, dengan filter `disabled=0` dan `is_purchase_item=1` yang sudah ada.
- Hasil tetap unik dan terurut.
- Pemilihan baris item versus grup dan urutan validasi tidak berubah.
- Cakupan company grup, override company leaf, serta aturan akun debit tetap identik.
- `reference_company` kosong tetap diisi dari pengaturan global saat validasi, termasuk lewat readiness.
- Jenis/pesan error dan struktur hasil readiness tetap identik.
- Template save, readiness, daftar Template, dan submit Spend tetap menggunakan aturan yang sama.

## Cakupan perubahan

Berkas utama:
- `pos_next/pos_next/doctype/pos_spend_template/pos_spend_template.py`
- `pos_next/services/spend_accounts.py`
- `pos_next/api/spend.py`
- `pos_next/pos_next/doctype/pos_spend_template/pos_spend_template.json` (field Select dan default)
- `pos_next/pos_next/doctype/pos_spend_template/pos_spend_template.js` (keterangan Companies sesuai mode, mengikuti pola form yang ada)
- `pos_next/translations/id.csv` (string baru, pertahankan byte baris lama)
- Patch migrasi dan registrasinya hanya jika pemeriksaan migrasi membuktikan backfill eksplisit diperlukan.

Pengujian relevan:
- `pos_next/pos_next/doctype/pos_spend_template/test_pos_spend_template.py`
- `pos_next/tests/test_spend_accounts.py`
- `pos_next/api/test_spend.py`

Periksa seluruh import dan patch target `allowed_items` sebelum implementasi, termasuk pengujian, lalu sesuaikan hanya yang diperlukan. Dropdown membutuhkan perubahan schema dan migrasi DocType, tetapi tidak mengubah fixture hak akses atau konfigurasi transaksi yang sudah ada.

## Pengujian dan kriteria penerimaan

Interface adalah test surface: gunakan validasi Template, readiness, dan submit Spend untuk membuktikan perilaku, bukan sekadar memeriksa lokasi fungsi.

1. Pin perilaku ekspansi grup, item langsung, deduplikasi, pengurutan, dan penolakan item melalui pengujian relevan yang paling kecil.
2. Pin pengisian default `reference_company` melalui readiness agar refactor tidak diam-diam mengubah mutasi di memori.
3. Jalankan pengujian Template, akun/readiness, dan Spend secara serial pada lingkungan uji yang ketersediaannya diverifikasi terlebih dahulu.
4. Periksa bahwa controller tidak lagi mengimpor module resolusi akun dan implementation ekspansi item tidak terduplikasi.
5. Periksa binding import lama tetap dapat digunakan tanpa fungsi penerus.
6. Pisahkan kegagalan baseline yang dibuktikan dari regresi baru; hasil historis bukan bukti tes terbaru.
7. Uji Selected kosong tetap kosong, Selected grup tetap mencakup turunannya, All mencakup semua leaf tetapi bukan grup, dan company leaf baru otomatis tercakup pada All.
8. Uji nilai scope kosong/legacy tetap Selected, nilai tak dikenal ditolak, override leaf tetap digunakan pada All, serta pergantian mode tidak menghapus tabel.
9. Uji All tidak melampaui hak akses company pengguna, role, atau readiness; company tanpa akun siap tetap ditolak. Uji migrasi Template lama mempertahankan cakupan.
10. Uji keterangan form berubah mengikuti dropdown dan Companies tetap tersedia; lakukan pemeriksaan browser pada form Desk setelah migrasi dev yang diotorisasi.

Keberhasilan: ketergantungan balik hilang, perilaku lama tetap sama pada Selected, dan perubahan perilaku terbatas pada All yang dipilih secara eksplisit.

## Di luar cakupan dan risiko

Tidak mengubah lock shift, idempotensi, GL, lampiran, UI SPA, otorisasi, dokumen native, pembelian stok/pajak, pembayaran tertunda, provisioning akun, atau batas nominal. Tidak menambahkan dependency.

Risiko utama: import atau patch target pengujian dapat bergantung pada lokasi lama, dan pemindahan dapat tanpa sengaja mengubah filter atau urutan validasi. Pertahankan implementation byte-equivalent sejauh praktis; verifikasi semua pemanggil sebelum menyentuhnya.

Tidak ada konflik dengan ADR Offline Price Mismatch. Refactor tidak menambahkan konsep domain baru; GLOSSARY.md tidak perlu diubah untuk keputusan ini.

## Gerbang berikutnya

User meninjau dan menyetujui spec tertulis ini sebelum rencana implementasi disusun. Setelah rencana ditinjau, user memilih metode eksekusi. Commit dan push masing-masing membutuhkan persetujuan terpisah; dokumen ini belum di-commit.
