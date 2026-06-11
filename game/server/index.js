import express from "express";
import http from "node:http";
import path from "node:path";
import crypto from "node:crypto";
import { fileURLToPath } from "node:url";
import { WebSocketServer } from "ws";
import { RoomManager } from "./rooms.js";
import { validate, sanitizeNick } from "./protocol.js";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const DIST = path.join(__dirname, "..", "client", "dist");

const app = express();
app.get("/healthz", (_req, res) => res.json({ ok: true }));
app.use(express.static(DIST));

const server = http.createServer(app);
const wss = new WebSocketServer({ server });
const rooms = new RoomManager();

function send(ws, msg) {
  if (ws.readyState === 1) ws.send(JSON.stringify(msg));
}
function broadcast(room, msg) {
  const s = JSON.stringify(msg);
  for (const ws of room.clients.values()) if (ws.readyState === 1) ws.send(s);
}

wss.on("connection", (ws) => {
  let room = null;
  let playerId = null;

  ws.on("message", (raw) => {
    let msg;
    try { msg = JSON.parse(raw); } catch { return; }
    if (!validate(msg)) return;
    const now = Date.now();

    if (msg.type === "join" && !room) {
      // quickJoin/create/join 모두 { room } 또는 { error } 반환 (정원·코드 고갈 검사는 RoomManager 책임)
      let res;
      if (msg.mode === "quick") res = rooms.quickJoin();
      else if (msg.mode === "create") res = rooms.create();
      else res = rooms.join(String(msg.code).trim());
      if (res.error) return send(ws, { type: "error", error: res.error });
      const r = res.room;
      playerId = crypto.randomUUID().slice(0, 8);
      room = r;
      room.clients.set(playerId, ws);
      const p = room.game.addPlayer(playerId, sanitizeNick(msg.nick), msg.weapon, now);
      send(ws, { type: "welcome", id: playerId, team: p.team, code: room.code, pos: p.pos });
      broadcast(room, { type: "joined", id: playerId, nick: p.nick, team: p.team });
      return;
    }

    if (!room || !playerId) return;
    if (msg.type === "input") {
      room.game.applyInput(playerId, msg.pos, msg.yaw, msg.pitch, msg.anim, now);
    } else if (msg.type === "shoot") {
      for (const e of room.game.shoot(playerId, msg.origin, msg.dir, now)) broadcast(room, e);
    } else if (msg.type === "weapon") {
      room.game.setWeapon(playerId, msg.weapon);
    }
  });

  ws.on("close", () => {
    if (room && playerId) {
      const r = room;
      rooms.leave(r, playerId);
      broadcast(r, { type: "left", id: playerId });
      room = null;
    }
  });
});

setInterval(() => {
  const now = Date.now();
  for (const room of rooms.rooms.values()) {
    for (const e of room.game.tick(now)) broadcast(room, e);
    broadcast(room, { type: "state", ...room.game.snapshot() });
  }
}, 50);

const PORT = process.env.PORT || 3000;
server.listen(PORT, () => console.log(`[block-rivals] listening on :${PORT}`));
