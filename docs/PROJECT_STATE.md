# PROJECT STATE — POS Next

Dokumen status proyek yang hidup: dibaca sekali untuk tahu ke mana proyek
berada. Diperbarui manual setiap milestone besar. Detail teknis lengkap ada
di `docs/superpowers/plans/` (handoff per fase) dan checklist deploy di
`docs/DEPLOY_CHECKLIST_SECURITY_AUDIT_FIXES.md`.

## Status rilis (per 27 September 2026)

| Hal | Nilai |
| --- | --- |
| Versi app | 2.13.0 (`pos_next/__init__.py` + `POS/package.json` + root `package.json`) |
| `main` | `847f87e` (= `origin/main`, di-push 27 Sep) |
| `security-audit-fixes` | `2d16754` (di belakang main; isinya sudah terserap via merge 26 Sep + fix/open-items) |
| Produksi (Frappe Cloud) | **MASIH `b6f7ae9`** — seluruh remediasi belum melindungi produksi |
| Site uji dev | `roti-posnext-test.localhost:8001` (bundle build) |
| Site test-runner | `posnext.localhost` |

## Tercapai (kronologi 22–26 Sep)

- **Remediasi audit keamanan penuh** — 7 grup di branch
  `security-audit-fixes` (`869a7fd..01e10b5`): SEC-01..22 + follow-up,
  correctness BE/FE, higienitas rilis, performa (PERF-01..20, termasuk
  FULLTEXT hybrid pencarian item 30–60× lebih cepat di 50k item + paginasi
  50). Final review SHIP, 0 fix-before-merge. Handoff:
  `docs/superpowers/plans/2026-09-23-full-audit-handoff.md` +
  `2026-09-25-remaining-findings-handoff.md`.
- **Grup 8 pra-merge `064c102`** — write Production Log untuk kasir, gate
  `get_wallet_info`, resolver doctype pada reversal wallet (submit POS
  Invoice dengan loyalty-to-wallet yang semula patah di baseline).
- **Verifikasi pra-merge + merge `f9511bd`** — merge `main` (role system
  2-persona) ke branch; konflik `shift_schedule.py` + 16 entri dobel fixture
  DocPerm dari auto-merge diselesaikan; 135 test dijalankan pada pohon merge.
- **Grup 9 `2d16754` — bug checkout kasir dua-persona** (ditemukan lewat E2E
  kasir nyata): ERPNext v16 `account_perm_check()` menuntut akses Account
  saat resolusi `debit_to`. Fix: Custom DocPerm **Account select=1** (tanpa
  read) untuk POSNext Cashier + Nexus POS Manager (fixture + DB),
  `get_sales_persons` ignore_permissions, test baru
  `test_cashier_checkout_permissions`, hygiene fixture test (Item Price
  reuse-or-create, sweep Wallet Transaction, role fixture session-summary).
- **E2E GUI penuh di situs uji (Administrator)** — jual tunai sampai invoice
  Paid (`ACC-PSINV-2026-00050`), tutup shift dengan rekonsiliasi tunai
  seimbang (`POSA-CS-26-0000015`, selisih 0), buka-shift ulang. Bukti:
  `gui-test-screenshots/e2e_*.png`.
- **E2E GUI penuh (kasir `kasir.pku@posnext.test`)** — pilih profil, buka
  shift kas awal 200.000, jual Rp 1.000, bayar tunai pas →
  `ACC-PSINV-2026-00051` **Paid, owner kasir** (server terverifikasi).
- **Push 26 Sep** — `main` + `security-audit-fixes` = `origin` di `2d16754`.

## Ditutup sesi 27 Sep (branch `fix/open-items` → commit `cfd89fc`, ter-merge ke `main`)

Seluruh item A1–C di bawah dikerjakan tanpa commit selama sesi berjalan
(catatan "Tidak ada commit/push" di tiap item adalah status saat item itu
ditulis). Setelah E2E ronde-3 lulus penuh, semuanya di-commit — sempat
`04a753c`, di-amend menjadi `cfd89fc` untuk memuat perbaikan test guard —
lalu fast-forward merge ke `main` dan di-push (27 Sep).

- **A1 — Cancel invoice membatalkan Wallet Transaction ter-link.** Cancel
  invoice kini membatalkan Wallet Transaction yang ter-link: helper baru
  `cancel_wallet_transactions_for_invoice` (`pos_next/api/wallet.py:158-200`)
  mengambil semua WT docstatus=1 dengan reference_doctype=doc.doctype +
  reference_name=doc.name tanpa filter transaction_type, cancel satu per satu
  dengan flags.ignore_permissions=True, dan kegagalan mana pun di-frappe.throw
  (bukan ditelan) sehingga seluruh cancel roll back dan invoice tetap
  submitted. Helper dipanggil di `sales_invoice_hooks.before_cancel`
  (`pos_next/api/sales_invoice_hooks.py:237-243`), di luar try/except JE yang
  memang best-effort — guard is_consolidated yang ada dipakai, is_return tidak
  di-skip, perilaku lain hook tidak diubah; sink yang sama menjangkau Sales
  Invoice (hooks.py:166) dan POS Invoice via pos_invoice_events.before_cancel.
  WT.on_cancel otomatis mereversal GL dan menyegarkan saldo (dihitung realtime
  dari GL Entry). Tiga test baru di
  `pos_next/api/test_wallet_return_hardening.py` (class
  `TestInvoiceCancelCancelsLinkedWalletTransactions:820`): (a) cancel invoice
  ber-loyalty-WT sukses tanpa LinkExistsError, WT jadi docstatus 2, saldo
  GL-derived kembali ke nilai pra-submit; (b) cancel invoice retur
  ber-refund-credit-WT sukses, WT cancelled, original tak tersentuh; (c)
  invoice tanpa WT cancel bersih. Kedua test baru ini menuntut perbaikan pada
  test itu sendiri: assert saldo memakai saldo realtime dari GL
  (Wallet.current_balance terbaca stale saat cancel invoice karena hook
  menjalankan sebelum reversal GL invoice sendiri — akun wallet di site ini
  berlipat sebagai default receivable), dan assert WT memakai delta snapshot
  nama WT karena seri penamaan meng-recycle nama invoice yang di-delete
  tearDown (query berbasis nama saja menghitung WT peninggalan invoice mati —
  itu penyebab kegagalan pertama, bukan bug di kode fix). Catatan operasional:
  dua skrip diagnostik sementara yang dijalankan saat investigasi meninggalkan
  leftover di site (2 invoice submitted, 2 shift, 2 stock entry, pointer
  loyalty_program customer menunjuk LP terhapus); semuanya sudah dibersihkan
  dan nilai customer dikembalikan ke None (diverifikasi via historis:
  aktivitas loyalty customer baru mulai 2026-09-25 seiring test COR-BE-16).
  Verifikasi: `docker exec -w /workspace/development/frappe-bench
  erpnext16_dev-frappe-1 ./env/bin/python apps/pos_next/pos_next/_pn_run_tests.py
  pos_next.api.test_wallet_return_hardening` → 'Ran 17 tests ... OK
  (skipped=3)', dijalankan dua kali (stabil). Tidak ada file frontend yang
  diubah, jadi npm test/build tidak dijalankan. Tidak ada git commit/push.
