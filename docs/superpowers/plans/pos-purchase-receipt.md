# Plan — Purchase Receipt dari POS Kasir (proxy ke native ERPNext)

Branch: `feat/pos-purchase-receipt` (dari `main` setelah merge PO `b8013e6`)

## Goal

Kasir **menerima barang** terhadap PO yang sudah disubmit, tanpa keluar dari POS.
Hasilnya **`Purchase Receipt` native ERPNext**: stok masuk gudang (Stock Ledger),
`per_received`/status PO ter-update native, siap dilanjutkan Purchase Invoice di Desk.
Pattern identik fitur PO: POS hanya proxy, ERPNext single source of truth,
tanpa doctype baru, tanpa hooks baru, tanpa `ignore_permissions`.

## Arsitektur inti — reuse mapper ERPNext sepenuhnya

Draft PR TIDAK dibangun manual: backend memanggil
`erpnext.buying.doctype.purchase_order.purchase_order.make_purchase_receipt(po_name)`
(mapper resmi ERPNext, purchase_order.py:760) yang sudah mengisi rate, UOM,
conversion factor, **sisa qty (pending)**, warehouse, dan link `purchase_order` +
`purchase_order_item` per baris. Kita tidak menduplikasi logika pembuatan PR.

```
PO submitted (per_received < 100%)
  → POS: tombol "Receive" pada baris PO
  → get_purchase_receipt_draft(po_name)  [mapper ERPNext → bentuk ringkas]
  → kasir edit qty per baris (default = sisa)
  → save_purchase_receipt(data, submit)  [Document API: insert/save/submit]
  → Purchase Receipt native → Stock Ledger + status PO ter-update
```

## API Contract — pos_next/api/purchase_receipts.py (baru)

Gaya = purchase_orders.py (guest check, raising `_check_permission`, _parse JSON,
`_pr_summary` helper). Kontrak:

1. `get_purchase_receipt_draft(po_name)` → read perm PO + create perm PR;
   panggil mapper sebagai fungsi Python; return `{supplier, supplier_name,
   posting_date, company, currency, set_warehouse, items: [{item_code, item_name,
   purchase_order, purchase_order_item, ordered_qty, received_qty, pending_qty,
   uom, rate, warehouse}]}` — TANPA insert.
2. `save_purchase_receipt(data, submit=0)` → full-payload (konvensi PO):
   `name?` (update draft, docstatus==0), supplier, posting_date, company,
   set_warehouse?, items `[{item_code, qty, rate, uom, warehouse,
   purchase_order, purchase_order_item}]`; `doc.set_missing_values()` sebelum
   insert/save; insert()/save() → (+submit). Return `_pr_summary(doc)`:
   `{name, docstatus, status, supplier, supplier_name, posting_date, company,
   currency, net_total, grand_total, per_billed, items:[…]}`.
3. `get_purchase_receipts(pos_profile=None, status=None, search_term=None, limit=50)`
   → get_list (fields: name, supplier, supplier_name, posting_date, status,
   docstatus, grand_total, currency, per_billed, company, modified), filter
   company dari pos_profile, order modified desc.
4. `cancel_purchase_receipt(name)` → docstatus==1, cancel perm, doc.cancel().

Permission native per aksi (create/read/write/submit/cancel Purchase Receipt);
error ERPNext diteruskan apa adanya ke toast POS.

## Frontend — extend PurchaseOrderDialog.vue (view ketiga: "receive")

Tidak ada menu/wiring baru (dialog PO sudah terpasang). Perubahan:

- List PO: baris `docstatus==1 && per_received < 100` dapat tombol **Receive**
  (render hanya bila `usePermissionCheck("Purchase Receipt", "create")`).
- View receive: header (supplier + tanggal posting, `<input type="date">`),
  tabel: Item | Ordered | Received | **Sisa** | input Qty (default sisa) | Rate (readonly),
  tombol: kembali / Save Draft / **Submit Receipt** (perm submit PR).
