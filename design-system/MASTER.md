# ScrapR Design System — MASTER

Direction: **Quiet Authority.** Editorial restraint on warm paper, extreme negative space,
one ochre accent carrying all the energy. Expensive, calm, vibrant.

## The one feeling
Expensive, calm, vibrant. If a choice does not serve it, cut it. When in doubt, remove.

## Color — one dominant, one accent, no third
Dominant: Hurricane Grey (warm grey family). Accent: Ochre. Nothing else.

| Token | Hex | Use | Contrast on paper |
|---|---|---|---|
| paper | #F5F3F0 | page ground | — |
| surface | #FFFEFC | raised cards | — |
| sunk | #EDEAE6 | inset wells, inputs | — |
| ink | #171614 | headlines | 16.2:1 |
| ink-soft | #3B3733 | body text | 9.6:1 |
| ink-muted | #6B635C | labels, meta | 5.3:1 (AA) |
| line | #DFDAD4 | hairlines | — |
| line-strong | #C9C2BA | emphasized rules | — |
| ochre | #C2761B | graphics, fills, large type only | 3.4:1 (never small text) |
| ochre-deep | #8A4F0B | accent text on light | 6.5:1 (AA) |
| ochre-wash | #F7ECDD | accent tint surface | — |
| night | #1A1815 | inverted sections | — |

Rules: ochre appears at most twice per viewport. Fact vs analysis is signalled by ink vs ochre
plus a hairline treatment, never by adding a third hue.

**Scope: these two rules govern marketing surfaces only.** The product workspace inherits the
palette, type and spacing below, but renders far more simultaneous semantic states (claim types,
confidence, source accessibility, activity and conflict status) than a two-colour accent cap can
carry. It extends this system with a semantic token layer built on non-colour channels. See
`docs/superpowers/specs/implementation-plan.md` §11.4.

## Type — two families, nothing else
- Display: **Plus Jakarta Sans** 500/600/700. Headlines only.
- Body/UI: **Satoshi** 400/500/700. Everything else.
- Sentence case everywhere. No all-caps, no small-caps, no letter-spaced eyebrows.
- Body 17px, line-height 1.62, max 65ch. Never full page width.
- Scale: 13 / 15 / 17 / 19 / 22 / 28 / clamp(36..64) / clamp(52..96)

## Spacing — locked scale
4 8 12 16 24 32 48 64 96 128 160 192 (px).
Section rhythm: 160 desktop / 112 tablet / 80 mobile. Container 1240px, gutter 40 / 24.

## Surface
Radii: 8 (sm) / 14 (md) / 22 (lg) / 999 (pill). Two shadows only: `soft`, `lift`.
Hairlines do most of the structural work. No decorative gradients.

## Motion — three moments, nothing else
1. Hero: headline lines rise in, then the source-card stack fans out in stagger.
2. Section entry: `whileInView` once, y 16 → 0 + fade, 560ms expo-out, 60ms stagger.
3. Signature hover: a citation chip expands to reveal its source.
Everything else is static. `prefers-reduced-motion` disables all of it.

## Layout
Desktop-first. Every section fills the width in balance. Never strand content in one
quarter with an empty half. One idea per section.

## Non-negotiables
- Body text passes WCAG AA against its ground.
- No emoji as icons. Inline SVG only, 1.5px stroke.
- No em dashes in copy. Copy reads like a person wrote it.
- Lighthouse performance 90+.
