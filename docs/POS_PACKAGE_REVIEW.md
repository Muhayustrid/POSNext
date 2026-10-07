# Review — Alokasi Harga Paket POS (`feature/pos-package`)

Scope: commit `a6a7ca1..0c67f83` (4 commit, 24 file, +3143/-74) +
`docs/POS_PACKAGE_MANUAL_TEST.md`.

Status test saat review:
- Backend `pos_next.api.test_package_allocation_gate` — 21/21 OK.
- Frontend vitest (packageAllocation, packageQuote, printInvoice, POSSale,
  useInvoice) — 114/114 OK.

Test hijau **tidak** menangkap temuan B1 dan B2 di bawah. Keduanya harus
diperbaiki sebelum toggle dinyalakan di produksi.

Urutan prioritas: B = blocker, H = high, M = medium, L = low.

---

## B1 — Paket dengan komponen qty > 1 sebagai baris terakhir tidak bisa dijual (toggle ON)

**File:** `pos_next/api/packages.py:allocate_package_rates` (mirror:
`POS/src/utils/packageAllocation.js:allocatePackageRates`)

Sisa pembulatan dibebankan ke baris terakhir sebagai **rate per unit**:
`rate = (total_money - allocated) / line_qty`, lalu dibulatkan. Kalau
`line_qty > 1` dan sisanya tidak habis dibagi `line_qty`, invariant
`Σ(rate × qty) == harga_paket` **gagal**. Akibatnya, `validate_invoice_packages`
melempar error "cannot be split exactly at this site's currency precision".
Paket yang valid jadi sama sekali tidak bisa dijual.

Reproduksi (dijalankan pada fungsi asli, site `posnext.localhost`, presisi 0):

```
allocate_package_rates(25000, [Roti qty1 @20000, Teh qty2 @5000])
  -> [16667, 4166]   Σ = 24999   ❌ (harus 25000)
allocate_package_rates(25000, [Teh qty2 @5000, Roti qty1 @20000])
  -> [4167, 16666]   Σ = 25000   ✅ (urutan baris menentukan lolos/gagal)
```

Docstring ("so that sum(...) == package_price") dan dokumen manual test
mengklaim invariant ini selalu berlaku. Itu tidak benar. Test case JSON
`invariant: qty>1 line weights at package qty 5` hanya lolos karena angkanya
habis dibagi.

Jalur yang terdampak:
- checkout online,
- sync offline: `_verify_package_allocation_totals` menolak dengan pesan
  "component rates total ...",
- requote saat edit invoice offline.

**Arah perbaikan (pilih yang paling sederhana):**
1. Bebankan sisa ke baris dengan `line_qty == 1`. Pilih baris terakhir yang
   memenuhi, jangan asal baris terakhir. Kalau semua baris punya
   `line_qty > 1` dan sisa tidak habis dibagi, tolak **saat POS Package
   disimpan** (validate doctype) dengan pesan yang jelas. Jangan tolak saat
   kasir checkout.
2. Perbarui py + js + `packageAllocation.cases.json` bersamaan. Tambahkan
   kasus `[qty1 @20000, qty2 @5000] @25000` dan kasus "semua baris qty ≥ 2".

---

## B2 — Laporan EOD / Sales Recap / HQ Monitoring kehilangan pendapatan paket (toggle ON)

**File:**
- `pos_next/services/sales_recap.py:592, 701, 745`
- `pos_next/api/hq_monitoring.py:1024`

Semua query agregasi item/kategori membuang baris
`pos_package_role = 'Package Item'`. Tujuannya mencegah double count saat
uang ada di parent (mode lama). Di mode alokasi, uang ada di komponen dan
parent bernilai 0. Akibatnya:
- ranking produk dan kategori di EOD/recap/HQ **kehilangan seluruh omzet
  paket**,
- parent tetap muncul dengan qty tetapi amount 0.

Total header (`base_grand_total`) tetap benar, jadi selisihnya diam-diam:
breakdown per item/kategori tidak lagi sama dengan total. Tujuan fitur ini
("item-level sales reporting sees the revenue") justru gagal di laporan milik
app sendiri. Hanya laporan ERPNext bawaan yang benar.

**Arah perbaikan:** jangan filter berdasarkan role. Filter baris paket yang
tidak membawa uang, yaitu sisi bernilai nol dari setiap grup. Contoh:
`ifnull(sii.pos_package_role,'') = '' OR sii.base_net_amount <> 0`. Satu
kondisi ini bekerja untuk kedua mode, dan invoice lama tetap benar. Perbarui
juga docstring `hq_monitoring.py:22`. Tambahkan satu test: invoice alokasi
masuk ke `_aggregate_items` dengan amount di komponen.

