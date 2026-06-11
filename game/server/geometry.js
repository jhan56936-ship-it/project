// 슬랩 기법 ray-vs-AABB. 전방 교차 거리(t>=0) 또는 null.
export function rayAABB(origin, dir, min, max) {
  let tmin = 0;
  let tmax = Infinity;
  for (let i = 0; i < 3; i++) {
    if (Math.abs(dir[i]) < 1e-9) {
      if (origin[i] < min[i] || origin[i] > max[i]) return null;
    } else {
      let t1 = (min[i] - origin[i]) / dir[i];
      let t2 = (max[i] - origin[i]) / dir[i];
      if (t1 > t2) [t1, t2] = [t2, t1];
      tmin = Math.max(tmin, t1);
      tmax = Math.min(tmax, t2);
      if (tmin > tmax) return null;
    }
  }
  return tmin;
}

export function blockAABB(block) {
  const [x, y, z] = block.pos;
  const [sx, sy, sz] = block.size;
  return { min: [x - sx / 2, y, z - sz / 2], max: [x + sx / 2, y + sy, z + sz / 2] };
}

export const PLAYER_W = 0.9;
export const PLAYER_H = 1.8;

export function playerAABB(pos) {
  const h = PLAYER_W / 2;
  return { min: [pos[0] - h, pos[1], pos[2] - h], max: [pos[0] + h, pos[1] + PLAYER_H, pos[2] + h] };
}

// 한 발(펠릿 1개)의 판정: 벽까지 거리 안에서 가장 가까운 생존자.
export function resolveShot(origin, dir, shooterId, players, blocks, range) {
  let limit = range;
  for (const b of blocks) {
    const { min, max } = blockAABB(b);
    const t = rayAABB(origin, dir, min, max);
    if (t !== null && t < limit) limit = t;
  }
  let hitId = null;
  let hitT = limit;
  for (const p of players) {
    if (p.id === shooterId || !p.alive) continue;
    const { min, max } = playerAABB(p.pos);
    const t = rayAABB(origin, dir, min, max);
    if (t !== null && t < hitT) {
      hitT = t;
      hitId = p.id;
    }
  }
  return { hitId, dist: hitT };
}
