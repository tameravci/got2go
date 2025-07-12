const API_BASE_URL = 'http://tameravci.tplinkdns.com:5010';
let allCoffeeShops = [];
let markers;
let map;

function createPopupContent(shop) {
    let content = `<b>${shop.name}</b><br><small>${shop.address}</small><hr>`

    content += `<h5>Wifi Passwords</h5>`;
    if (shop.wifi_passwords && shop.wifi_passwords.length > 0) {
        shop.wifi_passwords.forEach(wifi => {
            const voteKey = `vote-${shop.id}-wifi_passwords-${wifi.id}`;
            const userVote = localStorage.getItem(voteKey);
            const upvoteClass = userVote === 'upvote' ? 'voted-up' : '';
            const downvoteClass = userVote === 'downvote' ? 'voted-down' : '';

            content += `
                <div>
                    <span>${wifi.password} (${wifi.votes} vote${(wifi.votes === 1 || wifi.votes === 0) ? '' : 's'})</span>
                    <button class="${upvoteClass}" onclick="handleVote(${shop.id}, 'wifi_passwords', '${wifi.password}', 'upvote', ${wifi.id})">👍</button>
                    <button class="${downvoteClass}" onclick="handleVote(${shop.id}, 'wifi_passwords', '${wifi.password}', 'downvote', ${wifi.id})">👎</button>
                </div>`;
        });
    } else {
        content += `<p>No wifi passwords yet. Be the first to suggest one!</p>`;
    }

    content += `
        <h5>Suggest a new Wifi Password</h5>
        <div class="suggestion-form">
            <input type="text" id="wifi-suggestion-${shop.id}" placeholder="New password">
            <button onclick="suggest(${shop.id}, 'wifi_passwords', 'wifi-suggestion-${shop.id}')">Suggest</button>
        </div>
    `;

    content += `<hr><h5>Bathroom Codes</h5>`;
    if (shop.bathroom_codes && shop.bathroom_codes.length > 0) {
        shop.bathroom_codes.forEach(code => {
            const voteKey = `vote-${shop.id}-bathroom_codes-${code.id}`;
            const userVote = localStorage.getItem(voteKey);
            const upvoteClass = userVote === 'upvote' ? 'voted-up' : '';
            const downvoteClass = userVote === 'downvote' ? 'voted-down' : '';

            content += `
                <div>
                    <span>${code.code} (${code.votes} vote${(code.votes === 1 || code.votes === 0) ? '' : 's'})</span>
                    <button class="${upvoteClass}" onclick="handleVote(${shop.id}, 'bathroom_codes', '${code.code}', 'upvote', ${code.id})">👍</button>
                    <button class="${downvoteClass}" onclick="handleVote(${shop.id}, 'bathroom_codes', '${code.code}', 'downvote', ${code.id})">👎</button>
                </div>`;
        });
    } else {
        content += `<p>No bathroom codes yet. Be the first to suggest one!</p>`;
    }

    content += `
        <h5>Suggest a new Bathroom Code</h5>
        <div class="suggestion-form">
            <input type="text" id="bathroom-suggestion-${shop.id}" placeholder="New code">
            <button onclick="suggest(${shop.id}, 'bathroom_codes', 'bathroom-suggestion-${shop.id}')">Suggest</button>
        </div>
    `;

    return content;
}

function displayCoffeeShops(shopsToDisplay) {
    markers.clearLayers(); // Clear existing markers
    var shopListDiv = document.getElementById('shop-list');
    shopListDiv.innerHTML = ''; // Clear existing list

    shopsToDisplay.forEach(function (shop) {
        var marker = L.marker([shop.lat, shop.lng]);
        marker.shopId = shop.id; // Associate shop ID with marker
        marker.bindPopup(createPopupContent(shop));
        marker.on('popupopen', function (e) {
            var currentZoom = map.getZoom();
            var offset = 0.006 * Math.pow(2, (13 - currentZoom)); // Adjust 0.006 and 13 as needed
            map.panTo([e.popup.getLatLng().lat + offset, e.popup.getLatLng().lng]);
        });
        markers.addLayer(marker);

        // Add to sidebar list
        var listItem = document.createElement('div');
        listItem.className = 'shop-list-item';
        listItem.innerHTML = `<b>${shop.name}</b><br><small>${shop.address}</small>`;
        listItem.onclick = function() {
            var targetZoom = 16;
            var offset = 0.006 * Math.pow(2, (13 - targetZoom)); // Use the same formula
            map.setView([shop.lat + offset, shop.lng], targetZoom);
            marker.openPopup();
            // Collapse sidebar if open
            var sidebar = document.getElementById('sidebar');
            if (sidebar.classList.contains('sidebar-open')) {
                sidebar.classList.remove('sidebar-open');
                map.invalidateSize(); // Invalidate map size after sidebar toggle
            }
        };
        shopListDiv.appendChild(listItem);
    });
}

