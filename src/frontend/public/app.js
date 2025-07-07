document.addEventListener('DOMContentLoaded', () => {
    // ... (existing map initialization) ...
    const map = L.map('map').setView([50.8056, -1.0875], 13);
    let baseMapLayer = L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
        attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
    }).addTo(map);

    const drawnItems = new L.FeatureGroup().addTo(map);
    map.addControl(new L.Control.Draw({
        edit: { featureGroup: drawnItems, remove: true },
        draw: {
            polygon: false, polyline: false, circle: false, marker: false, circlemarker: false,
            rectangle: { shapeOptions: { color: '#007bff' } }
        }
    }));


    // -------------------------------------------------------------------------
    // Global State
    // -------------------------------------------------------------------------
    const API_BASE_URL = 'http://localhost:4000';
    let drawnRectangle = null;
    let analysisState = {
        satLayer: null, gtMaskLayer: null, predMaskLayer: null, predGraphLayer: null,
        imageUrl: null, bounds: null, rawBounds: null,
        satellite: null // ADDED to store the selected satellite
    };

    // -------------------------------------------------------------------------
    // UI Element References
    // -------------------------------------------------------------------------
    const loader = document.getElementById('loader');
    const generateImageBtn = document.getElementById('generateImageBtn');
    const generateGTMaskBtn = document.getElementById('generateGTMaskBtn');
    const runDetectionBtn = document.getElementById('runDetectionBtn');
    const updateRoadsBtn = document.getElementById('updateRoadsBtn');
    const startDateInput = document.getElementById('startDate');
    const endDateInput = document.getElementById('endDate');

    // ADDED: Satellite option UI elements
    const satelliteSelect = document.getElementById('satelliteSelect');
    const sentinel2Options = document.getElementById('sentinel2Options');
    const cloudCoverSlider = document.getElementById('cloudCoverSlider');
    const cloudCoverValue = document.getElementById('cloudCoverValue');
    const sentinel1Options = document.getElementById('sentinel1Options');
    const polarizationSelect = document.getElementById('polarizationSelect');
    // END ADDED

    // ... (existing control containers and other UI refs) ...
    const satelliteControls = document.getElementById('satelliteControls');
    const gtMaskControls = document.getElementById('gtMaskControls');
    const detectionControls = document.getElementById('detectionControls');
    const imageDateDisplay = document.getElementById('imageDateDisplay');
    const toggleSat = document.getElementById('toggleSat');
    const opacitySlider = document.getElementById('opacitySlider');
    const gtMaskOpacitySlider = document.getElementById('gtMaskOpacitySlider');
    const predMaskOpacitySlider = document.getElementById('predMaskOpacitySlider');
    const toggleGtMask = document.getElementById('toggleGtMask');
    const togglePredMask = document.getElementById('togglePredMask');
    const togglePredGraph = document.getElementById('togglePredGraph');


    // -------------------------------------------------------------------------
    // Helper Functions & Event Listeners for New UI
    // -------------------------------------------------------------------------

    // ADDED: Event listener to show/hide satellite options
    satelliteSelect.addEventListener('change', (e) => {
        const selected = e.target.value;
        sentinel1Options.style.display = selected === 'sentinel_1' ? 'block' : 'none';
        sentinel2Options.style.display = selected === 'sentinel_2' ? 'block' : 'none';
    });

    // ADDED: Listener to update the cloud cover percentage display
    cloudCoverSlider.addEventListener('input', (e) => {
        cloudCoverValue.textContent = e.target.value;
    });

    // ... (the rest of the helper functions are the same) ...
    const showLoader = (text = 'Processing...') => { loader.textContent = text; loader.style.display = 'flex'; };
    const hideLoader = () => loader.style.display = 'none';

    const resetWorkflow = (fullReset = false) => {
        Object.values(analysisState).forEach(layer => {
            if (layer && typeof layer.remove === 'function') layer.remove();
        });
        analysisState = { satLayer: null, gtMaskLayer: null, predMaskLayer: null, predGraphLayer: null, imageUrl: null, bounds: null, rawBounds: null, satellite: null };
        satelliteControls.style.display = 'none';
        gtMaskControls.style.display = 'none';
        detectionControls.style.display = 'none';
        imageDateDisplay.style.display = 'none';
        generateGTMaskBtn.disabled = true;
        runDetectionBtn.disabled = true;

        if (fullReset) {
            generateImageBtn.disabled = true;
            if(drawnItems) drawnItems.clearLayers();
            if(osmRoadsLayer) osmRoadsLayer.clearLayers();
            drawnRectangle = null;
        }
    };

    const updateRectangleInfo = (layer) => {
        const bounds = layer.getBounds();
        const southWest = bounds.getSouthWest();
        const southEast = bounds.getSouthEast();
        const northWest = bounds.getNorthWest();
        const widthMeters = southWest.distanceTo(southEast);
        const heightMeters = southWest.distanceTo(northWest);
        const formatDistance = (meters) => (meters > 1000) ? `${(meters / 1000).toFixed(2)} km` : `${meters.toFixed(0)} m`;
        const content = `<b>Width:</b> ${formatDistance(widthMeters)}<br><b>Height:</b> ${formatDistance(heightMeters)}`;
        layer.bindPopup(content).openPopup();
    };
    
    const today = new Date();
    const threeMonthsAgo = new Date();
    threeMonthsAgo.setMonth(today.getMonth() - 3);
    startDateInput.value = threeMonthsAgo.toISOString().split('T')[0];
    endDateInput.value = today.toISOString().split('T')[0];


    // ... (map drawing and OSM logic is the same) ...
    map.on(L.Draw.Event.CREATED, (event) => {
        resetWorkflow(true);
        drawnRectangle = event.layer;
        drawnItems.addLayer(drawnRectangle);
        generateImageBtn.disabled = false;
        updateRoads();
        updateRectangleInfo(drawnRectangle);
    });

    map.on(L.Draw.Event.EDITED, (event) => {
        resetWorkflow(true);
        drawnItems.eachLayer(layer => {
            drawnRectangle = layer;
            updateRectangleInfo(layer);
        });
        if (drawnRectangle) {
            generateImageBtn.disabled = false;
            updateRoads();
        }
    });

    map.on(L.Draw.Event.DELETED, () => resetWorkflow(true));

    let osmRoadsLayer = L.geoJSON(null, { style: () => ({ color: '#ff0000', weight: 2.5, opacity: 0.8 }) }).addTo(map);
    const getSelectedRoadTypes = () => Array.from(document.querySelectorAll('.road-type-filter:checked')).map(cb => cb.value).join(',');
    const updateRoads = async () => {
        if (!drawnRectangle) return;
        const bbox = drawnRectangle.getBounds().toBBoxString();
        const types = getSelectedRoadTypes();
        if (!types) { osmRoadsLayer.clearLayers(); return; }
        showLoader('Fetching OSM Roads...');
        try {
            const response = await fetch(`${API_BASE_URL}/api/get_roads?bbox=${bbox}&types=${types}`);
            if (!response.ok) throw new Error('Failed to fetch OSM roads');
            osmRoadsLayer.clearLayers().addData(await response.json());
        } catch (error) { console.error('Error updating roads:', error); alert('Could not load OSM road data.');
        } finally { hideLoader(); }
    };
    updateRoadsBtn.addEventListener('click', updateRoads);
    document.getElementById('roadToggle').addEventListener('change', (e) => map.toggleLayer(osmRoadsLayer, e.target.checked));


    // -------------------------------------------------------------------------
    // STAGE 1: Generate Satellite Image - MODIFIED
    // -------------------------------------------------------------------------
    generateImageBtn.addEventListener('click', async () => {
        if (!drawnRectangle) { alert('Please draw a rectangle on the map first.'); return; }
        resetWorkflow();
        
        // MODIFIED: Gather all options from the UI
        const bbox = drawnRectangle.getBounds().toBBoxString();
        const satellite = satelliteSelect.value;
        
        const params = new URLSearchParams({
            bbox: bbox,
            start_date: startDateInput.value,
            end_date: endDateInput.value,
            satellite: satellite
        });

        if (satellite === 'sentinel_2') {
            params.append('cloudy_pixel_percentage', cloudCoverSlider.value);
        } else if (satellite === 'sentinel_1') {
            params.append('polarization', polarizationSelect.value);
        }

        showLoader('Downloading satellite data...');
        try {
            // Pass all params to the download endpoint
            const downloadRes = await fetch(`${API_BASE_URL}/api/download_satellite_image?${params.toString()}`);
            if (!downloadRes.ok) throw new Error((await downloadRes.json()).error);
            const downloadData = await downloadRes.json();

            showLoader('Processing image...');
            // IMPORTANT: Pass the satellite name to the processing endpoint as well
            const processRes = await fetch(`${API_BASE_URL}/api/process_satellite_image?satellite=${satellite}`);
            if (!processRes.ok) throw new Error((await processRes.json()).error);
            const processData = await processRes.json();
            
            Object.assign(analysisState, processData);
            analysisState.satellite = satellite; // Store the satellite in the state
            analysisState.satLayer = L.imageOverlay(processData.imageUrl, processData.bounds, { opacity: 0.8 }).addTo(map);
            map.fitBounds(processData.bounds);

            imageDateDisplay.innerHTML = `<b>Image Date:</b> ${downloadData.imageDate}`;
            imageDateDisplay.style.display = 'block';
            satelliteControls.style.display = 'block';
            generateGTMaskBtn.disabled = false;
            runDetectionBtn.disabled = false;
        } catch (error) { console.error('Error generating image:', error); alert(`Error: ${error.message}`);
        } finally { hideLoader(); }
    });

    // ... (The rest of the file, including STAGE 2, STAGE 3, and Global Listeners, is the same) ...
    generateGTMaskBtn.addEventListener('click', async () => {
        if (!analysisState.rawBounds) { alert('Generate a satellite image first.'); return; }
        if (!drawnRectangle) { alert('Error: The drawn rectangle reference was lost. Please draw a new one.'); return; }
    
        showLoader('Generating Ground Truth mask...');
        try {
            const bbox = drawnRectangle.getBounds().toBBoxString();
            const types = getSelectedRoadTypes();
            const { lat_min, lat_max, lon_min, lon_max } = analysisState.rawBounds;
            const image_bounds = `${lat_min},${lat_max},${lon_min},${lon_max}`;
    
            const url = `${API_BASE_URL}/api/generate_osm_mask?bbox=${bbox}&types=${types}&image_bounds=${image_bounds}`;
            
            const res = await fetch(url);
            if (!res.ok) throw new Error((await res.json()).error);
            const data = await res.json();
            
            if (analysisState.gtMaskLayer) analysisState.gtMaskLayer.remove();
            analysisState.gtMaskLayer = L.imageOverlay(data.maskUrl, analysisState.bounds, { opacity: 0.7 }).addTo(map);
            gtMaskControls.style.display = 'block';
    
        } catch (error) {
            console.error("GT Mask Error:", error);
            alert(`GT Mask generation failed: ${error.message}`);
        } finally {
            hideLoader();
        }
    });
    
    runDetectionBtn.addEventListener('click', async () => {
        if (!analysisState.imageUrl) { alert('Generate a satellite image first.'); return; }
        showLoader('Running road detection model...');
        try {
            const res = await fetch(`${API_BASE_URL}/api/get_predicted_roads?image_url=${analysisState.imageUrl}`);
            if (!res.ok) throw new Error((await res.json()).error);
            const data = await res.json();

            if (analysisState.predGraphLayer) analysisState.predGraphLayer.remove();
            if (analysisState.predMaskLayer) analysisState.predMaskLayer.remove();
            
            analysisState.predGraphLayer = L.geoJSON(data.geojson, { style: () => ({ color: '#00ffff', weight: 3 }) }).addTo(map);
            analysisState.predMaskLayer = L.imageOverlay(data.maskUrl, analysisState.bounds, { opacity: 0.7 }).addTo(map);
            detectionControls.style.display = 'block';
        } catch (error) { console.error("Detection Error:", error); alert(`Detection failed: ${error.message}`);
        } finally { hideLoader(); }
    });

    document.getElementById('baseMapToggle').addEventListener('change', (e) => map.toggleLayer(baseMapLayer, e.target.checked));
    opacitySlider.addEventListener('input', (e) => { if (analysisState.satLayer) analysisState.satLayer.setOpacity(e.target.value); });
    gtMaskOpacitySlider.addEventListener('input', (e) => { if (analysisState.gtMaskLayer) analysisState.gtMaskLayer.setOpacity(e.target.value); });
    predMaskOpacitySlider.addEventListener('input', (e) => { if (analysisState.predMaskLayer) analysisState.predMaskLayer.setOpacity(e.target.value); });
    
    L.Map.prototype.toggleLayer = (layer, show) => { if (layer) { if (show) map.addLayer(layer); else map.removeLayer(layer); } };
    toggleSat.addEventListener('change', (e) => map.toggleLayer(analysisState.satLayer, e.target.checked));
    toggleGtMask.addEventListener('change', (e) => map.toggleLayer(analysisState.gtMaskLayer, e.target.checked));
    togglePredMask.addEventListener('change', (e) => map.toggleLayer(analysisState.predMaskLayer, e.target.checked));
    togglePredGraph.addEventListener('change', (e) => map.toggleLayer(analysisState.predGraphLayer, e.target.checked));

    document.querySelectorAll('.collapsible-header').forEach(header => {
        header.addEventListener('click', () => {
            const content = header.nextElementSibling;
            const arrow = header.querySelector('.arrow');
            const isActive = content.style.display === 'block';
            content.style.display = isActive ? 'none' : 'block';
            arrow.textContent = isActive ? '▼' : '▲';
        });
    });

});