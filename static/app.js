const API_BASE_URL = 'https://got2gosea.com';
let allCoffeeShops = [];
let markers;
let map;

function escapeHtml(unsafe) {
    return unsafe
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#039;");
}

function createPopupContent(shop) {
    let content = `<b>${shop.name}</b><br><small>${shop.address}</small>`

    content += `<hr><h5>Bathroom Codes</h5>`;
    const filteredBathroom = shop.bathroom_codes.filter(code => code.votes > -3);
    if (filteredBathroom.length > 0) {
        filteredBathroom.forEach(code => {
            const upvoteClass = code.user_vote === 'upvote' ? 'voted-up' : '';
            const downvoteClass = code.user_vote === 'downvote' ? 'voted-down' : '';

            content += `
                <div>
                    <span class="popup-item-text">${escapeHtml(code.code)}</span>
                    <span class="vote-count">${code.votes >= 0 ? '❤️' : '💔'} ${code.votes}</span>
                    <button class="${upvoteClass}" onclick="handleVote(${shop.id}, 'bathroom_codes', 'upvote', ${code.id})">👍</button>
                    <button class="${downvoteClass}" onclick="handleVote(${shop.id}, 'bathroom_codes', 'downvote', ${code.id})">👎</button>
                </div>`;
        });
    } else {
        content += `<p>No bathroom codes yet.</p>`;
    }

    content += `
        <div class="suggestion-form">
            <input type="text" id="bathroom-suggestion-${shop.id}" placeholder="New code (digits, *, #)" pattern="[0-9*#]+" oninput="this.value = this.value.replace(/[^0-9*#]/g, '');" maxlength="12">
            <button onclick="suggest(${shop.id}, 'bathroom_codes', 'bathroom-suggestion-${shop.id}')">Suggest</button>
        </div>
    `;

    content += `<hr><h5>Wifi Passwords</h5>`;
    const filteredWifi = shop.wifi_passwords.filter(wifi => wifi.votes > -3);
    if (filteredWifi.length > 0) {
        filteredWifi.forEach(wifi => {
            const upvoteClass = wifi.user_vote === 'upvote' ? 'voted-up' : '';
            const downvoteClass = wifi.user_vote === 'downvote' ? 'voted-down' : '';

            content += `
                <div>
                    <span class="popup-item-text">${escapeHtml(wifi.password)}</span>
                    <span class="vote-count">${wifi.votes >= 0 ? '❤️' : '💔'} ${wifi.votes}</span>
                    <button class="copy-button" onclick="copyToClipboard('${wifi.password}')">📋</button>
                    <button class="${upvoteClass}" onclick="handleVote(${shop.id}, 'wifi_passwords', 'upvote', ${wifi.id})">👍</button>
                    <button class="${downvoteClass}" onclick="handleVote(${shop.id}, 'wifi_passwords', 'downvote', ${wifi.id})">👎</button>
                </div>`;
        });
    } else {
        content += `<p>No wifi passwords yet.</p>`;
    }

    content += `
        <div class="suggestion-form">
            <input type="text" id="wifi-suggestion-${shop.id}" placeholder="New password" maxlength="16" ">
            <button onclick="suggest(${shop.id}, 'wifi_passwords', 'wifi-suggestion-${shop.id}')">Suggest</button>
        </div>
    `;

    return content;
}

