/**
 * Lectura de báscula vía Web Serial API (Chrome / Edge).
 * Backend: servidor Python local.
 */

const $ = (sel) => document.querySelector(sel);

const state = {
  port: null,
  reader: null,
  buffer: '',
  lastWeight: null,
  reading: false,
  bytesReceived: 0,
  chunksReceived: 0,
  openOptions: null,
  breakCount: 0,
};

const LOG_MAX = 400;

/**
 * PRENLIS manda cada trama de 8 caracteres al revés.
 * "=" es el inicio de la trama una vez invertida.
 * "48.4000=" → "=0004.84" → 4.84 kg
 * (04.4000= también da 4.40, por eso el 4.4 parecía correcto leído al derecho.)
 */
const PESO_RE = /([+-]?\s*\d+(?:[.,]\d+)?)/;

export function extraerPeso(crudo) {
  if (!crudo || !crudo.includes('=')) {
    return null;
  }
  const invertido = crudo.split('').reverse().join('');
  const partes = invertido.split('=');
  for (let i = 1; i < partes.length; i += 1) {
    const parte = partes[i];
    if (!parte.trim()) continue;
    const match = parte.match(PESO_RE);
    if (!match) continue;
    const peso = parseFloat(match[1].replace(',', '.').replace(/\s/g, ''));
    if (!Number.isFinite(peso)) continue;
    return {
      peso,
      unidad: 'kg',
      estable: true,
      raw: parte.trim(),
      crudo: crudo.slice(-24),
    };
  }
  return null;
}

function getSerialOptions(overrides = {}) {
  return {
    baudRate: parseInt($('#baudRate').value, 10),
    dataBits: parseInt($('#dataBits').value, 10),
    stopBits: parseInt($('#stopBits').value, 10),
    parity: $('#parity').value,
    flowControl: $('#flowControl').value,
    ...overrides,
  };
}

function formatOptions(opts) {
  return JSON.stringify(opts);
}

function bytesToHex(u8) {
  return [...u8].map((b) => b.toString(16).padStart(2, '0')).join(' ');
}

function log(line, type = 'info') {
  const el = $('#log');
  const row = document.createElement('div');
  row.className = `log-line log-${type}`;
  row.textContent = `[${new Date().toLocaleTimeString()}] ${line}`;
  el.prepend(row);
  while (el.children.length > LOG_MAX) {
    el.removeChild(el.lastChild);
  }
}

function logError(prefix, err) {
  log(`${prefix}: ${err?.message ?? err}`, 'err');
  if (err?.name) {
    log(`  → DOMException.name: ${err.name}`, 'err');
  }
  if (err?.code != null) {
    log(`  → code: ${err.code}`, 'err');
  }
}

function describePortInfo(info) {
  if (!info || (!info.usbVendorId && !info.usbProductId)) {
    return 'sin IDs USB (puede ser ttyS clásico)';
  }
  const vid = info.usbVendorId?.toString(16).padStart(4, '0');
  const pid = info.usbProductId?.toString(16).padStart(4, '0');
  return `USB vid:0x${vid} pid:0x${pid}`;
}

async function logAuthorizedPorts() {
  try {
    const ports = await navigator.serial.getPorts();
    log(`Puertos ya autorizados en Chrome: ${ports.length}`, 'info');
    ports.forEach((p, i) => {
      log(`  [${i}] ${describePortInfo(p.getInfo())}`, 'info');
    });
  } catch (err) {
    logError('No se pudo listar puertos autorizados', err);
  }
}

