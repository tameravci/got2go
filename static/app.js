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

function createPopupContent(shop) {
    const hasAnything =
        (shop.wifi_passwords && shop.wifi_passwords.length > 0) ||
        (shop.bathroom_codes && shop.bathroom_codes.length > 0);

    let content = `<div class="popup-header"><b>${escapeHtml(shop.name)}</b><br><small>${escapeHtml(shop.address || '')}</small></div>`;
    if (!hasAnything) {
        content += `<div class="popup-empty-callout">No codes here yet — be the first to add one!</div>`;
    }

    content += `<hr><h5>Bathroom codes</h5>`;
    const filteredBathroom = shop.bathroom_codes.filter(code => code.votes > -3);
    if (filteredBathroom.length > 0) {
        filteredBathroom.forEach(code => {
            const upvoteClass = code.user_vote === 'upvote' ? 'voted-up' : '';
            const downvoteClass = code.user_vote === 'downvote' ? 'voted-down' : '';
            content += `
                <div class="popup-row">
                    <code class="popup-code">${escapeHtml(code.code)}</code>
                    <span class="vote-count">${code.votes >= 0 ? '▲' : '▽'} ${code.votes}</span>
                    <button class="vote-btn ${upvoteClass}" aria-label="Upvote" onclick="handleVote(${shop.id}, 'bathroom_codes', 'upvote', ${code.id})">👍</button>
                    <button class="vote-btn ${downvoteClass}" aria-label="Downvote" onclick="handleVote(${shop.id}, 'bathroom_codes', 'downvote', ${code.id})">👎</button>
                </div>`;
        });
    } else {
        content += `<p class="popup-none">None yet.</p>`;
    }

    content += `
        <div class="suggestion-form">
            <input type="text" id="bathroom-suggestion-${shop.id}" placeholder="New code (digits, *, #)" pattern="[0-9*#]+" oninput="this.value = this.value.replace(/[^0-9*#]/g, '');" maxlength="12">
            <button class="suggest-btn" onclick="suggest(${shop.id}, 'bathroom_codes', 'bathroom-suggestion-${shop.id}')">Add</button>
        </div>
    `;

    content += `<hr><h5>Wi-Fi passwords</h5>`;
    const filteredWifi = shop.wifi_passwords.filter(wifi => wifi.votes > -3);
    if (filteredWifi.length > 0) {
        filteredWifi.forEach(wifi => {
            const upvoteClass = wifi.user_vote === 'upvote' ? 'voted-up' : '';
            const downvoteClass = wifi.user_vote === 'downvote' ? 'voted-down' : '';
            content += `
                <div class="popup-row">
                    <code class="popup-password">${escapeHtml(wifi.password)}</code>
                    <span class="vote-count">${wifi.votes >= 0 ? '▲' : '▽'} ${wifi.votes}</span>
                    <button class="copy-button" aria-label="Copy password" onclick="copyToClipboard('${escapeHtml(wifi.password).replace(/'/g, "\\'")}')">📋</button>
                    <button class="vote-btn ${upvoteClass}" aria-label="Upvote" onclick="handleVote(${shop.id}, 'wifi_passwords', 'upvote', ${wifi.id})">👍</button>
                    <button class="vote-btn ${downvoteClass}" aria-label="Downvote" onclick="handleVote(${shop.id}, 'wifi_passwords', 'downvote', ${wifi.id})">👎</button>
                </div>`;
        });
    } else {
        content += `<p class="popup-none">None yet.</p>`;
    }

    content += `
        <div class="suggestion-form">
            <input type="text" id="wifi-suggestion-${shop.id}" placeholder="New password" maxlength="16">
            <button class="suggest-btn" onclick="suggest(${shop.id}, 'wifi_passwords', 'wifi-suggestion-${shop.id}')">Add</button>
        </div>
    `;

    return content;
}

function shopCounts(shop) {
    const w = (shop.wifi_passwords || []).length;
    const b = (shop.bathroom_codes || []).length;
    if (w === 0 && b === 0) return '';
    const parts = [];
    if (w > 0) parts.push(`<span class="count-pill count-wifi">📶 ${w}</span>`);
    if (b > 0) parts.push(`<span class="count-pill count-bathroom">🚻 ${b}</span>`);
    return `<div class="shop-counts">${parts.join(' ')}</div>`;
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
    return shops.slice().sort((a, b) =>
        (hasCodes(b) - hasCodes(a)) || (dist(a) - dist(b)));
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
        const listItem = document.createElement('div');
        listItem.className = 'shop-list-item';
        listItem.innerHTML = `
            <div class="shop-list-main">
                <b>${escapeHtml(shop.name)}</b>
                <small>${escapeHtml(shop.address || '')}</small>
            </div>
            ${shopCounts(shop)}
        `;
        listItem.onclick = () => focusShop(shop.id);
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
            showToast('Added — thanks!');
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