function populateSidebar(shopsToDisplay) {
    var shopListDiv = document.getElementById('shop-list');
    shopListDiv.innerHTML = ''; // Clear existing list

    // Sort shops alphabetically by name
    shopsToDisplay.sort((a, b) => a.name.localeCompare(b.name));

    shopsToDisplay.forEach(function (shop) {
        var listItem = document.createElement('div');
        listItem.className = 'shop-list-item';
        listItem.innerHTML = `<b>${shop.name}</b><br><small>${shop.address}</small>`;
        listItem.onclick = function() {
            // Find the actual shop object from allCoffeeShops (important for consistent data)
            const clickedShop = allCoffeeShops.find(s => s.id === shop.id);
            if (!clickedShop) return; // Should not happen

            let targetMarker = null;
            // Check if a marker for this shop already exists on the map
            markers.eachLayer(function(layer) {
                if (layer.shopId === clickedShop.id) {
                    targetMarker = layer;
                }
            });

            if (!targetMarker) {
                // If no marker exists, create one and add it to the map
                targetMarker = L.marker([clickedShop.lat, clickedShop.lng]);
                targetMarker.shopId = clickedShop.id;
                targetMarker.bindPopup(createPopupContent(clickedShop));
                targetMarker.on('click', function() {
                    fetch(`${API_BASE_URL}/api/coffee_shops/${clickedShop.id}`)
                        .then(response => response.json())
                        .then(updatedShop => {
                            updateShopInAllCoffeeShops(updatedShop);
                            refreshMarkerPopup(clickedShop.id);
                        });
                });
                markers.addLayer(targetMarker);
            }

            // Center the map on the clicked shop and open its popup
            map.setView([clickedShop.lat, clickedShop.lng], 16); // Zoom to a reasonable level
            targetMarker.openPopup();

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

function displayCoffeeShops(shopsToDisplayOnMap) {
    // Do NOT clear layers here, as we want to preserve markers added by sidebar clicks
    // markers.clearLayers(); 

    shopsToDisplayOnMap.forEach(function (shop) {
        // Only add marker if it doesn't already exist
        let markerExists = false;
        markers.eachLayer(function(layer) {
            if (layer.shopId === shop.id) {
                markerExists = true;
            }
        });

        if (!markerExists) {
            var marker = L.marker([shop.lat, shop.lng]);
            marker.shopId = shop.id; // Associate shop ID with marker
            marker.bindPopup(createPopupContent(shop));
            marker.on('click', function() {
                fetch(`${API_BASE_URL}/api/coffee_shops/${shop.id}`)
                    .then(response => response.json())
                    .then(updatedShop => {
                        updateShopInAllCoffeeShops(updatedShop);
                        refreshMarkerPopup(shop.id);
                    });
            });
            markers.addLayer(marker);
        }
    });
}

function updateMarkers() {
    const showAllLocationsCheckbox = document.getElementById('show-all-locations');
    let shopsToDisplayOnMap;

    if (showAllLocationsCheckbox.checked) {
        shopsToDisplayOnMap = allCoffeeShops;
    } else {
        shopsToDisplayOnMap = allCoffeeShops.filter(shop =>
            (shop.wifi_passwords && shop.wifi_passwords.length > 0) ||
            (shop.bathroom_codes && shop.bathroom_codes.length > 0)
        );
    }
    // Clear existing markers before adding new ones
    markers.clearLayers();
    displayCoffeeShops(shopsToDisplayOnMap);
}

function debounce(func, delay) {
    let timeout;
    return function(...args) {
        const context = this;
        clearTimeout(timeout);
        timeout = setTimeout(() => func.apply(context, args), delay);
    };
}

// Debounced search function
const debouncedSearch = debounce(function() {
    var searchTerm = this.value.toLowerCase();
    var filteredShops = allCoffeeShops.filter(shop => 
        shop.name.toLowerCase().includes(searchTerm) || 
        shop.address.toLowerCase().includes(searchTerm)
    );
    populateSidebar(filteredShops); // Update sidebar with filtered shops

    // Filter for mappable shops among the search results
    const mappableFilteredShops = filteredShops.filter(shop =>
        (shop.wifi_passwords && shop.wifi_passwords.length > 0) ||
        (shop.bathroom_codes && shop.bathroom_codes.length > 0)
    );
    displayCoffeeShops(mappableFilteredShops); // Update map markers with filtered mappable shops

    // Toggle clear button visibility
    document.getElementById('clear-search').style.display = this.value ? 'inline-block' : 'none';
}, 300); // 300ms debounce delay

document.addEventListener('DOMContentLoaded', function () {
    const loadingIndicator = document.getElementById('loading-indicator');
    loadingIndicator.style.display = 'flex'; // Show loading indicator

    map = L.map('map').setView([47.6062, -122.3321], 13); // Set initial view to central Seattle
    markers = L.featureGroup().addTo(map); // Layer to manage markers

    L.tileLayer('https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}.png', {
        attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors &copy; <a href="https://carto.com/attributions">CARTO</a>',
        maxZoom: 19
    }).addTo(map);

    fetch(`${API_BASE_URL}/api/coffee_shops`)
        .then(response => response.json())
        .then(coffeeShops => {
            allCoffeeShops = coffeeShops; // Store all shops
            populateSidebar(allCoffeeShops); // Populate sidebar with all shops
            updateMarkers(); // Display mappable markers on the map
            // Delay invalidateSize to ensure map container is fully rendered
            setTimeout(function(){
                map.invalidateSize();
            }, 500); // Increased delay to 500ms
            loadingIndicator.style.display = 'none'; // Hide loading indicator
        });

    document.getElementById('search-input').addEventListener('input', debouncedSearch);

    document.getElementById('clear-search').addEventListener('click', function() {
        document.getElementById('search-input').value = '';
        // Trigger the input event to re-filter and display all shops
        document.getElementById('search-input').dispatchEvent(new Event('input'));
    });

    // Hide clear button initially
    document.getElementById('clear-search').style.display = 'none';

    // PWA Installation Logic
    console.log("PWA logic script loaded.");
    if ('serviceWorker' in navigator) {
        console.log("Service Worker is supported by the browser.");
        navigator.serviceWorker.register('/sw.js')
        .then(function(registration) {
            console.log('Service Worker registered successfully! Scope:', registration.scope);
        }).catch(function(error) {
            console.error('Service Worker registration failed:', error);
        });
    } else {
        console.log("Service Worker is NOT supported by the browser.");
    }

    let deferredPrompt;
    const installButton = document.getElementById('install-app-button');

    window.addEventListener('beforeinstallprompt', (e) => {
        console.log("'beforeinstallprompt' event fired.");
        // Prevent the mini-infobar from appearing on mobile
        e.preventDefault();
        // Stash the event so it can be triggered later.
        deferredPrompt = e;
        // Update UI to notify the user they can install the PWA
        console.log("Install button should now be visible.");
        installButton.style.display = 'block';

        installButton.addEventListener('click', (e) => {
            console.log("Install button clicked.");
            // hide our user interface that shows our A2HS button
            installButton.style.display = 'none';
            // Show the prompt
            deferredPrompt.prompt();
            // Wait for the user to respond to the prompt
            deferredPrompt.userChoice.then((choiceResult) => {
                if (choiceResult.outcome === 'accepted') {
                    console.log('User accepted the A2HS prompt');
                } else {
                    console.log('User dismissed the A2HS prompt');
                }
                deferredPrompt = null;
            });
        });
    });

    window.addEventListener('appinstalled', (evt) => {
        console.log('PWA was installed.');
    });

    // Welcome Modal Logic
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

    if (hasSeenWelcome !== 'true') {
        welcomeModal.style.display = 'flex';
    }

    letsGoButton.addEventListener('click', dismissWelcomeModal);

    // Close modal if user clicks outside the modal content
    welcomeModal.addEventListener('click', (event) => {
        if (event.target === welcomeModal) {
            dismissWelcomeModal();
        }
    });

    // Toggle sidebar on mobile
    document.getElementById('toggle-sidebar-header').addEventListener('click', function() {
        document.getElementById('sidebar').classList.toggle('sidebar-open');
        map.invalidateSize(); // Invalidate map size after sidebar toggle
    });

    // Close sidebar when clicking on the map
    map.on('click', function() {
        var sidebar = document.getElementById('sidebar');
        if (sidebar.classList.contains('sidebar-open')) {
            sidebar.classList.remove('sidebar-open');
            map.invalidateSize(); // Invalidate map size after sidebar toggle
        }
    });

    // Event listener for the new checkbox
    document.getElementById('show-all-locations').addEventListener('change', updateMarkers);

    // Geolocation button
    document.getElementById('find-me-button').addEventListener('click', function() {
        map.locate({setView: true, maxZoom: 16});
    });

    map.on('locationfound', function(e) {
        var radius = e.accuracy;
        L.circle(e.latlng, radius).addTo(map);
    });

    map.on('locationerror', function(e) {
        showToast(e.message);
    });

    window.onload = function() {
    map.invalidateSize();
    };
});

function showToast(message) {
    var toast = document.getElementById("toast");
    if (toast) {
        toast.className = "show";
        toast.innerHTML = message;
        setTimeout(function(){ toast.className = toast.className.replace("show", ""); }, 3000);
    }
}

function copyToClipboard(text) {
    // Modern browsers with secure context (HTTPS)
    if (navigator.clipboard && window.isSecureContext) {
        navigator.clipboard.writeText(text).then(function() {
            showToast("Copied to clipboard!");
        }, function(err) {
            showToast("Failed to copy.");
            console.error('Async: Could not copy text: ', err);
        });
    } else {
        // Fallback for older browsers or insecure contexts (HTTP)
        let textArea = document.createElement("textarea");
        textArea.value = text;
        // Make the textarea out of sight
        textArea.style.position = "fixed";
        textArea.style.top = "-9999px";
        textArea.style.left = "-9999px";

        document.body.appendChild(textArea);
        textArea.focus();
        textArea.select();

        try {
            let successful = document.execCommand('copy');
            if (successful) {
                showToast("Copied to clipboard!");
            } else {
                showToast("Failed to copy.");
            }
        } catch (err) {
            showToast("Failed to copy.");
            console.error('Fallback: Oops, unable to copy', err);
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
        if (response.ok) {
            return response.json();
        } else {
            response.json().then(data => showToast('Error: ' + data.error));
            return Promise.reject('Error voting');
        }
    }).then(updatedShop => {
        if (updatedShop) {
            updateShopInAllCoffeeShops(updatedShop);
            refreshMarkerPopup(shopId);
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
        if (response.ok) {
            return response.json();
        } else {
            response.json().then(data => showToast('Error: ' + data.error));
            return Promise.reject('Error suggesting item');
        }
    }).then(data => {
        if (data) {
            const updatedShop = data.shop;
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