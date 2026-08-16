import assert from "node:assert/strict";
import test from "node:test";

import { bulkDismissTargets } from "./bulkDismiss.ts";


test("a repeated kind becomes a bulk target with a readable label", () => {
  const items = [
    ...Array.from({ length: 5 }, () => ({ kind: "malformed_llm" })),
    { kind: "routing" },
  ];
  assert.deepEqual(bulkDismissTargets(items), [
    { kind: "malformed_llm", label: "malformed llm", count: 5 },
  ]);
});


test("small groups stay individual decisions", () => {
  const items = [{ kind: "routing" }, { kind: "routing" }, { kind: "project_fit" }];
  assert.deepEqual(bulkDismissTargets(items), []);
});


test("multiple flooded kinds sort by size", () => {
  const items = [
    ...Array.from({ length: 3 }, () => ({ kind: "snow_invalid_row" })),
    ...Array.from({ length: 7 }, () => ({ kind: "malformed_llm" })),
  ];
  assert.deepEqual(bulkDismissTargets(items).map(target => target.kind), [
    "malformed_llm", "snow_invalid_row",
  ]);
});
