#!/usr/bin/env node
// Mint an internal / admin key (or an external one for testing) WITHOUT GitHub OAuth.
//   node scripts/mint-key.mjs --scope admin --email ops@example.org > mint.sql
//   npx wrangler d1 execute nicheclock --local  --file mint.sql     # local dev
//   npx wrangler d1 execute nicheclock --remote --file mint.sql     # production (needs the Cloudflare token)
// stdout = the INSERT (only the SHA-256 of the key); stderr = the key itself, shown once.
import { createHash, randomBytes } from "node:crypto";

const args = Object.fromEntries(
  process.argv
    .slice(2)
    .map((a, i, all) => (a.startsWith("--") ? [a.slice(2), all[i + 1]] : null))
    .filter(Boolean),
);
const scope = args.scope ?? "internal";
const email = (args.email ?? "").trim().toLowerCase();
const plan = args.plan ?? "free";
if (!["external", "internal", "admin"].includes(scope) || !email.includes("@") || /[^a-z0-9@._+-]/.test(email) || /[^a-z0-9_-]/i.test(plan)) {
  console.error("usage: node scripts/mint-key.mjs --scope internal|admin|external --email name@example.org [--plan free]");
  process.exit(2);
}
const key = "nk_" + randomBytes(32).toString("base64url");
const id = "k_" + randomBytes(8).toString("hex");
const hash = createHash("sha256").update(key).digest("hex");
const now = new Date().toISOString();
console.log(
  `INSERT INTO keys (id, owner_github_id, email, hash, scope, plan, created_at) VALUES ('${id}', NULL, '${email}', '${hash}', '${scope}', '${plan}', '${now}');`,
);
console.error(`key id : ${id}\nscope  : ${scope}\nkey    : ${key}\n(shown once; only its SHA-256 is in the SQL on stdout)`);
