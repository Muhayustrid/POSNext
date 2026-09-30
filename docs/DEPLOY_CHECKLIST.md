# Checklist Deploy — pos_next v2.13.0 (per 30 Sep 2026)

Checklist deploy yang hidup untuk kondisi `main` saat ini. Checklist
bersejarah (remediasi audit branch `security-audit-fixes`) tetap ada di
`DEPLOY_CHECKLIST_SECURITY_AUDIT_FIXES.md`; referensi `PROJECT_STATE.md`
yang lama mengarah ke sini.

## 0. Pra-deploy

- [ ] Gerbang branch: `main == origin/main` (gap `develop` by design — jangan disinkronkan).
- [ ] Fix batch audit (batch-1 P1 + batch-2 P2/P3) sudah di-commit ke `main` oleh sesi orchestrator (working tree 30 Sep belum di-commit — LARANG commit tanpa perintah user).
- [ ] `npm --prefix POS run test:run` dan `npm --prefix POS run build` hijau di host.

## 1. Deploy (Frappe Cloud)

1. Deploy `main` → `bench build` (FC auto-build SPA via root `package.json` — **verifikasi bundle baru**: `version.json` / hash `index-*.js` berubah).
2. `bench migrate` (19 patch incl. v2_13_0 ×2; backup/restore posnext.json gotcha lama).
3. Restart.

## 2. Pasca-migrate

- [ ] Index DDL: `after_install`/`after_migrate` kini MEMBUAT sendiri `ft_item_pos_search` (tabItem) dan `payment_entry_reference_name_doctype_idx` (tabPayment Entry Reference) — verifikasi opsional tapi disarankan:
  ```sql
  SELECT index_name FROM information_schema.STATISTICS
  WHERE table_schema = DATABASE()
    AND ((table_name='tabItem' AND index_name='ft_item_pos_search')
      OR (table_name='tabPayment Entry Reference' AND index_name='payment_entry_reference_name_doctype_idx'));
  ```
  (Catatan MariaDB: bila index baru dibuat di tabel dengan sejarah FTS kotor, satu `OPTIMIZE TABLE` menuntaskan index kosong — kasus langka.)
- [ ] Search item ≥3 char mengembalikan hasil (jalur FULLTEXT; tanpa index pun kini fallback LIKE — jangan error).
- [ ] Allowed Locales = `{"en","id"}`.
- [ ] Flag Stock Settings `enable_serial_and_batch_no_for_item` untuk outlet yang memakai batch.
- [ ] Role persona (POSNext Cashier / POSNext Manager) ter-import via fixture.
- [ ] Smoke kasir: buka shift → jual 1 item → Paid → retur → tutup shift seimbang.

## 3. Rollback

- Kembali ke commit sebelumnya + `bench migrate` (patch idempotent; index DDL ber-guard — aman diulang).
