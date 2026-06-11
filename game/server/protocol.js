const isNum = (v) => typeof v === "number" && Number.isFinite(v);
const isVec3 = (v) => Array.isArray(v) && v.length === 3 && v.every(isNum);

export function sanitizeNick(nick) {
  if (typeof nick !== "string") return null;
  const t = nick.trim().slice(0, 12);
  return t.length ? t : null;
}

export function validate(msg) {
  if (!msg || typeof msg !== "object") return false;
  switch (msg.type) {
    case "join":
      return ["quick", "create", "join"].includes(msg.mode)
        && sanitizeNick(msg.nick) !== null
        && (msg.mode !== "join" || typeof msg.code === "string")
        && (msg.weapon === undefined || typeof msg.weapon === "string");
    case "input":
      return isVec3(msg.pos) && isNum(msg.yaw) && isNum(msg.pitch) && typeof msg.anim === "string";
    case "shoot":
      return isVec3(msg.origin) && isVec3(msg.dir);
    case "weapon":
      return typeof msg.weapon === "string";
    default:
      return false;
  }
}
