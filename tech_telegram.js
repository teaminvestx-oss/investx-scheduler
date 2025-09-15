// tech_telegram.js
// Post de INVESTX: mapa SOLO del sector Tecnología + tabla (captura + texto) desde Finviz.
// Soporta Cloudflare (puppeteer-extra + stealth) y hace fallback si el click de sector no carga.

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

// --- Helpers Telegram ---
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

// --- Main ---
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
  await page.setUserAgent(
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
  );
  await page.setExtraHTTPHeaders({ "accept-language": "en-US,en;q=0.9,es;q=0.8" });

  const outDir = path.join(process.cwd(), "out");
  if (!fs.existsSync(outDir)) fs.mkdirSync(outDir);
  const ts = dayjs().format("YYYYMMDD");
  const mapPng = path.join(outDir, `tech_map_${ts}.png`);
  const tablePng = path.join(outDir, `tech_table_${ts}.png`);

  // --- 1) Abrir mapa Finviz con reintentos (Cloudflare) ---
  const FINVIZ_MAP = "https://finviz.com/map.ashx";
  async function loadMapWithRetry(tries = 6) {
    for (let i = 1; i <= tries; i++) {
      try {
        await page.goto(FINVIZ_MAP, { waitUntil: "domcontentloaded", timeout: 60000 });
        const isCF = await page.evaluate(() =>
          /review the security of your connection|verifying you are human|checking your browser/i.test(
            document.body.innerText || ""
          )
        );
        if (isCF) { await page.waitForTimeout(8000); continue; }
        await page.waitForSelector("#map", { timeout: 60000 });
        return true;
      } catch (e) {
        await page.waitForTimeout(5000);
      }
    }
    return false;
  }
  const mapOk = await loadMapWithRetry();
  if (!mapOk) {
    await page.screenshot({ path: mapPng, fullPage: true });
    await sendPhoto(mapPng, "⚠️ No se pudo cargar Finviz (Cloudflare).");
    await sendMessage("No se pudo cargar Finviz tras varios intentos. Enviada captura de diagnóstico.");
    await browser.close();
    process.exit(0);
  }

  // --- 2) Localizar bloque del SECTOR 'TECHNOLOGY' y hacer click para vista sector ---
  const techRect = await page.evaluate(() => {
    function rect(el){ const r = el.getBoundingClientRect(); return {x:r.x,y:r.y,w:r.width,h:r.height}; }
    const map = document.querySelector("#map") || document.body;
    const blocks = [...map.querySelectorAll("div")].filter(d => (d.innerText||"").trim());
    for (const el of blocks) {
      const t = (el.innerText||"").replace(/\s+/g," ").toUpperCase();
      if (t.includes("TECHNOLOGY")) {
        const r = rect(el);
        if (r.w > 300 && r.h > 200) return r;
      }
    }
    return null;
  });

  if (techRect) {
    await page.mouse.click(techRect.x + techRect.w/2, techRect.y + 20, { clickCount: 1 });
    await page.waitForTimeout(1500); // tiempo para que Finviz cargue vista sector
  }

  // ¿Estamos en vista sector?
  const sectorView = await page.evaluate(() => /Technology/i.test(document.body.innerText || ""));
  if (sectorView) {
    const mapBox = await page.$("#map");
    if (mapBox) {
      const bb = await mapBox.boundingBox();
      await page.screenshot({ path: mapPng, clip: bb });
    } else {
      await page.screenshot({ path: mapPng, fullPage: true });
    }
  } else if (techRect) {
    // fallback: recortar el rectángulo de Tecnología del mapa general
    await page.screenshot({ path: mapPng, clip: { x: techRect.x, y: techRect.y, width: techRect.w, height: techRect.h } });
  } else {
    await page.screenshot({ path: mapPng, fullPage: true });
  }

  // --- 3) Tabla robusta: Screener filtrado por sector Tecnología ---
  const FINVIZ_SCREENER_TECH = "https://finviz.com/screener.ashx?v=111&f=sec_technology";
  await page.goto(FINVIZ_SCREENER_TECH, { waitUntil: "domcontentloaded", timeout: 60000 });
  await page.waitForSelector("#screener-content", { timeout: 60000 }).catch(()=>{});

  // Extraer primeras ~15 filas con símbolo / cambio % / market cap / nombre
  const rows = await page.evaluate(() => {
    const out = [];
    const table = document.querySelector("#screener-content table.table-light");
    if (!table) return out;
    const trs = table.querySelectorAll("tr[valign='top']");
    for (const tr of trs) {
      const tds = tr.querySelectorAll("td");
      if (tds.length < 3) continue;
      const sym = tds[1]?.innerText?.trim();    // símbolo
      const name = tds[2]?.innerText?.trim();   // nombre
      const cellsTxt = [...tds].map(td => td.innerText.trim());

      // cambio % y market cap (patrones típicos de Finviz)
      const chg = cellsTxt.find(x => /[-+]\d+(?:\.\d+)?%$/.test(x)) || "";
      const mcap = cellsTxt.find(x => /^[\d.]+[MBT]$/.test(x)) || "";

      if (sym) out.push({ sym, name, chg, mcap });
    }
    return out.slice(0, 15);
  });

  // Captura visual de la tabla del screener
  try {
    const tbl = await page.$("#screener-content");
    if (tbl) {
      const bb = await tbl.boundingBox();
      await page.screenshot({ path: tablePng, clip: bb });
    }
  } catch (_) {}

  // Texto formateado para Telegram
  let tablaTexto = "";
  if (rows && rows.length) {
    const header = "SYMB   CHANGE   M.CAP   NAME";
    const lines = rows.map(r =>
      `${String(r.sym||"").padEnd(5)}  ${String(r.chg||"").padStart(7)}  ${String(r.mcap||"").padStart(6)}  ${r.name||""}`
    );
    tablaTexto = `<b>Top Tecnología – % y M.Cap</b>\n<pre>${header}\n${lines.join("\n")}</pre>`;
  } else {
    tablaTexto = "(No se pudo extraer la tabla del screener; adjuntamos capturas.)";
  }

  // --- 4) Enviar al canal ---
  const fecha = dayjs().format("DD/MM/YYYY");
  await sendPhoto(mapPng, "🗺️ Tecnología – mapa (Finviz)");
  if (fs.existsSync(tablePng)) await sendPhoto(tablePng, "📋 Tabla (Screener Finviz)");
  await sendMessage(`📊 <b>Tecnología – Cierre ${fecha}</b>\n\n${tablaTexto}\n\nDesde InvestX (automático).`);

  await browser.close();
})().catch(async (e) => {
  console.error(e);
  try { await sendMessage("⚠️ Error inesperado en la captura de Tecnología. Revisa el workflow."); } catch {}
  process.exit(1);
});

