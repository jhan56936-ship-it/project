import * as THREE from "three";
import type { BlockBox } from "./world";

const WALK = 8, SPRINT = 12, JUMP = 9, GRAVITY = 25;
const HALF = 0.45, HEIGHT = 1.8, EYE = 1.6;

export class LocalPlayer {
  pos = new THREE.Vector3(0, 5, 0);
  vel = new THREE.Vector3();
  yaw = 0;
  pitch = 0;
  grounded = false;
  enabled = false; // 살아있고 포인터락 상태일 때만 true
  private keys = new Set<string>();

  constructor(public camera: THREE.PerspectiveCamera, private colliders: BlockBox[]) {
    this.camera.rotation.order = "YXZ";
    addEventListener("keydown", (e) => this.keys.add(e.code));
    addEventListener("keyup", (e) => this.keys.delete(e.code));
    addEventListener("mousemove", (e) => {
      if (!this.enabled || !document.pointerLockElement) return;
      this.yaw -= e.movementX * 0.0023;
      this.pitch = Math.max(-1.5, Math.min(1.5, this.pitch - e.movementY * 0.0023));
    });
  }

  spawn(p: [number, number, number]) {
    this.pos.set(p[0], p[1] + 0.1, p[2]);
    this.vel.set(0, 0, 0);
  }

  get anim(): string {
    const moving = ["KeyW", "KeyA", "KeyS", "KeyD"].some((k) => this.keys.has(k));
    return this.enabled && moving ? "run" : "idle";
  }

  update(dt: number) {
    if (this.enabled) {
      const speed = this.keys.has("ShiftLeft") ? SPRINT : WALK;
      const f = new THREE.Vector3(-Math.sin(this.yaw), 0, -Math.cos(this.yaw));
      const r = new THREE.Vector3(-f.z, 0, f.x);
      const move = new THREE.Vector3();
      if (this.keys.has("KeyW")) move.add(f);
      if (this.keys.has("KeyS")) move.sub(f);
      if (this.keys.has("KeyD")) move.add(r);
      if (this.keys.has("KeyA")) move.sub(r);
      if (move.lengthSq() > 0) move.normalize().multiplyScalar(speed);
      this.vel.x = move.x;
      this.vel.z = move.z;
      if (this.keys.has("Space") && this.grounded) { this.vel.y = JUMP; this.grounded = false; }
    } else {
      this.vel.x = 0;
      this.vel.z = 0;
    }
    this.vel.y -= GRAVITY * dt;

    this.moveAxis("x", this.vel.x * dt);
    this.moveAxis("z", this.vel.z * dt);
    this.grounded = false;
    this.moveAxis("y", this.vel.y * dt);
    if (this.pos.y < -20) this.pos.set(0, 10, 0); // 낙사 안전망

    this.camera.position.set(this.pos.x, this.pos.y + EYE, this.pos.z);
    this.camera.rotation.y = this.yaw;
    this.camera.rotation.x = this.pitch;
  }

  private overlaps(b: BlockBox) {
    return this.pos.x + HALF > b.min.x && this.pos.x - HALF < b.max.x
      && this.pos.y + HEIGHT > b.min.y && this.pos.y < b.max.y
      && this.pos.z + HALF > b.min.z && this.pos.z - HALF < b.max.z;
  }

  private moveAxis(axis: "x" | "y" | "z", delta: number) {
    if (delta === 0) return;
    this.pos[axis] += delta;
    for (const b of this.colliders) {
      if (!this.overlaps(b)) continue;
      if (axis === "y") {
        if (delta < 0) { this.pos.y = b.max.y; this.vel.y = 0; this.grounded = true; }
        else { this.pos.y = b.min.y - HEIGHT; this.vel.y = 0; }
      } else if (axis === "x") {
        this.pos.x = delta > 0 ? b.min.x - HALF : b.max.x + HALF;
      } else {
        this.pos.z = delta > 0 ? b.min.z - HALF : b.max.z + HALF;
      }
    }
  }
}
