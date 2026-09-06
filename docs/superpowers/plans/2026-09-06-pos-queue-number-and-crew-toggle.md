# POS Queue Number + Crew Slip Toggle — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Cetak nomor antrian (per Company per hari, offline-capable) di paling atas struk customer & crew slip, dan pisahkan crew slip dari pengaturan `copies` menjadi toggle sendiri.

**Architecture:** Counter server-side di DocType `POS Queue Counter` (lock per company+tanggal), nomor distempel ke custom field Sales Invoice saat checkout, mengalir ke 3 template print. Crew slip: knob boolean `crewSlipEnabled` (device > server > default false), driver mencetak N copy identik + 1 crew slip di akhir.

**Tech Stack:** Frappe/ERPNext v16 (Python), Vue 3 SPA + Vitest, iMin SDK bitmap pipeline.

**Spec:** `docs/superpowers/specs/2026-09-06-pos-queue-number-design.md` (queue) + desain crew-slip decoupling yang disetujui di chat 2026-09-06 (task 1–6).

## Global Constraints

- SEMUA perintah bench/python/test lewat container: `docker exec erpnext16_dev-frappe-1 bash -lc "cd /workspace/development/frappe-bench && <cmd>"`. Host macOS tidak punya env.
- Test BE serial-only: `./env/bin/python apps/pos_next/pos_next/_pn_run_tests.py <modul>` (site `erpnext16.localhost`). Jangan jalankan dua proses test python paralel.
- Test FE: `cd apps/pos_next/POS && yarn vitest run <file>` (di dalam container).
- Setelah perubahan schema/doctype/custom field/print format: `bench --site posnext.localhost migrate` DAN `bench --site erpnext16.localhost migrate`.
- Print format fixture WAJIB bump key `modified` ke waktu sekarang, atau migrate melewatkan sync.
- Sync custom fields: `bench --site <site> execute pos_next.install.sync_custom_fields` (module `pos_next.install`, lihat `pos_next/install.py`).
- Format antrian: label `NO. ANTRIAN`, angka zero-pad 3 digit (`048`), blok di ELEMEN PALING ATAS struk (sebelum nama company).
- Default `crewSlipEnabled` = false; help text operator bahasa Inggris mengikuti halaman DirectPrint yang ada.
- Commit per task, pesan Conventional Commits (caveman-commit): `<type>(<scope>): <imperative>`.
- Branch kerja: `main` (workflow repo ini). Cek `git branch --show-current` sebelum commit.

---

## Part 1 — Crew Slip Toggle

### Task 1: POS Settings field + API `crew_slip_enabled`

**Files:**
- Modify: `pos_next/pos_next/doctype/pos_settings/pos_settings.json`
- Modify: `pos_next/pos_next/api/printing.py:14-35` (PRINT_CONFIG_FIELDS), `printing.py:234-256` (return)
- Test: `pos_next/pos_next/api/test_printing.py`

**Interfaces:**
- Produces: `get_print_config()` response key baru `crew_slip_enabled: bool`. Kolom Check `imin_crew_slip_enabled` di POS Settings (setelah `imin_crew_font_scale`). Task 3 & 5 mengonsumsi key ini.

- [ ] **Step 1: Write the failing test** — tambah di `test_printing.py` dalam class `TestPrintingAPI`:

```python
	def test_crew_slip_enabled_from_settings_row(self):
		with _settings_row(imin_crew_slip_enabled=1):
			cfg = get_print_config(self.profile)
		self.assertTrue(cfg["crew_slip_enabled"])

	def test_crew_slip_enabled_defaults_off(self):
		with _settings_row():
			cfg = get_print_config(self.profile)
		self.assertFalse(cfg["crew_slip_enabled"])
```

- [ ] **Step 2: Run** `./env/bin/python apps/pos_next/pos_next/_pn_run_tests.py pos_next.api.test_printing` — expect FAIL (KeyError `crew_slip_enabled`).
- [ ] **Step 3: Implement.** `pos_settings.json`: duplikat field `imin_crew_font_scale` sebagai field baru `{fieldname: "imin_crew_slip_enabled", label: "Print Crew Slip", fieldtype: "Check", default: "0", description: "Print one crew slip after the customer copies, independent of the copy count."}` tepat setelah `imin_crew_font_scale`. `printing.py`: tambah `"imin_crew_slip_enabled",` ke tuple `PRINT_CONFIG_FIELDS`; di dict return tambah setelah `"crew_font_scale"`:

```python
		"crew_slip_enabled": bool(getattr(settings, "imin_crew_slip_enabled", None)),
```

- [ ] **Step 4: Run test** — expect PASS.
- [ ] **Step 5: Migrate kedua site** (kolom baru): `bench --site posnext.localhost migrate && bench --site erpnext16.localhost migrate`.
- [ ] **Step 6: Commit** `feat(printing): add POS Settings crew-slip toggle`

### Task 2: Resolver `crewSlipEnabled`

**Files:**
- Modify: `POS/src/utils/print/receipt_layout.js:227-308` (`resolvePrintConfig`)
- Test: `POS/src/utils/print/receipt_layout.test.js`

**Interfaces:**
- Consumes: Task 1 key `crew_slip_enabled` (lewat objek `server`).
- Produces: `resolvePrintConfig()` return key `crewSlipEnabled: boolean`. Lane eod selalu `false` (EOD tidak punya crew slip).

- [ ] **Step 1: Failing test** — tambah ke describe `resolvePrintConfig`:

