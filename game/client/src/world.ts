import * as THREE from "three";
import mapData from "../../shared/map.json";

export interface BlockBox { min: THREE.Vector3; max: THREE.Vector3; }

export function createWorld() {
  const scene = new THREE.Scene();
  scene.background = new THREE.Color(0x87ceeb);
  scene.fog = new THREE.Fog(0x87ceeb, 70, 170);
  scene.add(new THREE.HemisphereLight(0xffffff, 0x88aa66, 0.9));
  const sun = new THREE.DirectionalLight(0xffffff, 1.2);
  sun.position.set(30, 50, 20);
  scene.add(sun);

  const colliders: BlockBox[] = [];
  for (const b of mapData.blocks) {
    const [x, y, z] = b.pos;
    const [sx, sy, sz] = b.size;
    const mesh = new THREE.Mesh(
      new THREE.BoxGeometry(sx, sy, sz),
      new THREE.MeshLambertMaterial({ color: b.color }),
    );
    mesh.position.set(x, y + sy / 2, z);
    scene.add(mesh);
    colliders.push({
      min: new THREE.Vector3(x - sx / 2, y, z - sz / 2),
      max: new THREE.Vector3(x + sx / 2, y + sy, z + sz / 2),
    });
  }
  return { scene, colliders, mapData };
}
