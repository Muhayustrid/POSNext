# Outlet Monitoring (Checklist & Bukti Operasional POS)

Fitur Outlet Monitoring menyediakan mekanisme pengawasan operasional outlet POS berbasis checklist tugas dan bukti media (foto/video) untuk kasir di Single Page Application (SPA) POS dan reviewer di Frappe Desk.

---

## 1. Arsitektur & Cara Kerja

Fitur ini menggunakan model **Single DocType** untuk setiap penugasan: `POS Monitoring Report`.

### Cadence Penugasan
1. **Per Shift**:
   - Diterbitkan secara otomatis saat kasir membuka shift baru (`POS Opening Shift`).
   - Batas waktu (`due_at`) dihitung berdasarkan menit setelah shift dibuka (`due_minutes_after_open`).
   - Jendela waktu buka (`opens_at`): dihitung dari `open_after_minutes` setelah shift dibuka. Kasir tidak dapat mengisi laporan sebelum `opens_at`.
   - Memiliki peringatan saat kasir menutup shift jika masih ada checklist shift yang belum diserahkan.
2. **Per Hari**:
   - Diterbitkan untuk tanggal operasional per perusahaan (`Company`).
   - Batas waktu (`due_at`) mengacu pada jam tertentu (`due_time`, misalnya pukul 18:00).
   - Jendela waktu buka (`opens_at`): mengacu pada `open_time` (misalnya pukul 06:00).
   - Dapat dikerjakan oleh kasir pada profil POS mana pun di bawah perusahaan tersebut yang berada dalam cakupan template.
3. **Manual (Unboxing / Penerimaan Barang)**:
   - Diterbitkan secara insidental oleh kasir saat menerima kiriman barang dari gudang pusat atau outlet lain.
   - Wajib mereferensikan dokumen pengiriman: **Delivery Note (DN)** atau **Purchase Receipt (PR)**. Laporan dapat diisi sebelum atau sesudah submit PR, dan tidak pernah mengunci dokumen PR.
   - **Aturan Satu Kiriman Sekali per Template**: Setiap dokumen kiriman hanya boleh dilaporkan tepat satu kali per template manual (`obligation_key` berbasis referensi dokumen dan template).
   - **Normalisasi PR Internal**: Bila Purchase Receipt berasal dari transaksi antar-perusahaan (internal) dan memiliki `inter_company_reference` (merujuk ke Delivery Note pengirim), seluruh item pada PR internal harus sesuai dengan Delivery Note header pengirim, dan sistem secara otomatis menormalisasi referensi ke DN tersebut untuk mencegah pelaporan ganda. PR internal tanpa `inter_company_reference` tidak valid sebagai kandidat.
   - **Batasan Tanggal Kandidat**: Hanya dokumen pengiriman dalam 7 hari terakhir yang dapat dipilih oleh kasir.
   - **Batas Waktu**: Dihitung dari `submit_within_hours` sejak laporan manual dibuat. Laporan unboxing yang belum diserahkan juga memicu peringatan saat kasir menutup shift.

### Lifecycle Status Laporan
- **Draft**: Laporan aktif yang sedang dikerjakan kasir. Media bukti dapat ditambah atau dihapus.
- **Submitted**: Laporan telah diserahkan oleh kasir (semua checkpoint wajib memiliki bukti). Laporan terkunci dari perubahan kasir.
- **Reviewed**: Laporan telah dinilai oleh reviewer di Desk dengan penetapan label kustom final pada setiap checkpoint.
- **Waived**: Laporan dibebaskan dari kewajiban oleh reviewer (misal outlet renovasi atau kendala teknis). Wajib mencantumkan alasan pembebasan.
- **Missed**: Laporan melewati batas waktu ditambah toleransi grace period tanpa diserahkan. Laporan ditutup otomatis oleh sistem.

### Alur Revisi (Correction Workflow)
- Bila reviewer menilai laporan dengan catatan perbaikan dan mencentang `correction_requested`, kasir dapat menekan **Buat Revisi**.
- Sistem membuat laporan baru dengan `revision = revision + 1`, mereferensikan laporan lama (`correction_of`), dan memberikan batas waktu baru sesuai `shift_missed_grace_hours`.
- Laporan lama tetap tersimpan sebagai rekam jejak audit yang tidak berubah.

---

## 2. Struktur DocType & Pengaturan

