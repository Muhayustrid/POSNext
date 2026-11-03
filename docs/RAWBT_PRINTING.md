# Cetak lewat RawBT (alternatif driver iMin)

Driver iMin bergantung pada servis print iMin di `ws://127.0.0.1:8081` (plugin
iMinprinterplugin). Kalau servis itu tidak ada, versinya beda, atau Chrome
memblokir akses ke jaringan lokal, device tidak bisa mencetak. RawBT adalah
jalur kedua yang tidak memakai servis itu. Driver iMin tetap ada dan tetap
menjadi default.

## Cara kerja

- Struk dirender jadi bitmap dengan renderer yang sama dengan mode gambar iMin,
  jadi hasil cetaknya sama.
- Bitmap dikodekan ke ESC/POS (`GS v 0` raster), lalu ditambah feed dan cut
  (kalau `imin_cut_paper` aktif). Semua lembar (salinan + crew slip) dikirim
  dalam satu job.
- Bitmap diserahkan ke aplikasi RawBT (`ru.a402d.rawbtprinter`). RawBT yang
  meneruskan ke printer yang dipilih di aplikasinya: printer bawaan (kalau
  modelnya didukung), Bluetooth, USB, atau LAN.

Ada dua mode pengiriman (diatur per device):

| Mode | Cara | Catatan |
| --- | --- | --- |
| Intent Android (default) | `intent:base64,...#Intent;scheme=rawbt;package=ru.a402d.rawbtprinter;end;` | Tanpa jaringan, jadi tidak kena izin "Local network access" Chrome. RawBT muncul sebentar lalu mencetak. Hanya untuk Android. |
| Websocket server | `ws://127.0.0.1:40213/` | Butuh aplikasi "RawBT websocket server" (RawBT 7+). Tidak pindah aplikasi, tapi tetap koneksi loopback, jadi izin Chrome berlaku. |

Batasan:
- Mode teks iMin tidak berlaku. RawBT selalu mencetak gambar.
- Tidak ada jeda sobek antar salinan. Aktifkan cut kalau mencetak lebih dari satu lembar.
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
