'use strict';

const criteriaForm = document.querySelector('#filters');
const criteriaKeys = ['priceMax', 'areaMin', 'numberOfRoomsMin'];
const storageKey = 'boligblik.filters.v1';
const viewNames = {all: 'Alle villaer', new: 'Nye siden i går', drop: 'Prisfald på 7 dage'};
const currency = new Intl.NumberFormat('da-DK', {maximumFractionDigits: 0});
const updatedFormat = new Intl.DateTimeFormat('da-DK', {
  timeZone: 'Europe/Copenhagen', day: 'numeric', month: 'long', year: 'numeric', hour: '2-digit', minute: '2-digit',
});
const dayFormat = new Intl.DateTimeFormat('sv-SE', {
  timeZone: 'Europe/Copenhagen', year: 'numeric', month: '2-digit', day: '2-digit',
});
const dateLabelFormat = new Intl.DateTimeFormat('da-DK', {timeZone:'UTC', day:'numeric', month:'long'});
let data = null;
let view = 'all';
let criteria = {};

function dayOffset(day, offset) {
  const stamp = Date.parse(day + 'T12:00:00Z') + offset * 86400000;
  return new Date(stamp).toISOString().slice(0, 10);
}

function isRecentNew(home, today) {
  return !home.isBaseline && home.firstSeen >= dayOffset(today, -1) && home.firstSeen <= today;
}

function isRecentDrop(home, today) {
  const change = home.priceChange;
  return change && change.date >= dayOffset(today, -6) && change.date <= today &&
    home.price !== null && change.previous > home.price;
}

function meetsCriteria(home) {
  return (!criteria.priceMax || (home.price !== null && home.price <= criteria.priceMax)) &&
    (!criteria.areaMin || home.area >= criteria.areaMin) &&
    (!criteria.numberOfRoomsMin || home.rooms >= criteria.numberOfRoomsMin);
}

function displayFilterSummary() {
  const labels = [];
  if (criteria.priceMax) labels.push('maks. ' + currency.format(criteria.priceMax) + ' kr.');
  if (criteria.areaMin) labels.push('min. ' + criteria.areaMin + ' m²');
  if (criteria.numberOfRoomsMin) labels.push('min. ' + criteria.numberOfRoomsMin + ' vær.');
  document.querySelector('#filter-summary').textContent = labels.length ? ' · ' + labels.join(' · ') : '';
}

function saveCriteria() {
  criteria = {};
  for (const key of criteriaKeys) {
    const value = criteriaForm.elements[key].valueAsNumber;
    if (Number.isSafeInteger(value) && value > 0) criteria[key] = value;
  }
  try { localStorage.setItem(storageKey, JSON.stringify(criteria)); } catch { /* Browser storage is optional. */ }
  displayFilterSummary();
  render();
}

function restoreCriteria() {
  try {
    const saved = JSON.parse(localStorage.getItem(storageKey) || '{}');
    for (const key of criteriaKeys) {
      if (Number.isSafeInteger(saved[key]) && saved[key] > 0) {
        criteria[key] = saved[key];
        criteriaForm.elements[key].value = saved[key];
      }
    }
  } catch { /* Use open search when browser storage is unavailable. */ }
  displayFilterSummary();
}

function sourceLink(value) {
  if (!value) return null;
  try {
    const url = new URL(value);
    if (url.protocol === 'https:' && ['www.nybolig.dk', 'nybolig.dk'].includes(url.hostname)) return url.href;
  } catch { /* Some published records have no link yet. */ }
  return null;
}

function renderHome(home, today) {
  const card = document.querySelector('#home').content.cloneNode(true);
  const town = [home.postalCode, home.city].filter(Boolean).join(' ');
  card.querySelector('.town').textContent = town;
  const suffix = ', ' + town;
  const street = town && home.address.endsWith(suffix) ? home.address.slice(0, -suffix.length) : home.address;
  card.querySelector('h3').textContent = street || 'Adresse ikke oplyst';
  const specs = ['Villa'];
  if (home.area !== null) specs.push(currency.format(home.area) + ' m²');
  if (home.rooms !== null) specs.push(currency.format(home.rooms) + (home.rooms === 1 ? ' værelse' : ' værelser'));
  card.querySelector('.specs').textContent = specs.join(' · ');
  card.querySelector('.price').textContent = home.price === null ? 'Pris ikke oplyst' : currency.format(home.price) + ' kr.';
  if (isRecentNew(home, today)) {
    const chip = card.querySelector('.chip');
    chip.hidden = false;
    chip.textContent = home.firstSeen === today ? 'Først set i dag' : 'Først set i går';
  }
  if (isRecentDrop(home, today)) {
    const drop = card.querySelector('.drop');
    drop.hidden = false;
    drop.textContent = 'Nedsat ' + currency.format(home.priceChange.previous - home.price) + ' kr.';
  }
  const anchor = card.querySelector('a');
  const href = sourceLink(home.url);
  if (href) {
    anchor.href = href;
    anchor.setAttribute('aria-label', 'Se ' + home.address + ' hos Nybolig');
  } else {
    anchor.replaceWith('Annoncelink afventes');
  }
  return card;
}

