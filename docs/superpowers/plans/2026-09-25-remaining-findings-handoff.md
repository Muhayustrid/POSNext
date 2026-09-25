# Handoff Temuan Tersisa — sesi berikutnya (disusun 25 Sep 2026)

Konteks: remediasi audit 6 grup SUDAH SELESAI, di-commit, dan di-push ke
`origin/security-audit-fixes` (`869a7fd..131a813`: G1 `4dd7e1c`, G2 `4109803`,
G3 `56f9a3b`, G4 `41f3bb4`, G5 `5494e41`, G6 `131a813`). Verifikasi keamanan
deploy sudah dilakukan (migrate 2× idempoten, smoke 403 elegan, loyalty
terlindungi) — lihat `docs/DEPLOY_CHECKLIST_SECURITY_AUDIT_FIXES.md`.

Dokumen ini = daftar kerja TERSISA, diturunkan dari
`docs/superpowers/plans/2026-09-23-full-audit-handoff.md` (dokumen audit
verifikasi) setelah direkonsiliasi 25 Sep: plan 6 grup ternyata hanya
memetakan PERF-01..06 + PERF-08, sehingga 14 temuan performa sedang/rendah
belum pernah dikerjakan (terverifikasi di kode 25 Sep).

## §0. Aturan keras (warisan, WAJIB dibaca dulu)

