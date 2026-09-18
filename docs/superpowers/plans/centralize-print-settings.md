# Plan: Centralize Print Settings + print_mode behavior

## Context

The POS app's print behavior is controlled from five scattered places:
POS Settings doctype (Printing section), the SPA POS Settings dialog
(silent_print + QZ picker), DirectPrint page (per-device localStorage
overrides that SHADOW server config), the POS Profile field
`print_receipt_on_order_complete` (auto-print gate), and `silent_print`
(lane selector). Owner wants ONE central config place, with the POS exposing
only a print on/off mode: Off (no print), Manual (print button), Auto
(auto-print on invoice).

Design approved by advisor. Backend groundwork already on this branch:
`pos_next/api/printing.py` rewritten (table-driven clamps shared by read +
write, new `update_print_config` whitelisted endpoint with permission gate),
`print_mode` Select (Off/Manual/Auto, default Manual) added to POS Settings
doctype JSON.

## Global Constraints

- `print_mode` values are the exact strings `"Off" | "Manual" | "Auto"`.
- `silent_print` column stays in DB but NO code reads it after this change
  (one-release deprecation). Same for POS Profile's
  `print_receipt_on_order_complete`: column stays, nothing reads it.
- QZ printer selection/cert (localStorage) and the iMin printer host are
  device attachments — they stay per-device in localStorage.
- No new npm/pip dependencies. No DB column drops.
- Match each file's EXISTING style (POS/src/utils/print/* uses no-semicolon
  biome style; stores/pages use semicolons). Do NOT reformat whole files —
  the repo is not biome-clean globally; keep diffs surgical. Verify with
  `npx vitest run` from POS/ (26 files / 360+ tests must stay green).
- User-visible strings go through `__()`.
- Transport singleton must refetch server config after any save
  (`initTransportFromServer` currently guarded by a once-per-session flag in
  `ensureTransportInitialized` — printInvoice.js — bust it on save).

## Task 1: Backend completion — settings feed, migration patch, tests

1. `pos_next/api/constants.py`: add `"print_mode"` to POS_SETTINGS_FIELDS
   (after `silent_print`) and `"print_mode": "Manual"` to
   DEFAULT_POS_SETTINGS.
2. Migration patch `pos_next/patches/v2_6_0/set_print_mode.py` with
   `execute()` that, per POS Profile, resolves `print_mode` from that
   profile's own old flags: `Auto` if (POS Settings.silent_print OR POS
   Profile.print_receipt_on_order_complete) else `Manual`; updates the
   existing POS Settings row or creates an enabled one. Guard the
   `print_receipt_on_order_complete` column read with meta (custom field).
   Register in `pos_next/patches.txt` under [post_model_sync] after
   v2_5_0.
3. Extend `pos_next/api/test_printing.py`: update_print_config clamps to the
   same bands as get_print_config; whitelists (unknown keys rejected/ignored
   — assert they never reach the doc); permission gate throws for a user
   with neither profile access nor POS Settings write. Follow the file's
   existing mock/_settings_row pattern where possible.

## Task 2: Frontend print_mode behavior

1. `POS/src/stores/posSettings.js`: add `"print_mode": "Manual"` default;
   add computeds `printMode`, `isPrintAuto` (=== "Auto"), `isPrintOff`
   (=== "Off"); REMOVE the `silentPrint` computed and its export (and the
   `silent_print` key from the defaults object).
2. `POS/src/pages/POSSale.vue`:
   - offline path (~line 2437) and online path (~line 2516): replace
     `if (shiftStore.autoPrintEnabled)` with `if (posSettingsStore.isPrintAuto)`.
   - success-dialog "Print Invoice" button (~line 1007): render only when
     `!posSettingsStore.isPrintOff`.
   - `runPrintInvoice` (~3298): drop the silentPrint branch — always
     `printWithSilentFallback` (it already falls back to browser print).
   - `:silent-print-enabled="posSettingsStore.silentPrint"` (~line 26):
     pass `!posSettingsStore.isPrintOff`; in POSHeader.vue rename prop to
     `printEnabled` and adapt the tooltip/dot conditions (dot shows when
     printEnabled && driver lane is silent-capable — keep current QZ-status
     dot behavior, only re-key the gate).
   - watcher on `posSettingsStore.silentPrint` (~line 1501): rewire to
     `printMode` (same behavior) or remove if obsolete.
3. `POS/src/stores/posShift.js`: remove the `autoPrintEnabled` computed and
   its export (no readers left).
4. `POS/src/components/settings/POSSettings.vue` Printing tab: replace the
   silent_print CheckboxField with a SelectField bound to
   `settings.print_mode` (options Off/Manual/Auto, translated labels);
   re-gate the QZ block `v-if` from `settings.silent_print` to
   `settings.print_mode !== 'Off'`.

## Task 3: Print path reads server config only (kill layout localStorage)

1. `POS/src/utils/printInvoice.js` `effectiveReceiptDots()`: stop merging
   `loadDeviceConfig()`; resolve from transport config alone
   (`resolvePrintConfig({}, { paper, customDots })`).
2. `POS/src/utils/print/imin_client.js`: default `deps.loadConfig` to
   `() => ({})` (keep the injection point for tests) so layout knobs come
   from opts.config only; the device `host` continues to be read from
   localStorage wherever the printer connection is established
   (keep/extract a `loadPrinterHost()` reading only `host`).
3. Keep `loadDeviceConfig`/`saveDeviceConfig` exported (DirectPrint Task 4
   migration + host still use them). Existing tests
   (imin_client.test.js, printInvoice.test.js, receipt_layout.test.js)
   must stay green; adjust only what the behavior change requires.

## Task 4: DirectPrint saves to server

1. `POS/src/pages/DirectPrint.vue`: the three save handlers currently call
   `saveDeviceConfig` (device: host+paper+cut; receipt layout; EOD layout).
   Change: receipt-layout and EOD-layout buttons call
   `pos_next.api.printing.update_print_config` with the snake_case payload
   (fieldnames as in PRINT_CONFIG_FIELDS; build from the same parsed values
   the handlers already compute), then force-refresh the transport
   (`initTransportFromServer(posProfile)` — ensure it refetches, see
   constraint on ensureTransportInitialized) and refresh previews/logs.
   The device section keeps ONLY the iMin host (localStorage via
   saveDeviceConfig({host})) — paper/cut move to the server payload
   (paper+cut go with the receipt layout save).
2. One-time migration banner: on mount, if localStorage
   `pos_imin_device_config` contains any key other than host/_v, show a
   dismissible banner listing those legacy values ("saved on this device
   before print settings were centralized — re-apply them below if still
   wanted"), and on dismiss (or immediately after capturing) strip the
   layout keys from localStorage keeping only {host,_v}.
3. Previews/EOD preview seed from the transport/server config only
   (serverConfigFromTransport already does; remove localStorage seeding if
   any remains).
4. Requires a POS Profile in context: when none (no shift), the server-save
   buttons show the endpoint's error message from
   update_print_config (it throws "No POS Profile in context").

## Task 5: Final verification

Full `npx vitest run` in POS/, `npx biome check` on changed files only
(compare against pre-change noise), python compile check
(`python3 -m py_compile`) on changed .py files, and review that no code
reads `silent_print` / `print_receipt_on_order_complete` anymore
(`grep -rn`).