async function runSystemDiagnostico() {
  log('--- Diagnóstico Linux / permisos ---', 'info');
  try {
    const res = await fetch('/api/diagnostico');
    const data = await res.json();
    if (!data.ok) {
      log('Diagnóstico no disponible', 'warn');
      return;
    }
    log(`Usuario: ${data.user} (uid ${data.uid})`, 'info');
    log(`Grupos: ${data.groups.join(', ')}`, 'info');
    log(
      data.in_dialout
        ? 'Grupo dialout: SÍ'
        : 'Grupo dialout: NO (plugdev solo no abre /dev/ttyUSB0)',
      data.in_dialout ? 'ok' : 'err'
    );
    log(`plugdev: ${data.in_plugdev ? 'sí' : 'no'}`, 'info');
    log(
      data.can_access_serial
        ? 'Acceso serial USB: OK (R/W en ttyUSB/ttyACM)'
        : 'Acceso serial USB: DENEGADO — causa del NetworkError en Chrome',
      data.can_access_serial ? 'ok' : 'err'
    );
    if (data.adapter_note) {
      log(data.adapter_note, 'info');
    }
    log(`ModemManager: ${data.ModemManager} | brltty: ${data.brltty}`, 'info');
    if (!data.devices?.length) {
      log('Ningún /dev/ttyUSB* o ttyACM* detectado', 'warn');
    } else {
      for (const d of data.devices) {
        let msg = `${d.path} ${d.owner ?? '?'}:${d.group ?? '?'} mode=${d.mode ?? '?'} R=${d.readable} W=${d.writable}`;
        if (d.fuser?.length) {
          msg += ` OCUPADO: ${d.fuser.join(' | ')}`;
        }
        log(msg, d.writable ? 'ok' : 'err');
      }
    }
    for (const h of data.hints ?? []) {
      log(`Sugerencia: ${h}`, 'warn');
    }
  } catch (err) {
    logError('Error al llamar /api/diagnostico', err);
  }
  log('--- Fin diagnóstico ---', 'info');
}

async function openPortWithRetries(port) {
  const attempts = [];
  const base = getSerialOptions();

  attempts.push({ label: 'configuración UI', opts: { ...base } });

  if (base.flowControl !== 'none') {
    attempts.push({
      label: 'flowControl=none',
      opts: { ...base, flowControl: 'none' },
    });
  }

  attempts.push({
    label: '9600 8N1 sin flow',
    opts: {
      baudRate: 9600,
      dataBits: 8,
      stopBits: 1,
      parity: 'none',
      flowControl: 'none',
    },
  });

  if (!$('#auto-retry').checked) {
    attempts.length = 1;
  }

  let lastErr = null;
  for (const attempt of attempts) {
    log(`Intento open (${attempt.label}): ${formatOptions(attempt.opts)}`, 'info');
    try {
      await port.open(attempt.opts);
      state.openOptions = attempt.opts;
      log(`Puerto abierto con: ${attempt.label}`, 'ok');
      return attempt.opts;
    } catch (err) {
      lastErr = err;
      logError(`Open falló (${attempt.label})`, err);
      try {
        await port.close();
      } catch {
        /* puede no estar abierto */
      }
    }
  }
  throw lastErr ?? new Error('No se pudo abrir el puerto');
}

function updateDisplay(parsed) {
  state.lastWeight = parsed;
  $('#peso-display').textContent = parsed.peso.toFixed(
    parseInt($('#decimals').value, 10)
  );
  $('#unidad-display').textContent = parsed.unidad;
  $('#estado-display').textContent = parsed.estable ? 'Estable' : 'Inestable';
  $('#estado-display').className = parsed.estable ? 'badge stable' : 'badge unstable';
  $('#raw-display').textContent = `${parsed.crudo || ''} → ${parsed.raw}`;
}

function updateTrafficStats(extraBytes = 0) {
  if (extraBytes) {
    state.bytesReceived += extraBytes;
    state.chunksReceived += 1;
  }
  const el = $('#traffic-stats');
  if (el) {
    el.textContent = `Bytes RX: ${state.bytesReceived} | chunks: ${state.chunksReceived}`;
  }
}

function processChunk(text, rawBytes) {
  if ($('#debug-hex').checked && rawBytes?.length) {
    const hex = bytesToHex(rawBytes);
    const shown = hex.length > 180 ? `${hex.slice(0, 180)} …` : hex;
    log(`HEX (${rawBytes.length}): ${shown}`, 'rx');
  }

  updateTrafficStats(rawBytes?.length ?? text.length);
  state.breakCount = 0;

  state.buffer += text;
  if (state.buffer.length > 128) {
    state.buffer = state.buffer.slice(-96);
  }

  const parsed = extraerPeso(state.buffer);
  if (!parsed) return;
  const changed = !state.lastWeight || state.lastWeight.raw !== parsed.raw;
  updateDisplay(parsed);
  if (changed) {
    log(`RX ${parsed.crudo} → ${parsed.raw} → ${parsed.peso} kg`, 'ok');
  }
}

