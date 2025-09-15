// Finviz Technology sector -> screenshot + tabla + envío a Telegram
// Maneja Cloudflare con puppeteer-extra + stealth y reintentos.

const fs = require("fs");
const path = require("path");
const dayjs = require("dayjs");

const BOT_TOKEN = process.env.BOT_TOKEN;
const CHAT_ID = process.env.CHAT_ID;

if (!BOT_TOKEN || !CHAT_ID) {
  console.error("Faltan BOT_TOKEN o CHAT_ID");
  process.exit(1);
}

const puppeteer = require("puppeteer-extra");
const StealthPlugin = require("puppeteer-extra-plugin-stealth");
puppeteer.use(StealthPlugin());

async function sendPhoto(filepath, caption = "") {
  const fetch = (await import("node-fetch")).default;
  const FormData = (await import("form-data")).default;
  const url = `https://api.telegram.org/bot${BOT_TOKEN}/sendPhoto`;
  const fd = new FormData();
  fd.append("chat_id", CHAT_ID);
  if (caption) fd.append("caption", caption);
  fd.append("parse_mode", "HTML");
  fd.append("photo", fs.createReadStream(filepath));
  const res = await fetch(url, { method: "POST", body: fd });
  if (!res.ok) console.error("sendPhoto error:", await res.text());
}

async function sendMessage(text) {
  const fetch = (await import("node-fetch")).default;
  const url = `https://api.telegram.org/bot${BOT_TOKEN}/sendMessage`;
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      chat_id: CHAT_ID,
      text,
      parse_mode: "HTML",
      disable_web_page_preview: true,
    }),
  });
  if (!res.ok) console.error("sendMessage error:", await res.text());
}

