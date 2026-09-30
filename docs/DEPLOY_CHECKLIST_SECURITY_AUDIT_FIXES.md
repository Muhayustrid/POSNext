# Checklist Deploy — Branch `security-audit-fixes` (grup remediasi audit 1-6)

> **BERSEJARAH**: branch ini sudah ter-merge ke `main`. Untuk deploy kondisi
> `main` sekarang (v2.13.0 + fix batch audit), pakai `docs/DEPLOY_CHECKLIST.md`.

Tanggal disusun: 25 Sep 2026. Berlaku untuk deploy ke Frappe Cloud (production).
Semua langkah di bawah sudah diverifikasi di situs dev `posnext.localhost` +
`roti-posnext-test.localhost` pada state final branch.

## 0. Verdict keamanan (ringkas)

| Aspek | Status | Bukti |
|---|---|---|
| Patch migrate | AMAN, idempoten | migrate 2× berturut di dev = SUCCESS, run kedua no-op |
| Patch index (grup 2) | AMAN | guard `information_schema`; DDL saja, tanpa sentuh data |
| Jalur destruktif install | AMAN | `reclaim_pos_settings_doctype` default SKIP; hanya jalan bila `allow_settings_reclaim=1` di site config (JANGAN set di production) |
| Import modul | AMAN | `pos_next.hooks`, `uninstall`, `install` ter-import bersih |
| Handler realtime | AMAN | `realtime/handlers.js` CommonJS valid (`node --check` OK) |
| Situs tanpa loyalty | AMAN | `process_loyalty_to_wallet` punya 5 early-return sebelum logika throw (flag off / customer tanpa program = tidak tersentuh) |
| Frontend | AMAN | vitest 552/552, build OK, `/pos` 200, `/sw.js` 200 text/javascript |
| Endpoint gate | AMAN | guest dapat 403 elegan (bukan 500) |

Patch yang akan berjalan (baris baru vs versi production lama):
`v2_11_0.copy_global_settings_to_single`, `v2_11_0.reorder_pos_settings_fields`,
`v2_12_0.add_payment_entry_reference_index` — ditambah seluruh patch main yang
belum pernah jalan di site production (migrate menjalankan semuanya berurutan).

## 1. Pra-deploy (WAJIB)

1. **Backup penuh** situs production (database + files) via Frappe Cloud snapshot.
2. **Cek data legacy COR-BE-01** (akurasi uang tutup shift; dev: 0 baris).
   Jalankan di console/DB production:
   ```sql
   SELECT name FROM `tabPayment Entry`
   WHERE docstatus = 1 AND payment_type = 'Receive'
     AND reference_no IN (SELECT name FROM `tabPOS Opening Shift`)
     AND NOT EXISTS (
       SELECT 1 FROM `tabPayment Entry Reference` per
       WHERE per.parent = `tabPayment Entry`.name
     );
   ```
   - Hasil 0 baris → bersih, lanjut.
   - Hasil > 0 → ada PE lama ber-stamp nama shift tanpa reference rows; PE itu
     berhenti terhitung di expected cash tutup shift. Laporkan sebelum lanjut
     (perlu keputusan data, bukan blocker teknis).
3. **Laporan dampak data (audit A7, READ-ONLY)** — ukur dua kondisi data
   produksi sebelum deploy, TANPA auto-fix (hasilnya hanya untuk keputusan
   dan laporan; perbaikan data adalah keputusan terpisah):
   - (a) Invoice submitted (POS Invoice + Sales Invoice `is_pos=1`) dengan
     baris item `rate < price_list_rate` tanpa `pos_offer_item_rules`
     → indikasi diskon tak terjelaskan.
   - (b) Wallet Transaction `docstatus=1` yang mereferensi invoice
     cancelled (`docstatus=2`) atau invoice yang sudah tidak ada.
   - Script tersimpan di repo: `pos_next/audit.py` (fungsi `run`; isi
     lengkapnya di Lampiran §7). Jalankan dari direktori bench di server production:
     ```bash
     bench --site <site-produksi> execute pos_next.audit.run
     ```
     atau via console: `bench --site <site> console` lalu
     `from pos_next.audit import run; run()`.
   - Script hanya membaca (satu query UNION ALL + loop `get_value`); bila
     kolom `pos_offer_item_rules` belum ada (sebelum `bench migrate`),
     pemeriksaan (a) dilaporkan DILEWATI, bukan error.
   - **Hasil pembanding situs test** (`posnext.localhost`, 2026-09-27):
     (a) 1 baris item di 1 invoice, total diskon 1800.00 — SINV-OT2601,
     item CR001, rate 16200 vs price_list_rate 18000 (Sales Invoice,
     bukan return); (b) 17 dari 22 Wallet Transaction dicek bermasalah
     (17 invoice cancelled, 0 missing).
   - Bila angka produksi > 0: catat angkanya di laporan deploy dan
     eskalasi ke keputusan data (jangan block deploy otomatis, jangan
     perbaiki dari script ini).