- **A2 — Audit rate manual mencakup POS Invoice.** Log_manual_rate_edit kini
  menerima parameter doctype (default DOCTYPE_SALES_INVOICE) yang dipakai
  sebagai reference_doctype Comment (sebelumnya hardcoded "Sales Invoice"),
  dan lane audit di submit_invoice diperluas dari hanya Sales Invoice ke kedua
  doctype dengan meneruskan doctype ter-resolve. Modul test baru
  `pos_next/api/test_manual_rate_audit.py` membuktikan: rate manual 100→80 di
  POS Invoice menghasilkan tepat satu Comment audit dengan
  reference_doctype="POS Invoice" dan reference_name benar (dan bukan
  mis-reference ke Sales Invoice), perilaku Sales Invoice tetap terjaga, serta
  checkout tanpa flag manual tidak meninggalkan Comment. Semua test dijalankan
  sendiri via docker _pn_run_tests.py dan lulus; tidak ada perubahan frontend.
- **A5 — Cap replay offline + cap halaman customer sync.** Cap replay antrean
  offline ±50 invoice/siklus via SYNC_CONFIG.MAX_SYNC_BATCH (slice sebelum
  loop, penanganan gagal per-invoice tak diubah, sisanya ditunda ke siklus
  berikutnya dengan log), plus cap 100 halaman (CUSTOMER_SYNC_MAX_PAGES) di
  kedua loop paginasi customer (cache.js dan customerSearch.js) + 1 test
  vitest untuk cap-nya. Predikat `_item_from` di hq_monitoring.py dirapikan:
  where dari _si_window_where diteruskan apa adanya ke kedua union sehingga
  pembungkus ganda docstatus/is_pos hilang (branch sii 2x→1x, branch si
  3x→2x; sisa 2x di branch si adalah prefix milik _invoice_from sendiri yang
  dipakai 5 caller lain — di luar rentang yang diminta). Untuk sales_recap.py
  ±363: TIDAK ada perubahan — render SQL aktual membuktikan _item_from di
  sana meneruskan scope.where tanpa pembungkus docstatus/is_pos tambahan
  (docstatus hanya 1x per branch), jadi premis 'pembungkus ganda' tidak
  terpenuhi untuk file itu. Test: backend 49 OK (test_hq_monitoring +
  test_sales_recap + test_perf_be1_recap, serial via _pn_run_tests.py, site
  posnext.localhost), vitest host 558/558 OK (52 file). Tidak ada git
  commit/push.
- **A6 — Dua modul flake ditandai known-flaky.** Akar flake tidak ditemukan
  dalam 2 siklus penyelidikan, jadi sesuai kontrak kedua modul ditandai
  known-flaky di `pos_next/_pn_run_tests.py` (frozenset KNOWN_FLAKY baris
  68-89 + print anotasi baris 127-132 yang tercetak di stdout, exit code tidak
  diubah). SIKLUS 1 (audit statis + repro): (a) seluruh varian
  _set_invoice_type (`pos_next/tests/_posi_test_utils.py:25-31`,
  `pos_next/api/test_cashier_checkout_permissions.py:44-49`,
  `pos_next/api/test_submit_post_submit_hardening.py:35-39`) menghapus
  frappe.local._pos_next_invoice_doctype saat flip dan restore di
  tearDownClass, jadi residu invoice_type tidak menjelaskan; (c) satu-satunya
  modul yang menulis Custom DocPerm
  (`pos_next/api/test_docperm_mirror.py:78-82`) membersihkan barisnya +
  clear_cache(doctype) di tearDown, dan berjalan SETELAH
  test_cashier_permissions di urutan glob sapuan; (d) cache perizinan aman:
  frappe.set_user me-reset frappe.local.role_permissions
  (frappe/__init__.py:393) dan class-cleanup FrappeTestCase melakukan
  rollback + restore thread locals
  (frappe/deprecation_dumpster.py:573-580,602-607); (e)
  test_uninstall_coverage hermetik (cek file/mock tanpa baca DB,
  `pos_next/tests/test_uninstall_coverage.py`) sehingga residu DB tak mungkin
  menjatuhkannya. Bukti eksekusi: prefix sapuan persis untuk posisi #3
  (test_backdate_invoices + test_block_sale_toggle +
  test_cashier_checkout_permissions + test_cashier_permissions) lulus 2x (Ran
  54 tests, OK); chunk 11 predecessor terakhir + test_uninstall_coverage
  lulus (6/6 ok). Temuan sampingan di luar tugas: pos_next.test_promotions
  error 10 test saat dijalankan setelah test_shift_schedule dalam chunk
  (posisi-dependent juga) — perlu tugas tersendiri. IMPLEMENTASI: KNOWN_FLAKY
  di `pos_next/_pn_run_tests.py`; runner mencetak 'known-flaky modules in
  this run: ...' bila salah satu modul itu ikut dalam run, exit code tetap
  0/1 dari wasSuccessful(). VERIFIKASI: run berpasangan kedua modul → Ran 19
  tests, OK, EXIT=0, anotasi tercetak sebagai baris stdout terakhir; run
  modul non-flaky (pos_next.tests.test_release_hygiene) → EXIT=0 tanpa
  anotasi. Tidak ada perubahan frontend, jadi npm test/build tidak
  dijalankan. Tidak ada commit/push.
