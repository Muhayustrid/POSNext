# Checklist Verifikasi Pasca-Update ke `main` (987aab5)

Produksi saat ini: `e9c6b2b` (9 Sep). Target: `main` HEAD (18 Sep) — ±45 commit.
Tiga perubahan besar yang ikut: print settings terpusat, sales recap, POS Invoice jadi default.

## Sebelum update

- [ ] Update di luar jam operasional, **semua shift sudah ditutup** (tutup EOD dulu di semua outlet).
- [ ] Pastikan backup site otomatis Frappe Cloud jalan (atau trigger manual).
- [ ] Catat posisi commit lama untuk rollback: `e9c6b2b`.

## Saat update (Frappe Cloud: pull app + migrate)

- [ ] Di log migrate harus muncul patch **`pos_next v2_6_0: set_print_mode`** — ini wajib jalan.
      Patch ini menggabungkan flag lama (`silent_print` di POS Settings + `print_receipt_on_order_complete`
      di POS Profile) menjadi satu field `print_mode`. Idempotent, tidak destruktif.
- [ ] Build assets POS sukses (tidak ada error webpack/vite di log).

## Pasca-migrate — cek setting (Desk, 5 menit)

- [ ] Buka **POS Settings** untuk tiap profile: `print_mode` harus terisi.
      Dulu silent_print / print-on-complete aktif → **Auto**. Selebihnya → **Manual**.
      Tidak ada yang boleh kosong; profile tanpa row settings otomatis dibuatkan (enabled).
- [ ] Semua knob print sekarang ada di SATU tempat: POS Settings per profile
      (paper width, cut, copies, delay, feed/tail dots, font scale, line spacing,
      margin, crew slip, EOD, queue) — tidak lagi tersebar di device/profile.
- [ ] `print_driver` sesuai perangkat (iMin vs fallback).

## Smoke test kasir — pilih satu outlet pilot dulu

Alur invoice baru (perubahan terbesar):

- [ ] Buka shift → jual satu item → submit. Invoice harus tersimpan sebagai **POS Invoice**
      (default baru; cek di Desk listview POS Invoice).
- [ ] POS Invoice tersebut langsung punya entri **Stock Ledger + General Ledger** sendiri
      (tidak menunggu konsolidasi akhir shift).
- [ ] Jalur kredit tersembunyi di mode POS Invoice (tidak ada opsi credit yang nyasar).
- [ ] Invoice history & return tampil untuk **kedua** doctype (POS Invoice + Sales Invoice lama).

Print (setting terpusat + gate `print_mode`):

- [ ] Mode **Manual**: tombol Print muncul, print saat diklik.
- [ ] Mode **Auto**: receipt keluar otomatis saat order complete.
- [ ] Mode **Off**: tidak ada print sama sekali.
- [ ] Ubah satu knob di POS Settings (mis. `imin_print_copies`) → kasir print lagi →
      knob terpakai tanpa refresh device. Layout kini diselesaikan dari config server;
      host DirectPrint tetap di device.

Sales recap (fitur baru):

- [ ] Menu management (icon clipboard, "Sales Recap") → dialog terbuka.
- [ ] Filter **shift** (recap shift berjalan/terakhir) dan **periode** (rentang tanggal) sama-sama jalan.
- [ ] Angka mencakup gabungan POS Invoice + Sales Invoice lama non-konsolidasi.
- [ ] Tombol Print keluar hasil EOD-style (nested, seperti sheet EOD).

Tutup shift (EOD):

- [ ] Closing summary benar: items sold dibaca dari child table masing-masing doctype.
- [ ] Setiap invoice masuk kolom child-nya sendiri di entri closing.
- [ ] Stok efektif sudah mengurangi POS Invoice yang belum dikonsolidasi (kalau masih ada jalur lama).

## Kalau bermasalah

- Rollback: kembalikan app ke `e9c6b2b` di Frappe Cloud lalu migrate balik.
  Patch `set_print_mode` hanya menulis field baru — aman terhadap data lama.
  POS Invoice yang sudah terbuat tetap doctype valid (standard ERPNext).
- Jangan lupa: `feat/bakery-pos-capabilities` (14 commit promotion) dan `compat-v16`
  memang BELUM ada di `main` — jangan cari fitur itu pas testing.