4. **Kalau ada outlet non-IDR**: sadari bahwa UI dan struk kini menampilkan
   2 desimal untuk mata uang non-IDR (IDR tetap 0). Ini perbaikan yang disengaja,
   tetapi tampilan berubah.
5. **Pastikan site config production TIDAK punya** `allow_settings_reclaim: 1`
   (tidak akan ada secara default; cukup tidak menambahkannya).

## 2. Langkah deploy (urut)

1. Deploy branch (`security-audit-fixes` setelah di-push/merge sesuai kebijakan).
2. **Build frontend** (CI Frappe Cloud TIDAK membuild):
   ```bash
   npm --prefix POS install        # bila node_modules belum ada
   npm --prefix POS run build      # menghasilkan ../pos_next/public/pos/*
   ```
   Pastikan hasil build ikut ter-deploy (artifact `public/pos/` + `www/pos.html`).
3. `bench migrate` — menjalankan patch + sinkron doctype + fixture.
   Catatan: pembuatan index di `tabPayment Entry Reference` pada tabel besar
   bisa makan beberapa menit (normal, tidak memblokir transaksi berjalan).
4. Restart services (Frappe Cloud melakukannya otomatis pasca-migrate;
   pastikan realtime/socket service ikut naik — handler baru dimuat di situ).

## 3. Verifikasi pasca-deploy (smoke)

1. Buka `/pos` → 200, kasir bisa login dan boot profil (get_pos_settings lolos
   untuk anggota profil).
2. `curl -s https://<site>/sw.js` → 200 `text/javascript` (service worker controller).
3. Guest memanggil endpoint gated (mis. `POST /api/method/pos_next.api.promotions.get_referral_codes`)
   → 403, BUKAN 500.
4. Buka shift → jual 1 item kecil → tutup shift: angka expected cash cocok
   dengan pembayaran (validasi COR-BE-01 di dunia nyata).
5. Cetak satu struk dari perangkat iMin → keluar satu kali (validasi jalur cetak).
6. Cek `bench --site <site> show-config` tidak ada flag reclaim.

## 4. Perilaku baru yang disengaja (perlu diketahui operasional)

- **Konversi loyalty gagal kini menggagalkan checkout** (dulu senyap; kasir
  melihat error dan bisa retry). Hanya berlaku bila loyalty-to-wallet AKTIF.
- **Cek kode diskon dibatasi 20 percobaan / 5 menit per IP** (anti tebakan).
- **Return tanpa `return_against` ditolak** (UI resmi selalu mengirimnya).
- **Konflik draft dua perangkat ditolak 409** ("updated in another session").
- **GET tutup shift murni baca**; printed draft di-submit saat close, dan
  preview menampilkan `pending_printed_drafts` (jumlah menunggu).
- **Toggle per-profil blokir-stok 0 kini berfungsi** (dulu terpaksa aktif).
- **Kupon one_use** di-recek saat submit (race tertutup).
- **Angka non-IDR 2 desimal** (UI + struk).
- **Rate limit / gate endpoint**: integrasi pihak ketiga lama yang memanggil
  endpoint tanpa permission akan mendapat 403 (perlu penyesuaian integrasi).