function isBreakError(err) {
  return err?.name === 'BreakError' || /break/i.test(err?.message || '');
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function reopenKeepingPort() {
  const port = state.port;
  const opts = state.openOptions;
  if (!port || !opts || !state.reading) return false;
  try {
    await port.close();
  } catch {
    /* ya cerrado */
  }
  try {
    await port.open(opts);
    if (!$('#skip-signals').checked) {
      await setPortSignals();
    }
    log(`Puerto reabierto (${opts.baudRate} ${opts.dataBits}${opts.parity[0].toUpperCase()}${opts.stopBits})`, 'ok');
    return true;
  } catch (err) {
    logError('No se pudo reabrir tras break', err);
    return false;
  }
}

async function readLoop() {
  while (state.reading && state.port) {
    if (!state.port.readable) {
      log('Stream serial caído. Reabriendo…', 'warn');
      if (!(await reopenKeepingPort())) break;
      continue;
    }

    let reader;
    try {
      reader = state.port.readable.getReader();
      state.reader = reader;
    } catch (err) {
      if (!state.reading) break;
      logError('getReader', err);
      if (!(await reopenKeepingPort())) break;
      continue;
    }

    let broken = false;
    try {
      for (;;) {
        const { value, done } = await reader.read();
        if (done) {
          log('Reader: stream cerrado', 'warn');
          broken = true;
          break;
        }
        if (value?.length) {
          processChunk(new TextDecoder('utf-8', { fatal: false }).decode(value), value);
        }
      }
    } catch (err) {
      if (!state.reading) break;
      if (isBreakError(err)) {
        broken = true;
        state.breakCount += 1;
        log(
          `Break #${state.breakCount}: RX en nivel de break (baud distinto, TX/RX cruzados, o línea en 0). Reabriendo…`,
          'warn'
        );
        if (state.breakCount === 1 || state.breakCount % 4 === 0) {
          log(
            'Prueba: marca Omitir DTR/RTS, cambia baudios a 2400 / 4800 / 19200 y reconecta. GND común y TX de la báscula al RX del adaptador.',
            'warn'
          );
        }
        if (state.breakCount > 12) {
          log('Demasiados breaks seguidos. Desconecta, cambia baudios y vuelve a conectar.', 'err');
          break;
        }
      } else {
        logError('Error en readLoop', err);
        break;
      }
    } finally {
      try {
        reader.releaseLock();
      } catch {
        /* ignore */
      }
      if (state.reader === reader) state.reader = null;
    }

    if (!state.reading) break;
    if (!broken) break;
    await sleep(400);
    if (!(await reopenKeepingPort())) break;
  }

  if (state.reading && state.breakCount > 12) {
    log('Lectura detenida por breaks. El puerto puede seguir abierto: pulsa Desconectar.', 'err');
  }
}

async function setPortSignals() {
  if (!state.port) return;
  const dtr = $('#dtr').checked;
  const rts = $('#rts').checked;
  try {
    await state.port.setSignals({
      dataTerminalReady: dtr,
      requestToSend: rts,
    });
    log(`Señales DTR=${dtr} RTS=${rts} aplicadas`, 'ok');
  } catch (err) {
    logError('setSignals no soportado o falló', err);
  }
}

async function logPortConnectionState() {
  if (!state.port) return;
  log(
    `Estado: readable=${!!state.port.readable} writable=${!!state.port.writable}`,
    'info'
  );
}

export async function conectarBascula() {
  if (!('serial' in navigator)) {
    alert(
      'Web Serial no está disponible. Usa Google Chrome o Microsoft Edge en esta PC (HTTPS o localhost).'
    );
    return;
  }

  if (state.port) {
    log('Ya hay un puerto abierto. Desconecta primero.', 'warn');
    return;
  }

  state.bytesReceived = 0;
  state.chunksReceived = 0;
  updateTrafficStats();

  await logAuthorizedPorts();

  let port;
  try {
    log('Abriendo selector de puerto…', 'info');
    port = await navigator.serial.requestPort();
    const info = port.getInfo();
    log(`Puerto elegido: ${describePortInfo(info)}`, 'info');

    await openPortWithRetries(port);

    state.port = port;

    if ($('#skip-signals').checked) {
      log('Omitiendo DTR/RTS (opción activada)', 'warn');
    } else {
      await setPortSignals();
    }

    await logPortConnectionState();

    state.reading = true;
    $('#btn-conectar').disabled = true;
    $('#btn-desconectar').disabled = false;
    $('#btn-capturar').disabled = false;
    $('#btn-enviar-w').disabled = false;
    log('Lectura iniciada. Si no hay RX, revisa baud/modo continuo en báscula.', 'ok');

    readLoop();

    window.setTimeout(() => {
      if (state.bytesReceived === 0 && state.reading) {
        log(
          '0 bytes tras 5 s: cable TX/RX/GND, baud 9600, salida continua en PRENLIS, o pulsa Enviar W',
          'warn'
        );
      }
    }, 5000);
  } catch (err) {
    logError('Conexión fallida', err);
    await runSystemDiagnostico();
    try {
      if (port) {
        await port.close();
      }
    } catch {
      /* ignore */
    }
    state.port = null;
  }
}

export async function desconectarBascula() {
  state.reading = false;

  if (state.reader) {
    try {
      await state.reader.cancel();
    } catch (err) {
      logError('Cancel reader', err);
    }
  }

  if (state.port) {
    try {
      await state.port.close();
    } catch (err) {
      logError('Cierre de puerto', err);
    }
  }

  state.port = null;
  state.buffer = '';
  $('#btn-conectar').disabled = false;
  $('#btn-desconectar').disabled = true;
  $('#btn-capturar').disabled = true;
  $('#btn-enviar-w').disabled = true;
  log('Puerto cerrado.', 'info');
}

export async function enviarComandoPeso() {
  if (!state.port?.writable) {
    log('Puerto no listo para escribir.', 'err');
    return;
  }
  const writer = state.port.writable.getWriter();
  try {
    await writer.write(new Uint8Array([0x57]));
    log('TX: byte 0x57 ("W" solicitar peso)', 'tx');
  } catch (err) {
    logError('TX falló', err);
  } finally {
    writer.releaseLock();
  }
}

export async function capturarPeso(nota = '') {
  if (!state.lastWeight) {
    log('No hay peso parseado aún.', 'warn');
    return null;
  }

  const body = {
    peso: state.lastWeight.peso,
    unidad: state.lastWeight.unidad,
    estable: state.lastWeight.estable,
    raw: state.lastWeight.raw,
    nota,
  };

  const res = await fetch('/api/registrar-peso', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });

  const data = await res.json();
  if (!data.ok) {
    log(data.error || 'Error al guardar', 'err');
    return null;
  }

  log(`Guardado: ${body.peso} ${body.unidad}`, 'ok');
  await cargarHistorial();
  return data.registro;
}