```javascript
	it("resolves crewSlipEnabled: device wins, server next, default false", () => {
		expect(
			resolvePrintConfig({ crewSlipEnabled: true }, { crewSlipEnabled: false }),
		).toMatchObject({ crewSlipEnabled: true })
		expect(resolvePrintConfig({}, { crewSlipEnabled: true })).toMatchObject({
			crewSlipEnabled: true,
		})
		expect(resolvePrintConfig({}, {})).toMatchObject({ crewSlipEnabled: false })
	})

	it("never enables a crew slip on the eod lane", () => {
		expect(
			resolvePrintConfig({ crewSlipEnabled: true }, {}, { kind: "eod" }),
		).toMatchObject({ crewSlipEnabled: false })
	})
```

- [ ] **Step 2: Run** `yarn vitest run src/utils/print/receipt_layout.test.js` — expect FAIL.
- [ ] **Step 3: Implement** — di `resolvePrintConfig`, setelah deklarasi `crewFontScale`:

```javascript
	// The crew slip is a separate sheet开关, decoupled from the copy count:
	// copies are identical receipts, the slip prints once after them. The
	// EOD lane has no crew slip at all.
	const crewSlipEnabled = eod
		? false
		: Boolean(device.crewSlipEnabled ?? server.crewSlipEnabled)
```

(catatan: tulis komentar dalam bahasa Inggris — "The crew slip is a separate sheet switch, decoupled from the copy count..."). Masukkan `crewSlipEnabled` ke objek return.
- [ ] **Step 4: Run test** — PASS. Commit `feat(print): resolve crewSlipEnabled knob in print config`.

### Task 3: Transport plumbing

**Files:**
- Modify: `POS/src/utils/print/transport.js:64-85` (config driver), `transport.js:191-219` (`initTransportFromServer`)
- Test: `POS/src/utils/print/transport.test.js`

**Interfaces:**
- Consumes: Task 1 `crew_slip_enabled`; Task 2 `crewSlipEnabled` (dibaca driver via `opts.config`).
- Produces: `driver.printHTML(html, opts)` menerima `opts.config.crewSlipEnabled`.

- [ ] **Step 1: Failing test** — di test transport yang sudah meng-inspect config yang diteruskan ke driver (ikuti idiom yang ada, contohnya test yang meng-assert `config.copies`), tambah assertion `crewSlipEnabled: true` pada config driver ketika `setConfig({ crew_slip_enabled: true })` dipanggil sebelum `printHTML`.
- [ ] **Step 2: Run** — FAIL.
- [ ] **Step 3: Implement**: di `printHTML` config object tambah `crewSlipEnabled: current.crew_slip_enabled,`; di `initTransportFromServer` setConfig tambah `crew_slip_enabled: cfg.crew_slip_enabled,`.
- [ ] **Step 4: Run** — PASS. Commit `feat(print): plumb crew_slip_enabled through the transport`.

### Task 4: Driver iMin — sheet baru (copy identik + crew terakhir)

**Files:**
- Modify: `POS/src/utils/print/imin_client.js:225-288` (`printHTML`)
- Test: `POS/src/utils/print/imin_client.test.js` (rewrite describe "crew slip")

**Interfaces:**
- Consumes: `r.crewSlipEnabled` dari resolver (Task 2), `opts.crewHTML` (sudah ada).
- Produces: urutan cetak `[receipt × copies] + [crew?]`; tear-off pause antar semua lembar, tidak setelah lembar terakhir.

- [ ] **Step 1: Rewrite failing tests** — ganti seluruh describe `crew slip (copy 2 is a compact slip...)` menjadi (ikut idiom mock yang ada di file):

```javascript
	describe("crew slip (own toggle, prints once after the copies)", () => {
		const crewHTML = '<div class="crew">crew</div>'
		// helper render existing: resolve (h) => { dataURL: ..., width/height }

		it("prints one crew slip after a single copy when enabled", async () => {
			// opts: { crewHTML, config: { crewSlipEnabled: true, copies: 1, ... } }
			// expect render called with receipt then crew; printSingleBitmap 2x
		})

		it("prints identical copies and no slip when disabled", async () => {
			// config: { crewSlipEnabled: false, copies: 2 } + crewHTML
			// expect render NEVER called with crewHTML; printSingleBitmap 2x receipt
		})

		it("prints copies then crew when both are on", async () => {
			// config: { crewSlipEnabled: true, copies: 2 }
			// expect order: receipt, receipt, crew
		})
	})
```

Lengkapi mock/expectation mengikuti pola test yang ada (render injection `opts.render`, `printSingleBitmap` spy via factory fake). Detail idiom: lihat test lama di blok yang sama — struktur factory/render/urls dipertahankan, hanya semantik yang berubah.
- [ ] **Step 2: Run** `yarn vitest run src/utils/print/imin_client.test.js` — FAIL.
- [ ] **Step 3: Implement** — di `imin_client.js printHTML`:

```javascript
			const crewApplies = Boolean(opts.crewHTML) && crewSlipEnabled
```

(destructure `crewSlipEnabled` dari `r`), lalu ganti loop:

```javascript
			const sheets = Array.from({ length: copies }, () => bitmap)
			if (crewApplies) sheets.push(crewBitmap)

			for (let i = 0; i < sheets.length; i++) {
				const bmp = sheets[i]
				const tQueued = Date.now()
				await p.printSingleBitmap(bmp.dataURL, 1)
				await new Promise((r) => setTimeout(r, SETTLE_MS))
				p.printAndFeedPaper(feedDots)
				if (cut) p.partialCut()
				await waitIdle(p)
				const elapsed = Date.now() - tQueued
				const reserveMs = Math.max(
					0,
					SETTLE_MS + bitmapPrintMs(bmp.height) - elapsed,
				)
				const isLastSheet = i === sheets.length - 1
				const pauseMs = isLastSheet ? 0 : reserveMs + copyDelayMs
				log.info("sheet printed", {
					sheet: i + 1,
					kind: crewApplies && i === sheets.length - 1 ? "crew" : "receipt",
					heightDots: bmp.height,
					elapsedMs: elapsed,
					reserveMs,
					pauseMs,
				})
				if (!isLastSheet) {
					await new Promise((r) => setTimeout(r, pauseMs))
				}
			}

			return { paper, dots, copies: sheets.length, tailDots }
```

