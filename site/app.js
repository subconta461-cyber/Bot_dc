'use strict';
const $ = id => document.getElementById(id);
const key = document.querySelector('meta[name="uminida-key"]').content;
let config;
try { config = JSON.parse(localStorage.getItem('uminida.channels') || '{}'); } catch { config = {}; }
let state = null, busy = false, requesting = false, toastTimer, failed = false;
let currentId = null, displayedElapsed = 0, stateTime = 0, pendingConfirm = null;
const time = seconds => { seconds = Math.max(0, Math.floor(Number(seconds) || 0)); return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}`; };
const icon = name => { const el = document.createElementNS('http://www.w3.org/2000/svg','svg'); const use = document.createElementNS(el.namespaceURI,'use'); use.setAttribute('href', '#i-' + name); el.append(use); return el; };
const safeImage = url => { try { return new URL(url).protocol === 'https:' ? url : 'moon.svg'; } catch { return 'moon.svg'; } };
function toast(message, error = false) { clearTimeout(toastTimer); $('toast').textContent = message; $('toast').className = 'toast visible' + (error ? ' error' : ''); toastTimer = setTimeout(() => $('toast').classList.remove('visible'), error ? 6500 : 3500); }
function selectedGuild() { return state?.guilds.find(g => g.id === config.guild); }
function storeConfig() { localStorage.setItem('uminida.channels', JSON.stringify(config)); }
function options(select, items, selected, placeholder) {
  select.replaceChildren();
  const first = new Option(placeholder, ''); first.disabled = true; select.add(first);
  items.forEach(item => select.add(new Option(item.name, item.id)));
  select.value = items.some(item => item.id === selected) ? selected : '';
}
function fillChannels(guildId) {
  const guild = state?.guilds.find(g => g.id === guildId);
  const same = guildId === config.guild;
  options($('voice-select'), guild?.voice_channels || [], same ? (state?.voice_channel_id || config.voice) : '', 'Escolha um canal de voz');
  options($('text-select'), guild?.text_channels || [], same ? (state?.text_channel_id || config.text) : '', 'Escolha um canal de mensagens');
}
function openSettings() {
  options($('guild-select'), state?.guilds || [], config.guild, state?.guilds.length ? 'Escolha um servidor' : 'Aguardando conexão ao Discord');
  fillChannels($('guild-select').value);
  $('settings-dialog').showModal();
}
function showConfirm(title, description, action) {
  $('confirm-title').textContent = title; $('confirm-description').textContent = description;
  pendingConfirm = action; $('confirm-dialog').showModal();
}
function progress() {
  const duration = state?.current?.duration || 0;
  let elapsed = displayedElapsed + (state?.playing && !failed ? (performance.now() - stateTime) / 1000 : 0);
  if (duration) elapsed = Math.min(duration, elapsed);
  if (!state?.current) elapsed = 0;
  $('elapsed').textContent = time(elapsed);
  $('duration').textContent = duration ? time(duration) : (state?.current ? 'AO VIVO' : '0:00');
  const percentage = duration ? Math.min(100, elapsed / duration * 100) : 0;
  $('progress-fill').style.width = percentage + '%';
  document.querySelector('[role="progressbar"]').setAttribute('aria-valuenow', Math.round(percentage));
}
let renderedQueue = '';
function renderQueue(queue) {
  const signature = JSON.stringify(queue);
  if (signature === renderedQueue) return;
  renderedQueue = signature;
  $('queue-list').replaceChildren();
  if (!queue.length) {
    const empty = document.createElement('div'); empty.className = 'empty-queue';
    const badge = document.createElement('span'); badge.className = 'empty-icon'; badge.append(icon('list'));
    const star = document.createElement('span'); star.textContent = '✦'; badge.append(star);
    const heading = document.createElement('h3'); heading.textContent = 'Uma fila cheia de possibilidades';
    const description = document.createElement('p'); description.append('Adicione suas favoritas.', document.createElement('br'), 'A próxima pode ser a sua.');
    const button = document.createElement('button'); button.className = 'text-button'; button.textContent = 'Encontrar uma música ↗'; button.onclick = () => $('query').focus();
    empty.append(badge, heading, description, button); $('queue-list').append(empty);
  }
  queue.forEach((track, index) => {
    const row = document.createElement('div'); row.className = 'queue-row';
    const position = document.createElement('span'); position.className = 'position'; position.textContent = String(index + 1).padStart(2, '0');
    const image = document.createElement('img'); image.src = safeImage(track.thumbnail); image.alt = ''; image.loading = 'lazy'; image.onerror = () => { image.onerror = null; image.src = 'moon.svg'; };
    const info = document.createElement('div'); info.className = 'queue-track';
    const title = document.createElement('strong'); title.textContent = track.title; title.title = track.title;
    const subtitle = document.createElement('small'); subtitle.textContent = `${time(track.duration)} • ${track.requester || 'Discord'}`;
    info.append(title, subtitle);
    const actions = document.createElement('div'); actions.className = 'queue-actions';
    [['up','Mover para cima','move',-1],['down','Mover para baixo','move',1],['close','Remover da fila','remove',0]].forEach(([symbol,label,action,direction]) => {
      const button = document.createElement('button'); button.className = 'icon-button'; button.title = label; button.setAttribute('aria-label', label + ': ' + track.title); button.append(icon(symbol));
      button.disabled = (direction === -1 && index === 0) || (direction === 1 && index === queue.length - 1);
      button.onclick = () => perform(action, {track: track.id, direction}, button);
      actions.append(button);
    });
    row.append(position, image, info, actions); $('queue-list').append(row);
  });
}
function render() {
  if (!state) return;
  const guild = selectedGuild(), current = state.current, online = state.ready && !failed;
  document.body.classList.toggle('is-offline', !online);
  document.body.classList.toggle('playing', online && state.playing);
  $('status-dot').classList.toggle('offline-dot', !online);
  $('connection-label').textContent = !online ? 'Bot desconectado' : (state.connected ? 'Conectada ao Discord' : 'Uminida está online');
  $('voice-label').textContent = state.connected ? state.voice_channel : (guild ? 'Adicione uma música para entrar no canal' : 'Escolha seu servidor para começar');
  $('sync-label').textContent = online ? 'Sincronização automática' : 'Tentando reconectar…';
  $('server-name').textContent = guild?.name || 'Escolher servidor';
  $('server-initial').textContent = (guild?.name || 'U')[0].toUpperCase();
  $('side-channel').textContent = state.connected ? state.voice_channel : 'Pronto para ouvir?';
  $('side-status').textContent = state.connected ? 'Seu som está no Discord. 💜' : 'Escolha uma música para começar.';
  if (state.bot?.avatar) $('avatar').src = safeImage(state.bot.avatar);
  $('play-status').textContent = current ? (state.paused ? 'PAUSADA' : state.playing ? 'AO VIVO NO DISCORD' : 'PREPARANDO') : 'EM ESPERA';
  $('player-heading').textContent = current ? 'TOCANDO AGORA' : 'SEU PLAYER';
  $('track-kicker').textContent = current ? (state.paused ? 'UM RESPIRO NA TRILHA SONORA' : 'A TRILHA SONORA DE AGORA') : 'TEM ESPAÇO PARA O SEU SOM';
  $('track-title').textContent = current?.title || 'Bora ouvir alguma coisa?';
  $('track-title').title = current?.title || '';
  $('track-requester').textContent = current ? `Pedido por ${current.requester || 'alguém do servidor'}` : 'Escolha uma música e fique à vontade. 💜';
  const nextId = current?.id || null;
  if (currentId !== nextId || $('cover').dataset.thumb !== (current?.thumbnail || '')) {
    currentId = nextId; $('cover').src = current?.thumbnail ? safeImage(current.thumbnail) : 'moon.svg';
    $('cover').dataset.thumb = current?.thumbnail || '';
    $('cover').alt = current ? `Capa de ${current.title}` : 'Ilustração de uma lua lilás e estrelas';
  }
  $('play-pause').replaceChildren(icon(state.playing ? 'pause' : 'play'));
  $('play-pause').setAttribute('aria-label', state.playing ? 'Pausar música' : 'Continuar música');
  ['play-pause','skip'].forEach(id => $(id).disabled = !(online && (state.playing || state.paused)));
  ['repeat','volume','stop'].forEach(id => $(id).disabled = !(online && state.connected));
  $('repeat').setAttribute('aria-pressed', String(state.repeat));
  $('repeat-label').textContent = state.repeat ? 'Repetição ligada 🔁' : 'Repetição desligada';
  if (document.activeElement !== $('volume')) { $('volume').value = state.volume; $('volume-value').textContent = state.volume + '%'; }
  const queue = state.queue || [];
  $('queue-count').textContent = queue.length; $('nav-count').textContent = queue.length;
  $('clear-queue').disabled = !online || !queue.length;
  $('queue-duration').textContent = queue.length ? `${queue.length} ${queue.length === 1 ? 'música' : 'músicas'} • ${time(queue.reduce((sum,t) => sum + t.duration, 0))}` : 'Nenhuma música na fila';
  renderQueue(queue); progress();
}
async function sync() {
  if (requesting) return; requesting = true;
  try {
    const selected = config.guild || '';
    const response = await fetch('/api/state?guild=' + encodeURIComponent(selected), {cache:'no-store', signal:AbortSignal.timeout(6000)});
    if (!response.ok) throw Error('Falha ao conectar');
    const data = await response.json();
    if (selected !== (config.guild || '')) return;
    state = data; failed = false; displayedElapsed = state.elapsed || 0; stateTime = performance.now();
    if (config.guild && !selectedGuild() && data.ready) { config = {}; storeConfig(); }
    render();
  } catch {
    failed = true;
    if (state) render();
    else {
      $('connection-label').textContent = 'Aguardando a Uminida'; $('voice-label').textContent = 'Abra o bot para conectar o painel';
      $('status-dot').classList.add('offline-dot'); $('sync-label').textContent = 'Tentando reconectar…';
    }
  } finally { requesting = false; }
}
async function perform(action, extra = {}, button = null) {
  if (busy) return false;
  if (!config.guild) { openSettings(); return false; }
  busy = true; if (button) button.disabled = true;
  try {
    const response = await fetch('/api/action', {method:'POST', headers:{'Content-Type':'application/json','X-Uminida-Key':key}, body:JSON.stringify({...config, ...extra, action}), signal:AbortSignal.timeout(action === 'add' ? 65000 : 18000)});
    const result = await response.json();
    if (!response.ok) throw Error(result.error || 'Não consegui concluir. Tente novamente.');
    if (action === 'add') $('query').value = '';
    toast(result.message || 'Prontinho 💜');
    await sync(); return true;
  } catch(error) {
    toast(error.name === 'TimeoutError' ? 'A resposta está demorando. Confira a fila antes de tentar de novo.' : error.message === 'Failed to fetch' ? 'Não consegui falar com o bot. Confira se ele está aberto.' : error.message, true);
    await sync(); return false;
  } finally { busy = false; if (button) button.disabled = false; render(); }
}
$('guild-select').onchange = () => fillChannels($('guild-select').value);
$('settings-form').onsubmit = async event => {
  event.preventDefault(); config = {guild:$('guild-select').value,voice:$('voice-select').value,text:$('text-select').value}; storeConfig();
  currentId = null; renderedQueue = ''; $('settings-dialog').close(); await sync(); toast('Seu cantinho está escolhido 💜');
};
$('add-form').onsubmit = async event => {
  event.preventDefault(); if (busy) return;
  if (!config.guild || !config.voice || !config.text) { openSettings(); return; }
  if (!selectedGuild()) { openSettings(); return; }
  const query = $('query').value.trim(); if (!query) return;
  $('add-button').querySelector('span').textContent = 'Buscando sua música…'; document.body.classList.add('loading');
  await perform('add', {query, voice: state?.voice_channel_id || config.voice, text: state?.text_channel_id || config.text}, $('add-button'));
  $('add-button').querySelector('span').textContent = 'Adicionar à fila'; document.body.classList.remove('loading');
};
$('play-pause').onclick = () => perform('pause', {}, $('play-pause'));
$('skip').onclick = () => perform('skip', {}, $('skip'));
$('repeat').onclick = () => perform('repeat', {}, $('repeat'));
$('volume').oninput = () => $('volume-value').textContent = $('volume').value + '%';
$('volume').onchange = () => perform('volume', {value:Number($('volume').value)});
$('clear-queue').onclick = () => showConfirm('Limpar a fila?', 'As músicas que ainda não começaram serão removidas. A música atual continua tocando.', () => perform('clear'));
$('stop').onclick = () => showConfirm('Encerrar por agora?', 'Vou parar a música, limpar a fila e sair do canal de voz.', () => perform('stop'));
$('confirm-ok').onclick = () => { $('confirm-dialog').close(); pendingConfirm?.(); pendingConfirm = null; };
$('confirm-cancel').onclick = () => { $('confirm-dialog').close(); pendingConfirm = null; };
['open-settings','side-settings'].forEach(id => $(id).onclick = openSettings);
['profile','nav-help'].forEach(id => $(id).onclick = () => $('help-dialog').showModal());
$('nav-player').onclick = () => { window.scrollTo({top:0,behavior:'smooth'}); $('nav-player').classList.add('active'); $('nav-queue').classList.remove('active'); };
$('nav-queue').onclick = () => { $('queue-section').scrollIntoView({behavior:'smooth',block:'start'}); $('nav-queue').classList.add('active'); $('nav-player').classList.remove('active'); };
$('empty-add').onclick = () => $('query').focus();
document.querySelectorAll('[data-close]').forEach(button => button.onclick = () => $(button.dataset.close).close());
document.querySelectorAll('dialog').forEach(dialog => dialog.addEventListener('click', event => { if(event.target === dialog) { const rect = dialog.getBoundingClientRect(); if(event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) dialog.close(); } }));
$('cover').onerror = () => { if (!$('cover').src.endsWith('/moon.svg')) $('cover').src = 'moon.svg'; };
document.addEventListener('visibilitychange', () => { if (!document.hidden) sync(); });
sync(); setInterval(() => { if (!document.hidden) sync(); }, 2000); setInterval(progress, 1000);