- **Offline (perangkat kasir)**: tabel `payment_queue` yang mati dihapus saat
  upgrade Dexie (tidak pernah dibaca; tanpa kehilangan data). SW pindah ke
  `/sw.js` scope `/` — lama di-sweep otomatis.

## 5. Rollback

- Apps: kembali ke commit sebelumnya + `bench migrate` (patch index tidak
  perlu di-drop; kolom/flag tambahan tidak mengganggu versi lama).
- Frontend: kembalikan bundle `public/pos/` versi lama (SW baru akan
  di-update otomatis oleh skipWaiting + cleanupOutdatedCaches).
- Bila perlu drop index (tidak wajib):
  `ALTER TABLE \`tabPayment Entry Reference\` DROP INDEX payment_entry_reference_name_doctype_idx;`

## 6. Catatan

- Flag dev yang TIDAK ikut ter-deploy (site config, bukan repo):
  `allow_settings_reclaim` (posnext.localhost), `server_script_enabled`
  (common_site dev). Production tidak terpengaruh.
- Utang yang TIDAK ikut deploy ini (backlog): re-sync penuh terjemahan id.csv,
  gate `get_wallet_info`, tampilan `pending_printed_drafts` di dialog SPA,
  PERF-07/09..21 (14 temuan performa sedang/rendah), CLN §7 (opsional).

## 7. Lampiran: script laporan dampak data (audit A7, READ-ONLY)

Isi lengkap `pos_next/audit.py` — salin ke `<bench>/apps/pos_next/pos_next/audit.py`
bila file belum ikut ter-deploy, lalu jalankan seperti langkah 3 di §1.
Tidak ada INSERT/UPDATE/DELETE; aman dijalankan di produksi kapan saja.