Perbarui juga komentar header file (baris 1-20) dan komentar blok crew: sebutkan bahwa crew slip kini dikendalikan `crewSlipEnabled`, bukan `copies > 1`.
- [ ] **Step 4: Run test** — PASS. Commit `feat(print): decouple crew slip from copy count`.

### Task 5: Preview mengikuti semantik baru

**Files:**
- Modify: `POS/src/utils/print/receipt_preview.js:40-92` (`buildReceiptPreviewSet`)
- Test: `POS/src/utils/print/receipt_preview.test.js`

**Interfaces:**
- Consumes: Task 2 `r.crewSlipEnabled`.
- Produces: `copies` array = `Copy 1..N` + satu row `CREW COPY` terakhir saat aktif; `delayMs` row krew = `N * copyDelayMs`.

- [ ] **Step 1: Rewrite failing tests** — ubah test crew preview yang ada (copy 2 = crew) menjadi: enabled + copies 1 → 2 row `[Copy 1, CREW COPY]` dan bitmap row 2 dirender dari crewHTML dengan crewFontScale; disabled + copies 2 → 2 row `Copy 1/2` tanpa render crewHTML; enabled + copies 2 → `[Copy 1, Copy 2, CREW COPY]`.
- [ ] **Step 2: Run** — FAIL.
- [ ] **Step 3: Implement** — ganti `crewApplies` dan array bitmap:

```javascript
	const crewApplies = Boolean(opts.crewHTML) && !eod && r.crewSlipEnabled
	// ... crewBitmap render sama seperti sekarang ...
	const bitmaps = Array.from({ length: r.copies }, () => shared)
	if (crewApplies) bitmaps.push(crewBitmap)
	// label: index < r.copies ? `Copy ${index + 1}` : "CREW COPY"
```

- [ ] **Step 4: Run** — PASS. Commit `feat(print): preview mirrors crew-as-last-sheet`.

### Task 6: Halaman DirectPrint — toggle & teks

**Files:**
- Modify: `POS/src/pages/DirectPrint.vue` (baris: 197-224 inputs, 205-207 help copies, 349-370 summary, 425-445 preview help & tombol, 925-998 state/load, 1130-1200 save)
- Build: `yarn build` (halaman, tanpa unit test)

**Interfaces:**
- Consumes: Task 1-5 (`crewSlipEnabled` device key; resolver).
- Produces: device config key `crewSlipEnabled: true|false|undefined` (undefined = ikut server).

- [ ] **Step 1: State.** Tambah `crewOptions` + pilihan tri-state, muat/simpan:

```javascript
	const crewOptions = [
		{ label: "Server default", value: "" },
		{ label: "On", value: "1" },
		{ label: "Off", value: "0" },
	]
	const crewSlipChoice = ref("") // "" | "1" | "0"
```

`readReceiptIntoForm`: `crewSlipChoice.value = stored.crewSlipEnabled === true ? "1" : stored.crewSlipEnabled === false ? "0" : ""`. Di `onSaveReceiptConfig`, `saveDeviceConfig({...})` tambah:

```javascript
				crewSlipEnabled:
					crewSlipChoice.value === "1"
						? true
						: crewSlipChoice.value === "0"
							? false
							: undefined, // JSON.stringify drops it = unset
```

- [ ] **Step 2: Template.** Tambah Select "Crew slip" (`id="direct-print-crew-slip"`, `v-model="crewSlipChoice"`, `:options="crewOptions"`) di grid setelah blok copies, help text: `"One short order slip for the outlet, printed after the customer copies — independent of the copy count."`. Ganti help copies jadi `{{ __("1 = one receipt. 2 = two identical receipts.") }}`. Delay-input visibility: `v-if="Number(cfg.copies) > 1 || crewSlipChoice === '1'"`. Ganti help preview (baris ~431) menyebut crew sheet sebagai lembar terakhir. Summary line (baris ~358) tambah segmen `Crew {on/off}` dari `effectiveCfg.crewSlipEnabled`.
- [ ] **Step 3: Preview & Test Print.** Tombol `Preview 2 copies` biarkan (preview pakai `buildReceiptPreviewSet` yang sudah baru). Pastikan pemanggil preview mengoper `crewHTML: sampleCrewHTML(bundle)` (jika belum di jalur preview — di `onTestPrint` sudah ada). Label tombol/preview biarkan; row CREW COPY otomatis muncul.
- [ ] **Step 4: Build & lint** — `yarn build && yarn biome check src/pages/DirectPrint.vue`; lalu hard-check manual nanti di rollout.
- [ ] **Step 5: Commit** `feat(direct-print): separate crew-slip toggle from copies`.

---

## Part 2 — Nomor Antrian

### Task 7: Custom fields + DocType counter

**Files:**
- Modify: `pos_next/install.py` (`CUSTOM_FIELDS`, dict entry `"Sales Invoice"` mulai baris 31; tambah entry `"Company"`)
- Create: `pos_next/pos_next/doctype/pos_queue_counter/__init__.py`, `pos_queue_counter.json`, `pos_queue_counter.py`
- Modify: `pos_next/pos_next/hooks.py` (tidak — workspace link tidak diminta; skip)

**Interfaces:**
- Produces: field `Company.enable_pos_queue` (Check), `Sales Invoice.pos_queue_number` (Int), `Sales Invoice.pos_queue_date` (Date); DocType `POS Queue Counter` (fields `company` Link Company, `date` Date, `current_number` Int; autoname `hash`).

