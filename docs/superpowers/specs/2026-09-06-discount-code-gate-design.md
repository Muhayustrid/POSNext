# Discount Code Gate — konsep v2 (2026-09-06)

> Supersedes `2026-09-04-discount-restriction-design.md` (POS Discount Restriction rule system — dihapus).

## Latar belakang

POS Discount Restriction (jendela waktu, scope multi-company, kuota Global/Per Company, kode sekali pakai per item terdaftar) terbukti terlalu berat untuk kebutuhan aktual: yang dimiliki head office hanyah kendali atas **siapa yang boleh memberi diskon manual**. Konsep diganti menjadi satu gerbang sederhana: **setiap edit diskon manual di POS wajib kode akses dari HQ**.

## Keputusan desain (dikonfirmasi user)

1. **Gate selalu aktif** — tidak ada lagi rule, jendela waktu, scope company opt-in, atau kuota transaksi.
2. **Cakupan: diskon item + diskon keranjang** — edit diskon per item (persentase/nominal, atau edit rate manual di bawah `price_list_rate`) DAN additional discount di dialog pembayaran sama-sama wajib kode (menutup celah bypass lewat additional discount).
3. **Kode multi-use** — aktif sampai HQ menonaktifkannya (status `Active`/`Disabled`), bukan sekali pakai.
4. **Generate hanya head office** — `generate_codes` di controller `POS Discount Confirmation Code` dijaga permission `create` (doctype ini kini System Manager only). 8 karakter, alphabet tanpa `0O1IL`, 1–500 kode sekali generate, opsional terikat company (kosong = semua outlet) + notes.
5. **Audit di kode, bukan ledger** — `used_count`, `last_used_by`, `last_used_in_invoice`, `last_used_on` di-stamp saat submit (row-lock `for_update` agar increment aman race). Tidak ada doctype usage lagi.
6. **Penegakan**: Sales Invoice `validate` (draft save + submit) — invoice POS (`is_pos=1`, bukan return) yang punya diskon manual wajib membawa `discount_confirmation_code` yang Active dan cocok company-nya. `on_submit` re-check di bawah row lock. `on_cancel` tidak melepas apa pun (kode multi-use).
7. **Kasir tidak bisa bypass**: UI (EditItemDialog/PaymentDialog) meminta kode sebelum apply, server me-re-validasi di save/submit. API `get_status`/`validate_confirmation_code` hanya UX.

## Perubahan

- **Dihapus**: doctype `POS Discount Restriction` (+ Company/Item/Usage), `overrides/discount_restriction.py`, `api/discount_restriction.py` + test, custom field `pos_discount_restriction` di Sales Invoice, Desk JS `pos_discount_restriction.js`.
- **Dirombak**: `POS Discount Confirmation Code` (buang link restriction & field sekali-pakai; status Active/Disabled; section Usage audit; notes; perms System Manager only; `generate_codes` pindah ke sini).
- **Baru**: `overrides/discount_code.py`, `api/discount_code.py` + `api/test_discount_code.py`, patch `v2_3_0/remove_discount_restriction.py` (hapus doctype lama, bersihkan row kode lama, drop kolom basi), Desk JS `pos_discount_code.js`.
- **Frontend**: store `discountRestriction.js` disederhanakan (gate selalu on; `needsCodeForCart` = additional discount > 0 atau ada item berdiskon); EditItemDialog/PaymentDialog memakai gerbang yang sama; payload `discount_confirmation_code` tetap.

## Alur operasional

1. Kasir telepon/chat HQ minta izin diskon.
2. HQ buka Desk → POS Discount Confirmation Code → **Generate Codes** (qty, company opsional, notes) → bacakan kodenya.
3. Kasir edit diskon item / additional discount → dialog minta kode → kode di-validate live → apply.
4. Submit → server re-validasi + audit tercatat di kode.
5. Revoke kapan saja: HQ set status kode jadi `Disabled` → submit berikutnya ditolak.
