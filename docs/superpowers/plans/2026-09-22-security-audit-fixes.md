# Rencana Perbaikan Hasil Audit Keamanan — pos_next

**Tanggal audit:** 2026-09-22 · **Status:** LAPORAN + RENCANA — belum ada satu pun fix diterapkan.
**Audiens:** AI agent di sesi lain yang akan mengeksekusi perbaikan. Dokumen ini self-contained; nomor baris adalah anchor dari working tree 22 Sep 2026 — selalu baca file dan verifikasi dulu sebelum edit.

---

## 0. Aturan main (WAJIB dibaca sebelum mulai)

1. **JANGAN `git commit` / `git push` tanpa perintah eksplisit dari user.** Selesaikan + verifikasi, lalu laporkan dan tunggu.
2. **Test backend WAJIB lewat runner khusus, JANGAN `bench run-tests`** (akan hang di bootstrap):
   ```
   docker exec -w /workspace/development/frappe-bench erpnext16_dev-frappe-1 \
     ./env/bin/python apps/pos_next/pos_next/_pn_run_tests.py <modul>
   ```
   Site test: `posnext.localhost`. Serial saja. Gotcha: nama modul 3 segmen (mis. `pos_next.pos_next.utils.test_pos_closing_print`); kalau banyak test gagal massal, cek dulu Redis Queue penuh — bersihkan `rq:*` di container `erpnext16_dev-redis-queue-1`; ada residu `invoice_type=Sales Invoice` + fixture stranding tiap run.
3. **Frontend test/build di HOST, bukan container** (yarn tidak ada di host):
   `npm --prefix POS run test:run` (~28s) dan `npm --prefix POS run build` (~6s).
4. **JANGAN `bench build` di dalam container** — OOM yang membunuh MariaDB (exit 137). JS/CSS Desk page tidak butuh build.
5. Setelah ubah file `.py`: restart proses `bench serve` user (biasanya `--noreload`) sebelum test manual.
6. **JANGAN `biome --write` / lint:fix repo-wide** — biome 1.9.4 panic di `POSSale.vue` (bug upstream) dan `--write` menghapus semua semicolon.
7. Konvensi vitest: jsdom, mock `FeatherIcon` + global `__` via mount config; assert Teleport lewat `document.body`.
8. Deploy produksi (kalau diminta): butuh `bench migrate` (workspace + patch reorder + posnext.json) dan build vite + copy-html-entry; CI tidak build frontend.

**Estimasi total:** Fase 1–4 ≈ 1–2 hari kerja. Pola-pola fix yang benar **sudah ada di app ini sendiri** — instruksi tiap item menunjuk contohnya; salin, jangan karang baru.

---

## 1. Ringkasan eksekutif

Audit 4-subagent (API layer, logika bisnis backend, frontend, konfigurasi/izin) atas seluruh app. Semua temuan CRITICAL sudah diverifikasi manual di kode. Akar masalah umum: **guard izin bersifat per-modul, bukan per-endpoint, dan server mempercayai field dari klien.**

