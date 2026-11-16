const http = require('node:http');
const url = require('node:url');
const { openDb } = require('./db');

const PORT = 8788;
const dbHostal = openDb();

const HTML = `<!DOCTYPE html>
<html lang="es">
<head>
  <meta charset="UTF-8" />
  <title>Centro SQLite & WhatsApp Recepción en Vivo — Laptop 2</title>
  <style>
    * { box-sizing: border-box; }
    body { margin: 0; font-family: system-ui, -apple-system, sans-serif; background: #0b1120; color: #f8fafc; }
    header { background: #1e293b; padding: 14px 24px; display: flex; align-items: center; justify-content: space-between; border-bottom: 1px solid #334155; flex-wrap: wrap; gap: 12px; }
    .brand { display: flex; align-items: center; gap: 14px; }
    h1 { margin: 0; font-size: 18px; color: #38bdf8; }
    .badge-live { background: #064e3b; color: #a7f3d0; border: 1px solid #10b981; padding: 6px 14px; border-radius: 999px; font-weight: 700; font-size: 12.5px; }
    .stats { display: flex; gap: 10px; font-size: 12px; flex-wrap: wrap; }
    .pill { background: #0f172a; border: 1px solid #334155; padding: 5px 11px; border-radius: 999px; }
    .pill b { color: #4ade80; }
    .toolbar { padding: 12px 24px; display: flex; gap: 8px; flex-wrap: wrap; align-items: center; background: #131c31; border-bottom: 1px solid #1e293b; }
    .tab { background: #1e293b; color: #cbd5e1; border: 1px solid #334155; padding: 7px 13px; border-radius: 8px; cursor: pointer; font-weight: 600; font-size: 13px; transition: 0.15s; }
    .tab.active { background: #0284c7; color: white; border-color: #38bdf8; }
    input { background: #0f172a; color: white; border: 1px solid #475569; padding: 8px 14px; border-radius: 8px; font-size: 13px; min-width: 260px; margin-left: auto; }
    button.btn { background: #22c55e; color: #052e16; font-weight: 700; border: none; padding: 8px 16px; border-radius: 8px; cursor: pointer; }
    main { padding: 16px 24px; overflow-x: auto; }
    table { width: 100%; border-collapse: collapse; background: #1e293b; border-radius: 10px; overflow: hidden; font-size: 12.5px; }
    th { background: #0f172a; color: #94a3b8; text-align: left; padding: 10px 12px; border-bottom: 1px solid #334155; white-space: nowrap; }
    td { padding: 8px 12px; border-bottom: 1px solid #263349; vertical-align: top; max-width: 480px; word-break: break-word; }
    tr:hover td { background: #24324a; }
    .tag { display: inline-block; padding: 2px 8px; border-radius: 6px; font-size: 11px; font-weight: 700; background: #334155; color: #e2e8f0; }
    .tag.VIGENTE, .tag.OK, .tag.FACTURA, .tag.COMPROBANTE_SUNAT, .tag.ACTIVA { background: #065f46; color: #a7f3d0; }
    .tag.BOLETA, .tag.VOUCHER_BANCARIO { background: #581c87; color: #e9d5ff; }
    .tag.CONTRATO_LEGAL { background: #1e3a8a; color: #bfdbfe; }
    .tag.PENDIENTE, .tag.ANULADO, .tag.FALTA { background: #7f1d1d; color: #fecaca; }
  </style>
</head>
<body>
  <header>
    <div class="brand">
      <h1>🏨 Centro SQLite Hostal (ovalo_sistema.db) + WhatsApp Recepción en Vivo</h1>
      <span class="badge-live" id="wspBadge">🟢 WSP Recepción Conectado</span>
    </div>
    <div class="stats" id="stats">Cargando...</div>
  </header>

  <div class="toolbar">
    <div id="tabsContainer" style="display:flex;gap:8px;flex-wrap:wrap;"></div>
    <input id="q" type="text" placeholder="Buscar huésped, DNI, habitación, celular, serie, mensaje..." onkeydown="if(event.key==='Enter') loadData()" />
    <button class="btn" onclick="loadData()">🔍 Buscar / Refrescar</button>
  </div>

  <main>
    <div id="tableWrap">Cargando datos...</div>
  </main>

  <script>
    const TABS = [
      { id: 'estancias', label: '🛏️ Estancias (WebApp)' },
      { id: 'pagos', label: '💳 Pagos (WebApp)' },
      { id: 'tienda', label: '🥤 Ventas Tienda' },
      { id: 'gastos', label: '💸 Gastos' },
      { id: 'wsp_recepcion', label: '💬 Mensajes WSP Recepción (En Vivo)' },
      { id: 'wsp_chats', label: '👥 Agenda/Chats WSP Recepción (Cruzado)' },
      { id: 'wsp_docs', label: '🧾 Vouchers & Docs OCR Recepción' },
      { id: 'sunat_ventas', label: '🧾 Ventas SUNAT (Facturas/Boletas)' },
      { id: 'sunat_lineas', label: '🌙 Noches Facturadas (Calzado)' },
      { id: 'sire_compras', label: '🛒 Compras SIRE & Bancarización' },
      { id: 'hostal_ocr', label: '📄 Docs OCR del Hostal' }
    ];

    let currentTab = 'estancias';

    function esc(s) {
      return String(s ?? '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
    }

    function renderTabs() {
      const c = document.getElementById('tabsContainer');
      c.innerHTML = TABS.map(t =>
        '<button class="tab ' + (t.id === currentTab ? 'active' : '') + '" onclick="selectTab(\\'' + t.id + '\\')">' + t.label + '</button>'
      ).join('');
    }

    function selectTab(id) {
      currentTab = id;
      renderTabs();
      loadData();
    }

    async function loadStats() {
      const r = await fetch('/api/stats?t=' + Date.now()).then(x => x.json());
      document.getElementById('wspBadge').textContent = '🟢 WSP: ' + r.estado_wsp + (r.usuario_wsp ? ' (' + r.usuario_wsp.split(':')[0] + ')' : '');
      document.getElementById('stats').innerHTML =
        '<div class="pill">Estancias: <b>' + r.estancias.toLocaleString() + '</b></div>' +
        '<div class="pill">Pagos: <b>' + r.pagos.toLocaleString() + '</b></div>' +
        '<div class="pill">Chats WSP: <b>' + r.chats.toLocaleString() + '</b></div>' +
        '<div class="pill">Mensajes WSP: <b>' + r.mensajes.toLocaleString() + '</b></div>' +
        '<div class="pill">Ventas SUNAT: <b>' + r.sunat_ventas.toLocaleString() + '</b></div>' +
        '<div class="pill">Compras SIRE: <b>' + r.sire_compras.toLocaleString() + '</b></div>';
    }

    async function loadData() {
      try {
        await loadStats();
        const q = encodeURIComponent(document.getElementById('q').value.trim());
        const rows = await fetch('/api/data?tab=' + currentTab + '&q=' + q + '&t=' + Date.now()).then(x => x.json());
        if (!rows.length) {
          document.getElementById('tableWrap').innerHTML = '<p style="color:#94a3b8;">No hay registros en esta tabla o con ese filtro.</p>';
          return;
        }
        const cols = Object.keys(rows[0]);
        let h = '<table><thead><tr>' + cols.map(c => '<th>' + esc(c) + '</th>').join('') + '</tr></thead><tbody>';
        for (const row of rows) {
          h += '<tr>' + cols.map(c => {
            const val = row[c];
            if (['Tipo','Estado','categoria','Estado_Respaldo','Direccion'].includes(c)) {
              return '<td><span class="tag ' + esc(val) + '">' + esc(val) + '</span></td>';
            }
            return '<td>' + esc(val) + '</td>';
          }).join('') + '</tr>';
        }
        h += '</tbody></table>';
        document.getElementById('tableWrap').innerHTML = h;
      } catch (_) {}
    }

    renderTabs();
    loadData();
    // Auto-refresco en vivo cada 4 segundos cuando el usuario no está escribiendo un filtro
    setInterval(() => {
      if (document.activeElement !== document.getElementById('q')) {
        loadData();
      }
    }, 4000);
  </script>
</body>
</html>`;

