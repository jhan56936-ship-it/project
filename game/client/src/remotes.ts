import * as THREE from "three";
import type { NetPlayer } from "./net";

const TEAM_COLORS: Record<string, number> = { red: 0xd64545, blue: 0x4569d6 };
const DELAY_MS = 120;

interface Snap { t: number; pos: [number, number, number]; yaw: number; }

function makeAvatar(team: string, nick: string) {
  const g = new THREE.Group();
  const box = (w: number, h: number, d: number, c: number, x: number, y: number, z: number) => {
    const m = new THREE.Mesh(new THREE.BoxGeometry(w, h, d), new THREE.MeshLambertMaterial({ color: c }));
    m.position.set(x, y, z);
    g.add(m);
  };
  const tc = TEAM_COLORS[team] ?? 0x999999;
  box(0.22, 0.7, 0.22, 0x444455, -0.14, 0.35, 0);
  box(0.22, 0.7, 0.22, 0x444455, 0.14, 0.35, 0);
  box(0.6, 0.6, 0.32, tc, 0, 1.0, 0);
  box(0.2, 0.6, 0.2, 0xf0c987, -0.42, 1.0, 0);
  box(0.2, 0.6, 0.2, 0xf0c987, 0.42, 1.0, 0);
  box(0.42, 0.42, 0.42, 0xf0c987, 0, 1.55, 0);
  const canvas = document.createElement("canvas");
  canvas.width = 256; canvas.height = 64;
  const ctx = canvas.getContext("2d")!;
  ctx.font = "bold 36px sans-serif";
  ctx.textAlign = "center";
  ctx.fillStyle = "#fff";
  ctx.strokeStyle = "#000";
  ctx.lineWidth = 6;
  ctx.strokeText(nick, 128, 44);
  ctx.fillText(nick, 128, 44);
  const sprite = new THREE.Sprite(new THREE.SpriteMaterial({ map: new THREE.CanvasTexture(canvas), depthTest: false }));
  sprite.scale.set(2, 0.5, 1);
  sprite.position.y = 2.15;
  g.add(sprite);
  return g;
}

class RemotePlayer {
  group: THREE.Group;
  buf: Snap[] = [];
  constructor(team: string, nick: string) { this.group = makeAvatar(team, nick); }
}

export class Remotes {
  private players = new Map<string, RemotePlayer>();
  constructor(private scene: THREE.Scene) {}

  applyState(players: NetPlayer[], myId: string) {
    const seen = new Set<string>();
    for (const p of players) {
      if (p.id === myId) continue;
      seen.add(p.id);
      let rp = this.players.get(p.id);
      if (!rp) {
        rp = new RemotePlayer(p.team, p.nick);
        this.scene.add(rp.group);
        this.players.set(p.id, rp);
      }
      rp.buf.push({ t: performance.now(), pos: p.pos, yaw: p.yaw });
      if (rp.buf.length > 30) rp.buf.shift();
      rp.group.visible = p.alive;
    }
    for (const [id, rp] of this.players) {
      if (!seen.has(id)) { this.scene.remove(rp.group); this.players.delete(id); }
    }
  }

  update() {
    const t = performance.now() - DELAY_MS;
    for (const rp of this.players.values()) {
      const buf = rp.buf;
      while (buf.length >= 2 && buf[1].t <= t) buf.shift();
      if (buf.length === 0) continue;
      const a = buf[0];
      if (buf.length === 1) {
        rp.group.position.set(a.pos[0], a.pos[1], a.pos[2]);
        rp.group.rotation.y = a.yaw;
        continue;
      }
      const b = buf[1];
      const k = Math.max(0, Math.min(1, (t - a.t) / (b.t - a.t || 1)));
      rp.group.position.set(
        a.pos[0] + (b.pos[0] - a.pos[0]) * k,
        a.pos[1] + (b.pos[1] - a.pos[1]) * k,
        a.pos[2] + (b.pos[2] - a.pos[2]) * k,
      );
      rp.group.rotation.y = a.yaw + (b.yaw - a.yaw) * k;
    }
  }
}
