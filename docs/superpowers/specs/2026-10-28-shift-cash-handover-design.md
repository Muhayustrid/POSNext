# Serah-Terima Kas Antarshift — Desain

Tanggal: 2026-10-28
Status: disetujui secara lisan per bagian; menunggu review spec tertulis.
Baseline: `main` @ 4c5c51eb.
Konteks: `.scratch/shift-cash-handover/handoff.md`.

## Tujuan

Satu POS Profile dipakai beberapa shift berurutan dengan **laci kas fisik yang sama**,
oleh akun kasir yang berbeda (kasir A logout, kasir B login). Saat ini setiap opening
mulai dari input "Modal Awal" kosong yang tidak terhubung ke closing sebelumnya.

Setelah fitur ini:

- Shift pertama hari itu tetap mengisi **Modal Awal** secara manual (biasanya Rp 200.000).
- Shift berikutnya di hari yang sama melihat **usulan** dari server = kas tunai yang
  diharapkan (expected) pada closing sebelumnya di profil yang sama, lalu kasir B
  **menghitung fisik dan mengetik jumlah yang diterima**.
- Usulan, jumlah diterima, dan selisih tersimpan di server pada opening B; tidak
  bergantung pada browser atau sesi kasir A.
- Serah-terima kas **bukan penjualan**: tidak membuat invoice, Payment Entry, atau
  menambah omzet.

Contoh: 200.000 + 800.000 penjualan tunai bersih = usulan 1.000.000. Jika 800.000
sudah diambil pemilik, kasir B mengetik 200.000; selisih −800.000 tercatat, dan
expected closing B dihitung dari 200.000.

## Keputusan

| Topik | Keputusan |
|---|---|
| Siapa menghitung fisik | Kasir B saat opening. Closing A tetap otomatis (`closing_amount = expected`). |
| Siklus kas | Reset harian: shift pertama dengan `posting_date` hari ini pada profil itu = Modal Awal manual. |
| Selisih | Hanya dicatat dan terlihat oleh manajer; tidak wajib alasan, tidak butuh persetujuan. |
| Input | Kosong; usulan ditampilkan sebagai acuan; kasir wajib mengisi (boleh `0`). |
| Penyimpanan | Field tambahan pada doctype opening yang sudah ada (bukan doctype baru). |
| Latensi closing | Di luar spec ini. Sub-proyek terpisah: ukur per tahap dulu, baru perbaiki. |

## Fakta kode saat ini (diverifikasi)

- `POS Opening Shift Detail` hanya punya `mode_of_payment` dan `amount` (label "Modal Awal").
- `create_opening_shift` (`pos_next/api/shifts.py:152`) mengunci baris POS Profile
  (`for_update`), menutup otomatis shift kedaluwarsa (`_submit_closing(..., auto_balance=True,
  auto_closed=True)`), lalu membuat opening; mode di luar `get_modal_awal_modes` dipaksa 0.
- `make_closing_shift_from_opening` menyemai `opening_amount` dari `balance_details.amount`,
  jadi expected closing B otomatis memakai jumlah yang diterima B.
- `_submit_closing` dengan `auto_balance` menyamakan `closing_amount` dengan `expected_amount`
  (dikunci oleh `test_closing_always_expected.py`). Tidak diubah.
- Kasir tidak bisa membaca `POS Closing Shift` milik user lain; `_check_profile_access`
  (`shifts.py:598`) memeriksa keanggotaan `POS Profile User`.
- Tidak ada fitur kas keluar di tengah shift; karena itu angka sistem hanya usulan, dan
  hitungan fisik B adalah angka yang dipakai.

## Desain

### 1. Skema (doctype milik pos_next, masuk lewat `bench migrate`)

`POS Opening Shift`:

- `handover_from` — Link ke `POS Closing Shift`, read-only. Kosong pada shift pertama hari itu.

`POS Opening Shift Detail`:

- `proposed_amount` — Currency, read-only, label "Menurut Sistem". Expected kas closing
  sebelumnya untuk mode ini; 0/kosong bila tidak ada serah-terima.
- `amount` — tetap, makna = jumlah yang benar-benar diterima/dimasukkan. Label tetap "Modal Awal"
  di Desk (tidak mengubah laporan yang sudah membaca field ini).
- Selisih tidak disimpan sebagai field; dihitung `amount − proposed_amount` bila ditampilkan.

### 2. Usulan dihitung di server (satu helper)

Helper `get_cash_handover(pos_profile)` di modul shift (lokasi persis ditentukan di plan),
dipakai oleh dialog dan oleh create:

1. Jika tidak ada `POS Opening Shift` submitted dengan `pos_profile` ini dan
   `posting_date = hari ini` → `None` (shift pertama hari ini).