| ID | Severity | Lokasi | Judul singkat | Fase |
|----|----------|--------|---------------|------|
| SEC-01 | CRITICAL | `pos_next/pos_next/doctype/wallet_transaction/wallet_transaction.py:191,252` | Whitelist endpoint mint wallet credit tak terbatas | 1 |
| SEC-02 | CRITICAL | `pos_next/pos_next/doctype/pos_closing_shift/pos_closing_shift.py:607` | Submit closing shift dari JSON mentah klien, ignore_permissions | 1 |
| SEC-03 | HIGH | `pos_next/api/invoices.py:875-890,1195-1210` | `update_invoice`/`submit_invoice` IDOR + mass-assignment | 2 |
| SEC-04 | HIGH | `pos_next/api/invoices.py:117-121,991-1016` + `pos_next/overrides/discount_code.py:57-69` | Batas diskon dilewati via flag klien `is_rate_manually_edited` | 2 |
| SEC-05 | HIGH | `pos_next/api/invoices.py:2377,2648,2881` | Endpoint return bocorkan invoice tanpa permission | 2 |
| SEC-06 | HIGH | `pos_next/api/invoices.py:2124-2162` | `cleanup_old_drafts` mass-delete situs-wide | 2 |
| SEC-07 | HIGH | `pos_next/api/shifts.py:159-179,79-110,113-151` | Shift: read tanpa gate + user param dari klien + open tanpa cek profil | 2 |
| SEC-08 | HIGH | `pos_next/api/offers.py:660-677` | `get_active_coupons` bocorkan kode gift card | 2 |
| SEC-09 | HIGH | `pos_next/api/credit_sales.py:230-306,579-622` | Redeem/cancel JE tanpa authorization | 2 |
| SEC-10 | HIGH | `pos_next/overrides/discount_code.py:79-113` + `pos_next/overrides/pos_offer_usage.py:170-183` | Exemption gate via klaim nama Pricing Rule sembarangan | 2 |
| SEC-11 | HIGH | `POS/src/utils/printInvoice.js:188+` + `POS/src/utils/print/crew_slip.js:103-118` | XSS: HTML struk tanpa escape → `document.write` same-origin | 4 |
| SEC-12 | HIGH | `POS/src/components/common/AutocompleteSelect.vue:365-373` + 2 dialog | XSS: `v-html` highlight tanpa escape | 4 |
| SEC-13 | HIGH | `pos_next/fixtures/custom_docperm.json` + 3 doctype JSON | Kasir: cancel SI/CE, submit/cancel/amend deposit & shift, **write POS Settings** | 3 |
| SEC-14 | MEDIUM | `pos_next/api/wallet.py:16-42` + `wallet.py(doctype):125-156` | Wallet double-spend: tanpa lock + filter miss POS Invoice | 5 |
| SEC-15 | MEDIUM | `pos_next/pos_next/doctype/pos_coupon/pos_coupon.py:181-207` | Coupon usage race + commit sebelum submit | 5 |
| SEC-16 | MEDIUM | `pos_next/pos_next/utils/pos_closing_print.py:21-24` | Jinja methods bocor rekap shift tanpa izin | 5 |
| SEC-17 | MEDIUM | `POS/vite.config.js:191-205` + `POS/src/utils/sessionCleanup.js` | SW cache `/api` 24 jam + IndexedDB lintas kasir tak dibersihkan | 5 |
| SEC-18 | MEDIUM | `pos_next/api/partial_payments.py:427-429,799-809` | Account GL arbitrer diterima sebagai `paid_to` | 5 |
| SEC-19 | MEDIUM | `pos_next/api/customers.py:12-68,264-278` | Dump PII customer tanpa permission | 5 |
| SEC-20 | MEDIUM | `pos_next/overrides/queue_counter.py:16-43` | Queue counter bisa dinaikkan via payload klien | 5 |
| SEC-21 | MEDIUM | `pos_next/api/qz.py:47-107` | Oracle tanda-tangan QZ terbuka semua user | 5 |
| SEC-22 | MEDIUM | `pos_next/pos_next/doctype/referral_code/referral_code.py:447-511` | Self-referral → gift card tak terbatas | 5 |
| SEC-23 | MEDIUM | `pos_next/overrides/pricing_rule.py` (offline sync path) | Offline replay percaya rate klien (`ignore_pricing_rule`) — keputusan desain | backlog* |
| LOW-01..08 | LOW | lihat §8 | imin-probe guest, brute-force code, session lock, dll. | backlog |

\* SEC-23 butuh keputusan user (re-pricing server-side mengubah perilaku offline); jangan dikerjakan tanpa persetujuan.

---

## 2. Pola fix yang dipakai berulang (salin dari kode yang sudah benar)

- **PATTERN A — cek akses profil** (untuk endpoint yang menyentuh POS Profile):
  `pos_next/api/shifts.py` → `_check_profile_access(pos_profile)`; varian lain: `partial_payments._has_pos_profile_access`, `packages._assert_profile_access`, `backdate_invoices._check_backdate_access`. Faktorkan jadi helper bersama bila rapi, tapi jangan refactor besar-besaran.
- **PATTERN B — owner-or-permission**:
  `pos_next/api/shifts.py:368-374` (`get_session_summary`): pemilik shift ATAU `frappe.has_permission(doctype, "read", doc=doc)`.
- **PATTERN C — kunci baris**:
  `pos_next/overrides/discount_code.py:272-278`: `frappe.get_doc(..., for_update=True)` di dalam transaksi submit; JANGAN `frappe.db.commit()` manual di tengah request.
