# Copyright (c) 2026, POS Next and contributors
# For license information, please see license.txt

"""REL-02 companion: mirror DocPerm standar ke Custom DocPerm.

Custom DocPerm menggantikan total DocPerm standar pada doctype yang
memilikinya, jadi baris fixture persona POS akan mengunci role standar
keluar dari doctype itu. mirror_standard_perms_for_custom_doctypes()
(install.py, dipanggil after_migrate/after_install) menyalin baris standar
untuk role yang belum punya baris custom, ter-scoped ke doctype milik
fixture pos_next sendiri.

Run via pos_next/_pn_run_tests.py pos_next.api.test_docperm_mirror
"""

from unittest import mock

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import cint

from pos_next.install import MIRROR_NAME_PREFIX, mirror_standard_perms_for_custom_doctypes


def _custom_rows(parent):
    return frappe.get_all(
        "Custom DocPerm",
        filters={"parent": parent},
        fields=["name", "role", "permlevel", "read", "write", "submit", "if_owner"],
    )


class TestCustomDocPermMirror(FrappeTestCase):
    def setUp(self):
        super().setUp()
        # Doctype netral: punya baris DocPerm standar tapi belum pernah
        # disentuh Custom DocPerm, sehingga skenario fixture-triggered bisa
        # disimulasikan tanpa mempengaruhi 16 doctype POS.
        self.doctype = frappe.db.sql(
            """
            select dp.parent
            from `tabDocPerm` dp
            where not exists (
                select 1 from `tabCustom DocPerm` cd where cd.parent = dp.parent
            )
            group by dp.parent
            having count(distinct dp.role) >= 2
            order by dp.parent
            limit 1
            """
        )[0][0]
        self.standard_rows = frappe.get_all(
            "DocPerm",
            filters={"parent": self.doctype},
            fields=["role", "permlevel", "read", "write", "submit", "if_owner"],
        )
        self.assertTrue(self.standard_rows, "precondition: doctype punya DocPerm standar")

        frappe.get_doc(
            {
                "doctype": "Custom DocPerm",
                "parent": self.doctype,
                "parenttype": "DocType",
                "parentfield": "permissions",
                "role": "POSNext Cashier",
                "permlevel": 0,
                "read": 1,
            }
        ).insert(ignore_permissions=True)
        # Scope mirror ke doctype uji saja — produksi mem scope-nya ke
        # doctype milik fixture pos_next.
        patcher = mock.patch(
            "pos_next.install._mirror_target_parents", return_value=[self.doctype]
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        for name in frappe.get_all("Custom DocPerm", filters={"parent": self.doctype}, pluck="name"):
            frappe.delete_doc("Custom DocPerm", name, ignore_permissions=True, force=True)
        frappe.clear_cache(doctype=self.doctype)
        super().tearDown()

    def test_mirrors_standard_roles_without_touching_custom_ones(self):
        mirror_standard_perms_for_custom_doctypes(quiet=True)

        mirrored = _custom_rows(self.doctype)
        mirrored_roles = {row.role for row in mirrored}
        # Semua role standar kini punya baris custom (akses role lain selamat).
        self.assertTrue(
            {r.role for r in self.standard_rows} <= mirrored_roles,
            f"role standar tak termirror",
        )
        # Role yang sudah punya baris custom tidak diduplikasi.
        self.assertEqual(
            len([row for row in mirrored if row.role == "POSNext Cashier"]), 1,
            "baris persona POS tidak boleh diduplikasi mirror",
        )
        # Nilai perm hasil mirror menyalin baris standarnya field-per-field
        # (bukan sekadar kehadiran role) untuk satu role sampel.
        sample_role = sorted({r.role for r in self.standard_rows})[0]
        by_key = {
            (row.role, cint(row.permlevel), cint(row.if_owner or 0)): row
            for row in mirrored
        }
        for std in self.standard_rows:
            if std.role != sample_role:
                continue
            copied = by_key.get((std.role, cint(std.permlevel), cint(std.if_owner or 0)))
            self.assertIsNotNone(copied, f"baris standar {std} tak termirror")
            for field in ("read", "write", "submit"):
                self.assertEqual(
                    cint(copied.get(field)), cint(std.get(field)),
                    f"nilai {field} mirror beda dari standar utk {sample_role}",
                )

    def test_idempotent_and_resyncs(self):
        mirror_standard_perms_for_custom_doctypes(quiet=True)
        after_first = sorted(map(str, _custom_rows(self.doctype)))
        # Run kedua: baris mirror lama dibuang lalu disalin ulang (nama
        # deterministik), jadi jumlah dan isi tidak berubah.
        mirror_standard_perms_for_custom_doctypes(quiet=True)
        after_second = sorted(map(str, _custom_rows(self.doctype)))
        self.assertEqual(after_first, after_second, "run kedua tidak boleh menambah baris")
        mirror_names = [n for n in frappe.get_all(
            "Custom DocPerm", filters={"parent": self.doctype}, pluck="name"
        ) if n.startswith(MIRROR_NAME_PREFIX)]
        self.assertTrue(mirror_names, "baris mirror memakai nama deterministik")