- [ ] **Step 1: Custom fields** — di `CUSTOM_FIELDS` `"Sales Invoice"` list tambah setelah `pos_applied_offer_rules`:

```python
		{
			"fieldname": "pos_queue_number",
			"label": "POS Queue Number",
			"fieldtype": "Int",
			"insert_after": "pos_applied_offer_rules",
			"read_only": 1,
			"no_copy": 1,
			"print_hide": 1,
			"translatable": 0,
			"description": "Daily per-outlet queue number stamped at checkout; printed at the top of the receipt.",
		},
		{
			"fieldname": "pos_queue_date",
			"label": "POS Queue Date",
			"fieldtype": "Date",
			"insert_after": "pos_queue_number",
			"read_only": 1,
			"no_copy": 1,
			"print_hide": 1,
			"translatable": 0,
			"description": "Calendar date the queue number belongs to.",
		},
```

Tambah entry baru di dict:

```python
	"Company": [
		{
			"fieldname": "enable_pos_queue",
			"label": "Enable POS Queue Number",
			"fieldtype": "Check",
			"insert_after": "default_bank_account",
			"default": "0",
			"translatable": 0,
			"description": "Print a daily queue number on receipts from this outlet.",
		},
	],
```

- [ ] **Step 2: DocType** — buat folder `pos_next/pos_next/doctype/pos_queue_counter/`. `__init__.py` kosong. JSON ikuti pola doctype lain di modul yang sama (mis. `pos_discount_confirmation_code`): module "POS Next", custom 0, istable 0, editable_grid 0, engine InnoDB, field_order & fields:

```json
{
 "actions": [],
 "autoname": "hash",
 "creation": "2026-09-06 17:30:00.000000",
 "doctype": "DocType",
 "engine": "InnoDB",
 "field_order": ["company", "date", "current_number"],
 "fields": [
  {"fieldname": "company", "fieldtype": "Link", "in_list_view": 1, "label": "Company", "options": "Company", "reqd": 1},
  {"fieldname": "date", "fieldtype": "Date", "in_list_view": 1, "label": "Date", "reqd": 1},
  {"fieldname": "current_number", "default": "0", "fieldtype": "Int", "in_list_view": 1, "label": "Current Number"}
 ],
 "index_web_pages_for_search": 1,
 "links": [],
 "modified": "2026-09-06 17:30:00.000000",
 "modified_by": "Administrator",
 "module": "POS Next",
 "name": "POS Queue Counter",
 "owner": "Administrator",
 "permissions": [
  {"create": 1, "delete": 1, "email": 1, "print": 1, "read": 1, "report": 1, "role": "System Manager", "share": 1, "write": 1},
  {"create": 1, "email": 1, "print": 1, "read": 1, "report": 1, "role": "Sales User", "share": 1, "write": 1}
 ],
 "sort_field": "modified",
 "sort_order": "DESC",
 "states": []
}
```

`pos_queue_counter.py`:

```python
# Copyright (c) 2026, POS Next and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document


class POSQueueCounter(Document):
	# begin: auto-generated types
	from frappe.types import DF

	if frappe.__version__ >= "15":
		company: DF.Link
		date: DF.Date | None
		current_number: DF.Int
	# end: auto-generated types

	def validate(self):
		duplicate = frappe.db.exists(
			"POS Queue Counter",
			{"company": self.company, "date": self.date, "name": ("!=", self.name)},
		)
		if duplicate:
			frappe.throw(
				_("A queue counter for {0} on {1} already exists").format(
					frappe.bold(self.company), frappe.bold(self.date)
				)
			)
```

- [ ] **Step 3: Migrate + sync kedua site**:

```bash
bench --site posnext.localhost migrate && bench --site erpnext16.localhost migrate
bench --site posnext.localhost execute pos_next.install.sync_custom_fields
bench --site erpnext16.localhost execute pos_next.install.sync_custom_fields
```

- [ ] **Step 4: Verify via console** (`bench --site erpnext16.localhost console`): insert dua counter (company sama, tanggal sama) → yang kedua throw; `frappe.db.exists("DocType", "POS Queue Counter")` truthy; rollback.
- [ ] **Step 5: Commit** `feat(queue): add queue counter doctype and invoice/company fields`.

### Task 8: API `get_next_queue_number` + tests

**Files:**
- Create: `pos_next/pos_next/api/queue.py`
- Test: `pos_next/pos_next/api/test_queue.py`

**Interfaces:**
- Consumes: Task 7 doctype + `Company.enable_pos_queue`.
- Produces: `pos_next.api.queue.get_next_queue_number(pos_profile: str) -> dict` dengan shape `{"enabled": bool, "company"?: str, "date"?: str, "queue_number"?: int}` — dipakai Task 10 (SPA).

- [ ] **Step 1: Failing tests** (`test_queue.py`, idiom `FrappeTestCase` dari `test_printing.py`):

```python
import frappe
from frappe.tests.utils import FrappeTestCase

from pos_next.api.queue import get_next_queue_number


class TestQueueAPI(FrappeTestCase):
	def setUp(self):
		self.company = frappe.db.get_value("Company", {}, "name")
		self.profile = frappe.db.get_value(
			"POS Profile", {"disabled": 0, "company": self.company}, "name"
		)
		frappe.db.set_value("Company", self.company, "enable_pos_queue", 1)
		frappe.db.delete("POS Queue Counter", {"company": self.company})

	def tearDown(self):
		frappe.db.rollback()

	def test_disabled_company_gets_no_number(self):
		frappe.db.set_value("Company", self.company, "enable_pos_queue", 0)
		out = get_next_queue_number(self.profile)
		self.assertFalse(out["enabled"])
		self.assertNotIn("queue_number", out)

	def test_numbers_increment_per_day(self):
		a = get_next_queue_number(self.profile)
		b = get_next_queue_number(self.profile)
		self.assertTrue(a["enabled"])
		self.assertEqual(b["queue_number"], a["queue_number"] + 1)

	def test_separate_companies_do_not_share(self):
		# buat company kedua minimal: cukup dua profile beda company; jika hanya
		# satu company di site, skip test ini dengan self.skipIf
		...

	def test_counter_rolls_to_new_date(self):
		a = get_next_queue_number(self.profile)
		# ubah tanggal baris counter ke kemarin; nomor berikutnya mulai dari 1
		name = frappe.db.get_value(
			"POS Queue Counter", {"company": self.company}, "name"
		)
		frappe.db.set_value(name, "date", "2000-01-01")
		c = get_next_queue_number(self.profile)
		self.assertEqual(c["queue_number"], 1)
```

