const API_BASE_URL = 'http://tameravci.tplinkdns.com:5010';
document.addEventListener('DOMContentLoaded', function () {
    var map = L.map('map').setView([47.6062, -122.3321], 13);
    var allCoffeeShops = []; // To store all fetched coffee shops
    var markers = L.featureGroup().addTo(map); // Layer to manage markers

    L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
        attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
    }).addTo(map);

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
            marker.bindPopup(createPopupContent(shop));
            markers.addLayer(marker);

            // Add to sidebar list
            var listItem = document.createElement('div');
            listItem.className = 'shop-list-item';
            listItem.innerHTML = `<b>${shop.name}</b><br><small>${shop.address}</small>`;
            listItem.onclick = function() {
                map.setView([shop.lat, shop.lng], 16); // Pan to shop and zoom in
                marker.openPopup();
            };
            shopListDiv.appendChild(listItem);
        });
    }

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
});

function handleVote(shopId, itemType, itemValue, voteType, itemId) {
    const voteKey = `vote-${shopId}-${itemType}-${itemId}`;
    const userVote = localStorage.getItem(voteKey);

    if (userVote === voteType) { // User is trying to undo their vote
        fetch(`${API_BASE_URL}/api/vote`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ shop_id: shopId, item_type: itemType, item_value: itemValue, vote_type: voteType })
        }).then(response => {
            if (response.ok) {
                localStorage.removeItem(voteKey);
                alert('Vote undone!');
                location.reload();
            } else {
                response.json().then(data => alert('Error: ' + data.error));
            }
        });
    } else if (userVote && userVote !== voteType) {
        alert('You have already voted on this item. Please undo your previous vote first.');
    } else { // New vote
        fetch(`${API_BASE_URL}/api/vote`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ shop_id: shopId, item_type: itemType, item_value: itemValue, vote_type: voteType })
        }).then(response => {
            if (response.ok) {
                localStorage.setItem(voteKey, voteType);
                alert('Thanks for your vote!');
                location.reload();
            } else {
                response.json().then(data => alert('Error: ' + data.error));
            }
        });
    }
}

function suggest(shopId, itemType, inputId) {
    const itemValue = document.getElementById(inputId).value;
    if (!itemValue) {
        alert('Please enter a value.');
        return;
    }

    fetch(`${API_BASE_URL}/api/suggest`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ shop_id: shopId, item_type: itemType, item_value: itemValue })
    }).then(response => {
        if (response.ok) {
            alert('Thanks for your suggestion!');
            location.reload(); // Simple way to refresh the data
        } else {
            response.json().then(data => alert('Error: ' + data.error));
        }
    });
}
