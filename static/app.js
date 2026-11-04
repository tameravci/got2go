const API_BASE_URL = ''; // same-origin: works on prod, piku, localhost

// At this zoom and closer, the map shows every shop, gray pins included.
// Farther out, it shows only shops that have a code.
const DETAIL_ZOOM = 16;
// Zoom for "my location" on first open and on the Find Nearby button.
const LOCATE_ZOOM = 17;
const MAX_SIDEBAR_ITEMS = 100;

const shopsById = new Map();   // every shop the client has seen
const markerById = new Map();  // shops that have a marker on the map now
let markers;
let map;
let userLocationLayer = null;
let userLatLng = null;
let viewportRequest = null;    // AbortController for the newest viewport fetch
let visibleShopIds = [];
let searchActive = false;
let pendingOpenId = null;      // open this shop's popup after the next load
let locateFromButton = false;

function escapeHtml(unsafe) {
    return unsafe
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#039;");
}

function shopPinClass(shop) {
    if (shop.category === 'restroom') return 'pin pin-restroom';
    const hasWifi = shop.wifi_passwords && shop.wifi_passwords.length > 0;
    const hasBathroom = shop.bathroom_codes && shop.bathroom_codes.length > 0;
    if (hasWifi && hasBathroom) return 'pin pin-both';
    if (hasWifi) return 'pin pin-wifi';
    if (hasBathroom) return 'pin pin-bathroom';
    return 'pin pin-empty';
}

const PIN_SVG = '<svg viewBox="0 0 22 28" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">' +
    '<path d="M11 27.5 C 5 18, 0.5 14, 0.5 8 A 10.5 10.5 0 0 1 21.5 8 C 21.5 14, 17 18, 11 27.5 Z" />' +
    '<circle cx="11" cy="9" r="3.6" />' +
    '</svg>';

function shopIcon(shop) {
    return L.divIcon({
        className: shopPinClass(shop),
        iconSize: [22, 28],
        iconAnchor: [11, 28],
        popupAnchor: [0, -26],
        html: PIN_SVG
    });
}

// "today", "3 days ago" ... for an ISO timestamp from the API.
function timeAgo(isoString) {
    if (!isoString) return '';
    const days = Math.floor((Date.now() - Date.parse(isoString)) / 86400000);
    if (days < 1) return 'today';
    if (days < 2) return 'yesterday';
    if (days < 45) return `${days} days ago`;
    if (days < 365) return `${Math.floor(days / 30)} months ago`;
    const years = Math.floor(days / 365);
    return `${years} year${years > 1 ? 's' : ''} ago`;
}

// Same labels as feature_labels() in app.py.
function featureLabels(tags) {
    tags = tags || {};
    const out = [];
    const wheelchair = tags.wheelchair || tags['toilets:wheelchair'];
    if (wheelchair === 'yes') out.push('♿ Accessible');
    else if (wheelchair === 'limited') out.push('♿ Limited access');
    if (tags.changing_table === 'yes') out.push('🚼 Changing table');
    if (tags.unisex === 'yes' || tags.gender_segregated === 'no') out.push('All-gender');
    if (tags.fee === 'yes') out.push('Fee');
    else if (tags.fee === 'no') out.push('Free');
    if (tags.access === 'customers' || tags['toilets:access'] === 'customers') out.push('Customers only');
    if (tags.opening_hours) out.push(`🕒 ${tags.opening_hours}`);
    return out;
}

// ---- "Did it work?" memory ------------------------------------------------
// When someone copies a code, remember it. The next time they open that
// shop, ask whether the code worked. The answer is an upvote or downvote.
const COPIED_KEY = 'got2go.copied';
const ASK_AFTER_MS = 3 * 60 * 1000;
const FORGET_AFTER_MS = 7 * 24 * 60 * 60 * 1000;

function readJson(key) {
    try { return JSON.parse(localStorage.getItem(key)) || {}; } catch (e) { return {}; }
}
function writeJson(key, value) {
    try { localStorage.setItem(key, JSON.stringify(value)); } catch (e) { /* storage off */ }
}
function rememberCopy(shopId, itemType, itemId) {
    const copied = readJson(COPIED_KEY);
    copied[`${shopId}:${itemType}:${itemId}`] = Date.now();
    writeJson(COPIED_KEY, copied);
}
function forgetCopy(shopId, itemType, itemId) {
    const copied = readJson(COPIED_KEY);
    delete copied[`${shopId}:${itemType}:${itemId}`];
    writeJson(COPIED_KEY, copied);
}

