import * as THREE from "three";
import weapons from "../../shared/weapons.json";
import type { Hud } from "./hud";

type WeaponKey = keyof typeof weapons;

export class Shooting {
  weaponKey: WeaponKey = "rifle";
  ammo = weapons.rifle.magazine;
  private reloading = false;
  private reloadDoneAt = 0;
  private lastShot = 0;
  private mouseDown = false;
  private tracers: { line: THREE.Line; die: number }[] = [];

  constructor(
    private scene: THREE.Scene,
    private camera: THREE.PerspectiveCamera,
    private send: (m: object) => void,
    private hud: Hud,
  ) {
    addEventListener("mousedown", (e) => { if (e.button === 0) this.mouseDown = true; });
    addEventListener("mouseup", (e) => { if (e.button === 0) this.mouseDown = false; });
    addEventListener("keydown", (e) => { if (e.code === "KeyR") this.reload(); });
  }

  setWeapon(key: string) {
    if (!(key in weapons)) return;
    this.weaponKey = key as WeaponKey;
    this.ammo = weapons[this.weaponKey].magazine;
    this.reloading = false;
    this.hud.setAmmo(this.ammo, weapons[this.weaponKey].magazine);
  }

  reload() {
    const w = weapons[this.weaponKey];
    if (this.reloading || this.ammo === w.magazine) return;
    this.reloading = true;
    this.reloadDoneAt = performance.now() + w.reloadMs;
    this.hud.setReloading();
  }

  update(canShoot: boolean) {
    const now = performance.now();
    const w = weapons[this.weaponKey];
    if (this.reloading && now >= this.reloadDoneAt) {
      this.reloading = false;
      this.ammo = w.magazine;
      this.hud.setAmmo(this.ammo, w.magazine);
    }
    if (this.mouseDown && canShoot && !this.reloading && this.ammo > 0 && now - this.lastShot >= w.fireDelayMs) {
      if (!w.auto) this.mouseDown = false; // 단발 무기는 클릭당 1발
      this.lastShot = now;
      this.ammo -= 1;
      this.hud.setAmmo(this.ammo, w.magazine);
      const o = this.camera.position;
      const d = this.camera.getWorldDirection(new THREE.Vector3());
      this.send({ type: "shoot", origin: [o.x, o.y, o.z], dir: [d.x, d.y, d.z] });
      this.spawnTracer(o, d, Math.min(w.range, 80));
      if (this.ammo === 0) this.reload();
    }
    this.tracers = this.tracers.filter((t) => {
      if (now > t.die) { this.scene.remove(t.line); return false; }
      return true;
    });
  }

  private spawnTracer(origin: THREE.Vector3, dir: THREE.Vector3, len: number) {
    const start = origin.clone().addScaledVector(dir, 0.5).add(new THREE.Vector3(0, -0.15, 0));
    const end = origin.clone().addScaledVector(dir, len);
    const line = new THREE.Line(
      new THREE.BufferGeometry().setFromPoints([start, end]),
      new THREE.LineBasicMaterial({ color: 0xffe066 }),
    );
    this.scene.add(line);
    this.tracers.push({ line, die: performance.now() + 60 });
  }
}