- **PATTERN D — escape HTML frontend**:
  `POS/src/components/common/TranslatedHTML.vue` sudah pakai DOMPurify. Tambahkan satu helper `escapeHtml()` (escape `&<>"'`) di `POS/src/utils/` dan pakai di semua interpolasi teks.
- **Prinsip umum:** field yang bisa dihitung server (total, expected_amount, harga, flag edit-manual, conversion factor) → hitung di server; perlakukan nilai klien sebagai hint.

---

## 3. Fase 1 — CRITICAL (kerjakan lebih dulu)

### SEC-01 — Endpoint whitelist mencetak saldo wallet tak terbatas
- **Bukti:** `pos_next/pos_next/doctype/wallet_transaction/wallet_transaction.py:191` (`create_wallet_credit`) dan `:252` (`credit_loyalty_points_to_wallet`) keduanya `@frappe.whitelist()`; keduanya `transaction.insert(ignore_permissions=True)` lalu `.submit()`. `credit_loyalty_points_to_wallet` menerima `conversion_factor` dari klien (fallback 1.0 kalau program tidak ketemu) → `credit_amount = points × factor`.
- **Eksploit:** user login apa pun POST `/api/method/pos_next.pos_next.doctype.wallet_transaction.wallet_transaction.create_wallet_credit` dengan wallet & amount apa pun → kredit ber-GD tersubmit. Kontras: `api/wallet.py::create_manual_wallet_credit` sudah cek `frappe.has_permission("Wallet Transaction", "create")`.
- **Fix:**
  1. Lepas `@frappe.whitelist()` dari kedua fungsi (jadikan internal — seperti `credit_return_to_wallet` di baris ~301 yang sudah benar). Cari dulu pemanggilnya (grep seluruh repo termasuk `api/wallet.py`, `overrides/`, JS) dan pastikan semua jalur produk lewat wrapper yang sudah di-gate.
  2. `conversion_factor` HARUS dihitung server-side dari Loyalty Program; parameter klien dihapus/diabaikan.
- **Acceptance:** panggil kedua endpoint via HTTP sebagai user non-priviledged → 404/PermissionError; test unit baru: helper internal tetap terpakai oleh jalur loyalty/refund yang sah.

### SEC-02 — Pemalsuan POS Closing Shift
- **Bukti:** `pos_closing_shift.py:607-614`:
  ```python
  @frappe.whitelist()
  def submit_closing_shift(closing_shift):
      closing_shift = json.loads(closing_shift)
      closing_shift_doc = frappe.get_doc(closing_shift)          # seluruh dokumen dari klien
      closing_shift_doc.flags.ignore_permissions = True
      closing_shift_doc.save(); closing_shift_doc.submit()
  ```
  `update_payment_reconciliation` menghitung `difference` dari DUA nilai klien (`closing_amount`, `expected_amount`). Wrapper `api/shifts.py:183` tidak menambah cek apa pun.
- **Eksploit:** user apa pun submit closing untuk shift orang lain dengan angka kas buatannya; atau kasir set `expected_amount = closing_amount` per mode → shift "balanced" padahal kas bolong.
- **Fix:**
  1. Ambil `pos_opening_shift` dari payload, validasi: pemanggil adalah `owner` opening shift ATAU punya `frappe.has_permission("POS Closing Shift", "create"/"submit")` (PATTERN B).
  2. Rebuild dokumen closing server-side via `make_closing_shift_from_opening` (sudah ada; `get_closing_shift_data` memakainya) dan ambil `payment_reconciliation` hasil rekomendasi server; nilai klien hanya boleh mengisi `closing_amount` aktual (uang fisik yang dihitung kasir).
  3. Lepas `ignore_permissions`, atau pertahankan hanya SETELAH cek eksplisit di atas.
- **Acceptance:** test: non-owner tanpa peran → ditolak; owner dengan total manipulasi → `expected_amount` tetap angka server; `tests/test_pos_invoice_closing.py` dan `pos_next/pos_next/doctype/pos_closing_shift/test_pos_closing_shift.py` tetap hijau (sesuaikan bila payload test-nya kini harus minimal).

---

## 4. Fase 2 — HIGH backend (klaster otorisasi + gate diskon)

