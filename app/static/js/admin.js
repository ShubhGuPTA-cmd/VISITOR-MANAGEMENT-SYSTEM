const A = { view: 'dash', hosts: [], seen: new Set(), first: true, timer: null };
const main = $('#main');

/* ---------------- auth ---------------- */
async function boot() {
  try { await api('/api/admin/overview'); showApp(); } catch (e) { showLogin(); }
}
function showLogin() { $('#appView').style.display = 'none'; $('#loginView').style.display = 'grid'; clearInterval(A.timer); $('#lp').focus(); }
function showApp() { $('#loginView').style.display = 'none'; $('#appView').style.display = 'grid'; go(A.view); clearInterval(A.timer); A.timer = setInterval(tick, 2500); }
$('#lbtn').onclick = async () => {
  try { await api('/api/admin/login', { method: 'POST', body: { user: $('#lu').value, password: $('#lp').value } }); showApp(); }
  catch (e) { $('#lerr').textContent = e.message; }
};
$('#lp').onkeydown = e => { if (e.key === 'Enter') $('#lbtn').click(); };
$('#logout').onclick = async () => { await api('/api/admin/logout', { method: 'POST' }); showLogin(); };
$$('.nav[data-v]').forEach(b => b.onclick = () => go(b.dataset.v));

function go(v) {
  A.view = v; $$('.nav[data-v]').forEach(b => b.classList.toggle('on', b.dataset.v === v));
  ({ dash: viewDash, visits: viewVisits, people: viewPeople, hosts: viewHosts, settings: viewSettings })[v]();
}
async function tick() {
  try {
    const o = await api('/api/admin/overview'); badge(o.pending);
    const fresh = o.pending.filter(p => !A.seen.has(p.id));
    fresh.forEach(p => A.seen.add(p.id));
    if (fresh.length && !A.first) { beep(); toast(`${fresh[0].name} is waiting at the door`); document.title = `(${o.pending.length}) Visitor waiting`; }
    A.first = false; if (!o.pending.length) document.title = 'Admin';
    if (A.view === 'dash') paintDash(o);
  } catch (e) { if (e.status === 401) showLogin(); }
}
function badge(p) { const d = $('#pdot'); d.style.display = p.length ? 'inline-block' : 'none'; d.textContent = p.length; }
function beep() { try { const c = new (window.AudioContext || window.webkitAudioContext)(), o = c.createOscillator(), g = c.createGain();
  o.connect(g); g.connect(c.destination); o.frequency.value = 880; g.gain.setValueAtTime(.2, c.currentTime); g.gain.exponentialRampToValueAtTime(.001, c.currentTime + .5); o.start(); o.stop(c.currentTime + .5); } catch (_) {} }
const head = (t, extra = '') => `<div class="top"><h2>${t}</h2><div>${extra}</div></div>`;
const modal = (html, wide) => { $('#modal').innerHTML = html; $('#modalBg').classList.add('on'); };
const closeModal = () => { $('#modalBg').classList.remove('on'); stopCamera($('#ev')); };
$('#modalBg').onclick = e => { if (e.target.id === 'modalBg') closeModal(); };
document.addEventListener('click', e => { if (e.target.matches('.tcell img, .zoom')) { $('#lbimg').src = e.target.src; $('#lb').classList.add('on'); } });
$('#lb').onclick = () => $('#lb').classList.remove('on');
async function loadHosts() { A.hosts = (await api('/api/admin/hosts')).hosts; return A.hosts; }

