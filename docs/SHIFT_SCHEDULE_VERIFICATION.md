# Laporan Verifikasi: Shift Terjadwal (POS Profile)

Dokumen verifikasi untuk fitur shift terjadwal. Referensi fitur:
[SHIFT_SCHEDULE.md](SHIFT_SCHEDULE.md) (EN) dan
[SHIFT_SCHEDULE_ID.md](SHIFT_SCHEDULE_ID.md) (Panduan HQ Bahasa Indonesia).

## 1. Tujuan Verifikasi

1. Memastikan pengaturan jadwal oleh HQ di POS Profile tervalidasi saat disimpan
   (jam mulai/selesai wajib, warning ≥ 0, grup harus satu perusahaan).
2. Memastikan penegakan berjalan di server: buka shift di luar jam ditolak,
   penjualan/pembayaran/refund ditolak setelah deadline, refresh/login ulang
   tidak melewati pembatasan, penutupan shift (hitung kas aktual) selalu boleh.
3. Memastikan jadwal OFF tidak mengubah perilaku sama sekali.
4. Menguji lewat GUI nyata (browser), bukan hanya unit test.
5. Memperbaiki celah yang ditemukan: test yang rusak, dialog closing kosong
   setelah refresh (F1), dan pesan error generik saat buka shift di luar jam.

## 2. Ringkasan Perubahan Kode

| Berkas | Perubahan |
|---|---|
| `POS/src/utils/apiWrapper.js` | Helper `serverErrorMessage()` — mengambil pesan asli hasil terjemahan server dari `error.messages` frappe-ui, bukan string generik `Error: <endpoint> ValidationError`. |
| `POS/src/components/ShiftOpeningDialog.vue` | 3 titik tampilan error memakai `serverErrorMessage(...)`. |
| `POS/src/utils/apiWrapper.test.js` | **Baru** — 5 test regresi untuk helper di atas. |
| `POS/src/components/ShiftClosingDialog.vue` | Perbaikan F1: saat dialog closing dipaksa-buka sebelum komponen selesai mount (setelah refresh di luar jam), data reconciliasi kini dimuat via `onMounted`. |
| `POS/src/components/ShiftClosingDialog.test.js` | Mock store posSync/posCart agar test tidak menyentuh offline worker. |
| `pos_next/test_shift_schedule.py` | Perbaikan 3 test yang rusak di frappe 16.32 + tambahan test: `extend_deadline` (hak akses, forward-only, audit Comment), `_gate_shifts` (shift milik kasir lain/tertutup/missing → fallback fail-closed), `assert_invoice_sales_allowed`, hook POS Profile & POS Profile Group, admit berdasarkan `creation`. |
| `docs/SHIFT_SCHEDULE.md` | Pemulihan resmi via API `extend_deadline` (bukan raw DB); tegas bahwa ini aksi teknis admin **tanpa UI khusus**. |
| `docs/SHIFT_SCHEDULE_ID.md` | **Baru** — Panduan HQ Bahasa Indonesia; catatan honesty yang sama. |

Total: 16 berkas diubah + 5 berkas baru (di luar folder bukti GUI).

## 3. Perintah Pengujian Persis

Semua perintah backend dijalankan di dalam container `erpnext16_dev-frappe-1`
(site `posnext.localhost`).

### 3.1 Unit test fitur (backend)

```
docker exec erpnext16_dev-frappe-1 bash -lc "cd frappe-bench && \
  ./env/bin/python apps/pos_next/pos_next/_pn_run_tests.py pos_next.test_shift_schedule"
```

Hasil akhir: **Ran 55 tests — OK** (kondisi awal sesi ini: 36 test, 3 error).

### 3.2 Baseline 14 modul (regresi backend)