### SEC-03 — `update_invoice` / `submit_invoice` (api/invoices.py:875-890, 1195-1210)
- **Masalah:** tidak ada cek keanggotaan POS Profile User; `frappe.get_doc(doctype, data.get("name"))` + `invoice_doc.update(data)` (mass-assignment mentah) + `ignore_permissions=True`. Draft siapa pun bisa ditimpa; invoice bisa dibuat di profil mana pun.
- **Fix:** di awal kedua fungsi: (a) validasi `data["pos_profile"]` dengan PATTERN A; (b) untuk update existing: `invoice_doc.owner == frappe.session.user` ATAU `frappe.has_permission(doctype, "write", doc=invoice_doc)`; (c) putuskan `ignore_permissions` — boleh dipertahankan HANYA untuk insert-own-draft setelah cek (a), hapus untuk update doc milik orang lain. Field yang tidak boleh dari klien: `owner`, `docstatus`, `name` relasi lintas profil.
- **Acceptance:** test IDOR: user B menimpa draft user A → PermissionError; kasir sah tetap bisa simpan draft + submit (regresi `test_pos_invoice_submit.py` hijau).

### SEC-04 — Batas diskon dilewati via flag klien
- **Bukti:** `api/invoices.py:117-121` — validasi manual-edit hanya jalan bila `is_rate_manually_edited=1` dari klien; jalur "normal" (`:991-1016`) memercayai `price_list_rate`/`rate` klien. `overrides/discount_code.py:57-69` — deteksi diskon manual untuk gate kode konfirmasi juga bergantung flag yang sama (di-set klien di `POS/src/composables/useInvoice.js:512`).
- **Fix:** turunkan "manual edit" server-side: bandingkan `rate` payload dengan `price_list_rate` yang server ambil sendiri (item price / pricing rule attribution yang sudah di-stash server). Flag klien hanya boleh memicu jalur audit, bukan menonaktifkan validasi. Di `discount_code.py`: `invoice_has_manual_discount` = ada baris dengan `rate < price_list_rate` efektif yang tidak teratribusi pricing rule (terkait SEC-10).
- **Acceptance:** test: payload `rate=50%` dari price list TANPA flag → tunduk batas diskon / minta kode konfirmasi; payload dengan flag dan harga wajar → lolos.

### SEC-05 — Endpoint return tanpa permission (api/invoices.py:2377 `get_invoice_for_return`, :2648 `prepare_return_invoice`, :2881 `search_invoices_for_return`)
- **Fix:** terapkan `frappe.has_permission(doctype, "read", doc=...)` + PATTERN A sama seperti `get_invoice` (`:1886`) dan `get_invoices`. `get_returnable_invoices`/`search_invoice_by_number` ikut disaring per profil/owner bila murah.
- **Acceptance:** user tanpa a profil → tidak bisa baca invoice luar profinya.

### SEC-06 — `cleanup_old_drafts` mass-delete (api/invoices.py:2124-2162)
- **Fix:** (a) clamp `max_age_hours` minimal 24; (b) filter draft ke `owner = frappe.session.user` (atau profil miliknya); (c) hapus `force=True, ignore_permissions=True`; (d) idealnya jadikan scheduled job, endpoint whitelist dihapus atau di-gate System Manager. Cek pemanggil frontend (`draftManager.js` / `posDrafts.js`) dan sesuaikan.
- **Acceptance:** test: hanya draft sendiri yang terhapus; `max_age_hours=0` → clamp.

### SEC-07 — Klaster shifts (api/shifts.py)
- `get_closing_shift_data` (:159-179): tambah PATTERN B (buka `get_session_summary` :368-374 sebagai contoh).
- `check_opening_shift` (:79-110): `user` param → pin ke `frappe.session.user` kecuali pemanggil punya read POS Opening Shift.
- `create_opening_shift` (:113-151): PATTERN A sebelum insert; serialisasi cek open-shift (kunci profil/user dengan `for_update` atau unique constraint) supaya tidak ada 2 shift terbuka duplikat.
- **Acceptance:** test ketiga kasus; regresi GUI shift dashboard tetap jalan (manual oleh user bila perlu).