- [ ] **Step 2: Run** `./env/bin/python apps/pos_next/pos_next/_pn_run_tests.py pos_next.api.test_queue` — FAIL (module tidak ada).
- [ ] **Step 3: Implement** `queue.py`:

```python
# Copyright (c) 2026, POS Next and contributors
# For license information, please see license.txt

"""Daily per-outlet queue numbers for receipts.

One counter row per (company, date). Allocation locks the row so two tills
in one outlet serialize; the increment commits in its own transaction so
the number exists the moment it is answered, independent of when the
invoice is saved.
"""

import frappe
from frappe import _
from frappe.utils import nowdate

import frappe.database.database  # noqa: F401  (type hints only)


@frappe.whitelist()
def get_next_queue_number(pos_profile: str) -> dict:
	company = frappe.db.get_value("POS Profile", pos_profile, "company")
	if not company:
		frappe.throw(_("POS Profile {0} not found").format(pos_profile))
	if not frappe.db.get_value("Company", company, "enable_pos_queue"):
		return {"enabled": False}

	date = nowdate()
	current = _locked_current_number(company, date)
	next_number = current + 1
	name = frappe.db.exists("POS Queue Counter", {"company": company, "date": date})
	if name:
		frappe.db.set_value("POS Queue Counter", name, "current_number", next_number)
	else:
		frappe.get_doc(
			{
				"doctype": "POS Queue Counter",
				"company": company,
				"date": date,
				"current_number": next_number,
			}
		).insert(ignore_permissions=True)
	frappe.db.commit()
	return {
		"enabled": True,
		"company": company,
		"date": date,
		"queue_number": next_number,
	}


def _locked_current_number(company: str, date: str) -> int:
	"""Current number under a row lock, 0 when the row does not exist yet.

	Locks only an existing row: the insert race for the first number of the
	day resolves through the doctype's (company, date) uniqueness instead.
	"""
	row = frappe.db.get_value(
		"POS Queue Counter",
		{"company": company, "date": date},
		["name", "current_number"],
		as_dict=True,
		for_update=True,
	)
	if row:
		return int(row.current_number or 0)
	return 0
```

Catatan: `frappe.db.get_value(..., for_update=True)` mengunci baris dalam transaksi request ini; karena get_next dipanggil sebagai endpoint sendiri, commit di akhir menutup lock. Jika dua kasir memanggil bersamaan, keduanya serial pada lock baris. Hapus import `frappe.database.database` bila lint tidak butuh.
- [ ] **Step 4: Run tests** — PASS (jika `test_separate_companies_do_not_share` tak bisa dibuat di site single-company, ganti dengan dua profile perusahaan yang ada atau `self.skipIf`).
- [ ] **Step 5: Commit** `feat(queue): server-side daily queue number allocation`.

### Task 9: Hook `on_submit` menaikkan counter

**Files:**
- Create: `pos_next/pos_next/overrides/queue_counter.py`
- Modify: `pos_next/pos_next/hooks.py:158-173` (doc_events "Sales Invoice" on_submit)
- Test: `pos_next/pos_next/api/test_queue.py`

**Interfaces:**
- Consumes: Task 7 field `pos_queue_number`/`pos_queue_date`; Task 8 `_locked_current_number` pattern.
- Produces: `pos_next.overrides.queue_counter.bump_queue_counter(doc, method)`.

- [ ] **Step 1: Failing test** — tambah ke `test_queue.py`:

```python
	def test_submit_bumps_counter_to_printed_number(self):
		from pos_next.overrides.queue_counter import bump_queue_counter

		# struk offline menyimpan nomor 7 tanpa server tahu
		frappe.get_doc({
			"doctype": "POS Queue Counter",
			"company": self.company,
			"date": frappe.utils.nowdate(),
			"current_number": 2,
		}).insert(ignore_permissions=True)
		bump_queue_counter(
			frappe._dict(
				company=self.company,
				pos_queue_number=7,
				pos_queue_date=frappe.utils.nowdate(),
			),
			None,
		)
		out = get_next_queue_number(self.profile)
		self.assertEqual(out["queue_number"], 8)

	def test_submit_lower_number_does_not_lower_counter(self):
		# counter 9, invoice sync bernomor 3 -> next tetap 10
		...
```

- [ ] **Step 2: Run** — FAIL.
- [ ] **Step 3: Implement** `overrides/queue_counter.py`:

```python
# Copyright (c) 2026, POS Next and contributors
# For license information, please see license.txt

"""Keep the queue counter ahead of numbers already printed offline.

Offline tills continue locally from their last known number; when those
invoices sync and submit, this hook raises the company counter so the
next online allocation never reuses a number that already left the
printer. It never lowers the counter.
"""

import frappe
from frappe.utils import nowdate


def bump_queue_counter(doc, method=None):
	number = doc.get("pos_queue_number")
	company = doc.get("company")
	if not number or not company:
		return
	date = doc.get("pos_queue_date") or nowdate()
	name = frappe.db.get_value(
		"POS Queue Counter", {"company": company, "date": date}, ["name"]
	)
	if name:
		frappe.db.set_value(
			"POS Queue Counter", name, "current_number", number
		)
	else:
		frappe.get_doc(
			{
				"doctype": "POS Queue Counter",
				"company": company,
				"date": date,
				"current_number": number,
			}
		).insert(ignore_permissions=True)
```

