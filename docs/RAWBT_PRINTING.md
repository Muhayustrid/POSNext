# Cetak lewat RawBT (alternatif driver iMin)

Driver iMin bergantung pada servis print iMin di `ws://127.0.0.1:8081` (plugin
iMinprinterplugin). Kalau servis itu tidak ada, versinya beda, atau Chrome
memblokir akses ke jaringan lokal, device tidak bisa mencetak. RawBT adalah
jalur kedua yang tidak memakai servis itu. Driver iMin tetap ada dan tetap
menjadi default.

## Cara kerja

Mode cetak memakai setelan yang sama dengan iMin: POS Settings → **Mode cetak**
(struk) dan **Mode cetak EOD** (`imin_print_mode` / `imin_eod_print_mode`).

| Mode | Isi job | Ukuran | Catatan |
| --- | --- | --- | --- |
| **Teks (cepat)**, disarankan | Teks ESC/POS dengan font bawaan printer (font A 12x24 dot: 32 kolom di 58mm, 48 kolom di 80mm). Layout-nya sama dengan mode teks iMin (`receipt_text.js`). | Beberapa KB | Tanpa html2canvas. Hanya logo kecil yang dikirim sebagai gambar. Knob skala font tidak berlaku; line spacing serta margin kiri/atas tetap berlaku. Huruf non-ASCII dilipat (é → e), sisanya jadi `?`. |
| Gambar | Struk dirender jadi bitmap (renderer yang sama dengan mode gambar iMin), lalu dikodekan ke raster `GS v 0`. | Puluhan KB per lembar | Tampilan persis format cetak, tapi berat. |

Setiap lembar (salinan + crew slip) diakhiri feed dan cut (kalau
`imin_cut_paper` aktif), dan semuanya dikirim dalam satu job. Mode teks butuh
data dokumen. Cetakan tanpa dokumen (misalnya fallback HTML server) tetap
memakai gambar, sama seperti aturan di iMin.

Job ESC/POS diserahkan ke aplikasi RawBT (`ru.a402d.rawbtprinter`). RawBT yang
meneruskan ke printer yang dipilih di aplikasinya: printer bawaan (kalau
modelnya didukung), Bluetooth, USB, atau LAN.

Ada dua mode pengiriman (diatur per device):

| Mode | Cara | Catatan |
| --- | --- | --- |
| Intent Android (default) | `intent:base64,...#Intent;scheme=rawbt;package=ru.a402d.rawbtprinter;end;` | Tanpa jaringan, jadi tidak kena izin "Local network access" Chrome. RawBT muncul sebentar lalu mencetak. Hanya untuk Android. |
| Websocket server | `ws://127.0.0.1:40213/` | Butuh aplikasi "RawBT websocket server" (RawBT 7+). Tidak pindah aplikasi, tapi tetap koneksi loopback, jadi izin Chrome berlaku. |

Batasan:
- Jeda sobek antar lembar (Copy delay / Crew delay) hanya ada di mode websocket:
  tiap lembar dikirim terpisah, lalu POS menunggu perkiraan waktu cetak
  (Print speed) ditambah delay-nya. Mode intent mengirim semua lembar sekaligus
  tanpa jeda (tiap intent berarti pindah aplikasi), jadi aktifkan cut kalau
  mencetak lebih dari satu lembar.
- Mode intent bersifat fire-and-forget. POS tidak tahu apakah kertas benar-benar
  keluar, jadi `POS Print Log` mencatat `Success` begitu intent dikirim.
- Chrome bisa memblokir intent yang tidak dipicu oleh tap (misalnya auto-print
  yang muncul jauh setelah tombol bayar ditekan). Kalau auto-print tidak jalan
  tapi Test Print jalan, pakai mode websocket.

## Setup di device

1. Pasang **RawBT** dari Play Store. Di aplikasinya, pilih printer
   (bawaan/Bluetooth/USB/LAN), lebar kertas 58/80 mm, lalu coba test print dari RawBT.
2. (Opsional) Pasang **RawBT websocket server** kalau ingin memakai mode websocket.
3. Di POSNext buka `/pos/direct-print`. Di kartu **Device**:
   - **Printer di perangkat ini** → `RawBT`. Hanya device ini yang pindah;
     kasir lain tetap memakai driver dari Profil POS.
   - **Koneksi RawBT** → `Intent Android` (atau `Server websocket`).
   - Simpan, lalu tekan **Test Print**.
4. Muat ulang halaman POS supaya ikon printer di header ikut berubah.

Supaya semua kasir di satu profil memakai RawBT, pilih `RawBT (Android)` di
POS Settings → Driver Cetak. Knob kertas/salinan/feed/cut/margin memakai field
`imin_*` yang sama.

## Fallback

- `rawbt` → `browser` (kalau fallback aktif).
- Chain iMin (`imin → qz → browser`) **tidak** turun ke RawBT. Di device tanpa
  RawBT, intent akan membuka Play Store di tengah transaksi.