export async function cargarHistorial() {
  const res = await fetch('/api/pesos');
  const data = await res.json();
  const tbody = $('#historial-body');
  tbody.innerHTML = '';

  if (!data.ok || !data.pesos?.length) {
    tbody.innerHTML =
      '<tr><td colspan="4" class="empty">Sin registros</td></tr>';
    return;
  }

  for (const row of data.pesos) {
    const tr = document.createElement('tr');
    const fecha = new Date(row.fecha).toLocaleString();
    const detalle = row.nota || row.trama || row.raw || '';
    tr.innerHTML = `
      <td>${fecha}</td>
      <td class="num">${Number(row.peso).toFixed(3)}</td>
      <td>${escapeHtml(row.articulo || '')}</td>
      <td class="mono">${escapeHtml(detalle)}</td>
    `;
    tbody.appendChild(tr);
  }
}

function escapeHtml(s) {
  return s
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');
}

export function initBasculaUI() {
  $('#btn-conectar').addEventListener('click', () => conectarBascula());
  $('#btn-desconectar').addEventListener('click', () => desconectarBascula());
  $('#btn-capturar').addEventListener('click', () => {
    capturarPeso($('#nota').value.trim());
  });
  $('#btn-enviar-w').addEventListener('click', () => enviarComandoPeso());
  $('#btn-refrescar').addEventListener('click', () => cargarHistorial());
  $('#btn-diagnostico').addEventListener('click', () => runSystemDiagnostico());

  cargarHistorial();
  updateTrafficStats();

  if (!('serial' in navigator)) {
    log('Navegador sin Web Serial. Usa Chrome/Edge.', 'err');
  } else {
    log('Listo. Conecta USB-RS232 y pulsa "Conectar puerto".', 'info');
    log('Si falla open, pulsa "Diagnóstico sistema" o revisa el log tras el error.', 'info');
    runSystemDiagnostico();
  }
}

document.addEventListener('DOMContentLoaded', initBasculaUI);
