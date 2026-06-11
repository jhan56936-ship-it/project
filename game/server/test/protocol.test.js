import { describe, it, expect } from "vitest";
import { validate, sanitizeNick } from "../protocol.js";

describe("sanitizeNick", () => {
  it("trims and caps at 12 chars", () => {
    expect(sanitizeNick("  철수  ")).toBe("철수");
    expect(sanitizeNick("a".repeat(30))).toBe("a".repeat(12));
  });
  it("rejects empty or non-string", () => {
    expect(sanitizeNick("   ")).toBeNull();
    expect(sanitizeNick(5)).toBeNull();
  });
});

describe("validate", () => {
  it("accepts well-formed messages", () => {
    expect(validate({ type: "join", mode: "quick", nick: "철수" })).toBe(true);
    expect(validate({ type: "join", mode: "join", nick: "철수", code: "1234" })).toBe(true);
    expect(validate({ type: "input", pos: [0, 1, 2], yaw: 0, pitch: 0, anim: "run" })).toBe(true);
    expect(validate({ type: "shoot", origin: [0, 0, 0], dir: [0, 0, 1] })).toBe(true);
    expect(validate({ type: "weapon", weapon: "smg" })).toBe(true);
  });
  it("rejects malformed messages", () => {
    expect(validate(null)).toBe(false);
    expect(validate({ type: "hack" })).toBe(false);
    expect(validate({ type: "join", mode: "join", nick: "x" })).toBe(false);
    expect(validate({ type: "input", pos: [0, NaN, 2], yaw: 0, pitch: 0, anim: "run" })).toBe(false);
    expect(validate({ type: "shoot", origin: [0, 0], dir: [0, 0, 1] })).toBe(false);
  });
});
