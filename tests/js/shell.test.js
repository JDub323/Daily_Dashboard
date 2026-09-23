// The phone's offline shell covers the phone's module graph.
//
// This is a test about a failure that cannot be noticed by using the app.
//
// `web/m/sw.js` lists the files cached so `/m` opens with the laptop off. If
// that list is missing something the app imports, nothing breaks on wifi: the
// service worker's fetch handler caches whatever it fetches on the way past, so
// the missing file is quietly there anyway. It breaks on the first cold open
// after a `VERSION` bump with no server — the cache is fresh, the file was
// never in `SHELL`, the ES module graph fails whole, and the screen is blank.
// That is the one morning the feature exists for, and by then there is no way
// to fix it from the phone.
//
// It had happened. `m/app.js` imported two pure functions from `app/quicklog.js`,
// which imports `api.js`, `capture.js`, `nav.js`, `store.js` and `tabs.js` —
// five desktop modules, none of them listed, dragged in for ten lines of
// branching logic. The functions live in `logs.js` now, and this test is what
// stops the next import from doing it again.
//
// So: walk the imports out of every entry point in `SHELL`, and assert the
// closure is a subset of `SHELL`. No bundler, no parser — these are hand-written
// ES modules with static imports at the top, and a regex over `from "..."` reads
// them exactly. If that ever stops being true, this test is the thing that
// should be rewritten rather than deleted.

import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { describe, it } from "node:test";
import { fileURLToPath } from "node:url";

// `fileURLToPath` rather than `.pathname`, which hands back a percent-encoded
// string — this checkout lives under "Claude Playgrounds", and `%20` is not a
// directory.
const ROOT = fileURLToPath(new URL("../../", import.meta.url));
const M = path.join(ROOT, "web", "m");

const read = (file) => fs.readFileSync(file, "utf8");

/** The `SHELL` array out of `sw.js`, as absolute paths. */
function shell() {
  const source = read(path.join(M, "sw.js"));
  const block = source.match(/const SHELL = \[([\s\S]*?)\n\];/);
  assert.ok(block, "sw.js no longer has a `const SHELL = [...]` to read");
  const listed = [...block[1].matchAll(/"([^"]+)"/g)].map((m) => m[1]);
  assert.ok(listed.length > 5, "SHELL parsed as suspiciously short");
  return listed;
}

/** Static `import ... from "./x.js"` specifiers in one file. */
const importsOf = (file) =>
  [...read(file).matchAll(/^\s*(?:import|export)\b[^;]*?\bfrom\s+"([^"]+)"/gm)]
    .map((m) => m[1])
    .filter((spec) => spec.startsWith("."));

describe("the phone's offline shell", () => {
  const listed = shell();
  // `"./"` is the navigation, served as index.html; it is not a file to walk.
  const files = listed.filter((p) => p !== "./").map((p) => path.resolve(M, p));

  it("lists only files that exist", () => {
    for (const file of files) {
      assert.ok(fs.existsSync(file), `SHELL lists a file that is not there: ${file}`);
    }
  });

  it("covers everything the app imports, transitively", () => {
    const cached = new Set(files.map((f) => path.normalize(f)));
    const seen = new Set();
    const missing = [];

    // Start from the JS in the shell rather than from `app.js` alone: the
    // service worker is entitled to list an entry point the app reaches only
    // through another one, and every one of them has the same obligation.
    const queue = files.filter((f) => f.endsWith(".js"));

    while (queue.length) {
      const file = queue.pop();
      if (seen.has(file)) continue;
      seen.add(file);
      for (const spec of importsOf(file)) {
        const target = path.normalize(path.resolve(path.dirname(file), spec));
        if (!cached.has(target)) {
          missing.push(`${path.relative(ROOT, file)} imports ${spec}, which is not in SHELL`);
          continue;
        }
        queue.push(target);
      }
    }

    assert.deepEqual(missing, [], `the offline app would fail to load:\n  ${missing.join("\n  ")}`);
  });

  it("stamps the title screen with the version that decides the cache", () => {
    // `app.js` prints `BUILD` along the foot of the splash so the phone can be
    // asked which build it is running — the question a cached shell otherwise
    // makes unanswerable, since a stale app and a fresh one look identical and
    // you find out by hitting the bug again.
    //
    // That is only worth reading if it is the same string as the `VERSION` the
    // shell is cached under. `sw.js` is a classic worker and cannot import a
    // module, so the constant is declared twice and this is what stops the two
    // from drifting: a stamp that can lie about the code under it is worse than
    // no stamp, because it is trusted.
    const worker = read(path.join(M, "sw.js"));
    const app = read(path.join(M, "app.js"));

    const version = worker.match(/const VERSION = "([^"]+)"/);
    const build = app.match(/const BUILD = "([^"]+)"/);
    assert.ok(version, 'sw.js no longer has a `const VERSION = "..."` to read');
    assert.ok(build, 'app.js no longer has a `const BUILD = "..."` to read');
    assert.equal(
      build[1],
      version[1],
      "app.js BUILD and sw.js VERSION have drifted — bump both, or the splash "
        + "reports a build the phone is not running",
    );
  });

  it("paints the build without waiting for the laptop", () => {
    // The stamp exists to be read with the laptop off, so it cannot be painted
    // from the health panel. `paintBuild` is called at boot, and again after
    // `paintSplash` — which is the dashboard's own module and writes the fight
    // date into the same element on its way past.
    const app = read(path.join(M, "app.js"));
    const boot = app.search(/^initSplash\(.*\);$/m);
    assert.ok(boot > 0, "app.js no longer calls initSplash()");
    assert.match(
      app.slice(boot, boot + 200),
      /paintBuild\(\);/,
      "the build is no longer painted at boot, so it is absent exactly when it is worth reading",
    );
    const after = app.indexOf("paintSplash(panel.plan)");
    assert.ok(after > 0, "app.js no longer calls paintSplash");
    assert.match(
      app.slice(after, after + 200),
      /paintBuild\(\);/,
      "paintSplash overwrites the foot with the fight date and nothing puts the build back",
    );
  });

  it("does not reach the desktop's store, nav or HTTP client", () => {
    // A narrower statement of the same rule, and the one worth failing loudly:
    // those four modules are the top of the dashboard's dependency cone, and an
    // import of any of them from the phone means several hundred lines and a
    // websocket have just become load-bearing for logging a meal in a gym.
    const forbidden = ["api.js", "store.js", "nav.js", "tabs.js", "capture.js"];
    for (const file of files.filter((f) => f.endsWith(".js"))) {
      for (const spec of importsOf(file)) {
        const base = path.basename(spec);
        assert.ok(
          !forbidden.includes(base),
          `${path.relative(ROOT, file)} imports ${spec} — see the head of sw.js`,
        );
      }
    }
  });
});
