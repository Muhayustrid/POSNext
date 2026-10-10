# POS Reject Request — Design

Status: disetujui user 2026-10-11 (sesi grilling). Stack: ERPNext v16 + `pos_next`.

## Tujuan
Kru outlet (kasir, non-kasir, pengelola) melaporkan barang reject ke Head Office (HO).
HO memutuskan siapa yang menanggung. Barang yang di-acc dimusnahkan (roti — tidak ditukar).
Sistem harus mencegah kru memanipulasi laporan.

## Keputusan
| # | Keputusan |
|---|---|
| D1 | Satu DocType generik `POS Reject Request` (+ child `POS Reject Request Item`). Field `purpose`: `Unsold`, `Production Loss`. Purpose lain ditambah nanti. |
| D2 | Pengaju: role yang sudah ada `POSNext Cashier` / `POSNext Manager`. Approver: role baru `POSNext Reject Approver`. |
| D3 | Pengaju ≠ pemutus (owner dokumen tidak boleh memutus). |
| D4 | Bukti wajib: alasan + 1–5 foto (File terlampir). Video opsional berupa link http(s) (YouTube/Drive). |
| D5 | `Unsold`: saat submit, stok langsung pindah (Material Transfer) dari gudang outlet ke gudang reject outlet → tidak bisa dijual. |
| D6 | `Production Loss`: kehilangan sudah tercatat native (process loss Work Order), jadi tanpa gerakan stok. Draft dibuat **otomatis** oleh sistem saat produksi selesai/ditutup dengan output < rencana. Qty diambil dari Work Order, tidak bisa diubah kru. |
| D7 | Tenggat = `incident_date` + N hari. N global di settings, override khusus Production Loss (0 = pakai global). |
| D8 | Mode lewat tenggat (settings): `Lenient` (default) = masuk antrean HO dengan flag `is_late`; `Strict` = sistem langsung memutus `Charge Crew`. |
| D9 | Keputusan HO: `Write Off` (beban pabrik/HO), `Charge Crew` (beban kru, catatan wajib), `Rejected` (catatan wajib; hanya Unsold — stok dikembalikan ke gudang outlet). |
| D10 | Setelah `Write Off`/`Charge Crew`: kru wajib unggah foto bukti pemusnahan, baru bisa `Closed`. Saat Close, Unsold → Material Issue dari gudang reject ke Stock Adjustment Account. |
| D11 | Batas nilai reject per outlet per bulan (settings, 0 = off). Lewat batas → flag `over_limit` (tetap bisa diajukan, HO melihat). |
| D12 | Audit: `track_changes` + komentar timeline untuk setiap aksi. |
| D13 | Notifikasi: in-app (Notification Log) + email. WhatsApp setelah MVP. |
| D14 | Beban kru hanya dicatat (laporan). Potong gaji/jurnal piutang kru setelah MVP. |
| D15 | UI: pengajuan & bukti pemusnahan di Vue POS; antrean & keputusan HO di Desk ERPNext. |

## Status
`Draft` → (submit) `Pending` → (keputusan) `Approved` / `Rejected` → (bukti pemusnahan) `Closed`. `Cancelled` hanya dari `Pending`.
`Rejected` adalah status akhir.
