# Desain: Nomor Antrian POS (Queue Number)

Tanggal: 2026-09-06 · Status: disetujui untuk implementasi · Repo: `pos_next`

## Ringkasan

Nomor antrian pelanggan yang dicetak di struk (customer receipt) dan crew
slip. Counter **akumulasi per Company (= outlet) per hari**, dibagi semua
POS Profile di company tersebut, reset otomatis berganti tanggal. Fitur
bisa diaktifkan/nonaktifkan **per Company**.

### Keputusan yang sudah dikunci (dari diskusi)

| Keputusan | Pilihan |
|---|---|
| Cakupan counter | Per Company per hari (semua profil berbagi) |
| Perilaku offline | Lanjut lokal dari nomor terakhir yang diketahui; duplikat diterima hanya jika dua kasir offline bersamaan |
| Lokasi cetak | Customer receipt **dan** crew slip, di **bagian paling atas** struk |
| Toggle | Custom field di Company; tanpa switch global |
| Waktu alokasi | Saat checkout, distempel ke Sales Invoice sebelum print |

## 1. Konfigurasi — toggle per outlet

Custom field di **Company** (ditambahkan ke `CUSTOM_FIELDS` di
`pos_next/install.py`, pola yang sudah ada):

- `enable_pos_queue` — Check, default 0, label "Enable POS Queue Number".

"Semua outlet" = operator menyalakan field ini di tiap Company;
"tidak sama sekali" = dibiarkan mati. Tidak ada konfigurasi global baru.

Bootstrap SPA mengirim nilai ini (via payload pos profile / bootstrap
yang sudah ada) sehingga SPA tahu tanpa round-trip tambahan.

## 2. Counter — DocType `POS Queue Counter`

Module POS Next, bukan child table, tidak tampil di workspace:

- `company` — Link Company, wajib
- `date` — Date, wajib
- `current_number` — Int, default 0
- autoname: `hash` (nama company bisa berisi karakter yang tidak aman
  untuk docname); keunikan (company, date) dijaga controller `validate`.

Satu baris per (company, tanggal). Baris baru = reset harian, tanpa
cron/cleanup — baris tanggal lama dibiarkan sebagai riwayat (kecil,
satu baris per outlet per hari).

## 3. API — `pos_next.api.queue.get_next_queue_number`

Request: `pos_profile` (string) → resolve `company` dari POS Profile.

Perilaku:

1. Ambil/hitung tanggal hari ini (server date).
2. Lock baris counter untuk (company, hari ini):
   `SELECT ... FOR UPDATE` via `frappe.db.sql(..., for_update=True)`
   pada baris yang sudah ada; jika belum ada, insert dengan retry
   singkat menangani race insert dua kasir pertama.
3. `current_number += 1`, commit segera (transaksi sendiri, tidak
   menumpuk di transaksi checkout), return
   `{enabled: true, company, date, queue_number}`.
4. Jika `enable_pos_queue` = 0 di company tersebut → return
   `{enabled: false}` tanpa menyentuh counter (client tidak mencetak
   blok antrian).

Alasan commit-terpisah: dua kasir online di outlet yang sama memanggil
API ini hampir bersamaan; row-lock + commit langsung = nomor dijamin
berurutan unik, tidak bergantung pada kapan invoice akhirnya disimpan.

## 4. Checkout (SPA)

Saat checkout, jika queue aktif untuk company profil aktif:

1. Online: `await get_next_queue_number(pos_profile)`.
2. Offline / API gagal: lanjut lokal (lihat §5).
3. Nomor distempel ke payload invoice: `pos_queue_number` (Int) dan
   `pos_queue_date` (Date) — custom fields di **Sales Invoice**
   (pola `CUSTOM_FIELDS`), ikut terkirim saat sync invoice offline.
4. Cache device diperbarui (lihat §5).
5. Print berjalan seperti biasa — template membaca field (lihat §6).

