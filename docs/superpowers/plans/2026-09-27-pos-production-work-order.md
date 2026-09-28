# Desain: Production POSNext berbasis Native ERPNext Work Order

- **Tanggal**: 2026-09-27
- **Status**: DESAIN SELESAI — belum implementasi. Semua klaim ERPNext diverifikasi dari kode **ERPNext 16.33.0** di bench ini (`apps/erpnext`), bukan dari ingatan versi lain.
- **Proses**: eksplorasi 3 subagent (current-state POSNext, Work Order native, Stock Entry/GL/BOM) + diskusi advisor; 2 keputusan default disetujui user via plan.

---

## 0. Ringkasan eksekutif

Production POSNext beralih dari **Stock Entry "Manufacture" independen** (tanpa work_order/bom_no) menjadi **Work Order native ERPNext** dengan UX POS tetap 1–2 klik:

```
POS Production Recipe  ──sync satu arah──▶  BOM (artefak turunan)
                                               │
Start  ──▶  Work Order (submit, skip_transfer=1)          [status: Not Started]
Finish ──▶  SE Manufacture (lahir dari make_stock_entry)  [status: In Process → Completed]
            raw: warehouse outlet → FG: warehouse outlet
```

Keputusan inti:

| # | Keputusan | Pilihan |
|---|---|---|
| D1 | Jembatan Recipe→BOM | Auto-sync satu arah saat Recipe disave; 1 BOM aktif per company |
| D2 | Alur produksi | **Satu langkah `skip_transfer=1`** — BISA DIBALIK (tanpa ubah skema) |
| D3 | Loss normal | `process_loss` native di SE Manufacture |
| D4 | Gagal total | `close_work_order` + SE Material Issue ke expense |
| D5 | Home data POS | Custom fields `posa_*` di Work Order; Log pensiun bertahap — BISA DIBALIK |
| D6 | Status custom | TIDAK ADA. Label "Failed"/"Partially Completed" dihitung dari field native |
| D7 | Batch FG | Setting native `make_serial_no_batch_from_work_order`; kode batch manual DIHAPUS |
| D8 | Batch raw | FIFO-expiry pick existing di service layer (tetap) |
| D9 | Warehouse produksi | Dua field di POS Settings per-profil (source & FG), resolver existing; kosong → fallback POS Profile.warehouse. GL ideal via Opsi B (akun gudang berbeda) |

Dampak akuntansi pergantian ≈ **nol** untuk operasi normal (same-warehouse → GL net-zero, sama seperti hari ini). Dampak skema: **nol doctype baru**, hanya 3 custom fields di WO + 1 field di child Recipe Company.

---

## 1. Kondisi saat ini (current state, terverifikasi)

- `pos_next/api/production.py` — 2 endpoint: `get_production_recipes(pos_profile)` (:49-120) dan `create_production(recipe, qty, pos_profile)` (:156-299). `create_production` membuat + submit **Stock Entry purpose "Manufacture" tanpa `work_order` dan tanpa `bom_no`** (independent manufacture — secara native legal, `stock_entry.py:2169-2172, 2205`), `s_warehouse == t_warehouse ==` POS Profile warehouse, raw dari resep (faktor `qty / output_qty`), batch FIFO-expiry (1 batch, tanpa split), FG ber-batch → Batch baru dibuat manual (`:258-264`), `ignore_permissions`, lalu insert+submit **POS Production Log** (submittable: recipe, production_item, qty, items_used JSON, stock_entry Link, pos_profile, company).
- `POS Production Recipe`: recipe_name (unique), production_item, output_qty, disabled, items (item_code, qty, stock_uom), companies (company, enabled). **Tidak terhubung ke BOM.**
- Frontend `POS/src/components/pos/ProductionDialog.vue` embedded di POSMenuDialog (menu disembunyikan bila offline / tanpa izin create log; `POSSale.vue:3178-3186`): kartu resep + indikator bahan → detail locked → input qty → "Process Production" → 1 API call atomik.
- Permission: kasir `POSNext Cashier` TIDAK punya DocPerm Stock Entry/BOM/Work Order; satu-satunya pintu = endpoint POSNext dengan `_assert_profile_access` (borrowed dari `packages.py:46-63`) lalu `ignore_permissions`.
- Test: `pos_next/api/test_production.py` (±17 kasus) + `ProductionDialog.test.js` + gate menu.

