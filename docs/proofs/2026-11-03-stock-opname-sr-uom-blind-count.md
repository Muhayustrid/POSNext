# Bukti sementara — SR UOM blind count

Tanggal: 2026-11-03. **Status: belum siap rollout.** Review independen gabungan sudah selesai; sebagian verifikasi dan tindak lanjut masih berlangsung. Dokumen ini bukan persetujuan commit, merge, atau deployment.

## Lingkungan dan batas scope

- POS worktree: `.claude/worktrees/pos-next-sr-uom-blind-count`, branch `worktree-pos-next-sr-uom-blind-count`, base `9f8db97f8af91b127ab31d07a0129b6ebda3ca83`; patch belum di-commit.
- Warehouse worktree: `/Users/rotiropi/ERPNext-Project/development/frappe-bench/.worktrees/warehouse_app-sr-uom`; patch akses milik sesi warehouse, belum di-commit.
- Runtime khusus: Compose `erpnext-sr-uom-test`, container `erpnext-sr-uom-test-backend-1`, site `sr-uom-test.localhost`. Kedua source worktree dipasang read-only; data berada di volume sintetis terpisah.
- Frappe 16.52.0, ERPNext 16.51.1. Dev/prod berisi data restored tidak dipakai untuk pengujian ini.
- Scope adalah permukaan Stock Reconciliation/opname; bukan pembatasan seluruh akses stok ERP atau harga jual POS.
- Kredensial pengujian disimpan privat mode0600 di site; tidak dimuat dalam bukti ini.

Semua nama log berikut relatif terhadap `.superpowers/sdd/2026-11-03-stock-opname-sr-uom-blind-count/runtime/`.

## Hasil terfokus

| Pemeriksaan | Hasil dan bukti |
|---|---|
| Enam modul backend terkait | `rpc-suffix-rerun.log`: 123/123 lulus, setelah perubahan normalisasi suffix RPC v1 |
| Contract tests warehouse dalam container | `warehouse-combined-current.log`: 85/85 lulus; memakai stubs, bukan bukti runtime standalone |
| Field permission dan urutan setup metadata | `metadata-order-current.log`: 110/110 pada revisi sebelumnya; setup kedua urutan mempertahankan field sensitif level1 dan count/UOM/factor level0 |
| Migrasi native lengkap kedua urutan aplikasi | `migrate-both-orders.log`: warehouse→POS dan POS→warehouse lulus; MRO dan metadata diperiksa, urutan semula dipulihkan |
| Hook/MRO/HTTP kedua urutan aplikasi | `app-order-after-migrate.log`: lulus pada revisi sebelum perluasan RPC terakhir |
| Stock qty/rate roundtrip | `roundtrip-current.log`: 13/13; qty10/rate123.456789 stabil saat save berulang; perubahan AFTER4 dengan faktor3 menjadi qty12 |
| Pemeriksaan whitespace | `git -c core.whitespace=cr-at-eol diff --check` lulus |

## HTTP aktual

- `http-fresh-create.log`: create dokumen baru tidak mengirim field sensitif; empat jalur baca blind; HQ tetap memperoleh valuation nonzero.
- `http-generic-writes-green.log`: native update, `frappe.client.save`, dan Desk `savedocs` blind, valuation HQ tersimpan tidak berubah. Injeksi rate/custom-rate/allow-zero ditolak403.
- `http-rpc-amount-current.log`: `client.set_value` unversioned/v1/v2 dan `client.save/compat` unversioned/v1 blind; proyeksi child melalui list/report tidak mengirim field sensitif. Injeksi amount pada existing row tidak mengubah amount/rate HQ.
- `http-new-input-rerun.log`: `client.insert` menolak tiga input rate403. Amount-only injection tidak dipertahankan: native menghitung amount=qty×valuation; respons create blind.
- `http-mixed-role-rerun.log`: Cashier+Stock User tetap blind pada REST/report; ekspor report, data export, print dan endpoint stock native ditolak403.
- `submitted-ledger-final.log`: cashier→Stock Manager→Accounts Manager sebagai persona terpisah berhasil sampai Approved/docstatus1. Qty, valuation, amount, serta SLE sesuai; empat jalur baca outlet pada dokumen submitted tetap blind.
- `workflow-response-green.log`: native `apply_workflow` kini mengembalikan respons blind setelah kebocoran direproduksi lewat HTTP dan tes envelope. Perbaikan hanya menambah rute RPC eksak.
- `import-equality-current.log`: import count17 (cocok) dan16 (berbeda) menghasilkan applied1/skipped0/errors[] yang sama; counted rows tetap ada tanpa field rahasia. Ini karakterisasi perilaku existing, bukan klaim bug RED→GREEN.
- `no-before-valuation-green.log`: suite terbaru **126/126 lulus**. Tes baru mereproduksi rate0 pada dokumen baru tanpa before-image yang melewati lookup native; normalisasi rate baru non-HQ ke None mengembalikan valuation tepercaya, tanpa mengubah rate existing. `http-zero-valuation-green.log` membuktikan create rate0 dihitung native, amount tidak memakai input penyerang dan respons blind.
- `perpetual-gl.log`: GL pada `_Test Company with perpetual inventory` **lulus**. Kasir menghitung blind (17→3 dan 5→8, rate10), lalu workflow cashier→SM→AM menghasilkan `MAT-RECO-2026-00139` Approved/docstatus1. SLE −140/+30, difference_amount −110; GL seimbang: Stock In Hand −110, Stock Adjustment +110. Lima jalur baca kasir pada dokumen submitted tetap blind, dan GL Entry/Stock Ledger Entry REST ditolak403. Sebelum probe, kolom accounting dimension (Department/Branch/Location) pada site sintetis dibuat ulang dengan fungsi native ERPNext karena sweep uninstall menghapusnya.

