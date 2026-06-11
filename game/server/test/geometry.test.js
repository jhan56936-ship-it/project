import { describe, it, expect } from "vitest";
import { rayAABB, blockAABB, playerAABB, resolveShot } from "../geometry.js";

describe("rayAABB", () => {
  const min = [-1, -1, -1], max = [1, 1, 1];
  it("hits a box straight ahead", () => {
    expect(rayAABB([0, 0, -5], [0, 0, 1], min, max)).toBeCloseTo(4);
  });
  it("misses a box to the side", () => {
    expect(rayAABB([0, 5, -5], [0, 0, 1], min, max)).toBeNull();
  });
  it("returns 0 when origin is inside", () => {
    expect(rayAABB([0, 0, 0], [0, 0, 1], min, max)).toBe(0);
  });
  it("ignores boxes behind the ray", () => {
    expect(rayAABB([0, 0, 5], [0, 0, 1], min, max)).toBeNull();
  });
});

describe("blockAABB", () => {
  it("converts pos(center-x, base-y, center-z) + size to min/max", () => {
    const { min, max } = blockAABB({ pos: [0, 0, 0], size: [4, 2, 6] });
    expect(min).toEqual([-2, 0, -3]);
    expect(max).toEqual([2, 2, 3]);
  });
});

describe("resolveShot", () => {
  const players = [
    { id: "a", alive: true, pos: [0, 0, 10] },
    { id: "b", alive: true, pos: [0, 0, 20] },
    { id: "dead", alive: false, pos: [0, 0, 5] },
  ];
  it("hits the nearest living player, skipping shooter and dead", () => {
    const r = resolveShot([0, 1, 0], [0, 0, 1], "self", players, [], 100);
    expect(r.hitId).toBe("a");
  });
  it("is blocked by a wall in front of the player", () => {
    const wall = { pos: [0, 0, 7], size: [10, 5, 1] };
    const r = resolveShot([0, 1, 0], [0, 0, 1], "self", players, [wall], 100);
    expect(r.hitId).toBeNull();
  });
  it("respects weapon range", () => {
    const r = resolveShot([0, 1, 0], [0, 0, 1], "self", players, [], 8);
    expect(r.hitId).toBeNull();
  });
});