```
docker exec erpnext16_dev-frappe-1 bash -lc "cd frappe-bench && \
  ./env/bin/python apps/pos_next/pos_next/_pn_run_tests.py \
  pos_next.api.test_credit_sales pos_next.api.test_customers \
  pos_next.api.test_discount_code pos_next.api.test_invoices_offer_relay \
  pos_next.api.test_invoices_strip_fields pos_next.api.test_offers \
  pos_next.api.test_pos_offer_usage pos_next.api.test_pos_offer_validation \
  pos_next.api.test_pricing_rule_cap pos_next.api.test_printing \
  pos_next.api.test_queue \
  pos_next.pos_next.doctype.pos_opening_shift.test_pos_opening_shift \
  pos_next.pos_next.doctype.pos_closing_shift.test_pos_closing_shift \
  pos_next.pos_next.doctype.pos_coupon.test_pos_coupon"
```

Hasil: **Ran 192 tests — FAILED (errors=27)**, dan **identik dengan HEAD
bersih** (dibuktikan dengan `git stash push -u` → jalankan baseline yang sama →
27 error yang sama → `git stash pop`). Rincian 27 error:

- `pos_next.api.test_discount_code` — 19 error (`TestValidateCode` 11,
  `TestRecordCodeUsageOnSubmit` 6, `TestValidateInvoiceDiscounts` 2).
- `pos_next.api.test_pos_offer_usage` — 8 error (`TestCheckQuota` 3,
  `TestGetQuotaInfo` 3, `TestSubmitAndCancel` 2).

Penyebab: ketidakcocokan mock dengan frappe 16.32 (`'Meta' object has no
attribute 'istable'`, stub `frappe.utils.today`), semua di modul test
discount_code/pos_offer_usage — **tidak ada hubungannya dengan fitur shift**;
tidak diperbaiki di luar cakupan. Catatan tambahan: `pos_next.api.test_packages`
gagal 5 di `setUpClass` karena data site dev (Mode of Payment "Bank Draft"
belum punya akun default) — juga pra-ada.

### 3.3 Frontend

```
cd POS && npx vitest run
```

Hasil akhir: **310 passed (310)** — 305 lama + 5 test regresi baru
(`apiWrapper.test.js`). Kondisi awal sesi ini: 6 gagal (dialog menarik offline
worker ke test env) — diperbaiki dengan mock store.

### 3.4 Build

```
cd POS && npm run build
```

Hasil: sukses (PWA precache 66 entries). Setelah perbaikan F1 dan perbaikan UX
error, build diulang + `bench --site posnext.localhost clear-cache` (HTML
`/pos/` lama ternyata ter-cache di redis — penting saat memverifikasi bundle
baru).

### 3.5 Pemeriksaan diff

```
git diff --check        # bersih, tanpa error whitespace
git stash list          # kosong — latihan baseline stash sudah dipulihkan penuh
```

## 4. Bukti Pengujian GUI

Browser nyata (Playwright, Chromium headless), site
`http://posnext.localhost:8001/pos`, user QA terisolasi
`qa-schedule@posnext.local` (dinonaktifkan setelah tes), profil QA-SCHEDULE
(hanya profil QA yang disentuh). Semua bukti di
`gui-test-screenshots/` (path relatif terhadap root app):