/* ---------------- dashboard ---------------- */
function viewDash() { main.innerHTML = head('Dashboard') + `<div id="dash"><div class="empty">Loading...</div></div>`; tick(); api('/api/admin/overview').then(paintDash); }
function paintDash(o) {
  if (A.view !== 'dash') return;
  const s = o.stats;
  $('#dash').innerHTML = `
  <div class="cards">
    <div class="stat"><span>Visitors today</span><b>${s.today}</b></div>
    <div class="stat"><span>Currently inside</span><b>${s.inside}</b></div>
    <div class="stat"><span>Auto-approved by face</span><b>${s.auto}</b></div>
    <div class="stat"><span>Denied / blocked</span><b>${s.denied}</b></div>
    <div class="stat"><span>Registered faces</span><b>${s.people}</b></div>
  </div>
  <div class="panel"><h3>&#128276; Waiting for approval ${o.pending.length ? `<span class="badge pending">${o.pending.length}</span>` : ''}</h3>
    ${o.pending.length ? `<div class="pend">${o.pending.map(pcard).join('')}</div>` : `<div class="empty">No visitors are waiting. New arrivals show up here instantly.</div>`}</div>
  <div class="panel"><h3>Recent activity</h3>${visitsTable(o.recent)}</div>`;
  $$('[data-decide]').forEach(b => b.onclick = async () => {
    const [id, d, rem] = b.dataset.decide.split(':');
    await api(`/api/admin/visits/${id}/decide`, { method: 'POST', body: { decision: d, remember: rem === '1' } });
    toast(d === 'approve' ? 'Visitor approved' : 'Visitor denied'); tick();
  });
  bindCheckout();
}
function pcard(p) { return `<div class="pcard"><img src="${p.snapshot_url}" alt="" class="zoom"><div style="flex:1;min-width:0">
  <h4>${esc(p.name)}</h4><small>${esc(p.purpose || 'No purpose given')}${p.host_name ? '<br>For ' + esc(p.host_name) : ''}${p.phone ? '<br>' + esc(p.phone) : ''}<br>${ago(p.created_at)}</small>
  <div class="acts"><button class="btn ok sm" data-decide="${p.id}:approve:0">Approve</button>
  <button class="btn sm" data-decide="${p.id}:approve:1" title="Approve and remember face for automatic entry next time">Approve &amp; remember</button>
  <button class="btn bad sm" data-decide="${p.id}:deny:0">Deny</button></div></div></div>`; }
function visitsTable(rows) {
  if (!rows.length) return `<div class="empty">Nothing here yet.</div>`;
  return `<div class="tablewrap"><table><thead><tr><th>Visitor</th><th>Host</th><th>Status</th><th>Arrived</th><th>Left</th><th></th></tr></thead><tbody>${rows.map(v => `
  <tr><td><div class="tcell">${v.snapshot_url ? `<img src="${v.snapshot_url}" alt="">` : ''}<div><b>${esc(v.name)}</b><br><small style="color:var(--muted)">${esc(v.purpose || '')}</small></div></div></td>
  <td>${esc(v.host_name || '-')}</td>
  <td><span class="badge ${v.status}">${v.status}</span> ${v.status === 'approved' ? `<span class="badge face">${v.method === 'face' ? 'face ' + Math.round(v.score * 100) + '%' : 'by ' + esc(v.decided_by)}</span>` : ''}</td>
  <td>${fmtTime(v.created_at)}</td><td>${v.checkout_at ? fmtTime(v.checkout_at) : '-'}</td>
  <td>${v.status === 'approved' && !v.checkout_at ? `<button class="btn ghost sm" data-out="${v.id}">Check out</button>` : ''}</td></tr>`).join('')}</tbody></table></div>`; }
function bindCheckout() { $$('[data-out]').forEach(b => b.onclick = async () => { await api(`/api/admin/visits/${b.dataset.out}/checkout`, { method: 'POST' }); toast('Checked out'); go(A.view); }); }

/* ---------------- visit log ---------------- */
function viewVisits() {
  main.innerHTML = head('Visit log', `<a class="btn ghost" id="csv" href="#">&#11015;&#65039; Export CSV</a>`) + `
  <div class="filters"><input id="q" placeholder="Search name, purpose, phone"><select id="st"><option value="">All statuses</option>
    <option>approved</option><option>pending</option><option>denied</option><option>blocked</option><option>expired</option></select>
    <input type="date" id="df"><input type="date" id="dt"></div><div class="panel" id="vt"></div>`;
  const qs = () => new URLSearchParams({ q: $('#q').value, status: $('#st').value, from: $('#df').value, to: $('#dt').value }).toString().replace(/[^&=]+=(&|$)/g, '');
  const load = async () => { const r = await api('/api/admin/visits?' + qs()); $('#vt').innerHTML = visitsTable(r.visits); bindCheckout(); $('#csv').href = '/api/admin/visits.csv?' + qs(); };
  let t; ['q', 'st', 'df', 'dt'].forEach(id => $('#' + id).oninput = () => { clearTimeout(t); t = setTimeout(load, 250); }); load();
}