(async () => {
  const browser = await puppeteer.launch({
    headless: "new",
    args: [
      "--no-sandbox",
      "--disable-setuid-sandbox",
      "--window-size=1920,1080",
      "--lang=en-US,en",
    ],
    defaultViewport: { width: 1920, height: 1080 },
  });
  const page = await browser.newPage();

  // UA y cabeceras "humanas"
  await page.setUserAgent(
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
  );
  await page.setExtraHTTPHeaders({
    "accept-language": "en-US,en;q=0.9,es;q=0.8",
  });

  const FINVIZ_URL = "https://finviz.com/map.ashx";

  // Función: cargar la página hasta que aparezca el mapa (salvando Cloudflare)
  async function loadWithRetry(max = 6) {
    for (let i = 1; i <= max; i++) {
      try {
        await page.goto(FINVIZ_URL, { waitUntil: "domcontentloaded", timeout: 60000 });

        // Si es pantalla de Cloudflare, espera y reintenta
        const isCF = await page.evaluate(() =>
          /review the security of your connection|verifying you are human|checking your browser/i.test(
            document.body.innerText || ""
          )
        );

        if (isCF) {
          await page.waitForTimeout(8000);
          continue;
        }

        // Espera a que cargue el contenedor del mapa
        await page.waitForSelector("#map", { timeout: 60000 });
        return true;
      } catch (e) {
        await page.waitForTimeout(5000);
      }
    }
    return false;
  }

  const ok = await loadWithRetry();
  const outDir = path.join(process.cwd(), "out");
  if (!fs.existsSync(outDir)) fs.mkdirSync(outDir);

  const ts = dayjs().format("YYYYMMDD");
  const mapPng = path.join(outDir, `tech_map_${ts}.png`);
  const tablePng = path.join(outDir, `tech_table_${ts}.png`);

  if (!ok) {
    // No se pudo pasar Cloudflare: avisa y sal.
    await page.screenshot({ path: mapPng, fullPage: true });
    await sendPhoto(mapPng, "⚠️ No se pudo cargar el mapa de Finviz (Cloudflare).");
    await sendMessage("No se pudo cargar Finviz tras varios intentos. Se envía captura de diagnóstico.");
    await browser.close();
    process.exit(0);
  }

  // Encontrar caja del sector "TECHNOLOGY"
  const techBox = await page.evaluate(() => {
    function getRect(el) {
      const r = el.getBoundingClientRect();
      return { x: Math.max(0, r.x), y: Math.max(0, r.y), width: r.width, height: r.height };
    }
    const map = document.querySelector("#map") || document.body;
    const blocks = [...map.querySelectorAll("div")].filter(
      d => d.children.length && (d.innerText || "").trim().length > 0
    );
    for (const el of blocks) {
      const t = (el.innerText || "").replace(/\s+/g, " ").trim();
      if (/\bTECHNOLOGY\b/i.test(t)) {
        const r = getRect(el);
        if (r.width > 300 && r.height > 200) return r;
      }
    }
    return null;
  });

  if (techBox) {
    await page.screenshot({ path: mapPng, clip: techBox });
  } else {
    await page.screenshot({ path: mapPng, fullPage: true });
  }

  // Extraer top 15 por área (símbolo + %)
  const topRows = await page.evaluate((box) => {
    function inside(rect, el) {
      const r = el.getBoundingClientRect();
      return (
        r.x >= rect.x &&
        r.y >= rect.y &&
        r.x + r.width <= rect.x + rect.width + 1 &&
        r.y + r.height <= rect.y + rect.height + 1
      );
    }
    const container = document.querySelector("#map") || document.body;
    const cells = [...container.querySelectorAll("div")];
    const rows = [];
    for (const el of cells) {
      if (!el.innerText) continue;
      if (box && !inside(box, el)) continue;
      const text = el.innerText.replace(/\s+/g, " ").trim();
      const mSym = text.match(/\b[A-Z][A-Z.\-]{1,5}\b/);
      const mPct = text.match(/[-+]\d+(?:\.\d+)?%/);
      if (mSym && mPct) {
        const r = el.getBoundingClientRect();
        rows.push({ sym: mSym[0], pct: mPct[0], area: r.width * r.height });
      }
    }
    const bySym = new Map();
    for (const r of rows) {
      const prev = bySym.get(r.sym);
      if (!prev || r.area > prev.area) bySym.set(r.sym, r);
    }
    return [...bySym.values()].sort((a, b) => b.area - a.area).slice(0, 15)
      .map(r => ({ sym: r.sym, pct: r.pct }));
  }, techBox);

  // Intento de capturar "tooltip" de la mayor
  let tooltipDone = false;
  try {
    const targetSym = topRows?.[0]?.sym || "MSFT";
    const handle = await page.evaluateHandle((box, sym) => {
      const all = [...document.querySelectorAll("#map div")];
      function inside(rect, el) {
        const r = el.getBoundingClientRect();
        return r.x >= rect.x && r.y >= rect.y &&
               r.x + r.width <= rect.x + rect.width + 1 &&
               r.y + r.height <= rect.y + rect.height + 1;
      }
      for (const el of all) {
        if (!box || inside(box, el)) {
          const words = (el.innerText || "").split(/\s+/);
          if (words.includes(sym)) return el;
        }
      }
      return null;
    }, techBox, topRows?.[0]?.sym || "MSFT");

    const el = handle.asElement();
    if (el) {
      const bb = await el.boundingBox();
      if (bb) {
        await page.mouse.move(bb.x + bb.width / 2, bb.y + 12);
        await page.waitForTimeout(900);
        const clip = {
          x: Math.max(0, bb.x - 220),
          y: Math.max(0, bb.y - 60),
          width: 460,
          height: 320,
        };
        await page.screenshot({ path: tablePng, clip });
        tooltipDone = true;
      }
    }
  } catch (_) {}

  // Mensaje
  const fecha = dayjs().format("DD/MM/YYYY");
  let text = `📊 <b>Tecnología – Cierre ${fecha}</b>\n`;
  if (topRows && topRows.length) {
    const lines = topRows.map(r => `${r.sym.padEnd(5, " ")} ${r.pct}`).join("\n");
    text += `\n<b>Principales movimientos (por tamaño en el mapa):</b>\n<pre>${lines}</pre>`;
  } else {
    text += `\n(No se pudo extraer la tabla de movimientos; adjuntamos capturas.)`;
  }
  text += `\n\nDesde InvestX: mapa y tabla automáticos (Finviz).`;

  // Envíos
  await sendPhoto(mapPng, "🗺️ Mapa sector Tecnología (Finviz)");
  if (tooltipDone) await sendPhoto(tablePng, "📋 Detalle (tooltip)");
  await sendMessage(text);

  await browser.close();
})().catch(async (e) => {
  console.error(e);
  try {
    await sendMessage("⚠️ Error inesperado en la captura de Tecnología. Revisa el workflow.");
  } catch (_) {}
  process.exit(1);
});
