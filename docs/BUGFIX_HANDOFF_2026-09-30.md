# HANDOFF PERBAIKAN BUG — Audit Menyeluruh 30 September 2026

> Dokumen untuk **sesi agen AI berikutnya** yang akan memperbaiki bug.
> Sumber: audit pengujian menyeluruh 30 Sep 2026 (backend sweep + vitest + GUI browser + smoke keamanan).
> Status audit: SELESAI, laporan penuh tersampaikan ke user. **Belum ada perbaikan yang dieksekusi.**
> Bukti visual: `gui-test-screenshots/audit_*.png` (relative ke root app).

---

## 0. Aturan main untuk agen perbaikan (WAJIB dibaca)

1. **JANGAN commit/push tanpa perintah eksplisit user** (larangan tetap user sejak 22 Sep). Selesaikan + verifikasi, lalu laporkan dan tunggu.
2. **Pengujian print Dikecualikan** — printer tidak ada di perangkat POS. Jangan tambah asumsi pengujian print; saat verifikasi gunakan perintah yang mengecualikan print (lihat §6).
3. **File test baru** `pos_next/api/test_auth_localization.py` (17 test, sudah lulus 2×) **belum di-commit** — ikutkan dalam commit perbaikan, jangan dihapus/diubah perilakunya.
4. Tidak boleh `biome check --write` repo-wide (bug upstream #4574 pada POSSale.vue; kebiasaan format akan berubah massal).
5. Uji di situs `roti-posnext-test.localhost:8001` (bundle build); runner backend di site `posnext.localhost`. Frontend test/build di HOST, backend di container (lihat §6).
6. Saat menguji GUI di webview IAB: **setelah menutup dialog apa pun, reload halaman dulu** sebelum aksi berikutnya — ini akibat Bug A di bawah. frappe-ui `<Input>` di bawah otomasi wajib native setter + `dispatchEvent('input'/'change')`.
7. Jangan jalankan `bench build` di container (OOM → MariaDB mati).

---

## Ringkasan prioritas

| ID | Judul | Keparahan | Estimasi ukuran fix |
|---|---|---|---|
| **A** | Ghost dialog: dialog tertutup tetap meneruskan klik → UI beku | **Tinggi** (perangkat target) | Kecil (CSS 1–2 baris) + opsi penguatan |
| **B** | Versi app 2.15.0 ≠ patch v2_16_0 → gate release hygiene merah | Rendah (menahan rilis) | 1 perintah script |
| **C** | Kedip katalog tak-terfilter ±2–3 dtk pasca-reload | Rendah–Sedang | Sedang (boot order) |
| **D** | Kolom TIME kosong di dialog Tutup Shift | Rendah | 1 baris backend |
| **E** | Badge jumlah draf ≠ daftar draf (scope beda) | Rendah | Sedang + keputusan produk |
| **F** | Drift konfigurasi akun uji (user_type & role & dokumentasi password) | Rendah (data/dokumentasi) | Konfigurasi |
| G | Kontensi "branding validation 1020" mengotori Error Log | Kosmetik | Kecil (retry/backoff) |
| H | Kosmetik lain (chip profil terpotong, durasi "0m", input qty tak snap-back) | Kosmetik | Opsional |

Urutan eksekusi yang disarankan: **B** (paling murah, membuka gate) → **A** (paling berdampak) → **D** → **C** → **E** → **F** → G/H bila diizinkan.

---

## A. Ghost dialog — dialog tertutup masih meneruskan klik (UI beku)

- **Keparahan**: Tinggi di perangkat POS (Android WebView — keluarga webview yang sama dengan stall rAF yang sudah pernah dimitigasi di `fc7cd4b`). Di desktop Chrome normal tidak terjadi; di webview IAB uji terbukti 100% reproduksi.
- **Gejala**: SETIAP dialog yang ditutup (konfirmasi overpayment, dialog sukses invoice, konfirmasi Clear Cart, dialog resume shift, dst.) meninggalkan DOM `data-state="closed"` yang masih tergambar (opacity 1, hit-testable, `pointer-events: auto`, z-index 500) dan menutupi layar. Klik apa pun setelahnya "tidak merespons". Karena hampir semua interaksi kasir lewat dialog, app praktis macet sampai reload.
- **Repro terverifikasi** (bukti: `audit_bug_ghost_dialog_overpay.png`, `audit_t5_hasil_submit.png`):
  1. Checkout → bayar "Pas" → ketik 50.000 → Add → muncul "Large Overpayment" → klik Cancel.
  2. Klik tombol "Complete Payment" (atau apa pun) → tidak terjadi apa-apa.
  3. DOM: dialog overpay `data-state="closed"`, `animation: dialog-content-out-3428e46c` berstatus `running` ber-menit-menit (seharusnya 0,15 dtk), opacity tetap 1. Kompositor memudarkan visualnya, tapi event `animationend` tidak pernah fire → Vue `<Transition>` tidak pernah melepas elemen → ghost tetap meneruskan klik.
  - Repro alternatif yang lebih pendek: submit pembayaran sukses → tutup dialog "Invoice Created Successfully" → coba klik tombol mana pun.
- **Akar**: app mendefinisikan leave-animation dialog sendiri di `POS/src/index.css` (baris ~117–127: `.dialog-leave-active { animation: dialog-overlay-out 150ms }`, `.dialog-content-leave-active { animation: dialog-content-out 150ms }`; keyframes di bawahnya; ditambah commit `4165a7a`). Di webview yang stall, `animationend` tidak fire sehingga unmount tidak pernah selesai. Bukan bug frappe-ui/Radix.
- **Fix yang diusulkan** (urut preferensi):
  1. **Minimal (disarankan pertama)**: hilangkan leave-animation di `POS/src/index.css` — ganti rule `.dialog-content-leave-active`/`.dialog-leave-active` menjadi `animation: none;` (atau hapus kedua rule + keyframes `*-out`). Trade-off: dialog hilang tanpa fade-out — dapat diterima; guaranteed unmount.
  2. **Penguatan (opsional, tahap 2)**: fallback timer global — bila `@after-leave` tidak fire dalam ~300 ms setelah close, paksa unmount; atau pola `runFrameSafe` yang sudah ada (`POS/src/utils/lowEndOptimizations.js`) diperluas ke event animasi.
  3. Jangan coba "tunggu transitionend lebih lama" — event memang tidak pernah datang di webview target.
- **Verifikasi**:
  1. GUI: jalankan repro di atas di IAB/webview → setelah Cancel, semua tombol harus tetap hidup tanpa reload. Ulangi juga: tutup dialog sukses invoice, tutup konfirmasi Clear Cart, tutup dialog resume shift.
  2. DOM check read-only: setelah menutup dialog, `document.querySelectorAll('[role="dialog"][data-state="closed"]')` harus kosong (atau tidak hit-testable).
  3. Pastikan vitest + build masih hijau (§6).
- **Catatan terkait (jangan dianggap bug terpisah)**: klik tombol "View Shift" saat shift terbuka selalu memunculkan dialog "Existing Shift Found" (resume / close & open new) alih-alih dasbor — dengan ghost dialog ini jadi loop. Setelah A diperbaiki, evaluasi ulang apakah perilaku ini masih mengganggu; bila iya, usulkan "View Shift" langsung ke dasbor saat shift milik sendiri sudah aktif (keputusan UX → tanya user dulu).

---

## B. Versi app tertinggal dari patch (gate release hygiene merah)

- **Keparahan**: Rendah secara teknis, tapi **memblocker** — sweep backend tidak hijau.
- **Bukti**: `pos_next.tests.test_release_hygiene.TestReleaseHygiene.test_app_version_not_below_latest_patch_series` gagal (satu-satunya kegagalan dari 943 test).
- **Fakta**: `pos_next/patches.txt:28` memuat `pos_next.patches.v2_16_0.move_company_target_fields_to_buying_tab`, sedangkan `pos_next/__init__.py:6`, `POS/package.json:4`, dan root `package.json:3` masih `"2.15.0"`.
- **Fix**: jalankan `scripts/version-bump.sh` untuk men-set **2.16.0** (script men-bump ketiga manifest sekaligus — jangan bump manual sebagian).
- **Verifikasi**: `./env/bin/python apps/pos_next/pos_next/_pn_run_tests.py pos_next.tests.test_release_hygiene` → OK; lalu sweep penuh hijau.

---

## C. Kedip katalog tak-terfilter saat boot

- **Keparahan**: Rendah–Sedang (kasir di jaringan lambat bisa menambah item di luar profilnya pada jendela ±2–3 detik; server tetap menghitung di checkout, tapi UX/data yang tampil salah).
- **Gejala**: setelah reload, katalog sempat menampilkan **413 item** semua profil (item pertama "Consulting Rp 1.234", paginasi "1–100 of 413") sebelum akhirnya turun ke 3 item milik profil `KASIR UJI`. Bukti: `audit_t9_dasbor_embedded.png` (dialog di depan menutupi, katalog di belakang terlihat 413 item).
- **Repro**: reload `/pos/` dan amati katalog sebelum boot tuntas (±2–3 dtk pertama).
- **Akar (perlu trace saat fix)**: daftar item di-fetch sebelum data POS Profile (item group filter) selesai dimuat — jalur boot di `POS/src/stores/itemSearch.js` / `POSSale.vue` / bootstrap store. Verifikasi dulu apakah fetch pertama memakai parameter profil kosong.
- **Fix yang diusulkan**: jangan render katalog sampai data profil siap (tampilkan skeleton/blank), atau tunda fetch items hingga `get_pos_profile_data` selesai.
- **Verifikasi**: reload dengan throttle jaringan lambat → item di luar profil tidak pernah terlihat.

---

## D. Kolom TIME kosong di dialog Tutup Shift

- **Keparahan**: Rendah (informasi hilang; regresi parsial dari fix `7b4fafe` yang hanya memperbaiki sisi frontend).
- **Gejala**: tabel "Invoice Details" di `ShiftClosingDialog.vue` menampilkan kolom TIME kosong, padahal `posting_time` ada di DB (mis. `ACC-PSINV-2026-00014` → 21:02:12).
- **Akar (terverifikasi)**: frontend merender `formatTime(invoice.posting_time)` (`POS/src/components/ShiftClosingDialog.vue:287` dan `:383`), tetapi payload dari server **tidak pernah memuat `posting_time`**: `_process_invoice()` di `pos_next/pos_next/doctype/pos_closing_shift/pos_closing_shift.py` membangun dict transaction (baris ~667–678) hanya dengan `posting_date`; varian early-return retur tanpa payment (baris ~653–664) juga tidak memuatnya. (Pengecualian `posting_time` di baris 148/826 hanya untuk append child table doc — bukan jalur payload SPA.)
- **Fix**: tambahkan `"posting_time": invoice.posting_time,` di dict transaction pada `_process_invoice` **dan** di dict early-return retur tanpa payment. `formatTime()` di `useFormatters.js:47` sudah mampu mem-parse "HH:MM:SS.ffffff".
- **Verifikasi**: buka dialog Tutup Shift di situs uji → kolom TIME menampilkan jam invoice; tambahkan/assert test backend yang mengecek payload `make_closing_shift_from_opening` memuat `posting_time`.

---

## E. Badge jumlah draf ≠ daftar draf (scope beda)

- **Keparahan**: Rendah (bingung kasir; perlu keputusan produk).
- **Gejala**: badge "Draft Invoices 1" di UserMenu/quick-action menghitung draf milik user LAIN di profil yang sama (draf `Administrator` bernama `ACC-PSINV-2026-00001` di profil `KASIR UJI`), sedangkan dialog Draft Invoices hanya menampilkan draf milik sendiri → badge bilang 1, daftar kosong.
- **Data faktanya**: `SELECT name, pos_profile, owner FROM \`tabPOS Invoice\` WHERE docstatus=0` → satu baris di atas.
- **Akar (perlu trace saat fix)**: perhitungan badge (kemungkinan `POS/src/stores/posDrafts.js`) memakai filter per-profil, sedangkan daftar dialog per-owner (+shift). Verifikasi dulu endpoint/field yang dipakai badge.
- **Fix — dua opsi, tanya user dulu**:
  1. Samakan badge dengan daftar (per-owner) — paling aman; atau
  2. Tampilkan juga draf lintas-user di daftar (perangkat shared, shift sama) — berarti fitur, bukan sekadar fix.
- **Verifikasi**: sebagai kasir uji, badge hanya menghitung draf miliknya (buat draf, reload, bandingkan badge vs daftar).

---

## F. Drift konfigurasi akun uji (vs resep resmi 30 Sep)

Fakta di situs `roti-posnext-test.localhost` (dari `tabUser` / `tabHas Role`):

| Akun | Faktanya | Seharusnya (resep PROJECT_STATE 30 Sep) |
|---|---|---|
| `kasir.uji@posnext.test` | `user_type = Website User` | **System User** |
| `hq.uji@posnext.test` | role hanya `Sales Manager` | Sales Manager **+ Accounts Manager** |
| `hq.uji@posnext.test` | password TIDAK sesuai dokumentasi — **sudah di-reset oleh audit ke `HQ#Uji#2026!`** (tercatat; ini perubahan data yang dilakukan sesi audit) | sesuai dokumentasi ✓ |

- **Fix**: set `kasir.uji` ke System User (jangan lupa re-login/clear cache — ada jebakan "Website User → 403 /desk" di PROJECT_STATE), tambahkan role Accounts Manager ke `hq.uji` (HQ = Sales Manager + Accounts Manager), sinkronkan dokumentasi bila resep berubah.
- **Catatan**: semua fungsi tetap bekerja dengan konfigurasi sekarang (SPA kasir OK, endpoint HQ OK — karena gate menerima Sales Manager saja), jadi ini konsistensi/dokumentasi, bukan kerusakan.

---

## G. Kontensi "branding validation 1020" mengotori Error Log

- **Gejala**: 3 entri `tabError Log` selama sesi audit: `Error validating branding: (1020, "Record has changed since last read in table 'tabSingles'; try restarting transaction")` — hanya muncul saat ada beban paralel (sweep test di site lain + GUI bersamaan).
- **Fix (opsional)**: retry/backoff 1× pada validasi branding, atau turunkan level logging untuk error ini (kode di `pos_next/api/branding.py` / `tasks/branding_monitor.py`).
- **Verifikasi**: jalankan beban paralel ringan → tidak ada entri baru di `tabError Log`.

---

## H. Kosmetik / observasi minor (opsional, kumpulkan keputusan user dulu)

1. **Chip profil terpotong** di header lebar sempit: "KASIR UJI" → "KA…" (terlihat di screenshot 125%; mungkin by-design truncate).
2. **Durasi "0m"** di header dialog Tutup Shift SETELAH submit sukses (sebelum submit "2h 27m") — durasi ikut terkalkulasi ulang pasca-close.
3. **Input qty tidak snap-back**: setelah toast stok menolak qty 9999, kolom input masih menampilkan "9999" padahal qty ter-commit 3 (totals sudah benar). Snap-back ke nilai ter-commit setelah reject akan lebih jujur.
4. Slot TIME pada tabel invoice dialog tutup shift juga terkait item D di atas.

---

## 6. Perintah verifikasi (gate) — salin-tempel

```bash
# BACKEND sweep penuh TANPA test_printing (excluded atas kebijakan user), serial, site runner
docker exec erpnext16_dev-redis-queue-1 redis-cli FLUSHDB   # bersihkan RQ dulu (residu memalsukan kegagalan)
docker exec erpnext16_dev-frappe-1 bash -c 'cd /workspace/development/frappe-bench/apps/pos_next && find pos_next -name "test_*.py" | sed "s/\.py$//; s#/#.#g" | grep -v "\.test_printing$" | sort' > /tmp/pn_modules.txt
docker exec -w /workspace/development/frappe-bench erpnext16_dev-frappe-1 \
  ./env/bin/python apps/pos_next/pos_next/_pn_run_tests.py $(cat /tmp/pn_modules.txt | tr '\n' ' ')
# Ekspektasi: semua OK (baseline audit: 943 test, skip 34; modul B harus hijau setelah fix)

# BACKEND modul tunggal (contoh)
docker exec -w /workspace/development/frappe-bench erpnext16_dev-frappe-1 \
  ./env/bin/python apps/pos_next/pos_next/_pn_run_tests.py pos_next.api.test_auth_localization

# FRONTEND (di HOST, di root app) — tanpa print
npm --prefix POS run test:run -- --exclude "**/print/**" --exclude "**/*print*" --exclude "**/*Print*" --exclude "**/*salesRecap*"
# Ekspektasi: 42 file / 355 test lulus

# BUILD
npm --prefix POS run build
```

Situasi DB dan akun uji: lihat `docs/PROJECT_STATE.md` §"Lingkungan & akun uji" + catatan drift di §F dokumen ini.

---

## 7. Definisi selesai (Definition of Done untuk sesi perbaikan)

1. Item A–D (minimal) dan item yang disetujui user (E/F/G/H) diperbaiki; **tanpa commit/push tanpa perintah**.
2. Gate §6 hijau sepenuhnya (backend sweep tanpa print, vitest non-print, build).
3. GUI repro masing-masing bug diverifikasi ulang di webview IAB (ingat aturan reload-setelah-dialog di §0.6 — setelah item A diperbaiki, aturan itu seharusnya tidak diperlukan lagi; kalau masih diperlukan, A belum tuntas).
4. File test baru `pos_next/api/test_auth_localization.py` ikut ter-commit bersama perbaikan (bila user memerintahkan commit).
5. Laporan hasil perbaikan kembali ke user: apa yang berubah, bukti verifikasi, sisa yang ditunda.
