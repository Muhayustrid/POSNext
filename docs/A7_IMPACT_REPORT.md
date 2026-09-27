# Laporan Dampak A7 — Audit Data Pra-deploy (READ-ONLY)

Item A7 (sesi 27 Sep) membuat script audit `pos_next/audit.py` yang murni
membaca (satu query UNION ALL + satu loop `get_value`, tanpa
INSERT/UPDATE/DELETE). Dokumen ini adalah laporannya: apa yang diukur, angka
situs uji, interpretasinya, dan prosedurnya di produksi.

## Apa yang diukur

**(a) Diskon tak terjelaskan** — invoice submitted (POS Invoice + Sales
Invoice `is_pos=1`) yang punya baris item `rate < price_list_rate` TANPA
atribusi `pos_offer_item_rules`. Pola inilah yang kini ditolak server oleh
gate SEC-04/SEC-23 saat submit/replay — sebelum fix-nya, baris seperti ini
bisa lahir senyap dari klien yang memanipulasi payload.

**(b) Referensi wallet rusak** — Wallet Transaction `docstatus=1` yang
mereferensi invoice cancelled (docstatus 2) atau invoice yang sudah tidak
ada. Pola inilah yang kini dicegah oleh fix A1
(`cancel_wallet_transactions_for_invoice`): cancel invoice ikut membatalkan
WT ter-link dalam satu transaksi.

## Angka situs uji `posnext.localhost` (27 Sep 2026)

```
(a) Diskon tak terjelaskan  : 1 baris item di 1 invoice, total diskon 1800.00
    - Sales Invoice SINV-OT2601, item CR001, rate 16.200 vs
      price_list_rate 18.000 (bukan return, tanpa atribusi offer)
(b) Referensi wallet rusak  : 17 bermasalah dari 22 WT dicek
    (missing=0, cancelled=17; antara lain duplikasi Loyalty Credit pada
     ACC-PSINV-2026-00464 dan ACC-PSINV-2026-00460)
```

## Interpretasi

1. **Kedua pola adalah anomali historis pra-fix.** Angka ini menunjukkan
   lubang yang kini tertutup memang pernah dimanfaatkan/terjadi di data
   nyata (situs uji dipakai E2E + manipulasi replay).
2. **Tidak ada indikasi pola baru pasca-fix.** Seluruh temuan (a) dan (b)
   bertanggal sebelum remediasi; gate baru menolak pola (a) di titik submit,
   dan fix A1 mencegah pola (b) lahir baru.
3. **Data legacy tidak diubah otomatis.** Script sengaja read-only —
   pembersihan 17 WT orphan legacy adalah keputusan data terpisah (manual,
   via Desk), bukan bagian deploy.

## Prosedur produksi (tertaut checklist §1 item 3)

1. **Pra-deploy** (sebelum `bench migrate`):
   `bench --site <site> execute pos_next.audit.run`
   → catat angka; ini **baseline legacy produksi**. Angka pembanding situs
   uji ada di atas (produksi diharapkan jauh lebih kecil atau 0).
2. **Pasca-deploy + go-live beberapa hari**: jalankan ulang
   → angka (a) dan (b) harus **tidak tumbuh** dari baseline. Pertumbuhan =
   red flag (gate tidak hidup / jalur lain belum tertutup).
3. Remediasi legacy (opsional, terpisah dari deploy): batalkan/hapus WT
   orphan hasil audit lewat Desk dengan jejak audit normal.