Catatan: `frappe.db.set_value` pada DocType field Int tidak menaikkan nilai — gunakan `frappe.db.sql("UPDATE `tabPOS Queue Counter` SET current_number = GREATEST(current_number, %s) WHERE name = %s", (number, name))` agar tidak pernah turun. `hooks.py`: dalam `doc_events["Sales Invoice"]["on_submit"]` list tambah `"pos_next.overrides.queue_counter.bump_queue_counter",`.
- [ ] **Step 4: Run tests** — PASS. Commit `feat(queue): raise counter on invoice submit`.

### Task 10: SPA queue util (online + offline cache)

**Files:**
- Create: `POS/src/utils/queue/queueNumber.js`
- Test: `POS/src/utils/queue/queueNumber.test.js`

**Interfaces:**
- Consumes: Task 8 API `pos_next.api.queue.get_next_queue_number`.
- Produces (dipakai Task 12 & 13): `acquireQueueNumber({ company, posProfile, offline }) -> Promise<{queue_number, date} | null>`; `formatQueueNumber(n) -> "048"`; `readQueueCache(company)`.

- [ ] **Step 1: Failing tests** (ikut idiom mock `call` dari test yang ada, mis. `printInvoice.test.js`):

```javascript
import { describe, it, expect, vi, beforeEach } from "vitest"

const { acquireQueueNumber, formatQueueNumber, readQueueCache } = await import(
	"./queueNumber"
)

describe("queueNumber", () => {
	beforeEach(() => {
		localStorage.clear()
		vi.resetModules()
	})

	it("formats with 3-digit zero pad and beyond", () => {
		expect(formatQueueNumber(7)).toBe("007")
		expect(formatQueueNumber(1000)).toBe("1000")
	})

	it("uses the server number online and caches it", async () => {
		mockCall.resolveWith({ enabled: true, queue_number: 12, date: "2026-09-06" })
		const out = await acquireQueueNumber({ company: "C", posProfile: "P" })
		expect(out.queue_number).toBe(12)
		expect(readQueueCache("C")).toEqual({ date: "2026-09-06", number: 12 })
	})

	it("returns null when the company disabled the queue", async () => {
		mockCall.resolveWith({ enabled: false })
		expect(await acquireQueueNumber({ company: "C", posProfile: "P" })).toBeNull()
		expect(readQueueCache("C")).toBeNull()
	})

	it("continues locally offline from today's cache", async () => {
		localStorage.setItem(
			"pos_queue_last::C",
			JSON.stringify({ date: today(), number: 41 }),
		)
		const out = await acquireQueueNumber({ company: "C", posProfile: "P", offline: true })
		expect(out.queue_number).toBe(42)
	})

	it("restarts at 1 when the cached date is stale or absent", async () => {
		localStorage.setItem(
			"pos_queue_last::C",
			JSON.stringify({ date: "2000-01-01", number: 41 }),
		)
		const out = await acquireQueueNumber({ company: "C", posProfile: "P", offline: true })
		expect(out.queue_number).toBe(1)
	})

	it("falls back to local continuation when the API call fails online", async () => {
		mockCall.rejectWith(new Error("net"))
		localStorage.setItem(
			"pos_queue_last::C",
			JSON.stringify({ date: today(), number: 5 }),
		)
		const out = await acquireQueueNumber({ company: "C", posProfile: "P" })
		expect(out.queue_number).toBe(6)
	})
})
```

- [ ] **Step 2: Run** `yarn vitest run src/utils/queue/queueNumber.test.js` — FAIL (module missing).
- [ ] **Step 3: Implement**:

```javascript
/**
 * Daily per-outlet queue numbers.
 *
 * Online: the server allocates under a row lock (unique across tills).
 * Offline (or when the API is unreachable): continue locally from the last
 * number this device printed for the company today — two tills offline at
 * the same moment can collide; that trade-off is accepted (queue numbers
 * are a calling aid, not a legal document).
 */
import { call } from "@/utils/apiWrapper"
import { logger } from "@/utils/logger"

const log = logger.create("QueueNumber")
const cacheKey = (company) => `pos_queue_last::${company}`
const localDate = () => new Date().toISOString().slice(0, 10)

export function readQueueCache(company) {
	try {
		const raw = JSON.parse(localStorage.getItem(cacheKey(company)) || "null")
		if (raw && typeof raw.number === "number" && typeof raw.date === "string") {
			return raw
		}
	} catch {
		/* corrupt cache = absent */
	}
	return null
}

function writeQueueCache(company, date, number) {
	localStorage.setItem(cacheKey(company), JSON.stringify({ date, number }))
}

export function formatQueueNumber(n) {
	const v = Number(n)
	if (!Number.isFinite(v) || v <= 0) return ""
	return String(Math.floor(v)).padStart(3, "0")
}

async function fetchServerNumber(posProfile) {
	const res = await call("pos_next.api.queue.get_next_queue_number", {
		pos_profile: posProfile,
	})
	if (!res?.enabled) return null
	return { queue_number: res.queue_number, date: res.date }
}

function nextLocalNumber(company) {
	const today = localDate()
	const cached = readQueueCache(company)
	const number = cached && cached.date === today ? cached.number + 1 : 1
	return { queue_number: number, date: today }
}

export async function acquireQueueNumber({ company, posProfile, offline = false }) {
	if (!company) return null
	if (!offline) {
		try {
			const out = await fetchServerNumber(posProfile)
			if (out) {
				writeQueueCache(company, out.date, out.queue_number)
				return out
			}
			return null // disabled company
		} catch (err) {
			log.warn("queue number fetch failed, continuing locally:", err?.message || err)
		}
	}
	const out = nextLocalNumber(company)
	writeQueueCache(company, out.date, out.queue_number)
	return out
}
```