// The copied code at this shop that we should ask about, or null.
function pendingQuestion(shop) {
    const copied = readJson(COPIED_KEY);
    const now = Date.now();
    for (const itemType of ['bathroom_codes', 'wifi_passwords']) {
        for (const item of shop[itemType] || []) {
            const ts = copied[`${shop.id}:${itemType}:${item.id}`];
            if (!ts) continue;
            if (now - ts > FORGET_AFTER_MS || item.user_vote) {
                forgetCopy(shop.id, itemType, item.id);
            } else if (now - ts > ASK_AFTER_MS) {
                return { itemType, item };
            }
        }
    }
    return null;
}

function codeRow(shop, itemType, item) {
    const value = itemType === 'wifi_passwords' ? item.password : item.code;
    const ids = `data-shop="${shop.id}" data-type="${itemType}" data-item="${item.id}"`;
    const up = item.user_vote === 'upvote' ? 'voted-up' : '';
    const down = item.user_vote === 'downvote' ? 'voted-down' : '';
    const fresh = item.last_confirmed_at
        ? `<span class="code-fresh">worked ${timeAgo(item.last_confirmed_at)}</span>` : '';
    return `
        <div class="popup-row">
            <code class="${itemType === 'wifi_passwords' ? 'popup-password' : 'popup-code'}">${escapeHtml(value)}</code>
            <button class="copy-button" aria-label="Copy" data-action="copy" data-value="${escapeHtml(value)}" ${ids}>📋</button>
            <span class="vote-count" title="votes">${item.votes}</span>
            <button class="vote-btn ${up}" aria-label="It worked" title="It worked" data-action="vote" data-vote="upvote" ${ids}>👍</button>
            <button class="vote-btn ${down}" aria-label="It didn't work" title="It didn't work" data-action="vote" data-vote="downvote" ${ids}>👎</button>
            ${fresh}
        </div>`;
}

function codeSection(shop, itemType, title) {
    const items = (shop[itemType] || []).filter(i => i.votes > -3);
    let html = `<hr><h5>${title}</h5>`;
    if (itemType === 'wifi_passwords' && shop.wifi_speed) {
        const sp = shop.wifi_speed;
        html += `<div class="speed-badge">⚡ ${sp.mbps} Mbps <small>(${sp.tests} test${sp.tests > 1 ? 's' : ''})</small></div>`;
    }
    html += items.length
        ? items.map(i => codeRow(shop, itemType, i)).join('')
        : `<p class="popup-none">None yet.</p>`;

    const inputId = `${itemType}-suggestion-${shop.id}`;
    const attrs = itemType === 'bathroom_codes'
        ? `placeholder="New code (digits, *, #)" inputmode="tel" maxlength="12" data-filter="code"`
        : `placeholder="New password" maxlength="16"`;
    html += `
        <div class="suggestion-form">
            <input type="text" id="${inputId}" ${attrs}>
            <button class="suggest-btn" data-action="suggest" data-shop="${shop.id}" data-type="${itemType}" data-input="${inputId}">Add</button>
        </div>`;
    if (itemType === 'wifi_passwords') {
        html += `<button class="link-btn" data-action="speedtest" data-shop="${shop.id}">⚡ Test this Wi-Fi's speed</button>`;
    }
    return html;
}

function createPopupContent(shop) {
    const isRestroom = shop.category === 'restroom';
    let content = `<div class="popup-header"><b>${escapeHtml(shop.name)}</b><br><small>${escapeHtml(shop.address || '')}</small></div>`;

    const labels = featureLabels(shop.tags);
    if (labels.length) {
        content += `<div class="popup-chips">${labels.map(l => `<span>${escapeHtml(l)}</span>`).join('')}</div>`;
    }

    const question = pendingQuestion(shop);
    if (question) {
        const value = question.item.password || question.item.code;
        const ids = `data-shop="${shop.id}" data-type="${question.itemType}" data-item="${question.item.id}"`;
        content += `
            <div class="did-it-work">
                Did <code>${escapeHtml(value)}</code> work last time?
                <div>
                    <button data-action="answer" data-vote="upvote" ${ids}>✓ Yes</button>
                    <button data-action="answer" data-vote="downvote" ${ids}>✗ No</button>
                    <button data-action="answer" data-vote="" ${ids}>Not sure</button>
                </div>
            </div>`;
    } else if (!isRestroom && !hasCodes(shop)) {
        content += `<div class="popup-empty-callout">No codes here yet. Be the first to add one!</div>`;
    }

    content += codeSection(shop, 'bathroom_codes', isRestroom ? 'Door codes' : 'Bathroom codes');
    if (!isRestroom) content += codeSection(shop, 'wifi_passwords', 'Wi-Fi passwords');
    if (shop.url) content += `<a class="popup-page-link" href="${shop.url}">Details &amp; share</a>`;
    return content;
}