**Masalah yang mendorong redesain**: tidak ada lifecycle produksi, tak bisa membedakan rencana/proses/hasil/gagal, loss dicatat manual, history tidak proper.

---

## 2. Validasi konsep (A)

Konsep `Production → Work Order → Material Transfer → In Progress → Finish → Manufacture` **valid secara native**, dengan dua koreksi:

1. **Langkah Material Transfer→WIP tidak dijadikan default** (D2). Untuk produksi F&B menit-skala di satu outlet, gudang WIP tidak menambah informasi — hanya menambah 1 dokumen per produksi + kewajiban konfigurasi `wip_warehouse` per company. `skip_transfer=1` membuat SE Manufacture menarik raw **langsung dari `required_items.source_warehouse`** yang di-set dari warehouse outlet per-WO (`work_order.py:2696-2700`; `source_warehouse` WO dipropagasikan ke required_items di `work_order.py:483-484`).
   - Trade-off diterima: tidak ada snapshot stok "sedang diproses" di gudang WIP; batch raw tidak terbawa dari transfer (fitur eksklusif mode backflush "Material Transferred").
   - Upgrade path: set `Company.default_wip_warehouse` + `skip_transfer=0` + backflush "Material Transferred for Manufacture" — service layer (yang selalu memanggil `make_stock_entry` per purpose) mendukung tanpa ubah skema. Jangan dibangun sebelum ada kebutuhan nyata.
2. **"Failed" bukan status tersimpan** — label dihitung (D6). WO native tidak punya status Failed; memaksa status custom = dua sumber kebenaran.

**Yang cocok native**: seluruh siklus WO (status otomatis dari ledger), planned vs actual (`qty` vs `produced_qty`/`process_loss_qty`), partial output (multi SE Manufacture per WO), loss (`process_loss`), stop/close (`stop_unstop`, `close_work_order`), traceability (`SE.work_order` indexed).

**Yang berpotensi bermasalah**: `bom_no` wajib → butuh jembatan Recipe→BOM (D1); Manufacturing Settings bersifat **global site-wide** (issingle) → satu kebijakan untuk semua outlet; SE Material Issue **dipaksa kehilangan link `work_order`** (`stock_entry.py:1028`) → linkage gagal-total lewat remark + sisi POS.

---

## 3. Fakta native ERPNext 16.33.0 yang menjadi fondasi desain

Dengan referensi file (root `apps/erpnext/erpnext/`):

