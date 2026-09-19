# Plan — Purchase Order Proxy dari Halaman POS

Branch: `feat/pos-purchase-order` (base `main` @ 987aab5)

## Goal

Kasir membuat/mengelola **Purchase Order native ERPNext** tanpa keluar dari halaman POS.
POS hanya interface/proxy; `Purchase Order` ERPNext tetap single source of truth.
Tidak ada doctype baru, tidak ada sinkronisasi, tidak ada override ERPNext.

## Global Constraints (binding)

1. Semua tulis lewat Document API (`frappe.new_doc` / `frappe.get_doc` → `insert/save/submit/cancel`).
   Tidak ada `frappe.db.sql` untuk tulis. Tidak ada `ignore_permissions=True`.
2. Permission native: method backend melakukan explicit `frappe.has_permission("Purchase Order", perm, doc=name)`
   untuk pesan error jelas; framework tetap meng-enforce pada insert/save/submit/cancel.
3. Field permlevel-1 (`ignore_pricing_rule`) tidak pernah disentuh proxy.
4. Backend file baru: `pos_next/api/purchase_orders.py` (plural, ikut konvensi `invoices.py`/`shifts.py`).
   Test: `pos_next/api/test_purchase_orders.py` (FrappeTestCase).
5. Style backend: tab indent, line-length 110, double quotes, ruff F/E/W/I/UP/B/RUF.
   Header copyright seperti file api lain.
6. Frontend mengikuti pola existing: entry menu via `managementMenu.js` + prop filter seperti `requiresProduction`,
   dialog frappe-ui `Dialog` size lg pola `DraftInvoicesDialog.vue`, komponen diimpor eksplisit (TIDAK ada auto-register).
7. Error backend ERPNext ditampilkan apa adanya (parseError/serverErrorMessage) — dilarang diganti pesan generik.
8. Jangan menambah dependensi baru. Reuse: `AutocompleteSelect`, `StatusBadge`, `useToast`, `usePermissions`,
   `useFormatters`, `call` dari `@/utils/apiWrapper`.
9. Module path API **binding**: `pos_next.api.purchase_orders` (kontrak di bawah). Perubahan kontrak hanya oleh controller.

## API Contract (binding untuk Task 1 & Task 2)

RULING (controller, setelah review Task 1 — ini menggantikan teks kontrak awal bila berbeda):
- Permission check memakai helper `_check_permission(doctype, ptype, doc=None)` yang **raise** `frappe.PermissionError`
  (v16 `frappe.has_permission` hanya mengembalikan bool). Pada path dengan `name`: `_check_permission` dipanggil
  langsung setelah `frappe.get_doc`, SEBELUM validasi docstatus (jangan bocorkan eksistensi/docstatus ke user tanpa perm).
- List memakai `limit_page_length` (bukan `limit_page_limit` — bukan kwarg frappe).
- `remarks` di payload UI dipetakan ke field native PO **`terms`**; `_po_summary` mengembalikan `"remarks": doc.terms`.
- `get_purchase_item_details` men-seed ctx dengan company default currency + supplier default buying price list
  (tanpa itu `price_list_rate` tidak resolve di v16).
- Update path `save_purchase_order(name=...)` = **full payload**: field opsional yang di-omit di-reset ke default.
- Malformed JSON payload → `frappe.throw(_("Invalid request data"), frappe.ValidationError)`.

Semua method `@frappe.whitelist()`, awali guest-check:
`if frappe.session.user == "Guest": frappe.throw(_("Authentication required"), frappe.AuthenticationError)`
Argumen list/dict dikirim sebagai JSON string → parse dengan pola `json.loads(x) if isinstance(x, str) else x`.

1. `search_suppliers(search_term=None, limit=20)` → `{"suppliers": [{name, supplier_name, supplier_group}]}`
   `frappe.get_list("Supplier", filters={"disabled": 0}, or_filters=[["name","like",f"%{t}%"],["supplier_name","like",f"%{t}%"]], fields=[...], limit_page_limit=limit)`. (get_list enforces read perm.)