// One click handler for every popup button (data-action="...").
document.addEventListener('click', function (event) {
    const el = event.target.closest('[data-action]');
    if (!el) return;
    const shopId = Number(el.dataset.shop);
    const itemId = Number(el.dataset.item);
    const itemType = el.dataset.type;
    switch (el.dataset.action) {
        case 'copy':
            copyToClipboard(el.dataset.value);
            rememberCopy(shopId, itemType, itemId);
            if (itemType === 'wifi_passwords') armSpeedPrompt(shopId);
            break;
        case 'vote':
            forgetCopy(shopId, itemType, itemId);
            handleVote(shopId, itemType, el.dataset.vote, itemId);
            break;
        case 'answer':
            forgetCopy(shopId, itemType, itemId);
            if (el.dataset.vote) handleVote(shopId, itemType, el.dataset.vote, itemId);
            else refreshMarker(shopId);
            break;
        case 'suggest':
            suggest(shopId, itemType, el.dataset.input);
            break;
        case 'speedtest':
            runSpeedTest(shopId);
            break;
    }
});

// Bathroom codes: digits, * and # only.
document.addEventListener('input', function (event) {
    if (event.target.dataset && event.target.dataset.filter === 'code') {
        event.target.value = event.target.value.replace(/[^0-9*#]/g, '');
    }
});

function shopCounts(shop) {
    const parts = [];
    if (shop.category === 'restroom') parts.push(`<span class="count-pill count-restroom">🚻 public</span>`);
    const w = (shop.wifi_passwords || []).length;
    const b = (shop.bathroom_codes || []).length;
    if (w > 0) parts.push(`<span class="count-pill count-wifi">📶 ${w}</span>`);
    if (b > 0) parts.push(`<span class="count-pill count-bathroom">🔢 ${b}</span>`);
    return parts.length ? `<div class="shop-counts">${parts.join(' ')}</div>` : '';
}

function hasCodes(shop) {
    return (shop.wifi_passwords && shop.wifi_passwords.length > 0) ||
        (shop.bathroom_codes && shop.bathroom_codes.length > 0);
}

function rememberShops(shops) {
    shops.forEach(shop => shopsById.set(shop.id, shop));
}

// Sidebar order: shops with codes first, then the closest to the user
// (or to the map center when the location is unknown).
function sortForSidebar(shops) {
    const ref = userLatLng || map.getCenter();
    const dist = s => haversineDistance(ref.lat, ref.lng, s.lat, s.lng);
    const rank = s => (hasCodes(s) ? 2 : 0) + (s.category === 'restroom' ? 1 : 0);
    return shops.slice().sort((a, b) => (rank(b) - rank(a)) || (dist(a) - dist(b)));
}

function populateSidebar(shopsToDisplay, emptyMessage) {
    const shopListDiv = document.getElementById('shop-list');
    shopListDiv.innerHTML = '';

    if (shopsToDisplay.length === 0) {
        shopListDiv.innerHTML = `<div class="empty-state">${emptyMessage ||
            'No shops here. Zoom in, or tap <strong>Find Nearby</strong>.'}</div>`;
        return;
    }

    shopsToDisplay.slice(0, MAX_SIDEBAR_ITEMS).forEach(function (shop) {
        // A real link, so "open in new tab" and crawlers work too.
        const listItem = document.createElement('a');
        listItem.className = 'shop-list-item';
        listItem.href = shop.url;
        listItem.innerHTML = `
            <div class="shop-list-main">
                <b>${escapeHtml(shop.name)}</b>
                <small>${escapeHtml(shop.address || '')}</small>
            </div>
            ${shopCounts(shop)}
        `;
        listItem.onclick = (event) => {
            if (event.metaKey || event.ctrlKey || event.shiftKey) return;
            event.preventDefault();
            focusShop(shop.id);
        };
        shopListDiv.appendChild(listItem);
    });
}

// Fly to a shop and open its popup. The viewport load after the flight
// creates the marker if the shop was not on the map yet.
function focusShop(shopId) {
    const shop = shopsById.get(shopId);
    if (!shop) return;
    pendingOpenId = shopId;
    map.flyTo([shop.lat, shop.lng], Math.max(map.getZoom(), LOCATE_ZOOM), { duration: 0.5 });

    const sidebar = document.getElementById('sidebar');
    if (sidebar.classList.contains('sidebar-open')) {
        sidebar.classList.remove('sidebar-open');
        map.invalidateSize();
    }
}

function openPendingPopup() {
    if (pendingOpenId === null) return;
    const marker = markerById.get(pendingOpenId);
    if (!marker) return;
    pendingOpenId = null;
    markers.zoomToShowLayer(marker, () => marker.openPopup());
}

function createMarker(shop) {
    const marker = L.marker([shop.lat, shop.lng], { icon: shopIcon(shop) });
    marker.shopId = shop.id;
    // Build the popup when it opens, so it always shows the newest data.
    marker.bindPopup(() => createPopupContent(shopsById.get(shop.id)));
    return marker;
}

// Make the markers on the map match `shops`. A marker with an open popup
// stays, so a small pan never closes the shop the user is reading.
function syncMarkers(shops) {
    const wanted = new Set(shops.map(s => s.id));
    const toRemove = [];
    markerById.forEach((marker, id) => {
        if (!wanted.has(id) && !marker.isPopupOpen()) toRemove.push(id);
    });
    markers.removeLayers(toRemove.map(id => markerById.get(id)));
    toRemove.forEach(id => markerById.delete(id));

    const toAdd = [];
    shops.forEach(shop => {
        const existing = markerById.get(shop.id);
        if (existing) {
            existing.setIcon(shopIcon(shop));
        } else {
            const marker = createMarker(shop);
            markerById.set(shop.id, marker);
            toAdd.push(marker);
        }
    });
    markers.addLayers(toAdd);
}

function updateZoomHint() {
    const zoomedIn = map.getZoom() >= DETAIL_ZOOM;
    document.body.classList.toggle('zoomed-in', zoomedIn);
}

// Load the shops for the current map view. Zoomed out: only shops with
// codes. Zoomed in: every shop, so people can add a code to a gray pin.
function loadViewport() {
    updateZoomHint();
    if (viewportRequest) viewportRequest.abort();
    viewportRequest = new AbortController();

    const b = map.getBounds().pad(0.2);
    const bbox = [b.getSouth(), b.getWest(), b.getNorth(), b.getEast()]
        .map(n => n.toFixed(5)).join(',');
    const params = new URLSearchParams({ bbox });
    if (map.getZoom() < DETAIL_ZOOM) params.set('has_codes', '1');

    return fetch(`${API_BASE_URL}/api/coffee_shops?${params}`, { signal: viewportRequest.signal })
        .then(r => r.json())
        .then(shops => {
            rememberShops(shops);
            syncMarkers(shops);
            visibleShopIds = shops.map(s => s.id);
            if (!searchActive) showViewportInSidebar();
            openPendingPopup();
        })
        .catch(err => { if (err.name !== 'AbortError') showToast('Could not load shops.'); })
        .finally(() => { document.getElementById('loading-indicator').style.display = 'none'; });
}

function showViewportInSidebar() {
    const inView = map.getBounds();
    const shops = visibleShopIds
        .map(id => shopsById.get(id))
        .filter(s => s && inView.contains([s.lat, s.lng]));
    populateSidebar(sortForSidebar(shops));
}

function haversineDistance(lat1, lon1, lat2, lon2) {
    const R = 3958.8;
    const rlat1 = lat1 * (Math.PI/180);
    const rlat2 = lat2 * (Math.PI/180);
    const difflat = rlat2 - rlat1;
    const difflon = (lon2 - lon1) * (Math.PI/180);
    return 2 * R * Math.asin(Math.sqrt(
        Math.sin(difflat / 2) ** 2 + Math.cos(rlat1) * Math.cos(rlat2) * Math.sin(difflon / 2) ** 2));
}

function debounce(func, delay) {
    let timeout;
    return function(...args) {
        const context = this;
        clearTimeout(timeout);
        timeout = setTimeout(() => func.apply(context, args), delay);
    };
}

const debouncedSearch = debounce(function () {
    const term = this.value.trim();
    document.getElementById('clear-search').style.display = term ? 'inline-block' : 'none';
    searchActive = term.length > 0;
    if (!searchActive) {
        showViewportInSidebar();
        return;
    }
    fetch(`${API_BASE_URL}/api/coffee_shops?${new URLSearchParams({ q: term })}`)
        .then(r => r.json())
        .then(shops => {
            if (document.getElementById('search-input').value.trim() !== term) return;
            rememberShops(shops);
            populateSidebar(sortForSidebar(shops), 'No shops match that search.');
        });
}, 300);

function locateUser(fromButton) {
    locateFromButton = fromButton;
    if (fromButton) document.getElementById('nearby-spinner').style.display = 'flex';
    map.locate({ setView: true, maxZoom: LOCATE_ZOOM });
}

document.addEventListener('DOMContentLoaded', function () {
    map = L.map('map', { maxZoom: 19 }).setView([47.6062, -122.3321], 13);
    markers = L.markerClusterGroup({
        showCoverageOnHover: false,
        spiderfyOnMaxZoom: true,
        disableClusteringAtZoom: 17,
        maxClusterRadius: 50
    }).addTo(map);

    // CARTO basemaps now watermark every keyless tile, so use OSM directly.
    // The 'basemap-muted' class (style.css) fades OSM colors to a light palette.
    L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
        attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
        maxZoom: 19,
        className: 'basemap-muted'
    }).addTo(map);

    map.on('moveend', debounce(loadViewport, 150));
    loadViewport();

    document.getElementById('search-input').addEventListener('input', debouncedSearch);

    document.getElementById('clear-search').addEventListener('click', function() {
        document.getElementById('search-input').value = '';
        document.getElementById('search-input').dispatchEvent(new Event('input'));
    });
    document.getElementById('clear-search').style.display = 'none';

    if ('serviceWorker' in navigator) {
        // updateViaCache: 'none' forces the browser to bypass the HTTP cache
        // when checking /sw.js, so worker updates are picked up promptly.
        navigator.serviceWorker.register('/sw.js', { updateViaCache: 'none' })
            .catch(err => console.error('SW failed:', err));

        // If the page was already controlled by an older worker, reload once
        // when a new worker takes over so the user lands on fresh assets
        // without needing a hard refresh. Guarded against reload loops, and
        // skipped on first-ever install (no existing controller).
        if (navigator.serviceWorker.controller) {
            let reloading = false;
            navigator.serviceWorker.addEventListener('controllerchange', () => {
                if (reloading) return;
                reloading = true;
                window.location.reload();
            });
        }
    }

    let deferredPrompt;
    const installButton = document.getElementById('install-app-button');

    window.addEventListener('beforeinstallprompt', (e) => {
        e.preventDefault();
        deferredPrompt = e;
        installButton.style.display = 'block';
        installButton.addEventListener('click', () => {
            installButton.style.display = 'none';
            deferredPrompt.prompt();
            deferredPrompt.userChoice.then(() => { deferredPrompt = null; });
        });
    });

    // Welcome modal. Ask for the location after the modal closes, so the
    // browser prompt does not appear on top of it.
    const welcomeModal = document.getElementById('welcome-modal');
    const letsGoButton = document.getElementById('lets-go-button');
    const dontShowWelcomeAgainCheckbox = document.getElementById('dont-show-welcome-again');
    const hasSeenWelcome = localStorage.getItem('hasSeenWelcomeModal');

    function dismissWelcomeModal() {
        if (welcomeModal.style.display === 'none') return;
        welcomeModal.style.display = 'none';
        if (dontShowWelcomeAgainCheckbox.checked) {
            localStorage.setItem('hasSeenWelcomeModal', 'true');
        } else {
            localStorage.removeItem('hasSeenWelcomeModal');
        }
        locateUser(false);
    }
    if (hasSeenWelcome !== 'true') {
        welcomeModal.style.display = 'flex';
    } else {
        locateUser(false);
    }
    letsGoButton.addEventListener('click', dismissWelcomeModal);
    welcomeModal.addEventListener('click', (event) => {
        if (event.target === welcomeModal) dismissWelcomeModal();
    });

    document.getElementById('toggle-sidebar-header').addEventListener('click', function() {
        document.getElementById('sidebar').classList.toggle('sidebar-open');
        map.invalidateSize();
    });

    map.on('click', function() {
        var sidebar = document.getElementById('sidebar');
        if (sidebar.classList.contains('sidebar-open')) {
            sidebar.classList.remove('sidebar-open');
            map.invalidateSize();
        }
    });

    document.getElementById('zoom-in-hint').addEventListener('click', function () {
        map.setZoom(DETAIL_ZOOM);
    });

    document.getElementById('find-me-button').addEventListener('click', () => locateUser(true));

    document.getElementById('speed-card-run').addEventListener('click', () =>
        runSpeedTest(Number(document.getElementById('speed-card').dataset.shop)));
    document.getElementById('speed-card-dismiss').addEventListener('click', hideSpeedCard);

    map.on('locationfound', function(e) {
        userLatLng = e.latlng;
        if (userLocationLayer) {
            map.removeLayer(userLocationLayer);
        }
        userLocationLayer = L.circle(e.latlng, {
            radius: e.accuracy,
            color: '#2563eb',
            fillColor: '#2563eb',
            fillOpacity: 0.08,
            weight: 1.5
        }).addTo(map);
        document.getElementById('nearby-spinner').style.display = 'none';
        // setView triggers moveend, which loads the shops around the user.
    });

    map.on('locationerror', function(e) {
        document.getElementById('nearby-spinner').style.display = 'none';
        // Stay quiet on first open: a user who declines the prompt still
        // gets the city map. Explain only when they pressed the button.
        if (locateFromButton) showToast(e.message);
    });
});

