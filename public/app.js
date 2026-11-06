'use strict';

const criteriaForm = document.querySelector('#filters');
const criteriaKeys = ['priceMin', 'priceMax', 'areaMin', 'numberOfRoomsMin'];
const storageKey = 'boligblik.filters.v1';
const visitKey = 'boligblik.visits.v1';
const savedKey = 'boligblik.saved.v1';
const viewNames = {all: 'Alle villaer', new: 'Nye siden dit sidste besøg', saved: 'Gemte villaer'};
const currency = new Intl.NumberFormat('da-DK', {maximumFractionDigits: 0});
const updatedFormat = new Intl.DateTimeFormat('da-DK', {
  timeZone: 'Europe/Copenhagen', day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit',
});
const clockFormat = new Intl.DateTimeFormat('en-GB', {timeZone:'Europe/Copenhagen', hour:'2-digit', minute:'2-digit', hourCycle:'h23'});
const dayFormat = new Intl.DateTimeFormat('sv-SE', {
  timeZone: 'Europe/Copenhagen', year: 'numeric', month: '2-digit', day: '2-digit',
});
let data = null;
let visit = null;
let newAddresses = new Set();
let loading = false;
let view = 'all';
const pageSize = 24;
let shown = pageSize;
let criteria = {};
let saved = new Set();
let savedInTabOnly = false;
let searchWords = [];

function readSaved() {
  try {
    const keys = JSON.parse(localStorage.getItem(savedKey) || '[]');
    if (Array.isArray(keys)) saved = new Set(keys.filter(key => typeof key === 'string'));
  } catch { /* Keep bookmarks available in this tab when storage is blocked. */ }
}

function compareVisit(timestamp, addresses) {
  let previous = visit;
  try {
    const saved = JSON.parse(localStorage.getItem(visitKey));
    if (saved && Number.isFinite(saved.timestamp) && Array.isArray(saved.addresses) &&
        Array.isArray(saved.added) && (!previous || saved.timestamp >= previous.timestamp)) previous = saved;
  } catch { /* Fall back to the visit in this tab. */ }
  // Reopening the same edition keeps its batch of new homes, even in another tab.
  if (previous && timestamp <= previous.timestamp) return previous;
  const known = new Set(previous?.addresses);
  return {timestamp, addresses, added:previous ? addresses.filter(key => !known.has(key)) : [], first:!previous};
}

function meetsCriteria(home) {
  const {priceMin, priceMax} = criteria;
  const inBudget = (!priceMin && !priceMax) || (Number.isFinite(home.price) &&
    (!priceMin || home.price >= priceMin) && (!priceMax || home.price <= priceMax));
  const address = [home.address, home.city, home.postalCode].join(' ').toLocaleLowerCase('da');
  return inBudget && searchWords.every(word => address.includes(word)) &&
    (!criteria.areaMin || home.area >= criteria.areaMin) &&
    (!criteria.numberOfRoomsMin || home.rooms >= criteria.numberOfRoomsMin);
}

function displayFilterSummary() {
  const labels = [];
  if (criteria.priceMin && criteria.priceMax) {
    labels.push(currency.format(criteria.priceMin) + '–' + currency.format(criteria.priceMax) + ' kr.');
  } else if (criteria.priceMin) labels.push('min. ' + currency.format(criteria.priceMin) + ' kr.');
  else if (criteria.priceMax) labels.push('maks. ' + currency.format(criteria.priceMax) + ' kr.');
  if (criteria.areaMin) labels.push('min. ' + criteria.areaMin + ' m²');
  if (criteria.numberOfRoomsMin) labels.push('min. ' + criteria.numberOfRoomsMin + ' vær.');
  const summary = document.querySelector('#filter-summary');
  summary.hidden = !labels.length;
  summary.textContent = labels.join(' · ');
  document.querySelector('.filter-label').textContent = labels.length ? 'Filtre (' + Object.keys(criteria).length + ')' : 'Filtre';
}

