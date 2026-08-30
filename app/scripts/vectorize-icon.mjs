/**
 * Trace turgot_dictating.png → SVG (line art), then rasterize sharp PNGs.
 * Run from repo root: node app/scripts/vectorize-icon.mjs
 */

import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";
import potrace from "potrace";
import { Resvg } from "@resvg/resvg-js";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(__dirname, "..", "..");
const sourcePng = path.join(repoRoot, "assets", "turgot_dictating.png");
const assetsDir = path.join(repoRoot, "app", "src", "renderer", "assets");
const svgPath = path.join(assetsDir, "turgot-avatar.svg");

function traceToSvg(input, output) {
  return new Promise((resolve, reject) => {
    potrace.trace(
      input,
      {
        threshold: 180,
        turdSize: 2,
        optCurve: true,
        optTolerance: 0.2,
        color: "#1a1a1a",
        background: "transparent",
      },
      (err, svg) => {
        if (err) return reject(err);
        fs.writeFileSync(output, svg, "utf-8");
        resolve(svg);
      },
    );
  });
}

/** Embed traced art in a square canvas with optional background (for taskbar / window icons). */
function squareIconSvg(svg, size, { background = null, inset = 0.12, cornerRadius = 0.14 } = {}) {
  const viewBoxMatch = svg.match(/viewBox="([^"]+)"/);
  const viewBox = viewBoxMatch ? viewBoxMatch[1] : `0 0 ${size} ${size}`;
  const inner = svg.replace(/^[\s\S]*?<svg[^>]*>/, "").replace(/<\/svg>\s*$/, "");
  const pad = size * inset;
  const innerSize = size - pad * 2;
  const radius = size * cornerRadius;

  const bgRect = background
    ? `<rect width="${size}" height="${size}" fill="${background}" rx="${radius}"/>`
    : "";

  return `<svg xmlns="http://www.w3.org/2000/svg" width="${size}" height="${size}" viewBox="0 0 ${size} ${size}">
${bgRect}
<svg x="${pad}" y="${pad}" width="${innerSize}" height="${innerSize}" viewBox="${viewBox}" preserveAspectRatio="xMidYMid meet">
${inner}
</svg>
</svg>`;
}

function rasterizeSvg(svgString, output) {
  const resvg = new Resvg(svgString);
  const png = resvg.render().asPng();
  fs.writeFileSync(output, png);
}

async function main() {
  if (!fs.existsSync(sourcePng)) {
    console.error(`Missing source: ${sourcePng}`);
    process.exit(1);
  }
  fs.mkdirSync(assetsDir, { recursive: true });

  console.log("Tracing PNG → SVG (potrace)…");
  const svg = await traceToSvg(sourcePng, svgPath);
  console.log(`Wrote ${path.relative(repoRoot, svgPath)}`);

  const exports = [
    { name: "turgot-avatar.png", size: 96, background: null },
    { name: "icon.png", size: 512, background: "#ffffff" },
    { name: "tray-icon.png", size: 32, background: "#ffffff" },
    { name: "tray-icon@2x.png", size: 64, background: "#ffffff" },
  ];

  console.log("Rasterizing SVG → PNG…");
  for (const { name, size, background } of exports) {
    const out = path.join(assetsDir, name);
    const composed = background ? squareIconSvg(svg, size, { background }) : svg;
    rasterizeSvg(composed, out);
    console.log(`  ${name} (${size}px${background ? ", white bg" : ""})`);
  }

  console.log("Done.");
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