- List view: segmen/tab kecil **"Receipts"** — PR terbaru (status badge, per_billed),
  aksi: Cancel (docstatus 1 + cancel perm), Open di Desk (`/app/purchase-receipt/<name>`).
- Semua string via `__()`; tambah entries `pos_next/translations/id.csv`.
- State lokal komponen; tidak ada store baru; tidak ada dependensi baru.

## File

Baru: `pos_next/api/purchase_receipts.py`, `pos_next/api/test_purchase_receipts.py`.
Modif: `POS/src/components/purchase/PurchaseOrderDialog.vue` (+ `.test.js`),
`pos_next/translations/id.csv`.

## Eksekusi (SDD, seperti fitur PO)

1. Backend Engineer (subagent) → implement + test → Reviewer → fix round.
2. Frontend Engineer (subagent) → extend dialog + test → Reviewer → fix round.
3. Integrasi (controller): suite backend (container, serial), suite frontend, build,
   migrate bila perlu, final review seluruh branch.

## Test

Backend (test_purchase_receipts.py, fixtures mandiri + cleanup):
draft-shape dari mapper (pending qty benar setelah partial receive), create draft,
submit → SLE tercipta + `PO.per_received` naik + status berubah
(To Receive and Bill → To Bill/Completed), partial receive dua tahap → Completed,
update draft, cancel PR (stok terbalik, PO status dikembalikan), permission denied
(user tanpa role, incl. cancel), validasi (items kosong, PO non-submitted → error
mapper/ERPNext asli).

Frontend: tombol Receive perm-gated & hanya pada PO eligible, prefill qty = sisa,
payload `save_purchase_receipt` benar (link PO per baris), error backend
ditampilkan apa adanya.

## Acceptance Criteria

1. PO submitted → POS Receive → **PR native docstatus 1**, terlihat di
   Desk (Stock → Purchase Receipt) tanpa perbedaan struktur.
2. Stok gudang bertambah; PO per_received/status ter-update native.
3. PR lanjut ke Purchase Invoice di Desk normal (downstream utuh).
4. Tanpa permission PR → tombol hidden + backend menolak.
5. Validasi ERPNext jalan (mis. item batch/serial → error jelas; tolerance
   over-receive mengikuti Stock Settings).
6. Partial receive berulang sampai tuntas → PO Completed.

## Konteks bisnis (konfirmasi pemilik app)

Alur group: **outlet** membuat PO ke **pabrik** (supplier afiliasi, harga berisi
margin — transaksi nyata antar company, BUKAN jalur internal-transfer nol-margin
ERPNext); pabrik memproses SO + DN + SI di sisi Desk. POS hanya mewakili sisi
outlet: beli (PO) + terima (PR). Setup master: Supplier pabrik
`is_internal_supplier=1`, `represents_company=<company pabrik>` (≠ company outlet
→ `is_internal_transfer()` false → harga nyata). Isolasi kasir per company outlet
via **User Permission "Company"** (native, tanpa kode): list dialog otomatis
ter-scoped dan akses per-dokumen lintas company ditolak PermissionError.

## Out of scope (YAGNI)

Purchase Invoice dari POS, picker batch/serial (error native ditampilkan),
rejected qty/LCV, Purchase Return, edit warehouse per-baris di UI
(default dari PO/POS Settings), amend PR.

## Risiko

- **Item batch/serial-tracked**: submit PR butuh Serial/Batch Bundle — di luar
  scope; error ERPNext asli ditampilkan (keterbatasan tercatat; solusi: terima
  via Desk untuk item tersebut).
- Over/under-receive tolerance (Stock Settings) — perilaku native diikuti.
- `base_net_total`/`base_rate` (reqd) terisi `calculate_taxes_and_totals` saat
  insert via Document API — mapper + set_missing_values menutupnya.
- Role: ERPNext default memberi `Stock User` create PR — kemungkinan kasir sudah
  bisa tanpa role tambahan; tetap 100% permission native (tidak di-bypass).
