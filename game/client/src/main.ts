import * as THREE from "three";
import { createWorld } from "./world";
import { LocalPlayer } from "./player";
import { Net, NetPlayer } from "./net";
import { Remotes } from "./remotes";
import { Hud } from "./hud";
import { Shooting } from "./shooting";
import weapons from "../../shared/weapons.json";

const $ = (id: string) => document.getElementById(id)!;

const renderer = new THREE.WebGLRenderer({ antialias: true });
renderer.setSize(innerWidth, innerHeight);
document.body.appendChild(renderer.domElement);
const camera = new THREE.PerspectiveCamera(75, innerWidth / innerHeight, 0.1, 500);
const { scene, colliders } = createWorld();
const player = new LocalPlayer(camera, colliders);
const net = new Net();
const remotes = new Remotes(scene);
const hud = new Hud();
const shooting = new Shooting(scene, camera, (m) => net.send(m), hud);

let myId = "";
let alive = true;
let selectedWeapon = "rifle";
let lastPlayers: NetPlayer[] = [];
let showScores = false;
let respawnIv: ReturnType<typeof setInterval> | null = null;

// ── 메뉴: 무기 선택 ──
for (const [key, w] of Object.entries(weapons) as [string, { name: string }][]) {
  const btn = document.createElement("button");
  btn.textContent = w.name;
  if (key === selectedWeapon) btn.classList.add("sel");
  btn.onclick = () => {
    selectedWeapon = key;
    $("weaponPick").querySelectorAll("button").forEach((b) => b.classList.remove("sel"));
    btn.classList.add("sel");
  };
  $("weaponPick").appendChild(btn);
}

async function join(mode: "quick" | "create" | "join") {
  const nick = ($("nick") as HTMLInputElement).value.trim();
  if (!nick) { $("menuError").textContent = "닉네임을 입력하세요"; return; }
  try { await net.connect(); } catch { $("menuError").textContent = "서버에 연결할 수 없어요"; return; }
  const code = ($("codeInput") as HTMLInputElement).value.trim();
  net.send({ type: "join", mode, nick, code, weapon: selectedWeapon });
}
$("quickBtn").onclick = () => join("quick");
$("createBtn").onclick = () => join("create");
$("joinBtn").onclick = () => join("join");

net.on("error", (m) => {
  $("menuError").textContent = m.error === "no_room" ? "그 코드의 방이 없어요" : "방이 꽉 찼어요";
});

net.on("welcome", (m) => {
  myId = m.id;
  $("menu").classList.add("hidden");
  $("hud").classList.remove("hidden");
  $("roomCode").textContent = m.code === "PUBLIC" ? "공개 방" : `방 코드: ${m.code}`;
  player.spawn(m.pos);
  shooting.setWeapon(selectedWeapon);
  renderer.domElement.requestPointerLock();
});

net.on("state", (m) => {
  remotes.applyState(m.players, myId);
  lastPlayers = m.players;
  $("scoreRed").textContent = m.scores.red;
  $("scoreBlue").textContent = m.scores.blue;
  const me = (m.players as NetPlayer[]).find((p) => p.id === myId);
  if (me) $("health").textContent = String(me.hp);
  if (showScores) hud.scoreboard(lastPlayers, myId, true);
});

net.on("hit", (m) => { if (m.victim === myId) hud.flash(); });

function onDeath(killerNick: string) {
  alive = false;
  document.exitPointerLock();
  $("deathMsg").textContent = `${killerNick} 에게 당했다!`;
  hud.showDeath(weapons, selectedWeapon, (k) => {
    selectedWeapon = k;
    net.send({ type: "weapon", weapon: k });
  });
  let n = 3;
  hud.setRespawnTimer(n);
  if (respawnIv) clearInterval(respawnIv);
  respawnIv = setInterval(() => { n -= 1; if (n > 0) hud.setRespawnTimer(n); else clearInterval(respawnIv!); }, 1000);
}

net.on("kill", (m) => {
  hud.killfeed(`${m.killerNick} 🔫 ${m.victimNick}`);
  if (m.victim === myId) onDeath(m.killerNick);
  else if (m.killer === myId) hud.banner("+1 킬!", 800);
});

net.on("respawn", (m) => {
  if (m.id !== myId) return;
  alive = true;
  hud.hideDeath();
  player.spawn(m.pos);
  shooting.setWeapon(selectedWeapon);
  renderer.domElement.requestPointerLock();
});

net.on("gameover", (m) => hud.banner(m.winner === "red" ? "🏆 빨강 팀 승리!" : "🏆 파랑 팀 승리!"));
net.on("reset", () => hud.banner("새 게임 시작!", 2000));

net.onClose = () => {
  $("hud").classList.add("hidden");
  $("menu").classList.remove("hidden");
  $("menuError").textContent = "연결이 끊겼어요 — 새로고침 후 다시 입장하세요";
};

addEventListener("keydown", (e) => {
  if (e.code === "Tab") { e.preventDefault(); showScores = true; hud.scoreboard(lastPlayers, myId, true); }
});
addEventListener("keyup", (e) => {
  if (e.code === "Tab") { showScores = false; hud.scoreboard(lastPlayers, myId, false); }
});

renderer.domElement.addEventListener("click", () => {
  if (myId && alive) renderer.domElement.requestPointerLock();
});

setInterval(() => {
  if (!myId || !alive) return;
  net.send({
    type: "input",
    pos: [player.pos.x, player.pos.y, player.pos.z],
    yaw: player.yaw, pitch: player.pitch, anim: player.anim,
  });
}, 50);

addEventListener("resize", () => {
  renderer.setSize(innerWidth, innerHeight);
  camera.aspect = innerWidth / innerHeight;
  camera.updateProjectionMatrix();
});

let last = performance.now();
renderer.setAnimationLoop(() => {
  const now = performance.now();
  const dt = Math.min((now - last) / 1000, 0.05);
  last = now;
  const locked = !!document.pointerLockElement;
  player.enabled = locked && alive;
  player.update(dt);
  remotes.update();
  shooting.update(locked && alive && !!myId);
  renderer.render(scene, camera);
});
