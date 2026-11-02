# Trending now: fonts and colours taken from Google Trends

> **Superseded on 2 Nov 2026 for fonts, colours and layout** by `docs/ui-redesign-linear-style.md` (Linear / Vercel dense-table reference, one face: Google Sans Flex; Roboto removed). The Google Trends measurements below stay as the record of the first version.

Rule from the user (01/11): do not pick fonts or colours. Copy them from a real reference product, record the measured values, and use exactly one accent besides black, white and grey. This page is the evidence.

**Reference:** Google Trends, "Trending now" (`https://trends.google.com/trending?geo=US&hours=168`), the product the user compares against. Opened once in a headless Chromium on **2026-11-01**; read computed CSS only (no content copied or stored). Why this reference: the user asked for "the Google Trends UI", and it is the only one of the candidates (Exploding Topics, Glimpse, Treendly, SparkToro, Similarweb, AnswerThePublic) whose table layout we follow.

## Fonts

| Where on Trends | Measured `font-family` (size / weight) | Used on nicheclock | Licence |
|---|---|---|---|
| Body, default text | `Roboto, RobotoDraft, Helvetica, Arial, sans-serif` (16 px) | **Roboto** (variable, wght 400-700) | SIL OFL 1.1 (`google/fonts` `ofl/roboto`) |
| Trend titles, volume, started, header cells | `"Google Sans Flex", "Google Sans Text", "Google Sans", Roboto, Arial` (titles 18 / 500, headers 14 / 500) | **Google Sans Flex** (wght 400-700, opsz 14-24) | SIL OFL 1.1; open-sourced by Google in 2025 (`google/fonts` `ofl/googlesansflex`, `googlefonts/googlesans-flex`). The exact face Trends uses, so no substitute |
| Tabs, chips, related-query chips | `"Google Sans", sans-serif` (16 / 500; chips 12) | Google Sans Flex (same family; Google Sans is also OFL, but one file is enough) | SIL OFL 1.1 |
| Logo word "Trends" | `"Product Sans", Arial, sans-serif` (22 / 400) | not used: Product Sans is proprietary. The wordmark "nicheclock" is set in Google Sans Flex 500 | n/a |
| Filter buttons | `Arial` (browser default, not styled) | Roboto (not copied: it is an unstyled default) | n/a |

Both files are self-hosted from `hosted/site/trending/fonts/` (no request leaves the origin), with their OFL texts. Subsetting: Latin, Latin Extended-A and Vietnamese; Google Sans Flex pinned to `wdth 100, ROND 0, slnt 0` (files: 80 KB and 39 KB, woff2). Neither font carries arrows, so the rise and sort marks are drawn with CSS borders.

Banned list check (Inter, Geist, Space Grotesk, IBM Plex, Atkinson Hyperlegible, Source Sans 3, JetBrains Mono, Manrope, DM Sans, Outfit, Plus Jakarta Sans, Barlow): none is used on the Trending now page. Source Sans 3 is gone from `hosted/site/trending/`. The older report page at `/demo/` and `nicheclock serve` (`nicheclock/output/app/`) still use it until the user decides to restyle them.

## Colours

| Where on Trends | Measured | Used on nicheclock |
|---|---|---|
| Primary button ("Sign in") | `rgb(11,87,208)` = `#0B57D0`, white text, radius 100 px | **`#0B57D0` is the one accent.** Uses: content links, selected tab underline, selected window button, primary buttons, rising numbers, rising source chips, sparklines |
| Links in page chrome | `rgb(41,98,255)` = `#2962FF` | merged into the accent (`#0B57D0`): one blue, not three |
| Selected-tab indicator | 2 px `rgb(24,90,188)` = `#185ABC` | merged into the accent |
| Focus ring | 3 px `rgb(0,99,155)` = `#00639B` | accent, 3 px |
| Export button (tonal) | `rgb(194,231,255)` = `#C2E7FF` | not used (a second tint) |
| "Active" chip and sparklines | chip `#C4EED0` on text `#0F5223`; sparkline stroke `rgb(30,164,70)` = `#1EA446` (green) | **not used**: a second hue would break the one-accent rule. Active is a filled dot and Ended a hollow one; the rising sparkline is the accent |
| Text | titles `#1F1F1F`; secondary `#444746`; body `rgba(0,0,0,.87)` | ink `#1F1F1F`, secondary `#444746` |
| Borders (rows, filter buttons) | 1 px `rgb(196,199,197)` = `#C4C7C5`; filter buttons radius 8 px | rule `#C4C7C5`, controls radius 8 px |
| Page and table surface | page `rgb(240,244,249)` = `#F0F4F9`; table `#FFFFFF` | page `#F0F4F9`, card `#FFFFFF` (radius 16 px is not measured), hover and open-row tint `#F0F4F9` |
| Warning (publish within 7 days, licence unknown) | Trends has none | red `#B3261E` (the Material 3 error colour; light only meaning "warning", never decoration) |

**Dark theme.** Trends has none: its body colour does not change under `prefers-color-scheme: dark` (measured). The dark values are the dark counterparts of the same Google palette in Material 3, **not measured on Trends** `[INFERENCE]`: page `#131314`, card `#1E1F20`, ink `#E3E3E3`, secondary `#C4C7C5`, rule `#444746`, accent `#A8C7FA` with text `#062E6F` on it, warning `#F2B8B5`.

## Contrast and colour use (measured on the built page, real catalog data)

| | Light | Dark |
|---|---|---|
| Lowest text contrast anywhere (accent link on page background) | 5.78 : 1 | 7.5 : 1 |
| Both above WCAG AA 4.5 : 1 | yes | yes |
| Colours other than grey, page tint and the accent, on Trending and Upcoming | only red `#B3261E` (Upcoming countdown) | only `#F2B8B5` |

## Screenshots (1440 x 900 and 390 x 844, light and dark)

Before: Source Sans 3, black and white. After: Roboto and Google Sans Flex with one accent (list, opened row, Upcoming). The screenshots are not in the repository.
