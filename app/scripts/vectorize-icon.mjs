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

function rasterizeSvg(svg, size, output) {
  const resvg = new Resvg(svg, {
    fitTo: { mode: "width", value: size },
    background: "transparent",
  });
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
    ["turgot-avatar.png", 96],
    ["icon.png", 512],
    ["tray-icon.png", 32],
    ["tray-icon@2x.png", 64],
  ];

  console.log("Rasterizing SVG → PNG…");
  for (const [name, size] of exports) {
    const out = path.join(assetsDir, name);
    rasterizeSvg(svg, size, out);
    console.log(`  ${name} (${size}px)`);
  }

  console.log("Done.");
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