2. Selain itu, ambil `POS Closing Shift` submitted terakhir milik profil ini yang opening-nya
   ber-`posting_date` hari ini (urut `period_end_date desc`). Jika tidak ada → `None`.
3. Kembalikan `{closing, user, period_end_date, auto_closed, amounts: {mode: expected_amount}}`
   hanya untuk mode di `get_modal_awal_modes(pos_profile)` — QRIS/debit tidak ikut.

Dibaca dengan query langsung (bukan `get_doc` dengan cek izin) setelah pemanggil lolos
`_check_profile_access`; kasir B tidak mendapat hak baca dokumen closing A.

### 3. Dialog opening

`get_opening_dialog_data` menambah `data["handover"] = {profile: get_cash_handover(profile)}`
untuk setiap profil yang bisa diakses. Tidak ada request tambahan.

`ShiftOpeningDialog.vue`:

- `handover` kosong → tampilan sekarang, label "Modal Awal".
- `handover` ada → label "Kas Diterima dari Shift Sebelumnya"; di setiap baris mode tunai
  teks kecil "Menurut sistem: Rp X (shift <kasir>, tutup <jam>)", plus "(ditutup otomatis)"
  bila closing berasal dari auto-close.
- Input kosong; tombol "Buka Shift" nonaktif sampai semua input mode serah-terima terisi
  (`0` sah, kosong tidak).
- Di bawah input tampil "Selisih ±Rp Y" saat nilai berbeda dari usulan; informatif, tidak memblokir.
- String baru ditambahkan ke CSV terjemahan (`docs/LOCALIZATION.md`).

### 4. Create opening (batas kepercayaan)

Di `create_opening_shift`, **setelah** lock profil dan blok auto-close, sebelum insert:

- Panggil `get_cash_handover(pos_profile)` lagi; hasilnya sumber satu-satunya untuk
  `handover_from` dan `proposed_amount`. Nilai `proposed_amount`/`handover_from` dari klien diabaikan.
- Klien hanya mengirim `opening_amount` (= jumlah diterima), seperti sekarang; aturan
  "mode non-Modal-Awal dipaksa 0" tetap berlaku.
- Karena profil terkunci dan satu profil hanya punya satu shift aktif, usulan basi atau klaim
  ganda tidak bisa terjadi; tidak perlu status "sudah diklaim".
- Jika auto-close terjadi di request yang sama, closing hasil auto-close itulah yang diusulkan.
- Jalur resume manajer tidak membuat opening, jadi tidak menyentuh serah-terima.

### 5. Tampilan manajer

- Desk form `POS Opening Shift` menampilkan `handover_from` dan kolom `proposed_amount` di tabel detail.
- Laporan/HQ monitoring tidak diubah dalam lingkup ini; datanya sudah tersimpan untuk laporan nanti.

## Di luar lingkup

- Latensi closing (sub-proyek terpisah, diukur dulu).
- Pencatatan kas keluar/setoran di tengah shift.
- Hitung fisik oleh kasir A saat closing; perubahan `auto_balance`.
- Persetujuan manajer atau alasan wajib untuk selisih.
- Membawa kas lintas tanggal (shift malam → pagi). Shift yang melewati tengah malam ikut
  tanggal opening-nya; opening pertama tanggal berikutnya kembali ke Modal Awal manual.
  Tinjau ulang bila toko ternyata membawa kas shift malam ke shift pagi.
- Cetak ulang EOD lintas akun.

## Pengujian

Backend (test site Docker terisolasi, runner `pos_next/_pn_run_tests.py`, serial; bukan data dev/prod):

1. Belum ada shift hari ini → `handover` None; opening tanpa `handover_from`, `proposed_amount` 0.
2. 200.000 + 800.000 tunai → usulan 1.000.000; diterima 200.000 → tersimpan `proposed_amount`
   1.000.000, `amount` 200.000; expected closing B dimulai dari 200.000.
3. Mode QRIS/non-Modal-Awal tidak muncul di usulan.
4. Kasir B (bukan pemilik closing A, bukan manajer) menerima usulan; user di luar profil ditolak.
5. `proposed_amount`/`handover_from` palsu dari klien diabaikan.
6. Shift A kedaluwarsa ditutup otomatis saat B membuka → usulan dari closing auto-close itu.
7. Closing dengan opening bertanggal kemarin tidak diusulkan.
8. Tidak ada invoice/Payment Entry baru dan total penjualan tidak berubah karena serah-terima.

Frontend (Vitest):

- Label berganti sesuai ada/tidaknya `handover`.
- "Buka Shift" nonaktif saat input serah-terima kosong; aktif dengan `0`.
- Selisih tampil saat nilai ≠ usulan.

Verifikasi manual di dev setelah rebuild/migrate (dengan backup dulu): kasir A tutup + logout,
kasir B login di profil yang sama, usulan tampil, jumlah diterima tersimpan.
