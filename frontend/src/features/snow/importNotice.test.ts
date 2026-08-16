import assert from "node:assert/strict";
import test from "node:test";

import { emptyImportNotice } from "./importNotice.ts";

const RESULT = {
  tickets_read: 0, new_comments_applied: 0, tickets_unchanged: 0,
  review_or_error_count: 0, pending_ai: 0, affected_projects: [],
  review_item_ids: [],
};


test("a zero-ticket import explains itself", () => {
  const notice = emptyImportNotice(RESULT);
  assert.match(notice, /no ticket rows/);
  assert.match(notice, /export again/);
});


test("a real import shows no notice", () => {
  assert.equal(emptyImportNotice({ ...RESULT, tickets_read: 172 }), "");
});


test("no result means no notice", () => {
  assert.equal(emptyImportNotice(null), "");
});