// ---- Wi-Fi speed test -----------------------------------------------------
// After someone copies a Wi-Fi password, they leave to join the network in
// their phone settings. When they come back to the app, offer a short test.
const SPEED_PROMPT_KEY = 'got2go.speedPrompt';
const SPEED_PROMPT_MIN_MS = 5 * 1000;        // they had time to join the Wi-Fi
const SPEED_PROMPT_MAX_MS = 30 * 60 * 1000;  // the offer expires
const SPEED_MAX_DISTANCE_M = 200;            // same limit as the server
let speedTestRunning = false;

function armSpeedPrompt(shopId) {
    writeJson(SPEED_PROMPT_KEY, { shopId, ts: Date.now() });
}

function maybeOfferSpeedTest() {
    const pending = readJson(SPEED_PROMPT_KEY);
    if (!pending.shopId) return;
    const age = Date.now() - pending.ts;
    if (age < SPEED_PROMPT_MIN_MS) return;
    writeJson(SPEED_PROMPT_KEY, {});
    if (age > SPEED_PROMPT_MAX_MS) return;
    const shop = shopsById.get(pending.shopId);
    if (shop) showSpeedCard(shop);
}

function showSpeedCard(shop) {
    const card = document.getElementById('speed-card');
    card.querySelector('.speed-card-text').innerHTML =
        `On the <b>${escapeHtml(shop.name)}</b> Wi-Fi now? Test its speed. It takes about 5 seconds.`;
    card.dataset.shop = shop.id;
    card.hidden = false;
}