function saveCriteria() {
  criteria = {};
  for (const key of criteriaKeys) {
    const value = criteriaForm.elements[key].valueAsNumber;
    if (Number.isSafeInteger(value) && value > 0) criteria[key] = value;
  }
  try { localStorage.setItem(storageKey, JSON.stringify(criteria)); } catch { /* Browser storage is optional. */ }
  displayFilterSummary();
  shown = pageSize;
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

const brokerHosts = {
  Nybolig: ['nybolig.dk', 'www.nybolig.dk'],
  home: ['home.dk'], danbolig: ['danbolig.dk'], LokalBolig: ['www.lokalbolig.dk'],
  Nordkystmæglerne: ['nordkystmaeglerne.dk'], Estate: ['www.estate.dk'], 'Ivan Eltoft Nielsen': ['eltoftnielsen.dk'],
  '&LIVING': ['andliving.dk'], LILIENHOFF: ['www.lilienhoff.dk'],
};
const previewHosts = {home: 'home.mindworking.eu', danbolig: 'danbolig.mindworking.eu', LokalBolig: 'lokalbolig.io',
  Nordkystmæglerne:'mediaserver.prod.realequity.dk', Estate:'estate.mindworking.eu', 'Ivan Eltoft Nielsen':'eltoftnielsen.dk',
  '&LIVING':'andliving.mindworking.eu', LILIENHOFF:'lilienhoff.mindworking.eu'};

function sourceLink(value, source) {
  if (!value) return null;
  try {
    const url = new URL(value);
    if (url.protocol === 'https:' && brokerHosts[source]?.includes(url.hostname)) return url.href;
  } catch { /* Some published records have no link yet. */ }
  return null;
}

const filterPanel = document.querySelector('#filter-panel');
const viewer = document.querySelector('#viewer');
const viewerStage = viewer.querySelector('.viewer-stage');
const viewerPhoto = document.querySelector('#viewer-photo');
const zoomButton = document.querySelector('#zoom');
let openPhoto = null;

function setZoom(active) {
  if (active && (!viewerPhoto.naturalWidth || !viewerPhoto.naturalHeight)) return;
  viewerStage.classList.toggle('zoomed', active);
  zoomButton.setAttribute('aria-pressed', String(active));
  zoomButton.querySelector('span').textContent = active ? 'Zoom ud' : 'Zoom ind';
  if (!active) {
    viewerStage.scrollTo(0, 0);
    return;
  }
  const fit = Math.min(viewerStage.clientWidth / viewerPhoto.naturalWidth, viewerStage.clientHeight / viewerPhoto.naturalHeight);
  viewerStage.style.setProperty('--zoom-width', viewerPhoto.naturalWidth * fit * 2.5 + 'px');
  viewerStage.style.setProperty('--zoom-height', viewerPhoto.naturalHeight * fit * 2.5 + 'px');
  viewerStage.scrollTo((viewerStage.scrollWidth - viewerStage.clientWidth) / 2, (viewerStage.scrollHeight - viewerStage.clientHeight) / 2);
}

function showPhoto(home, cardImage, fullUrl) {
  openPhoto = cardImage;
  document.querySelector('#viewer-title').textContent = home.address;
  const facts = [];
  if (home.price != null) facts.push(currency.format(home.price) + ' kr.');
  if (home.area != null) facts.push(home.area + ' m²');
  if (home.rooms != null) facts.push(home.rooms + ' vær.');
  document.querySelector('#viewer-meta').textContent = facts.join(' · ');
  viewerPhoto.alt = cardImage.alt;
  viewerPhoto.src = cardImage.currentSrc || cardImage.src;
  setZoom(false);
  viewer.showModal();
  if (fullUrl) {
    // Keep the already loaded cover visible while the detail image downloads.
    const full = new Image();
    full.onload = () => {
      if (viewer.open && openPhoto === cardImage) viewerPhoto.src = full.src;
    };
    full.src = fullUrl;
  }
}

const cardWidths = [360, 480, 640, 768, 900, 1080, 1440, 1920];
const cardSizes = '(max-width: 360px) calc(100vw - 24px), (max-width: 699px) calc(100vw - 36px), (max-width: 1049px) calc((100vw - 85px) / 2), (max-width: 1312px) calc((100vw - 106px) / 3), 402px';

function addPhoto(card, home, index) {
  const preview = home.thumbnailUrl;
  if (home.source !== 'Nybolig') {
    const href = sourceLink(home.url, home.source);
    if (!href || !preview?.startsWith('https://' + previewHosts[home.source] + '/')) return;
    const image = new Image();
    const link = document.createElement('a');
    const missing = card.querySelector('.missing');
    link.className = 'photo-link';
    link.href = href;
    link.setAttribute('aria-label', 'Se billeder af ' + home.address + ' hos ' + home.source);
    image.alt = 'Annoncefoto fra ' + home.source + ' af ' + home.address;
    image.loading = index < 2 ? 'eager' : 'lazy';
    if (!index) image.fetchPriority = 'high';
    image.addEventListener('error', () => { link.remove(); missing.hidden = false; }, {once:true});
    missing.hidden = true;
    link.append(image);
    card.querySelector('.photo').prepend(link);
    image.src = preview;
    return;
  }
  if (!preview?.startsWith('https://nybolig.mindworking.eu/api/')) return;
  const image = new Image();
  const enlarge = card.querySelector('.enlarge');
  const missing = card.querySelector('.missing');
  const asset = new URL(preview);
  const canResize = asset.pathname.includes('/imageSize/Preview/');
  asset.pathname = asset.pathname.replace('/imageSize/Preview/', '/imageSize/Assets/');
  function resized(width) {
    asset.searchParams.set('jq', '84');
    asset.searchParams.set('w', String(width));
    return asset.href;
  }
  const fullUrl = canResize ? resized(2100) : null;
  image.alt = 'Annoncefoto af ' + home.address;
  image.loading = index < 2 ? 'eager' : 'lazy';
  if (index === 0) image.fetchPriority = 'high';
  image.addEventListener('load', () => { enlarge.hidden = false; });
  image.addEventListener('error', () => {
    if (image.hasAttribute('srcset')) {
      image.removeAttribute('srcset');
      image.removeAttribute('sizes');
      image.src = preview;
    } else {
      image.remove();
      enlarge.hidden = true;
      missing.hidden = false;
    }
  });
  enlarge.setAttribute('aria-label', 'Forstør foto af ' + home.address);
  enlarge.addEventListener('click', () => showPhoto(home, image, fullUrl));
  missing.hidden = true;
  if (canResize) {
    image.sizes = cardSizes;
    image.srcset = cardWidths.map(width => resized(width) + ' ' + width + 'w').join(', ');
  }
  image.src = canResize ? resized(768) : preview;
  card.querySelector('.photo').prepend(image);
}

function renderHome(home, index) {
  const card = document.querySelector('#home').content.cloneNode(true);
  addPhoto(card, home, index);
  const town = [home.postalCode, home.city].filter(Boolean).join(' ');
  card.querySelector('.town').textContent = town + ' · ' + home.distanceKm.toLocaleString('da-DK', {maximumFractionDigits:1}) + ' km · ' + home.source;
  const last = data.sources?.[home.source];
  if (last?.status === 'delayed' && last.updatedAt) {
    card.querySelector('.town').textContent += ' · Sidst set ' + updatedFormat.format(new Date(last.updatedAt));
  }
  const suffix = ', ' + town;
  const street = town && home.address.endsWith(suffix) ? home.address.slice(0, -suffix.length) : home.address;
  card.querySelector('h3').textContent = street || 'Adresse ikke oplyst';
  const area = card.querySelector('.area');
  const rooms = card.querySelector('.rooms');
  area.hidden = home.area == null;
  rooms.hidden = home.rooms == null;
  if (!area.hidden) area.querySelector('span').textContent = currency.format(home.area) + ' m²';
  if (!rooms.hidden) rooms.querySelector('span').textContent = home.rooms + ' vær.';
  const key = home.addressKey || home.id;
  const bookmark = card.querySelector('.bookmark');
  bookmark.dataset.key = key;
  bookmark.setAttribute('aria-label', 'Gem ' + home.address);
  bookmark.setAttribute('aria-pressed', String(saved.has(key)));
  card.querySelector('.price').textContent = home.price === null ? 'Pris ikke oplyst' : currency.format(home.price) + ' kr.';
  if (newAddresses.has(home.addressKey || home.id)) {
    const chip = card.querySelector('.chip');
    chip.hidden = false;
    chip.textContent = 'Ny siden sidst';
  }
  const anchor = card.querySelector('.listing-link');
  const href = sourceLink(home.url, home.source);
  if (href) {
    anchor.href = href;
    anchor.setAttribute('aria-label', 'Se ' + home.address + ' hos ' + home.source);
  } else {
    const pending = document.createElement('span');
    pending.className = 'link-pending';
    pending.textContent = 'Annoncelink afventes';
    anchor.replaceWith(pending);
  }
  return card;
}

function render(append = false) {
  if (!data) return;
  const all = data.listings.filter(meetsCriteria);
  const fresh = all.filter(home => newAddresses.has(home.addressKey || home.id));
  const lists = {all, new:fresh, saved:all.filter(home => saved.has(home.addressKey || home.id))};
  for (const button of document.querySelectorAll('#views button')) {
    button.setAttribute('aria-pressed', String(button.dataset.view === view));
    button.querySelector('span:last-child').textContent = lists[button.dataset.view].length;
  }
  const results = lists[view];
  results.sort((a, b) => {
    const byFirstSeen = b.firstSeen.localeCompare(a.firstSeen);
    return Number(a.isBaseline) - Number(b.isBaseline) || byFirstSeen || a.distanceKm - b.distanceKm || a.address.localeCompare(b.address, 'da', {numeric:true});
  });
  document.querySelector('#list-title').textContent = viewNames[view];
  document.querySelector('#count').textContent = results.length + (results.length === 1 ? ' bolig' : ' boliger');
  const list = document.querySelector('#list');
  const cards = document.createDocumentFragment();
  const start = append ? list.childElementCount : 0;
  results.slice(start, shown).forEach((home, i) => cards.append(renderHome(home, start + i)));
  if (append) list.append(cards); else list.replaceChildren(cards);
  const more = document.querySelector('#more');
  more.hidden = shown >= results.length;
  more.textContent = 'Vis ' + Math.min(pageSize, Math.max(0, results.length - shown)) + ' flere villaer';
  const empty = document.querySelector('#empty');
  empty.hidden = results.length > 0;
  const narrowed = searchWords.length || Object.keys(criteria).length;
  if (view === 'saved') empty.textContent = saved.size && narrowed
    ? 'Ingen gemte villaer matcher din søgning.'
    : 'Gem en villa med bogmærket på billedet. Her finder du dine gemte villaer, så længe de er til salg.';
  else if (view === 'new') empty.textContent = visit.first
    ? 'Dit første besøg er udgangspunktet. Under Alle kan du se villaerne, der er til salg nu.'
    : (narrowed ? 'Ingen nye villaer matcher din søgning siden dit sidste besøg.'
      : 'Ingen nye villaer siden dit sidste besøg.');
  else empty.textContent = all.length === 0 && narrowed
    ? 'Ingen villaer matcher dine søgekriterier. Prøv at justere dem ovenfor.'
    : 'Ingen af de tilknyttede mæglere har aktive villaer i den seneste opdatering.';
}

function showFreshness(snapshot) {
  const timestamp = Date.parse(snapshot.updatedAt);
  const now = new Date();
  const today = dayFormat.format(now);
  const sources = Object.entries(snapshot.sources || {});
  const missingMorning = clockFormat.format(now) >= '07:45' && sources.some(([, source]) => {
    const checked = new Date(source.updatedAt);
    return !Number.isFinite(checked.getTime()) || dayFormat.format(checked) !== today ||
      clockFormat.format(checked) < '06:00';
  });
  const messages = [];
  if (missingMorning || now.getTime() - timestamp > 30 * 3600000) {
    messages.push('Morgenopdateringen er forsinket. Listen viser senest hentede boliger og priser.');
  }
  const delayed = sources.filter(([, source]) => source.status === 'delayed').map(([name]) => name);
  if (delayed.length) {
    messages.push('Seneste hentning fra ' + delayed.join(', ') + ' er forsinket. Se tidspunktet under mæglerkilder.');
  }
  document.querySelector('#freshness').textContent = 'Opdateret ' + updatedFormat.format(new Date(timestamp));
  const warning = document.querySelector('#warning');
  warning.hidden = !messages.length;
  warning.textContent = messages.join(' ');
}

async function load() {
  if (loading || document.visibilityState !== 'visible') return;
  loading = true;
  try {
    const response = await fetch('./boliger.json?format=radius12', {cache:'no-store'});
    if (!response.ok) throw Error('Fetch failed');
    const result = await response.json();
    const timestamp = Date.parse(result.updatedAt);
    if (result.schemaVersion !== 3 || result.scope?.radiusKm !== 12 || !Array.isArray(result.listings) || !Number.isFinite(timestamp)) {
      throw Error('Data unavailable');
    }
    if (document.visibilityState !== 'visible') return;
    if (data && timestamp <= Date.parse(data.updatedAt)) {
      showFreshness(data);
      return;
    }
    const sources = result.sources || {Nybolig:{status:'ok', count:result.listings.length, updatedAt:result.updatedAt}};
    const list = document.querySelector('#source-list');
    list.replaceChildren();
    for (const [name, info] of Object.entries(sources)) {
      const li = document.createElement('li');
      li.textContent = name + ' · ' + info.count + (info.count === 1 ? ' villa' : ' villaer');
      const time = document.createElement('small');
      time.textContent = info.updatedAt ? 'Hentet ' + updatedFormat.format(new Date(info.updatedAt)) : 'Afventer første opdatering';
      if (info.pendingLinks) time.textContent += ' · ' + info.pendingLinks + ' uden direkte link er udeladt';
      if (info.status === 'delayed') {
        time.textContent += ' · Forsinket';
      }
      li.append(time);
      list.append(li);
    }
    document.querySelector('#coverage-open span').textContent = Object.keys(sources).length + ' mæglerkilder';
    // Prefer current records and direct listing links when brokers share an address.
    const rank = home => (sources[home.source]?.status === 'ok' ? 2 : 0) + Number(Boolean(sourceLink(home.url, home.source)));
    const unique = new Map();
    for (const row of result.listings) {
      if (!sourceLink(row.url, row.source)) continue;
      const key = row.addressKey || row.id;
      const other = unique.get(key);
      if (!other || rank(row) > rank(other)) unique.set(key, row);
    }
    const nextVisit = compareVisit(timestamp, [...unique.keys()]);
    if (nextVisit.timestamp > timestamp) throw Error('Stale edition');
    visit = nextVisit;
    newAddresses = new Set(visit.added);
    data = {...result, listings:[...unique.values()]};
    showFreshness(data);
    render();
    try {
      localStorage.setItem(visitKey, JSON.stringify(visit));
      document.querySelector('#visit-storage').hidden = true;
    } catch {
      document.querySelector('#visit-storage').hidden = false;
    }
  } catch {
    if (data) {
      document.querySelector('#freshness').textContent = 'Viser senest indlæste liste · ' + updatedFormat.format(new Date(data.updatedAt));
      return;
    }
    document.querySelector('#freshness').textContent = 'Boligdata kunne ikke indlæses.';
    document.querySelector('#list').replaceChildren();
    const empty = document.querySelector('#empty');
    empty.hidden = false;
    empty.textContent = 'Prøv at genindlæse siden om lidt.';
  } finally {
    loading = false;
  }
}
zoomButton.addEventListener('click', () => setZoom(!viewerStage.classList.contains('zoomed')));
window.addEventListener('resize', () => {
  if (viewer.open && viewerStage.classList.contains('zoomed')) setZoom(false);
});
viewer.addEventListener('close', () => {
  openPhoto = null;
  viewerPhoto.removeAttribute('src');
});
function validatePriceRange() {
  const {priceMin, priceMax} = criteriaForm.elements;
  priceMax.setCustomValidity(priceMin.valueAsNumber > priceMax.valueAsNumber
    ? 'Maksimumsprisen skal være mindst lige så høj som minimumsprisen.' : '');
}
criteriaForm.addEventListener('input', validatePriceRange);
document.querySelector('#filter-open').addEventListener('click', () => {
  for (const key of criteriaKeys) criteriaForm.elements[key].value = criteria[key] || '';
  validatePriceRange();
  filterPanel.showModal();
});
const coverage = document.querySelector('#coverage');
document.querySelector('#coverage-open').addEventListener('click', () => coverage.showModal());
for (const dialog of [filterPanel, viewer, coverage]) {
  dialog.querySelector('[data-close]').addEventListener('click', () => dialog.close());
}
filterPanel.addEventListener('click', event => {
  if (event.target !== filterPanel) return;
  const bounds = filterPanel.getBoundingClientRect();
  if (event.clientX < bounds.left || event.clientX > bounds.right || event.clientY < bounds.top || event.clientY > bounds.bottom) filterPanel.close();
});
criteriaForm.addEventListener('submit', event => {
  event.preventDefault();
  validatePriceRange();
  if (!criteriaForm.reportValidity()) return;
  saveCriteria();
  filterPanel.close();
});
criteriaForm.addEventListener('reset', event => {
  event.preventDefault();
  for (const key of criteriaKeys) criteriaForm.elements[key].value = '';
  validatePriceRange();
  saveCriteria();
  filterPanel.close();
});
for (const button of document.querySelectorAll('#views button')) {
  button.addEventListener('click', () => {
    view = button.dataset.view;
    shown = pageSize;
    render();
    const headingTop = document.querySelector('#list-title').getBoundingClientRect().top;
    const controls = window.matchMedia('(min-width:700px)').matches ? '.browse-tools' : '#views';
    const margin = document.querySelector(controls).offsetHeight + 14;
    if (headingTop < margin) window.scrollBy(0, headingTop - margin);
  });
}
document.querySelector('#query').addEventListener('input', event => {
  const text = event.target.value.trim().toLocaleLowerCase('da');
  searchWords = text ? text.split(/\s+/) : [];
  shown = pageSize;
  render();
});
document.querySelector('#list').addEventListener('click', event => {
  const button = event.target.closest('.bookmark');
  if (!button) return;
  const key = button.dataset.key;
  const keep = button.getAttribute('aria-pressed') !== 'true';
  if (!savedInTabOnly) readSaved();
  if (keep) saved.add(key); else saved.delete(key);
  let message = keep ? 'Villaen er gemt.' : 'Villaen er fjernet fra Gemte.';
  try {
    localStorage.setItem(savedKey, JSON.stringify([...saved]));
    savedInTabOnly = false;
  } catch {
    savedInTabOnly = true;
    message += ' Ændringen gælder kun i denne fane.';
  }
  if (view === 'saved') {
    const index = [...document.querySelectorAll('#list .bookmark')].indexOf(button);
    render();
    const remaining = document.querySelectorAll('#list .bookmark');
    (remaining[Math.min(index, remaining.length - 1)] || document.querySelector('[data-view="saved"]')).focus();
  } else {
    button.setAttribute('aria-pressed', String(keep));
    document.querySelector('[data-view="saved"] span:last-child').textContent =
      data.listings.filter(home => saved.has(home.addressKey || home.id) && meetsCriteria(home)).length;
  }
  document.querySelector('#save-status').textContent = message;
});
window.addEventListener('storage', event => {
  if (event.key === savedKey || event.key === null) { readSaved(); render(); }
});
document.querySelector('#more').addEventListener('click', () => { shown += pageSize; render(true); });
readSaved();
restoreCriteria();
document.addEventListener('visibilitychange', load);
window.addEventListener('pageshow', event => { if (event.persisted) load(); });
load();
