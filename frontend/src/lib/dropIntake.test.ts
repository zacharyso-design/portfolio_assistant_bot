import assert from "node:assert/strict";
import test from "node:test";

import { EMPTY_DROP_WARNING, NON_FILE_DROP_WARNING, intakeFromDrop } from "./dropIntake.ts";


test("a drop with files passes them through without a warning", () => {
  const file = new File(["Number,Short description"], "snow-export.csv", { type: "text/csv" });
  const intake = intakeFromDrop([file]);
  assert.deepEqual(intake.files, [file]);
  assert.equal(intake.warning, "");
});


test("an empty drop with no other payload surfaces the OneDrive placeholder warning", () => {
  const intake = intakeFromDrop([]);
  assert.deepEqual(intake.files, []);
  assert.equal(intake.warning, EMPTY_DROP_WARNING);
});


test("an empty drop carrying non-file payload gets the Outlook/text advice instead", () => {
  // Outlook emails and text selections arrive with zero files but with
  // payload types; blaming OneDrive would send the user chasing the wrong fix.
  const intake = intakeFromDrop([], true);
  assert.equal(intake.warning, NON_FILE_DROP_WARNING);
  assert.match(NON_FILE_DROP_WARNING, /\.msg/);
});


test("both warnings tell the user what to do, never blaming the app", () => {
  assert.match(EMPTY_DROP_WARNING, /Always keep on this device/);
  assert.match(EMPTY_DROP_WARNING, /Choose file/);
  assert.match(NON_FILE_DROP_WARNING, /Choose file/);
});