### POS Monitoring Settings (Single DocType)
Konfigurasi terpusat seluruh fitur outlet monitoring:
- **enabled**: Saklar global fitur monitoring. Jika nonaktif (0), cron dan API kasir berhenti bekerja.
- **shift_missed_grace_hours**: Jam toleransi keterlambatan setelah batas waktu sebelum laporan ditandai Missed.
- **media_retention_days**: Masa retensi file media (hari) sebelum dibersihkan oleh cron `purge_expired_media`.
- **allow_late_submission**: Mengizinkan kasir menyerahkan laporan setelah `due_at`.
- **allow_correction** & **max_revisions**: Mengatur izin dan batas maksimal revisi laporan.
- **prevent_self_review**: Mencegah pengguna mereview laporan yang diserahkan oleh dirinya sendiri.
- **photo_max_mb**, **video_max_mb**, **video_max_seconds**: Batasan ukuran dan durasi upload media.
- **warn_on_shift_close**: Menampilkan peringatan di dialog tutup shift kasir.

### Master Data
- **POS Monitoring Label**: Master pilihan label penilaian yang fleksibel (menggantikan status kaku Pass/Fail/N/A). Setiap label memiliki nama dan warna palet terstandarisasi (misal Hijau, Merah, Abu-abu, Biru, Kuning, dsb.).
  - **Aturan Keamanan**: Dokumen label tidak dapat dihapus (`on_trash` dicegah); bila tidak lagi digunakan, label cukup dinonaktifkan (`disabled = 1`).
  - **Label Default Wajib**: Jika Outlet Monitoring aktif (`enabled = 1`), setidaknya harus ada minimal satu label default yang aktif di `POS Monitoring Settings`.
  - **Hierarki Pewarisan Label**: Opsi label yang berlaku pada sebuah checkpoint dievaluasi bertingkat:
    1. Checkpoint (`labels` pada POS Monitoring Checkpoint)
    2. Kategori (`labels` pada POS Monitoring Category jika checkpoint kosong)
    3. Global (`default_labels` pada POS Monitoring Settings jika kategori kosong)
- **POS Monitoring Category**: Pengelompokan checkpoint (misal: Kebersihan, Display Produk, Kas & Dokumen, Penerimaan Barang). Mendukung pengaturan opsi label kategori dan saklar `cashier_can_label` (mengizinkan kasir mengusulkan label draf untuk checkpoint di bawah kategori ini).
- **POS Monitoring Checkpoint**: Definisi standar audit per item, tipe media (Photo / Video / Both), batas jumlah bukti (min/max), durasi video, gambar referensi, dan opsi label kustom tingkat checkpoint.
- **POS Monitoring Template**: Template yang mengatur kumpulan checkpoint, cadence (Per Shift / Per Hari / Manual), aturan batas waktu (`due_minutes_after_open`, `due_time`, `submit_within_hours`), jendela buka (`open_after_minutes`, `open_time`), serta aturan cakupan (Scopes):
  - **Company Scope**: `All` (seluruh perusahaan yang dapat diakses) atau `Selected` (hanya perusahaan pada tabel anak `companies`).
  - **POS Profile Scope**: `All` (seluruh profil POS) atau `Selected` (hanya profil pada tabel anak `pos_profiles`).
  - **Shift Type Scope**: `All` (seluruh tipe shift) atau `Selected` (hanya tipe shift pada tabel anak `shift_types`).

---

## 3. Fitur Kasir di POS SPA

### Akses & Tampilan
- **Menu Navigasi**: Menu "Checklist & Monitoring" diakses melalui hamburger menu kasir (`POSMenuDialog.vue`).
- **Badge Notifikasi**: Menampilkan jumlah tugas aktif yang belum selesai di samping nama menu.
- **Realtime Refresh**: Badge dan daftar tugas otomatis diperbarui saat kasir membuka shift baru, membuka menu POS, atau menerima sinyal socket.io `outlet_monitoring_updated`.

### Pengisian Laporan (`OutletMonitoringDialog.vue`)
- Kasir melihat ringkasan tugas aktif serta riwayat laporan terdahulu.
- Checkpoint menampilkan instruksi jelas, indikator wajib/opsional, dan batas media.
- **Pemberian Label Kasir (Cashier Propose Label)**: Jika `cashier_can_label = 1` diaktifkan pada kategori checkpoint, kasir dapat memilih usulan label saat pengisian draf. Pilihan ini bersifat opsional bagi kasir dan berfungsi sebagai usulan awal sebelum dinilai final oleh reviewer HQ.
- **Validasi Jendela Waktu**: Laporan yang belum mencapai `opens_at` dikunci dari pengisian hingga jam/menit yang ditentukan tiba.
- Kasir dapat mengunggah foto (otomatis dikompresi di sisi browser menjadi JPEG ramah kuota) atau video MP4/WebM yang tervalidasi durasinya.
- Validasi mencegah pengiriman bila checkpoint wajib belum melampirkan bukti.
- Tombol **Mulai Laporan Manual**: Memungkinkan kasir memilih template Manual aktif, mencari dokumen Delivery Note atau Purchase Receipt (7 hari terakhir), dan memulai checklist unboxing.