Pembatalan invoice: nomor hangus (gap). Normal untuk sistem antrian;
tidak ada realokasi.

## 5. Offline — cache & penyembuhan sync

Cache per device, localStorage, key `pos_queue_last::{company}`:
`{date: "YYYY-MM-DD", number: <int>}`. Diperbarui setiap alokasi
(online maupun offline). "Hari ini" di cache = tanggal lokal device
kasir; server memakai server date (selisih hanya relevan saat
menyeberang tengah malam — diterima).

Alokasi offline:

- cache ada dan `date` == hari ini → `number + 1`;
- cache tidak ada / beda tanggal → mulai dari 1;
- hasil ditulis kembali ke cache dan distempel ke invoice.

Penyembuhan sync (server): hook `on_submit` Sales Invoice — jika
`pos_queue_number` terisi, naikkan counter (company,
`pos_queue_date`) ke `max(current, nomor tsb)` dengan lock yang sama
dengan §3. Efeknya kasir online berikutnya tidak pernah memakai ulang
nomor yang sudah tercetak di struk offline. (Hook on_submit dipilih
karena invoice offline melewati jalur sync → submit.)

Sisa risiko yang diterima: dua kasir offline **bersamaan** di satu
outlet bisa menghasilkan nomor sama. Sudah disepakati user.

## 6. Print — tiga template

Blok antrian hanya muncul jika `pos_queue_number` terisi. Posisi:
**elemen paling atas** struk, sebelum nama company. Format:

```
      NO. ANTRIAN
         048
```

Label kecil ("NO. ANTRIAN") + angka besar tebal, center, zero-pad
3 digit (`048`); di atas 999 tampil apa adanya (`1000`). Ukuran font
angka dikalibrasi mengikuti font scale yang sudah ada (ikut
`fontScale` lane receipt/crew, tidak ada knob baru).

1. **Print format server "POS Next Receipt"** (Jinja): blok di atas
   header company. Catatan rilis: fixture print format wajib bump
   `modified` agar migrate me-sync.
2. **Template offline client** `buildReceiptHTML` (`printInvoice.js`):
   blok yang sama, memakai `pos_queue_number` dari payload invoice
   (offline invoice membawa field ini karena distempel saat checkout).
3. **Crew slip** `buildCrewSlipHTML` (`crew_slip.js`): blok yang sama
   di paling atas slip.

## 7. Format nomor

- Integer tanpa prefix, zero-pad 3 digit.
- Tidak ada prefix per outlet/device (keputusan: tampilan konsisten).

## 8. Yang tidak termasuk (out of scope)

- Layar panggilan antrian (display/kiosk).
- Reset per shift.
- Prefix per device.
- Nomor di EOD/SALES RECAP.

## 9. Testing

Backend (`pos_next`/tests, dijalankan via pola `_pn_run_tests.py`
serial di container):

- alokasi berurutan unik untuk satu company;
- counter terpisah antar company dan antar tanggal (reset harian);
- `enable_pos_queue` = 0 → API tidak mengubah counter;
- on_submit invoice dengan `pos_queue_number` lebih besar → counter
  terangkat; lebih kecil → counter tidak turun.

Frontend (vitest, `POS/src/utils/print/`):

- cache offline: lanjut +1 hari yang sama, reset ke 1 beda hari;
- blok antrian dirender di customer template saat field terisi, tidak
  dirender saat kosong;
- crew slip memuat blok antrian di posisi teratas;
- checkout offline menstempel `pos_queue_number`/`pos_queue_date` ke
  payload invoice.

## 10. Rollout

1. `bench migrate` untuk KEDUA site (`posnext.localhost`,
   `erpnext16.localhost`) — custom fields + doctype + print format.
2. Aktifkan `enable_pos_queue` di Company outlet pilot.
3. Verifikasi di device iMin: struk online, crew slip, lalu simulasi
   offline (devtools offline) → struk tetap bernomor.