- **WO `bom_no` reqd=1** (`work_order.json:71-77`); BOM wajib `is_active=1` + `docstatus=1` + item match (`bom.py:1542-1565`); `is_default` tidak wajib; BOM `with_operations=0` valid tanpa Job Card/Routing (`bom.json:186`, `bom.py:722-724, 1234-1236`).
- **Status WO**: Draft, Submitted, Not Started, In Process, Stock (Partially) Reserved, Completed, Stopped, Closed, Cancelled (`work_order.json:21-34`). In Process saat `material_transferred_for_manufacturing > 0` (atau `produced_qty > 0` bila skip_transfer; `work_order.py:692-734`). **Completed saat `produced_qty + process_loss_qty >= qty`** (`work_order.py:705-708`).
- **`make_stock_entry(work_order_id, purpose, qty, ...)` whitelisted** (`work_order.py:2664-2723`) — mengembalikan **draft SE** (`as_dict`) hasil `get_items()`; penerima wajib insert+submit. Inilah satu-satunya cara sah membuat SE Manufacture yang menunjuk WO — math backflush/sisa-konsumsi/anti-duplikat ada di dalamnya.
- **Loss native**: `process_loss_percentage` ada di **BOM dan header Stock Entry** (BUKAN di WO). `validate_fg_completed_qty` menjadikan selisih `fg_completed_qty − qty baris FG` sebagai `process_loss_qty` otomatis (`stock_entry.py:847-880`); FG row qty = `fg_completed_qty − process_loss_qty` (`stock_entry.py:3383`); raw dikonsumsi GROSS; **valuasi FG = (biaya raw − scrap) / qty FG NET** (`stock_entry.py:1770`) → biaya loss terkapitalisasi ke unit-cost FG; **tidak ada SLE untuk loss**. WO mengakumulasi `process_loss_qty` dari SE submitted (`work_order.py:876-892`).
- **Scrap/secondary native**: BOM Secondary Items (Co-Product/By-Product/Scrap/Additional Finished Good) + `scrap_warehouse` (`stock_entry.py:3248-3262, 3503-3526`); nilai scrap mengurangi biaya FG.
- **Overproduction**: diblokir di atas `qty + overproduction_percentage_for_work_order%` (`stock_entry.py:2198-2211`, `work_order.py:770-803`).
- **Multi SE parsial per WO**: didukung native; agregat di `produced_qty`.
- **Cancel**: WO diblokir bila ada SE submitted (`work_order.py:1227-1242`); **cancel SE memanggil `update_work_order` → agregat & status WO mundur otomatis** (`stock_entry.py:605`).
- **Whitelisted lain**: `stop_unstop` (:2758), `close_work_order` (:2830), `make_stock_return_entry` (:3116), `make_work_order` (:2548), `check_if_scrap_warehouse_mandatory` (:2645).
- **GL**: hanya bila `company.enable_perpetual_inventory=1` (`stock_controller.py:333-335`). Akun warehouse: `warehouse.account` → parent → `Company.default_inventory_account` → satu-satunya akun stock (`stock/__init__.py:56-98`). **Akun WIP = akun di dokumen WIP Warehouse** (tidak ada field company khusus). Manufacture: credit akun warehouse raw, debit akun warehouse FG; selisih → `SE Detail.expense_account` ("Difference Account", default `Company.stock_adjustment_account`, dilarang bertipe Stock, `stock_entry.py:908-915`); `merge_similar_entries` menghapus baris yang saling meniadakan (`general_ledger.py:275-328`). Material Transfer → GL hanya bila akun asal ≠ akun tujuan. Additional costs → GL pasti (credit beban, debit FG).
- **Dimensi**: WO **tidak punya** cost_center; SE punya project + cost_center (header+detail) dan bisa diset via API; default cost center: Project → Item.buying_cost_center → Item Group → Brand → `Company.cost_center` (`get_item_details.py:1056-1086`, `stock_entry.py:2737-2738`).
- **Batch FG otomatis**: `Manufacturing Settings.make_serial_no_batch_from_work_order` (`manufacturing_settings.json:151`).
- **WO timestamp**: `actual_start_date`/`actual_end_date`/`lead_time` terisi dari posting SE pertama/terakhir (`work_order.py:1542-1558`).

---

## 4. Keputusan desain (detail)

### D1 — Recipe→BOM: auto-sync satu arah saat Recipe disave

- Recipe tetap **single source of truth** domain POS (gating per-outlet via child `companies`, `disabled`, UI kasir). BOM = **artefak turunan yang regenerable**: `with_operations=0`, `is_active=1`, **tanpa memaksa `is_default`** (WO membawa `bom_no` eksplisit → tidak konflik dengan BOM lain milik item yang sama).
- **BOM tidak menyimpan warehouse** — warehouse hidup di WO (`source_warehouse`/`fg_warehouse`). Wajib, karena 1 company bisa multi outlet.
- **Versioning**: perubahan item resep → BOM lama `is_active=0` + BOM baru disubmit. WO lama tetap menunjuk BOM lama (required_items sudah snapshot saat WO submit; backflush mode "BOM" membaca required_items WO, bukan BOM hidup) → "planned as of then" terjaga.
- Link hasil sync disimpan di child `POS Production Recipe Company` → field baru `bom_no` (Link BOM, read-only).
- **Ditolak**: (b) buang Recipe pakai BOM langsung — manajemen resep pindah ke Desk (melanggar prinsip UX), gating per-outlet hilang; (c) lazy-create BOM saat Start — kegagalan BOM meledak di momen produksi dan masalah sinkronisasi hanya ditunda.

### D2 — Satu langkah `skip_transfer=1` (BISA DIBALIK)

Lihat §2 poin 1. Konfigurasi runtime yang menyertai: `Manufacturing Settings.backflush_raw_materials_based_on = "BOM"`, `overproduction_percentage_for_work_order` (disarankan 10 — setting global, pastikan aman semua resep), `make_serial_no_batch_from_work_order = 1`.

### D2b — Warehouse produksi dapat disetel user (tanpa hardcode) + rekomendasi GL "Opsi B"