### SEC-08 — `get_active_coupons` (api/offers.py:660-677)
- **Fix:** batasi ke konteks kasir: pemanggil harus punya read POS Coupon ATAU shift terbuka yang mengcover customer tsb (praktis: cek `has_permission("POS Coupon", "read")`; kasir diberi read khusus via role check POSNext Cashier bila perlu). Jangan kembalikan `coupon_code` penuh tanpa gate.
- **Acceptance:** user arbitrer tidak bisa menarik kode gift card customer lain.

### SEC-09 — credit_sales (api/credit_sales.py:230-306, 579-622)
- **Fix:** `frappe.has_permission("Sales Invoice", "write", doc=invoice_name)` di awal `redeem_customer_credit` dan `cancel_credit_journal_entries`; `get_credit_sale_summary` ikut di-gate read. (`get_customer_balance`/`get_available_credit` read-only — masuk SEC-19 pola yang sama.)
- **Acceptance:** user asing tidak bisa memindah kredit / membatalkan JE invoice orang.

### SEC-10 — Exemption via klaim nama Pricing Rule (discount_code.py:79-113, pos_offer_usage.py:170-183)
- **Masalah:** muatan klien boleh mengklaim `pricing_rules: [...]` / `pos_relayed_offer_rules`; exemption gate dikonfer bila nama rule ada & enabled — tanpa cek rule benar-benar berlaku (company, validity, kondisi, dan diskonnya cocok dengan baris).
- **Fix:** saat memverifikasi klaim: rule harus (a) berlaku untuk company/customer/tsb, (b) `rate_or_discount`-nya merekonsiliasi dengan diskon aktual baris (pct/amount), (c) untuk jalur relay, hanya nama yang di-stash SERVER (`pos_relayed_offer_rules` dari server) yang dihitung — jangan merge klaim klien. Lihat `api/invoices.py:350-388` yang sudah strip field ini — pertahankan, dan jangan masukkan kembali nilai klien ke stash.
- **Acceptance:** test: klaim rule tidak-relevan → tetap tunduk gate kode; relay server sah → exempt.

---

## 5. Fase 3 — Izin deklaratif (fixtures + doctype JSON)

### SEC-13
1. `pos_next/fixtures/custom_docperm.json` untuk **POSNext Cashier**:
   - Sales Invoice: buang `cancel`, `amend` (biarkan create/write/submit/report untuk fungsi POS).
   - POS Closing Entry: buang `cancel`, `amend`.
   - Payment Entry: buang `submit` (create/write draft; manager yang submit).
2. Doctype JSON — turunkan baris perm kasir:
   - `pos_next/pos_next/doctype/bank_deposits/bank_deposits.json`: buang `cancel/amend/export` dari kasir (sisakan create/submit miliknya bila alur butuh).
   - `pos_closing_shift.json` & `pos_opening_shift.json`: kasir → create/submit milik sendiri (pertimbangkan `if_owner`), buang `cancel/amend/export`.
   - `pos_settings.json`: kasir → **read saja** (write POS Settings = angkat sendiri `max_discount_allowed`/`require_refund_code` yang dibaca server di `overrides/discount_code.py`).
3. Setelah ubah JSON: jalankan `bench migrate` di site dev agar Custom DocPerm ter-regenerasi dari fixtures, lalu verifikasi via `frappe.permissions.get_role_permissions`.
- **Acceptance:** kasir tidak bisa cancel SI; kasir tidak bisa edit POS Settings; alur POS harian (buka/tutup shift, simpan invoice, submit) tetap hijau di test suite.

---

## 6. Fase 4 — XSS frontend

### SEC-11 — HTML struk (prioritas tertinggi frontend)
- **Sink:** `POS/src/utils/printInvoice.js:775` (`printWindow.document.write`) + `print/browser_client.js:20`; sumber: `buildReceiptHTML` (:188,231,232,238,244,307,337) dan `print/crew_slip.js:103-118`; lane bitmap via `frame.innerHTML` di `print/receipt_renderer.js:203`.
- **Fix:** helper `escapeHtml()` di `POS/src/utils/` (PATTERN D), bungkus SEMUA interpolasi teks `${...}` yang berasal dari data (item_name, buyer_name, customer_name, company, header/footer, mode_of_payment, serial_no, cashier_name). Teks statis template tidak perlu. Alternatif: `DOMPurify.sanitize(doc, {USE_PROFILES:{html:true}})` sebelum write — tapi escape per-interpolasi lebih murah dan tidak menambah dependensi baru di jalur cetak.
- **Acceptance:** test vitest baru: `buildReceiptHTML` dengan nama item `<img src=x onerror=...>` → output ter-escape; snapshot test receipt yang ada tetap hijau.

