const API_BASE_URL = ''; // same-origin: works on prod, piku, localhost
let allCoffeeShops = [];
let markers;
let map;
let userLocationLayer = null;

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

function populateSidebar(shopsToDisplay) {
    var shopListDiv = document.getElementById('shop-list');
    shopListDiv.innerHTML = '';

    if (shopsToDisplay.length === 0) {
        shopListDiv.innerHTML = '<div class="empty-state">No shops match — try a broader search, or tap <strong>Find Nearby</strong>.</div>';
        return;
    }

    shopsToDisplay.sort((a, b) => a.name.localeCompare(b.name));

    shopsToDisplay.forEach(function (shop) {
        var listItem = document.createElement('div');
        listItem.className = 'shop-list-item';
        listItem.innerHTML = `
            <div class="shop-list-main">
                <b>${escapeHtml(shop.name)}</b>
                <small>${escapeHtml(shop.address || '')}</small>
            </div>
            ${shopCounts(shop)}
        `;
        listItem.onclick = function() {
            const clickedShop = allCoffeeShops.find(s => s.id === shop.id);
            if (!clickedShop) return;

            let targetMarker = null;
            markers.eachLayer(function(layer) {
                if (layer.shopId === clickedShop.id) targetMarker = layer;
            });

            if (!targetMarker) {
                targetMarker = L.marker([clickedShop.lat, clickedShop.lng], { icon: shopIcon(clickedShop) });
                targetMarker.shopId = clickedShop.id;
                targetMarker.bindPopup(createPopupContent(clickedShop));
                targetMarker.on('click', function() {
                    fetch(`${API_BASE_URL}/api/coffee_shops/${clickedShop.id}`)
                        .then(r => r.json())
                        .then(updatedShop => {
                            updateShopInAllCoffeeShops(updatedShop);
                            refreshMarker(clickedShop.id);
                        });
                });
                markers.addLayer(targetMarker);
            }

            map.flyTo([clickedShop.lat, clickedShop.lng], 17, { duration: 0.5 });
            setTimeout(() => {
                if (markers.zoomToShowLayer) {
                    markers.zoomToShowLayer(targetMarker, () => targetMarker.openPopup());
                } else {
                    targetMarker.openPopup();
                }
            }, 250);

            var sidebar = document.getElementById('sidebar');
            if (sidebar.classList.contains('sidebar-open')) {
                sidebar.classList.remove('sidebar-open');
                map.invalidateSize();
            }
        };
        shopListDiv.appendChild(listItem);
    });
}

function displayCoffeeShops(shopsToDisplayOnMap) {
    shopsToDisplayOnMap.forEach(function (shop) {
        let markerExists = false;
        markers.eachLayer(function(layer) {
            if (layer.shopId === shop.id) markerExists = true;
        });

        if (!markerExists) {
            var marker = L.marker([shop.lat, shop.lng], { icon: shopIcon(shop) });
            marker.shopId = shop.id;
            marker.bindPopup(createPopupContent(shop));
            marker.on('click', function() {
                fetch(`${API_BASE_URL}/api/coffee_shops/${shop.id}`)
                    .then(r => r.json())
                    .then(updatedShop => {
                        updateShopInAllCoffeeShops(updatedShop);
                        refreshMarker(shop.id);
                    });
            });
            markers.addLayer(marker);
        }
    });
}

