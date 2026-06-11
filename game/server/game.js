import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { resolveShot } from "./geometry.js";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const SHARED = path.join(__dirname, "..", "shared");
export const WEAPONS = JSON.parse(fs.readFileSync(path.join(SHARED, "weapons.json"), "utf8"));
export const MAP = JSON.parse(fs.readFileSync(path.join(SHARED, "map.json"), "utf8"));

export const WIN_KILLS = 30;
export const RESPAWN_MS = 3000;
export const RESET_MS = 10000;
const MAX_SPEED = 14;        // 달리기 12 + 여유
const FIRE_TOLERANCE = 0.8;  // 네트워크 지터 허용 비율
const EYE_HEIGHT = 1.6;
const MAX_ORIGIN_DRIFT = 2.5;

export class Game {
  constructor({ map = MAP, weapons = WEAPONS, rng = Math.random } = {}) {
    this.map = map;
    this.weapons = weapons;
    this.rng = rng;
    this.players = new Map();
    this.scores = { red: 0, blue: 0 };
    this.winner = null;
    this.resetAt = null;
  }

  pickTeam() {
    let red = 0, blue = 0;
    for (const p of this.players.values()) p.team === "red" ? red++ : blue++;
    return red <= blue ? "red" : "blue";
  }

  spawnPos(team) {
    const list = this.map.spawns[team];
    return [...list[Math.floor(this.rng() * list.length) % list.length]];
  }

  addPlayer(id, nick, weapon, now) {
    const team = this.pickTeam();
    const p = {
      id, nick, team,
      weapon: this.weapons[weapon] ? weapon : "rifle",
      pos: this.spawnPos(team), yaw: 0, pitch: 0, anim: "idle",
      hp: 100, alive: true, kills: 0, deaths: 0,
      lastShotAt: 0, respawnAt: null, lastInputAt: now,
    };
    this.players.set(id, p);
    return p;
  }

  removePlayer(id) { this.players.delete(id); }

  setWeapon(id, weapon) {
    const p = this.players.get(id);
    if (p && !p.alive && this.weapons[weapon]) p.weapon = weapon;
  }

  applyInput(id, pos, yaw, pitch, anim, now) {
    const p = this.players.get(id);
    if (!p || !p.alive || this.winner) return;
    const dt = Math.max((now - p.lastInputAt) / 1000, 0.01);
    const dist = Math.hypot(pos[0] - p.pos[0], pos[2] - p.pos[2]);
    if (dist <= MAX_SPEED * dt * 1.5) {
      p.pos = [pos[0], Math.max(-5, Math.min(50, pos[1])), pos[2]];
    }
    const bx = this.map.bounds.x / 2, bz = this.map.bounds.z / 2;
    p.pos[0] = Math.max(-bx, Math.min(bx, p.pos[0]));
    p.pos[2] = Math.max(-bz, Math.min(bz, p.pos[2]));
    p.yaw = yaw; p.pitch = pitch; p.anim = anim;
    p.lastInputAt = now;
  }

  applySpread(dir, spread) {
    const d = [
      dir[0] + (this.rng() * 2 - 1) * spread,
      dir[1] + (this.rng() * 2 - 1) * spread,
      dir[2] + (this.rng() * 2 - 1) * spread,
    ];
    const len = Math.hypot(d[0], d[1], d[2]) || 1;
    return [d[0] / len, d[1] / len, d[2] / len];
  }

  shoot(id, origin, dir, now) {
    const events = [];
    const p = this.players.get(id);
    if (!p || !p.alive || this.winner) return events;
    const w = this.weapons[p.weapon];
    if (now - p.lastShotAt < w.fireDelayMs * FIRE_TOLERANCE) return events;
    const eye = [p.pos[0], p.pos[1] + EYE_HEIGHT, p.pos[2]];
    const drift = Math.hypot(origin[0] - eye[0], origin[1] - eye[1], origin[2] - eye[2]);
    if (drift > MAX_ORIGIN_DRIFT) return events;
    p.lastShotAt = now;

    const damageBy = new Map();
    const alive = [...this.players.values()];
    for (let i = 0; i < w.pellets; i++) {
      const d = this.applySpread(dir, w.spread);
      const { hitId } = resolveShot(origin, d, id, alive, this.map.blocks, w.range);
      if (hitId) damageBy.set(hitId, (damageBy.get(hitId) || 0) + w.damage);
    }

    for (const [victimId, dmg] of damageBy) {
      const v = this.players.get(victimId);
      if (!v || !v.alive) continue;
      v.hp = Math.max(0, v.hp - dmg);
      events.push({ type: "hit", victim: victimId, shooter: id, hp: v.hp });
      if (v.hp === 0) {
        v.alive = false;
        v.deaths += 1;
        v.respawnAt = now + RESPAWN_MS;
        p.kills += 1;
        this.scores[p.team] += 1;
        events.push({
          type: "kill", killer: id, killerNick: p.nick, victim: victimId,
          victimNick: v.nick, weapon: p.weapon, scores: { ...this.scores },
        });
        if (!this.winner && this.scores[p.team] >= WIN_KILLS) {
          this.winner = p.team;
          this.resetAt = now + RESET_MS;
          events.push({ type: "gameover", winner: p.team, scores: { ...this.scores } });
        }
      }
    }
    return events;
  }

  tick(now) {
    const events = [];
    for (const p of this.players.values()) {
      if (!p.alive && p.respawnAt !== null && now >= p.respawnAt && !this.winner) {
        p.alive = true; p.hp = 100; p.respawnAt = null;
        p.pos = this.spawnPos(p.team);
        p.lastInputAt = now;
        events.push({ type: "respawn", id: p.id, pos: p.pos });
      }
    }
    if (this.winner && now >= this.resetAt) {
      this.winner = null;
      this.resetAt = null;
      this.scores = { red: 0, blue: 0 };
      for (const p of this.players.values()) {
        p.kills = 0; p.deaths = 0; p.hp = 100; p.alive = true; p.respawnAt = null;
        p.pos = this.spawnPos(p.team);
        p.lastInputAt = now;
        // 클라이언트가 새 스폰 위치로 이동하도록 리스폰 이벤트도 함께 방송
        events.push({ type: "respawn", id: p.id, pos: p.pos });
      }
      events.push({ type: "reset" });
    }
    return events;
  }

  snapshot() {
    return {
      players: [...this.players.values()].map((p) => ({
        id: p.id, nick: p.nick, team: p.team, pos: p.pos, yaw: p.yaw, pitch: p.pitch,
        anim: p.anim, hp: p.hp, alive: p.alive, kills: p.kills, deaths: p.deaths, weapon: p.weapon,
      })),
      scores: this.scores,
      winner: this.winner,
    };
  }
}
