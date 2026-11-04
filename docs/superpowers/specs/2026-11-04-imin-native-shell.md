# Prompt: Driver "native" lewat aplikasi wrapper Android (iMin SDK native)

Status: ready-for-agent (kode); verifikasi di device butuh manusia
Dibuat: 2026-11-04

Salin seluruh bagian **Prompt** di bawah ke sesi agent baru. Konteks singkat
untuk manusia:

- Printer yang dipakai **hanya printer internal iMin**. Web Bluetooth, WebUSB,
  dan printer LAN tidak relevan.
- Driver `imin` sekarang bergantung pada servis JS iMin (`ws://127.0.0.1:8081`,
  plugin iMinprinterplugin). Ada device yang tidak bisa, padahal modelnya mirip.
- Driver `rawbt` (branch `claude/upbeat-bardeen-y5082q`) adalah jalur kedua,
  tapi butuh aplikasi pihak ketiga, dan belum pasti mendukung printer internal
  iMin.
- Jalur ketiga yang paling andal: aplikasi Android milik sendiri (WebView yang
  memuat `/pos`) yang memanggil **iMin Printer SDK native** langsung. Tanpa
  plugin, tanpa izin "Local network access" Chrome, tanpa dialog print.

---

## Prompt

Kamu bekerja di repo POSNext (`Muhayustrid/POSNext`). Ini aplikasi
Frappe/ERPNext `pos_next` dengan SPA Vue 3 di `POS/`. Baca dulu `CLAUDE.md`,
`docs/RAWBT_PRINTING.md`, `docs/superpowers/specs/2026-09-02-imin-device-probe.md`,
dan semua file di `POS/src/utils/print/`, terutama `transport.js`,
`imin_client.js`, `rawbt_client.js`, `receipt_text.js`, dan `receipt_layout.js`.

### Tujuan

Tambahkan jalur cetak ketiga untuk **printer internal iMin**, tanpa mengubah
perilaku driver `imin` dan `rawbt` yang sudah ada:

1. **Aplikasi wrapper Android** ("POSNext Shell") di folder baru
   `android-shell/` di repo ini. Isinya WebView yang memuat URL POSNext dan
   meneruskan job cetak ke **iMin Printer SDK native**.
2. **Driver frontend baru `native`** (`POS/src/utils/print/native_client.js`)
   yang mengirim job ke aplikasi itu lewat bridge JavaScript.

### Bagian A: aplikasi Android (`android-shell/`)

- Kotlin, Gradle (Android Gradle Plugin versi stabil terbaru), `minSdk` cukup
  rendah untuk device iMin Android 7–13 (cek device yang dipakai; minimal API 24).
- Satu Activity fullscreen berisi WebView:
  - URL server disimpan di SharedPreferences. Layar pengaturan sederhana
    (bisa dibuka dengan long-press di pojok atau menu tersembunyi) untuk
    mengisi URL, misalnya `https://<site>/pos`, dan tombol "Test print native".
  - Aktifkan JavaScript, DOM storage, service worker (PWA offline POSNext
    harus tetap jalan), dan izin file upload kalau POS memakainya.
  - Tombol back Android diteruskan ke riwayat WebView.
- **Printer:** pakai **iMin Printer SDK v2** (library resmi iMin; menurut
  dokumentasi iMin, SDK 2.0 tidak butuh iMinprinterplugin). **Verifikasi nama
  class/method dari dokumentasi resmi iMin** (`oss-sg.imin.sg/docs/en/Printer.html`
  atau repo/aar resmi iMin) sebelum dipakai. Jangan menebak. Yang dibutuhkan:
  - inisialisasi servis printer,
  - kirim **raw ESC/POS bytes** (prioritas utama, karena POSNext sudah punya
    encodernya; catatan: dokumentasi iMin menyebut perintahnya "sedikit beda"
    dari ESC/POS standar, jadi uji `GS v 0`, `ESC a`, `GS !`, `ESC E`,
    `ESC 3`, `ESC J`, `GS V`),
  - cetak bitmap (cadangan kalau raster `GS v 0` tidak didukung),
  - status printer (normal / kertas habis / tutup terbuka),
  - feed dan partial cut.
- **Bridge ke JavaScript:** pakai `WebViewCompat.addWebMessageListener`
  (androidx.webkit) dengan `allowedOriginRules` = origin server yang
  dikonfigurasi saja. **Jangan** `addJavascriptInterface` yang terbuka untuk
  semua origin. Halaman lain yang dimuat di WebView tidak boleh bisa
  mencetak.
  - Nama objek di JS: `PosNextNative`.
  - Protokol pesan berbentuk JSON dengan `id` agar bisa dijadikan Promise:
    - `{id, cmd:"hello"}` → `{id, ok:true, app:"posnext-shell", version, sdk, model, android}`
    - `{id, cmd:"status"}` → `{id, ok, code, message}`. `code` 0 = normal;
      sebutkan mapping kode SDK di komentar.
    - `{id, cmd:"printRaw", data:"<base64 ESC/POS>"}` → `{id, ok, message}`.
      Balas **setelah** SDK selesai menerima job (atau error). Jalankan di
      background thread, jangan di UI thread.
    - `{id, cmd:"printBitmap", data:"<base64 PNG>", feedDots, cut}` → sama.
  - Job diproses berurutan (antrian satu thread) supaya dua struk tidak
    tercampur.
- Build: `./gradlew assembleDebug` dan `assembleRelease` (signing config dari
  env/`keystore.properties` yang di-gitignore; jangan commit keystore).
  Tambahkan `android-shell/README.md`: cara build, pasang APK, isi URL, dan
  troubleshooting.

