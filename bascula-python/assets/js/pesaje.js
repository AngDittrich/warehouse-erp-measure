import { cargarHistorial } from './bascula-serial.js';

const $ = (sel) => document.querySelector(sel);

function mostrarTabs() {
  document.querySelectorAll('.tab').forEach((boton) => {
    boton.addEventListener('click', () => {
      document.querySelectorAll('.tab').forEach((b) => b.classList.remove('active'));
      document.querySelectorAll('.panel').forEach((p) => p.classList.remove('active'));
      boton.classList.add('active');
      $('#panel-' + boton.dataset.panel).classList.add('active');
    });
  });
}

async function cargarPuertos() {
  const select = $('#pesaje-puerto');
  const previo = select.value;
  select.innerHTML = '';
  try {
    const res = await fetch('/api/puertos');
    const data = await res.json();
    const lista = data.puertos || [];
    if (!lista.length) {
      const opt = document.createElement('option');
      opt.value = '';
      opt.textContent = 'No hay /dev/ttyUSB*';
      select.appendChild(opt);
      return;
    }
    for (const puerto of lista) {
      const opt = document.createElement('option');
      opt.value = puerto.path;
      opt.textContent = puerto.writable ? puerto.path : `${puerto.path} (sin permiso)`;
      select.appendChild(opt);
    }
    if (previo && [...select.options].some((o) => o.value === previo)) {
      select.value = previo;
    }
  } catch (err) {
    const opt = document.createElement('option');
    opt.value = '';
    opt.textContent = 'No se pudo listar puertos';
    select.appendChild(opt);
  }
}

function setEstado(texto, ok) {
  const badge = $('#pesaje-estado');
  badge.textContent = texto;
  badge.className = ok ? 'badge stable' : 'badge unstable';
}

async function pesar() {
  const puerto = $('#pesaje-puerto').value;
  if (!puerto) {
    setEstado('Elige un puerto', false);
    return;
  }
  const boton = $('#btn-pesar');
  boton.disabled = true;
  setEstado('Leyendo báscula…', true);
  $('#pesaje-peso').textContent = '…';

  const body = {
    puerto,
    baudrate: 9600,
    data_bits: 8,
    stop_bits: 1,
    paridad: 'none',
    segundos: 0.45,
    dtr: true,
    rts: true,
    articulo: $('#art-codigo').value.trim(),
    nota: $('#art-nota').value.trim(),
  };

  try {
    const res = await fetch('/api/pesar', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    const data = await res.json();
    if (!data.ok) {
      $('#pesaje-peso').textContent = '—';
      setEstado(data.error || 'Error al leer', false);
      $('#pesaje-raw').textContent = data.crudo || '';
      return;
    }
    const decimales = 3;
    $('#pesaje-peso').textContent = Number(data.peso).toFixed(decimales);
    $('#pesaje-unidad').textContent = data.unidad || 'kg';
    setEstado('Peso registrado', true);
    $('#pesaje-raw').textContent = `${data.crudo || ''} → ${data.trama || ''}`;
    await cargarHistorial();
  } catch (err) {
    $('#pesaje-peso').textContent = '—';
    setEstado(err.message || 'Fallo de red', false);
  } finally {
    boton.disabled = false;
  }
}

document.addEventListener('DOMContentLoaded', () => {
  mostrarTabs();
  cargarPuertos();
  $('#btn-refrescar-puertos').addEventListener('click', cargarPuertos);
  $('#btn-pesar').addEventListener('click', pesar);
});