- **Dua field baru di `POS Settings`** (doctype milik app, baris per-profil): `production_source_warehouse` (asal bahan) dan `production_fg_warehouse` (tujuan hasil). Diresolusi via `get_effective_pos_setting(pos_profile, fieldname)` (settings_resolver existing, 3-tier). **Kosong → fallback `POS Profile.warehouse`** di service (perilaku hari ini terjaga; tidak ada hardcode). Tier global (tabSingles) sengaja tidak dipakai untuk dua field ini — warehouse itu spesifik outlet/company; nilai kosong pada baris enabled tetap di-coalesce ke fallback oleh service.
- Service mengisi `WO.source_warehouse` dan `WO.fg_warehouse` dari hasil resolusi; native `validate_warehouse_belongs_to_company` tetap jaring pengaman, ditambah pesan Indonesia yang ramah (gudang non-group/disabled/beda company).
- **UI**: `POSSettings.vue` tab stok (daftar warehouse sudah dimuat di dialog) — dua `SelectField` baru.
- **Rekomendasi GL "Opsi B"** (produksi tercatat di buku besar tanpa menambah dokumen): beri kedua gudang **akun persediaan berbeda** via field `account` di dokumen Warehouse (`stock/__init__.py:56-98`) — Gudang Bahan → *Persediaan Bahan Baku*, Gudang FG → *Persediaan Barang Jadi*. SE Manufacture menghasilkan credit BB / debit BJ: transformasi persediaan terlihat di GL dengan tetap **satu** dokumen. Setup murni master data per outlet; keputusan akuntansi (konfirmasi ke akuntan: *apakah neraca harus memisahkan persediaan BB vs BJ?*). Alternatif: biarkan net-zero (satu akun) bila memang cukup; gudang WIP dua-langkah hanya bila staging nyata.
- Catatan operasional Opsi B: penerimaan bahan masuk ke Gudang Bahan; POS tetap menjual dari warehouse POS Profile (= FG warehouse default); produksi memindahkan nilai antar dua akun persediaan. **Penerimaan bahan akan memakai Putaway Rule native ERPNext** (keputusan user 27 Sep) — aturan putaway per item mengarahkan target gudang ke Gudang Bahan secara otomatis di Purchase Receipt / Stock Entry, tanpa kode tambahan.

### D3 — Lifecycle & dokumen per transisi (B)

| Transisi POS | Aksi service | Dokumen native yang lahir/berubah |
|---|---|---|
| Draft POS (form) | belum ada dokumen | — |
| **Start** | WO insert + submit (`ignore_permissions`), `skip_transfer=1`, `bom_no` dari Recipe Company, `source_warehouse`/`fg_warehouse` = warehouse POS Profile, qty = planned, `posa_*` terisi | **Work Order** (status → Not Started) |
| **Finish (one-shot maupun dua-fase)** | `make_stock_entry(wo, "Manufacture", qty_gross)` → set batch raw FIFO + FG qty (good) + remark → insert + submit | **SE Manufacture** (WO → In Process; → Completed otomatis saat produced+loss ≥ qty) |
| **Close (berhenti lebih awal)** | `close_work_order(wo, "Closed")` (+ SE Material Issue bila material rusak) | WO → Closed |
| **Cancel** | cancel SE LIFO → cancel WO | SE Cancelled → WO Cancelled |
| **Koreksi hasil** | cancel SE (LIFO) → agregat WO mundur otomatis → Finish ulang | SE Cancelled, WO status mundur |

**One-shot "Process"** = Start+Finish dalam **satu request Frappe** = satu transaksi DB — atomik, rollback bersih.

### D4 — Label status POS (turunan, tanpa field tersimpan)

| POS menampilkan | Field native | Kondisi |
|---|---|---|
| Not Started | `status = Not Started` | WO submitted, belum ada SE |
| In Progress | `status = In Process` | SE Manufacture pertama |
| Completed | `status = Completed` | produced + process_loss ≥ qty |
| Partially Completed | `status = Closed` && 0 < produced < planned | label dihitung |
| Failed | `status = Closed` && produced = 0 | label dihitung |
| Cancelled | `status = Cancelled` | — |

`Stopped` native tidak diekspos ke POS (YAGNI). **Jangan pernah membuat `posa_status`.**

### D5 — Planned vs actual (D)

- Planned = `WO.qty` (+ snapshot `required_items`).
- Actual good = akumulasi baris FG SE submitted; actual loss = `WO.process_loss_qty`; kedua agregat dijaga native (`work_order.py:763-806, 862-892`).
- Contoh: Planned 100 → Good 92 / Loss 8 = **satu SE** dengan `fg_completed_qty=100` dan baris FG qty=92 (auto process_loss 8 — native `stock_entry.py:847-880`).
- **Nol tabel planned-vs-actual custom.**