- [ ] **Step 4: Run** — PASS. Commit `feat(pos): queue number acquisition with offline continuation`.

### Task 11: Bootstrap mengekspos `queue_enabled`

**Files:**
- Modify: `pos_next/pos_next/api/bootstrap.py` (`_get_pos_settings`, ~baris 195-230; company tersedia via `pos_profile_doc.company`)
- Modify: `POS/src/stores/posSettings.js` (computed)
- Test: extend `pos_next/pos_next/api/test_queue.py`

**Interfaces:**
- Produces: `settings.queue_enabled: bool` di payload bootstrap; FE computed `posSettings.queueEnabled`.

- [ ] **Step 1: Failing test** — di `test_queue.py`:

```python
	def test_bootstrap_settings_include_queue_enabled(self):
		from pos_next.api.bootstrap import _get_pos_settings

		profile = frappe.get_doc("POS Profile", self.profile)
		profile = profile  # company field present
		settings = _get_pos_settings(profile)
		self.assertIn("queue_enabled", settings)
```

- [ ] **Step 2: Run** — FAIL.
- [ ] **Step 3: Implement** — dalam `_get_pos_settings(pos_profile_doc)`, setelah settings dict terisi:

```python
	settings["queue_enabled"] = bool(
		frappe.db.get_value("Company", pos_profile_doc.company, "enable_pos_queue")
	)
```

`posSettings.js` (blok computed Printing ~149-159):

```javascript
	const queueEnabled = computed(() => Boolean(settings.value.queue_enabled));
```

dan export `queueEnabled` di return store (ikut pola `silentPrint` baris 414).
- [ ] **Step 4: Run test** — PASS. Commit `feat(pos): expose queue_enabled via bootstrap`.

### Task 12: Stempel nomor saat checkout

**Files:**
- Modify: `POS/src/composables/useInvoice.js:1091-1104` (invoiceData online)
- Modify: `POS/src/pages/POSSale.vue:2204-2228` (invoiceData offline) dan `~2259-2286` (offlinePrintDoc)
- Modify: `POS/src/utils/printInvoice.js:70-101` (`receiptDocFromQueuedInvoice` — passthrough field)
- Test: `POS/src/utils/printInvoice.test.js`

**Interfaces:**
- Consumes: Task 10 `acquireQueueNumber`; Task 11 `queueEnabled`.
- Produces: invoice payload membawa `pos_queue_number`/`pos_queue_date`; dokumen print (offline & server) membawanya.

- [ ] **Step 1: Failing test** — di `printInvoice.test.js`, tambah: `receiptDocFromQueuedInvoice` (via `hydrateLocalOnlyInvoice` path atau export jika ada) mempertahankan `pos_queue_number: 12` & `pos_queue_date` dari raw queue payload. (Fungsi tidak di-export → uji lewat `hydrateLocalOnlyInvoice` dengan IndexedDB mock yang sudah dipakai file test itu; jika mock tidak ada untuk field baru, cukup assert hasil mapping memuat kedua field.)
- [ ] **Step 2: Run** — FAIL.
- [ ] **Step 3: Implement**:
  - `receiptDocFromQueuedInvoice`: pada return object tambah `pos_queue_number: raw.pos_queue_number ?? null, pos_queue_date: raw.pos_queue_date || null,` (juga di `offlinePrintDoc` POSSale.vue: `pos_queue_number: queueNumber, pos_queue_date: queueDate`).
  - `useInvoice.js` online path — sebelum `const invoiceData = {`, di dalam mutex:

```javascript
				// Daily per-outlet queue number, stamped before the draft save so
				// it rides the invoice everywhere (server print, history, sync).
				let queueStamp = null
				if (
					posSettingsStore.queueEnabled &&
					shiftStore.profileCompany &&
					targetDoctype === "Sales Invoice"
				) {
					try {
						queueStamp = await acquireQueueNumber({
							company: shiftStore.profileCompany,
							posProfile: posProfile.value,
							offline: offlineState.isOffline,
						})
					} catch (err) {
						log.warn("queue number unavailable, printing without it:", err)
					}
				}
```

lalu setelah `discount_confirmation_code` di `invoiceData`: `...(queueStamp ? { pos_queue_number: queueStamp.queue_number, pos_queue_date: queueStamp.date } : {}),`. Pastikan store yang benar di-import di file itu (cek nama variabel store yang dipakai useInvoice.js — sesuaikan `posSettingsStore`/`shiftStore` dengan yang ada).
  - `POSSale.vue` offline path — sebelum `const invoiceData = {` (baris ~2204), blok sama dengan `offlineStore.isOffline === true` (path ini memang offline); stamp ke `invoiceData` dan `offlinePrintDoc`.
- [ ] **Step 4: Run test + build** `yarn vitest run src/utils/printInvoice.test.js && yarn build` — PASS/build ok.
- [ ] **Step 5: Commit** `feat(pos): stamp queue number at checkout`.

### Task 13: Blok antrian di tiga template print

**Files:**
- Modify: `POS/src/utils/printInvoice.js:210-215` (`buildReceiptHTML` top) — blok sebelum `<div class="header">`
- Modify: `POS/src/utils/print/crew_slip.js:94-135` — blok sebelum `slip-title`
- Modify: `pos_next/pos_next/print_format/pos_next_receipt/pos_next_receipt.json` — blok paling atas body + **bump `modified`**
- Test: `POS/src/utils/printInvoice.test.js`, `POS/src/utils/print/crew_slip.test.js`

