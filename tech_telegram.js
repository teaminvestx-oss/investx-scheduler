// scripts/tech_telegram.js
// Envia al canal InvestX: captura mapa Tecnología + tabla (texto y, si es posible, captura de la tooltip)
// Requisitos: puppeteer, dayjs

const puppeteer = require("puppeteer");
const fs = require("fs");
const path = require("path");
const dayjs = require("dayjs");

const BOT_TOKEN = process.env.BOT_TOKEN;
const CHAT_ID = process.env.CHAT_ID;

if (!BOT_TOKEN || !CHAT_ID) {
  console.error("Faltan BOT_TOKEN o CHAT_ID en variables de entorno.");
  process.exit(1);
}

const FINVIZ_URL = "https://finviz.com/map.ashx"; // mapa general; recortaremos Tecnología

async function sendPhoto(filepath, caption = "") {
  const fetch = (await import("node-fetch")).default;
  const url = `https://api.telegram.org/bot${BOT_TOKEN}/sendPhoto`;
  const formdata = new (require("form-data"))();
  formdata.append("chat_id", CHAT_ID);
  if (caption) formdata.append("caption", caption);
  formdata.append("parse_mode", "HTML");
  formdata.append("photo", fs.createReadStream(filepath));
  const res = await fetch(url, { method: "POST", body: formdata });
  if (!res.ok) {
    const txt = await res.text();
    console.error("Error Telegram sendPhoto:", txt);
  }
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
  if (!res.ok) {
    const txt = await res.text();
    console.error("Error Telegram sendMessage:", txt);
  }
}