| Titik uji | Hasil | Bukti |
|---|---|---|
| Buka shift di luar jendela (enforce ON) → ditolak server, tidak ada shift dibuat | PASS | `t2a_01_outside_before_open.png`, `t2a_02_outside_rejected.png` |
| Buka shift di dalam jendela → sukses, snapshot beku (deadline benar) | PASS | `t2b_02_shift_opened.png` (+ verifikasi DB) |
| Penjualan normal di dalam jendela (jadwal ON) → diterima | PASS | `t3p_04_after_submit.png` (ACC-SINV-2026-00073, Rp 2.000) |
| Toast peringatan 5 menit sebelum deadline (jam server-anchored) | PASS | `t3_02_warning_toast.png` |
| Setelah deadline: dialog closing dipaksa-buka, hitung kas aktual, submit sukses | PASS | `t4_01_forced_closing_dialog.png`, `t4_03_cash_counted.png`, `t4_08_closing_submitted.png` |
| Refresh halaman → enforcement berlaku ulang; Cancel pun dipaksa-buka ulang | PASS | `t5_01_after_refresh_forced.png` |
| F1: dialog kosong setelah refresh → diperbaiki → data reconciliasi termuat | PASS (setelah fix) | `f1_repro_empty_dialog.png` vs `f1_fix_dialog_loaded.png` |
| Jadwal OFF: buka shift di luar jendela → normal | PASS | `t6_01_open_outside_window_ok.png` |
| UX error buka di luar jam: pesan asli terjemahan server (Bahasa Indonesia), bukan string generik | PASS (setelah fix) | `ux_01_translated_error.png` — "21:51 berada di luar jam shift terjadwal (3:00:00 – 4:00:00). Shift tidak dapat dibuka sekarang." |
| Validasi pengaturan (jam kosong, warning negatif, grup beda perusahaan) | PASS (unit) | `test_shift_schedule.py` — `TestScheduleValidation`, `TestProfileGroupHooks` (GUI Desk diblokir, lihat §5) |
| Overnight (end < start, lintas tengah malam) | PASS (unit) | `test_shift_schedule.py` — `TestResolveWindow` + `TestApplyScheduleSnapshot` |

Catatan UX minor: pesan jam pada error backend ditampilkan sebagai "3:00:00"
(format backend), bukan "03:00".

## 5. Pemblokir Environment (pra-ada, tidak diperbaiki)

**Desk frappe v16.32 di site ini tidak bisa membuka form doctype mana pun lewat
URL** — `/desk/pos-profile/QA-SCHEDULE`, `/desk/customer/<nama>`, dll. semua
404 "Page pos-profile not found" (rute list seperti `/desk/pos-opening-shift`
berfungsi). Karena itu:

- Uji GUI "HQ mengatur jadwal lewat Desk" tidak bisa dijalankan; konfigurasi
  jadwal dilakukan via API sebagai persiapan environment, dan validasi
  save-nya diverifikasi lewat unit test (hook `validate_profile_schedule`).
- Ini diblokir sejak sebelum perubahan shift; **tidak diperbaiki** (di luar
  cakupan, berisiko menyentuh routing frappe).

## 6. Status Kebersihan Data (Cleanup)

Record QA yang dibuat dan statusnya setelah pembersihan:

| Record | Status |
|---|---|
| POS Closing Shift (uji T4 + repro F1) | Dihapus |
| POS Opening Shift POSA-OS-26-0000031 / -0032 | Cancelled (penghapusan penuh diblokir link audit standar; tanpa dampak bisnis) |
| Sales Invoice ACC-SINV-2026-00073 + GL Entry | Cancelled (audit trail ERPNext; penghapusan GL manual dihindari) |
| POS Print Log QA | Dihapus |
| User `qa-schedule@posnext.local` | **Disabled** (dibuat khusus QA; password sesi tidak disimpan di repo) |
| POS Profile `QA-SCHEDULE` | Tetap ada, jadwal OFF, tanpa shift aktif |

Dua open shift bisnis pra-ada (POSA-OS-26-0000026 milik Administrator,
POSA-OS-26-0000021 milik user demo lain beserta invoice demo-nya)
**tidak disentuh**.

## 7. Keamanan

- Sweep `gui-test-screenshots/*.py` dan `docs/`: password sesi hanya ada di
  `phase1_desk_setup.py` → **berkas dihapus** (skrip driver lain yang meng-import
  helper tersebut menjadi tidak runnable — memang throwaway). Hasil sweep
  akhir: 0 kemunculan kredensial.
- Tidak ada commit/push yang dilakukan.

## 8. Hasil Akhir

- Backend: 55/55 OK.
- Frontend: 310/310 OK.
- Build: sukses.
- `git diff --check`: bersih.
- Jumlah berkas berubah vs HEAD: 16 modified + 5 baru (kode/dok),
  plus folder bukti GUI `gui-test-screenshots/` (untracked).

