# POSNext Shell (Android, iMin Printer SDK 2.0)

Aplikasi Android kecil yang membuka POSNext (`/pos`) di WebView layar penuh dan
mencetak ke **printer internal iMin** lewat iMin Printer SDK 2.0. Tidak perlu
servis JS iMin (`ws://127.0.0.1:8081`), RawBT, izin jaringan lokal Chrome, atau
dialog print.

Di POSNext, pakai driver **`imin2`** ("iMin SDK 2.0 (POSNext app)"). Driver ini
dipilih di POS Settings → Print Driver, atau di-override per device di
`/pos/direct-print`. Driver tidak dipilih otomatis. Rantai fallback-nya
`imin2 → imin → browser`.

## Cara kerja

- `MainActivity` membuka URL server yang disimpan di SharedPreferences.
  JavaScript, DOM storage, service worker, dan upload file aktif. Tombol back
  mundur di riwayat WebView.
- Bridge `window.PosNextNative` dipasang lewat
  `WebViewCompat.addWebMessageListener`, **hanya** untuk origin server yang
  dikonfigurasi (`scheme://host[:port]`). Halaman dari origin lain tidak
  mendapat bridge. `addJavascriptInterface` tidak dipakai.
- Protokol JSON (setiap pesan membawa `id`, balasan memakai `id` yang sama):

  | cmd | request | balasan |
  |---|---|---|
  | `hello` | - | `{id, ok, app:"posnext-shell", version, sdk, service, model, android}` |
  | `status` | - | `{id, ok, code, message}` |
  | `printRaw` | `data` (base64 ESC/POS) | `{id, ok, code?, message?, printed?}` |
  | `printBitmap` | `data` (base64 PNG), `feedDots`, `cut` | sama dengan `printRaw` |

  `printed: true` berarti kertas mungkin sudah keluar, jadi web tidak akan
  mencoba driver lain (mencegah struk dobel).
- Semua perintah jalan di **satu thread** antrian (`ShellApp.printQueue`), jadi
  job tidak saling tumpang tindih dan UI tidak macet. Status diperiksa sebelum
  setiap job.
- SDK: `com.github.iminsoftware:IminPrinterLibrary:V2.0.0.18` (JitPack),
  `com.imin.printer.PrinterHelper`. Method yang dipakai: `initPrinterService`,
  `getPrinterStatus`, `sendRAWData`, `printBitmap`, `printAndFeedPaper`,
  `partialCut`, `getSDKVersionName`, `getServiceVersion`. Nama API dicek dari
  jar SDK resmi dan repo resmi `iminsoftware/PrinterDemo`.
- Kode status: 0 normal, 3 tutup terbuka, 4 baterai lemah, 5 overheat, 6 cutter
  error, 7 kertas habis, -1 servis printer belum terhubung.

> **Versi Android.** Menurut dokumentasi iMin, SDK 2.0 untuk device
> **Android 13+**, sedangkan device Android 11 ke bawah memakai SDK 1.0
> (driver `imin`). APK ini bisa dipasang mulai Android 7 (minSdk 24), tetapi
> printer hanya jalan kalau device punya servis printer iMin SDK 2.0. Kalau
> status selalu -1, pakai driver `imin` atau `rawbt`.

## Build

Butuh JDK 17+ (JBR bawaan Android Studio sudah cukup) dan Android SDK
(platform 36).

```bash
cd android-shell
echo "sdk.dir=$HOME/Library/Android/sdk" > local.properties   # sekali saja
./gradlew testDebugUnitTest      # unit test protokol bridge
./gradlew assembleDebug          # app/build/outputs/apk/debug/app-debug.apk
./gradlew assembleRelease        # app/build/outputs/apk/release/
```

### Signing release

Buat `android-shell/keystore.properties` (sudah di-gitignore, **jangan
di-commit**, begitu juga file `.jks`):

```properties
storeFile=/path/ke/posnext.jks
storePassword=...
keyAlias=posnext
keyPassword=...
```

Sebagai gantinya bisa juga pakai env `POSNEXT_KEYSTORE`,
`POSNEXT_KEYSTORE_PASSWORD`, `POSNEXT_KEY_ALIAS`, dan `POSNEXT_KEY_PASSWORD`.
Tanpa keystore, hasil build adalah `app-release-unsigned.apk`.

Membuat keystore baru:

```bash
keytool -genkeypair -v -keystore posnext.jks -alias posnext -keyalg RSA -keysize 2048 -validity 10000
```

Simpan keystore itu baik-baik. Update APK hanya bisa dipasang kalau
ditandatangani dengan key yang sama.

## Pasang APK

```bash
adb install -r app/build/outputs/apk/debug/app-debug.apk
```

Cara lain: salin APK ke device lalu buka dari file manager (izinkan "install
unknown apps").

## Isi URL server

1. Pada pembukaan pertama, layar pengaturan muncul otomatis.
2. Isi URL lengkap, misalnya `https://erp.contoh.id/pos` atau
   `http://192.168.1.10:8000/pos`, lalu tekan **Simpan**.
3. Untuk mengubahnya nanti, **tahan lama pojok kanan atas** layar (area kira-kira
   56dp). Halaman pengaturan juga punya tombol **Test print native**.

Bridge hanya aktif untuk origin URL ini. Kalau server pindah domain atau port,
URL harus diubah di sini.

## Troubleshooting

| Gejala | Penyebab / solusi |
|---|---|
| Toast "WebView terlalu lama" | Android System WebView tidak mendukung `WEB_MESSAGE_LISTENER`. Perbarui "Android System WebView" / Chrome dari Play Store. |
| Direct Print: "POSNext app (iMin SDK 2.0) not detected" | Halaman tidak dibuka dari aplikasi ini, atau origin berbeda dari URL yang disimpan (cek http/https, host, port). |
| Status -1 / "Printer service not connected" | Device tidak punya servis printer iMin SDK 2.0 (umumnya Android 11 ke bawah). Pakai driver `imin` atau `rawbt`. |
| "Printer cover is open" / "out of paper" | Tutup cover atau isi kertas, lalu cetak ulang. |
| Test print OK tetapi struk POS tidak keluar | Pastikan driver efektif di `/pos/direct-print` = `imin2`, lalu cek `POS Print Log`. |
| Halaman tidak memuat (http) | Aplikasi mengizinkan cleartext. Cek jaringan dan URL. |

Log Android: `adb logcat -s IminPrinter chromium`.

## Checklist manual di device

1. Pasang APK di device iMin yang **gagal** mencetak dengan driver `imin` biasa.
2. Isi URL server, login, buka `/pos/direct-print`, lalu pastikan driver
   efektif = `imin2` dan info "POSNext app detected" muncul (versi app/SDK).
3. Test Print struk dalam mode **Teks** dan **Gambar**. Cek kolom harga rata
   kanan, logo, ukuran nomor antrian, feed, dan cut.
4. Lakukan transaksi sungguhan: struk keluar otomatis dan `POS Print Log` berisi
   `Success` dengan driver `imin2`.
5. Saat kertas habis atau cover terbuka, muncul error yang jelas dan struk tidak
   tercetak dobel.
6. Tutup shift: laporan EOD tercetak.
7. Mode offline (matikan Wi-Fi): penjualan dan cetak tetap jalan.
8. Buka situs lain di WebView kalau bisa: `PosNextNative` tidak tersedia
   (`typeof PosNextNative === "undefined"`).
