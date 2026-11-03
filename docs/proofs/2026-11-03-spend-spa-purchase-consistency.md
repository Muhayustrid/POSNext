# Bukti: penyelarasan SPA Spend dengan Purchase Order

Tanggal: 2026-11-03. Perubahan disetujui user setelah pemeriksaan dev; commit/push dan fast-forward main diotorisasi terpisah.

## Cakupan

- Riwayat sebagai awal; klik baris membuka detail PI/JE/PE yang dibatasi company dan penanda POS Spend.
- Form mengikuti struktur PO: breadcrumb, Informasi Transaksi, tabel dengan header/baris sejajar, Catatan & Lampiran dan Ringkasan.
- Pemilih Template/sumber bayar memakai AutocompleteSelect; nama barang utama, kode/satuan pendamping. Supplier dicari melalui endpoint Spend berotorisasi, bukan pencarian PO internal-only.
- Daftar ringkas tanpa kartu lampiran/tombol pembatalan per baris; lampiran dan pembatalan tersedia di detail.
- Payload retry dan client_request_id dipertahankan; navigasi/reopen menu diuji. Tidak menambahkan offline Spend, draft server, pajak/stok atau konversi UOM.

## Verifikasi

- Full frontend terakhir: 89 berkas, 1247 tes OK (`/tmp/spend-po-aligned-full.log`).
- Regresi terfokus Spend/menu/PO/PR: 107 OK (`/tmp/spend-layout-tests.log`).
- Backend Spend sebelum penambahan detail: API36 + accounts30 + Template16 + cancel11 + shift14 + setup4 =111 OK.
- Setelah penambahan detail: API37 OK (`/tmp/spend-detail-be-green.log`), termasuk nama item/supplier dan penolakan company asing. Seluruh enam module tidak dijalankan ulang setelah penambahan detail; backend tidak berubah dalam koreksi layout terakhir.
- Tes ownership, scope, list-first, detail/cancel dan struktur form ditulis sebelum implementation terkait dan diamati gagal lalu lulus.
- Build terakhir exit0 (`/tmp/spend-po-aligned-build.log`). CRLF-aware diff check OK; Ruff backend OK pada koreksi detail.
- Harness sintetis memakai ruang sidebar244px, capture daftar/detail/PI/JE pada1280/820/390/2000; tidak ada overflow dokumen dalam assertion. Ini bukan E2E transaksi backend. Capture lokal .impeccable/review/spend, tidak dimasukkan ke commit.
- Review independen tidak selesai: peluncuran reviewer salah memakai isolasi, dihentikan tanpa verdict. Tidak ada klaim clean review. User menerima tampilan dev setelah koreksi dan mengotorisasi commit/push/merge.

## Deployment dev

- Backup terakhir `backups/dev-20261103-130848`; rebuild dev backend exit0, clear-cache exit0.
- HTTP memverifikasi bundle `POSSale-YwkRCQbm.js` memuat marker informasi, catatan, ringkasan dan grid baru.
- Pembacaan detail native sebelumnya diverifikasi tanpa writes: PI-RDSKY-261103001 satu baris12345; ACC-JV-2026-00074 satu baris30000 dan satu lampiran.
- Tidak ada transaksi baru dibuat untuk smoke test, tidak ada perubahan prod.

## Batas

Pemeriksaan visual seluruh state dan review independen belum selesai. Tes frontend menggunakan toolchain host yang secara eksplisit diizinkan user karena node_modules bind mount tidak memiliki Rollup Linux; backend tetap dalam Docker. Artefak lokal/harness/config tidak di-commit. Production memerlukan deployment terpisah dengan migrasi Company Scope dan rebuild frontend.