## 9. Verifikasi Fase 2: Shift Group (Grup Shift) — 2026-09-07

Evolusi `POS Profile Group` (label user-facing: **Shift Group** / Grup Shift)
dari grouping-only menjadi grup jadwal: grup menyimpan jam shift + kontrol
warning/enforce + tabel anggota, dan **menyimpan grup otomatis menyinkronkan
semua profil anggota**.

### 9.1 Perubahan

| Berkas | Perubahan |
|---|---|
| `pos_next/pos_next/doctype/pos_profile_group/pos_profile_group.json` | + field jadwal (`pos_schedule_*`), + tabel `profiles`, label "Shift Group" |
| `pos_next/pos_next/doctype/pos_profile_group/pos_profile_group.py` | Controller: validasi anggota (duplikat, lintas perusahaan, reassignment, profil tak ada) + `sync_members()` (anggota baru/berubah di-save penuh; anggota dihapus di-unlink dengan mempertahankan jadwal terakhir; satu transaksi) |
| `pos_next/pos_next/doctype/pos_profile_group/pos_profile_group.js` | **Baru** — filter anggota per company + guard duplikat di grid |
| `pos_next/pos_next/doctype/pos_profile_group_member/` | **Baru** — child doctype tabel anggota |
| `pos_next/shift_schedule.py` | `validate_schedule_values(..., label=)` agar pesan error grup menyebut "Shift Group {nama}" |
| `pos_next/install.py` | Custom field POS Profile: label `Shift Group`, **default `pos_schedule_enabled` = 1 untuk profil BARU saja** (custom field default tidak menyentuh baris lama — profil bisnis lama tetap 0) |
| `pos_next/pos_next/workspace/posnext/posnext.json` | Shortcut + link kartu "Shift Group" |
| `pos_next/workspace_sidebar/posnext.json` | Item sidebar "Shift Group" |
| `pos_next/translations/id.csv` | Terjemahan baru (mis. "POS Profile Group" → "Grup Shift") |
| `pos_next/test_pos_profile_group.py` | **Baru** — 14 test unit |
| `docs/SHIFT_SCHEDULE.md`, `docs/SHIFT_SCHEDULE_ID.md` | Alur Shift Group + semantik penghapusan anggota |

### 9.2 Hasil pengujian

- Backend unit: `pos_next.test_pos_profile_group` + `pos_next.test_shift_schedule`
  → **Ran 68 tests — OK**.
- E2E di DB site (skrip terisolasi, hanya profil QA `QA-SCHEDULE`, semua grup QA
  dihapus setelahnya):
  - PASS anggota lintas perusahaan ditolak.
  - PASS grup aktif tanpa jam ditolak (pesan menyebut "Shift Group QA No Times").
  - PASS baris anggota duplikat ditolak.
  - PASS simpan grup menyinkronkan anggota (05:00–12:00, warn 15, enforce 1, link terisi).
  - PASS ubah jam grup → anggota ikut (06:00–14:00).
  - PASS reassignment ke grup kedua ditolak ("Remove it there first").
  - PASS hapus anggota → link kosong, jadwal terakhir dipertahankan.
  - PASS profil baru default `pos_schedule_enabled = 1`; profil lama tetap 0.
- Frontend vitest (`shiftSchedule`, `apiWrapper`): **15 passed**.
- Regresi baseline 14 modul: identik dengan sebelum perubahan (27 error
  pra-ada yang sama di test discount_code/pos_offer_usage, lihat §3.2).
- Migrate: `bench --site posnext.localhost migrate` sukses (dua kali — sekali
  setelah perbaikan `default` custom field dari int ke string "1"; frappe
  version-diff memformat field Data dan gagal pada int).