function hideSpeedCard() {
    document.getElementById('speed-card').hidden = true;
}

// Resolve with the user's position. Ask the browser if we do not have it.
function currentPosition() {
    if (userLatLng) return Promise.resolve(userLatLng);
    return new Promise((resolve, reject) => {
        const timer = setTimeout(() => reject(new Error('timeout')), 15000);
        map.once('locationfound', e => { clearTimeout(timer); resolve(e.latlng); });
        map.once('locationerror', e => { clearTimeout(timer); reject(e); });
        map.locate({ setView: false });
    });
}

async function downloadOnce(mb) {
    const start = performance.now();
    const res = await fetch(`${API_BASE_URL}/api/speedtest/download?mb=${mb}`,
                            { method: 'POST', cache: 'no-store' });
    if (!res.ok) throw new Error((await res.json()).error || 'Speed test failed.');
    const bytes = (await res.arrayBuffer()).byteLength;
    return { bytes, secs: (performance.now() - start) / 1000 };
}

// Ping three times and keep the best. Download 1 MB; if that is quick,
// download 5 MB for a steadier number.
async function measureSpeed() {
    const pings = [];
    for (let i = 0; i < 3; i++) {
        const start = performance.now();
        await fetch(`${API_BASE_URL}/api/ping?t=${Date.now()}`, { cache: 'no-store' });
        pings.push(performance.now() - start);
    }
    let result = await downloadOnce(1);
    if (result.secs < 1.5) result = await downloadOnce(5);
    return {
        mbps: Math.round((result.bytes * 8 / result.secs / 1e6) * 10) / 10,
        ping_ms: Math.round(Math.min(...pings)),
    };
}

