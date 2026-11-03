# Bukti: POS Spend tahap 1 (pembelian lokal dan pengeluaran outlet)

Spec: `docs/superpowers/specs/2026-11-02-pos-cash-out-design.md`
Plan: `docs/superpowers/plans/2026-11-02-pos-spend-stage1.md`
Commit (belum di-push): fabf7013, 7a9b4b5f, c8484ee1, 97783fd2, 81a71535; sidebar b2a827c5.

## Tes otomatis (stack fix, fix.localhost)

- Backend spend: 99 OK, termasuk race 2 koneksi (submit vs closing, cancel vs closing).
- Regresi: hanya baseline lama (cash_handover 3E stok PNXT Teh, session_summary 17F/6E fixture, pos_closing_print 19/20).
- Frontend purchase/spend/menu 121 OK; full suite 1243 OK + build OK.

## Rollout dev

- Backup ERPNext-Project `backups/dev-20261103-055601`, `docker/erp rebuild dev backend` exit 0.
- Bundle `POSSale-DdB5GiXF.js` memuat `submit_spend`; DocType POS Spend Template ada; Patch Log v2_36_0 ada.

## E2E browser di dev.localhost:8000 (2026-11-03)

Profil/company RS DR SOETARTO KOTA YOGYAKARTA (RDSKY), kasir `qa-spend-cashier@posnext.local`,
manager `qa-spend-mgr@posnext.local`. Dijalankan subagent Playwright; dokumen dan GL dicek ulang
lewat query DB di sesi utama.

| # | Skenario | Dokumen | Hasil |
|---|---|---|---|
| 1 | Buka shift, modal 200.000 | POPEN-RDSKY-26110301 | PASS |
| 2 | Penjualan tunai 5.000 | PINV-RDSKY-261103001 | PASS |
| 3 | JE "E2E Listrik" 30.000 Cash + lampiran | ACC-JV-2026-00074 | PASS: Dr 5230.001 Biaya Listrik 30.000 / Cr 1111.003 Kas Kasir 30.000, shift + shift account terisi |
| 4 | PI "E2E Perlengkapan" 12.345 Cash, nota E2E-NOTA-1 | PI-RDSKY-261103001 | PASS: Paid, grand_total = paid_amount = 12.345, Dr 5220.015 / Cr Kas Kasir (hutang 2111.001 net 0) |
| 5 | JE 10.000 dari QRIS (bukan sumber shift) | ACC-JV-2026-00075 | PASS: Cr 1121.005 Qris, tanpa shift |
| 6 | Idempotensi (submit_spend 2x request ID sama, via API) | ACC-JV-2026-00076 | PASS: panggilan kedua `duplicate: True`, satu dokumen. Lalu dibatalkan |
| 7 | Riwayat: nominal, status, thumbnail, tombol Batal | - | PASS |
| 8 | Batal dari SPA dengan alasan | ACC-JV-2026-00077 | PASS: GL dibalik (is_cancelled=1) |
| 9 | Tutup shift | PCLOSE-RDSKY-26110302 | PASS: Cash expected 162.655 = 200.000 + 5.000 − 30.000 − 12.345; 2 baris spend_entries; EOD/Sales Recap total_expense 42.345 |
| 10 | Batal setelah closing | ACC-JV-2026-00074 | PASS: SPA tanpa tombol; Desk (Administrator) ditolak "This spend belongs to a closed shift (PCLOSE-RDSKY-26110302)..." |
| 11 | Manager: menu, riwayat company, unduh lampiran | File 6eaf03d2eb | PASS |
| 12 | Tampilan 1280 / 390, label Indonesia | - | PASS |

Batas: skenario 6 diuji lewat API, bukan respons yang diputus di browser.
Screenshot: `/tmp/e2e-spend/` (lokal, tidak di-commit).

## Data dev yang dibuat/diubah

- User `qa-spend-cashier@posnext.local`, `qa-spend-mgr@posnext.local` (password di `/tmp/qa-spend-pw.txt` host).
- POS Next Global Settings `spend_reference_company`: kosong → OUTLET.
- POS Settings profil Soetarto: `posa_shift_spend_sources` = Cash (sebelumnya kosong).
- POS Spend Template "E2E Listrik" (JE, Cash + QRIS) dan "E2E Perlengkapan" (PI, ATK260016, supplier Lokal Supplier yang sudah ada).
- POS Shift Slot `clfbuujjs3` start_time sempat 05:00 → 04:00 (jam container di luar slot), sudah dikembalikan ke 05:00.
- Dokumen di tabel di atas tidak dihapus.
- Shift POPEN-RDSKY-26110203 ditutup sendiri oleh ropierpnext@gmail.com (PCLOSE-RDSKY-26110301), bukan oleh tes.
