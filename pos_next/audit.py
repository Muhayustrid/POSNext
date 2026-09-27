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