### SEC-12 — `v-html` highlight
- `POS/src/components/common/AutocompleteSelect.vue:132,365-373`; `POS/src/components/sale/WarehouseAvailabilityDialog.vue:193,198,1348`; `POS/src/components/sale/ReturnInvoiceDialog.vue:111,129,142`.
- **Fix:** escape `text` SEBELUM `.replace(regex, "<mark>$1</mark>")`; juga escape query sebelum masuk `new RegExp` (sekaligus memperbaiki crash render saat query mengandung `(`). Paling bersih: ganti v-html dengan render split-text (prefix/match/suffix).
- **Acceptance:** test: nama item dengan HTML → dirender sebagai teks; query `(` tidak crash.

---

## 7. Fase 5 — MEDIUM

- **SEC-14 Wallet double-spend** (`api/wallet.py:16-42`, `pos_next/pos_next/doctype/wallet/wallet.py:125-156`, `wallet_transaction.py:33-41`): (a) filter pending-payment harus union `Sales Invoice` + `POS Invoice` (doctype default = `invoice_type.get_pos_invoice_doctype()`) dan jangan saring `outstanding_amount>0` untuk draft (draft = 0); hitung dari baris wallet pada dokumen open; (b) kunci wallet row (`SELECT ... FOR UPDATE` / `for_update=True` pada Wallet atau transaksi terakhir) selama validasi debit & submit.
- **SEC-15 Coupon race** (`pos_coupon.py:181-207`): increment pakai `frappe.get_doc("POS Coupon", ..., for_update=True)` DI DALAM transaksi submit invoice; hapus `frappe.db.commit()` di `increment_coupon_usage`. Contoh benar: `overrides/discount_code.py:272-278`.
- **SEC-16 Jinja methods** (`pos_closing_print.py:21-24`): `_as_closing_doc` tambah `frappe.has_permission("POS Closing Shift", "read", doc=...)` → tanpa izin kembalikan struktur kosong; tetap gunakan untuk print format kasir sendiri (cek pemilik shift).
- **SEC-17 Cache lintas sesi**: (a) `POS/vite.config.js:191-205` — keluarkan `/api/` dari workbox `runtimeCaching` (atau NetworkOnly); (b) `POS/src/utils/sessionCleanup.js` — tambahkan pembersihan Cache Storage (`caches.delete` nama cache API) + `db` clear untuk `invoice_history`, `unpaid_invoices`, `customers`, `payment_methods` (`clearInvoiceHistoryCache` di `offline/sync.js:605` sudah ada, panggil); pastikan SW menerima pesan clear (postMessage) bila cache di SW scope.
- **SEC-18 Partial payments** (`partial_payments.py:427-429,799-809`): validasi `payment_account`: `company` sama dengan invoice + `account_type in ("Bank","Cash")` (salin pola `_validate_receivable_account` di `api/invoices.py`).
- **SEC-19 PII customer** (`customers.py`): `get_customer_details` → `has_permission("Customer", "read", doc=...)`; `get_customers` → pakai `frappe.get_list` (perm-aware) + panjang `search_term` minimal 2, batasi field.
- **SEC-20 Queue counter** (`overrides/queue_counter.py:16-43`): bump hanya bila `pos_queue_number` di-stamp server (bandingkan dengan record alokasi `get_next_queue_number`) atau clamp terhadap max harian wajar.
- **SEC-21 QZ** (`api/qz.py:47-107`): gate `get_certificate`, `get_certificate_download`, `sign_message` pada role print-capable (POSNext Cashier/Nexus POS Manager) + rate limit.
- **SEC-22 Referral** (`referral_code.py:447-511`): satu gift card referrer per kode (cap), tolak `referee == referral.customer`, derivasikan "customer baru" dari histori pembelian server-side; increment `referrals_count` dengan `for_update`; hapus `frappe.db.commit()` manual.

---

## 8. Backlog LOW (kerjakan bila diminta)

