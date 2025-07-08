// js/map.js
let map;
let drawnItems;
let baseMapLayer;
let osmRoadsLayer;

export function initMap() {
    map = L.map('map').setView([50.8056, -1.0875], 13);
    baseMapLayer = L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
        attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
    }).addTo(map);

    drawnItems = new L.FeatureGroup().addTo(map);

    const drawControl = new L.Control.Draw({
        edit: {
            featureGroup: drawnItems,
            remove: true
        },
        draw: {
            polygon: false,
            polyline: false,
            circle: false,
            marker: false,
            circlemarker: false,
            rectangle: {
                shapeOptions: {
                    color: '#007bff'
                }
            }
        }
    });
    map.addControl(drawControl);

    osmRoadsLayer = L.geoJSON(null, {
        style: () => ({
            color: '#ff0000',
            weight: 2,
            opacity: 0.8,
            dashArray: '5, 10'
        })
    }).addTo(map);

    // Add a generic toggle method to the map instance
    L.Map.prototype.toggleLayer = (layer, show) => {
        if (layer) {
            if (show && !map.hasLayer(layer)) {
                map.addLayer(layer);
            } else if (!show && map.hasLayer(layer)) {
                map.removeLayer(layer);
            }
        }
    };

    return {
        map,
        drawnItems
    };
}

export function getMap() {
    return map;
}

export function getOsmRoadsLayer() {
    return osmRoadsLayer;
}

export function clearDrawnItems() {
    if (drawnItems) drawnItems.clearLayers();
}

export function clearOsmRoadsLayer() {
    if (osmRoadsLayer) osmRoadsLayer.clearLayers();
}

export function removeLayer(layer) {
    if (map && layer) {
        map.removeLayer(layer);
    }
}

export function updateRectangleInfo(layer) {
    const bounds = layer.getBounds();
    const southWest = bounds.getSouthWest();
    const southEast = bounds.getSouthEast();
    const northWest = bounds.getNorthWest();
    const widthMeters = southWest.distanceTo(southEast);
    const heightMeters = southWest.distanceTo(northWest);
    const formatDistance = (meters) => (meters > 1000) ? `${(meters / 1000).toFixed(2)} km` : `${meters.toFixed(0)} m`;
    const content = `<b>Width:</b> ${formatDistance(widthMeters)}<br><b>Height:</b> ${formatDistance(heightMeters)}`;
    layer.bindPopup(content).openPopup();
}

export function createCasedGeoJSON(geojson, mainStyle, casingStyle) {
    const casingLayer = L.geoJSON(geojson, {
        style: casingStyle
    });
    const mainLayer = L.geoJSON(geojson, {
        style: mainStyle
    });
    return L.featureGroup([casingLayer, mainLayer]);
}