/* ---------------- people ---------------- */
async function viewPeople() {
  await loadHosts(); const r = await api('/api/admin/people');
  main.innerHTML = head('People & faces', `<button class="btn" id="add">&#10133; Enrol new person</button>`) +
    (r.people.length ? `<div class="people">${r.people.map(p => `
    <div class="person ${p.status}"><div class="head"><img class="avatar zoom" src="${p.photo_url}" alt=""><div style="min-width:0"><h4>${esc(p.name)}</h4>
      <small>${esc(p.kind)}${p.host_name ? ' &middot; ' + esc(p.host_name) : ''}</small><br>
      <span class="badge ${p.status === 'blocked' ? 'blocked' : 'approved'}">${p.status}</span> <span class="badge">${p.faces} face samples</span></div></div>
      <small style="color:var(--muted)">${p.last_seen ? 'Last seen ' + ago(p.last_seen) : 'Never seen at the kiosk'}</small>
      <div class="acts"><button class="btn ghost sm" data-addface="${p.id}">+ Face samples</button>
      <button class="btn ghost sm" data-block="${p.id}:${p.status === 'blocked' ? 'active' : 'blocked'}">${p.status === 'blocked' ? 'Unblock' : 'Block'}</button>
      <button class="btn ghost sm" data-del="${p.id}" data-name="${esc(p.name)}">Delete</button></div></div>`).join('')}</div>`
    : `<div class="panel"><div class="empty">No one is registered yet. Enrol staff, residents and frequent visitors so they can walk straight in.</div></div>`);
  $('#add').onclick = () => enrolModal();
  $$('[data-addface]').forEach(b => b.onclick = () => enrolModal(+b.dataset.addface));
  $$('[data-block]').forEach(b => b.onclick = async () => { const [id, s] = b.dataset.block.split(':'); await api('/api/admin/people/' + id, { method: 'PATCH', body: { status: s } }); toast(s === 'blocked' ? 'Person blocked' : 'Person unblocked'); viewPeople(); });
  $$('[data-del]').forEach(b => b.onclick = async () => { if (confirm(`Delete ${b.dataset.name} and their face data?`)) { await api('/api/admin/people/' + b.dataset.del, { method: 'DELETE' }); viewPeople(); } });
}