function render() {
  if (!data) return;
  const today = dayFormat.format(new Date());
  const all = data.listings.filter(meetsCriteria);
  const fresh = all.filter(home => isRecentNew(home, today));
  const drops = all.filter(home => isRecentDrop(home, today));
  const lists = {all, new:fresh, drop:drops};
  for (const button of document.querySelectorAll('#views button')) {
    button.setAttribute('aria-pressed', String(button.dataset.view === view));
    button.querySelector('span').textContent = lists[button.dataset.view].length;
  }
  const results = lists[view];
  results.sort((a, b) => {
    if (view === 'drop') {
      const byDate = b.priceChange.date.localeCompare(a.priceChange.date);
      if (byDate) return byDate;
    }
    const byFirstSeen = b.firstSeen.localeCompare(a.firstSeen);
    return byFirstSeen || a.address.localeCompare(b.address, 'da', {numeric:true});
  });
  document.querySelector('#list-title').textContent = viewNames[view];
  document.querySelector('#count').textContent = results.length + (results.length === 1 ? ' bolig' : ' boliger');
  const list = document.querySelector('#list');
  const cards = document.createDocumentFragment();
  for (const home of results) cards.append(renderHome(home, today));
  list.replaceChildren(cards);
  const empty = document.querySelector('#empty');
  empty.hidden = results.length > 0;
  if (view === 'new') empty.textContent = 'Ingen nye fund blandt dine match siden i går.';
  else if (view === 'drop') empty.textContent = 'Ingen registrerede prisfald blandt dine match de seneste syv dage.';
  else empty.textContent = all.length === 0 && Object.keys(criteria).length
    ? 'Ingen villaer matcher dine søgekriterier. Prøv at justere dem ovenfor.'
    : 'Nybolig har ingen aktive villaer i området i seneste opdatering.';
  const baseline = document.querySelector('#baseline');
  const isFirstDay = data.baselineDate === dayFormat.format(new Date(data.updatedAt));
  baseline.hidden = !isFirstDay;
  if (isFirstDay) {
    const date = dateLabelFormat.format(new Date(data.baselineDate + 'T12:00:00Z'));
    baseline.textContent = 'Første optælling ' + date + '. Ændringer vises fra næste opdatering.';
  }
}

async function load() {
  try {
    const response = await fetch('./boliger.json', {cache:'no-store'});
    if (!response.ok) throw Error('Fetch failed');
    const result = await response.json();
    const timestamp = Date.parse(result.updatedAt);
    if (result.schemaVersion !== 1 || !Array.isArray(result.listings) || !Number.isFinite(timestamp)) {
      throw Error('Data unavailable');
    }
    data = result;
    document.querySelector('#freshness').textContent = 'Senest hentet ' + updatedFormat.format(new Date(timestamp));
    if (Date.now() - timestamp > 30 * 3600000) {
      const warning = document.querySelector('#warning');
      warning.hidden = false;
      warning.textContent = 'Opdateringen er forsinket. Listen viser senest hentede boliger og priser.';
    }
    render();
  } catch {
    document.querySelector('#freshness').textContent = 'Boligdata kunne ikke indlæses.';
    document.querySelector('#list').replaceChildren();
    const empty = document.querySelector('#empty');
    empty.hidden = false;
    empty.textContent = 'Prøv at genindlæse siden om lidt.';
  }
}
criteriaForm.addEventListener('submit', event => {
  event.preventDefault();
  saveCriteria();
  document.querySelector('#filter-panel').open = false;
});
criteriaForm.addEventListener('reset', event => {
  event.preventDefault();
  for (const key of criteriaKeys) criteriaForm.elements[key].value = '';
  saveCriteria();
});
for (const button of document.querySelectorAll('#views button')) {
  button.addEventListener('click', () => {
    view = button.dataset.view;
    render();
  });
}
restoreCriteria();
load();