2. `search_purchase_items(search_term=None, limit=20)` → `{"items": [{item_code, item_name, stock_uom}]}`
   `frappe.get_list("Item", filters={"disabled": 0, "is_purchase_item": 1}, or_filters=[name/item_name like], fields=["name","item_name","stock_uom"], ...)`. item_code = name.

3. `get_supplier_details(supplier)` → subset `erpnext.accounts.party.get_party_details(party=supplier, party_type="Supplier", company=<resolve>, doctype="Purchase Order")`:
   `{"supplier_name", "currency", "buying_price_list", "taxes_and_charges"}` (nilai None tetap dikirim sebagai null).
   company resolve: dari kwargs `pos_profile` (POS Profile.company) bila ada.

4. `get_purchase_item_details(item_code, supplier=None, pos_profile=None, qty=1, transaction_date=None, warehouse=None)` →
   panggil `erpnext.stock.get_item_details.get_item_details` dengan ctx dict `{item_code, company, supplier, doctype: "Purchase Order", currency: None (biar default), transaction_date: transaction_date or nowdate(), qty, set_warehouse: warehouse}`.
   Return subset: `{item_code, item_name, uom, stock_uom, conversion_factor, price_list_rate, rate, warehouse}`.

5. `save_purchase_order(data, pos_profile=None, submit=0)`:
   data keys: `name?` (untuk update draft), `supplier` (wajib), `transaction_date?` (default today), `schedule_date?` (default besok), `company?` (fallback pos_profile → user default), `currency?`, `conversion_rate?`, `set_warehouse?` (fallback pos_profile warehouse), `taxes_and_charges?`, `remarks?`, `items` (wajib non-empty): `[{item_code, qty, rate?, uom?, warehouse?, schedule_date?}]`.
   - Update path (`name` ada): `frappe.get_doc("Purchase Order", name)`; jika `docstatus != 0` → `frappe.throw(_("Only a Draft Purchase Order can be edited"))`; explicit `frappe.has_permission("Purchase Order", "write", doc=name)`.
   - Create path: explicit `frappe.has_permission("Purchase Order", "create")`; `frappe.new_doc`.
   - Set field parent (jangan sentuh `ignore_pricing_rule`); `doc.set("items", [])` lalu append rows per payload (schedule_date per-row fallback ke parent schedule_date; warehouse per-row fallback `set_warehouse`).
   - **WAJIB**: `doc.set_missing_values()` SEBELUM `insert()`/`save()` (expand template pajak + item details — `for_validate=True` di dalam validate TIDAK expand taxes).
   - `doc.insert()` / `doc.save()`; jika `cint(submit)`: `doc.submit()`.
   - Return `_po_summary(doc)` (dipakai semua method mutasi): `{name, docstatus, status, supplier, supplier_name, transaction_date, schedule_date, company, currency, net_total, total_taxes_and_charges, grand_total, remarks, items: [{name, item_code, item_name, qty, uom, rate, amount, warehouse, schedule_date}]}`.

6. `get_purchase_order(name)` → explicit read perm; return `_po_summary(frappe.get_doc(...))`.

7. `get_purchase_orders(pos_profile=None, status=None, search_term=None, limit=50)` →
   `frappe.get_list("Purchase Order", fields=["name","supplier","supplier_name","transaction_date","schedule_date","grand_total","currency","status","docstatus","per_received","per_billed","company","modified"], order_by="modified desc", limit_page_limit=limit)`; filter company=<pos_profile company> bila pos_profile diberikan; status filter exact; search_term → or_filters name/supplier/supplier_name like. Return `{"orders": [...]}`.

8. `submit_purchase_order(name)` → read doc; `docstatus != 0` → throw `_("Only a Draft Purchase Order can be submitted")`; explicit submit perm; `doc.submit()`; return summary.

9. `cancel_purchase_order(name)` → doc; `docstatus != 1` → throw `_("Only a submitted Purchase Order can be cancelled")`; explicit cancel perm; `doc.cancel()`; return summary.

## Task 1 — Backend (implementer: Backend Engineer)