const POSES = ['Look straight at the camera', 'Turn slightly to your LEFT', 'Turn slightly to your RIGHT', 'Chin slightly UP', 'Smile naturally'];
function enrolModal(existingId) {
  const hostOpts = A.hosts.filter(h => h.active).map(h => `<option value="${h.id}">${esc(h.name)}</option>`).join('');
  const shots = [];
  modal(`<h3>${existingId ? 'Add face samples' : 'Enrol a new person'}</h3><div class="enrol">
    <div><div class="ecam"><video id="ev" autoplay muted playsinline></video><div class="hint" id="eh">Starting camera...</div></div>
      <div class="pose" id="pose"></div><div class="shots" id="shots"></div>
      <div style="display:flex;gap:8px"><button class="btn" id="snap" disabled>&#128247; Capture</button><button class="btn ghost" id="undo">Undo</button></div></div>
    <div>${existingId ? '<p style="color:var(--muted)">Capture 3-5 new photos in different lighting or angles. More variety = better recognition.</p>' : `
      <div class="field"><label>Full name</label><input id="en"></div>
      <div class="grid2"><div class="field"><label>Phone</label><input id="ep"></div><div class="field"><label>Email</label><input id="ee"></div></div>
      <div class="grid2"><div class="field"><label>Type</label><select id="ek"><option value="visitor">Frequent visitor</option><option value="staff">Staff</option><option value="resident">Resident</option><option value="vip">VIP</option></select></div>
      <div class="field"><label>Default host</label><select id="eho"><option value="">None</option>${hostOpts}</select></div></div>
      <div class="field"><label>Notes</label><input id="eno"></div>`}
      <div class="err" id="eerr"></div>
      <div style="display:flex;gap:8px;justify-content:flex-end"><button class="btn ghost" id="ecancel">Cancel</button><button class="btn" id="esave" disabled>Save</button></div></div></div>`);
  const v = $('#ev'); const draw = () => {
    $('#shots').innerHTML = Array.from({ length: 5 }, (_, i) => shots[i] ? `<div class="f"><img src="${shots[i]}"></div>` : `<div>${i + 1}</div>`).join('');
    $('#pose').textContent = shots.length < 5 ? POSES[shots.length] : 'All set - you can save, or add more'; $('#esave').disabled = shots.length < 3; };
  draw();
  startCamera(v).then(() => { $('#eh').textContent = 'Position face in frame'; $('#snap').disabled = false; }).catch(e => { $('#eh').textContent = e.message; });
  $('#snap').onclick = async () => { const d = await api('/api/kiosk/detect', { method: 'POST', body: { frames: [grab(v, 480, .7)] } });
    if (!d.face) return toast('No face detected - move into frame', true);
    if (d.hint && d.reasons.some(r => !['turned', 'tilted'].includes(r))) return toast(d.hint, true);
    if (shots.length >= 8) return; shots.push(grab(v, 720, .9)); draw(); };
  $('#undo').onclick = () => { shots.pop(); draw(); };
  $('#ecancel').onclick = closeModal;
  $('#esave').onclick = async (force) => {
    $('#eerr').textContent = ''; $('#esave').disabled = true;
    try {
      if (existingId) { const r = await api(`/api/admin/people/${existingId}/faces`, { method: 'POST', body: { frames: shots } }); toast(`Added ${r.added} samples`); }
      else { const r = await api('/api/admin/people', { method: 'POST', body: { frames: shots, force: force === true, name: $('#en').value, phone: $('#ep').value, email: $('#ee').value,
        kind: $('#ek').value, host_id: $('#eho').value, notes: $('#eno').value } }); toast(`Enrolled with ${r.samples} samples`); }
      closeModal(); viewPeople();
    } catch (e) { $('#eerr').innerHTML = esc(e.message) + (e.data?.duplicate ? ' <a href="#" id="force">Enrol anyway</a>' : ''); $('#esave').disabled = false;
      if ($('#force')) $('#force').onclick = ev => { ev.preventDefault(); $('#esave').onclick(true); }; }
  };
}

/* ---------------- hosts ---------------- */
async function viewHosts() {
  const hs = await loadHosts();
  main.innerHTML = head('Hosts', `<button class="btn" id="addh">&#10133; Add host</button>`) + `<div class="panel">
  ${hs.filter(h => h.active).length ? `<div class="tablewrap"><table><thead><tr><th>Name</th><th>Unit / dept</th><th>Email</th><th>Phone</th><th></th></tr></thead><tbody>
  ${hs.filter(h => h.active).map(h => `<tr><td><b>${esc(h.name)}</b></td><td>${esc(h.unit)}</td><td>${esc(h.email)}</td><td>${esc(h.phone)}</td>
  <td><button class="btn ghost sm" data-eh="${h.id}">Edit</button> <button class="btn ghost sm" data-dh="${h.id}">Remove</button></td></tr>`).join('')}</tbody></table></div>`
  : `<div class="empty">Add the people visitors can come to see. They'll be notified by email/webhook when someone arrives.</div>`}</div>`;
  const edit = h => { h = h || {}; modal(`<h3>${h.id ? 'Edit' : 'Add'} host</h3><div class="field"><label>Name</label><input id="hn" value="${esc(h.name || '')}"></div>
    <div class="grid2"><div class="field"><label>Unit / department</label><input id="hu" value="${esc(h.unit || '')}"></div><div class="field"><label>Phone</label><input id="hp" value="${esc(h.phone || '')}"></div></div>
    <div class="field"><label>Email (receives approval links)</label><input id="he" value="${esc(h.email || '')}"></div>
    <div style="display:flex;gap:8px;justify-content:flex-end"><button class="btn ghost" id="hc">Cancel</button><button class="btn" id="hs">Save</button></div>`);
    $('#hc').onclick = closeModal; $('#hs').onclick = async () => { try { await api('/api/admin/hosts', { method: 'POST', body: { id: h.id, name: $('#hn').value, unit: $('#hu').value, phone: $('#hp').value, email: $('#he').value } }); closeModal(); viewHosts(); } catch (e) { toast(e.message, true); } }; };
  $('#addh').onclick = () => edit(); $$('[data-eh]').forEach(b => b.onclick = () => edit(hs.find(h => h.id == b.dataset.eh)));
  $$('[data-dh]').forEach(b => b.onclick = async () => { await api('/api/admin/hosts/' + b.dataset.dh, { method: 'DELETE' }); viewHosts(); });
}