---

## H1 — Dokumen dan kode bertentangan soal toggle OFF + invoice ber-marker

**File:** `packages.py:validate_invoice_packages`
(`allocate = marker AND toggle`) vs `POS_PACKAGE_MANUAL_TEST.md` (d)4.

Dokumen menyatakan: "Invoice yang sudah tersimpan dalam mode alokasi **tetap**
dialokasi saat dibuka/di-save ulang (marker di snapshot menang)". Kode
melakukan kebalikannya. Saat toggle OFF, draft atau antrean offline yang
membawa marker di-reprice ke bentuk lama.

Perilaku kode masih masuk akal karena server adalah otoritas. Tetapi salah
satu harus diputuskan dan yang lain diselaraskan. Jika perilaku kode yang
dipertahankan, tulis ulang (d)4. Retur sudah benar (memakai rate invoice asal
apa pun toggle-nya).

## H2 — Retur: rate komponen dirata-rata per item_code lalu dibulatkan

**File:** `packages.py:_validate_return_packages` (blok `original_money /
original_qty`)

Penjualan sengaja menyimpan rate berbeda per baris untuk item yang sama
(`_allocated_component_rates`: "collapsing them into one merged rate could not
satisfy the exact-sum invariant"). Retur justru meratakannya. Contoh: dua
baris Teh dengan rate 3333 dan 3334 → rata-rata 3333,5 → dibulatkan 3334 →
refund 6668, padahal bayar 6667. **Over-refund.**

Perbaikan: kembalikan rate **per baris asal** lewat link baris
(`sales_invoice_item` / `pos_invoice_item`). Link ini sudah dipulihkan oleh
`_restore_return_package_metadata`. Tambahkan test paket dengan item
komponen duplikat + retur penuh, lalu assert `grand_total` retur ==
`-grand_total` asal.

## H3 — Komponen sekarang punya `price_list_rate > 0`: risiko Pricing Rule / Item Tax menyentuh komponen

Di mode lama komponen bernilai 0, jadi tidak ada yang bisa didiskon atau
dipajaki. Sekarang ada dua hal yang **belum diuji**:
- **Pricing Rule level item** (mis. diskon 10% untuk Roti). ERPNext dapat
  mengisi `pricing_rules` pada baris komponen saat validate. Setelah itu
  `apply_min_max_price_discounts` (hook yang berjalan **setelah**
  `validate_invoice_packages`) bisa mendiskon komponen dan merusak Σ. Perlu
  dibuktikan dengan test atau E2E. Jika memang bocor, nolkan
  `pricing_rules` pada baris paket di `validate_invoice_packages`.
- **Item Tax Template**: pajak kini mengikuti template komponen, bukan
  template parent. Kalau template parent dan komponen berbeda, total pajak
  berubah hanya karena toggle. Minimal tambahkan ke manual test dan catat
  sebagai perubahan semantik.

---

## M1 — Manual test berisi ekspektasi yang salah

`docs/POS_PACKAGE_MANUAL_TEST.md`:
- **(a)6**: "pendapatan 20.000 di Roti dan 10.000 di Teh" bertentangan dengan
  (a)4. Yang benar: **15.333 dan 7.667**.
- **(a)1**: "Tunggu ±10 detik (cache frontend)". Saya tidak menemukan TTL 10
  detik di `posSettings.js`; setting dibaca saat bootstrap/`loadSettings`.
  Tulis saja "reload POS".
- **(d)4**: lihat H1.
- **(b)2**: "Teh -0.5". UOM Nos biasanya *must be whole number*, sehingga
  ERPNext menolak dengan pesan lain sebelum guard paket berjalan. Uji dengan
  paket berkomponen qty 2 dan retur qty 1.
- **Persiapan kurang**: tidak ada langkah `bench migrate`. Field toggle ada di
  DocType JSON. Tanpa migrate, `_package_allocation_enabled()` diam-diam
  mengembalikan False, sehingga toggle tidak muncul atau tidak berefek.
  **Migrate kedua site** (device IP jatuh ke `posnext.localhost`, bukan hanya
  site uji).

Skenario yang **wajib ditambahkan**:
1. Paket dengan komponen qty 2 sebagai baris terakhir (memunculkan B1).
2. EOD report + Sales Recap + HQ Monitoring setelah jual paket (memunculkan B2).
3. Paket dengan item komponen duplikat + retur penuh (memunculkan H2).
4. Pricing Rule aktif pada item komponen, dan Item Tax Template berbeda
   antara parent dan komponen (H3).
5. Diskon header (manual/kupon) pada cart yang berisi paket: Σ komponen
   setelah diskon == grand_total.
6. Toggle diubah saat ada invoice di antrean offline, lalu sync.

## M2 — `_package_allocation_enabled()` = `get_meta` + query DB tanpa cache, dipanggil berulang

Fungsi ini dipanggil per instance paket di `validate_invoice_packages`, di
`_verify_package_allocation_totals`, di `quote()` default, di bootstrap, dan
di `get_pos_settings`. Satu invoice berisi N paket menghasilkan N+1 query
untuk single yang sama. Baca sekali per request (hitung sekali di awal
`validate_invoice_packages`, lalu teruskan). `cache=False` hanya diperlukan
untuk test, yang bisa memanggil `frappe.clear_cache`.

## M3 — Bobot alokasi memakai harga hari ini, bukan `posting_date`

`_component_price_list_rates` → `_fetch_uom_prices_map(..., transaction_date=None)`.
Akibatnya invoice backdate (fitur backdate entry ada di app ini) atau sync
offline yang tertunda melewati perubahan Item Price akan dibagi dengan harga
hari sync. Totalnya tetap benar, hanya split per item yang bergeser.
Teruskan `doc.posting_date`. Cukup satu argumen.

---

## L1 — Kemungkinan dead code: retur lintas doctype

`_original_child_doctype` dan fallback `other_field` di
`_restore_return_package_metadata` menangani retur POS Invoice terhadap Sales
Invoice (atau sebaliknya). Padahal `return_against` adalah Link ke doctype
**yang sama** (POS Invoice → POS Invoice, Sales Invoice → Sales Invoice), dan
`ReturnInvoiceDialog` membuat retur di doctype asal. Kalau skenario ini tidak
bisa terjadi, hapus. Jika bisa, buktikan dengan test.

## L2 — `_verify_package_allocation_totals` menduplikasi cek Σ

`validate_invoice_packages` sudah me-reprice dari snapshot dan memeriksa Σ.
Pre-check di `invoices.py` hanya membuat payload yang dimanipulasi ditolak
lebih awal dengan pesan lain. Fungsi ini boleh dipertahankan sebagai
defense-in-depth. Jika dipertahankan, pastikan perbaikan B1 juga membuat
offline preview selalu lolos cek ini. Saat ini preview offline untuk kasus B1
gagal sync.

## L3 — Precision mirror frontend

`packageQuote.js` memakai `getPrecision().currency`. Server memakai
`get_precision("Sales Invoice Item","rate")`. Biasanya keduanya sama. Ini
hanya mempengaruhi preview karena server me-requote, jadi cukup diberi
komentar. Tidak perlu diubah.

---

## Yang sudah baik (pertahankan)

- Toggle default OFF dan snapshot tanpa marker selalu memakai jalur lama.
  Kompatibilitas mundur terjaga.
- Server selalu me-requote dari snapshot; rate dari klien tidak pernah
  mengikat.
- Fail-closed untuk pajak "On Item Quantity" (dengan test).
- Baris paket dikeluarkan dari engine offer **dengan dilaporkan** ke kasir
  (`package_rows_excluded`), bukan di-skip diam-diam.
- `ReturnInvoiceDialog` kini mengirim link baris sesuai doctype
  (`pos_invoice_item`). Ini bug nyata pada mode POS Invoice yang ikut
  diperbaiki.
- Edit invoice offline gagal tertutup (fail closed) bila definisi paket
  berubah.

## Checklist untuk agent

- [x] B1: perbaiki carrier sisa pembulatan (py + js + cases.json), validasi di POS Package save
- [x] B2: filter laporan berbasis amount, bukan role (sales_recap ×3, hq_monitoring ×1) + test
- [x] H1: selaraskan dokumen (d)4 dengan kode
- [x] H2: retur memakai rate per baris asal + test duplikat item
- [x] H3: test Pricing Rule pada komponen; dokumentasikan perubahan Item Tax
- [x] M1: koreksi manual test + tambah 6 skenario
- [x] M2/M3: satu kali baca toggle per validate; teruskan posting_date
- [x] L1: hapus kode lintas doctype atau buktikan dengan test
