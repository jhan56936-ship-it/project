export interface NetPlayer {
  id: string; nick: string; team: "red" | "blue";
  pos: [number, number, number]; yaw: number; pitch: number; anim: string;
  hp: number; alive: boolean; kills: number; deaths: number; weapon: string;
}

export class Net {
  private ws!: WebSocket;
  private handlers = new Map<string, (m: any) => void>();
  onClose: () => void = () => {};

  connect(): Promise<void> {
    // vite dev(5173)에서는 게임 서버(3000)로, 배포 환경에서는 같은 호스트로 접속
    const url = location.port === "5173"
      ? `ws://${location.hostname}:3000`
      : `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}`;
    this.ws = new WebSocket(url);
    this.ws.onmessage = (ev) => {
      let m: any;
      try { m = JSON.parse(ev.data); } catch { return; }
      this.handlers.get(m.type)?.(m);
    };
    this.ws.onclose = () => this.onClose();
    return new Promise((resolve, reject) => {
      this.ws.onopen = () => resolve();
      this.ws.onerror = () => reject(new Error("connect failed"));
    });
  }

  on(type: string, fn: (m: any) => void) { this.handlers.set(type, fn); }
  send(msg: object) { if (this.ws?.readyState === 1) this.ws.send(JSON.stringify(msg)); }
}