### D6 — Loss handling (E)

| Skenario | Mekanisme native | Stock | Valuation/COGS | Dokumen |
|---|---|---|---|---|
| Loss normal (100→92+8, material habis) | SE Manufacture: `fg_completed_qty=100`, FG row 92 → auto process_loss | FG +92; raw −100-worth | Biaya loss **terkapitalisasi ke unit-cost FG**; COGS naik saat jual | 1 SE |
| By-product/scrap konsisten (reworkable, bernilai) | BOM Secondary Item di Recipe→BOM | Scrap masuk `scrap_warehouse` | Nilai scrap **mengurangi** biaya FG | 1 SE |
| Gagal total (100→0, material hancur) | `close_work_order` (produced=0) + **SE Material Issue** ke akun wastage | raw −(hancur) | **Beban periode ini** (credit stok, debit expense) | close + 1 SE |

Perbedaan treatment = **fitur akuntansi, bukan inkonsistensi**: process loss = normal loss (ditanggung unit yang jadi — standar costing); Material Issue = abnormal loss (beban periode).

**Ditolak**: loss hanya-data-POS (stock ledger bohong — raw tercatat 92-worth padahal fisik 100 hilang); SE tambahan untuk loss normal (dua dokumen + math manual + timing gap). **Jangan bangun UI scrap ad-hoc** — scrap hanya bila resep memang konsisten menghasilkan by-product.

### D7 — Production history (F)

- **End-state = query native**: list = WO `filters={posa_pos_profile: ...}` + detail SE via `SE.work_order` (indexed, `stock_entry.json:442-451`). Satu join, nol sinkronisasi.
- Trade-off vs doctype siap-pakai: butuh satu query join — menang karena menghilangkan duplikasi status dan dokumen kedua.
- **POS Production Log pensiun bertahap**: Fase 1 masih ditulis + field `work_order` (history lama-baru menyatu); Fase 3 daftar beralih ke query WO, Log freeze read-only untuk data lama. Existing standalone SE (pra-WO) tidak tersentuh.

### D8 — UX (G)

- **One-shot** (default): kartu resep (availability) → input qty → **Process** → 1 request (WO+SE). Selesai. Cocok untuk produksi menit-skala.
- **Dua-fase** (proses lama — rendam/simmer): **Start** → masuk daftar "Sedang Diproduksi" (WO Not Started/In Process per outlet: item, planned, produced, elapsed) → aksi **Finish / Close / Cancel**.
- Field **Finish**: Good qty (wajib), Loss qty (opsional, default 0, auto-suggest = planned − good), catatan (opsional → SE remark). Tidak ada field lain.
- Field **New/Start**: resep (pilih), planned qty (wajib), catatan (opsional). Sisanya server-side (warehouse, BOM, company dari POS Profile).
- Kolom riwayat: waktu (started/finished), item, planned, good, loss, status (label), operator, link WO/SE.
- Offline → menu tetap disembunyikan (perilaku existing). UX tetap locked (kasir tak bisa ubah bahan) — bahan dari resep server-side.

### D9 — Accounting & stock (H)

- **Dampak pergantian ≈ nol**: hari ini independent Manufacture same-warehouse → GL net-zero (debit FG = credit raw pada akun sama, habis dimerge); besok WO skip_transfer same-warehouse → **tetap net-zero**. Bedanya guard & agregat, bukan akuntansi.
- **Kapan GL muncul** (config penentu):
  1. `Company.enable_perpetual_inventory = 1` — tanpa ini TIDAK PERNAH ada GL.
  2. Akun warehouse asal ≠ akun tujuan (baris transfer muncul); kalau sama + dimension sama → net-zero.
  3. Additional costs ada → GL pasti (credit beban, debit FG).
  4. Selisih nilai (scrap manual, pembulatan) → tersisa di Difference Account (`Company.stock_adjustment_account`).
- Material Issue (gagal total): credit akun warehouse, debit akun wastage/expense → beban periode.
- COGS FG tetap terbentuk saat penjualan POS (perpetual). Cost center mengikuti default ERPNext (Company.cost_center fallback) — konsisten dengan model per-outlet company.

