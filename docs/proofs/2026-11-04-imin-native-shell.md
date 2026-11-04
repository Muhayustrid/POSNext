# Catatan print — RawBT, escpos.js, driver imin2 + POSNext Shell

Tanggal: 2026-11-04. Branch: `claude/upbeat-bardeen-y5082q` (belum di-merge ke `main`).
Spec: `docs/superpowers/specs/2026-11-04-imin-native-shell.md`.

**Status: selesai di level kode, belum diuji di device.** Device iMin yang dipakai
sekarang sudah bisa mencetak dengan driver `imin` (SDK 1.0), jadi `imin2` dan APK
disimpan sebagai cadangan.

## Isi branch

| Commit | Isi |
|---|---|
| `b03968dd`, `a4edf138` | Driver `rawbt` (Intent Android / websocket), mode teks ESC/POS. Lihat `docs/RAWBT_PRINTING.md`. |
| `9ddc29b1` | Encoder ESC/POS pindah dari `rawbt_client.js` ke `POS/src/utils/print/escpos.js` (`buildEscPosPrint`). Perilaku RawBT tidak berubah. |
| `3192ad5a` | Driver `imin2` (`native_client.js`): kirim job ESC/POS ke aplikasi Android lewat `window.PosNextNative`. |
| `d378c09f` | Aplikasi `android-shell/` (Kotlin, WebView + iMin Printer SDK 2.0). |
| merge `main` | 3 commit stock opname / Stock Reader dari `main`; tanpa konflik. |

## Driver print

| Driver | Jalur | Fallback | Catatan |
|---|---|---|---|
| `imin` | Servis JS iMin `ws://127.0.0.1:8081` (SDK 1.0) | imin → browser | Dipakai di device sekarang. Kena izin Chrome "Jaringan lokal / Aplikasi di perangkat". |
| `rawbt` | Aplikasi RawBT (Intent) | rawbt → browser | Belum diuji di printer iMin. |
| `imin2` | Aplikasi POSNext Shell → `PrinterHelper.sendRAWData` (SDK 2.0) | imin2 → imin → browser | Hanya jalan di dalam APK. Tidak dipilih otomatis. |

Driver dipilih di POS Settings / POS Next Global Settings → Print Driver, atau
di-override per device di `/pos/direct-print`. Opsi `imin2` juga ditambahkan di
POS Print Log dan validasi `pos_next/api/printing.py`; perubahan JSON doctype
butuh `bench migrate`.

## Izin Chrome untuk driver `imin`

Driver `imin` menghubungi aplikasi di device sendiri (`127.0.0.1:8081`), jadi
Chrome meminta izin "Aplikasi di perangkat" (dan "Jaringan lokal" untuk IP LAN).
Kalau status izin **"Diblokir secara otomatis"**, halaman tidak bisa memunculkan
permintaan izin lagi; tombol minta akses dan ikon printer tidak berefek. Cara
pulih: ikon gembok → **Reset izin** → muat ulang `/pos` → picu Test Print →
**Izinkan**. Driver `imin2` dan RawBT mode Intent tidak memakai izin ini.

## Aplikasi android-shell

- API iMin SDK 2.0 diverifikasi dari jar resmi `iMinPrinter_SDK2_V2.0.0.19` dan
  repo resmi `iminsoftware/PrinterDemo`; dependency
  `com.github.iminsoftware:IminPrinterLibrary:V2.0.0.18` (JitPack).
- Menurut dokumentasi iMin, SDK 2.0 untuk Android 13+. APK minSdk 24, tetapi
  printer hanya jalan di device dengan servis SDK 2.0 (status -1 kalau tidak ada).
- Cara build, pasang, isi URL, troubleshooting, dan checklist 8 langkah di
  device: `android-shell/README.md`.

## Verifikasi

Sudah:
- Vitest penuh 92 file / 1289 test lolos (setelah merge `main`); `npm run build` OK.
- Backend `pos_next.api.test_printing` + test stock opname dari `main`: 76 test OK
  (fix stack).
- Biome: file baru bersih; file `.vue` yang diubah jumlah temuannya sama dengan baseline.
- Android: `./gradlew assembleDebug assembleRelease testDebugUnitTest` sukses
  (AGP 9.5.0, Gradle 9.7.1); 9 unit test bridge lolos.
- Dev: backup `backups/dev-20261104-133818`, rebuild, migrate, clear-cache sukses;
  opsi `print_driver` = browser/qz/imin/rawbt/imin2.

Belum:
- Uji `imin2`/APK di device iMin (checklist README belum dikerjakan).
- RawBT di printer iMin.
- APK release masih unsigned (belum ada keystore).
- `printBitmap` di aplikasi hanya diuji unit test; web belum memakainya.
- Merge ke `main` dan deploy prod. Image `erpnext16:local` sekarang berisi branch
  ini: jangan recreate prod tanpa otorisasi.
