const assert = require("node:assert");
const { test } = require("node:test");
const { greet } = require("../../greet.js");

test("greets by name", () => {
  assert.strictEqual(greet("Rex"), "Hello, Rex!");
});

test("greets without a name", () => {
  assert.strictEqual(greet(""), "Hello!");
});
