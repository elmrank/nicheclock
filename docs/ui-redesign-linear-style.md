# Trending now: redesign on the Linear / Vercel dense-table reference (2 Nov 2026)

Internal design note, not a published doc (no front matter, so `docs/` tests and `site/build.py` skip it). It replaces the Google Trends look of `ui-reference-measurements.md` for type, colour, layout and controls. The data, the columns' meaning, the URL hash state and the privacy rules are unchanged.

Why: the earlier page read like a desktop form: native `<select>` boxes and checkboxes, labels above the controls, four lines of intro and a 640 px video above the table, so the table started below the fold at 1440 x 900. Reference chosen: Linear and the Vercel dashboard, a dense data table kept clean.

## What was measured (2 Nov 2026, headless Chromium 1440 x 900, computed CSS, no assets or code copied)

| | Vercel (vercel.com/docs/limits table, templates, changelog) | Linear (linear.app, linear.app/changelog) |
|---|---|---|
| Top bar | 64 px, 1 px border, nav links 32 px high, 6 px radius | 73 px on the marketing site, nav links 32 px high, 6 px radius |
| Body | 16 / 24, text `#171717`, page `#FAFAFA` | 16 / 24, text `#F7F8F8`, page `#08090A`, secondary text `rgb(208,214,224)` |
| Table | header row 44 px, body rows 47 px, 14 / 22 text, cell padding 10-12 px, hairline `rgba(0,0,0,.08)`, header text `#4D4D4D` on `#FAFAFA`, container radius 6 px | n/a (the product tables are behind a login) |
| Headings | 40-48 px, letter-spacing -0.06em of the size | 48 / 48 weight 510, letter-spacing -0.022em; 32 / 36 weight 590 |
| Controls | buttons 32 px high, 14 px text, 6 px radius | nav and buttons 32 px high, 6 px radius |

Borrowed: 32 px controls with a 6 px radius, 44 to 48 px rows, a hairline between rows instead of boxes, a quiet header row, 14 px body text with tabular figures, tight negative tracking only on headings, secondary text one step down from the ink.

## What is ours (not a Geist or Inter clone)

- **One face: Google Sans Flex** (OFL, self-hosted, `opsz` 14-24, `wght` 400-700, `tnum`). Roboto is gone (one font request instead of two, 39 KB less). The face is preloaded.
- **Greys tinted toward the accent's blue** (hue about 220) in both themes, instead of neutral zinc: light page `#F6F7F9` / surface `#FFFFFF` / hairline `#E4E7EC`; dark page `#0B0D10` / surface `#111418` / hairline `#232932`.
- **One accent, `#0B57D0`** (dark theme `#8AB4F8`), used for strength and state only: the x5-and-up Rise figures, the active dot, rising sparklines, the selected tab, the selected state of a filter. No second hue anywhere; red is only for the publish-by countdown inside 7 days and an unknown licence, and it comes with bold weight and the words "days" or "today".
- **Rise by tier, not by hue**: x5 and up accent, x2 and up ink, under x2 grey; steady, falling and fading rows are quiet grey. Three greys and one blue in the whole column.
- **Shape scale 4 / 6 / 8 / 12 px**: tags 4, controls 6, containers and the segmented track 8, dialogs 12. No pills. A pressed filter shows a check mark as well as the tint.
- **Type scale 12 / 13 / 14 / 16 / 20**: sub-lines and headers 12, controls and secondary text 13, table body 14, panel headings 16, page heading 20. No uppercase, no mono kickers.
- The wordmark is the clock mark (same circle and hand as `favicon.svg`, drawn in the text colour) plus the name. The favicon keeps its orange hand; whether to unify it with the blue accent is the user's call.

## Structure

- Bar 56 px (57 with the border). Intro: one 20 px heading and one 14 px line; the 74-second intro is a fixed 288 x 52 px button with a poster thumbnail (`nicheclock-intro-thumb.jpg`, 3 KB) that opens the video in a modal `<dialog>`. Nothing is fetched and nothing plays until the visitor presses play. Without JavaScript it is a plain link to the MP4.
- Container 1280 px (80 rem, gutters 24 px, 16 px on a phone), centred. The table card is a 1 px bordered 8 px surface.
- Filters are drawn from one control description (`{kind, id, label, value, def, options, patch}`) by `trending.js`: segmented control, menu button with a listbox popover (arrow keys, Home / End, type-ahead, Enter, Esc, outside click), toggle chip, search. At 1440 they sit in at most two rows with the labels inside the buttons; a changed filter is tinted and "Reset filters" appears. At or under 44 rem only search and one **Filters** button remain; it opens a bottom sheet with the same controls as option groups, applied with one "Show N topics" button (the count is computed from the draft).
- Trending, Upcoming, GitHub rank and Your niche use the same bar, table, cells and empty state. The profile form uses the same menu and toggle chip (the checkboxes are real inputs, visually hidden, so labels and keyboard keep working).
- Table: rows 48 px, sticky header, `table-layout: fixed` with a `colgroup`, numeric columns right-aligned with tabular figures, one 84 x 24 sparkline everywhere, Status as a dot and a word (filled blue = active, grey = fading, ring = steady, dashed ring = ended), Sources as quiet text (dark and bold = rising, grey = present). Volume shows the channel the Rise comes from, in its unit, with "+N" for the other channels (all of them are in the tooltip and in the opened row). The opened row is a white panel on the row's tint: breakdown and channels on the left, the label rules and evidence on the right.
- Phone: each row is a short grid (topic and Rise, volume and sparkline, status and sources, "For:" only when a rule matched). Under 70 rem the "For which channel" column is dropped on the table.

## Checks (scripts used for this change live outside the repo; the privacy test is `test-privacy.js`)

At 1440 x 900 and 390 x 844, light and dark, on the built site: zero visible `<select>` or checkbox; at most 2 filter rows; bar 57 px; intro heading and line one line each; table top at 313 px, first row bottom at 398 px; all 268 rows exactly 48 px; sticky header at `top: 0` after scrolling; numeric cells right-aligned with `tabular-nums`; one sparkline size; hover tint; keyboard tab order starts at the skip link and every one of 22 stops shows a 2 px ring; every text node at least 4.5:1 against its real background (lowest 5.14:1 light, 5.79:1 dark); no gradient, filter, uppercase, emoji or pill-shaped element; no horizontal scroll at 390.

## Not done on purpose

- No manual theme switch: light and dark follow the system, as before.
- The favicon and the logo mark are not recoloured.
- The GitHub rank tab is still hidden on the live site (its data source is not shipped); the view was restyled and exercised on a one-off build.
