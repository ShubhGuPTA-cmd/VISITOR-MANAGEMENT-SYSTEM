const video = $('#video'), cam = $('#cam'), ov = $('#ov'), hintEl = $('#hint'), card = $('#card');
const K = { hosts: [], busy: false, mode: 'idle', okStreak: 0, frames: [], timers: [] };

setInterval(() => $('#clock').textContent = new Date().toLocaleString([], { weekday: 'short', hour: '2-digit', minute: '2-digit' }), 1000);

function setHint(t, state) { hintEl.textContent = t; cam.classList.remove('ready', 'busy'); if (state) cam.classList.add(state); }
function later(fn, ms) { const t = setTimeout(fn, ms); K.timers.push(t); return t; }
function clearTimers() { K.timers.forEach(clearTimeout); K.timers = []; }
function render(html) { card.innerHTML = html; card.style.animation = 'none'; void card.offsetWidth; card.style.animation = ''; }

function idle() {
  clearTimers(); K.mode = 'idle'; K.okStreak = 0; K.busy = false; K.frames = [];
  setHint('Step in front of the camera');
  render(`<h1>Welcome &#128075;</h1>
    <p>Look at the camera and we'll check you in automatically.</p>
    <div class="steps"><div><b>1</b> Face the camera, keep your face inside the oval</div>
      <div><b>2</b> Registered visitors are let in instantly</div>
      <div><b>3</b> New here? We'll notify your host to approve</div></div>
    <div class="k-actions"><button class="btn ghost" id="newBtn">I'm a new visitor</button></div>`);
  $('#newBtn').onclick = () => capture(true);
}

/* ---- live loop: cheap detection to guide the user, then a multi-frame burst for the real decision ---- */
async function loop() {
  if (K.mode === 'idle' && !K.busy && video.videoWidth) {
    try {
      const f = grab(video, 480, 0.7);
      const d = await api('/api/kiosk/detect', { method: 'POST', body: { frames: [f] } });
      if (K.mode === 'idle') {
        drawBox(ov, video, d.bbox, d.ok ? '#10b981' : '#fbbf24');
        if (!d.face) { K.okStreak = 0; setHint('Step in front of the camera'); }
        else if (!d.ok) { K.okStreak = 0; setHint(d.hint || 'Adjust your position'); }
        else { K.okStreak++; setHint('Hold still...', 'ready'); if (K.okStreak >= 2) { capture(false); } }
      }
    } catch (e) { setHint('Connection problem - retrying'); }
  } else if (K.mode !== 'idle') drawBox(ov, video, null);
  setTimeout(loop, 450);
}

async function capture(manual) {
  if (K.busy) return; K.busy = true; K.mode = 'recognizing';
  setHint('Verifying...', 'busy');
  render(`<div class="big-icon info pulse">&#128269;</div><h1>Checking...</h1><p>Please hold still for a moment.</p>`);
  K.frames = await burst(video, 3, 160);
  try {
    const r = await api('/api/kiosk/recognize', { method: 'POST', body: { frames: K.frames } });
    handle(r, manual);
  } catch (e) { toast(e.message, true); idle(); }
}

function handle(r, manual) {
  if (r.state === 'welcome') return welcome(r);
  if (r.state === 'choose') return choose(r);
  if (r.state === 'blocked') return blocked();
  if (r.state === 'unknown') return form(r.uncertain);
  // quality problems: when the user asked to register we still let them through, otherwise guide them
  if (manual && K.frames.length) return form(false);
  setHint(r.hint || 'Try again'); K.mode = 'idle'; K.busy = false; K.okStreak = 0;
  render(`<div class="big-icon wait">&#9888;&#65039;</div><h1>${esc(r.hint || "Let's try again")}</h1><p>Look straight at the camera in good light.</p>
    <div class="k-actions"><button class="btn ghost" id="newBtn">I'm a new visitor</button></div>`);
  $('#newBtn').onclick = () => capture(true);
  later(idle, 3500);
}

function welcome(r) {
  K.mode = 'result'; setHint('Access granted', 'ready');
  const p = r.person;
  render(`<div class="who">${p.photo ? `<img src="${p.photo}" alt="" onerror="this.style.display='none'">` : ''}
      <div><div class="big-icon ok" style="width:56px;height:56px;font-size:28px;margin:0">&#10003;</div></div></div>
    <h1>Welcome${r.repeat ? ' back' : ''}, ${esc(p.name.split(' ')[0])}!</h1>
    <p>${p.host ? `Your host <b>${esc(p.host)}</b> has been notified. ` : ''}You're checked in - please proceed.</p>
    <div class="progress"><div id="bar"></div></div>`);
  countdown(6000);
}
function choose(r) {
  K.mode = 'result'; setHint('Recognised', 'ready');
  render(`<div class="who"><img src="${r.person.photo}" alt="" onerror="this.style.display='none'"><div><h1 style="font-size:28px;margin:0">Hi, ${esc(r.person.name.split(' ')[0])}</h1></div></div>
    <p>You're currently checked in. What would you like to do?</p>
    <div class="k-actions"><button class="btn lg bad" id="out">Check out</button><button class="btn lg ghost" id="in">New visit</button></div>
    <div class="progress"><div id="bar"></div></div>`);
  const go = async a => { try { const x = await api('/api/kiosk/action', { method: 'POST', body: { ticket: r.ticket, action: a } });
      x.state === 'goodbye' ? goodbye(x) : welcome(x); } catch (e) { toast(e.message, true); idle(); } };
  $('#out').onclick = () => go('checkout'); $('#in').onclick = () => go('checkin'); countdown(15000);
}
function goodbye(x) {
  render(`<div class="big-icon ok">&#128075;</div><h1>Goodbye, ${esc(x.person.name.split(' ')[0])}</h1><p>You've been checked out. Have a great day!</p><div class="progress"><div id="bar"></div></div>`);
  countdown(4000);
}
function blocked() {
  K.mode = 'result'; setHint('Please see reception');
  render(`<div class="big-icon bad">&#9940;</div><h1>Please see reception</h1><p>We can't check you in automatically. A member of staff will assist you.</p><div class="progress"><div id="bar"></div></div>`);
  countdown(8000);
}
function countdown(ms) {
  const bar = $('#bar'); if (bar) { bar.style.transition = `width ${ms}ms linear`; requestAnimationFrame(() => bar.style.width = '100%'); }
  later(idle, ms);
}