**Interfaces:**
- Consumes: `doc.pos_queue_number` (+ `formatQueueNumber` Task 10).
- Produces: render blok `NO. ANTRIAN` + angka besar hanya bila field terisi.

- [ ] **Step 1: Failing tests**:

```javascript
	// printInvoice.test.js
	it("renders the queue block first when pos_queue_number is set", () => {
		const html = buildReceiptHTML({ ...baseInvoice, pos_queue_number: 48 })
		const receiptStart = html.indexOf('class="receipt"')
		const queueStart = html.indexOf('class="queue-number"')
		expect(queueStart).toBeGreaterThan(-1)
		expect(queueStart).toBeGreaterThan(receiptStart)
		expect(html.indexOf('class="header"')).toBeGreaterThan(queueStart)
		expect(html).toContain(">048<")
	})

	it("renders no queue block without a number", () => {
		expect(buildReceiptHTML(baseInvoice)).not.toContain("queue-number")
	})
```

dan di `crew_slip.test.js`: slip dengan `pos_queue_number: 48` mengandung `>048<` sebelum `slip-title`; tanpa nomor → tidak ada.
- [ ] **Step 2: Run** — FAIL.
- [ ] **Step 3: Implement client templates**. Di `buildReceiptHTML`, tepat setelah `<div class="receipt">`:

```javascript
				${
					invoiceData.pos_queue_number
						? `<div class="queue-number"><div class="queue-label">${__(
								"NO. ANTRIAN",
							)}</div><div class="queue-value">${formatQueueNumber(
								invoiceData.pos_queue_number,
							)}</div></div>`
						: ""
				}
```

CSS (di `receiptStylesFor`, setelah `.header` rule): `.queue-number { text-align: center; margin-bottom: 8px; border-bottom: 2px dashed #000; padding-bottom: 4px; } .queue-label { font-size: 11px; } .queue-value { font-size: 26px; font-weight: bold; }`. Di `buildCrewSlipHTML`, sebelum `title`:

```javascript
	const queueBlock =
		doc.pos_queue_number != null && doc.pos_queue_number !== ""
			? `<div class="slip-queue"><div class="slip-queue-label">${__(
					"NO. ANTRIAN",
				)}</div><div class="slip-queue-value">${formatQueueNumber(
					doc.pos_queue_number,
				)}</div></div>`
			: ""
```

(urutan body: `queueBlock + title + ...`; CSS slip: `.slip-queue { text-align: center; margin: 2px 0 6px; } .slip-queue-label { font-size: 11px; } .slip-queue-value { font-size: 24px; font-weight: bold; }`).
- [ ] **Step 4: Server print format** — `pos_next_receipt.json`: setelah `{%- set cashier_name ... -%}` dan sebelum `<!-- Header -->`, sisipkan:

```jinja
{%- if doc.pos_queue_number -%}<div class="text-center" style="margin-bottom: 4px;"><div style="font-size: 11px;">NO. ANTRIAN</div><div style="font-size: 26px; font-weight: bold;">{{ "%03d" % doc.pos_queue_number if doc.pos_queue_number < 1000 else doc.pos_queue_number }}</div></div><div class="divider" style="margin-top: 4px; margin-bottom: 4px;"></div>{%- endif -%}
```

Ubah key `"modified"` di JSON ke `"2026-09-06 18:30:00.000000"` (lebih baru dari DB). Migrate kedua site.
- [ ] **Step 5: Run FE tests** — PASS. Verify server template via console: `frappe.www.printview.get_html_and_style`-style call atau `bench --site posnext.localhost execute frappe.utils.jinja ...` — paling sederhana: buat invoice draft bernomor via console dan render `frappe.get_print("Sales Invoice", name, "POS Next Receipt")`, assert `"NO. ANTRIAN" in html`.
- [ ] **Step 6: Commit** `feat(print): queue number block on receipt and crew slip`.

### Task 14: Rollout & verifikasi akhir

**Files:** none (eksekusi).

- [ ] **Step 1**: Full BE suite serial: `./env/bin/python apps/pos_next/pos_next/_pn_run_tests.py pos_next.api.test_printing pos_next.api.test_queue pos_next.pos_next.utils.tests.test_pos_closing_print` — PASS.
- [ ] **Step 2**: Full FE suite: `cd apps/pos_next/POS && yarn test:run` — PASS.
- [ ] **Step 3**: `yarn build`.
- [ ] **Step 4**: Migrate kedua site + clear-cache.
- [ ] **Step 5**: Manual di browser `http://posnext.localhost:8001/pos`: (a) aktifkan `enable_pos_queue` di Company outlet, (b) transaksi → struk iMin / preview menampilkan NO. ANTRIAN di paling atas + crew slip, (c) devtools offline → transaksi → nomor lanjut lokal, (d) Direct Print: toggle Crew Slip On → Test Print = 1 receipt + 1 crew.
- [ ] **Step 6**: Push: `git push origin main`.

## Self-Review (done)

- Spec coverage: cakupan counter (T7-8), offline+sync healing (T9-10), checkout stamping (T12), 3 template print posisi atas (T13), toggle company (T7+T11), format 3-digit (T10/T13). Crew: knob server (T1), resolver (T2), plumbing (T3), driver (T4), preview (T5), UI (T6).
- Type konsisten: `crew_slip_enabled` (API) → `crew_slip_enabled` (transport key) → `crewSlipEnabled` (driver/resolver/device) — dicek silang T1/T2/T3/T6. `pos_queue_number`/`pos_queue_date` konsisten T7/T9/T12/T13. `acquireQueueNumber({company, posProfile, offline})` konsisten T10/T12.
- Catatan implementasi T9: gunakan UPDATE GREATEST (bukan set_value) agar counter tak pernah turun.
