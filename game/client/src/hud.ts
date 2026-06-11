import type { NetPlayer } from "./net";

const $ = (id: string) => document.getElementById(id)!;
const esc = (s: string) =>
  s.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]!));

export class Hud {
  setAmmo(cur: number, mag: number) { $("ammo").textContent = `${cur} / ${mag}`; }
  setReloading() { $("ammo").textContent = "재장전..."; }

  killfeed(text: string) {
    const div = document.createElement("div");
    div.textContent = text;
    $("killfeed").prepend(div);
    setTimeout(() => div.remove(), 5000);
    while ($("killfeed").children.length > 5) $("killfeed").lastChild!.remove();
  }

  flash() {
    const f = $("flash") as HTMLElement;
    f.style.opacity = "1";
    setTimeout(() => (f.style.opacity = "0"), 120);
  }

  banner(text: string, ms = 0) {
    const b = $("banner");
    b.textContent = text;
    b.classList.remove("hidden");
    if (ms) setTimeout(() => b.classList.add("hidden"), ms);
  }

  showDeath(weapons: Record<string, { name: string }>, selected: string, onPick: (k: string) => void) {
    const wrap = $("deathWeapons");
    wrap.innerHTML = "";
    for (const [key, w] of Object.entries(weapons)) {
      const btn = document.createElement("button");
      btn.textContent = w.name;
      if (key === selected) btn.classList.add("sel");
      btn.onclick = () => {
        onPick(key);
        wrap.querySelectorAll("button").forEach((b) => b.classList.remove("sel"));
        btn.classList.add("sel");
      };
      wrap.appendChild(btn);
    }
    $("deathOverlay").classList.remove("hidden");
  }
  setRespawnTimer(sec: number) { $("respawnTimer").textContent = String(sec); }
  hideDeath() { $("deathOverlay").classList.add("hidden"); }

  scoreboard(players: NetPlayer[], myId: string, show: boolean) {
    const sb = $("scoreboard");
    sb.classList.toggle("hidden", !show);
    if (!show) return;
    const rows = [...players].sort((a, b) => b.kills - a.kills).map((p) =>
      `<tr style="color:${p.team === "red" ? "#ff8a8a" : "#8ab0ff"};${p.id === myId ? "font-weight:bold" : ""}">` +
      `<td>${esc(p.nick)}${p.id === myId ? " (나)" : ""}</td><td>${p.team === "red" ? "빨강" : "파랑"}</td>` +
      `<td>${p.kills}</td><td>${p.deaths}</td></tr>`).join("");
    sb.innerHTML = `<table><tr><th>플레이어</th><th>팀</th><th>킬</th><th>데스</th></tr>${rows}</table>`;
  }
}
