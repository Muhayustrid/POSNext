# Role User POSNext — Panduan Lengkap

> Dua role persona yang dibundel aplikasi (`pos_next/fixtures/role.json`), permission dokumennya
> (`pos_next/fixtures/custom_docperm.json`), dan gate fungsional per fitur di kode.
> "Nexus POS Manager" adalah nama lama yang otomatis di-rename menjadi **POSNext Manager**
> oleh patch `v2_15_0/rename_nexus_pos_manager_role.py`.

## Ringkasan

| | **POSNext Cashier** | **POSNext Manager** |
|---|---|---|
| Untuk siapa | kasir outlet (SPA `/pos`) | kepala outlet / HO |
| Jualan | ya | ya |
| Batal dokumen submitted | **tidak** (hanya hapus draft) | ya (cancel + amend) |
| Purchasing (PO/PR) | hanya dengan role tambahan | ya, penuh |
| Fitur HO (backdate, laporan HQ, target) | tidak | ya |

## Resep pemasangan user

Pola dua-persona hasil audit security (23 Sep), dipakai sejak itu:

- **Kasir** = `POSNext Cashier` + `Stock User` + `POS Profile User` di tabel user
  POS Profile outletnya (child table `applicable_for_users`). Tanpa baris itu, user
  tidak bisa masuk SPA kasir outlet tersebut.
- **Manager** = `POSNext Manager` saja (sudah mencakup semua kecuali role backend
  khusus seperti System Manager).

## Permission dokumen (fixture Custom DocPerm)

Huruf: r=read, w=write, c=create, s=submit, a=cancel, m=amend, d=delete.
("select" = hak pilih nilai di field Link saja, tanpa read dokumen.)

| Doctype | POSNext Cashier | POSNext Manager |
|---|---|---|
| POS Invoice | rwcs + d | rwcsam |
| Sales Invoice | rwcs + d | rwcsam |
| POS Opening Entry | rwcm | — |
| POS Closing Entry | rwcs | rwcsam |
| Payment Entry | rwc | rwcsam |
| Customer | rwc | rwc |
| Item / Bin / Territory | r | r |
| Warehouse | r | r |
| POS Profile | r | rwc |
| Company | — | rw |
| Promotional Scheme | r | rwd |
| Purchase Order | — | rwcsam |
| Purchase Receipt | — | rwcsam |
| POS Production Recipe | r | — |
| Sales Invoice Item | — | r |
| Account | select | select |

Hal yang perlu dibaca teliti dari tabel ini:

- **Cashier bisa `delete` draft invoice tapi TIDAK bisa `cancel` invoice submitted**;
  Manager sebaliknya (`cancel`+`amend`, tanpa `delete`).
- **Account hanya select** — kasir/manajer bisa memilih akun di field Link
  (mis. akun kas di Payment Entry) tanpa bisa membuka daftar Chart of Accounts.
  Ini hasil fix audit 26 Sep (dulu kasir gagal checkout karena tak bisa select Account).
- Doctype milik pos_next sendiri (`POS Opening Shift` / `POS Closing Shift`) tidak
  ada di fixture ini — aksesnya lewat pemeriksaan kepemilikan shift di API
  (`api/shifts.py`), bukan DocPerm kasar.
- Role ERPNext lain yang menempel pada user (mis. `Stock User`) menambah permission
  native di luar tabel ini — tabel di atas hanya yang dibundel pos_next.

## Gate fungsional per fitur (di kode)

| Fitur | Siapa yang boleh | Referensi |
|---|---|---|
| SPA kasir `/pos` | user terdaftar di POS Profile outlet (POS Profile User) | `api/packages.py:_assert_profile_access` (pola sama di profil) |
| Tombol "ke Desk" dari SPA | POSNext Manager | `api/bootstrap.py:70` |
| Cetak struk / silent print (QZ) | Cashier, Manager, System Manager | `api/qz.py:29` |
| Perpanjang deadline shift schedule | System Manager, POSNext Manager | `shift_schedule.py:366` |
| Backdate entry (HO) | System Manager, POSNext Manager, Sales Manager, Accounts Manager **+** checkbox `Allow Change Posting Date` (POS Settings → Advanced Settings, per profil / global) | `api/backdate_invoices.py:28` |
| 5 laporan HQ + Sales Monitoring | System Manager, Accounts Manager, Sales Manager, POSNext Manager | `api/hq_monitoring.py:85` |
| PO / PR dari dialog Purchase | mengikuti DocPerm native Purchase Order / Purchase Receipt (Manager penuh; kasir perlu role purchasing); menu digate POS Settings | `api/purchase_orders.py:24` |
| Tutup shift / rekap | kasir mengelola shift miliknya; Manager lintas shift outlet | `api/shifts.py` |
| Receive intercompany (PR) | gate checkbox POS Settings (default OFF) | `api/purchase_receipts.py` |

## Catatan operasional

- Menambah role baru tidak perlu deploy — role & Custom DocPerm tersinkron via
  fixture saat `bench migrate` (hooks `fixtures`, `hooks.py:93`).
- Menghapus/di-rename role persona akan ditolak uninstall guard
  (`uninstall.py:90`).
- Password/akun uji di situs dev: lihat `docs/PROJECT_STATE.md`.
