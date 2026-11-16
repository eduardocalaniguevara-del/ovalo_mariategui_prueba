const { McpServer } = require('@modelcontextprotocol/sdk/server/mcp.js');
const { StdioServerTransport } = require('@modelcontextprotocol/sdk/server/stdio.js');
const { execFileSync, spawn } = require('node:child_process');
const path = require('node:path');
const { z } = require('zod');
const { openDb, DB_HOSTAL_PATH } = require('./db');

const db = openDb();
const OCR_SCRIPT = path.join(__dirname, 'ocr_extractor.py');

// Auto-arranque del daemon y visor ligados al servidor MCP
const childProcs = [];
function startCompanion(scriptName) {
  try {
    const p = spawn(process.execPath, [path.join(__dirname, scriptName)], {
      cwd: __dirname,
      stdio: 'ignore',
      windowsHide: true
    });
    childProcs.push(p);
  } catch (_) {}
}
startCompanion('wsp_daemon.js');
startCompanion('visor_web.js');

function cleanupChildren() {
  for (const p of childProcs) {
    try { p.kill(); } catch (_) {}
  }
}
process.on('exit', cleanupChildren);
process.stdin.on('close', () => {
  cleanupChildren();
  process.exit(0);
});

const server = new McpServer({
  name: 'whatsapp-recepcion-ovalo',
  version: '1.0.0'
});

server.tool(
  'wsp_recepcion_estado',
  'Muestra el estado de conexión del WhatsApp de Recepción y cuántos chats, mensajes y documentos OCR hay en ovalo_sistema.db.',
  {},
  async () => {
    const estados = db.prepare(`SELECT * FROM wsp_recepcion_estado`).all();
    const totalChats = db.prepare(`SELECT COUNT(*) AS n FROM wsp_recepcion_chats`).get().n;
    const totalMensajes = db.prepare(`SELECT COUNT(*) AS n FROM wsp_recepcion_mensajes`).get().n;
    const totalDocs = db.prepare(`SELECT COUNT(*) AS n FROM wsp_recepcion_documentos`).get().n;
    return {
      content: [{
        type: 'text',
        text: JSON.stringify({ db_path: DB_HOSTAL_PATH, totalChats, totalMensajes, totalDocs, estados }, null, 2)
      }]
    };
  }
);

server.tool(
  'wsp_recepcion_buscar_mensajes',
  'Busca mensajes de WhatsApp de Recepción en ovalo_sistema.db cruzando automáticamente el celular con el nombre del huésped, DNI y habitación.',
  {
    busqueda: z.string().optional().describe('Texto a buscar dentro del mensaje, nombre o número'),
    jid: z.string().optional().describe('JID o celular opcional para filtrar un chat específico'),
    limite: z.number().optional().default(30).describe('Cantidad máxima de mensajes a devolver')
  },
  async ({ busqueda = '', jid = '', limite = 30 }) => {
    let sql = `
      SELECT m.fecha_hora,
             m.jid,
             SUBSTR(REPLACE(REPLACE(m.jid, '@s.whatsapp.net', ''), '@c.us', ''), -9) AS celular,
             COALESCE(
               (SELECT hp.nombre || ' (DNI ' || hp.dni || ' | Hab ' || hp.hab_frec || ')'
                FROM huespedes_padron hp
                WHERE hp.celular != '' AND SUBSTR(REPLACE(hp.celular, ' ', ''), -9) = SUBSTR(REPLACE(m.jid, '@s.whatsapp.net', ''), -9)
                LIMIT 1),
               (SELECT e.huesped || ' (DNI ' || e.dni || ' | Hab ' || e.hab || ')'
                FROM estancias e
                WHERE e.celular != '' AND SUBSTR(REPLACE(e.celular, ' ', ''), -9) = SUBSTR(REPLACE(m.jid, '@s.whatsapp.net', ''), -9)
                ORDER BY e.ymd_in DESC LIMIT 1),
               'Sin vincular aún'
             ) AS huesped_cruzado,
             m.nombre_emisor,
             m.es_mio,
             m.tipo_mensaje,
             m.texto,
             m.ruta_archivo
      FROM wsp_recepcion_mensajes m
      WHERE 1=1
    `;
    const params = [];
    if (busqueda) {
      sql += ` AND (m.texto LIKE ? OR COALESCE(m.nombre_emisor,'') LIKE ? OR m.jid LIKE ?)`;
      const q = `%${busqueda}%`;
      params.push(q, q, q);
    }
    if (jid) {
      sql += ` AND m.jid LIKE ?`;
      params.push(`%${jid}%`);
    }
    sql += ` ORDER BY m.timestamp_unix DESC LIMIT ?`;
    params.push(limite);
    const rows = db.prepare(sql).all(...params);
    return {
      content: [{ type: 'text', text: JSON.stringify(rows, null, 2) }]
    };
  }
);