function updateMarkers() {
    const showAll = document.getElementById('show-all-locations').checked;
    const shopsToDisplay = showAll
        ? allCoffeeShops
        : allCoffeeShops.filter(s =>
            (s.wifi_passwords && s.wifi_passwords.length > 0) ||
            (s.bathroom_codes && s.bathroom_codes.length > 0));
    markers.clearLayers();
    displayCoffeeShops(shopsToDisplay);
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

const debouncedSearch = debounce(function() {
    var searchTerm = this.value.toLowerCase();
    var filteredShops = allCoffeeShops.filter(shop =>
        shop.name.toLowerCase().includes(searchTerm) ||
        (shop.address || '').toLowerCase().includes(searchTerm));
    populateSidebar(filteredShops);

    const mappable = filteredShops.filter(s =>
        (s.wifi_passwords && s.wifi_passwords.length > 0) ||
        (s.bathroom_codes && s.bathroom_codes.length > 0));
    markers.clearLayers();
    displayCoffeeShops(mappable);

    document.getElementById('clear-search').style.display = this.value ? 'inline-block' : 'none';
}, 300);

document.addEventListener('DOMContentLoaded', function () {
    const loadingIndicator = document.getElementById('loading-indicator');
    loadingIndicator.style.display = 'flex';

    map = L.map('map', { maxZoom: 19 }).setView([47.6062, -122.3321], 13);
    markers = L.markerClusterGroup({
        showCoverageOnHover: false,
        spiderfyOnMaxZoom: true,
        disableClusteringAtZoom: 17,
        maxClusterRadius: 50
    }).addTo(map);

    L.tileLayer('https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}.png', {
        attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors &copy; <a href="https://carto.com/attributions">CARTO</a>',
        maxZoom: 19
    }).addTo(map);

    fetch(`${API_BASE_URL}/api/coffee_shops`)
        .then(response => response.json())
        .then(coffeeShops => {
            allCoffeeShops = coffeeShops;
            populateSidebar(allCoffeeShops);
            updateMarkers();
            setTimeout(() => map.invalidateSize(), 500);
            loadingIndicator.style.display = 'none';
        });

    document.getElementById('search-input').addEventListener('input', debouncedSearch);

    document.getElementById('clear-search').addEventListener('click', function() {
        document.getElementById('search-input').value = '';
        document.getElementById('search-input').dispatchEvent(new Event('input'));
    });
    document.getElementById('clear-search').style.display = 'none';

    if ('serviceWorker' in navigator) {
        navigator.serviceWorker.register('/sw.js').catch(err => console.error('SW failed:', err));
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

    // Welcome modal
    const welcomeModal = document.getElementById('welcome-modal');
    const letsGoButton = document.getElementById('lets-go-button');
    const dontShowWelcomeAgainCheckbox = document.getElementById('dont-show-welcome-again');
    const hasSeenWelcome = localStorage.getItem('hasSeenWelcomeModal');

    function dismissWelcomeModal() {
        welcomeModal.style.display = 'none';
        if (dontShowWelcomeAgainCheckbox.checked) {
            localStorage.setItem('hasSeenWelcomeModal', 'true');
        } else {
            localStorage.removeItem('hasSeenWelcomeModal');
        }
    }
    if (hasSeenWelcome !== 'true') welcomeModal.style.display = 'flex';
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

    document.getElementById('show-all-locations').addEventListener('change', updateMarkers);

    document.getElementById('find-me-button').addEventListener('click', function() {
        document.getElementById('nearby-spinner').style.display = 'flex';
        map.locate({ setView: true, maxZoom: 16 });
    });

    map.on('locationfound', function(e) {
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

        const nearbyShops = allCoffeeShops.filter(shop =>
            haversineDistance(e.latlng.lat, e.latlng.lng, shop.lat, shop.lng) <= 0.3);

        markers.clearLayers();
        populateSidebar(nearbyShops);
        displayCoffeeShops(nearbyShops);
        document.getElementById('nearby-spinner').style.display = 'none';

        if (nearbyShops.length === 0) {
            showToast("No coffee shops within 0.3 miles of you.");
        }
    });

    map.on('locationerror', function(e) {
        showToast(e.message);
        document.getElementById('nearby-spinner').style.display = 'none';
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
            updateShopInAllCoffeeShops(updatedShop);
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
            updateShopInAllCoffeeShops(data.shop);
            refreshMarker(shopId);
            document.getElementById(inputId).value = '';
            showToast('Added — thanks!');
        }
    });
}

function updateShopInAllCoffeeShops(updatedShop) {
    const index = allCoffeeShops.findIndex(shop => shop.id === updatedShop.id);
    if (index !== -1) allCoffeeShops[index] = updatedShop;
}

function refreshMarker(shopId) {
    const shop = allCoffeeShops.find(s => s.id === shopId);
    if (!shop) return;
    markers.eachLayer(function (layer) {
        if (layer.shopId === shopId) {
            layer.setIcon(shopIcon(shop));
            layer.setPopupContent(createPopupContent(shop));
            if (layer.isPopupOpen && layer.isPopupOpen()) layer.openPopup();
        }
    });
}