1. `pos_next/www/imin_probe.html` + route `hooks.py:318` — halaman debug guest; hapus/gate login.
2. `api/discount_code.py:74` `check_code` — tambah `frappe.rate_limit`/counter percobaan.
3. `useSessionLock.js` — dokumentasikan sebagai screen-lock kosmetik; offline unlock fail-closed via server saat reconnect.
4. `realtime_events.py:200-215` — emit customer event hanya nama, atau scope per profile/company (PII broadcast).
5. `sales_vs_shifts_report.py:1172-1245` — whitelisted chart endpoints tanpa role check → gate reporting role.
6. `api/pos_profile.py:130,179,218,256,285` — faktorkan cek membership profil (baris 57) ke semua getter.
7. `api/items.py:481,1185,1921` — resolve warehouse dari profil pemanggil, jangan terima nama mentah.
8. `pos_coupon.py:18-20` — kode promosi deterministik (8 char nama) → random hash selalu.
9. `api/printing.py:277` / `api/branding.py:132` — validasi reference, batasi ignore_permissions insert log.
10. `install.py:645-655` `reclaim_pos_settings_doctype` — snapshot data sebelum DROP TABLE DDL.
11. `tasks/cleanup_expired_promotions.py` — skip rule yang dimiliki Promotional Scheme ber-pos_offer.
12. `payments_and_cash_control_report.py:256-279` — `_get_bank_deposit_data` dead code (kolom deposit kosong selalu): sambungkan ke `get_data`.
13. `public/js/pos_discount_code.js:51` — escape `${code}` di msgprint HTML.
14. `overrides/pricing_rule.py:290-296` — swallow-all exception: re-raise untuk jalur submit server-side.
15. `pos_opening_shift.py:141-164` — tolak shift ke-2 Open per (profile, user); pin `user` ke session user.

---

## 9. Yang TIDAK perlu diubah (sudah diverifikasi aman)

- Semua `frappe.db.sql` sudah parameterized (termasuk 5 report; LIKE di-escape di `search_invoice_by_number`). Tidak ada SQL injection.
- Tidak ada hardcoded secrets di file git-tracked; private key QZ server-side mode 0600 (`setup_qz_certificate` benar, System Manager only).
- Tidak ada eval/exec/SSRF; tidak ada postMessage handler; harness dev (`POS/src/harness.js`, `harness-shim.js`, `POS/harness.html`) **tidak** ikut bundle produksi.
- Idempotensi offline sync aman (`offline_id` UNIQUE + `check_offline_invoice_synced` + `DUPLICATE_OFFLINE_INVOICE`) — JANGAN diubah.
- Offer-quota sudah diserialisasi (`for_update` + ledger `ignore_if_duplicate`) — kecuali item SEC-10 soal klaim nama rule.
- `TranslatedHTML.vue` sudah DOMPurify. `errorHandler.js` innerHTML trick aman (dibaca balik sebagai textContent).
- `hq_monitoring.py`, `backdate_invoices.py`, `purchase_orders.py`, `purchase_receipts.py`, `promotions.py`, `partial_payments.py` (kecuali SEC-18) sudah ter-gate baik — jadikan referensi gaya, bukan objek perubahan.
- `guard_against_retroactive_consolidation` (merge log) dan `validate_pos_opening_entry` sudah efektif.

---

## 10. Checklist verifikasi akhir (sebelum melapor ke user)

1. `docker exec -w /workspace/development/frappe-bench erpnext16_dev-frappe-1 ./env/bin/python apps/pos_next/pos_next/_pn_run_tests.py pos_next.api` (atau per-modul yang disentuh) — target: hijau semua; baseline saat ini 414 test OK.
2. Test suite doctype yang disentuh: `pos_next.pos_next.doctype.pos_closing_shift.test_pos_closing_shift`, `...wallet_transaction` bila ada, `pos_next.tests.*`.
3. `npm --prefix POS run test:run` dan `npm --prefix POS run build` di HOST — hijau.
4. Regression manual minimal di site uji (posnext.localhost / roti-posnext-test.localhost): buka shift → jual → tutup shift dengan kasir normal (alur utama tidak boleh rusak oleh gate baru).
5. Tulis ringkasan: item mana selesai (dengan file:line baru), mana skip + alasan. **Jangan commit/push — tunggu perintah user.**
