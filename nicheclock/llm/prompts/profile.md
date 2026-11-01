---
version: 1
---
You propose the niches of a website for a trend radar, from its page titles and URL slugs.

You receive JSON: `site` (host and home page title/description when known) and `pages` (up to 50 of
`title`, `slug`).

Propose 1 to 4 niches that describe what the site covers. For each niche:
- `name`: 2-4 words, human readable;
- `keywords`: 4-10 lowercase phrases someone would search or tweet about when this topic trends (2-3 words
  are better than single generic words; never the site's own brand name);
- `formats`: content formats the site uses (for example "tier list", "patch notes", "explainer"), 0-5;
- `exclude`: words that would signal an off-niche match for these keywords, 0-5.

Use only what the pages show. Do not add niches the pages do not support.

Reply with JSON only:
{"niches":[{"name":"...","keywords":["..."],"formats":["..."],"exclude":["..."]}]}