### Peringatan Tutup Shift (`ShiftClosingDialog.vue`)
- Pengecekan tugas shift dan manual berjalan secara asinkron tanpa memblokir kalkulasi atau tampilan penutupan kasir.
- Jika terdapat laporan shift atau unboxing yang belum diserahkan, banner peringatan ramah kasir akan muncul merincikan daftar laporan tersebut.

---

## 4. Reviewer di Frappe Desk

### Hak Akses (Role)
- Role standar: `POSNext Monitoring Reviewer`.
- Reviewer dibatasi berdasarkan `User Permission` pada dokumen `Company`.

### Form Review (`POS Monitoring Report`)
- Menampilkan pratinjau thumbnail media foto dan video secara instan.
- **Penilaian Fleksibel**: Reviewer memilih label final untuk setiap checkpoint dari daftar label yang berlaku (diwarisi dari Checkpoint > Category > Settings). Usulan label kasir (`cashier_label`) ditampilkan sebagai referensi pembanding.
- Reviewer dapat menambahkan catatan penilaian per checkpoint (`assessment_note`) dan catatan umum laporan (`review_note`).
- Tombol **Finalize Review** memvalidasi bahwa seluruh checkpoint telah memiliki label, mengunci kolom review, mencatat penilai (`reviewed_by`) dan waktu penilaian server (`reviewed_at`).
- Tombol **Waive** memungkinkan pembebasan laporan (status Submitted atau Missed; Draft tidak dapat di-waive) dengan wajib mencantumkan alasan. Laporan yang sudah difinalisasi atau dibebaskan terkunci permanen dari perubahan status.

### Galeri Monitoring Outlet Desk (`/app/outlet-monitoring-gallery`)
- Halaman galeri interaktif untuk memantau bukti visual seluruh outlet secara cepat.
- Dilengkapi filter lengkap: Perusahaan, Profil POS, Periode (Hari Ini, Kemarin, Minggu Ini, Bulan Ini, Kustom), Kategori, Checkpoint, Hasil Penilaian, dan Tipe Media.
- Pemutaran video dan pembesaran gambar dalam dialog detail yang aman dari celah XSS.
- Memiliki request sequencing untuk mengabaikan respon usang saat kasir/reviewer mengganti filter dengan cepat.

---

## 5. Tugas Terjadwal (Cron / Scheduler)

Tiga tugas berjalan otomatis di `hooks.py`:

| Cron Hook | Fungsi | Detail |
| --- | --- | --- |
| `cron: */5 * * * *` -> `ensure_obligations` | Menjamin penerbitan tugas | Menerbitkan laporan Per Hari atau Per Shift yang tertunda setiap 5 menit |
| `cron: */5 * * * *` -> `mark_missed` | Menutup laporan terlambat | Mengubah status Draft kadaluarsa menjadi `Missed` setelah grace period setiap 5 menit |
| `daily` -> `purge_expired_media` | Membersihkan media usang | Menghapus fisik file yang melewati `media_retention_days` dan menandai `purged = 1` |

Seluruh tugas cron secara otomatis berhenti (no-op) bila `POS Monitoring Settings.enabled = 0`.

---

## 6. Keamanan & Proteksi Data

1. **Private File Storage**:
   - Seluruh bukti media disimpan sebagai File privat (`is_private = 1`), tidak dapat diakses langsung oleh publik tanpa autentikasi dan hak akses dokumen laporan terkait.
2. **Atomic Upload & Rollback**:
   - Pendaftaran media terlindungi pembersihan otomatis (`after_rollback` hook) jika terjadi kegagalan transaksi, memastikan tidak ada file yatim yang tertinggal di disk atau database.
3. **Immutability After Submit**:
   - Struktur laporan (perusahaan, profil, opening shift, tanggal, revisi, media child table) dikunci secara ketat di backend melalui lifecycle hook `before_update_after_submit`.
4. **Validasi File & Durasi Video**:
   - Pemeriksaan format file (JPEG untuk foto/thumbnail, MP4/WebM untuk video) dan durasi video dicek di browser dan divalidasi integritasnya sebelum disimpan.
