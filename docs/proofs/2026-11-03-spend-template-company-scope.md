# Bukti: POS Spend Template — Company Scope dan kepemilikan aturan

Tanggal: 2026-11-03. Baseline `1f145cc9`; perubahan belum di-commit/push.

Spec: `docs/superpowers/specs/2026-11-03-spend-template-rule-ownership-design.md`.
Plan: `docs/superpowers/plans/2026-11-03-spend-template-rule-ownership.md`.

## Perubahan

- `allowed_items` dimiliki module POS Spend Template, binding import lama dipertahankan; controller tidak lagi bergantung balik pada resolusi akun.
- Select Company Scope: Selected Companies (default) / All Companies. Scope legacy kosong tetap Selected; kosong bukan All.
- All menyertakan seluruh company leaf, termasuk yang ditambahkan kemudian. Hak akses pengguna, role dan readiness tetap diperiksa.
- Tabel Companies tetap tersedia untuk override dan tidak dihapus ketika berpindah mode.
- Keterangan form mengikuti mode; terjemahan Indonesia +5, seluruh byte lama id.csv dipertahankan.

## Pengujian terbaru

Backend dijalankan serial dalam `erpnext-fix-backend-1`, site `fix.localhost`, melalui `_pn_run_tests.py` dengan `FRAPPE_STREAM_LOGGING=1`.

| Module | Hasil |
|---|---:|
| Template | 16 OK |
| Akun/readiness | 30 OK |
| Transaksi Spend | 34 OK |
| Cancel | 11 OK |
| Shift | 14 OK |
| Setup | 4 OK |
| Total | 109 OK |

- Desk node:test `spend_template.test.cjs`: 3/3 OK; seluruh `pos_next/tests/js/*.test.cjs`: 25/25 OK.
- Baseline sebelum refactor: Template11 OK; karakterisasi akun28 OK.
- Tes kepemilikan aturan: RED (fungsi belum diekspor pemilik baru) → GREEN.
- Tes scope: RED (5 kegagalan perilaku scope) → GREEN setelah implementasi+migrate.
- Tes keterangan Desk: RED → GREEN.
- Ruff check: OK; format check: enam berkas Python sudah terformat.
- `git -c core.whitespace=cr-at-eol diff --check`: OK. Flag mempertahankan konvensi CRLF terjemahan.
- Pemeriksaan byte-prefix id.csv: byte lama identik, lima baris baru valid.

Log lokal: `/tmp/spend-final-backend.log`, `/tmp/spend-scope-red.log`, `/tmp/spend-owner-red.log`.

## Review

Review Opus independen terhalang isolasi worktree dan dihentikan tanpa bypass atau verdict. User menyetujui self-review terfokus sebagai pengganti. Diff inti diperiksa untuk legacy scope, izin company, override, dan arah import; tidak ada defect penting yang terkonfirmasi.

## Rollout dan kompatibilitas

- Fix migrate exit0. Fix tidak memiliki Template lama tersimpan, jadi bukan bukti backfill dokumen lama.
- Dev sebelum deployment: E2E Listrik dan E2E Perlengkapan masing-masing mencakup tepat RS DR SOETARTO KOTA YOGYAKARTA.
- Backup dev: `backups/dev-20261103-095208`, exit0.
- Rebuild dev backend exit0; migrasi eksplisit dev.localhost exit0.
- Pemeriksaan pascamigrasi: kedua Template lama bernilai `Selected Companies` dan tetap hanya mencakup Soetarto; binding import lama sama dengan fungsi pemilik baru. Tidak diperlukan patch backfill tambahan.
- Browser dengan sesi qa-spend-mgr: getdoctype 200, field baru dengan opsi/default benar dan marker script keterangan baru tersedia. Membuka form E2E Listrik ditolak izin baca dokumen; tidak mengubah izin atau login untuk melewati penolakan. Pemeriksaan visual dan pergantian dropdown pada form nyata belum selesai.
- Probe Python awal mengeluarkan warning translation cache karena import sebelum inisialisasi Frappe; assertion schema/cakupan tetap lulus. Ini bukan hasil tes yang diabaikan; metadata terkonfirmasi ulang melalui request browser 200.

## Batas verifikasi

Full suite aplikasi di luar Spend tidak dijalankan. Tidak ada transaksi uji baru pada dev dan tidak ada perubahan production. Tidak mengklaim review independen selesai. Tidak ada commit/push.
