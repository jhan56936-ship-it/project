import { describe, it, expect } from "vitest";
import { RoomManager, MAX_PLAYERS } from "../rooms.js";

describe("RoomManager", () => {
  it("quick join reuses the PUBLIC room", () => {
    const rm = new RoomManager();
    expect(rm.quickJoin()).toBe(rm.quickJoin());
    expect(rm.quickJoin().code).toBe("PUBLIC");
  });
  it("create makes a 4-digit code room", () => {
    const rm = new RoomManager();
    expect(rm.create().code).toMatch(/^\d{4}$/);
  });
  it("join by unknown code fails", () => {
    expect(new RoomManager().join("0000").error).toBe("no_room");
  });
  it("join rejects a full room", () => {
    const rm = new RoomManager();
    const room = rm.create();
    for (let i = 0; i < MAX_PLAYERS; i++) room.clients.set(`p${i}`, {});
    expect(rm.join(room.code).error).toBe("full");
  });
  it("removes a room when the last player leaves", () => {
    const rm = new RoomManager();
    const room = rm.create();
    room.clients.set("p1", {});
    room.game.addPlayer("p1", "철수", "rifle", 0);
    rm.leave(room, "p1");
    expect(rm.rooms.has(room.code)).toBe(false);
  });
});