Buat `pos_next/api/purchase_orders.py` + `pos_next/api/test_purchase_orders.py` sesuai kontrak & Global Constraints.
Fixtures test dibuat sendiri (Supplier + Item stok unik prefix, cleanup tearDown), company dari site.
Ikuti pola test existing (lihat `pos_next/api/test_pos_invoice_submit.py` / `test_printing.py` untuk gaya & pola set_user bila ada).
Test minimal: create draft (rate eksplisit & tanpa rate), submit via flag, submit draft existing, update draft,
edit non-draft throw, cancel, validasi (supplier kosong, items kosong, schedule_date < transaction_date),
permission denied via `frappe.set_user` user tanpa role purchasing (restore Administrator di finally),
list + search menemukan PO, get_supplier_details & get_purchase_item_details bentuk benar.
Jalankan: `./env/bin/python apps/pos_next/pos_next/_pn_run_tests.py pos_next.api.test_purchase_orders` dari bench root (serial only).
Commit pesan: `feat(pos): purchase order proxy API for the POS cashier`.

## Task 2 — Frontend (implementer: Frontend Engineer)

Depends on: Task 1 (module path + kontrak di atas; mock API di test).

File baru `POS/src/components/purchase/PurchaseOrderDialog.vue` + `PurchaseOrderDialog.test.js`.
Dimodifikasi: `POS/src/components/pos/managementMenu.js` (entry `purchase-order`, icon `truck`,
label "Purchase Order", `requiresPurchaseOrder: true`), `ManagementSlider.vue` + `ManagementDrawer.vue`
(prop `showPurchaseOrder`, filter `visibleItems`), `POSHeader.vue` (forward prop), `POS/src/pages/POSSale.vue`
(`usePermissionCheck("Purchase Order", "create")` → `:show-purchase-order`, handler menu buka dialog,
mount `<PurchaseOrderDialog v-model="showPurchaseOrderDialog" :pos-profile="shiftStore.profileName"
:company="shiftStore.profileCompany" :warehouse="shiftStore.profileWarehouse" :currency="shiftStore.profileCurrency" />`
— persis pola ProductionDialog).

Dialog (frappe-ui Dialog lg, pola DraftInvoicesDialog, dua view internal):
- List view: search (debounce ~300ms) + chips status (All/Draft/To Receive and Bill/To Receive/To Bill/Completed/Cancelled),
  rows: name, supplier_name, tanggal (formatDate), grand_total (formatCurrency + currency field), status (StatusBadge),
  aksi: Edit (docstatus 0), Submit (docstatus 0, tampil hanya bila `usePermissionCheck("Purchase Order","submit")`),
  Cancel (docstatus 1, bila cancel perm), Open di ERPNext (`window.open('/app/purchase-order/'+name)`).
- Form view: Supplier (AutocompleteSelect + search_suppliers debounce), tanggal (`<input type="date">` transaction_date=today,
  schedule_date=tomorrow), caption Company & Warehouse dari props, taxes: otomatis pakai default `taxes_and_charges`
  dari get_supplier_details bila ada (tampil info + tombol hapus), tabel item (+ Add via AutocompleteSelect →
  get_purchase_item_details → row qty=1, rate=price_list_rate||rate||0; kolom editable Qty & Rate input number,
  Amount = qty*rate lokal untuk display, tombol hapus baris), remarks textarea.
- Footer: tutup / Save Draft / Save & Submit (perm-gated). Sukses → toast nama PO + kembali ke list refresh.
  Gagal → `showError(parseError(error).message)` — pesan server asli.
Test vitest ikut resep mock (frappe-ui stub h/render-function, mock `@/utils/apiWrapper`, global `__`).
Tambah string baru ke `pos_next/translations/id.csv`.
Jalankan `npx vitest run src/components/purchase/PurchaseOrderDialog.test.js` dan pastikan suite lain tetap hijau (`npm run test:run`).
Commit: `feat(pos): purchase order dialog on the cashier management menu`.

## Out of scope (YAGNI)

Amend PO, Payment Schedule, PR/Pi dari POS, offline caching PO, reports PO custom,
supplier create dari POS, taxes editor penuh (cukup default supplier + hapus).
