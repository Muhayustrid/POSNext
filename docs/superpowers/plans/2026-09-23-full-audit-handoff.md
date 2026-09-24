# Laporan Audit Penuh pos_next — 23 September 2026

> **Dokumen handoff untuk AI agent penindaklanjut.** Audit dilakukan 23 Sep 2026 di branch
> `security-audit-fixes` oleh 6 subagent paralel (keamanan, correctness backend, correctness frontend,
> performa, kompleksitas, higienitas rilis) + verifikasi silang manual untuk semua temuan CRITICAL.
> Semua temuan **belum diperbaiki**. Kerjakan sesuai prioritas di §1, satu grup per sesi.

---

## Status Eksekusi Perbaikan (branch `security-audit-fixes`)

| Grup | Cakupan | Status | Keterangan |
|---|---|---|---|
| 1 | REL-01, REL-02, REL-04, REL-05 (+ paket belum-commit "Nexus POS Manager" + perbaikan infra test) | **SELESAI & terverifikasi** (22 Sep) | Commit "audit grup 1". Migrate 2× idempoten (105 Custom DocPerm = 27 fixture + 78 mirror); suite 740 = 728 lolos + 12 known-issue pra-ada (test_promotions ×2 vs gate R3 083cbf3; test_invoice_authorization_security ×7 + test_medium_gates ×3 = profil bersama OUTLET TRAINING vs core `validate_pos_opening_entry`; proper fix = profil khusus per modul — utang follow-up) |
| 2 | COR-BE-01 + PERF-08 (match Payment Entry via child reference, index patch v2_12_0) | **BERJALAN** | |
| 3 | COR-FE-01, COR-BE-02..05, SEC-NEW-01..03 | MENUNGGU | |
| 4 | COR-FE-02..05 (frontend correctness) | MENUNGGU | |
| 5 | PERF-01..06 + SEC-NEW-04 | MENUNGGU | |
| 6 | Sisa SEC/COR/REL + REL-03/06..10 | MENUNGGU | |
| 7 (opsional) | CLN §7 (−5.215 baris) | BUTUH PERSETUJUAN TERPISAH | branch terpisah |

Catatan state situs dev pasca-grup 1: `server_script_enabled=1` di common_site_config.json; `allow_settings_reclaim=1` di posnext.localhost; `invoice_type` dikembalikan ke `POS Invoice`.

---

## §0. Aturan main untuk agent penindak (WAJIB dibaca dulu)

1. **DILARANG `git commit` / `git push` tanpa perintah eksplisit user.** Selesaikan + verifikasi dulu, lalu laporkan dan tunggu.
2. **Jalankan test backend HANYA via:**
   `docker exec -w /workspace/development/frappe-bench erpnext16_dev-frappe-1 ./env/bin/python apps/pos_next/pos_next/_pn_run_tests.py <modul>`
   (site `posnext.localhost`, serial saja). JANGAN `bench run-tests` (mati di bootstrap).
   Jebakan: modul 3 segmen (`pos_next.pos_next.utils...`); kalau gagal massal, flush `rq:*` di container `erpnext16_dev-redis-queue-1` dulu; tiap run meninggalkan residu `invoice_type=Sales Invoice` + fixture stranding.