### Bagian B: driver frontend `native`

- Pindahkan encoder ESC/POS yang sekarang ada di `rawbt_client.js`
  (`escRaster`, `escFeed`, `escCut`, `buildEscPosSheets`, `encodeTextOps`,
  `asciiBytes`, `escposCols`, logo loader) ke modul bersama
  `POS/src/utils/print/escpos.js`. `rawbt_client.js` dan `native_client.js`
  sama-sama import dari sana. Perilaku RawBT tidak boleh berubah; semua test
  RawBT yang ada harus tetap lolos.
- `native_client.js` (`createNativeDriver(deps)`, semua side effect bisa
  di-inject untuk test, ikuti pola `rawbt_client.js`):
  - `isAvailable()`: true hanya kalau `window.PosNextNative` ada dan `hello`
    menjawab dalam ~2 detik (cache hasilnya per sesi).
  - `getStatus()`: dari `status`.
  - `printHTML(html, opts)`: ikuti aturan `resolvePrintConfig` dan mode
    teks/gambar persis seperti `rawbt_client.js` (mode teks butuh
    `receiptDoc`/`eodDoc`; tanpa doc → gambar). Kirim lewat `printRaw`.
  - Error **sebelum** job diterima aplikasi → reject biasa (transport boleh
    fallback). Error **setelah** sebagian kertas keluar → tandai
    `err.postPrint = true` (lihat COR-FE-10 di `transport.js`) supaya tidak
    tercetak dobel.
  - Status kertas habis / tutup terbuka → pesan error yang jelas (pakai
    string yang sudah ada di `imin_client.js` kalau cocok).
  - `describe()` → `{ id: "native", label: "iMin Native (aplikasi)", detail: <versi app/SDK> }`.
- `transport.js`:
  - Daftarkan driver `native`. Chain: `native: ["native", "imin", "browser"]`.
    Chain `imin` dan `rawbt` **tidak berubah**.
  - **Auto-pilih:** kalau `window.PosNextNative` ada **dan** device override
    kosong, driver efektif di device itu = `native`, apa pun driver di Profil
    POS. Device yang dibuka di Chrome biasa tetap memakai driver Profil POS.
    Override per device (Direct Print) tetap menang.
- Direct Print (`POS/src/pages/DirectPrint.vue`): tambah opsi
  "iMin Native (aplikasi POSNext)" di "Printer di perangkat ini", dan tampilkan
  info aplikasi (versi app/SDK/model dari `hello`) kalau bridge ada.
- `POSSale.vue`: ikon printer di header memakai status driver `native` kalau
  driver efektifnya `native`.
- `printInvoice.js`: tambahkan `native` ke `TEXT_MODE_DRIVERS`.
- Backend: tambahkan `native` ke opsi `driver` di
  `pos_next/pos_next/doctype/pos_print_log/pos_print_log.json` (update
  `modified`) supaya log tercatat. Tidak perlu jadi opsi di POS Settings,
  karena dipilih otomatis per device. Kalau menurutmu perlu, jelaskan alasannya
  dulu.
- Terjemahan: semua string UI baru masuk `pos_next/translations/id.csv`
  (`docs/LOCALIZATION.md`).

### Test dan pengecekan

- Vitest: `native_client.test.js` (bridge palsu: hello/status/printRaw sukses,
  timeout, error sebelum dan sesudah cetak, mode teks vs gambar, EOD, crew
  slip), test `escpos.js`, dan test transport untuk auto-pilih `native` dan
  chain-nya.
- Jalankan `npm run test:run`, `npm run build`, dan lint Biome untuk file yang
  diubah (di container: lihat `CLAUDE.md`; kalau tidak ada Docker, jalankan
  langsung di `POS/`).
- Android: `./gradlew assembleDebug` harus sukses. Kalau SDK Android tidak
  tersedia di environment, tulis dengan jelas di laporan bahwa build Android
  belum diverifikasi.

### Checklist manual di device (dikerjakan manusia, tulis di README)

1. Pasang APK di device iMin yang **gagal** dengan driver `imin` biasa.
2. Isi URL server, login, buka `/pos/direct-print`, dan pastikan driver efektif
   = iMin Native.
3. Test Print struk dalam mode **Teks** dan **Gambar**. Cek kolom harga rata
   kanan, logo, ukuran nomor antrian, feed, dan cut.
4. Transaksi sungguhan: struk otomatis keluar, `POS Print Log` = `Success`,
   driver `native`.
5. Kertas habis / tutup terbuka → error jelas, tidak tercetak dobel.
6. Tutup shift → laporan EOD tercetak.
7. Mode offline (matikan Wi-Fi) → jual dan cetak tetap jalan.
8. Buka situs lain di WebView (kalau bisa) → `PosNextNative` tidak tersedia.

### Aturan kerja

- Driver `imin` dan `rawbt` yang ada **tidak boleh berubah perilakunya**.
- Jangan ubah penanganan ID invoice offline (`docs/OFFLINE_SYNC.md`).
- Commit conventional dengan scope dan deskripsi berbahasa Indonesia, misalnya
  `feat(print): ...`, `feat(android): ...`.
- Kerjakan di branch yang ditentukan sesi. Jangan buat PR kecuali diminta.
- Kalau dokumentasi SDK iMin tidak bisa diakses (domain diblokir), berhenti di
  bagian Android dan laporkan; jangan mengarang nama API.
