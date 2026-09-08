# Small-screen responsiveness overhaul (2026-09-07)

Targeted fixes for phone (320-430px), tablet (768px), and short-landscape
viewports. Desktop (>= 1024px) layout is unchanged.

## Root causes fixed

- **Dead `xs:` breakpoint** — templates used `hidden xs:inline` / `xs:hidden`
  (header clock, items pagination labels) but no `xs` screen was defined, so
  those classes never applied. Defined `xs: 400px` in
  `POS/tailwind.config.js`.
- **`100vh` viewport math** — the app shell and content area used `100vh`,
  which overstates the visible viewport on mobile browsers. Replaced with
  `.pos-app-shell` / `.pos-content-shell` utilities in `POS/src/index.css`
  (vh fallback, `dvh` where supported). Viewport meta also gained
  `interactive-widget=resizes-content` (Chrome/Android) so the keyboard
  resizes the layout instead of covering the cart footer.
- **Cart rows clipped on narrow phones** — the qty/UOM/total line had a
  min-width larger than a 320px screen, so amounts were cut off. The line now
  wraps (`flex-wrap`) with the line total kept right-aligned (`ms-auto`), the
  qty input is narrower on phones, and item remove buttons have a 24px hit
  area.
- **Product cards unusable below desktop** — square images made cards taller
  than the viewport in landscape. Image area is capped (`max-h-44`) below
  `lg`.
- **Dialog footers out of reach** — the shift-closing dialog body is bounded
  on <  lg (`.pos-dialog-bound`: one scroll region, footer always visible);
  the invoice-history shell and POS Settings use `dvh` heights; POS Settings
  header wraps on small screens.
- **Touch targets** — header hamburger is 44x44; secondary header icon
  buttons get 36px minimum on < lg (`.pos-icon-btn`).

## Verification

- `POS/src/components/sale/ResponsiveDialogs.test.js` — structural contracts
  (bounded dialog bodies, wrap-instead-of-clip cart rows, 44px hamburger).
- Playwright audit of a static harness (real components, mocked API — not a
  live POS flow): screenshots + DOM bounds for 320/360/390/430, 738x420
  landscape, 768x1024, 1280x900 in `gui-test-screenshots/responsive-*.png`
  and `responsive-{pre,post}-metrics.json`.
- `npm run build` and the full Vitest suite pass (352 tests).
