# Checklist Deploy — Branch `security-audit-fixes` (grup remediasi audit 1-6)

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
3. **Kalau ada outlet non-IDR**: sadari bahwa UI dan struk kini menampilkan
   2 desimal untuk mata uang non-IDR (IDR tetap 0). Ini perbaikan yang disengaja,
   tetapi tampilan berubah.
4. **Pastikan site config production TIDAK punya** `allow_settings_reclaim: 1`
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