1. **DILARANG git commit/push tanpa perintah eksplisit user.**
2. Test backend HANYA via: `docker exec -w /workspace/development/frappe-bench erpnext16_dev-frappe-1 ./env/bin/python apps/pos_next/pos_next/_pn_run_tests.py <modul>` — SERIAL, satu modul per perintah. JANGAN `bench run-tests`. Residu: pastikan `POS Next Global Settings`.invoice_type kembali `POS Invoice`.
3. Frontend di HOST: `npm --prefix POS run test:run` + `npm --prefix POS run build`. JANGAN `bench build` (container OOM MariaDB). JANGAN biome/lint/format repo-wide (panic #4574).
4. Python host RUSAK — container only. `.py` berubah → restart serve (`pkill -f bench_helper` lalu `docker exec -d ... serve --port 8000 --noreload` dari `sites/`).
5. Subagent container WAJIB serial (runner paralel = deadlock 1213).
6. TDD: tiap fix wajib test gagal-dulu (atau bukti EXPLAIN before/after utk item index/query).
7. Tanpa em dash di string UI. Gate profil = `_assert_profile_access` (packages.py:46).

## §1. PERF-sisa — 14 temuan (kandidat "grup 7 performa", satu sesi)

Sumber: audit §6. Skala acuan audit: 50k item, 1jt invoice.

### [PERF-07] sedang — subquery shift di sales recap
`pos_next/services/sales_recap.py:79-81`: scope memakai
`posa_pos_opening_shift IN (SELECT os.name FROM tabPOS Opening Shift os WHERE ...)`
padahal list `shifts` SUDAH diambil di baris 67. Fix: ganti subquery dengan
`IN %(shifts)s` (parameter list yang sudah ada). Verifikasi: hasil recap
identik + EXPLAIN tanpa subquery dependent.

### [PERF-09] sedang — TERBERAT: pencarian item CONCAT LIKE
`pos_next/api/items.py:1257-1265`: `CONCAT(...13 kolom...) LIKE '%kata%'` per
kata. Fix: index FULLTEXT (MySQL ngram utk multi-kata) ATAU prefix lookup.
PILIHAN DESAIN: FULLTEXT mengubah hasil (stopword/minimum token); decision
point sebelum eksekusi. Verifikasi: latency search 50k item + hasil set
dipetakan sadar (bukan silent change).

### [PERF-10] sedang — 5 index doctype JSON
Set `"search_index": 1` + migrate (model-sync otomatis buat index di situs
existing — TERBUKTI pola ini dari grup 6 utk pos_closing_shift; JANGAN buat
patch kecuali tabel core):
- `pos_opening_shift.json`: `status`, `posting_date` (dipakai bootstrap.py:180)
- `offline_invoice_sync.json`: `status` (dipakai invoice_type.py:69)
- `wallet_transaction.json`: `reference_name`, `transaction_type` (dipakai wallet_transaction.py:481-485)
Verifikasi: EXPLAIN pakai index + migrate idempoten.

### [PERF-11] — wallet list invoice tanpa limit
`pos_next/api/wallet.py:238-258`: `get_all` invoice customer tanpa limit +
`get_value("Mode of Payment")` per baris. Fix: set metode wallet sekali di
luar loop + batasi draft pending.

### [PERF-12] — FE: hasil pencarian dirender semua
`POS/src/components/sale/ItemsSelector.vue:1158-1161`: tanpa virtual
scroll/paginasi. Fix: virtualisasi ATAU batas 50/halaman (pilih yang lebih
murah; virtualisasi menyentuh UX — keputusan kecil).

### [PERF-13] — build: manualChunks
`POS/vite.config.js:225`: tanpa `manualChunks`, chunk limit dinaikkan 1.5MB.
Fix: pisah `qz-tray`, `html2canvas`, `dexie`, `frappe-ui`. Verifikasi: build
hijau + ukuran chunk turun + SW precache tetap absolut (plugin rewrite grup 4).

### [PERF-14] — batch N+1
`pos_next/api/items.py:2391-2396`: per item `get_batch_qty` +
`get_cached_doc("Batch")`. Fix: single join query.

### [PERF-15] — packages N+1
`pos_next/api/packages.py:286, 310, 457`: `get_value("Item", ..., "is_stock_item")`
per komponen. Fix: bulk dict (pola grup 5 PERF-05 di price_group.py).

### [PERF-16] — promotions per-scheme
`pos_next/api/promotions.py:205-238, 282-290`: `db.count` + `get_doc` per
scheme. Fix: GROUP BY + bulk child.

### [PERF-17] — exists redundan
`pos_next/pos_next/doctype/pos_closing_shift/pos_closing_shift.py:174, 214`:
`frappe.db.exists` sebelum `get_cached_doc`. Fix: try/except DoesNotExistError.

### [PERF-18] — loop set_value cleanup
`pos_next/tasks/cleanup_expired_promotions.py:41`: `set_value` per rule.
Fix: satu UPDATE bulk `IN %(names)s`.

### [PERF-19] — get_doc master
`pos_next/api/shifts.py:116-118, 176-177`: `frappe.get_doc` POS
Profile/Company → `get_cached_doc`.

### [PERF-20] — dobel cache Workbox
`POS/vite.config.js:132, 175-184`: aset hash precache di-cache LAGI via
runtime `pos-assets-cache`. Fix: hapus pola runtime itu. Verifikasi: vitest
SW terkait + buka /pos dev.

(DIPINDAI: PERF-21 dianggap terpenuhi tak langsung oleh index COR-BE-01 di
`tabPayment Entry Reference`; `reference_no` sendiri tak lagi dikueri kode kita.)

**Estimasi**: 11 item mekanis (07, 10, 11, 14, 15, 16, 17, 18, 19, 20 +
13) + 2 keputusan-kecil (09 FULLTEXT, 12 virtualisasi). Orkestrasi disarankan:
BE-A (07, 10, 11, 14..16, 18, 19) ∥ FE (12, 13, 20) ∥ BE-B (17 menyentuh
pos_closing_shift — serial container tetap dijaga).

## §2. SEC-23 [MEDIUM] — BUTUH KEPUTUSAN USER (jangan dikerjakan diam-diam)

`pos_next/overrides/pricing_rule.py` jalur offline sync: replay percaya rate
klien (`ignore_pricing_rule`). Fix = re-pricing server-side saat replay, TAPI
itu mengubah perilaku offline (invoice yang dibuat offline bisa berharga
beda saat replay). Ini trade-off produk, bukan bug murni. Bahan diskusi:
seberapa besar risiko kasir curang vs kebutuhan offline jujur di lapangan.

## §3. CLN §7 — opsional (branch terpisah, butuh persetujuan terpisah)

Lengkap di `2026-09-23-full-audit-handoff.md` §7: −5.215 baris, −12 deps.
Sudah terpetakan per-kelompok (BE delete/yagni/shrink/stdlib/native + FE + deps).

## §4. Utang follow-up non-audit (terdokumentasi dari review grup 3-6)

1. **id.csv full re-sync**: 895 string belum terjemah, 838 stale (duplikat
   kunci sudah dibersihkan grup 6; ini maintenance `bench get-untranslated`).
2. **`get_wallet_info` belum di-gate** (adik dari gate grup 6; backlog reviewer).
3. **Dialog SPA tutup shift belum menampilkan `pending_printed_drafts`**
   (backend sudah kirim angkanya — grup 6; tinggal UI + copy).
4. **Ruff ignore F401/F403/F405 menutup bug nyata** — ketatkan = kerja CLN.
5. **2 flake test posisi-dependent** (cashier_permissions, uninstall_coverage):
   lulus individual & berpasangan; hanya gagal dalam sapuan panjang. Akar =
   situs dev bersama + data residu antar modul. Proper fix = profil/fixture
   terisolasi per modul (sama dengan akar known-issue lama).
6. **test_promotions**: 10 error mode-dependent (bukan regresi — terbukti
   identik di kode pra-grup-6). Akar: modul tanpa opening-entry fixture +
   gate R3 + sensitivitas `invoice_type`. Proper fix = profil khusus modul.
7. **POS Production Log**: `POSNext Cashier` tak punya `write` → alur
   `create_production` kasir gagal di `log.submit()` (pra-audit, grup 3 mencatat).
8. **`reverse_wallet_transactions_for_return` hardcode "Sales Invoice"** (grup 3).
9. **SW update guard multi-tab + `wb.update()` periodik** (MINOR reviewer grup 4).
10. **Cap halaman sync + predikat `_item_from` bersarang** (NIT reviewer grup 5).

## §5. Snapshot state (25 Sep, setelah push)

- Branch `security-audit-fixes` = origin (6 commit audit). main tetap di
  belakang — merge/PR menyusul keputusan user.
- Versi app 2.12.0 (3 manifest sinkron, dijungjung `test_release_hygiene`).
- Situs dev `posnext.localhost`: invoice_type `POS Invoice`; role/workspace/
  docperm mirror utuh; flag dev: `allow_settings_reclaim=1` (site),
  `server_script_enabled=1` (common) — JANGAN ditiru ke production.
- Serve dev jalan (port 8000→host 8001, `--noreload`, site
  `roti-posnext-test.localhost`); log `/tmp/web-pos.log`.
- Deploy production: ikuti `docs/DEPLOY_CHECKLIST_SECURITY_AUDIT_FIXES.md`
  (SELECT pra-cek PE legacy + build frontend manual + migrate).