document.addEventListener('DOMContentLoaded', function () {
    map = L.map('map').setView([47.6062, -122.3321], 13);
    markers = L.featureGroup().addTo(map); // Layer to manage markers

    L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
        attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
    }).addTo(map);

    fetch(`${API_BASE_URL}/api/coffee_shops`)
        .then(response => response.json())
        .then(coffeeShops => {
            allCoffeeShops = coffeeShops; // Store all shops
            displayCoffeeShops(allCoffeeShops); // Display all initially
            // Delay invalidateSize to ensure map container is fully rendered
            setTimeout(function() {
                map.invalidateSize();
            }, 200); // 200ms delay
        });

    document.getElementById('search-button').addEventListener('click', function() {
        var searchTerm = document.getElementById('search-input').value.toLowerCase();
        var filteredShops = allCoffeeShops.filter(shop => 
            shop.name.toLowerCase().includes(searchTerm) || 
            shop.address.toLowerCase().includes(searchTerm)
        );
        displayCoffeeShops(filteredShops);
    });

    // Toggle sidebar on mobile
    document.getElementById('toggle-sidebar-header').addEventListener('click', function() {
        document.getElementById('sidebar').classList.toggle('sidebar-open');
        map.invalidateSize(); // Invalidate map size after sidebar toggle
    });
});

function handleVote(shopId, itemType, itemValue, voteType, itemId) {
    const userVoteKeyPrefix = `vote-${shopId}-${itemType}`;
    const currentVoteKey = `${userVoteKeyPrefix}-${itemId}`;
    let previousVoteKey = null;

    for (let i = 0; i < localStorage.length; i++) {
        const key = localStorage.key(i);
        if (key.startsWith(userVoteKeyPrefix) && localStorage.getItem(key) === 'upvote') {
            previousVoteKey = key;
            break;
        }
    }

    fetch(`${API_BASE_URL}/api/vote`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ shop_id: shopId, item_type: itemType, item_value: itemValue, vote_type: voteType, item_id: itemId })
    }).then(response => {
        if (response.ok) {
            return response.json();
        } else {
            response.json().then(data => alert('Error: ' + data.error));
            return Promise.reject('Error voting');
        }
    }).then(updatedShop => {
        if (updatedShop) {
            if (previousVoteKey && previousVoteKey !== currentVoteKey) {
                localStorage.removeItem(previousVoteKey);
            }

            const userVote = localStorage.getItem(currentVoteKey);
            if (userVote === voteType) {
                localStorage.removeItem(currentVoteKey);
            } else {
                localStorage.setItem(currentVoteKey, voteType);
            }

            updateShopInAllCoffeeShops(updatedShop);
            refreshMarkerPopup(shopId);
        }
    });
}

function suggest(shopId, itemType, inputId) {
    const itemValue = document.getElementById(inputId).value;
    if (!itemValue) {
        alert('Please enter a value.');
        return;
    }

    const userVoteKeyPrefix = `vote-${shopId}-${itemType}`;
    let previousVoteKey = null;

    for (let i = 0; i < localStorage.length; i++) {
        const key = localStorage.key(i);
        if (key.startsWith(userVoteKeyPrefix) && localStorage.getItem(key) === 'upvote') {
            previousVoteKey = key;
            break;
        }
    }

    fetch(`${API_BASE_URL}/api/suggest`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ shop_id: shopId, item_type: itemType, item_value: itemValue })
    }).then(response => {
        if (response.ok) {
            return response.json();
        } else {
            response.json().then(data => alert('Error: ' + data.error));
            return Promise.reject('Error suggesting item');
        }
    }).then(data => {
        if (data) {
            if (previousVoteKey) {
                localStorage.removeItem(previousVoteKey);
            }

            const updatedShop = data.shop;
            const newItemId = data.newItemId;

            const voteKey = `vote-${shopId}-${itemType}-${newItemId}`;
            localStorage.setItem(voteKey, 'upvote');

            updateShopInAllCoffeeShops(updatedShop);
            refreshMarkerPopup(shopId);
            document.getElementById(inputId).value = '';
        }
    });
}

function updateShopInAllCoffeeShops(updatedShop) {
    const index = allCoffeeShops.findIndex(shop => shop.id === updatedShop.id);
    if (index !== -1) {
        allCoffeeShops[index] = updatedShop;
    }
}

function refreshMarkerPopup(shopId) {
    const shop = allCoffeeShops.find(s => s.id === shopId);
    if (shop) {
        markers.eachLayer(function (layer) {
            if (layer.shopId === shopId) {
                layer.setPopupContent(createPopupContent(shop));
                if (layer.isPopupOpen()) {
                    layer.openPopup();
                }
            }
        });
    }
}