(async () => {
  const browser = await puppeteer.launch({
    headless: "new",
    args: ["--no-sandbox", "--disable-setuid-sandbox", "--window-size=1920,1080"],
    defaultViewport: { width: 1920, height: 1080 },
  });
  const page = await browser.newPage();

  // 1) Ir a Finviz y esperar a que cargue el mapa
  await page.goto(FINVIZ_URL, { waitUntil: "domcontentloaded", timeout: 60000 });
  await page.waitForSelector("#map", { timeout: 60000 }).catch(() => {});

  // Cerrar posibles barras/cookies si aparecen
  try {
    await page.evaluate(() => {
      const btn = [...document.querySelectorAll("a, button")].find(
        el => /accept|agree|consent|got it/i.test(el.textContent || "")
      );
      btn && btn.click();
    });
  } catch (e) {}

  // 2) Localizar el contenedor del sector "TECHNOLOGY" y capturarlo
  // El mapa se compone de divs; buscamos el área cuyo texto visible contenga "TECHNOLOGY"
  // y cogemos su bounding box.
  const techBox = await page.evaluate(() => {
    function getRect(el) {
      const r = el.getBoundingClientRect();
      return { x: r.x, y: r.y, width: r.width, height: r.height };
    }
    // Encontrar bloques de primer nivel (sectores)
    const map = document.querySelector("#map") || document.body;
    const blocks = [...map.querySelectorAll("div")].filter(
      d => d.children.length && (d.innerText || "").trim().length > 0
    );

    // Elegimos el bloque cuyo texto incluye exactamente "TECHNOLOGY" en mayúsculas
    let best = null;
    for (const el of blocks) {
      const t = (el.innerText || "").replace(/\s+/g, " ").trim();
      if (/^TECHNOLOGY\b/i.test(t) || /\bTECHNOLOGY\b/i.test(t)) {
        const r = getRect(el);
        // ignorar bloques demasiado pequeños
        if (r.width > 300 && r.height > 200) {
          best = r;
          break;
        }
      }
    }
    return best;
  });

  // Path para imágenes
  const outDir = path.join(process.cwd(), "out");
  if (!fs.existsSync(outDir)) fs.mkdirSync(outDir);

  const ts = dayjs().format("YYYYMMDD");
  const mapPng = path.join(outDir, `tech_map_${ts}.png`);
  const tablePng = path.join(outDir, `tech_table_${ts}.png`);

  if (techBox) {
    await page.screenshot({ path: mapPng, clip: techBox });
  } else {
    // Fallback: captura completa si no se pudo aislar Tecnología
    await page.screenshot({ path: mapPng, fullPage: true });
  }

  // 3) Extraer tickers y % dentro del área Tecnología (para una tabla de texto)
  // Buscamos rectángulos hijos que muestren el símbolo (texto corto sin espacios) y porcentaje
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
    const cells = [...container.querySelectorAll("div")].filter(el => {
      const t = (el.innerText || "").trim();
      return t && /\b[A-Z.\-]{2,6}\b/.test(t); // símbolos
    });

    // Extraer símbolo y % de cambio (normalmente está en el mismo nodo o descendientes)
    const rows = [];
    for (const el of cells) {
      if (box && !inside(box, el)) continue;
      const text = (el.innerText || "").replace(/\s+/g, " ").trim();
      const mSym = text.match(/\b[A-Z][A-Z.\-]{1,5}\b/); // primer token tipo símbolo
      const mPct = text.match(/[-+]\d+(?:\.\d+)?%/);
      if (mSym && mPct) {
        // tamaño como proxy de importancia
        const r = el.getBoundingClientRect();
        const area = r.width * r.height;
        rows.push({ sym: mSym[0], pct: mPct[0], area });
      }
    }

    // agrupar por símbolo (puede repetirse)
    const bySym = new Map();
    for (const r of rows) {
      const prev = bySym.get(r.sym);
      if (!prev || r.area > prev.area) bySym.set(r.sym, r);
    }
    // ordenar por área (proxy market cap dentro del plano)
    const ordered = [...bySym.values()].sort((a, b) => b.area - a.area);
    // quedarnos con ~15 más grandes del sector
    return ordered.slice(0, 15).map(r => ({ sym: r.sym, pct: r.pct }));
  }, techBox);

  // 4) Intentar sacar CAPTURA de la "tooltip" de MSFT (o del mayor)
  let tooltipDone = false;
  try {
    const targetSym = topRows?.[0]?.sym || "MSFT";
    // localizar el nodo que contiene ese símbolo dentro del box
    const selectorHandle = await page.evaluateHandle((box, sym) => {
      const all = [...document.querySelectorAll("#map div")];
      function inside(rect, el) {
        const r = el.getBoundingClientRect();
        return r.x >= rect.x && r.y >= rect.y &&
               r.x + r.width <= rect.x + rect.width + 1 &&
               r.y + r.height <= rect.y + rect.height + 1;
      }
      for (const el of all) {
        if (!box || inside(box, el)) {
          if ((el.innerText || "").split(/\s+/).includes(sym)) return el;
        }
      }
      return null;
    }, techBox, topRows?.[0]?.sym || "MSFT");

    const el = selectorHandle.asElement();
    if (el) {
      const boxEl = await el.boundingBox();
      if (boxEl) {
        await page.mouse.move(boxEl.x + boxEl.width / 2, boxEl.y + 10);
        await page.waitForTimeout(800);
        // el tooltip suele ser un div flotante; tomamos un área alrededor del puntero
        const clip = {
          x: Math.max(0, boxEl.x - 220),
          y: Math.max(0, boxEl.y - 60),
          width: 460,
          height: 320,
        };
        await page.screenshot({ path: tablePng, clip });
        tooltipDone = true;
      }
    }
  } catch (e) {
    // si falla, seguimos sin tooltip
  }

  // 5) Preparar mensaje
  const fecha = dayjs().format("DD/MM/YYYY");
  let text = `📊 <b>Tecnología – Cierre ${fecha}</b>\n`;
  if (topRows && topRows.length) {
    const lines = topRows
      .map(r => `${r.sym.padEnd(5, " ")} ${r.pct}`)
      .join("\n");
    text += `\n<b>Principales movimientos (por tamaño en el mapa):</b>\n<pre>${lines}</pre>`;
  } else {
    text += `\n(No se pudo extraer la tabla de movimientos; adjuntamos solo las capturas.)`;
  }
  text += `\n\nDesde InvestX: mapa y tabla automáticos (Finviz).`;

  // 6) Enviar al canal
  await sendPhoto(mapPng, "🗺️ Mapa sector Tecnología (Finviz)");
  if (tooltipDone) {
    await sendPhoto(tablePng, "📋 Detalle (tooltip) – mayores componentes");
  }
  await sendMessage(text);

  await browser.close();
})().catch(err => {
  console.error(err);
  process.exit(1);
});