Filter respons dibatasi pada endpoint native tertentu dan envelope dokumen SR langsung; bukan scrubber rekursif/global. Native RPC v1 menormalisasi suffix slash, sehingga boundary memakai normalisasi yang sama. Tes suffix diamati gagal sebelum perbaikan (`rpc-suffix-red.log`).

## Kegagalan yang tetap dilaporkan

- Full backend sweep: `full-backend-current.log`: **1291 tes, 4 failures, 409 errors, 8 skips**. Semua nama ada di `full-backend-failures.txt`.
- Empat failures: `test_parents_within_allowlist`, `test_categories_grouped_and_sorted_by_total_then_amount`, `test_app_version_not_below_latest_patch_series`, `test_translation_file_has_no_duplicate_keys`.
- Pesan error dominan berkaitan antrean penuh, shift aktif pada shared profile, schema department dan mapping Bank Draft; kategorisasi ini bukan pembuktian akar masalah setiap error.
- `review-boundary-rerun.log` pernah menghasilkan tujuh PermissionErrors; pemeriksaan izin tersimpan serta rerun isolated/pasangan/gabungan kemudian lulus tanpa perubahan izin/kode. Penyebab intermiten belum terbukti.
- Antrean sintetis beberapa kali mencapai QueueOverloaded550. Native burst worker dipakai tanpa menghapus queue. Exit0 worker **bukan** seluruh job berhasil: `worker-rpc-suffix.log` memuat48 tracebacks. Log lainnya dipertahankan dalam ledger.
- Sweep memuat uninstall tests yang mengubah fixture site. Pemulihan native migrate dijalankan; keberhasilan probe terfokus tidak mengubah sweep menjadi lulus.

## Belum selesai / gate rilis

- Review independen selesai: tidak ada Critical yang terbukti. Satu bug scope terkonfirmasi: outlet dapat membaca SR foreign dengan set_warehouse kosong (HTTP200). Perbaikan default-deny sudah RED→GREEN, suite terfokus `final-scope-green.log`124/124 lulus dan `warehouse-less-http-green.log` membuktikan empat jalur baca403. Envelope lain, before-image valuation dan import equality masih perlu dituntaskan; report lokal `final-combined-review.md` mencatat adjudikasi.
- Runtime standalone warehouse tanpa pos_next.
- Background worker aktual kini terverifikasi: `background-verify.log` membuktikan101 rows Approved/docstatus1,101 SLE qty2/rate10 dan job berjalan dengan persona Accounts Manager+Stock User asli. Target tidak memiliki komentar Action Failed. Worker menggunakan container/source mounts yang sama; log worker memuat37 tracebacks job fixture lain, sehingga bukan klaim seluruh job bersih.
- `standalone-branch-rerun.log` membuktikan branch standalone mengikuti izin native nyata (cashier tanpa Company read ditolak, HQ diizinkan, mismatch ditolak), tetapi installed-app detection dimock. Ini belum menggantikan site standalone tanpa pos_next.
- Batch workflow/ledger dan seluruh variasi HTTP/actions.
- Suite terfokus sebelum merge: `pre-merge-suite.log` 126/126 lulus.
- Frontend Vitest belum berjalan karena node_modules/vitest tidak tersedia di worktree. Ruff tidak tersedia; tidak ada dependency dipasang.
- Full suite masih gagal. Task completion dan kesiapan rollout belum dinyatakan.

Tidak ada commit/push/merge/deploy, penghapusan volume, atau penulisan ke restored dev/prod yang dilakukan untuk pekerjaan ini. Rollout tetap memerlukan persetujuan terpisah dan aktivasi kedua proteksi beserta metadata sebelum akses dibuka.
