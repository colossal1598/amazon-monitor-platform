const fs = require("fs");
const path = require("path");
const express = require("express");
const qrcode = require("qrcode-terminal");
const { Client, LocalAuth, MessageMedia } = require("whatsapp-web.js");

const DEFAULT_IMAGE_CACHE_ROOT = path.join(
  __dirname,
  "..",
  "Amazon Scraper",
  "amazon_monitor",
  "data",
  "product_images"
);

function getImageCacheRoot() {
  const root = process.env.IMAGE_CACHE_ROOT || DEFAULT_IMAGE_CACHE_ROOT;
  return path.resolve(root);
}

function isPathUnderCacheRoot(filePath, cacheRoot) {
  const resolved = path.resolve(filePath);
  const root = path.resolve(cacheRoot);
  const rel = path.relative(root, resolved);
  return rel === "" || (!rel.startsWith("..") && !path.isAbsolute(rel));
}

function resolveValidImagePath(imagePath) {
  if (typeof imagePath !== "string" || !imagePath.trim()) {
    return null;
  }
  const cacheRoot = getImageCacheRoot();
  const resolved = path.resolve(imagePath.trim());
  if (!isPathUnderCacheRoot(resolved, cacheRoot)) {
    return null;
  }
  try {
    if (!fs.existsSync(resolved) || !fs.statSync(resolved).isFile()) {
      return null;
    }
  } catch {
    return null;
  }
  return resolved;
}

const app = express();
app.use(express.json());

const PORT = process.env.PORT || 3001;
const API_KEY = process.env.WA_API_KEY || "eTjW1zf2cDDZ";

let isReady = false;

const client = new Client({
  authStrategy: new LocalAuth({ clientId: "amazon-bot" }),
  puppeteer: {
    headless: true,
    args: ["--no-sandbox", "--disable-setuid-sandbox"],
  },
});

client.on("qr", (qr) => {
  console.log("\nScan this QR with WhatsApp:");
  qrcode.generate(qr, { small: true });
});

client.on("ready", () => {
  isReady = true;
  console.log("WhatsApp client is ready.");
});

client.on("auth_failure", (msg) => {
  isReady = false;
  console.error("Auth failure:", msg);
});

client.on("disconnected", (reason) => {
  isReady = false;
  console.warn("WhatsApp disconnected:", reason);
});

client.initialize();
client.on("message", (msg) => {
  console.log("CHAT_ID:", msg.from);
});

app.get("/health", (req, res) => {
  res.json({ ok: true, ready: isReady });
});

app.post("/send", async (req, res) => {
  try {
    const apiKey = req.header("x-api-key");
    if (apiKey !== API_KEY) {
      return res.status(401).json({ ok: false, error: "unauthorized" });
    }

    if (!isReady) {
      return res.status(503).json({ ok: false, error: "whatsapp_not_ready" });
    }

    const { to, message, image_url, image_path } = req.body;
    if (!to || !message) {
      return res.status(400).json({ ok: false, error: "to_and_message_required" });
    }

    // Expecting full WhatsApp JID, e.g. 9725XXXXXXXX@c.us
    let result;
    const validImagePath = resolveValidImagePath(image_path);
    const hasValidImageUrl =
      typeof image_url === "string" &&
      /^https?:\/\//i.test(image_url.trim());

    if (validImagePath) {
      const media = MessageMedia.fromFilePath(validImagePath);
      result = await client.sendMessage(to, media, { caption: message || "" });
    } else if (hasValidImageUrl) {
      const media = await MessageMedia.fromUrl(image_url.trim(), { unsafeMime: true });
      result = await client.sendMessage(to, media, { caption: message || "" });
    } else {
      result = await client.sendMessage(to, message || "");
    }

    return res.json({ ok: true, id: result.id._serialized });
  } catch (err) {
    console.error("Send error:", err);
    return res.status(500).json({ ok: false, error: "send_failed" });
  }
});

app.listen(PORT, () => {
  console.log(`WA server listening on http://localhost:${PORT}`);
});