3. **Test frontend di HOST** (bukan container): `npm --prefix POS run test:run` (vitest, ~30s) dan `npm --prefix POS run build` (~6s). Yarn tidak ada di host. JANGAN `bench build` di container (OOM → MariaDB mati).
4. **JANGAN** `biome --write` / lint:fix repo-wide (Biome 1.9.4 punya bug panic upstream #4574 di POSSale.vue; keputusan 20 Sep: dibiarkan).
5. **SEC-23 jangan dikerjakan** (offline replay percaya rate klien) — keputusan desain, butuh persetujuan user.
6. Ubah backend `.py` → restart proses `bench serve` + hard refresh browser (service worker menyandera JS basi). Ubah frontend → `npm --prefix POS run build` di host.
7. Field doctype JSON baru masuk `idx` akhir — setelah nambah field/index, cek meta + patch idx bila perlu (pola patch v2_11_0).
8. Setiap fix di §2–§5 wajib menyisakan minimal satu test yang gagal tanpa fix-nya (pola test yang sudah ada: `pos_next/api/test_*_security.py`, `test_money_race_security.py`). Test di HOST untuk vitest.

**Pola fix yang dipakai berulang (salin dari kode yang sudah benar):**
- Gate profil: `_check_profile_access(pos_profile)` di `pos_next/api/packages.py:46` — panggil di awal endpoint yang menerima `pos_profile` dari klien.
- Gate dokumen: `frappe.has_permission("<DocType>", "<action>", doc=name, throw=True)`.
- Lock baris: `frappe.get_doc(..., for_update=True)` DI DALAM transaksi submit (contoh benar: `overrides/discount_code.py:272-278`).
- Rate limit: dekorator `@rate_limit` (contoh: `api/qz.py`, `api/printing.py`).

---

## §1. Prioritas pengerjaan

| Urutan | Grup | Alasan |
|---|---|---|
| 1 | REL-01, REL-02, REL-04 (§6) | Blokir deploy production — data loss & perusak permission saat migrate |
| 2 | AUD-01 (§2), COR-BE-01 (§3) | Uang salah di tutup shift / recap |
| 3 | COR-FE-01 (§4), COR-BE-02..05, SEC-NEW-01..03 (§2) | Cegah dobel transaksi & lubang otorisasi |
| 4 | COR-FE-02..05 (§4) | Janji offline mode belum jalan (SW scope) + state bocor antar transaksi |
| 5 | PERF-01..05 (§7) | Hang di skala production (1jt invoice, 50k item) |
| 6 | REL-03, REL-05..10 (§6), sisanya | Kerapian & kesiapan rilis |
| 7 | CLN-* (§8) | Opsional — cleanup −5.215 baris, −12 deps, kerjakan di branch terpisah |

---

## §2. Keamanan — temuan baru

**Status audit 22 Sep: SEC-01..22 semua ✅ terpasang** (terverifikasi ulang per fix).
Satu caveat: **SEC-04 ⚠️** — deteksi manual-edit di `api/invoices.py:1149-1158` hanya aktif bila server punya Item Price untuk item/UOM itu (`server_plr > 0`); item tanpa harga server masih lolos deteksi. Ditutup oleh COR-BE-02 di §3 (branch submit draft melewati validasi sama sekali — lubang yang lebih besar di area yang sama; fix keduanya bersamaan).

### SEC-NEW-01 [HIGH] `get_pos_invoices` whitelisted tanpa gate — TERVERIFIKASI MANUAL
- Lokasi: `pos_next/pos_next/doctype/pos_closing_shift/pos_closing_shift.py:313-314`
- Bukti: `@frappe.whitelist()` + `def get_pos_invoices(pos_opening_shift, doctype=None)` → `frappe.get_doc(dt, d.name).as_dict()` untuk semua invoice shift; efek samping `submit_printed_invoices()` ikut mem-posting draft tercetak. `has_permission` hanya ada di baris 513 & 636 (fungsi lain).
- Dampak: user login mana pun membaca invoice lengkap (customer, pembayaran, total) shift outlet mana pun + memicu submit draft milik orang lain.
- Fix: pin gate owner-or-read (pola `get_closing_shift_data`) di awal fungsi, ATAU hapus `@frappe.whitelist()` dan jadikan helper internal (cek dulu pemanggil frontend: `POS/src` grep `get_pos_invoices`).
- Verifikasi: test ala `test_closing_shift_security.py` — user non-anggota profil → `PermissionError`.

### SEC-NEW-02 [MEDIUM] `get_draft_invoices`: filter shift tidak pernah aktif → draft site-wide
- Lokasi: `pos_next/api/invoices.py:2283-2289`
- Bukti: `if frappe.db.has_column(doctype, "pos_opening_shift")` — nama kolom nyata adalah `posa_pos_opening_shift` (install.py:210), jadi `has_column` selalu False.
- Dampak: tiap kasir menerima dokumen draft penuh milik semua kasir/outlet (bisa saling menimpa cart).
- Fix: filter `posa_pos_opening_shift` + gate owner/anggota profil; draft hanya milik sendiri kecuali role manajemen.

### SEC-NEW-03 [MEDIUM] `create_production` tanpa gate keanggotaan profil (IDOR lintas outlet)
- Lokasi: `pos_next/api/production.py:155-260`
- Bukti: `_resolve_profile(pos_profile)` tanpa `_check_profile_access`; lalu `se.flags.ignore_permissions = True; se.insert(); se.submit()`.
- Dampak: kasir outlet A memicu Manufacture Stock Entry di warehouse outlet B.
- Fix: panggil `_check_profile_access(pos_profile)` di awal (pola `packages.py:46`).

### SEC-NEW-04 [MEDIUM] Broadcast realtime `user=None` bocor detail transaksi ke semua sesi login
- Lokasi: `pos_next/realtime_events.py:87-91, 127, 210`
- Bukti: `frappe.publish_realtime(event="pos_invoice_created", message=event_data, user=None, ...)`.
- Dampak: semua user (termasuk non-POS) menerima nama invoice, grand_total, customer, per profil; `pos_stock_update` lintas warehouse.
- Fix: kirim per-room `room=f"pos_profile:{profile}"` atau ke daftar user anggota profil. (Sama dengan PERF-06 — satu fix dua temuan.)

### SEC-NEW-05 [MEDIUM] `get_referral_codes` tanpa permission
- Lokasi: `pos_next/api/promotions.py:1060-1094`
- Bukti: `filters = {}` tanpa `check_promotion_permissions` (beda dengan `get_referral_details`).
- Dampak: semua kode referral (secret penebus hadiah) + konfigurasinya bocor ke user mana pun.
- Fix: `check_promotion_permissions("read")` di awal.

### SEC-NEW-06 [MEDIUM] [NEEDS-CONFIRM] `errorHandler.js` strip-tag via `innerHTML` elemen hidup
- Lokasi: `POS/src/utils/errorHandler.js:39-42`
- Bukti: `container.innerHTML = text; text = container.textContent ...` — `<img src=x onerror=...>` tetap dieksekusi meski tidak di-attach; pesan error server bisa membawa nama item/customer tak terpercaya.
- Fix: ganti `new DOMParser().parseFromString(text, "text/html").documentElement.textContent`.
- Verifikasi: vitest baru — pesan error berisi tag HTML tidak mengeksekusi apa pun.

### SEC-NEW-07..12 [LOW] (satu commit kecil, pola gate yang sama)
- `pos_next/api/credit_sales.py:19,133` — `get_customer_balance` / `get_available_credit` baca piutang customer sembarang tanpa gate → gate read.
- `pos_next/api/wallet.py:306-307, 263, 175` — `get_or_create_wallet` (whitelisted, insert `ignore_permissions`), `get_customer_wallet`, `get_customer_wallet_balance` tanpa gate → gate anggota profil / read Customer.
- `pos_next/api/pos_profile.py:425-518, 87` — `get_create_pos_profile` bocorkan daftar User + Mode of Payment; `get_pos_settings` baca konfigurasi profil mana pun → batasi ke role manajemen / anggota profil.
- `pos_next/pos_next/doctype/pos_closing_shift/pos_closing_shift.py:301-310` — `get_cashiers` meneruskan `filters` klien mentah ke `frappe.get_all` → whitelist kunci filter + gate.
- `pos_next/www/imin_probe.html` (route di `hooks.py:319`) — halaman probe hardware tanpa login → gate user terautentikasi (atau hapus, lihat CLN-03).
- `pos_next/api/discount_code.py:37-88` — `check_code` / `validate_confirmation_code` tanpa rate limit (penebakan kode) → tambah `@rate_limit`.

**Terverifikasi BERSIH (jangan dipermasalahkan lagi):** tidak ada SQL injection di seluruh `api/*` (semua `%s`/pypika); offer-quota sudah `for_update` + ledger `ignore_if_duplicate`; `frappe.db.commit` di `api/queue.py:65` disengaja & berdokumen (alokasi nomor antrean); `tasks/`, `hq_scope.py`, `shift_schedule.py` bersih.

---

## §3. Correctness backend (uang & integritas data)

### COR-BE-01 [CRITICAL] Pelunasan partial payment tidak pernah masuk expected cash tutup shift — TERVERIFIKASI MANUAL
- Lokasi: `pos_next/api/partial_payments.py:474` vs `pos_next/pos_next/doctype/pos_closing_shift/pos_closing_shift.py:359` + `pos_next/services/sales_recap.py:447`
- Bukti: PE di-stamp `pe.reference_no = f"POS-{invoice_name}"`; query drawer menyaring `filters={"reference_no": pos_opening_shift}` dan recap `WHERE pe.reference_no IN %(shifts)s`. Grep repo: **tidak ada kode produksi yang men-stamp `reference_no` = shift** — filter tidak pernah match apa pun. Test lulus karena helper men-stamp manual (`test_session_summary.py:417`).
- Dampak: invoice 100rb dibayar 40rb + pelunasan 60rb → tutup shift expected hanya 40rb; selisih palsu 60rb di setiap shift berpelunasan. `get_session_summary`/`get_period_summary` ikut salah.
- Fix (pilih satu, konsisten): (a) stamp `pe.reference_no = invoice.posa_pos_opening_shift` di `create_payment_entry`; ATAU (b) ganti query drawer/recap agar match PE via `tabPayment Entry Reference.reference_name IN (invoice di scope)` — opsi (b) lebih tahan masa depan tapi butuh index (lihat PERF-08).
- Verifikasi: test baru — buat invoice submit + pelunasan partial, panggil `build_recap`/closing data, expected cash = total kedua pembayaran.

### COR-BE-02 [HIGH] Branch draft-eksisting di `submit_invoice` melewati seluruh validasi harga (celah SEC-04)
- Lokasi: `pos_next/api/invoices.py:1746-1765`
- Bukti: `invoice_doc.update(invoice)` langsung dari payload; loop `_resolve_server_price_list_rate` + `validate_manual_rate_edit` hanya di `update_invoice`.
- Dampak: klien yang bypass UI submit draft dengan `price_list_rate = rate` → diskon tak terdeteksi → gate kode diskon & `max_discount_allowed` diloloskan.
- Fix: jalankan loop verifikasi harga server (pola SEC-04) atas item hasil `update` di branch ini sebelum `save()`.

### COR-BE-03 [HIGH] Gagal reversal wallet pada return ditelan → dobel kredit customer
- Lokasi: `pos_next/api/invoices.py:1942` + `pos_next/pos_next/doctype/wallet_transaction/wallet_transaction.py:581, 622`
- Bukti: `reverse_wallet_transactions_for_return` menelan exception per baris (`except Exception: frappe.log_error`) tanpa pernah re-raise → `wallet_reversal_ok = True` → guard di invoices.py:1966 tetap membuka `credit_return_to_wallet(...)`.
- Dampak: return atas invoice yang pernah dikredit wallet → debit pembalik gagal senyap, customer pegang kredit lama DAN dapat kredit refund.
- Fix: reversal gagal = gagalkan transaksi (atau kembalikan status sukses per baris); jangan set `wallet_reversal_ok = True` bila ada baris gagal.

### COR-BE-04 [HIGH] Gagal redeem kredit customer ditelan SETELAH invoice terbit
- Lokasi: `pos_next/api/invoices.py:1993-2010`
- Bukti: `except Exception as credit_error: frappe.log_error(...); frappe.msgprint(...)` — "Don't fail the entire transaction".
- Dampak: invoice ter-submit seolah dibayar kredit customer, JE/alokasi tidak jadi → outstanding menggantung, kredit customer tidak terkonsumsi (bisa dipakai lagi).
- Fix: jalankan `redeem_customer_credit` SEBELUM `invoice_doc.submit()`; gagal redeem = gagalkan checkout.

### COR-BE-05 [HIGH] `_complete_offline_sync` gagal → replay membuat invoice duplikat
- Lokasi: `pos_next/api/invoices.py:1538-1549` (dengan `1460-1463`, `1497-1499`)
- Bukti: `except Exception: frappe.log_error(...)` — record sync tetap `Pending` tanpa pointer invoice; setelah 5 menit `_is_pending_expired` + `_reuse_sync_record` mengizinkan submit ulang offline_id sama.
- Dampak: satu transaksi terposting dua kali (omzet + stok).
- Fix: isi pointer invoice via `frappe.db.set_value` idempoten; jika penandaan gagal → gagalkan request (jangan biarkan Pending).

### COR-BE-06..12 [MEDIUM]
- **COR-BE-06** [NEEDS-CONFIRM] Dua closing shift untuk satu opening bisa lolos race — `pos_closing_shift.py:41-60, 622-661`: cek duplikat tanpa `for_update`, tanpa constraint. Fix: kunci baris opening (`for_update`) di awal `submit_closing_shift`.
- **COR-BE-07** [NEEDS-CONFIRM] Kupon `one_use` per-customer hanya dicek saat save draft, tidak saat submit — `pos_coupon.py:127-135` + `invoices.py:1846-1852`. Fix: cek ulang one-use per-customer DI DALAM `increment_coupon_usage` di bawah lock yang sama.
- **COR-BE-08** Return ditandai pajak pakai setting HARI INI, bukan setting invoice asal — `api/sales_invoice_hooks.py:32-70`. Fix: untuk `is_return` dengan `return_against`, salin `included_in_print_rate` dari invoice asal.
- **COR-BE-09** [NEEDS-CONFIRM] Return tanpa `return_against` melewati `validate_return_items` sepenuhnya — `invoices.py:1029-1036`. Fix: cap qty untuk return mandiri (riwayat penjualan customer/warehouse) atau tolak return tanpa `return_against`. Cek dulu: apakah UI pernah mengirim return mandiri.
- **COR-BE-10** Toggle per-profil `posa_block_sale_beyond_available_qty = 0` tidak pernah bekerja — `invoices.py:649-651`: `cint(... or 1)` membuat `0 or 1 → 1`. Fix: bedakan `None` (default blokir) dari `0` (nonaktif) sebelum `or 1`.
- **COR-BE-11** Update draft dua perangkat = last-writer-wins — `invoices.py:967-973`: payload `modified` tidak pernah dibandingkan. Fix: tolak save bila `data.modified` < modified DB (conflict 409/412) kecuali `force`.
- **COR-BE-12** Cek outstanding partial payment tanpa kunci baris — `partial_payments.py:402-409, 788-799`. Fix: `frappe.db.get_value("Sales Invoice", name, "outstanding_amount", for_update=True)` sebelum loop buat PE.
- **COR-BE-12b** Gagal Min/Max discount ditelan saat submit — `overrides/pricing_rule.py:290-296`: `except Exception: log_error + msgprint`. Fix: di jalur submit (docstatus 0→1) lempar error.

### COR-BE-13..19 [LOW]
- `invoices.py:543-555` — `_set_payment_accounts` menelan `frappe.throw` milik `get_payment_account` → biarkan ValidationError naik.
- `partial_payments.py:396-400` — dead code `if invoice.docstatus == 2` (tak terjangkau setelah guard `!= 1`) → hapus.
- `pos_closing_shift.py:664-681, 315-331` — endpoint GET `get_closing_shift_data` ikut men-submit draft tercetak (efek tulis di GET) → pindahkan `submit_printed_invoices` ke alur tutup eksplisit.
- `api/wallet.py:150-154` — gagal konversi loyalty→wallet ditelah setelah submit invoice → retry via scheduled task atau tandai invoice untuk reprocess.
- `pos_next/_pn_sync_ws.py` — skrip maintenance one-off untracked, path hardcoded mesin dev → hapus (jangan commit).
- `api/queue.py:65` — `frappe.db.commit()` manual disengaja & berdokumen; biarkan, jaga polanya (jangan ada commit manual lain di jalur uang).
- Timezone: semua batas hari konsisten server-timezone; aman selama site timezone = jam toko. Catatan saja.

### Jalur uang yang BELUM punya test (buat test saat memfix yang bersangkutan)
1. `partial_payments.add_payment_to_partial_invoice` (batch + savepoint) — 0 test.
2. `credit_return_to_wallet` + `reverse_wallet_transactions_for_return` — 0 test (titik COR-BE-03).
3. `wallet.process_loyalty_to_wallet` — 0 test.
4. Dedup offline cabang Pending-expired/Failed/pointer-hilang — hanya happy path.
5. Matematika refund: `_build_item_tax_map`, `_remap_foreign_payment_modes`, nominal refund `prepare_return_invoice` — test hanya otorisasi, bukan angka.
6. Race tutup shift ganda (COR-BE-06) — 0 test.
7. `pricing_rule.apply_min_max_price_discounts` (blended pct, qty limit) — 0 test angka.
8. Path `disable_rounded_total` di `update_invoice` — 0 test grand_total rounded vs exact.

---

## §4. Correctness frontend

### COR-FE-01 [CRITICAL] Checkout offline dobel-tap = dobel antrean — TERVERIFIKASI MANUAL
- Lokasi: `POS/src/pages/POSSale.vue:2160-2343` + `POS/src/components/sale/PaymentDialog.vue:3475-3555`
- Bukti: `completePayment` hanya `emit("payment-completed")` tanpa flag; prop `isSubmitting` PaymentDialog **tidak pernah di-bind** dari POSSale (grep `:isSubmitting` di POSSale.vue = kosong); jalur offline tidak pernah set `cartStore.isSubmitting`; tidak ada guard in-flight di `handlePaymentCompleted` (grep `submitInFlight|inFlight|submitLock` = kosong).
- Dampak: dua tap cepat → dua baris `invoice_queue` dengan `offline_id` berbeda → server dedupe per `offline_id` saja → **transaksi dobel saat replay**.
- Fix: guard ref lokal `submitInFlight` di awal `handlePaymentCompleted` (set di awal, release di `finally`), atau bind prop `isSubmitting` dan set-nya di jalur offline juga.
- Verifikasi: vitest — dua panggilan `handlePaymentCompleted` beruntun → hanya satu baris antrean.

### COR-FE-02 [HIGH] Load draft tidak menyimpan/reset diskon header & kupon
- Lokasi: `POSSale.vue:2651-2698` + `POS/src/stores/posDrafts.js:41-46`
- Bukti: `handleLoadDraft` hanya set `invoiceItems`, `customer`, `buyerName`; `draftData` tidak memuat `additional_discount`; `additionalDiscount` & `couponCode` cart sebelumnya tidak direset.
- Dampak: diskon kupon cart A (Rp50rb) ikut ter-submit pada draft B; draft A kehilangan diskonnya saat reload.
- Fix: round-trip `additionalDiscount` + `couponCode` di draft; reset keduanya sebelum mengisi item draft.

### COR-FE-03 [HIGH] Ganti shift tidak membersihkan cart
- Lokasi: `POSSale.vue:1899-1962` (`handleShiftOpened` / `handleShiftClosed`)
- Bukti: `clearCart()` hanya di jalur logout (`cleanupUserSession`); `handleShiftOpened` set profil baru tanpa clear.
- Dampak: cart kasir lama (harga price list profil lama) ter-submit di profil baru — total salah harga.
- Fix: `cartStore.clearCart()` di `handleShiftClosed` (atau saat profil berubah di `handleShiftOpened`).

### COR-FE-04 [HIGH] Invoice `sync_failed` diretry tanpa akhir, tanpa backoff, tanpa notifikasi
- Lokasi: `POS/src/utils/offline/sync.js:114, 250-260` + `POS/src/stores/posSync.js:73-91`
- Bukti: filter antrean `!inv.synced && !inv.superseded` (tidak exclude `sync_failed`); `handleSyncFailure` set flag di `retry_count >= 3` tapi tidak ada konsumen flag (grep = 1 hit).
- Dampak: error 4xx permanen dipukul ulang tiap reconnect, menggantung invoice lain di belakangnya; kasir tak pernah tahu penyebabnya.
- Fix: exclude `sync_failed` dari auto-sync; tampilkan di OfflineInvoicesDialog untuk keputusan manual (retry/hapus); tambah backoff.

### COR-FE-05 [HIGH] Scope service worker tidak mencakup halaman `/pos` — offline mode halaman tidak jalan
- Lokasi: `POS/vite.config.js:70-217` + `POS/src/main.js:53-69`
- Bukti: SW dibuild ke `../pos_next/public/pos` (URL `/assets/pos_next/pos/sw.js`), `registerSW()` tanpa `scope`, tanpa header `Service-Worker-Allowed`; sementara `runtimeCaching` & manifest menarget `/pos`.
- Dampak: SW hanya mengontrol `/assets/pos_next/pos/*` — rule `pos-page-cache` (buka app saat offline) dan `NetworkOnly /api` tidak pernah dieksekusi di halaman kasir.
- Fix: serve SW dari root `/sw.js` ATAU header `Service-Worker-Allowed: /` + `registerSW({ scope: "/" })` (cek cara serve header di Frappe; opsi pindah outDir + route adalah perubahan lebih besar — mulai dari scope).
- Verifikasi: DevTools → Application → Service Workers: scope `/`; uji buka `/pos` dalam mode offline.

### COR-FE-06..11 [MEDIUM]
- **COR-FE-06** `POS/src/utils/currency.js:56, 95-103` — simbol SAR salah (`"ê"`), dan semua uang dipaksa 0 desimal (`formatCurrency` hardcode `getFormatter(0, ...)`, dipakai 242 lokasi) → profil non-IDR menampilkan `$13` untuk `$12.50` di UI dan struk. Fix: perbaiki `SYMBOLS.SAR`; desimal mengikuti `settings.currency`.
- **COR-FE-07** `POS/src/utils/mutex.js:118-131` — timeout mutex melepas lock padahal fn masih jalan (sync hang >60s) → dua loop sync paralel. Fix: lock tetap terpegang sampai fn settle; timeout hanya sinyal.
- **COR-FE-08** `POS/src/utils/offline/db.js:167-201` — jalur "recovery" `checkDBHealth` menghapus SELURUH Dexie DB termasuk `invoice_queue` saat VersionError (dead code sekarang, bom waktu). Fix: ekspor `invoice_queue`/`drafts` sebelum delete, atau hapus jalur recreate.
- **COR-FE-09** `POS/src/utils/offline/workerClient.js:390-392` — fallback PING/CHECK_OFFLINE mengembalikan `true` (= "online") padahal komentar bilang asumsi offline → terbalik. Fix: kembalikan `false` / selaraskan semantik.
- **COR-FE-10** `POS/src/utils/print/transport.js:50-126` + `printInvoice.js:708-754` — driver gagal SETELAH struk keluar → chain lanjut ke driver berikut + fallback browser → struk dobel/tripel saat printer bermasalah. Fix: bedakan gagal-pra-cetak vs pasca-cetak; matikan fallback untuk gagal pasca-cetak.
- **COR-FE-11** `sync.js:504-516` vs `offline.worker.js:416-449` — dua jalur save offline menyimpang; `saveOfflinePayment` menulis `payment_queue` yang tidak pernah dibaca siapa pun. Fix: hapus `saveOfflinePayment` + `payment_queue`; satu jalur save saja (worker).

### COR-FE-12..16 [LOW]
- `useInvoice.js:457` + `sync.js:294` — qty `0` diam-diam jadi `1` (`|| 1`) → validasi eksplisit.
- `PaymentDialog.vue:3198` — `type: method.type || __("Cash")`: string TERJEMAHKAN masuk payload data → kirim konstanta `"Cash"`, terjemahkan hanya label.
- `printInvoice.js:240-242` — tanggal struk `"YYYY-MM-DD"` diparse UTC → bisa mundur sehari di timezone negatif; parse manual tanpa shift zona.
- `POSSale.vue:1656-1658` — `offerReapplyTimer` tidak di-clear di `onUnmounted` (1756).
- `offline/db.js:378-419` — `clearBrowserCache` menghapus semua key `frappe_*` (termasuk sesi/CSRF) → pakai daftar eksplisit ala `sessionCleanup.js`.

**i18n: BERSIH.** Sampling menemukan < 10 string hardcoded (contoh: `"POS Next"` fallback struk `printInvoice.js:232`, `"BrainWise"` footer). ±1.956 pemanggilan `__()` — tidak perlu sprint i18n.

---

## §5. —

*(dimensi performa, kompleksitas, dan rilis ada di §6–§8)*

## §6. Performa (urut dampak; skala acuan: 50k item, 1jt invoice)

### PERF-01 [tinggi] HQ monitoring: derived table scan jutaan baris + WHERE tidak sargable
- `pos_next/api/hq_monitoring.py:751-759` — `sales_invoice_item_union(...)` tanpa filter tanggal/company di-join ke `_invoice_from()`. Fix: teruskan filter `posting_date`/`company` ke DALAM subquery union.
- `pos_next/api/hq_monitoring.py:567, 1142` — `TIMESTAMP(alias.posting_date, alias.posting_time) <= %(cutoff)s` mematikan index. Fix: `({alias}.posting_date < %(cutoff_date)s OR (posting_date = %(cutoff_date)s AND posting_time <= %(cutoff_time)s))`.
- Verifikasi: `EXPLAIN` sebelum/sesudah; target: tidak ada full scan `tabSales Invoice Item`.

### PERF-02 [tinggi] N+1 saat tutup shift
- `pos_closing_shift.py:348` — loop `frappe.get_doc(dt, d.name).as_dict()`. Fix: bulk header `frappe.get_all` + child payments/items `WHERE parent IN %(names)s`, kelompokkan per parent.
- `pos_next/api/invoices.py:2244-2268` — pola sama di riwayat invoice kasir (`get_invoices`): SQL per invoice. Fix yang sama.

### PERF-03 [tinggi] Sinkronisasi customer tanpa batas
- `POS/src/utils/offline/cache.js:188` — `get_customers` dengan `limit: 0` (semua customer sekali tarik). Fix: paginasi batch 500–1000 atau delta `modified_since`.

### PERF-04 [tinggi] Pencarian customer frontend tanpa debounce di array reaktif besar
- `POS/src/components/sale/InvoiceCart.vue:1892-1904` — `.filter()` semua customer di main thread per ketikan. Fix: debounce ≥250ms + index Map/trie.

### PERF-05 [tinggi] Price Group N+1 masif
- `pos_next/pos_next/doctype/price_group/price_group.py:80-91, 327-370` — `get_value` per baris item + `get_all("Item Price")` per identitas. Fix: bulk fetch + dict lookup.

### PERF-06..10 [sedang]
- **PERF-06** Broadcast realtime global (sama dengan SEC-NEW-04 — satu fix): `realtime_events.py:87-90, 127, 210` → room per profil/outlet.
- **PERF-07** `services/sales_recap.py:79-81` — subquery `IN (SELECT os.name FROM tabPOS Opening Shift ...)` tanpa filter tanggal → pakai list `shifts` yang sudah diambil (baris 67): `IN %(shifts)s`.
- **PERF-08** `sales_recap.py:443` — filter `reference_no` pada `tabPayment Entry` unindexed → index komposit `(reference_no, docstatus, payment_type)` (penuhi setelah COR-BE-01; kalau COR-BE-01 pakai opsi (b), index di `tabPayment Entry Reference.reference_name`).
- **PERF-09** `api/items.py:1257-1265` — search `CONCAT(...)` LIKE per kata di 13 kolom → index FULLTEXT atau prefix lookup.
- **PERF-10** Missing index doctype JSON (set `"search_index": 1` + patch): `pos_opening_shift.json` `status`, `posting_date` (dipakai bootstrap.py:180); `offline_invoice_sync.json` `status` (dipakai invoice_type.py:69); `wallet_transaction.json` `reference_name` + `transaction_type` (dipakai wallet_transaction.py:481-485).

### PERF-11..16 [sedang-rendah]
- `api/wallet.py:238-258` — `get_all` invoice customer tanpa limit + `get_value("Mode of Payment")` per baris → set metode wallet sekali + batasi draft pending.
- `POS/src/components/sale/ItemsSelector.vue:1158-1161` — hasil pencarian dirender semua tanpa virtual scroll/paginasi → virtualisasi atau batas 50/halaman.
- `POS/vite.config.js:225` — tanpa `manualChunks`, warning limit dinaikkan ke 1.5MB → pisahkan `qz-tray`, `html2canvas`, `dexie`, `frappe-ui`.
- `api/items.py:2391-2396` — batch N+1 (per item `get_batch_qty` + `get_cached_doc("Batch")`) → single join query.
- `api/packages.py:286, 310, 457` — `get_value("Item", ..., "is_stock_item")` per komponen → bulk dict.
- `api/promotions.py:205-238, 282-290` — `db.count` + `get_doc` per scheme → GROUP BY + bulk child.

### PERF-17..21 [rendah]
- `pos_closing_shift.py:174, 214` — `frappe.db.exists` redundan sebelum `get_cached_doc` → try/except DoesNotExistError.
- `tasks/cleanup_expired_promotions.py:41` — loop `set_value` per rule → satu UPDATE bulk `IN %(names)s`.
- `api/shifts.py:116-118, 176-177` — `frappe.get_doc` master POS Profile/Company → `get_cached_doc`.
- `POS/vite.config.js:132, 175-184` — dobel cache Workbox: aset hash hasil precache dicache lagi via runtime `pos-assets-cache` → hapus pola runtime itu.
- `services/sales_recap.py:440` — sudah ada catatan ponytail bahwa `reference_no` unindexed "fine at partial-payment volumes" — naikkan prioritasnya sejalan COR-BE-01.

---

## §7. Kompleksitas / ponytail-cleanup (OPSIONAL — branch terpisah, satu PR per kelompok)

Tags: `delete` = hapus, `stdlib`/`native` = ganti dengan bawaan, `yagni` = buang abstraksi, `shrink` = pendekkan. Estimasi total: **net −5.215 baris, −12 deps**.

**Backend (delete/yagni):**
- `delete` BrainWise Branding subsystem utuh: doctype `brainwise_branding`, `api/branding.py`, tasks branding monitor, observer loop klien, scheduler hook → footer teks statis atau hapus total.
- `delete` `pos_next/www/imin_probe.html` + route `/imin-probe` di hooks.py (probe hardware fase 0) — sekalian menutup SEC-NEW-12b.
- `yagni` CRUD POS Profile whitelisted (`api/pos_profile.py` get_create/create/update/delete) tak pernah dipanggil frontend → frappe.client/Desk.
- `delete` `get_product_bundle_availability` (`api/items.py`, 94 baris) dan `get_item_stock` — duplikat `get_items_bulk`/`get_stock_availability`, tak pernah dipanggil.
- `delete` `get_credit_sale_summary` + `get_credit_invoices` (`api/credit_sales.py`) — tak pernah dipanggil.
- `delete` admin wallet endpoints (`api/wallet.py` `create_manual_wallet_credit`, `get_wallet_payment_methods`) — sekalian menutup SEC-NEW-09.
- `delete` `OfflineInvoiceSync.create_sync_record` (invoices.py bypass langsung `frappe.get_doc`).
- `delete` `promotions.search_items` — duplikat `api/items.get_items`.
- `delete` `_pn_run_tests.py` dari paket → pindah ke `scripts/` (test runner tetap dipakai! pindah, jangan hapus — update perintah di memori/dok).
- `delete` `_pn_sync_ws.py` (untracked) — buang.
- `yagni` `_check_profile_access` diduplikasi persis di `credit_sales.py`, `shifts.py`, `invoices.py` → satu helper terpusat di `pos_profile.py`.
- `shrink` `pos_closing_print.py` menghitung ulang recap dari nol → reuse `services/sales_recap.shift_scope`.
- `shrink` `pos_invoice_events.py` daftar hook manual → event map dispatcher.
- `stdlib` `format_rupiah` hand-rolled (`pos_closing_print.py`) → `frappe.utils.fmt_money`.
- `stdlib` `get_csrf_token` endpoint custom (`api/utilities.py`) → `frappe.sessions.get_csrf_token()`/bootinfo.
- `stdlib` `_parse_json`/`_parse_list_parameter` (`api/packages.py`) → `frappe.parse_json`.
- `native` `CUSTOM_FIELDS` dict 400 baris di `install.py` → custom field JSON standar (`custom/*.json`). *(Sesuaikan dengan REL-03.)*

**Frontend:**
- `delete` harness dev: `POS/harness.js`, `harness-shim.js`, `harness.html`, `harness.vite.config.js` di root, `POS/src/harness*.js` → folder test terpisah di luar src (kalau masih dipakai GUI-test, pindah — jangan hapus buta; cek dulu pemakaian).
- `native` `lowEndOptimizations.js` + `performanceConfig.js` → rAF, CSS contain, konstanta statis.
- `native` `logger.js` (ANSI di browser, buffer memori) → console + drop di build.
- `yagni` `useCountryCodes.js` vs `stores/countries.js` identik 100% → sisakan satu.
- `yagni` `posDrafts.js` wrapper tipis draftManager → panggil langsung.
- `yagni` `useOfflineStatus.js`, `useStock.js`, `utils/payment.js` → inline/pakai yang sudah ada.
- `stdlib` `offline/uuid.js` fallback regex → `crypto.randomUUID()`.

**Deps (12):**
- `pyproject.toml`: hapus `pypdf` (0 import).
- `POS/package.json`: hapus `feather-icons` (lucide bawaan frappe-ui), test tools ke devDependencies (`@pinia/testing`, `@vue/test-utils`), evaluasi `showdown`/`highlight.js`/`interactjs` di optimizeDeps (0 import di src).
- root `package.json`: `playwright`, `baseline-browser-mapping` → devDependencies atau hapus.

---

## §8. Higienitas rilis & checklist pra-deploy

### REL-01 [CRITICAL] `install.py` reclaim bisa menghapus konfigurasi POS Settings — TERVERIFIKASI MANUAL
- Lokasi: `pos_next/install.py:670-673`
- Bukti: saat doctype POS Settings drift (`module != "POS Next"` ATAU `issingle=1`), `reclaim_pos_settings_doctype` menjalankan `frappe.db.sql("DROP TABLE IF EXISTS \`tabPOS Settings\`")` + `DELETE FROM tabSingles WHERE doctype='POS Settings'` + hapus DocField/DocPerm/DocType.
- Konteks: arsitektur saat ini memang doctype TABEL (baris per-profil) + global di tabSingles — reclaim sengaja scorched-earth untuk dev. Di production, drift apa pun (sinkronisasi ERPNext, developer_mode) = **seluruh override per-profil + setting global hilang diam-diam**.
- Fix: pertahankan pemulihan metadata, tapi (a) jangan `DELETE FROM tabSingles` bila yang tersisa memang Single yang valid — atau (b) ekspor baris per-profil + singles ke log/backup sebelum drop, dan (c) tambahkan guard env (`POS_ALLOW_SETTINGS_RECLAIM=1`) supaya tidak pernah jalan di production tanpa sengaja.

### REL-02 [CRITICAL] Fixture `custom_docperm.json` duplikat + membajak doctype core — TERVERIFIKASI MANUAL
- Lokasi: `pos_next/fixtures/custom_docperm.json` (regenerasi 23 Sep 01:26)
- Bukti: 59 entri; `Sales Invoice` punya **dua baris POSNext Cashier** yang bertentangan (read/write/create/submit=1 vs read=0/write=0/delete=1/if_owner=1); 48+ entri role non-kasir pada doctype core (Territory×6, Item×9, Warehouse×7, Customer×8, Bin×6, Sales Invoice Item×7, Payment Entry×3, POS Opening Entry×3, POS Closing Entry×3, POS Profile×3).
- Dampak: begitu Custom DocPerm ada untuk doctype, **DocPerm bawaan ERPNext diabaikan seluruhnya** — role yang tidak tercakup entri fixture kehilangan akses diam-diam; duplikat kasir bisa saling menimpa tergantung urutan.
- Fix: regen fixture HANYA untuk role `POSNext Cashier` + doctype milik app; hapus seluruh entri role core & doctype core yang tidak sengaja diubah SEC-13; pastikan satu baris per (doctype, role).
- Verifikasi: bandingkan jumlah entri sebelum/sesudah; test ala `test_cashier_permissions.py` untuk semua perm yang diharapkan.

### REL-03 [HIGH] `uninstall.py` bocor 20+ custom field + DocPerm + Role + Workspace
- Lokasi: `pos_next/uninstall.py` (`remove_custom_fields` hanya 10 field lama)
- Fix: iterasi seluruh key `CUSTOM_FIELDS` + `PRICE_GROUP_CUSTOM_FIELDS` dari install.py (termasuk `pos_coupon_code`, `pos_queue_*`, `pos_schedule_*`); tambah pembersihan Custom DocPerm hasil fixture, Role `POSNext Cashier`/`Nexus POS Manager`, Workspace `POSNext` + sidebar.

### REL-04 [HIGH] Patch `copy_global_settings_to_single.py` baca `allowed_locales` lewat meta yang sudah dihapus
- Lokasi: `pos_next/patches/v2_11_0/copy_global_settings_to_single.py:64-65, 31-36`
- Bukti: `frappe.get_doc("POS Settings", name).get("allowed_locales")` selalu None (field sudah hilang dari meta saat model-sync jalan sebelum patch); cek kolom menangkap exception MySQL 1054 mentah.
- Fix: baca data bahasa lama langsung via SQL/qb ke `tabPOS Allowed Locale`; ganti catch 1054 dengan `frappe.db.has_column`. Pastikan patch idempoten (migrate ulang tidak dobel).

### REL-05 [HIGH] `pos.html` load bundle `nexus_demo` yang 404 di production + build artefak tidak di-track
- Lokasi: `pos_next/www/pos.html:18-19` (`/assets/nexus_demo/css|js/demo_countdown_banner.bundle.*`), `.gitignore` (`pos_next/public/pos/`, `pos_next/www/pos.html`)
- Fix: hapus dua baris nexus_demo; pastikan pipeline deploy menjalankan `npm --prefix POS run build` (CI tidak build frontend) — lihat checklist bawah.

### REL-06..10 [MEDIUM/LOW]
- **REL-06** Versi tidak sinkron: `pos_next/__init__.py` = 1.17.0, root `package.json` = 1.15.0, patch database = v2_11_0 → samakan ke 2.11.0 (atau skema penamaan yang disepakati).
- **REL-07** `pyproject.toml`: hapus `pypdf`; longgarkan-ignore ruff F401/F403/F405/B023 dievaluasi ulang (F403/F405 menutupi bug nyata).
- **REL-08** `pos_next/translations/id.csv` (MODIFIKASI BELUM COMMIT): 895 string sumber belum diterjemahkan, 838 key kedaluwarsa, duplikat key akibat trailing space (`"Discount"` vs `"Discount "` baris 37/2066; minimum-purchase 1429/646) → bersihkan spasi, sinkronkan, BARU commit.
- **REL-09** `hooks.py:30` — `_asset_version = get_build_version()` dead code (include-nya dikomentari) → hapus (juga hilangkan I/O boot; sinkron dengan CLN `utils.get_build_version`).
- **REL-10** `docs/POST_UPDATE_VERIFICATION.md` (untracked) = SOP rilis valid → layak di-commit (dengan perintah user).

### Checklist pra-deploy production
1. REL-01, REL-02, REL-04 tuntas + test hijau.
2. `npm --prefix POS run build` di host → artefak masuk distribusi (CI tidak build).
3. `bench migrate` → pantau patch v2_11_0 (guard 1054 sudah ada di main `b6f7ae9`); jalankan DUA KALI untuk buktikan idempoten di staging.
4. Cek `bench --site <site> console`: `frappe.get_doc("POS Settings")` utuh, `frappe.db.count("Custom DocPerm")` sesuai harapan, Allowed Locales terisi.
5. Role `POSNext Cashier` + `Nexus POS Manager` ada; resep persona: kasir = POSNext Cashier + Stock User + POS Profile User outlet; manager = Nexus POS Manager saja.
6. Tutup semua shift aktif sebelum migrate (SOP POST_UPDATE_VERIFICATION).
7. Smoke test GUI: checkout online, checkout offline (setelah COR-FE-05), tutup shift dengan pelunasan partial (setelah COR-BE-01), struk, kupon.
8. Setelah semua selesai dan user minta: commit + push (LARANGAN tanpa perintah eksplisit).

---

## §9. Yang sudah diverifikasi BAIK (jangan diubah / tidak perlu re-audit)
- Idempotensi offline `offline_id` + `checkOfflineIdSynced` + `DUPLICATE_OFFLINE_INVOICE` di jalur sync — benar.
- Money math kartu konsisten `roundCurrency` (mode ikut System Settings).
- `/api/` di SW sudah NetworkOnly + pembersihan cache lintas-kasir (`sessionCleanup.js`) rapi — tinggal diselesaikan scope-nya (COR-FE-05).
- Semua SQL di `api/items.py` & `api/hq_monitoring.py` terparameterisasi; tidak ada injeksi.
- Offer-quota serialization (`for_update` + ledger `ignore_if_duplicate`) benar.
- Listener websocket/composable melepas listener secara simetris.
- vitest: 475/475 lulus per 23 Sep.
- Timezone konsisten server-timezone di seluruh batas hari.

## §10. Catatan objektif akhir audit
- Metode: audit statis (read-only). Temuan `[NEEDS-CONFIRM]` butuh bukti runtime (konkurensi/urutan request) — buat test reproduksi dulu sebelum fix.
- Ruff tidak terpasang di env bench — `./env/bin/python -m ruff` gagal (`No module named ruff`); evaluasi lint Python butuh install manual (keputusan user).
- Temuan dengan nomor sama di dua dimensi (SEC-NEW-04 = PERF-06; SEC-04 caveat = COR-BE-02) dikerjakan sekali saja.
