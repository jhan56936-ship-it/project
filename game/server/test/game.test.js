import { describe, it, expect } from "vitest";
import { Game, WIN_KILLS, RESPAWN_MS, RESET_MS } from "../game.js";

// rng를 0.5로 고정하면 spread 오프셋이 (2*0.5-1)=0 → 탄퍼짐 없음.
// 실제 맵은 중앙에 단상 블록이 있어 테스트 사선이 막히므로, 장애물 없는 테스트 맵 사용.
const TEST_MAP = {
  bounds: { x: 80, z: 80 },
  blocks: [],
  spawns: { red: [[-34, 0, 0]], blue: [[34, 0, 0]] },
};
const makeGame = () => new Game({ map: TEST_MAP, rng: () => 0.5 });

// a(red)가 b(blue)를 정면에서 쏘도록 배치
function setupDuel(g, now = 1000) {
  const a = g.addPlayer("a", "철수", "rifle", now);
  const b = g.addPlayer("b", "영희", "rifle", now);
  a.pos = [0, 0, 0];
  b.pos = [0, 0, 10];
  return { a, b };
}
const shootOnce = (g, now) => g.shoot("a", [0, 1.6, 0], [0, 0, 1], now);

describe("teams", () => {
  it("balances teams on join", () => {
    const g = makeGame();
    const teams = ["p1", "p2", "p3", "p4"].map((id) => g.addPlayer(id, id, "rifle", 0).team);
    expect(teams.filter((t) => t === "red").length).toBe(2);
    expect(teams.filter((t) => t === "blue").length).toBe(2);
  });
});

describe("shooting", () => {
  it("applies rifle damage on hit", () => {
    const g = makeGame();
    const { b } = setupDuel(g);
    const events = shootOnce(g, 2000);
    expect(b.hp).toBe(80);
    expect(events.some((e) => e.type === "hit" && e.victim === "b")).toBe(true);
  });
  it("enforces fire rate", () => {
    const g = makeGame();
    const { b } = setupDuel(g);
    shootOnce(g, 2000);
    shootOnce(g, 2010); // 150ms 쿨다운 무시하고 연사 시도
    expect(b.hp).toBe(80);
  });
  it("rejects an origin far from the player (cheat guard)", () => {
    const g = makeGame();
    const { b } = setupDuel(g);
    g.shoot("a", [0, 1.6, 9], [0, 0, 1], 2000); // 9m 떨어진 위치에서 발사 주장
    expect(b.hp).toBe(100);
  });
  it("kills at 0hp, scores the kill, schedules respawn", () => {
    const g = makeGame();
    const { a, b } = setupDuel(g);
    let now = 2000;
    let events = [];
    for (let i = 0; i < 5; i++) { events = shootOnce(g, now); now += 200; }
    expect(b.alive).toBe(false);
    expect(b.respawnAt).toBe(now - 200 + RESPAWN_MS);
    expect(a.kills).toBe(1);
    expect(b.deaths).toBe(1);
    expect(g.scores[a.team]).toBe(1);
    expect(events.some((e) => e.type === "kill" && e.killer === "a")).toBe(true);
  });
  it("ignores friendly fire entirely", () => {
    const g = makeGame();
    const { a, b } = setupDuel(g);
    b.team = a.team; // b를 아군으로 만들고 정면에서 사격
    const events = shootOnce(g, 2000);
    expect(b.hp).toBe(100);
    expect(events).toHaveLength(0);
  });
  it("dead player cannot shoot", () => {
    const g = makeGame();
    const { a, b } = setupDuel(g);
    a.alive = false;
    const events = shootOnce(g, 2000);
    expect(events).toHaveLength(0);
    expect(b.hp).toBe(100);
  });
  it("sniper kills in one shot", () => {
    const g = makeGame();
    const { b } = setupDuel(g);
    g.players.get("a").weapon = "sniper";
    shootOnce(g, 2000);
    expect(b.alive).toBe(false);
  });
});

describe("respawn & reset", () => {
  it("respawns a dead player after RESPAWN_MS at a team spawn", () => {
    const g = makeGame();
    const { b } = setupDuel(g);
    b.alive = false; b.hp = 0; b.respawnAt = 5000;
    const events = g.tick(5001);
    expect(b.alive).toBe(true);
    expect(b.hp).toBe(100);
    expect(g.map.spawns[b.team].some((s) => s[0] === b.pos[0] && s[2] === b.pos[2])).toBe(true);
    expect(events.some((e) => e.type === "respawn" && e.id === "b")).toBe(true);
  });
  it("declares winner at WIN_KILLS and resets after RESET_MS", () => {
    const g = makeGame();
    const { a } = setupDuel(g);
    g.scores[a.team] = WIN_KILLS - 1;
    const events = shootOnce(g, 2000).concat(
      shootOnce(g, 2200), shootOnce(g, 2400), shootOnce(g, 2600), shootOnce(g, 2800));
    expect(g.winner).toBe(a.team);
    expect(events.some((e) => e.type === "gameover" && e.winner === a.team)).toBe(true);
    const resetEvents = g.tick(2800 + RESET_MS + 1);
    expect(g.winner).toBeNull();
    expect(g.scores).toEqual({ red: 0, blue: 0 });
    expect(a.kills).toBe(0);
    expect(resetEvents.some((e) => e.type === "reset")).toBe(true);
  });
  it("only allows weapon change while dead", () => {
    const g = makeGame();
    const { b } = setupDuel(g);
    g.setWeapon("b", "shotgun");
    expect(b.weapon).toBe("rifle");
    b.alive = false;
    g.setWeapon("b", "shotgun");
    expect(b.weapon).toBe("shotgun");
  });
});