```python
"""Laporan dampak data pra-deploy — READ-ONLY (tanpa perbaikan otomatis).

Dua pemeriksaan (task A7):
  (a) Invoice submitted (POS Invoice + Sales Invoice is_pos=1) yang punya
      baris item rate < price_list_rate TANPA pos_offer_item_rules —
      indikasi diskon tak terjelaskan (tidak teratribusi ke offer/pricing rule).
  (b) Wallet Transaction docstatus=1 yang mereferensi invoice cancelled
      (docstatus=2) atau invoice yang sudah tidak ada.

Cara menjalankan di container (dari direktori bench):
    bench --site posnext.localhost execute pos_next.audit.run

Atau lewat bench console:
    bench --site posnext.localhost console
    >>> from pos_next.audit import run; run()

Script hanya MEMBACA: satu query UNION ALL + satu loop get_value, tanpa
INSERT/UPDATE/DELETE. Kolom pos_offer_item_rules dibuat oleh install.py;
bila kolom belum ada (dijalankan sebelum bench migrate), pemeriksaan (a)
dilewati dengan catatan, bukan error.
"""

import frappe
from frappe.utils import cint, flt, now_datetime

INVOICE_DOCTYPES = ("POS Invoice", "Sales Invoice")
SAMPLE_LIMIT = 50


def _unexplained_discount_rows():
	"""Baris item submitted dengan rate < price_list_rate tanpa atribusi offer."""
	if not frappe.db.has_column("Sales Invoice Item", "pos_offer_item_rules"):
		return None  # kolom belum dibuat (patch/install belum jalan di situs ini)

	parts = []
	for dt in INVOICE_DOCTYPES:
		parts.append(
			f"""
			SELECT
				{frappe.db.escape(dt)} AS doctype,
				sii.parent AS invoice,
				sii.item_code,
				sii.rate,
				sii.price_list_rate,
				(sii.price_list_rate - sii.rate) AS discount_amount,
				si.posting_date,
				si.is_return
			FROM `tab{dt} Item` sii
			INNER JOIN `tab{dt}` si ON si.name = sii.parent
			WHERE si.docstatus = 1
			  AND si.is_pos = 1
			  AND sii.rate < sii.price_list_rate
			  AND IFNULL(sii.pos_offer_item_rules, '') = ''
			"""
		)
	return frappe.db.sql(" UNION ALL ".join(parts), as_dict=True)


def _broken_wallet_references():
	"""WT submitted yang referensinya cancelled atau hilang."""
	if not frappe.db.table_exists("Wallet Transaction"):
		return None

	rows = frappe.get_all(
		"Wallet Transaction",
		filters={
			"docstatus": 1,
			"reference_doctype": ("in", list(INVOICE_DOCTYPES)),
		},
		fields=["name", "reference_doctype", "reference_name", "wallet", "amount", "transaction_type", "source_type", "posting_date"],
	)

	broken = []
	for wt in rows:
		if not wt.reference_name:
			broken.append({**wt, "problem": "reference_name kosong"})
			continue
		docstatus = frappe.db.get_value(wt.reference_doctype, wt.reference_name, "docstatus")
		if docstatus is None:
			broken.append({**wt, "problem": "invoice tidak ada"})
		elif cint(docstatus) == 2:
			broken.append({**wt, "problem": "invoice cancelled"})
	return broken


def run() -> dict:
	"""Cetak dan kembalikan laporan dampak data situs aktif (read-only)."""
	report = {
		"site": frappe.local.site,
		"generated_at": now_datetime().isoformat(),
	}

	# (a) Diskon tak terjelaskan
	rows = _unexplained_discount_rows()
	if rows is None:
		summary_a = {
			"skipped": "kolom pos_offer_item_rules belum ada — jalankan setelah bench migrate",
			"item_rows": 0,
			"invoices": 0,
			"total_discount": 0.0,
			"by_doctype": {},
			"sample": [],
		}
	else:
		by_doctype = {}
		for r in rows:
			agg = by_doctype.setdefault(r.doctype, {"item_rows": 0, "invoices": set(), "total_discount": 0.0})
			agg["item_rows"] += 1
			agg["invoices"].add(r.invoice)
			agg["total_discount"] = flt(agg["total_discount"]) + flt(r.discount_amount)
		summary_a = {
			"skipped": None,
			"item_rows": len(rows),
			"invoices": len({r.invoice for r in rows}),
			"total_discount": flt(sum(flt(r.discount_amount) for r in rows)),
			"by_doctype": {
				dt: {**agg, "invoices": len(agg["invoices"])} for dt, agg in by_doctype.items()
			},
			"sample": rows[:SAMPLE_LIMIT],
		}
	report["a_unexplained_discount"] = summary_a

	# (b) Referensi wallet rusak
	broken = _broken_wallet_references()
	if broken is None:
		summary_b = {
			"skipped": "doctype Wallet Transaction belum ada",
			"checked": 0,
			"missing": 0,
			"cancelled": 0,
			"rows": [],
		}
	else:
		summary_b = {
			"skipped": None,
			"checked": frappe.db.count(
				"Wallet Transaction", {"docstatus": 1, "reference_doctype": ("in", list(INVOICE_DOCTYPES))}
			),
			"missing": sum(1 for b in broken if b["problem"] != "invoice cancelled"),
			"cancelled": sum(1 for b in broken if b["problem"] == "invoice cancelled"),
			"rows": broken[:SAMPLE_LIMIT],
		}
	report["b_wallet_broken_reference"] = summary_b

	# Cetak ringkasan manusiawi
	print(f"\n=== LAPORAN DAMPAK DATA — {report['site']} ({report['generated_at']}) ===")
	a, b = summary_a, summary_b
	if a["skipped"]:
		print(f"(a) Diskon tak terjelaskan  : DILEWATI — {a['skipped']}")
	else:
		print(f"(a) Diskon tak terjelaskan  : {a['item_rows']} baris item di {a['invoices']} invoice, total diskon {a['total_discount']:.2f}")
		for dt, agg in a["by_doctype"].items():
			print(f"    - {dt}: {agg['item_rows']} baris, {agg['invoices']} invoice, {agg['total_discount']:.2f}")
	if b["skipped"]:
		print(f"(b) Referensi wallet rusak  : DILEWATI — {b['skipped']}")
	else:
		print(f"(b) Referensi wallet rusak  : {len(b['rows'])} bermasalah dari {b['checked']} WT dicek (missing={b['missing']}, cancelled={b['cancelled']})")
	print("(READ-ONLY — tidak ada data diubah)\n")

	return report
```