### D10 — Failure & cancellation (I)

| Kasus | Penanganan | Ledger |
|---|---|---|
| 1. Start lalu batal (belum ada SE) | cancel WO langsung (tidak diblokir) | nol dampak |
| 2. 100→70, dihentikan | SE Manufacture 70 (+loss bila ada) → `close_work_order` | benar otomatis — tak pernah ada yang pindah ke WIP |
| 3. Output lebih sedikit | SE parsial + close (label Partially Completed) | — |
| 4. Output lebih banyak (105) | SE 105 — diizinkan sampai qty+overproduction% (setting global 10%) | — |
| 5. Gagal total | close (produced=0) + SE Material Issue ke wastage; link via remark `WO-XXXX` (SE Material Issue dipaksa tanpa field work_order oleh `stock_entry.py:1028`) | raw keluar ke expense |
| 6. Salah input hasil | cancel SE LIFO → `update_work_order` otomatis (agregat & status mundur) → Finish ulang | SLE dibalik SE-cancel |
| 7. Koreksi setelah selesai | sama dengan 6 (cancel SE → status WO mundur → perbaiki → selesaikan lagi); cancel WO hanya setelah semua SE bersih | — |

### D11 — Source of truth (J)

| Data | Source of truth |
|---|---|
| Resep | POS Production Recipe (BOM = turunan regenerable) |
| Planned qty | Work Order (`qty` + `required_items`) |
| Material consumed | SE Manufacture (baris raw) |
| Actual FG | SE Manufacture (baris FG) + agregat `WO.produced_qty` |
| Loss | SE `process_loss_qty` + agregat `WO.process_loss_qty` (gagal total: Material Issue) |
| Status | **WO.status — satu-satunya** |
| Operator/profile/recipe | `posa_operator` / `posa_pos_profile` / `posa_recipe` di WO |
| Availability bahan | dihitung live dari Bin/batch — **tidak pernah disimpan** |
| History | query WO + SE |

### D12 — Arsitektur (K)

```
ProductionDialog.vue / daftar produksi   (UI: tidak tahu field WO/SE)
        │  call()
api/production.py                        (tipis: _assert_profile_access + parse/validasi input)
        │
services/production.py  [BARU]           (orchestrator: resolve BOM dari Recipe, WO lifecycle,
        │                                 make_stock_entry, set batch FIFO + FG/loss + remark, submit)
        ▼
ERPNext native: Work Order · Stock Entry · BOM · close/stop · valuation · GL
```

Aturan boundary: service **tidak pernah** menghitung konsumsi sendiri (delegasi `get_items()` via `make_stock_entry`); API tidak menyentuh dokumen lain; satu-satunya pintu tetap endpoint POSNext (pola permission existing).

---

## 5. Boundary kustomisasi (L)

**Native (jangan disentuh)**: Work Order, Stock Entry, BOM, status lifecycle, process_loss, close/stop, overproduction %, auto-batch FG (`make_serial_no_batch_from_work_order`), valuation, GL, planned-vs-actual agregat.

**Custom minimal (yang memang dibangun)**:
1. Hook sync Recipe→BOM satu arah (+ field `bom_no` di child Recipe Company).
2. Custom fields `posa_pos_profile`, `posa_operator`, `posa_recipe` di Work Order.
3. `pos_next/services/production.py` (orchestrator) + revisi `api/production.py` (endpoint baru: start/finish/close/cancel/list).
4. UI: ProductionDialog v2 (one-shot + dua-fase + daftar berjalan + riwayat).
5. Fixture DocPerm: TIDAK ditambah (kasir tetap tanpa DocPerm WO/BOM/SE).

**Jangan custom (berisiko merusak ERPNext)**: field status sendiri; math konsumsi/backflush manual; WIP warehouse per outlet (sekarang); Job Card/Routing/Workstation; doctype history baru (end-state); UI scrap ad-hoc; kode pembuatan batch FG manual (**dihapus**, diganti setting).

---

## 6. Verifikasi runtime (FASE 0 — wajib sebelum koding fitur)

Semua klaim di bawah berasal dari baca kode statis; verifikasi dengan posting nyata di site uji (posnext.localhost) sebelum implementasi:

