// 사용법: node test-bot.js [봇 수] [서버 주소]
// 예: node test-bot.js 4 ws://localhost:3000
import WebSocket from "ws";

const count = Number(process.argv[2] || 1);
const url = process.argv[3] || "ws://localhost:3000";

for (let i = 0; i < count; i++) {
  const ws = new WebSocket(url);
  let id = null;
  let pos = [0, 0, 0];
  let yaw = (i / count) * Math.PI * 2;
  ws.on("open", () => ws.send(JSON.stringify({ type: "join", mode: "quick", nick: `봇${i + 1}`, weapon: "smg" })));
  ws.on("message", (d) => {
    const m = JSON.parse(d);
    if (m.type === "welcome") { id = m.id; pos = [...m.pos]; console.log(`봇${i + 1} 입장 (${m.team})`); }
    if (m.type === "respawn" && m.id === id) pos = [...m.pos];
    if (m.type === "kill") console.log(`킬: ${m.killerNick} → ${m.victimNick} (${m.scores.red}:${m.scores.blue})`);
  });
  ws.on("error", (e) => console.error(`봇${i + 1} 오류:`, e.message));
  setInterval(() => {
    if (!id || ws.readyState !== 1) return;
    if (Math.random() < 0.05) yaw = Math.random() * Math.PI * 2;
    pos[0] = Math.max(-38, Math.min(38, pos[0] - Math.sin(yaw) * 0.3));
    pos[2] = Math.max(-38, Math.min(38, pos[2] - Math.cos(yaw) * 0.3));
    ws.send(JSON.stringify({ type: "input", pos, yaw, pitch: 0, anim: "run" }));
    if (Math.random() < 0.1) {
      ws.send(JSON.stringify({
        type: "shoot",
        origin: [pos[0], pos[1] + 1.6, pos[2]],
        dir: [-Math.sin(yaw), 0, -Math.cos(yaw)],
      }));
    }
  }, 50);
}
