'use strict';
const $ = id => document.getElementById(id);
let state, filter = 'all', editing = null, pendingAction = null, working = false;
function notice(message, error = false) { $('notice').textContent = message; $('notice').className = error ? 'error' : ''; $('notice').hidden = false; }
function showLogin(message) { $('login').hidden = false; $('dashboard').hidden = true; $('login-message').textContent = message; }
async function api(path, data) {
  const options = {credentials: 'same-origin', headers: {}};
  if (data !== undefined) { options.method = 'POST'; options.headers = {'Content-Type': 'application/json', 'X-CSRF-Token': state?.csrf || ''}; options.body = JSON.stringify(data); }
  const res = await fetch('/api/' + path, options);
  const body = await res.json();
  if (!res.ok) { if (res.status === 401 || res.status === 403) showLogin(body.error); throw new Error(body.error || 'Une erreur est survenue.'); }
  return body;
}
function page(settings) { $('accounts-page').hidden = settings; $('settings-page').hidden = !settings; $('nav-accounts').classList.toggle('active', !settings); $('nav-settings').classList.toggle('active', settings); }
function option(value, text) { const o = document.createElement('option'); o.value = value; o.textContent = text; return o; }
function renderSettings() {
  $('channel').replaceChildren(option('', 'Sélectionner un salon…'), ...state.channels.map(c => option(c.id, '# ' + c.name)));
  $('role').replaceChildren(option('0', 'Aucune notification'), ...state.roles.map(r => option(r.id, '@ ' + r.name)));
  $('channel').value = state.channel_id === '0' ? '' : state.channel_id; $('role').value = state.role_id;
}
function button(text, cls, action) { const b = document.createElement('button'); b.textContent = text; b.className = 'quiet ' + cls; b.addEventListener('click', action); return b; }
function render() {
  $('guild-name').textContent = state.guild; $('user-name').textContent = state.user;
  $('total').textContent = state.accounts.length; $('free').textContent = state.accounts.filter(a => !a.owner).length; $('busy').textContent = state.accounts.filter(a => a.owner).length;
  const accounts = state.accounts.filter(a => filter === 'all' || (filter === 'busy' ? !!a.owner : !a.owner));
  $('accounts').replaceChildren(); $('empty').hidden = accounts.length > 0;
  if (!accounts.length) {
    $('empty').querySelector('h2').textContent = state.accounts.length ? 'Aucun compte dans cette catégorie' : 'Votre premier compte vous attend';
    $('empty').querySelector('p').textContent = state.accounts.length ? 'Choisissez un autre filtre pour retrouver vos comptes.' : 'Choisissez votre salon dans Configuration, puis ajoutez un compte pour publier ses boutons sur Discord.';
  }
  for (const a of accounts) {
    const card = document.createElement('article'); card.className = 'card';
    const top = document.createElement('div'); top.className = 'card-top';
    const number = document.createElement('span'); number.className = 'card-number'; number.textContent = 'COMPTE ' + String(a.id).padStart(2, '0');
    const badge = document.createElement('span'); badge.className = 'badge' + (a.owner ? ' busy' : ''); badge.textContent = a.owner ? '● OCCUPÉ' : '● DISPONIBLE'; top.append(number, badge);
    const title = document.createElement('h3'); title.textContent = a.name;
    const detail = document.createElement('p'); detail.textContent = a.owner ? `Réservé par ${a.owner_name} · depuis le ${new Date(a.since * 1000).toLocaleString('fr-FR', {day:'2-digit',month:'2-digit',hour:'2-digit',minute:'2-digit'})}` : 'Prêt pour la prochaine session. Réservez ce compte depuis votre salon Discord.';
    if (a.pending) { detail.append(document.createElement('br'), 'Mise à jour Discord en attente.'); detail.className = 'pending'; }
    const actions = document.createElement('div'); actions.className = 'card-actions';
    actions.append(button('Renommer', '', () => edit(a)));
    if (a.owner) actions.append(button('Libérer', 'release', () => confirmAction('Libérer le compte ?', `Le compte « ${a.name} » redeviendra disponible et le rôle configuré sera prévenu.`, {action:'release',id:a.id})));
    if (!a.owner) actions.append(button('Retirer', 'remove', () => confirmAction('Retirer le compte ?', `Le panneau « ${a.name} » sera supprimé de Discord.`, {action:'remove',id:a.id})));
    if (a.pending) actions.append(button('Réessayer', '', () => run(() => mutate({action:'refresh',id:a.id}))));
    card.append(top, title, detail, actions); $('accounts').append(card);
  }
}
async function load(settings = false) { state = await api('state'); render(); if (settings) renderSettings(); }
async function run(fn) {
  if (working) return; working = true;
  document.querySelectorAll('button').forEach(b => b.disabled = true);
  try { await fn(); } catch (e) { notice(e.message || 'Connexion interrompue. Réessaie.', true); }
  finally { working = false; document.querySelectorAll('button').forEach(b => b.disabled = false); }
}
async function mutate(data) { const res = await api('accounts', data); await load(); notice(res.message); }
function edit(account = null) { editing = account; $('editor-title').textContent = account ? 'Renommer le compte' : 'Ajouter un compte'; $('account-name').value = account?.name || ''; $('editor').showModal(); $('account-name').focus(); }
function confirmAction(title, text, data) { pendingAction = data; $('confirm-title').textContent = title; $('confirm-text').textContent = text; $('confirm-dialog').showModal(); }
$('nav-accounts').onclick = () => page(false); $('nav-settings').onclick = () => page(true);
$('add-account').onclick = () => { if (state.channel_id === '0') { page(true); notice('Choisissez d’abord un salon et enregistrez les réglages.'); } else edit(); };
$('refresh').onclick = () => run(() => load());
$('cancel-editor').onclick = () => $('editor').close(); $('cancel-confirm').onclick = () => $('confirm-dialog').close();
$('editor-form').onsubmit = e => { e.preventDefault(); const data = {action:editing ? 'rename':'add', name:$('account-name').value.trim(), id:editing?.id}; run(async () => { await mutate(data); $('editor').close(); }); };
$('confirm-form').onsubmit = e => { e.preventDefault(); run(async () => { await mutate(pendingAction); $('confirm-dialog').close(); }); };
$('settings-form').onsubmit = e => { e.preventDefault(); run(async () => { const res = await api('settings', {channel_id:$('channel').value, role_id:$('role').value}); await load(true); notice(res.message); }); };
$('logout').onclick = () => run(async () => { await api('logout', {}); state = null; showLogin('Vous êtes déconnecté. Utilisez /panel dans Discord pour revenir.'); });
document.querySelectorAll('[data-filter]').forEach(b => b.onclick = () => { filter = b.dataset.filter; document.querySelectorAll('[data-filter]').forEach(x => x.classList.toggle('selected', x === b)); render(); });
async function start() {
  const ticket = new URLSearchParams(location.hash.slice(1)).get('ticket');
  if (location.hash) history.replaceState(null, '', location.pathname);
  try { if (ticket) await api('login', {ticket}); await load(true); $('login').hidden = true; $('dashboard').hidden = false; if (state.channel_id === '0') page(true); }
  catch (e) { showLogin(e.message || 'Connexion impossible. Réessaie depuis Discord.'); }
}
start();
setInterval(() => { if (state && !working && !document.hidden && !$('editor').open && !$('confirm-dialog').open) load().catch(e => notice(e.message, true)); }, 30000);
