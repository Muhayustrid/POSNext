# Panduan Shift Terjadwal (untuk HQ)

Versi ringkas Bahasa Indonesia dari [SHIFT_SCHEDULE.md](SHIFT_SCHEDULE.md).
Jam shift diatur sekali per **Grup Shift** (Shift Group) lalu otomatis
diterapkan ke semua Profil POS anggotanya; pengaturan per profil tetap bisa
dipakai. Grup bersifat **lintas perusahaan** — satu grup boleh berisi profil
dari beberapa perusahaan (mis. profil kantor pusat PT induk dan profil
gerai-gerai cabangnya dalam satu grup yang sama). Profil yang sudah ada tidak
diubah massal — nilainya tetap seperti sebelumnya.

## 1. Pengaturan oleh HQ (Desk → POSNext → Shift Group)

| Field | Arti |
|---|---|
| Shift Group | Nama grup (mis. "Pagi 05:00-12:00"). |
| Enable Shift Schedule | Saklar utama yang diterapkan ke anggota. Bawaan: aktif. |
| Shift Start Time / Shift End Time | Jam shift sesuai **zona waktu server** (contoh 05:00 → 12:00). Jam akhir lebih awal dari jam mulai berarti melewati tengah malam (contoh 21:00 → 05:00). Wajib diisi saat aktif — grup aktif tanpa jam valid ditolak saat disimpan. |
| Schedule Warning Minutes | Kasir anggota mendapat peringatan sesuai menit sebelum shift berakhir. 0 = tanpa peringatan. |
| Enforce Closing After Schedule | Aktif = jadwal bersifat **wajib** untuk anggota (penjualan ditolak setelah jam berakhir). Nonaktif = hanya peringatan. |
| POS Profiles (Members) | Daftar profil anggota — **boleh lintas perusahaan** (mis. profil PT induk dan profil gerai cabangnya dalam satu grup). Kolom **Company** di tiap baris terisi otomatis dari profil (baca-saja) agar HQ tetap tahu perusahaan tiap gerai. Satu profil hanya boleh di satu grup — memindahkan profil dari grup lain harus dilakukan dari grup lamanya dulu, tidak bisa diam-diam. |

Kolom "Company (retired)" pada grup yang dibuat sebelum fitur lintas
perusahaan tidak dipakai lagi — biarkan saja, datanya tetap utuh.

**Menyimpan grup otomatis menyinkronkan semua anggota**: setiap profil
anggota menerima nilai aktif/jam/peringatan/wajib-tutup dari grup (profil
masih bisa diedit manual sampai grup disimpan lagi). Menyimpan grup
**membutuhkan akses tulis ke setiap profil yang berubah — termasuk profil
yang dihapus dari daftar**; jika satu profil tidak bisa ditulis (mis. milik
perusahaan di luar wewenang Anda), seluruh penyimpanan dibatalkan — tidak
ada anggota yang tersinkron setengahnya. **Menghapus** profil dari daftar
hanya melepas tautannya — profil **mempertahankan jam shift terakhir yang
tersinkron** (tidak ada kehilangan diam-diam). Shift yang sedang berjalan
tidak terpengaruh — jadwalnya sudah menggumpal (snapshot) saat shift
dibuka, perubahan grup berlaku untuk shift berikutnya.

### Per profil (Desk → POS Next → POS Profile)

| Field | Arti |
|---|---|
| Shift Group | Keanggotaan grup — **cerminan read-only**: keanggotaan diatur di tabel Anggota grup. Profil yang menautkan grup tanpa baris anggota ditolak saat disimpan (jadwal grup tidak akan pernah tersinkron ke profil itu). |
| Enable Shift Schedule | Saklar utama. **Bawaan aktif untuk profil baru** — wajib punya Grup Shift atau jam shift, kalau tidak penyimpanan ditolak dengan pesan validasi yang jelas. Profil lama tetap memakai nilai yang sudah ada. |
| Shift Start Time / Shift End Time | Jam shift sesuai **zona waktu server**. Jam akhir lebih awal dari jam mulai berarti shift melewati tengah malam (contoh 21:00 → 05:00). Wajib diisi saat fitur aktif — pengaturan tidak valid ditolak saat disimpan. |
| Schedule Warning Minutes | Kasir mendapat peringatan sesuai menit sebelum shift berakhir. 0 = tanpa peringatan. |
| Enforce Closing After Schedule | Aktif = jadwal bersifat **wajib** (penjualan ditolak setelah jam berakhir). Nonaktif = hanya peringatan. |

## 2. Yang diberlakukan server (bisa diandalkan)

- **Pembukaan** — saat Enforce Closing aktif, shift tidak bisa dibuka di luar
  jam jadwal.
- **Setelah jam berakhir** — penjualan, pembayaran (termasuk cicilan /
  penukaran kredit), dan pengembalian **ditolak oleh server**. Refresh halaman
  atau login ulang tidak bisa melewati pembatasan ini.
- **Penutupan selalu diizinkan** — kasir menutup shift lewat alur normal dan
  menghitung uang tunai aktual. Tidak ada dokumen keuangan yang dibuat
  otomatis.
- **Transaksi yang sudah terlanjur disubmit** tidak pernah diubah — hanya
  submit baru pada atau setelah jam berakhir yang ditolak.

## 3. Mode offline

- Selama jadwal wajib berlaku, checkout offline **dimatikan secara
  proaktif** agar tidak ada invoice yang masuk antrean lalu ditolak saat sync.
- Invoice yang sudah ada di antrean **tidak pernah dibuang**; server
  menolak replay-nya dan invoice tetap di antrean.

## 4. Pemulihan: memperpanjang deadline shift (System Manager)

Jika ada invoice yang tertahan karena shift sudah melewati deadline, System
Manager memperpanjang deadline lewat API resmi (teraudit, hanya maju — tidak
bisa dipersingkat). **Catatan: ini tindakan teknis admin — belum ada tombol
atau layar UI khusus untuk ini**, dijalankan lewat konsol/panggilan API:

```
POST /api/method/pos_next.shift_schedule.extend_deadline
     {"opening_shift": "POSA-OS-0001", "new_deadline": "2026-09-08 01:00:00"}
```

Server mencatat Comment audit di dokumen shift. Setelah deadline
diperpanjang, kasir sync ulang dari Management → Offline Invoices, lalu tutup
shift seperti biasa. Mengedit langsung database **tidak** didukung.

## 5. Ringkasan penerimaan

Keputusan diterima/tidaknya transaksi diambil **jam server saat validasi
submit**. Pembayaran yang sedang berjalan saat deadline lewat tetap
diselesaikan (diterima jika validasi selesai sebelum deadline; jika ditolak,
invoice tetap aman sebagai draft). Pembayaran fisik yang submittnya **belum
dimulai** saat deadline lewat akan ditolak — itulah batasnya.
