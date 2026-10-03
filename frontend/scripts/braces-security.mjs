import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { dirname, join } from "node:path";

// Backport of micromatch/braces#72, preserving 3.0.3 stringify semantics.
const hashes = {
  compile: "8e1175e47b4e7c2feffc4be4a2e02f3f46d106594cef7081ab0d43649dfc7fd8",
  constants: "f9fb688959232eee3e6ad7906a5b0e3234815db49ee857ef86983d65b917dc7c",
  expand: "5eeb19f310a6295eea0b81fc3378571ab7cbdf4acf269e169e362522b9f55c65",
  parse: "043a67f0c94aa265a180a1775fb78560e4537227cc91b84555e1e7f5f8c04cdc",
  stringify: "1fa4166052be79a9d56b64c186cbb5534d9b1b1eb16681421f5e281e070988c5",
};
const require = createRequire(import.meta.url);

export function verifyPatchedBraces() {
  const tailwind = createRequire(require.resolve("tailwindcss"));
  const fastGlob = createRequire(tailwind.resolve("fast-glob"));
  const consumers = [
    createRequire(tailwind.resolve("chokidar")),
    createRequire(tailwind.resolve("micromatch")),
    createRequire(fastGlob.resolve("micromatch")),
  ];
  const files = new Set(consumers.map((consumer) => consumer.resolve("braces")));
  for (const entry of files) {
    const directory = dirname(entry);
    const metadata = JSON.parse(readFileSync(join(directory, "package.json"), "utf8"));
    assert.equal(metadata.version, "3.0.3", "Reassess mitigation after a braces release");
    for (const [name, expected] of Object.entries(hashes)) {
      const source = readFileSync(join(directory, "lib", `${name}.js`), "utf8").replace(/\r\n/g, "\n");
      assert.equal(createHash("sha256").update(source).digest("hex"), expected, `Unverified braces ${name}`);
    }
    checkDepthProtection(require(entry));
  }
}

function checkDepthProtection(braces) {
  for (const [open, close] of [["{", "}"], ["(", ")"], ["{(", ")}"]]) {
    const repetitions = open.length === 2 ? 50 : 100;
    const boundary = open.repeat(repetitions) + "a" + close.repeat(repetitions);
    assert.doesNotThrow(() => braces.compile(boundary));
    assert.doesNotThrow(() => braces.expand(boundary));
    const excessive = open.repeat(repetitions + 1) + "a" + close.repeat(repetitions + 1);
    for (const method of ["parse", "compile", "expand", "stringify"]) {
      assert.throws(() => braces[method](excessive), /exceeds max depth/);
    }
  }
  for (const method of ["parse", "compile", "expand", "stringify"]) {
    assert.throws(() => braces[method]("{".repeat(4500) + "a" + "}".repeat(4500)), /exceeds max depth/);
  }
  for (const type of ["brace", "paren"]) {
    for (const root of [false, true]) {
      for (const method of ["compile", "expand", "stringify"]) {
        let ast = { type: "text", value: "a" };
        for (let depth = 0; depth < 101; depth++) ast = { type, nodes: [ast] };
        if (root) ast = { type: "root", nodes: [ast] };
        assert.throws(() => braces[method](ast), /exceeds max depth/);
      }
    }
  }
  for (const literal of ["\\{".repeat(101), '"' + "{".repeat(101) + '"', "[" + "{".repeat(101) + "]"]) {
    assert.doesNotThrow(() => braces.compile(literal));
  }
  assert.equal(braces.stringify("{{a}}", { escapeInvalid: true }), "{{a}}");
  assert.equal(braces.stringify("{1..8}", { escapeInvalid: true }), "{1..8}");
  assert.equal(braces.compile("src/{a,b}/**/*.{js,tsx}"), "src/(a|b)/**/*.(js|tsx)");
  assert.deepEqual(braces.expand("{a,{b,c}}"), ["a", "b", "c"]);
  assert.deepEqual(braces.expand("{1..3}"), ["1", "2", "3"]);
}
