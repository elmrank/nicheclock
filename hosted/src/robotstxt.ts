/**
 * robots.txt, RFC 9309 (https://www.rfc-editor.org/rfc/rfc9309): groups by user-agent product token, Allow / Disallow with `*` and `$`,
 * the longest matching rule wins and Allow wins a tie. The fetch rules (2xx parse, 4xx allow, 5xx or unreachable refuse) are in suggest-net.ts.
 */
export interface Rule {
  allow: boolean;
  pattern: string;
}
export interface Group {
  agents: string[];
  rules: Rule[];
}
export interface Robots {
  groups: Group[];
  sitemaps: string[];
}

/** RFC 9309 2.5: a crawler must parse at least 500 kibibytes. */
export const ROBOTS_MAX_BYTES = 500 * 1024;

export function parseRobots(text: string): Robots {
  const groups: Group[] = [];
  const sitemaps: string[] = [];
  let cur: Group | null = null;
  let inAgents = false;
  for (const raw of text.replace(/^\uFEFF/, "").split(/\r\n|\n|\r/)) {
    const line = raw.replace(/#.*$/, "").trim();
    if (!line) continue;
    const colon = line.indexOf(":");
    if (colon < 0) continue;
    const field = line.slice(0, colon).trim().toLowerCase();
    const value = line.slice(colon + 1).trim();
    if (field === "user-agent") {
      if (!inAgents || !cur) {
        cur = { agents: [], rules: [] };
        groups.push(cur);
      }
      cur.agents.push(value.toLowerCase());
      inAgents = true;
    } else if (field === "allow" || field === "disallow") {
      inAgents = false;
      if (cur && value !== "") cur.rules.push({ allow: field === "allow", pattern: value });
    } else if (field === "sitemap") {
      if (value) sitemaps.push(value);
    } else {
      inAgents = false;
    }
  }
  return { groups, sitemaps };
}

/** Uppercase the hex of every percent escape, so %7e and %7E are the same octet. */
function normEscapes(s: string): string {
  return s.replace(/%[0-9a-fA-F]{2}/g, (m) => m.toUpperCase());
}

/** Does the rule pattern match the path (with its query)? `*` is any run of characters, a trailing `$` anchors the end. Linear scan, no regex. */
export function patternMatches(pattern: string, path: string): boolean {
  let p = normEscapes(pattern);
  const anchored = p.endsWith("$");
  if (anchored) p = p.slice(0, -1);
  const parts = p.split("*");
  const target = normEscapes(path);
  const first = parts[0] as string;
  if (parts.length === 1) return anchored ? target === first : target.startsWith(first);
  if (!target.startsWith(first)) return false;
  let pos = first.length;
  for (let i = 1; i < parts.length - 1; i++) {
    const at = target.indexOf(parts[i] as string, pos);
    if (at < 0) return false;
    pos = at + (parts[i] as string).length;
  }
  const last = parts[parts.length - 1] as string;
  if (anchored) return target.length - last.length >= pos && target.endsWith(last);
  return target.indexOf(last, pos) >= 0;
}

/**
 * The group for a product token: every group that names the token (a group agent that equals the token or is a prefix of it at a hyphen,
 * so `nicheclock` and `nicheclock-suggest` both match `nicheclock-suggest`) is merged; with none, the groups for `*` are merged.
 */
export function rulesFor(robots: Robots, token: string): Rule[] {
  const t = token.toLowerCase();
  const named = robots.groups.filter((g) => g.agents.some((a) => a !== "*" && (a === t || t.startsWith(a + "-"))));
  const pick = named.length ? named : robots.groups.filter((g) => g.agents.includes("*"));
  return pick.flatMap((g) => g.rules);
}

/** RFC 9309 2.2.2: the longest matching pattern decides, Allow wins a tie, no match means allowed. `/robots.txt` is always allowed. */
export function isAllowed(rules: Rule[], pathWithQuery: string): boolean {
  if (pathWithQuery === "/robots.txt") return true;
  let best: Rule | null = null;
  for (const r of rules) {
    if (!patternMatches(r.pattern, pathWithQuery)) continue;
    if (!best || r.pattern.length > best.pattern.length || (r.pattern.length === best.pattern.length && r.allow && !best.allow)) best = r;
  }
  return best ? best.allow : true;
}