const server = http.createServer((req, res) => {
  const parsed = url.parse(req.url, true);
  const q = `%${(parsed.query.q || '').trim()}%`;
  const tab = parsed.query.tab || 'estancias';

  if (parsed.pathname === '/api/stats') {
    const data = {
      estancias: dbHostal.prepare(`SELECT COUNT(*) AS n FROM estancias`).get().n,
      pagos: dbHostal.prepare(`SELECT COUNT(*) AS n FROM pagos`).get().n,
      sunat_ventas: dbHostal.prepare(`SELECT COUNT(*) AS n FROM sunat_ventas`).get().n,
      sunat_lineas: dbHostal.prepare(`SELECT COUNT(*) AS n FROM sunat_lineas_cache`).get().n,
      sire_compras: dbHostal.prepare(`SELECT COUNT(*) AS n FROM sire_compras_hostal`).get().n,
      chats: dbHostal.prepare(`SELECT COUNT(*) AS n FROM wsp_recepcion_chats`).get().n,
      mensajes: dbHostal.prepare(`SELECT COUNT(*) AS n FROM wsp_recepcion_mensajes`).get().n,
      docs: dbHostal.prepare(`SELECT COUNT(*) AS n FROM wsp_recepcion_documentos WHERE COALESCE(categoria,'') != 'DESCARTADO_SIN_TEXTO'`).get().n,
      estado_wsp: dbHostal.prepare(`SELECT valor FROM wsp_recepcion_estado WHERE clave='conexion'`).get()?.valor || 'CONECTADO',
      usuario_wsp: dbHostal.prepare(`SELECT valor FROM wsp_recepcion_estado WHERE clave='usuario_actual'`).get()?.valor || ''
    };
    res.writeHead(200, { 'Content-Type': 'application/json; charset=utf-8', 'Cache-Control': 'no-store' });
    return res.end(JSON.stringify(data));
  }

  if (parsed.pathname === '/api/data') {
    let rows = [];
    try {
      if (tab === 'estancias') {
        rows = dbHostal.prepare(`
          SELECT id_timestamp_str AS ID_Registro, fecha_in_dmy AS CheckIn, fecha_out_dmy AS CheckOut,
                 hab AS Hab, tipo_hab AS Tipo_Hab, huesped AS Huesped, dni AS DNI, celular AS Celular, empresa AS Empresa,
                 noches AS Noches, tarifa AS Tarifa,
                 (COALESCE(pago_ini_efec,0)+COALESCE(pago_ini_yape,0)+COALESCE(pago_post_efec,0)+COALESCE(pago_post_yape,0)+COALESCE(pago_visa,0)) AS Total_Pagado,
                 nro_comp AS Comprobante, estado_estancia AS Estado
          FROM estancias
          WHERE huesped LIKE ? OR dni LIKE ? OR hab LIKE ? OR COALESCE(empresa,'') LIKE ? OR COALESCE(celular,'') LIKE ?
          ORDER BY sheet_row DESC, rowid DESC LIMIT 200
        `).all(q, q, q, q, q);
      } else if (tab === 'pagos') {
        rows = dbHostal.prepare(`
          SELECT pago_id AS ID_Pago, fecha_iso AS Fecha, hab AS Hab, huesped AS Huesped,
                 medio_pago AS Medio, monto AS Monto, tipo_pago AS Tipo_Pago,
                 concepto AS Concepto, nro_operacion AS Voucher_Op
          FROM pagos
          WHERE huesped LIKE ? OR hab LIKE ? OR medio_pago LIKE ? OR pago_id LIKE ?
          ORDER BY sheet_row DESC, rowid DESC LIMIT 200
        `).all(q, q, q, q);
      } else if (tab === 'tienda') {
        rows = dbHostal.prepare(`
          SELECT venta_uuid AS ID_Venta, fecha_dmy AS Fecha, producto AS Producto,
                 cantidad AS Cant, precio_unit AS P_Unit, subtotal AS Subtotal,
                 metodo_pago AS Medio, hab AS Hab, personal AS Personal
          FROM ventas_tienda
          WHERE producto LIKE ? OR metodo_pago LIKE ? OR hab LIKE ?
          ORDER BY sheet_row DESC, rowid DESC LIMIT 200
        `).all(q, q, q);
      } else if (tab === 'gastos') {
        rows = dbHostal.prepare(`
          SELECT id_ts_str AS Fecha_Hora, fecha_gasto_dmy AS Fecha, categoria AS Categoria,
                 descripcion AS Descripcion, monto AS Monto, metodo_pago AS Medio,
                 responsable AS Resp, personal AS Personal
          FROM gastos
          WHERE descripcion LIKE ? OR categoria LIKE ? OR COALESCE(personal,'') LIKE ?
          ORDER BY sheet_row DESC, rowid DESC LIMIT 200
        `).all(q, q, q);
      } else if (tab === 'wsp_recepcion') {
        rows = dbHostal.prepare(`
          SELECT * FROM (
            SELECT m.fecha_hora AS Fecha,
                   SUBSTR(REPLACE(REPLACE(COALESCE(m.chat_jid, m.jid, ''), '@s.whatsapp.net', ''), '@c.us', ''), -9) AS Celular,
                   COALESCE(
                     (SELECT hp.nombre || ' (DNI ' || hp.dni || ' | Hab ' || hp.hab_frec || ')'
                      FROM huespedes_padron hp
                      WHERE hp.celular != '' AND SUBSTR(REPLACE(hp.celular, ' ', ''), -9) = SUBSTR(REPLACE(COALESCE(m.chat_jid, m.jid, ''), '@s.whatsapp.net', ''), -9)
                      LIMIT 1),
                     (SELECT e.huesped || ' (DNI ' || e.dni || ' | Hab ' || e.hab || ')'
                      FROM estancias e
                      WHERE e.celular != '' AND SUBSTR(REPLACE(e.celular, ' ', ''), -9) = SUBSTR(REPLACE(COALESCE(m.chat_jid, m.jid, ''), '@s.whatsapp.net', ''), -9)
                      ORDER BY e.ymd_in DESC LIMIT 1),
                     'Sin vincular aún'
                   ) AS Huesped_Ovalo,
                   COALESCE(m.nombre_emisor, m.chat_nombre, '') AS Nombre_WSP,
                   CASE WHEN COALESCE(m.es_mio, m.desde_recepcion, 0) = 1 THEN 'Enviado (Recepción)' ELSE 'Recibido (Cliente)' END AS Direccion,
                   COALESCE(m.tipo_mensaje, m.tipo, 'texto') AS Tipo,
                   m.texto AS Mensaje,
                   COALESCE(m.timestamp_unix, m.timestamp, 9999999999) AS ord_ts
            FROM wsp_recepcion_mensajes m
            WHERE m.texto LIKE ? OR COALESCE(m.nombre_emisor, m.chat_nombre, '') LIKE ? OR COALESCE(m.chat_jid, m.jid, '') LIKE ?

            UNION ALL

            SELECT COALESCE(c.fecha_ultimo_mensaje, c.actualizado_en, '') AS Fecha,
                   SUBSTR(REPLACE(REPLACE(c.jid, '@s.whatsapp.net', ''), '@c.us', ''), -9) AS Celular,
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
                   ) AS Huesped_Ovalo,
                   COALESCE(c.nombre, c.jid) AS Nombre_WSP,
                   'Chat Vinculado' AS Direccion,
                   COALESCE(c.tipo, 'chat') AS Tipo,
                   COALESCE(c.ultimo_mensaje, '🟢 Chat sincronizado en vivo — esperando mensaje') AS Mensaje,
                   COALESCE(c.ultimo_mensaje_ts, 0) AS ord_ts
            FROM wsp_recepcion_chats c
            WHERE (COALESCE(c.nombre, '') LIKE ? OR c.jid LIKE ? OR COALESCE(c.ultimo_mensaje, '') LIKE ?)
              AND NOT EXISTS (SELECT 1 FROM wsp_recepcion_mensajes m2 WHERE COALESCE(m2.chat_jid, m2.jid) = c.jid)
          )
          ORDER BY ord_ts DESC, Fecha DESC LIMIT 200
        `).all(q, q, q, q, q, q).map(({ ord_ts, ...rest }) => rest);
      } else if (tab === 'wsp_chats') {
        rows = dbHostal.prepare(`
          SELECT c.nombre AS Nombre_WSP,
                 SUBSTR(REPLACE(REPLACE(c.jid, '@s.whatsapp.net', ''), '@c.us', ''), -9) AS Celular,
                 COALESCE(
                   (SELECT hp.nombre || ' (DNI ' || hp.dni || ' | Hab ' || hp.hab_frec || ')'
                    FROM huespedes_padron hp
                    WHERE hp.celular != '' AND SUBSTR(REPLACE(hp.celular, ' ', ''), -9) = SUBSTR(REPLACE(c.jid, '@s.whatsapp.net', ''), -9)
                    LIMIT 1),
                   (SELECT e.huesped || ' (DNI ' || e.dni || ' | Hab ' || e.hab || ')'
                    FROM estancias e
                    WHERE e.celular != '' AND SUBSTR(REPLACE(e.celular, ' ', ''), -9) = SUBSTR(REPLACE(c.jid, '@s.whatsapp.net', ''), -9)
                    ORDER BY e.ymd_in DESC LIMIT 1),
                   'Sin vincular en padrón'
                 ) AS Huesped_Vinculado_Ovalo,
                 COALESCE(c.ultimo_mensaje, '(Chat sincronizado)') AS Ultimo_Mensaje,
                 COALESCE(c.fecha_ultimo_mensaje, c.actualizado_en, '') AS Actualizado_En,
                 c.jid AS JID_WhatsApp
          FROM wsp_recepcion_chats c
          WHERE COALESCE(c.nombre, '') LIKE ? OR c.jid LIKE ? OR COALESCE(c.ultimo_mensaje, '') LIKE ?
          ORDER BY COALESCE(c.ultimo_mensaje_ts, 0) DESC, c.actualizado_en DESC LIMIT 200
        `).all(q, q, q);
      } else if (tab === 'wsp_docs') {
        rows = dbHostal.prepare(`
          SELECT fecha_hora AS Fecha, COALESCE(chat_nombre, jid, '') AS Chat, categoria AS Categoria,
                 serie_numero AS Serie, COALESCE(ruc_dni, ruc_emisor, '') AS RUC_DNI, monto_detectado AS Monto,
                 operacion_nro AS Operacion, SUBSTR(texto_extraido, 1, 280) AS Texto_Extraido
          FROM wsp_recepcion_documentos
          WHERE COALESCE(categoria, '') != 'DESCARTADO_SIN_TEXTO'
            AND (COALESCE(texto_extraido,'') LIKE ? OR COALESCE(serie_numero,'') LIKE ? OR COALESCE(operacion_nro,'') LIKE ?)
          ORDER BY id DESC LIMIT 100
        `).all(q, q, q);
      } else if (tab === 'sunat_ventas') {
        rows = dbHostal.prepare(`
          SELECT periodo_mes AS Mes, fecha_emision AS Fecha, nro_cp AS Comprobante,
                 COALESCE(tipo_doc, '') AS Tipo, receptor_doc AS RUC_DNI, receptor_nombre AS Cliente,
                 total AS Monto_Total, estado AS Estado, resumen_items AS Detalle_Alojamiento
          FROM sunat_ventas
          WHERE nro_cp LIKE ? OR receptor_nombre LIKE ? OR receptor_doc LIKE ? OR resumen_items LIKE ?
          ORDER BY periodo_mes DESC, nro_cp DESC LIMIT 200
        `).all(q, q, q, q);
      } else if (tab === 'sunat_lineas') {
        rows = dbHostal.prepare(`
          SELECT noche_dmy AS Noche, hab AS Hab, nro_cp AS Comprobante, tipo AS Tipo,
                 monto_linea AS Monto_Noche, total_cp AS Total_CP, cliente AS Cliente,
                 doc AS Doc, fecha_emision AS Emision, estado AS Estado, descripcion AS Descripcion
          FROM sunat_lineas_cache
          WHERE nro_cp LIKE ? OR cliente LIKE ? OR hab LIKE ? OR noche_dmy LIKE ?
          ORDER BY id_linea DESC LIMIT 200
        `).all(q, q, q, q);
      } else if (tab === 'sire_compras') {
        rows = dbHostal.prepare(`
          SELECT periodo_mes AS Mes, fecha_emision AS Fecha, serie_numero AS Comprobante,
                 ruc_proveedor AS RUC, razon_social AS Proveedor, monto_total AS Total_Soles,
                 estado_respaldo AS Estado_Respaldo, estado_bancarizacion AS Bancarizacion,
                 observacion AS Observacion
          FROM sire_compras_hostal
          WHERE serie_numero LIKE ? OR razon_social LIKE ? OR ruc_proveedor LIKE ?
          ORDER BY id DESC LIMIT 150
        `).all(q, q, q);
      } else if (tab === 'hostal_ocr') {
        rows = dbHostal.prepare(`
          SELECT fecha_hora AS Fecha, origen_chat AS Origen, categoria,
                 serie_numero AS Serie, ruc_emisor AS RUC, monto_detectado AS Monto,
                 operacion_nro AS Operacion, SUBSTR(texto_extraido,1,300) AS Texto_Extraido
          FROM hostal_documentos_ocr
          WHERE texto_extraido LIKE ? OR COALESCE(serie_numero,'') LIKE ?
          ORDER BY id DESC LIMIT 100
        `).all(q, q);
      }
    } catch (err) {
      rows = [{ Error: err.message }];
    }
    res.writeHead(200, { 'Content-Type': 'application/json; charset=utf-8', 'Cache-Control': 'no-store' });
    return res.end(JSON.stringify(rows));
  }

  res.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8', 'Cache-Control': 'no-store' });
  res.end(HTML);
});

server.on('error', (err) => {
  if (err.code !== 'EADDRINUSE') {
    console.error('Error en visor_web:', err.message);
  }
});

server.listen(PORT, '127.0.0.1', () => {
  console.log(`VISOR_RECEPCION_LISTO: http://127.0.0.1:${PORT}`);
});
