import assert from "node:assert/strict";
import test from "node:test";

import { mergeModelOptions } from "./modelOptions.ts";


test("adds the refreshed catalog while preserving both configured models", () => {
  assert.deepEqual(
    mergeModelOptions(
      ["new-model", "gemini-3.5-flash"],
      "configured-routine",
      "configured-judgment",
    ),
    ["configured-judgment", "configured-routine", "gemini-3.5-flash", "new-model"],
  );
});


test("keeps configured models available when catalog refresh returns nothing", () => {
  assert.deepEqual(
    mergeModelOptions([], "configured-routine", "configured-judgment"),
    ["configured-judgment", "configured-routine"],
  );
});