server.tool(
  'wsp_recepcion_listar_chats',
  'Lista los chats de WhatsApp de Recepción registrados en ovalo_sistema.db cruzados con los datos del huésped.',
  {
    busqueda: z.string().optional().describe('Filtro por nombre o número de chat'),
    limite: z.number().optional().default(40)
  },
  async ({ busqueda = '', limite = 40 }) => {
    const q = `%${busqueda}%`;
    const rows = db.prepare(`
      SELECT c.jid,
             c.nombre,
             c.tipo,
             c.ultimo_mensaje,
             c.fecha_ultimo_mensaje,
             c.actualizado_en,
             COALESCE(
               (SELECT hp.nombre || ' (DNI ' || hp.dni || ' | Hab ' || hp.hab_frec || ')'
                FROM huespedes_padron hp
                WHERE hp.celular != '' AND SUBSTR(REPLACE(hp.celular, ' ', ''), -9) = SUBSTR(REPLACE(c.jid, '@s.whatsapp.net', ''), -9)
                LIMIT 1),
               (SELECT e.huesped || ' (DNI ' || e.dni || ' | Hab ' || e.hab || ')'
                FROM estancias e
                WHERE e.celular != '' AND SUBSTR(REPLACE(e.celular, ' ', ''), -9) = SUBSTR(REPLACE(c.jid, '@s.whatsapp.net', ''), -9)
                ORDER BY e.ymd_in DESC LIMIT 1),
               'Sin vincular aún'
             ) AS huesped_cruzado
      FROM wsp_recepcion_chats c
      WHERE c.jid LIKE ? OR COALESCE(c.nombre, '') LIKE ?
      ORDER BY c.fecha_ultimo_mensaje DESC
      LIMIT ?
    `).all(q, q, limite);
    return {
      content: [{ type: 'text', text: JSON.stringify(rows, null, 2) }]
    };
  }
);

server.tool(
  'wsp_recepcion_enviar_mensaje',
  'Encola el envío inmediato de un mensaje de texto o comprobante PDF/imagen desde el WhatsApp de Recepción.',
  {
    destino: z.string().describe('Número de celular (ej. 999888777 o 51999888777) o JID del chat'),
    mensaje: z.string().optional().describe('Texto del mensaje o pie de foto/documento'),
    ruta_adjunto: z.string().optional().describe('Ruta absoluta opcional a un PDF o imagen a adjuntar')
  },
  async ({ destino, mensaje = '', ruta_adjunto = null }) => {
    const now = new Date().toISOString();
    const nombreAdjunto = ruta_adjunto ? path.basename(ruta_adjunto) : null;
    const res = db.prepare(`
      INSERT INTO wsp_recepcion_cola_envio (telefono_o_jid, mensaje, ruta_adjunto, nombre_adjunto, estado, creado_en)
      VALUES (?, ?, ?, ?, 'PENDIENTE', ?)
    `).run(destino, mensaje, ruta_adjunto, nombreAdjunto, now);
    return {
      content: [{
        type: 'text',
        text: JSON.stringify({ ok: true, id_envio: res.lastInsertRowid, destino, estado: 'PENDIENTE (se enviará en <3 seg)' })
      }]
    };
  }
);

server.tool(
  'wsp_recepcion_buscar_documentos',
  'Busca vouchers Yape/Plin y comprobantes SUNAT recibidos en el WhatsApp de Recepción y extraídos por OCR.',
  {
    busqueda: z.string().optional().describe('Texto a buscar dentro del documento, RUC, serie o número de operación'),
    categoria: z.string().optional().describe('VOUCHER_BANCARIO, COMPROBANTE_SUNAT, RECIBO_SERVICIO, CONTRATO_LEGAL u OTRO_DOCUMENTO'),
    limite: z.number().optional().default(25)
  },
  async ({ busqueda = '', categoria = '', limite = 25 }) => {
    let sql = `
      SELECT id, fecha_hora, jid, tipo_archivo, categoria, serie_numero, ruc_emisor,
             monto_detectado, operacion_nro, ruta_local, SUBSTR(texto_extraido, 1, 500) AS extracto
      FROM wsp_recepcion_documentos
      WHERE categoria != 'DESCARTADO_SIN_TEXTO'
    `;
    const params = [];
    if (categoria) {
      sql += ` AND categoria = ?`;
      params.push(categoria);
    }
    if (busqueda) {
      sql += ` AND (COALESCE(texto_extraido,'') LIKE ? OR COALESCE(serie_numero,'') LIKE ? OR COALESCE(ruc_emisor,'') LIKE ? OR COALESCE(operacion_nro,'') LIKE ?)`;
      const q = `%${busqueda}%`;
      params.push(q, q, q, q);
    }
    sql += ` ORDER BY fecha_hora DESC LIMIT ?`;
    params.push(limite);
    const rows = db.prepare(sql).all(...params);
    return { content: [{ type: 'text', text: JSON.stringify(rows, null, 2) }] };
  }
);

server.tool(
  'wsp_recepcion_extraer_ocr',
  'Ejecuta el extractor OCR / PDF híbrido sobre cualquier archivo PDF o imagen en Recepción y lo guarda en ovalo_sistema.db.',
  {
    archivo_ruta: z.string().describe('Ruta absoluta del PDF o imagen a analizar')
  },
  async ({ archivo_ruta }) => {
    const out = execFileSync('python', [OCR_SCRIPT, '--archivo', archivo_ruta], { encoding: 'utf8' });
    return { content: [{ type: 'text', text: out }] };
  }
);

server.tool(
  'sqlite_hostal_consulta_sql',
  'Ejecuta una consulta SQL SELECT sobre ovalo_sistema.db en Laptop 2 (Recepción).',
  {
    sql: z.string().describe('Consulta SQL SELECT a ejecutar sobre ovalo_sistema.db')
  },
  async ({ sql }) => {
    const rows = db.prepare(sql).all();
    return {
      content: [{ type: 'text', text: JSON.stringify(rows, null, 2) }]
    };
  }
);

async function main() {
  const transport = new StdioServerTransport();
  await server.connect(transport);
}

main().catch(console.error);