- **B1 — Sapuan gate percobaan 1 hijau (373 test).** Gate percobaan 1: 11
  error + 1 fail dari 373 test. Semua akar ditemukan dan diperbaiki tanpa
  commit. (1) SEPULUH error test_promotions: pada submit-of-existing-draft
  baris tiba dengan pricing_rules yang sudah di-clear saat draft save,
  sehingga loop SEC-04 menganggap baris offer jujur sebagai manual edit
  ('Rate editing is not allowed') dan atribusi pos_offer_item_rules
  ter-blank sehingga discount-code gate menuntut kode. Fix: loop
  _validate_item_rates kini membaca klaim via `_row_offer_claims`
  (invoices.py:835-851) — fallback ke atribusi server yang dipulihkan
  _reapply_item_offer_attribution, tanpa membaca nilai payload mentah. (2)
  test_transaction_level_discount: aturan transaksi ber-window
  (min_qty/min_amt) tidak pernah lolos verifikasi relay di draft save karena
  doc.total/total_qty baru dihitung calculate_taxes_and_totals SETELAH
  stash-merge. Fix: bootstrap total_qty/total dari baris (basis yang sama dgn
  doc.total) tepat sebelum verify_transaction_rule_names
  (invoices.py:1840-1857); baris adalah satu-satunya sumber agar payload
  tidak bisa membuka window. (3) FAIL test_honest_offline_replay_passes (rate
  10.000.000): offline_id deterministik per-hari + record Offline Invoice
  Sync yang tidak dibersihkan membuat dedup mengembalikan invoice live milik
  test lain yang namanya di-assign ulang oleh seri. Fix: offline_id unik
  per-run (_offline_id dgn timestamp microsecond) + tearDown menghapus record
  Offline Invoice Sync _PNXT_SEC23_%
  (`test_offline_replay_price_validation.py:139-156`, 451). (4) ERROR
  test_e1_delete_invoice: pointer audit POS Discount Confirmation Code
  .last_used_in_invoice dan baris ledger orphan (GL Entry
  voucher_no/against_voucher, Payment Ledger Entry
  voucher_no/against_voucher_no, Stock Ledger Entry voucher_no) milik invoice
  sebelumnya yang dihapus masih menunjuk ke nama yang diseri ulang, memblokir
  delete_doc. Fix: purge link orphan tsb di test sebelum owner-delete
  (`test_medium_gates_security.py:467-495`) — perilaku produk tidak diubah.
  Verifikasi akhir: sweep lengkap 25 modul (373 test, serial, site
  posnext.localhost, via _pn_run_tests.py) = OK (skipped=3), dijalankan dua
  kali berturut-turut; tambahan 54 test pada 5 modul invoice-flow lain
  (invoice_authorization, partial_payment_lock, backdate,
  credit_authorization, draft_update_conflict) juga OK. Frontend tidak
  diubah. Tidak ada commit/push.
- **B2+B3 — Fail-fast invoice offline yang ditolak server.** sync.js kini
  mengklasifikasi error via isPermanentSyncError() — exc_type
  'ValidationError' (status < 500) = permanen, langsung sync_failed + alasan
  manusiawi dari serverErrorMessage() (pesan terjemahan server / fallback
  __()) pada percobaan PERTAMA tanpa menunggu MAX_RETRY_COUNT/backoff; error
  jaringan/timeout/5xx tetap transien di jadwal backoff existing, dan retry
  manual via retryOfflineInvoice tetap berfungsi. OfflineInvoicesDialog.vue
  tidak perlu diubah — field konsisten (invoice.sync_failed+error di render
  di baris 143-150, semua string UI-nya sudah via __()); rantai pesan dijamin
  manusiawi dari sisi sync.js dan dikunci test. Ditambah 5 test fail-fast di
  sync.test.js dan 1 test render alasan di dialog test; seluruh suite
  frontend lulus. Tidak ada git commit/push.
- **A3 — pending_printed_drafts mengikuti mode doctype.** Count
  pending_printed_drafts di preview tutup shift tidak lagi hardcoded 'Sales
  Invoice': helper baru `_count_pending_printed_drafts` memakai resolver
  get_pos_invoice_doctype() — mode Sales Invoice menghitung draft
  posa_is_printed=1, mode POS Invoice mengembalikan 0 karena kolom
  posa_is_printed hanya ada di Sales Invoice (custom/sales_invoice.json) dan
  jalur close tidak pernah auto-submit POS Invoice (submit_printed_invoices
  early-return). Dua test baru di modul test pos_closing_shift yang ada
  membuktikan count benar di kedua mode (lulus: 2 tests OK), plus sibling
  test_perf_be1_closing + test_closing_shift_race (13 tests OK). UI
  ShiftClosingDialog.vue mendapat kartu info Desk-native satu aksen (ikon
  printer, biru, tanpa badge) saat pending_printed_drafts > 0 dengan copy
  Indonesia via __() '{0} struk tercetak akan otomatis di-submit saat shift
  ditutup'; gate npm test:run (564 tests, 52 files, semua lulus) dan build
  lulus. Tanpa commit/push.
