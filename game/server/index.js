import express from "express";
import http from "node:http";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const DIST = path.join(__dirname, "..", "client", "dist");

const app = express();
app.get("/healthz", (_req, res) => res.json({ ok: true }));
app.use(express.static(DIST));

const server = http.createServer(app);
const PORT = process.env.PORT || 3000;
server.listen(PORT, () => console.log(`[block-rivals] listening on :${PORT}`));
