/* Shared helpers: fetch wrapper, toast, camera, frame capture */
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));

async function api(url, opts = {}) {
  const o = { headers: {}, ...opts };
  if (o.body && typeof o.body !== 'string') { o.body = JSON.stringify(o.body); o.headers['Content-Type'] = 'application/json'; }
  const r = await fetch(url, o);
  let data = {};
  try { data = await r.json(); } catch (_) {}
  if (!r.ok) { const e = new Error(data.error || `Request failed (${r.status})`); e.status = r.status; e.data = data; throw e; }
  return data;
}

let _toastT;
function toast(msg, err = false) {
  let t = $('#toast');
  if (!t) { t = document.createElement('div'); t.id = 'toast'; t.className = 'toast'; document.body.appendChild(t); }
  t.textContent = msg; t.classList.toggle('err', !!err); t.classList.add('show');
  clearTimeout(_toastT); _toastT = setTimeout(() => t.classList.remove('show'), 3200);
}

async function startCamera(video) {
  if (!navigator.mediaDevices?.getUserMedia)
    throw new Error('Camera needs https:// or http://localhost - see README');
  const stream = await navigator.mediaDevices.getUserMedia({
    video: { width: { ideal: 1280 }, height: { ideal: 960 }, facingMode: 'user', frameRate: { ideal: 30 } }, audio: false });
  video.srcObject = stream; video.muted = true; video.playsInline = true;
  await video.play();
  return stream;
}
function stopCamera(video) { video?.srcObject?.getTracks().forEach(t => t.stop()); if (video) video.srcObject = null; }

const _cv = document.createElement('canvas');
function grab(video, maxW = 640, q = 0.88) {
  const w = video.videoWidth, h = video.videoHeight;
  if (!w) return null;
  const s = Math.min(1, maxW / w);
  _cv.width = Math.round(w * s); _cv.height = Math.round(h * s);
  _cv.getContext('2d').drawImage(video, 0, 0, _cv.width, _cv.height);
  return _cv.toDataURL('image/jpeg', q);
}
const sleep = ms => new Promise(r => setTimeout(r, ms));
async function burst(video, n = 3, gap = 180, maxW = 720) {
  const out = [];
  for (let i = 0; i < n; i++) { const f = grab(video, maxW); if (f) out.push(f); if (i < n - 1) await sleep(gap); }
  return out;
}
/* draw a rounded face box on an overlay canvas, honouring object-fit:cover cropping */
function drawBox(canvas, video, bbox, color) {
  const W = canvas.clientWidth, H = canvas.clientHeight;
  canvas.width = W * devicePixelRatio; canvas.height = H * devicePixelRatio;
  const g = canvas.getContext('2d'); g.scale(devicePixelRatio, devicePixelRatio); g.clearRect(0, 0, W, H);
  if (!bbox || !video.videoWidth) return;
  const vw = video.videoWidth, vh = video.videoHeight, sc = Math.max(W / vw, H / vh);
  const ox = (W - vw * sc) / 2, oy = (H - vh * sc) / 2;
  const x = ox + bbox[0] * vw * sc, y = oy + bbox[1] * vh * sc, w = (bbox[2] - bbox[0]) * vw * sc, h = (bbox[3] - bbox[1]) * vh * sc;
  g.strokeStyle = color; g.lineWidth = 3; g.lineJoin = 'round';
  const L = Math.min(w, h) * 0.22; g.beginPath();
  [[x, y, 1, 1], [x + w, y, -1, 1], [x, y + h, 1, -1], [x + w, y + h, -1, -1]].forEach(([cx, cy, dx, dy]) => {
    g.moveTo(cx + dx * L, cy); g.lineTo(cx, cy); g.lineTo(cx, cy + dy * L); });
  g.stroke();
}
const fmtTime = t => t ? new Date(t * 1000).toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }) : '-';
const ago = t => { const s = Date.now() / 1000 - t; if (s < 60) return 'just now'; if (s < 3600) return Math.floor(s / 60) + ' min ago'; if (s < 86400) return Math.floor(s / 3600) + ' h ago'; return fmtTime(t); };