- **A4 — Guard reload service worker.** Ganti reload tanpa syarat pada event
  'activated' service worker (`POS/src/main.js:105-132`) dengan guard dua
  lapis: reload hanya bila state idle — dibaca dari store existing
  usePOSCartStore().isEmpty (posCart.js:209) dan usePOSSyncStore().isSyncing
  (posSync.js:49), store tak terbaca dianggap aktif (fail-safe) — dan bila
  generasi SW memang baru: generasi diidentifikasi dari hash SHA-256 byte
  /sw.js (main.js:80-90), penanda tersimpan di localStorage key
  pos_next_sw_reloaded_generation (main.js:71-78), maksimal satu auto-reload
  per generasi; yang ditunda cukup dicatat lewat logger 'Main'. Tambah
  wb.update() periodik per jam + saat visibilitychange menjadi visible dengan
  throttle 60 detik (main.js:134-148), plus import dua store (main.js:29-30).
  Gate host hijau: `npm --prefix POS run test:run` → 52 file/564 test passed;
  `npm --prefix POS run build` → sukses (sw.js digenerate, guard terverifikasi
  ada di bundle index-CcDkRdjR.js); `biome check POS/src/main.js` menghasilkan
  3 temuan yang identik dengan versi HEAD (semua pra-eksisting, tanpa temuan
  baru). Catatan desain: penanda generasi bersifat global lintas tab
  (localStorage) sesuai kontrak — tab lain yang menerima event activated
  generasi yang sama tidak me-reload ulang; kasir dengan transaksi aktif
  dibiarkan berjalan (kontrak item 1) dan mengambil SW baru pada navigasi
  berikutnya. Tidak ada commit/push; tidak ada file Python berubah sehingga
  test module backend tidak dijalankan.
- **A7 — Script audit pra-deploy read-only.** Script audit read-only dibuat
  di `pos_next/audit.py` (fungsi run): (a) hitung invoice submitted (POS
  Invoice + Sales Invoice is_pos=1) dengan baris item rate < price_list_rate
  tanpa pos_offer_item_rules, dan (b) hitung Wallet Transaction docstatus=1
  yang referensinya invoice cancelled atau hilang; murni baca (UNION ALL +
  get_value), aman jika kolom/doctype belum ada (dilewati dengan catatan).
  Dijalankan di situs test posnext.localhost via `docker exec -w
  /workspace/development/frappe-bench erpnext16_dev-frappe-1 bench --site
  posnext.localhost execute pos_next.audit.run`: (a) 1 baris item di 1
  invoice, total diskon 1800.00 — SINV-OT2601, item CR001, rate 16200 vs
  price_list_rate 18000 (Sales Invoice, bukan return); (b) 17 dari 22 WT
  dicek bermasalah (17 invoice cancelled, 0 missing; antara lain duplikasi
  Loyalty Credit pada ACC-PSINV-2026-00464 dan 00460). Script + langkah
  menjalankan ditempel ke `docs/DEPLOY_CHECKLIST_SECURITY_AUDIT_FIXES.md`
  sebagai item 3 di §1 Pra-deploy (WAJIB) + isi lengkap di Lampiran §7
  (diverifikasi identik dengan file repo via diff), lengkap dengan angka
  pembanding situs test; produksi TIDAK dijalankan. Tanpa git commit/push.
- **C — Re-sync terjemahan `id.csv`.** Ekstraksi per-app via 'bench
  get-untranslated id <file> --app pos_next' (flag diverifikasi via --help)
  mengukur volume nyata 921/2672 pesan app (angka lama memang tercampur
  string core). Via diff terhadap set string terpakai (frappe
  get_messages_for_app = backend _() + metadata doctype + bundle public,
  ditambah 1662 literal __() dari POS/src dan string sidebar JSON), 458 key
  stale dihapus termasuk varian trailing-space 'Discount ' dan 'This coupon
  requires a minimum purchase of ' yang baris lamanya berisi \n literal
  sehingga tak pernah cocok di runtime; 926 terjemahan baru + 2 nilai kosong
  terisi ('Nos', 'POS Next') + 4 kunci multi-baris runtime ditambahkan →
  id.csv 2357 → 2829 record (merged dict id 10902 → 11456). Pemanggil
  dirapikan: `POS/src/utils/printInvoice.js:218` '"Discount "'→'"Discount"'
  dan :363 '__("Powered by ")'→'__("Powered by") + spasi di luar kunci
  (pemanggil sebenarnya, bukan CouponDialog.vue seperti tertulis di tugas);
  `POS/src/components/sale/CouponDialog.vue:396` diubah ke placeholder 'This
  coupon requires a minimum purchase of {0}' sesuai pola kode sehingga
  nominal kini ikut tampil. Gate `scripts/validate_id_csv.py` (baru) LOLOS:
  CSV valid 2829 record, tanpa duplikat, paritas placeholder utuh, tanpa
  CR/kontrol-char/whitespace liar — catatan: gate juga menemukan duplikat
  pra-eksisting di ar.csv & pt-br.csv (di luar lingkup tugas ini, tidak
  diubah). Verifikasi akhir: get-untranslated --app pos_next = 'all
  translated!' (file output tak dibuat = 0 sisa), seluruh 1662 string
  frontend terverifikasi terjemah setelah clear_cache, `npm --prefix POS run
  test:run` 564/564 lulus, `npm --prefix POS run build` sukses. Tanpa git
  commit/push.

  (Catatan dokumen: angka "2829 baris" di atas adalah jumlah record CSV;
  `wc -l` fisik 2857 karena record multi-baris — diverifikasi 27 Sep.)

## E2E browser & commit-merge (27 Sep)

Tiga ronde E2E di situs uji dengan kriteria yang tidak dilonggarkan; bukti
`gui-test-screenshots/e2e2_*.png` dan `e2e3_*.png`.

- **E2E ronde-2 — temuan E2E-01** (pra-eksisting di main, bukan regresi
  fix/open-items): SPA "Lihat Detail" kasir → `get_invoice` 417 karena
  Custom DocPerm POS Invoice kosong di situs (akibat doctype-reversal 25 Sep
  yang memindahkan mode faktur situs ke POS Invoice; fixture 2-persona 23 Sep
  hanya memuat Sales Invoice). T0–T2 ronde-2 lulus; T4 terblokir artefak
  otomasi (v-model kupon).