/* ---- unknown visitor: details -> host approval ---- */
function form(uncertain) {
  clearTimers(); K.mode = 'form'; setHint('New visitor');
  const hosts = K.hosts.map(h => `<option value="${h.id}">${esc(h.name)}${h.unit ? ' - ' + esc(h.unit) : ''}</option>`).join('');
  render(`<h1>Nice to meet you</h1>
    <p>${uncertain ? "We couldn't confidently verify you, so your host will confirm. " : "We don't have you on file yet. "}Tell us who you're here to see.</p>
    <div class="field"><label>Your name</label><input id="f_name" autocomplete="off" placeholder="Full name"></div>
    <div class="row2"><div class="field"><label>Phone</label><input id="f_phone" inputmode="tel" placeholder="Optional"></div>
      <div class="field"><label>Email</label><input id="f_email" inputmode="email" placeholder="Optional"></div></div>
    <div class="row2"><div class="field"><label>Visiting</label><select id="f_host"><option value="">Reception</option>${hosts}</select></div>
      <div class="field"><label>Purpose</label><input id="f_purpose" placeholder="e.g. Meeting, delivery"></div></div>
    <div class="k-actions"><button class="btn lg" id="send">Notify host</button><button class="btn lg ghost" id="cancel">Cancel</button></div>
    <p style="font-size:12px;margin:14px 0 0;color:#7e8bc2">Your photo is shared with your host and stored for security purposes.</p>`);
  $('#f_name').focus();
  $('#cancel').onclick = idle;
  $('#send').onclick = async () => {
    const name = $('#f_name').value.trim(); if (name.length < 2) { toast('Please enter your name', true); return; }
    $('#send').disabled = true;
    try {
      const r = await api('/api/kiosk/register', { method: 'POST', body: { name, phone: $('#f_phone').value, email: $('#f_email').value,
        host_id: $('#f_host').value, purpose: $('#f_purpose').value, frames: K.frames } });
      waiting(r, name);
    } catch (e) { toast(e.message, true); $('#send').disabled = false; }
  };
  later(idle, 120000);
}

function waiting(r, name) {
  clearTimers(); K.mode = 'waiting'; setHint('Waiting for approval');
  render(`<div class="big-icon wait pulse">&#9203;</div><h1>Hang tight, ${esc(name.split(' ')[0])}</h1>
    <p>We've notified your host. This usually takes a minute.</p><div class="k-actions"><button class="btn ghost" id="cancel">Cancel</button></div>`);
  $('#cancel').onclick = idle;
  const t0 = Date.now(), limit = (r.timeout_min || 10) * 60000;
  (async function poll() {
    if (K.mode !== 'waiting') return;
    try {
      const s = await api(`/api/kiosk/status/${r.visit_id}?t=${r.token}`);
      if (s.status === 'approved') return approved(name);
      if (s.status === 'denied') return denied();
      if (s.status === 'expired' || Date.now() - t0 > limit) return expired();
    } catch (_) {}
    setTimeout(poll, 2000);
  })();
}
function approved(name) { K.mode = 'result'; setHint('Approved', 'ready');
  render(`<div class="big-icon ok">&#10003;</div><h1>You're approved!</h1><p>Welcome, ${esc(name.split(' ')[0])}. Please proceed in.</p><div class="progress"><div id="bar"></div></div>`); countdown(8000); }
function denied() { K.mode = 'result'; setHint('Not approved');
  render(`<div class="big-icon bad">&#10005;</div><h1>Sorry, not right now</h1><p>Your host couldn't approve this visit. Please speak with reception.</p><div class="progress"><div id="bar"></div></div>`); countdown(9000); }
function expired() { K.mode = 'result'; setHint('No response');
  render(`<div class="big-icon wait">&#8987;</div><h1>No response yet</h1><p>Your host hasn't replied. Please see reception for help.</p><div class="progress"><div id="bar"></div></div>`); countdown(9000); }

(async function init() {
  try { const c = await api('/api/kiosk/config'); K.hosts = c.hosts; } catch (_) {}
  try { await startCamera(video); } catch (e) {
    setHint('Camera unavailable');
    render(`<div class="big-icon bad">&#128247;</div><h1>Camera unavailable</h1><p>${esc(e.message)}. Allow camera access and reload the page.</p>`); return; }
  idle(); loop();
})();