/* ---------------- settings ---------------- */
async function viewSettings() {
  const r = await api('/api/admin/settings'), s = r.settings;
  const rng = (k, label, min, max, step, help) => `<div class="field"><label>${label}</label><div class="range"><input type="range" id="${k}" min="${min}" max="${max}" step="${step}" value="${s[k]}"><output id="${k}_o">${s[k]}</output></div><div class="help">${help}</div></div>`;
  main.innerHTML = head('Settings') + `<div class="panel"><h3>&#129504; Face recognition</h3>
  <p style="color:var(--muted);margin-top:-6px">Engine: ${esc(r.model)} (RetinaFace detector + ArcFace embeddings). Defaults suit most doors; raise the threshold for stricter security.</p>
  ${rng('match_threshold', 'Match threshold (similarity)', 0.3, 0.7, 0.01, 'Higher = fewer false accepts but more people asked to wait for approval. Default 0.45.')}
  ${rng('match_margin', 'Runner-up margin', 0, 0.2, 0.01, 'The best match must beat the second-best person by this much, otherwise a human decides.')}
  ${rng('min_face_px', 'Minimum face size (px)', 50, 250, 5, 'Faces smaller than this are asked to move closer.')}
  ${rng('blur_min', 'Sharpness required', 0, 150, 5, 'Rejects blurry frames before they can cause a wrong match.')}
  ${rng('frames_required', 'Consistent frames needed', 1, 3, 1, 'Several frames must agree on the same person. 2+ recommended.')}</div>
  <div class="panel"><h3>&#9201;&#65039; Workflow &amp; privacy</h3><div class="grid2">
  <div class="field"><label>Organisation name</label><input id="org_name" value="${esc(s.org_name)}"></div>
  <div class="field"><label>Repeat check-in cooldown (seconds)</label><input id="cooldown_seconds" type="number" value="${s.cooldown_seconds}"></div>
  <div class="field"><label>Approval timeout (minutes)</label><input id="approval_timeout_min" type="number" value="${s.approval_timeout_min}"></div>
  <div class="field"><label>Delete visit photos after (days, 0 = never)</label><input id="retention_days" type="number" value="${s.retention_days}"></div></div></div>
  <div class="panel"><h3>&#128236; Notifications</h3><p>Email: <span class="badge ${r.notify.email ? 'approved' : ''}">${r.notify.email ? 'configured' : 'not configured'}</span> &nbsp;
  Webhook (Slack/Teams/Discord): <span class="badge ${r.notify.webhook ? 'approved' : ''}">${r.notify.webhook ? 'configured' : 'not configured'}</span></p>
  <p style="color:var(--muted)">Set the <code>VMS_SMTP_*</code> and <code>VMS_WEBHOOK_URL</code> environment variables - see the README.</p></div>
  <button class="btn lg" id="save">Save settings</button>`;
  ['match_threshold', 'match_margin', 'min_face_px', 'blur_min', 'frames_required'].forEach(k => $('#' + k).oninput = e => $('#' + k + '_o').textContent = e.target.value);
  $('#save').onclick = async () => { const b = {}; Object.keys(s).forEach(k => { const el = $('#' + k); if (el) b[k] = el.value; });
    await api('/api/admin/settings', { method: 'POST', body: b }); toast('Settings saved'); };
}
boot();