- **E2E ronde-3 — SEMUA LULUS** setelah dua perbaikan produk:
  1. **Fix E2E-01 lewat fixture, bukan kode**: 3 baris POS Invoice klon
     persis baris Sales Invoice per persona di
     `pos_next/fixtures/custom_docperm.json` (kasir: read/write/create/
     submit/print + if_owner delete; manager: penuh) + guard test
     (`test_docperm_fixture` POS Invoice di ALLOWED_PARENTS;
     `test_cashier_permissions` matriks POS Invoice + delete own draft).
     Fix ini menambal semua permukaan yang di-gate DocPerm (Desk, print
     endpoint, `get_invoice`), konsisten dengan desain 2-persona.
  2. **Fix cache terjemahan klien** (temuan T4 ronde-3): cache IndexedDB
     TTL 24h melewati refresh jaringan sehingga terjemahan baru tidak sampai
     ke klien hingga ≤24h pasca-deploy → `POS/src/utils/translation.ts`
     `init()` kini `forceNetwork: true` (klien offline tetap jatuh ke cache;
     `getFresh` pada error fetch mengembalikan cache).
  Hasil ronde-3: **T0** ACC-PSINV-2026-00055 Paid + "Lihat Detail" kasir
  terbuka; **T1** WT-2026-00002 minted → Desk-cancel rantai (invoice & WT
  docstatus 2, GL net 0, LPE terhapus); **T2** replay offline jujur →
  ACC-PSINV-2026-00057, manipulasi → `sync_failed` permanen dengan alasan
  Indonesia tampil di dialog Faktur Offline ("Pengubahan harga tidak
  diizinkan untuk POS Profile ini" — gate rate-edit; lapisan magnitudo
  SEC-23 sudah terbukti ronde-2), hapus baris via dialog → 0 Tertunda;
  **T3** kartu "1 struk tercetak akan otomatis di-submit saat shift
  ditutup" tampil di mode SI (flip invoice_type + seed draft
  `posa_is_printed=1`, lalu di-cleanup penuh) → tutup shift aktual
  **POSA-CS-26-0000016 Seimbang ✓ 0** (docstatus 1, grand_total 5.000;
  EOD print gagal dicetak di IAB = tombol retry by design) → shift baru
  **POSA-OS-26-0000048** milik kasir terbuka; **T4** kupon {0} →
  "Rp 5.000" (Indonesia pasca-fix terjemahan) + apply sukses Diskon 100.