async function runSpeedTest(shopId) {
    if (speedTestRunning) return;
    const shop = shopsById.get(shopId);
    if (!shop) return;
    hideSpeedCard();

    let here;
    try {
        here = await currentPosition();
    } catch (e) {
        showToast('The speed test needs your location to confirm you are at the shop.');
        return;
    }
    if (haversineDistance(here.lat, here.lng, shop.lat, shop.lng) * 1609.34 > SPEED_MAX_DISTANCE_M) {
        showToast(`Go to ${escapeHtml(shop.name)} and join its Wi-Fi to test it.`);
        return;
    }

    speedTestRunning = true;
    showToast('Testing Wi-Fi speed…');
    try {
        const { mbps, ping_ms } = await measureSpeed();
        const res = await fetch(`${API_BASE_URL}/api/speedtest`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ shop_id: shopId, mbps, ping_ms, lat: here.lat, lng: here.lng }),
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data.error);
        rememberShops([data]);
        refreshMarker(shopId);
        showToast(`Wi-Fi speed: ${mbps} Mbps, ${ping_ms} ms ping. Thanks!`);
    } catch (e) {
        showToast(e.message || 'Speed test failed.');
    } finally {
        speedTestRunning = false;
    }
}

document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible') maybeOfferSpeedTest();
});
window.addEventListener('online', maybeOfferSpeedTest);