1. `make_serial_no_batch_from_work_order` benar membuat Batch baru utk FG saat SE Manufacture submit — sebelum menghapus kode batch manual.
2. Perilaku status WO setelah cancel satu-satunya SE di WO yang sudah Closed/Completed (mundur otomatis ke In Process? bila tidak → `db_set` status di service — corner kecil).
3. Dua SE Manufacture parsial dalam satu WO lolos `check_duplicate_entry_for_work_order`.
4. Tidak ada guard tersembunyi `BOM.company` vs `WO.company` (grep tak menemukan; desain 1-BOM-per-company tetap untuk kebersihan).
5. `close_work_order` di produced=0 tidak diblokir validasi.
6. Posting aktual: Manufacture same-warehouse → nol baris GL (`merge_similar_entries`); **Opsi B** — source/fg beda gudang dengan akun berbeda → baris GL credit BB / debit BJ muncul; kalau akunnya sama → kembali net-zero. Kedua hasil benar.
7. Nilai `overproduction_percentage_for_work_order` (disarankan 10) aman untuk semua resep/outlet (setting global).
8. Draft SE hasil `make_stock_entry` mode backflush "BOM" memang batch-kosong (asumsi FIFO pick service).

Juga fase 0: set Manufacturing Settings (backflush "BOM", auto-batch, overproduction %) + pastikan akun warehouse company outlet lengkap (warehouse.account / default_inventory_account / stock_adjustment_account) + flag item FG (auto-batch, has_expiry bila perlu).

---

## 7. Fase implementasi (M)

| Fase | Isi | Gate |
|---|---|---|
| **0** | Verifikasi runtime §6 + settings + akun + item flags + setup Opsi B bila dipilih (2 gudang + akun persediaan per outlet) | Bukti posting di site uji (SLE/GL) |
| **1** | Sync Recipe→BOM (hook + field child) + `create_production` beralih ke WO one-shot; field POS Settings `production_source_warehouse`/`production_fg_warehouse` + UI POSSettings (**deploy butuh migrate**); Log masih ditulis + field `work_order`; adaptasi test | Test backend production hijau (kedua mode ambient invoice_type) + vitest + build |
| **2** | Input Good/Loss di Finish; Start/Finish dua-fase + daftar "Sedang Diproduksi"; Close/failure (Material Issue); jalur koreksi | Test kasus I.1–I.7 + E2E GUI di IAB |
| **3** | History beralih ke query WO (`posa_*`); Log freeze; hapus kode batch manual | Sapuan penuh + verifikasi visual |

Dampak test existing (±17 kasus `test_production.py`): happy-path berubah dari "SE independen" → "WO+SE"; test batch FG berubah (setting native); test ignore-client-items / FIFO raw / IDOR / whole-UOM / insufficient-stock **tetap** (pindah ke jalur service). Test frontend dialog: payload endpoint berubah, pola mock tetap.

---

## 8. Lampiran — referensi kode utama

- `apps/erpnext/erpnext/manufacturing/doctype/work_order/work_order.py`: :2664-2723 (make_stock_entry), :692-734 (status), :705-708 (Completed), :763-806 & :862-892 (agregat), :1227-1242 (validate_cancel), :2758 (stop_unstop), :2830 (close_work_order), :3116 (make_stock_return_entry), :483-484 (source_warehouse→required_items), :1542-1558 (actual dates).
- `apps/erpnext/erpnext/stock/doctype/stock_entry/stock_entry.py`: :847-880 (validate_fg_completed_qty → auto process_loss), :1770 (valuasi FG), :2198-2211 (overproduction), :605 (on_cancel→update_work_order), :1028 (Material Issue buang link WO), :3187-3195 & :3023-3087 (backflush mode), :908-915 (difference account).
- `apps/erpnext/erpnext/controllers/stock_controller.py`: :333-335 (perpetual gate), :768-826 (GL map), :307 + `stock/__init__.py:56-98` (akun warehouse).
- `apps/erpnext/erpnext/manufacturing/doctype/bom/bom.py`: :1542-1565 (validate_bom_no), :722-724 (with_operations).
- POSNext: `pos_next/api/production.py` (yang direvisi), `pos_next/pos_next/doctype/pos_production_recipe/` (+ child company → field baru `bom_no`), `pos_next/api/packages.py:46-63` (`_assert_profile_access`).

---

*Keputusan yang ditandai BISA DIBALIK (D2, D5) dapat diubah sebelum Fase 1 tanpa biaya skema; setelah Fase 3, D5 menjadi permanen (Log freeze).*
