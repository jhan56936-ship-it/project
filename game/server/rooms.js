import { Game } from "./game.js";

export const MAX_PLAYERS = 10;

export class RoomManager {
  constructor(rng = Math.random) {
    this.rooms = new Map(); // code -> { code, game, clients: Map<playerId, ws> }
    this.rng = rng;
  }

  makeCode() {
    let code;
    do { code = String(Math.floor(this.rng() * 9000) + 1000); } while (this.rooms.has(code));
    return code;
  }

  getOrCreate(code) {
    if (!this.rooms.has(code)) {
      this.rooms.set(code, { code, game: new Game(), clients: new Map() });
    }
    return this.rooms.get(code);
  }

  quickJoin() { return this.getOrCreate("PUBLIC"); }
  create() { return this.getOrCreate(this.makeCode()); }

  join(code) {
    const room = this.rooms.get(code);
    if (!room) return { error: "no_room" };
    if (room.clients.size >= MAX_PLAYERS) return { error: "full" };
    return { room };
  }

  leave(room, playerId) {
    room.clients.delete(playerId);
    room.game.removePlayer(playerId);
    if (room.clients.size === 0) this.rooms.delete(room.code);
  }
}
