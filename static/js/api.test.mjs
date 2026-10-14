// T-09: the JSON fetch wrapper turns `{error: {code, message, detail}}` (§6)
// into an ApiError whose `fields` the form shows inline.

import { test } from "node:test";
import assert from "node:assert/strict";

import { ApiError, getJSON, postJSON, putJSON, request } from "./api.js";

function stubFetch(impl) {
  const real = globalThis.fetch;
  globalThis.fetch = impl;
  return () => {
    globalThis.fetch = real;
  };
}

const jsonResponse = (status, body) => ({
  ok: status < 400,
  status,
  json: async () => body,
});

test("a successful request returns the parsed body", async () => {
  const calls = [];
  const restore = stubFetch(async (path, opts) => {
    calls.push([path, opts]);
    return jsonResponse(200, { tags: [] });
  });
  try {
    assert.deepEqual(await getJSON("/api/tags?year=2026"), { tags: [] });
    assert.equal(calls[0][0], "/api/tags?year=2026");
    assert.equal(calls[0][1].method, "GET");
    assert.equal(calls[0][1].body, undefined);

    await postJSON("/api/tags", { tag_type_id: "deer_regular" });
    assert.equal(calls[1][1].method, "POST");
    assert.equal(calls[1][1].body, '{"tag_type_id":"deer_regular"}');
    assert.equal(calls[1][1].headers["Content-Type"], "application/json");

    await putJSON("/api/profile", { youth: true });
    assert.equal(calls[2][1].method, "PUT");
  } finally {
    restore();
  }
});

test("a validation error carries the per-field detail", async () => {
  const restore = stubFetch(async () =>
    jsonResponse(400, {
      error: {
        code: "validation_failed",
        message: "fix the highlighted fields",
        detail: { zone: "required for this tag type" },
      },
    }),
  );
  try {
    await assert.rejects(
      () => postJSON("/api/tags", {}),
      (err) => {
        assert.ok(err instanceof ApiError);
        assert.equal(err.code, "validation_failed");
        assert.equal(err.status, 400);
        assert.equal(err.message, "fix the highlighted fields");
        assert.deepEqual(err.fields, { zone: "required for this tag type" });
        return true;
      },
    );
  } finally {
    restore();
  }
});

test("a list detail is not mistaken for field errors", async () => {
  const restore = stubFetch(async () =>
    jsonResponse(400, { error: { code: "bad_request", message: "nope", detail: ["resident"] } }),
  );
  try {
    await assert.rejects(
      () => request("PUT", "/api/profile", {}),
      (err) => {
        assert.deepEqual(err.detail, ["resident"]);
        assert.deepEqual(err.fields, {});
        return true;
      },
    );
  } finally {
    restore();
  }
});

test("a dead server reports offline, not a crash", async () => {
  const restore = stubFetch(async () => {
    throw new TypeError("fetch failed");
  });
  try {
    await assert.rejects(
      () => getJSON("/api/tags"),
      (err) => err instanceof ApiError && err.code === "offline",
    );
  } finally {
    restore();
  }
});

test("a non-JSON failure still raises a readable ApiError", async () => {
  const restore = stubFetch(async () => ({
    ok: false,
    status: 500,
    json: async () => {
      throw new SyntaxError("not json");
    },
  }));
  try {
    await assert.rejects(
      () => getJSON("/api/tags"),
      (err) => err.status === 500 && err.message.includes("GET /api/tags failed (500)"),
    );
  } finally {
    restore();
  }
});
