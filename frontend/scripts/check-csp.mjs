// Fails when the built index.html would break the Content Security Policy:
// inline scripts, <style> elements, style attributes or inline event handlers.
import { readFileSync } from "node:fs";
import { pathToFileURL } from "node:url";

export function findCspViolations(html) {
  const problems = [];
  for (const [, attrs, body] of html.matchAll(/<script\b([^>]*)>([\s\S]*?)<\/script>/gi)) {
    if (!/\bsrc\s*=/.test(attrs) || body.trim() !== "") {
      problems.push("inline <script>");
    }
  }
  if (/<style\b/i.test(html)) problems.push("inline <style>");
  if (/\sstyle\s*=/i.test(html)) problems.push("style attribute");
  if (/\son[a-z]+\s*=/i.test(html)) problems.push("inline event handler");
  return problems;
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  const file = process.argv[2] ?? "dist/index.html";
  const problems = findCspViolations(readFileSync(file, "utf8"));
  if (problems.length > 0) {
    console.error(`CSP violations in ${file}: ${problems.join(", ")}`);
    process.exit(1);
  }
  console.log(`${file}: no inline scripts, styles or event handlers`);
}