- **Commit + push + merge** (otorisasi bersyarat user: "kalau udh lulus baru
  commit/push"): commit `04a753c` (33 file, +3890/−542) di-push sebagai
  branch baru `fix/open-items`. Re-run gate pra-merge lalu **menangkap 1 test
  baru yang belum pernah dijalankan**
  (`test_pos_invoice_delete_own_draft_only`) — gagalnya berlapis 3:
  POS Invoice wajib shift terbuka yang cocok profil, wajib ≥ 1 baris
  payments, dan rate di bawah price_list_rate pelanggan ber-price-group
  franchise terbaca diskon manual (gate kode head-office melempar). Fix di
  test: shift terbuka dibuat per-test + payments sebesar rate + pelanggan
  polos segar tanpa price group; di-amend ke commit → **`cfd89fc`** →
  fast-forward merge ke `main` + push (main = fix/open-items = origin).
  Gate akhir pohon: `test_docperm_fixture` 3 OK,
  `test_cashier_permissions` 15 OK, `test_manual_rate_audit` 3 OK,
  `test_offline_replay_price_validation` 11 OK,
  `test_wallet_return_hardening` 17 OK (skipped=3), vitest 564/564,
  build OK. PELAJARAN: test yang baru ditambahkan wajib dijalankan sebelum
  commit.

## Sesi 27 Sep ronde-2 — verifikasi "aman lokal" + penutupan item terbuka (belum commit)

Gate penuh dijalankan pada pohon `main` = `cfd89fc`: sweep backend penuh
**88 modul / 907 test** (serial via `_pn_run_tests.py`), vitest 564/564, build
OK. Situs `posnext.localhost` terverifikasi utuh (roles, Custom DocPerm
termasuk 2 baris POS Invoice, workspace, custom fields, 46 doctype) setelah
alarm palsu: output mock `test_uninstall_coverage` mencetak baris "Removing
roles/workspace" yang realistis — **cek `frappe.get_installed_apps` dulu
sebelum panik**.

Temuan & perbaikan (semua terverifikasi; **di-commit ke `main` atas
otorisasi user — belum di-push**):

1. **Root `package.json` 2.12.0 → 2.13.0** — bump versi di `cfd89fc`
   melewatkan root manifest; `test_release_hygiene` menangkapnya.
2. **`test_draft_invoices_security` gagal BAHKAN saat dijalankan sendirian**
   (5 error) — jebakan franchise customer memakan test-nya sendiri:
   `get_default_customer()` membalik "Mitra Swalayan Gombong (FRC)" dan item
   pertama situs, sehingga draft rate-10 terbaca diskon manual dan gate kode
   head-office melempar saat insert. Fix hermetik (pola grup-9): pelanggan
   polos segar tanpa price group + rate mengikuti Item Price daftar harga
   profil (bukan hardcode 10); 5/5 OK.
3. **Flake `test_promotions` — akar ketemu, fix permanen (bukan label
   flaky)**: modul tidak punya fixture opening-shift dan payload-nya
   hardcode Sales Invoice; saat situs di mode POS Invoice jalur submit
   menuntut shift terbuka → persis **10 error** (repro: flip mode → run).
   Fix: pin `SALES_INVOICE` untuk modul + restore baseline di tearDownClass
   (pola `_set_invoice_type`); 18/18 OK di KEDUA mode, baseline situs
   terjaga.
4. **Keluarga yang sama di 2 modul lain** — `test_invoice_authorization_security`
   (7 error) + `test_medium_gates_security` (3 error) saat ambient POS
   Invoice; fix pola sama, 15/15 OK. Kesimpulan: "flake posisi-dependent"
   yang tersisa ternyata akarnya satu — **mode `invoice_type` ambient situs
   uji**; suite kini robust di kedua mode. `KNOWN_FLAKY` runner tidak
   ditambah (2 modul lama tetap).
5. **CLN §7 quick-wins (keputusan 26 Sep: "aman saja") dieksekusi** —
   backend: hapus 4 fungsi mati 0-pemanggil (`get_item_stock`,
   `get_product_bundle_availability`, `promotions.search_items`,
   `OfflineInvoiceSync.create_sync_record`; ±217 baris; helper
   `_calculate_bundle_availability_bulk` tetap — 3 pemanggil lain).
   Ternyata sudah beres dari grup sebelumnya: `imin_probe.html` terhapus,
   `get_csrf_token` sudah pakai stdlib. Frontend/deps: `feather-icons`
   dihapus dari dependencies (masih hadir sebagai transitive milik
   frappe-ui), `@pinia/testing`+`@vue/test-utils` pindah ke devDependencies,
   `dompurify` kini dideklarasikan di `POS/package.json` (dipakai
   TranslatedHTML; sebelumnya hanya resolve dari root node_modules),
   `playwright` pindah ke devDependencies root. Modul tersentuh OK
   (offline_replay 11, offers 20, perf_search 11). Sisa CLN §7 (refactor
   besar) tetap backlog.
6. **`ar.csv` + `pt-br.csv` dedupe** — keep-last (semantik dict runtime
   Frappe): ar 1567→1481, pt-br 1517→1433, plus 1 dead key trailing-space di
   ar.csv dihapus; validator `scripts/validate_id_csv.py` kini default
   mengecek SEMUA `translations/*.csv`.
7. **Laporan dampak A7** → `docs/A7_IMPACT_REPORT.md` (angka situs uji +
   prosedur produksi: baseline pra-deploy → jalankan ulang pasca-deploy).
8. **Housekeeping atas rekomendasi** — RQ di-flush; **409 POS Opening Shift
   residu tanpa invoice dihapus** dari situs test-runner (324 dipertahankan
   karena punya invoice ter-link); sweep pasca-cleanup tetap 907 OK.

## Terbuka / menunggu keputusan

1. **Deploy produksi** — masih `b6f7ae9`; sumber deploy kini `main` =
   `cfd89fc` (satu commit memuat seluruh remediasi + open items). Wajib:
   build frontend manual di host (`npm --prefix POS run build`), **migrate**
   (2 patch index baru + FULLTEXT + field `price_replay_audit_only` di 2
   doctype settings + fixture DocPerm baru: Account select=1 dan 3 baris
   POS Invoice per persona dari fix E2E-01 — fixture tersinkron otomatis
   saat migrate, atau manual via `bench --site X execute
   frappe.utils.fixtures.sync_fixtures --args "['pos_next']"`), smoke test
   per `docs/DEPLOY_CHECKLIST_SECURITY_AUDIT_FIXES.md` (SELECT pra-cek PE
   legacy; kini termasuk item 3 §1: jalankan `pos_next.audit.run` — angka
   pembanding situs test ada di item A7 di atas).
2. **Rollout SEC-23 bertahap** — di produksi mulai audit-only → cek log →
   baru enforce. Kode + test sudah mendukung kedua mode (default 0 =
   enforce); tidak ada lagi kerja lokal untuk item ini.
3. **Ruff ignore** — menunggu CLN penuh (keputusan user: tetap terbuka).
4. **CLN §7 sisa (non-quick-win)** — refactor besar tetap backlog:
   centralize `_check_profile_access`, event-map dispatcher, CUSTOM_FIELDS →
   JSON, dedupe countries/useCountryCodes, logger/lowEndOptimizations,
   `_pn_run_tests.py` pindah scripts/, hapus admin wallet endpoints (ada
   test + keputusan produk), BrainWise Branding (HIDUP — jangan hapus),
   credit endpoints (dipakai test SEC-09), workbox-window dynamic import.
5. ~~Laporan dampak A7~~ — **TUTUP**: `docs/A7_IMPACT_REPORT.md`.
6. ~~Flake `test_promotions` posisi-dependent~~ — **TUTUP**: akar =
   mode `invoice_type` ambient (tanpa fixture shift); fix permanen pin-mode
   + restore (ronde-2 item 3).
7. ~~Duplikat key `ar.csv` & `pt-br.csv`~~ — **TUTUP**: dedupe keep-last +
   validator semua CSV (ronde-2 item 6).

## Catatan sapuan akhir

known-flaky modules in this run (fail only in long sweeps, pass individually):
`pos_next.api.test_cashier_permissions`, `pos_next.tests.test_uninstall_coverage`
— ditandai di `pos_next/_pn_run_tests.py` (frozenset `KNOWN_FLAKY`); runner
mencetak anotasi `known-flaky modules in this run: ...` tanpa mengubah exit
code. Detail penyelidikan di item A6.

Ronde-2 27 Sep: keluarga "flake posisi-dependent" lain (test_promotions,
test_invoice_authorization_security, test_medium_gates_security,
test_draft_invoices_security) ternyata BUKAN flake — akarnya satu: mode
`invoice_type` ambient situs uji vs modul tanpa fixture opening-shift
(detail ronde-2 item 2–4). Label KNOWN_FLAKY tidak bertambah. Output
"Removing roles/workspace" dari `test_uninstall_coverage` adalah mock yang
hermetik — bukan uninstall nyata.

## Lingkungan & akun uji (dev)

- `roti-posnext-test.localhost:8001` — serve `--noreload` dengan site
  eksplisit: `bench --site roti-posnext-test.localhost serve --noreload`
  (tanpa `--site` semua method pos_next balik "App pos_next is not
  installed"); restart proses setelah ubah `.py`; log `/tmp/web-pos.log`;
  kill pattern `pkill -f '[b]ench_helper.*serve'`.
- Script ad-hoc per-site: **jangan `frappe.init` standalone** (path site rusak
  di container) — taruh fungsi di module package lalu
  `docker exec -w /workspace/development/frappe-bench erpnext16_dev-frappe-1
  /home/frappe/.local/bin/bench --site <site> execute pos_next.<modul>.<fn>`
  (bench di `/home/frappe/.local/bin/bench`, bukan `env/bin`).
- Administrator password: `admin`. Kasir dua-persona:
  `kasir.pku@posnext.test` / `kasir123` (POSNext Cashier + Stock User,
  terdaftar di `applicable_for_users` profil `POS - PKU DELANGGU`).
- Shift aktif kini `POSA-OS-26-0000048` milik kasir uji (kas awal 0);
  shift sebelumnya ditutup seimbang lewat `POSA-CS-26-0000016` (27 Sep).
- Test backend WAJIB serial via
  `docker exec -w /workspace/development/frappe-bench erpnext16_dev-frappe-1
  ./env/bin/python apps/pos_next/pos_next/_pn_run_tests.py <modul>`;
  frontend (`vitest`/`build`) di host; jangan `bench build` (OOM).

## Jebakan yang sudah terbukti (jangan diulang)

- frappe-ui `<Input>` di bawah otomasi: fill + klik-luar maupun fill + Tab
  **TIDAK** meng-commit `update:modelValue` (dikoreksi 27 Sep; "Hitung & isi"
  di dialog tutup shift hanyalah label, bukan tombol). Yang andal:
  locator.evaluate → native setter `HTMLInputElement.prototype.value` +
  `dispatchEvent` input & change. Form SPA login tidak submit di bawah
  otomasi (normal dengan keyboard asli).
- Doctype faktur situs dibaca dari **`POS Next Global Settings`.invoice_type**
  (`invoice_type.py:get_pos_invoice_doctype`, request-cached), BUKAN POS
  Settings tabSingles; `validate_invoice_type_change` menolak ganti saat ada
  shift terbuka / offline pending / legacy deferred — untuk seed test lewati
  via `frappe.db.set_single_value` dan restore KEDUA single.
- **Mode `invoice_type` ambient situs uji menentukan lulus-tidaknya banyak
  modul test**: modul yang tanpa fixture opening-shift dan hardcode lane
  Sales Invoice akan error "No open POS Opening Entry" saat situs kebetulan
  di mode POS Invoice. Modul seperti itu wajib pin `SALES_INVOICE` di
  setUpClass + restore baseline di tearDownClass (pola
  `_set_invoice_type`; contoh: test_promotions, test_invoice_authorization_security,
  test_medium_gates_security).
- POS Opening Shift terbuka = `status: "Open"` dengan **docstatus = 1**
  (bukan 0).
- `get_default_customer()` (price_group_helpers) bisa balik pelanggan ber-
  price-group franchise → price_list_rate lompat ("Harga Outlet Franchise")
  dan rate daftar umum terbaca diskon manual → gate kode head-office melempar;
  draft test POS Invoice pakai pelanggan polos segar tanpa price group.
- Residu `Item Price` antar-run test memalsukan duplikat — fixture test
  memakai reuse-or-create.
- Wallet Transaction loyalty terbentuk per invoice submit (loyalty-to-wallet
  aktif) — test yang cancel invoice perlu sweep WT.
- RQ penuh memalsukan kegagalan massal — flush `rq:*` di
  `erpnext16_dev-redis-queue-1` sebelum sweep.

## Sesi 27 Sep ronde-3 — fix "baris hantu Cash" di rekonsiliasi tutup shift (belum commit)

Temuan user saat tutup shift PKU DELANGGU: muncul DUA baris cash — "Cash PKU
DELANGGU" (ekspektasi 71.000) + "Cash" generik (ekspektasi −19.750, "Tidak ada
penjualan") → total selisih +19.750 palsu. Akar: `change_amount` (kembalian)
selalu dipotong ke "cash mode" yang diambil dari field
`POS Profile.posa_cash_mode_of_payment`; field itu KOSONG di semua profile
outlet → fallback hard-code `"Cash"` generik → baris baru dibuat dengan
ekspektasi negatif. Angkanya sebenarnya konsisten: laci fisik = 71.000 −
19.750 = 51.250 (kembalian ACC-PSINV-2026-00060: tagihan 250, dibayar tunai
20.000).

Fix permanen (kode saja, per-POS-Profile — siap untuk model outlet
per-company dengan beberapa profile per company):

- Helper baru `pos_next/services/cash_mode.py:get_cash_mode_of_payment` —
  rantai: field `posa_cash_mode_of_payment` → baris payment profil bertipe
  Cash (baris default dulu, lalu urutan idx) → `"Cash"` generik terakhir.
- Dipakai di `_get_cash_mode_of_payment` + blok inline merge
  (`pos_closing_shift.py`; preview/submit), 4 titik `shifts.py`
  (session/period summary & dashboard — klasifikasi tunai ikut benar), dan
  endpoint whitelist `get_effective_cash_mode_of_payment` untuk JS Desk.
- Twin JS Desk `pos_closing_shift.js`: prefetch cash mode via endpoint baru
  (`resolve_cash_mode` di `run_serially` `pos_opening_shift`); lookup lama
  tetap ada sebagai jaring pengaman.
- Test: unit `pos_next/services/test_cash_mode.py` (6 kasus, profil hermetik
  via `flags.ignore_validate`) + integrasi `TestClosingCashModeRegression`
  di `test_pos_invoice_closing.py` (kembalian masuk baris kas outlet, baris
  generik tak tersentuh). 9/9 OK + 63 test closing/summary/recap/race OK.
- **Jebakan baru**: ERPNext menghitung ulang `change_amount = paid − grand`
  hanya bila ada baris payment bertipe Cash — payload tanpa `type` membuat
  kembalian jadi 0; SPA selalu mengirim `type` (`useInvoice.addPayment`).
  Test integrasi wajib menyertakan `type: "Cash"`.
- Terverifikasi live di situs uji (POSA-OS-26-0000049): preview tutup shift
  kini SATU baris "Cash PKU DELANGGU" ekspektasi 51.250. `bench serve
  --noreload` direstart — **cwd wajib `sites/`** (apps.txt dibaca relatif
  `./apps.txt`), bukan root bench.

Lanjutan ronde-3 (UX dialog tutup shift): input "Jumlah Aktual" kini
**auto-isi dari ekspektasi** (tetap bisa diedit) dan memakai format titik
live yang sama dengan dialog opening (`amountInput.js`). Detail:

- `loadClosingData` mengisi `closing_amount` + display `closing_text` dari
  `expected_amount`; `canSubmit` langsung true (prefill dianggap terisi).
  **Mode entry buta (`hideExpectedAmount`) TETAP kosong** — auto-isi tidak
  boleh membocorkan ekspektasi.
- Dua input frappe-ui `Input` (type=number) diganti native input `:value` +
  `@input` → `formatAmountInput`/`parseAmountInput` (jebakan frappe-ui Input
  tidak menulis balik DOM saat fokus).
- `amountInput.js` kini mempertahankan tanda minus di depan (baris return
  bisa prefill ekspektasi negatif); `parseAmountInput` menghormatinya.
- Test: amountInput (+minus, round-trip negatif) + dua test prefill di
  `ShiftClosingDialog.test.js`. vitest 574/574 hijau; build OK.

## Sesi 27 Sep ronde-4 — audit & fix layout mobile 390px (belum commit)

Audit visual penuh di viewport ponsel (390x844 + 360x740) via in-app browser di
situs uji `roti-posnext-test.localhost:8001/pos/`: items, cart kosong & berisi,
dialog Pembayaran, Tutup Shift (rekonsiliasi + auto-isi 70.250 terlihat hidup),
menu Manajemen, Dasbor shift, Buat Pelanggan, Penawaran (empty state), toast
error stok. Sebagian besar sudah rapi; temuan & fix:

1. **Overflow horizontal 12px di header (akar masalah "berantakan")** — seksi
   tengah `flex-1` di POSHeader tidak punya `min-w-0`, jadi min-content 290px
   (klaster ikon `flex-shrink-0` 205px) → baris header 402px > 390px, dan
   `.pos-app-shell` (`overflow-x-hidden`) jadi BISA tergulir horizontal; begitu
   tergulir, seluruh app bergeser dan hamburger terpotong. Fix: `min-w-0` di
   seksi tengah + shell diganti `overflow-x-clip` (clip benar-benar melarang
   gulir; lazy-load aman karena pakai IntersectionObserver).
2. **Target sentuh < 44px (R-03)** — `pos-icon-btn` 36px → 44px khusus ponsel
   (media query ≤639.98px di index.css; tablet tetap 36px) + tombol diberi
   `inline-flex items-center justify-center` agar ikon tetap center saat box
   membesar. Kompensasi lebar supaya muat sampai 360px: logo `w-12 sm:w-16`,
   chevron UserMenu `hidden sm:block`. Customer actions cart (edit/plus/x)
   28px → `w-11 h-11 sm:w-7 sm:h-7`; stepper qty & tombol serial 24px → 36px
   (input qty ikut h-9); chip grup item 29px → 39px (`py-2.5 sm:py-2`);
   Checkout/Tunda `min-h-[44px]`; Hapus/Sortir `min-h-[36px]`;
   Penawaran/Kupon `min-h-[40px]`; input "Nama pembeli" `h-11 sm:h-9`;
   tombol footer Buat Pelanggan `min-h-[44px] flex-1 sm:flex-none`.

Verifikasi: shellScrollW = viewport (390 & 360, tadinya 402), hamburger x=4
(tadinya -8), vitest 574/574, build host OK, screenshot ulang header/cart/
payment bersih. Jebakan audit: klik sintetis `dispatchEvent(MouseEvent)` kadang
diabaikan SPA — pakai `el.click()` via evaluate; viewport IAB bisa di-set via
`tab.setViewportSize` dan WAJIB reload agar SPA membaca ulang lebar.

## Sesi 27 Sep ronde-5 — fitur Ukuran Teks (85–125%, belum commit)

Fitur pengaturan ukuran font per-perangkat di UserMenu ("Ukuran Teks": − % +
; klik persen = reset ke 100%). Mekanisme: root `font-size` % (semua utility
Tailwind rem-based ikut skala; arbitrary `text-[10px]` tidak — sengaja).

**Batas aman hasil sweep empiris** (probe elemen-luber di luar carousel +
screenshot, 390px & 1050px): layout TIDAK rusak dari 80% sampai 135%;
yang menentukan bukan layout melainkan keterbacaan/estetika — di 130% judul
header menyusut jadi "P", di bawah 85% teks isi tidak nyaman. Putusan:
**MIN 85% – MAX 125%, langkah 5%, default 100%** (100% = base browser, jadi
preferensi font browser user tetap dihormati secara proporsional).

- `composables/useTextScale.js`: `initTextScale()` dipanggil di main.js SEBELUM
  mount (first paint sudah pakai ukuran tersimpan), `useTextScale()` =
  state + increase/decrease/reset; localStorage `pos_text_scale`; clamp ketat.
- `UserMenu.vue`: baris Ukuran Teks setelah header user (tampil di semua
  ukuran layar), `@click.stop` WAJIB — tanpa itu satu ketuk +/− menutup menu
  (handler klik container dropdown).
- Terjemahan: 4 baris baru di `pos_next/translations/id.csv` + `bench
  clear-cache` agar API terjemahan menyajikan string baru.
- Verifikasi: vitest 580/580 (+6 test composable: persist, clamp 300→125,
  korup→default, reset), build OK, live: boot 85% terpakai, menu tetap terbuka
  saat menyetel, label live, reset 100%; probe offenders = 0 di 85/100/125
  pada 390px; sweep desktop 80–135% tanpa pelanggaran.
