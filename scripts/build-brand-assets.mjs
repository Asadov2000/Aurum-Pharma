import { readFile, writeFile, mkdir } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { createRequire } from "node:module";
import path from "node:path";

const root = fileURLToPath(new URL("../", import.meta.url));
const source = path.join(root, "assets/brand/12-8");
const check = process.argv.includes("--check");
const outputs = new Map();
const normalize = (value) => value.replace(/\r\n/gu, "\n");
const read = async (name) =>
  normalize(await readFile(path.join(source, name), "utf8"));
const content = (svg) =>
  svg.replace(/^<svg\b[^>]*>/u, "").replace(/<\/svg>\s*$/u, "");
const frame = (inside) =>
  `<svg xmlns="http://www.w3.org/2000/svg" width="256" height="256" viewBox="0 0 256 256" role="img" aria-label="Aurum Pharma">${inside}</svg>\n`;
for (const name of [
  "logo-color.svg",
  "logo-white.svg",
  "mark-color.svg",
  "mark-white.svg",
  "icon-24.svg",
]) {
  const svg = await read(name);
  outputs.set(`frontend/public/brand/v12-8/${name}`, svg);
  outputs.set(`website/src/assets/brand/${name}`, svg);
}
const small = content(await read("icon-24.svg"));
const favicon = frame(
  `<rect width="256" height="256" rx="56" fill="#FFFFFF"/><g transform="translate(10 10) scale(9.8333333333)">${small}</g>`,
);
const app = await read("app-white.svg");
const maskable = frame(
  `<rect width="256" height="256" fill="#00566B"/><g transform="translate(43.52 43.52) scale(.66)">${content(await read("mark-white.svg"))}</g>`,
);
outputs.set("frontend/public/icons/icon.svg", app);
outputs.set("frontend/public/icons/favicon-12-8.svg", favicon);
outputs.set("frontend/public/icons/icon-maskable.svg", maskable);
outputs.set("website/src/assets/favicon.svg", favicon);
for (const [name, data] of outputs) {
  const target = path.join(root, name);
  if (check) {
    if (normalize(await readFile(target, "utf8")) !== data)
      throw new Error(`Outdated brand asset: ${name}`);
  } else {
    await mkdir(path.dirname(target), { recursive: true });
    await writeFile(target, data);
  }
}

const pngs = [
  ["icon-192.png", 192, app],
  ["icon-512.png", 512, app],
  ["apple-touch-icon-180.png", 180, app],
  ["icon-maskable-512.png", 512, maskable],
];
if (process.argv.includes("--png")) {
  const frontendRequire = createRequire(
    path.join(root, "frontend/package.json"),
  );
  const playwrightRequire = createRequire(
    frontendRequire.resolve("@playwright/test/package.json"),
  );
  const { chromium } = playwrightRequire("playwright");
  const browser = await chromium.launch({ headless: true });
  try {
    for (const [name, size, svg] of pngs) {
      const page = await browser.newPage({
        viewport: { width: size, height: size },
        deviceScaleFactor: 1,
      });
      await page.setContent(
        `<style>html,body{margin:0;background:#00566B}svg{display:block;width:100vw;height:100vh}</style>${svg}`,
      );
      await page.screenshot({
        path: path.join(root, "frontend/public/icons", name),
      });
      await page.close();
    }
  } finally {
    await browser.close();
  }
}
for (const [name, size] of pngs) {
  const data = await readFile(path.join(root, "frontend/public/icons", name));
  if (
    data.toString("hex", 0, 8) !== "89504e470d0a1a0a" ||
    data.readUInt32BE(16) !== size ||
    data.readUInt32BE(20) !== size
  ) {
    throw new Error(`Invalid PNG size: ${name}`);
  }
}
console.log(
  `Brand 12.8: ${outputs.size} SVG assets consistent; 4 PNG sizes valid.`,
);