- Catatan: browser-use tidak tersedia dari sesi agent ini (tool error "Browser
  is not available in subagent"). Verifikasi UI dilakukan lewat payload HTTP
  yang benar-benar disajikan Desk: rute `/desk/pos-profile-group` dan
  `/desk/pos-profile/QA-SCHEDULE` → 200 (blokir 404 lama sudah tidak ada),
  payload workspace menyajikan shortcut "Shift Group" (shortcuts + content +
  link kartu), sidebar menyajikan item "Shift Group", dan `getdoctype`
  menyajikan field jadwal+tabel anggota beserta client JS (`meta.__js`
  berisi guard duplikat/filter company) — jadi tidak perlu build asset.
  Sidebar baru ter-serve setelah field `modified` di json di-bump (sync
  workspace_sidebar mengikuti aturan import berbasis timestamp).

## 10. Fase 2.1: Alur pembuatan profil + otoritas keanggotaan — 2026-09-07

### 10.1 Hasil penelusuran alur

SPA POS **tidak memiliki** alur create-profile: hanya 4 halaman
(DirectPrint, Home, Login, POSSale); komponen settings hanya memanggil
`get_pos_profiles` / `get_pos_profile_data` / `get_warehouse` /
`update_warehouse`. Tidak ada pemanggil `create_pos_profile` di frontend.
Alur pembuatan profil yang nyata: **form Desk** (`/desk/pos-profile/new` →
`frappe.desk.form.save`) dan API programatik
`pos_next.api.pos_profile.create_pos_profile`.

### 10.2 Perubahan

| Berkas | Perubahan |
|---|---|
| `pos_next/api/pos_profile.py` | `create_pos_profile`: field jadwal kini di-set eksplisit via `_resolve_schedule_params()` — param tidak ada/kosong = default **aktif** (tidak pernah menonaktifkan diam-diam); hanya `pos_schedule_enabled: 0` eksplisit yang mematikan. Jam divalidasi server saat insert (aktif tanpa jam → pesan jelas). Docstring API memuat param baru. |
| `pos_next/shift_schedule.py` | `validate_group_membership`: link `pos_profile_group` tanpa baris di tabel Anggota grup **ditolak** saat save profil — tabel Anggota adalah satu-satunya sumber keanggotaan; link hanya cerminan (bisa jadi "klaim palsu" yang tidak pernah disinkronkan grup berikutnya). |
| `pos_next/install.py` | Custom field `pos_profile_group` kini `read_only: 1` (diisi lewat tabel Anggota grup). |
| `pos_next/test_shift_schedule.py` | + `TestGroupMembershipAuthority` (4 test); hook test lama diperbarui. |
| `pos_next/test_pos_profile_group.py` | + `TestCreateProfileScheduleParams` (4 test) + assert `read_only`. |
| `pos_next/translations/id.csv`, docs EN/ID | Terjemahan pesan baru + dokumentasi otoritas keanggotaan. |

Catatan: tidak ada perubahan source SPA, jadi `npm run build` tidak
diperlukan; vitest terfokus dijalankan sebagai sanity.

### 10.3 Hasil pengujian

- Unit: `test_pos_profile_group` + `test_shift_schedule` → **76 tests — OK**.
- Vitest terfokus (`shiftSchedule`, `apiWrapper`) → 15 passed.
- E2E di DB (isolasi profil/grup QA, semua dihapus setelahnya):
  - PASS Desk-style create dengan jam valid → aktif 05:00–12:00.
  - PASS Desk-style create tanpa jam → ditolak jelas ("start/end times are missing").
  - PASS link grup langsung tanpa baris anggota → ditolak ("not in the Members table").
  - PASS jalur keanggotaan resmi (tambah via tabel Anggota) → link + jam tersinkron.
  - PASS API create tanpa jam → ditolak jelas (tidak dibuat, tidak di-disable diam-diam).
  - PASS API create dengan jam valid → dibuat aktif.
  - PASS API `pos_schedule_enabled: 0` eksplisit → dibuat nonaktif (opt-out sadar).
  - PASS API `pos_schedule_enabled: ""` → tetap aktif (bukan jalan pintas menonaktifkan).
  - PASS API jendela tidak valid (`start: "garbage"`) → ditolak jelas.
- Migrate sukses; `read_only=1` terverifikasi di Custom Field.