function showToast(message) {
    var toast = document.getElementById("toast");
    if (toast) {
        toast.className = "show";
        toast.innerHTML = message;
        setTimeout(() => { toast.className = toast.className.replace("show", ""); }, 3000);
    }
}

function copyToClipboard(text) {
    if (navigator.clipboard && window.isSecureContext) {
        navigator.clipboard.writeText(text).then(
            () => showToast("Copied!"),
            () => showToast("Failed to copy."));
    } else {
        let textArea = document.createElement("textarea");
        textArea.value = text;
        textArea.style.position = "fixed";
        textArea.style.top = "-9999px";
        textArea.style.left = "-9999px";
        document.body.appendChild(textArea);
        textArea.focus();
        textArea.select();
        try {
            document.execCommand('copy') ? showToast("Copied!") : showToast("Failed to copy.");
        } catch (err) {
            showToast("Failed to copy.");
        }
        document.body.removeChild(textArea);
    }
}

function handleVote(shopId, itemType, voteType, itemId) {
    fetch(`${API_BASE_URL}/api/vote`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ shop_id: shopId, item_type: itemType, vote_type: voteType, item_id: itemId })
    }).then(response => {
        if (response.ok) return response.json();
        response.json().then(data => showToast('Error: ' + data.error));
        return Promise.reject('Error voting');
    }).then(updatedShop => {
        if (updatedShop) {
            rememberShops([updatedShop]);
            refreshMarker(shopId);
        }
    });
}

function suggest(shopId, itemType, inputId) {
    const itemValue = document.getElementById(inputId).value;
    if (!itemValue) {
        showToast('Please enter a value.');
        return;
    }
    fetch(`${API_BASE_URL}/api/suggest`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ shop_id: shopId, item_type: itemType, item_value: itemValue })
    }).then(response => {
        if (response.ok) return response.json();
        response.json().then(data => showToast('Error: ' + data.error));
        return Promise.reject('Error suggesting item');
    }).then(data => {
        if (data) {
            rememberShops([data.shop]);
            refreshMarker(shopId);
            document.getElementById(inputId).value = '';
            showToast('Added. Thanks!');
        }
    });
}

function refreshMarker(shopId) {
    const shop = shopsById.get(shopId);
    const marker = markerById.get(shopId);
    if (!shop || !marker) return;
    marker.setIcon(shopIcon(shop));
    if (marker.isPopupOpen()) marker.setPopupContent(createPopupContent(shop));
}
