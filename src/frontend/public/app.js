document.addEventListener('DOMContentLoaded', () => {
    // --- Leaflet Map Setup & Configuration ---
    const roadColors = {
        osm: getComputedStyle(document.documentElement).getPropertyValue('--osm-road-color').trim(),
        preEvent: getComputedStyle(document.documentElement).getPropertyValue('--pre-event-color').trim(),
        postEvent: getComputedStyle(document.documentElement).getPropertyValue('--post-event-color').trim(),
        damage: getComputedStyle(document.documentElement).getPropertyValue('--damage-color').trim(),
        casing: getComputedStyle(document.documentElement).getPropertyValue('--casing-color').trim()
    };

    const map = L.map('map').setView([39.217, -76.528], 15);
    let baseMapLayer = L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
        attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
    }).addTo(map);

    L.control.scale({ position: 'bottomleft', maxWidth: 200, metric: true, imperial: false }).addTo(map);

    const drawnItems = new L.FeatureGroup().addTo(map);
    map.addControl(new L.Control.Draw({
        edit: { featureGroup: drawnItems, remove: true },
        draw: {
            polygon: false, polyline: false, circle: false, marker: false, circlemarker: false,
            rectangle: { shapeOptions: { color: '#007bff' } }
        }
    }));

    // --- Global State and Constants ---
    const API_BASE_URL = 'http://localhost:4000';
    let drawnRectangle = null;
    let analysisState = {
        gtMaskLayer: null,
        damageLayerGroup: null, 
        pre: { satLayer: null, predMaskLayer: null, predGraphGroup: null, imageUrl: null, bounds: null, rawBounds: null, satellite: null },
        post: { satLayer: null, predMaskLayer: null, predGraphGroup: null, imageUrl: null, bounds: null, rawBounds: null, satellite: null }
    };

    // --- DOM Element References ---
    const loader = document.getElementById('loader');
    const generatePreImagesBtn = document.getElementById('generatePreImagesBtn');
    const generatePostImagesBtn = document.getElementById('generatePostImagesBtn');
    const generateGTMaskBtn = document.getElementById('generateGTMaskBtn');
    const updateRoadsBtn = document.getElementById('updateRoadsBtn');
    const compareRoadsBtn = document.getElementById('compareRoadsBtn');
    const startDateInput = document.getElementById('startDate');
    const midDateInput = document.getElementById('midDate');
    const endDateInput = document.getElementById('endDate');
    const gtMaskControls = document.getElementById('gtMaskControls');
    const damageAnalysisControls = document.getElementById('damageAnalysisControls');
    const preSatControls = document.getElementById('preSatControls');
    const postSatControls = document.getElementById('postSatControls');

    // Dynamic UI Elements
    const dataSourceSelect = document.getElementById('dataSourceSelect');
    const geeOptionsContainer = document.getElementById('geeOptionsContainer');
    const planetOptionsContainer = document.getElementById('planetOptionsContainer');
    const maxarOptionsContainer = document.getElementById('maxarOptionsContainer');
    const geeProjectInput = document.getElementById('geeProjectInput');
    const planetApiKeyInput = document.getElementById('planetApiKeyInput');
    const maxarApiKeyInput = document.getElementById('maxarApiKeyInput');
    const satelliteSelect = document.getElementById('satelliteSelect');
    const sentinel1Options = document.getElementById('sentinel1Options');
    const sentinel2Options = document.getElementById('sentinel2Options');
    const sentinel2NIROptions = document.getElementById('sentinel2NIROptions');
    const maxarImageryOptions = document.getElementById('maxarImageryOptions');
    const cloudCoverSlider = document.getElementById('cloudCoverSlider');
    const cloudCoverValue = document.getElementById('cloudCoverValue');
    const cloudCoverSliderNIR = document.getElementById('cloudCoverSliderNIR');
    const cloudCoverValueNIR = document.getElementById('cloudCoverValueNIR');
    const maxarCloudCoverSlider = document.getElementById('maxarCloudCoverSlider');
    const maxarCloudCoverValue = document.getElementById('maxarCloudCoverValue');
    
    // --- Satellite Data Configuration ---
    const SATELLITE_OPTIONS_MAP = {
        gee: [
            { value: 'sentinel_2', text: 'Sentinel-2' },
            { value: 'sentinel_2_nir', text: 'Sentinel-2 NIR' },
            { value: 'sentinel_1', text: 'Sentinel-1' }
        ],
        maxar: [
            { value: 'maxar_imagery', text: 'Maxar Imagery' }
        ]
    };

    // --- Helper Functions ---
    const showLoader = (text = 'Processing...') => { loader.textContent = text; loader.style.display = 'flex'; };
    const hideLoader = () => loader.style.display = 'none';

    const downloadLinks = {
        osmGeoJSON: document.getElementById('download-osm-geojson'),
        gtMask: document.getElementById('download-gt-mask'),
        preSat: document.getElementById('download-pre-sat'),
        preMask: document.getElementById('download-pre-mask'),
        preGeoJSON: document.getElementById('download-pre-geojson'),
        postSat: document.getElementById('download-post-sat'),
        postMask: document.getElementById('download-post-mask'),
        postGeoJSON: document.getElementById('download-post-geojson'),
        damageGeoJSON: document.getElementById('download-damage-geojson'),
    };

    const resetWorkflow = (fullReset = false) => {
        if (analysisState.gtMaskLayer) analysisState.gtMaskLayer.remove();
        if (analysisState.damageLayerGroup) analysisState.damageLayerGroup.remove();
        analysisState.gtMaskLayer = null;
        analysisState.damageLayerGroup = null;

        gtMaskControls.style.display = 'none';
        damageAnalysisControls.style.display = 'none';
        preSatControls.style.display = 'none';
        postSatControls.style.display = 'none';
        generateGTMaskBtn.disabled = true;
        compareRoadsBtn.disabled = true;

        ['pre', 'post'].forEach(prefix => {
            if (analysisState[prefix].satLayer) analysisState[prefix].satLayer.remove();
            if (analysisState[prefix].predMaskLayer) analysisState[prefix].predMaskLayer.remove();
            if (analysisState[prefix].predGraphGroup) analysisState[prefix].predGraphGroup.remove();

            analysisState[prefix] = { satLayer: null, predMaskLayer: null, predGraphGroup: null, imageUrl: null, bounds: null, rawBounds: null, satellite: null };
            document.getElementById(`${prefix}DetectionControls`).style.display = 'none';
            document.getElementById(`run${prefix.charAt(0).toUpperCase() + prefix.slice(1)}DetectionBtn`).disabled = true;
        });
        
        // Reset toggles and sliders to their default states
        document.getElementById('toggleGtMask').checked = false;
        document.getElementById('gtMaskOpacitySlider').value = 1.0;
        document.getElementById('togglePreSat').checked = true;
        document.getElementById('preOpacitySlider').value = 1.0;
        document.getElementById('togglePrePredMask').checked = false;
        document.getElementById('prePredMaskOpacitySlider').value = 1.0;
        document.getElementById('togglePrePredGraph').checked = true;
        document.getElementById('togglePostSat').checked = true;
        document.getElementById('postOpacitySlider').value = 1.0;
        document.getElementById('togglePostPredMask').checked = false;
        document.getElementById('postPredMaskOpacitySlider').value = 1.0;
        document.getElementById('togglePostPredGraph').checked = true;
        document.getElementById('toggleDamageLayer').checked = true;

        Object.values(downloadLinks).forEach(link => {
            link.classList.add('disabled');
            link.href = '#';
            link.removeAttribute('download');
        });

        if (fullReset) {
            if(drawnItems) drawnItems.clearLayers();
            if(osmRoadsLayer) osmRoadsLayer.clearLayers();
            drawnRectangle = null;
        }
    };
    
    function resetPreLayers() {
        if (analysisState.pre.satLayer) analysisState.pre.satLayer.remove();
        if (analysisState.pre.predMaskLayer) analysisState.pre.predMaskLayer.remove();
        if (analysisState.pre.predGraphGroup) analysisState.pre.predGraphGroup.remove();
        analysisState.pre = { satLayer: null, predMaskLayer: null, predGraphGroup: null, imageUrl: null, bounds: null, rawBounds: null, satellite: null };
        preSatControls.style.display = 'none';
        document.getElementById('preDetectionControls').style.display = 'none';
        document.getElementById('runPreDetectionBtn').disabled = true;
    }
    
    function resetPostLayers() {
        if (analysisState.post.satLayer) analysisState.post.satLayer.remove();
        if (analysisState.post.predMaskLayer) analysisState.post.predMaskLayer.remove();
        if (analysisState.post.predGraphGroup) analysisState.post.predGraphGroup.remove();
        analysisState.post = { satLayer: null, predMaskLayer: null, predGraphGroup: null, imageUrl: null, bounds: null, rawBounds: null, satellite: null };
        postSatControls.style.display = 'none';
        document.getElementById('postDetectionControls').style.display = 'none';
        document.getElementById('runPostDetectionBtn').disabled = true;
    }

    const updateRectangleInfo = (layer) => {
        const bounds = layer.getBounds();
        const content = `<b>Width:</b> ${bounds.getSouthWest().distanceTo(bounds.getSouthEast()).toFixed(0)} m<br><b>Height:</b> ${bounds.getSouthWest().distanceTo(bounds.getNorthWest()).toFixed(0)} m`;
        layer.bindPopup(content).openPopup();
    };

    // --- App Initialization ---
    startDateInput.value = '2024-02-01';
    midDateInput.value = '2024-03-26';
    endDateInput.value = '2024-05-01';

    // --- Map Event Listeners ---
    map.on(L.Draw.Event.CREATED, (event) => {
        resetWorkflow(true);
        drawnRectangle = event.layer;
        drawnItems.addLayer(drawnRectangle);
        generatePreImagesBtn.disabled = false;
        generatePostImagesBtn.disabled = false;
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
            generatePreImagesBtn.disabled = false;
            generatePostImagesBtn.disabled = false;
            updateRoads();
        }
    });

    map.on(L.Draw.Event.DELETED, () => resetWorkflow(true));

    // --- OSM Roads Logic ---
    let osmRoadsLayer = L.geoJSON(null, {
        style: () => ({ color: roadColors.osm, weight: 2, opacity: 0.8, dashArray: '5, 10' })
    }).addTo(map);

    const getSelectedRoadTypes = () => Array.from(document.querySelectorAll('.road-type-filter:checked')).map(cb => cb.value).join(',');

    const updateRoads = async () => {
        if (!drawnRectangle) return;
        const bbox = drawnRectangle.getBounds().toBBoxString();
        const types = getSelectedRoadTypes();
        const historicalDate = startDateInput.value;
        if (!types) { osmRoadsLayer.clearLayers(); return; }
        showLoader('Fetching OSM Roads...');
        try {
            const params = new URLSearchParams({ bbox: bbox, types: types, date: historicalDate });
            const response = await fetch(`${API_BASE_URL}/api/get_roads?${params.toString()}`);
            if (!response.ok) throw new Error('Failed to fetch OSM roads');
            const geojsonData = await response.json();
            osmRoadsLayer.clearLayers().addData(geojsonData);

            const blob = new Blob([JSON.stringify(geojsonData, null, 2)], { type: 'application/json' });
            downloadLinks.osmGeoJSON.href = URL.createObjectURL(blob);
            downloadLinks.osmGeoJSON.download = `osm_roads_${historicalDate}.geojson`;
            downloadLinks.osmGeoJSON.classList.remove('disabled');

        } catch (error) {
            console.error('Error updating roads:', error);
            alert('Could not load OSM road data.');
        } finally {
            hideLoader();
        }
    };
    updateRoadsBtn.addEventListener('click', updateRoads);

    // --- Dynamic UI Logic ---
    dataSourceSelect.addEventListener('change', () => {
        const selectedProvider = dataSourceSelect.value;
        document.querySelectorAll('.provider-options').forEach(el => el.style.display = 'none');
        
        if (selectedProvider === 'gee') geeOptionsContainer.style.display = 'block';
        else if (selectedProvider === 'maxar') maxarOptionsContainer.style.display = 'block';
        else if (selectedProvider === 'planet') planetOptionsContainer.style.display = 'block';
        
        updateSatelliteOptions(selectedProvider);
    });

    satelliteSelect.addEventListener('change', () => {
        const selectedSatellite = satelliteSelect.value;
        document.querySelectorAll('.satellite-options').forEach(el => el.style.display = 'none');

        if (selectedSatellite === 'sentinel_1') sentinel1Options.style.display = 'block';
        else if (selectedSatellite === 'sentinel_2') sentinel2Options.style.display = 'block';
        else if (selectedSatellite === 'sentinel_2_nir') sentinel2NIROptions.style.display = 'block';
        else if (selectedSatellite === 'maxar_imagery') maxarImageryOptions.style.display = 'block';
    });

    function updateSatelliteOptions(provider) {
        const options = SATELLITE_OPTIONS_MAP[provider] || [];
        satelliteSelect.innerHTML = '';

        options.forEach(opt => {
            const optionElement = document.createElement('option');
            optionElement.value = opt.value;
            optionElement.textContent = opt.text;
            satelliteSelect.appendChild(optionElement);
        });
        satelliteSelect.dispatchEvent(new Event('change'));
    }

    // --- Main Workflow Logic ---
    generatePreImagesBtn.addEventListener('click', async () => {
        if (!drawnRectangle) { alert('Please draw a rectangle on the map first.'); return; }
        resetPreLayers();
        const requestBody = buildApiRequestBody('pre');
        if (!requestBody) return;

        try {
            showLoader('Fetching Pre-Event image...');
            const preImageResult = await fetchAndProcessImage('pre', requestBody);
            if (preImageResult) {
                preSatControls.style.display = 'block';
                document.getElementById('runPreDetectionBtn').disabled = false;
                generateGTMaskBtn.disabled = false;
                map.fitBounds(analysisState.pre.bounds || analysisState.post.bounds);
            } else {
                alert('Could not find any images for the selected dates and area.');
            }
        } catch (error) { console.error('Error generating images:', error); alert(`Error: ${error.message}`);
        } finally { hideLoader(); }
    });

    generatePostImagesBtn.addEventListener('click', async () => {
        if (!drawnRectangle) { alert('Please draw a rectangle on the map first.'); return; }
        resetPostLayers();
        const requestBody = buildApiRequestBody('post');
        if (!requestBody) return;

        try {
            showLoader('Fetching Post-Event image...');
            const postImageResult = await fetchAndProcessImage('post', requestBody);
            if (postImageResult) {
                postSatControls.style.display = 'block';
                document.getElementById('runPostDetectionBtn').disabled = false;
                map.fitBounds(analysisState.post.bounds || analysisState.pre.bounds);
            } else {
                alert('Could not find any images for the selected dates and area.');
            }
        } catch (error) { console.error('Error generating images:', error); alert(`Error: ${error.message}`);
        } finally { hideLoader(); }
    });
    
    function buildApiRequestBody(prefix) {
        const bbox = drawnRectangle.getBounds().toBBoxString();
        const source_provider = dataSourceSelect.value;
        const satellite = satelliteSelect.value;
        
        let credentials = {};
        if (source_provider === 'gee') {
            credentials.project_id = geeProjectInput.value.trim().replace(/["']/g, "");
            if (!credentials.project_id) {
                alert('Please enter a Google Earth Engine Project ID.');
                return null;
            }
        } else if (source_provider === 'maxar') {
            credentials.api_key = maxarApiKeyInput.value.trim();
            if (!credentials.api_key) {
                alert('Please enter your Maxar API Key.');
                return null;
            }
        }

        let options = { satellite: satellite };
        if (satellite === 'sentinel_2') options.cloudy_pixel_percentage = cloudCoverSlider.value;
        else if (satellite === 'sentinel_2_nir') options.cloudy_pixel_percentage = cloudCoverSliderNIR.value;
        else if (satellite === 'sentinel_1') options.polarization = document.getElementById('polarizationSelect').value;
        else if (satellite === 'maxar_imagery') options.cloud_cover = maxarCloudCoverSlider.value;
        
        return {
            bbox: bbox,
            start_date: (prefix === 'pre' ? startDateInput.value : midDateInput.value),
            end_date: (prefix === 'pre' ? midDateInput.value : endDateInput.value),
            target_date: midDateInput.value,
            prefix: prefix,
            source_provider: source_provider,
            credentials: credentials,
            options: options
        };
    }

    async function fetchAndProcessImage(prefix, params) {
        try {
            const downloadRes = await fetch(`${API_BASE_URL}/api/download_satellite_image`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(params)
            });

            if (!downloadRes.ok) throw new Error((await downloadRes.json()).error);
            const downloadData = await downloadRes.json();
            
            showLoader(`Processing ${prefix}-event image...`);
            const processRes = await fetch(`${API_BASE_URL}/api/process_satellite_image?satellite=${params.options.satellite}&prefix=${prefix}`);

            if (!processRes.ok) throw new Error((await processRes.json()).error);
            const processData = await processRes.json();
            
            Object.assign(analysisState[prefix], processData);
            analysisState[prefix].satellite = params.options.satellite;
            analysisState[prefix].satLayer = L.imageOverlay(processData.imageUrl, processData.bounds, { opacity: 1.0 }).addTo(map);
            document.getElementById(`${prefix}ImageDateDisplay`).innerHTML = `<b>Image Date:</b> ${downloadData.imageDate}`;

            const linkElement = (prefix === 'pre') ? downloadLinks.preSat : downloadLinks.postSat;
            linkElement.href = processData.imageUrl;
            linkElement.download = `${prefix}_event_satellite.png`;
            linkElement.classList.remove('disabled');

            return true;
        } catch (error) {
            console.error(`Error for ${prefix}-event image:`, error);
            alert(`Could not process ${prefix}-event image: ${error.message}`);
            return false;
        }
    }

    generateGTMaskBtn.addEventListener('click', async () => {
        const imageState = analysisState.pre.rawBounds ? analysisState.pre : analysisState.post;
        if (!imageState.rawBounds) {
            alert('Generate a satellite image first.');
            return;
        }

        const osmData = osmRoadsLayer.toGeoJSON();
        if (osmData.features.length === 0) {
            alert('No OSM roads are loaded on the map. Please fetch roads first.');
            return;
        }

        showLoader('Generating Ground Truth mask...');
        try {
            const { lat_min, lat_max, lon_min, lon_max } = imageState.rawBounds;
            const image_bounds = `${lat_min},${lat_max},${lon_min},${lon_max}`;

            const res = await fetch(`${API_BASE_URL}/api/generate_osm_mask`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    osm_data: osmData,
                    image_bounds: image_bounds
                })
            });

            if (!res.ok) throw new Error((await res.json()).error);
            const data = await res.json();

            if (analysisState.gtMaskLayer) analysisState.gtMaskLayer.remove();
            // Create the layer but do not add it to the map by default
            analysisState.gtMaskLayer = L.imageOverlay(data.maskUrl, imageState.bounds, { opacity: 1.0 });
            gtMaskControls.style.display = 'block';
            document.getElementById('toggleGtMask').checked = false; // Ensure toggle is off

            downloadLinks.gtMask.href = data.maskUrl;
            downloadLinks.gtMask.download = `ground_truth_mask.png`;
            downloadLinks.gtMask.classList.remove('disabled');

        } catch (error) {
            console.error("GT Mask Error:", error);
            alert(`GT Mask generation failed: ${error.message}`);
        } finally {
            hideLoader();
        }
    });

    async function runDetection(prefix) {
        if (!analysisState[prefix].imageUrl) { alert(`Generate the ${prefix}-event satellite image first.`); return; }
        showLoader(`Running ${prefix}-event detection...`);
        try {
            const res = await fetch(`${API_BASE_URL}/api/get_predicted_roads?image_url=${analysisState[prefix].imageUrl}&prefix=${prefix}`);
            if (!res.ok) throw new Error((await res.json()).error);
            const data = await res.json();

            if (analysisState[prefix].predGraphGroup) analysisState[prefix].predGraphGroup.remove();
            if (analysisState[prefix].predMaskLayer) analysisState[prefix].predMaskLayer.remove();

            const styles = {
                pre: { color: roadColors.preEvent, weight: 3 },
                post: { color: roadColors.postEvent, weight: 3 }
            };
            const casingStyle = { color: roadColors.casing, weight: styles[prefix].weight + 2, opacity: 0.7 };
            const mainStyle = { ...styles[prefix], opacity: 1 };

            const casingLayer = L.geoJSON(data.geojson, { style: casingStyle });
            const mainLayer = L.geoJSON(data.geojson, { style: mainStyle });

            analysisState[prefix].predGraphGroup = L.featureGroup([casingLayer, mainLayer]).addTo(map);
            
            // Create the mask layer but do not add it to the map by default
            analysisState[prefix].predMaskLayer = L.imageOverlay(data.maskUrl, analysisState[prefix].bounds, { opacity: 1.0 });
            document.getElementById(`${prefix}DetectionControls`).style.display = 'block';
            document.getElementById(`toggle${prefix.charAt(0).toUpperCase() + prefix.slice(1)}PredMask`).checked = false; // Ensure toggle is off

            if (analysisState.pre.predGraphGroup && analysisState.post.predGraphGroup) {
                compareRoadsBtn.disabled = false;
            }

            const maskLink = (prefix === 'pre') ? downloadLinks.preMask : downloadLinks.postMask;
            maskLink.href = data.maskUrl;
            maskLink.download = `${prefix}_event_prediction_mask.png`;
            maskLink.classList.remove('disabled');

            const geojsonLink = (prefix === 'pre') ? downloadLinks.preGeoJSON : downloadLinks.postGeoJSON;
            const blob = new Blob([JSON.stringify(data.geojson, null, 2)], { type: 'application/json' });
            geojsonLink.href = URL.createObjectURL(blob);
            geojsonLink.download = `${prefix}_event_predicted_roads.geojson`;
            geojsonLink.classList.remove('disabled');

        } catch (error) { console.error(`${prefix} Detection Error:`, error); alert(`Detection failed: ${error.message}`);
        } finally { hideLoader(); }
    }

    compareRoadsBtn.addEventListener('click', async () => {
        if (!analysisState.pre.predGraphGroup || !analysisState.post.predGraphGroup) {
            alert('Please run both pre and post-event detection first.'); return;
        }

        const osmData = osmRoadsLayer.toGeoJSON();
        if (osmData.features.length === 0) {
            alert('No OSM roads are loaded. Please fetch roads to use as a damage reference.');
            return;
        }

        showLoader('Analyzing road damage...');
        try {
            const res = await fetch(`${API_BASE_URL}/api/compare_roads`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ osm_data: osmData })
            });

            if (!res.ok) throw new Error((await res.json()).error);
            const data = await res.json();

            if (analysisState.damageLayerGroup) analysisState.damageLayerGroup.remove();
            
            const casingStyle = { color: roadColors.casing, weight: 7, opacity: 0.8 };
            const mainStyle = { color: roadColors.damage, weight: 5, opacity: 1 };

            const casingLayer = L.geoJSON(data.geojson, { style: casingStyle });
            const mainLayer = L.geoJSON(data.geojson, { style: mainStyle });
            analysisState.damageLayerGroup = L.featureGroup([casingLayer, mainLayer]).addTo(map);
            
            damageAnalysisControls.style.display = 'block';

            if (analysisState.pre.predGraphGroup) map.removeLayer(analysisState.pre.predGraphGroup);
            if (analysisState.post.predGraphGroup) map.removeLayer(analysisState.post.predGraphGroup);
            map.addLayer(analysisState.damageLayerGroup);
            
            const blob = new Blob([JSON.stringify(data.geojson, null, 2)], { type: 'application/json' });
            downloadLinks.damageGeoJSON.href = URL.createObjectURL(blob);
            downloadLinks.damageGeoJSON.download = 'damaged_roads.geojson';
            downloadLinks.damageGeoJSON.classList.remove('disabled');

        } catch (error) {
            console.error('Damage Analysis Error:', error);
            alert(`Damage analysis failed: ${error.message}`);
        } finally {
            hideLoader();
        }
    });

    document.getElementById('runPreDetectionBtn').addEventListener('click', () => runDetection('pre'));
    document.getElementById('runPostDetectionBtn').addEventListener('click', () => runDetection('post'));

    // --- General UI Listeners ---
    document.getElementById('baseMapToggle').addEventListener('change', (e) => map.toggleLayer(baseMapLayer, e.target.checked));
    document.getElementById('roadToggle').addEventListener('change', (e) => map.toggleLayer(osmRoadsLayer, e.target.checked));
    document.getElementById('toggleGtMask').addEventListener('change', (e) => map.toggleLayer(analysisState.gtMaskLayer, e.target.checked));
    document.getElementById('gtMaskOpacitySlider').addEventListener('input', (e) => { if (analysisState.gtMaskLayer) analysisState.gtMaskLayer.setOpacity(e.target.value); });
    document.getElementById('toggleDamageLayer').addEventListener('change', (e) => map.toggleLayer(analysisState.damageLayerGroup, e.target.checked));
    
    ['pre', 'post'].forEach(prefix => {
        document.getElementById(`toggle${prefix.charAt(0).toUpperCase() + prefix.slice(1)}Sat`).addEventListener('change', (e) => map.toggleLayer(analysisState[prefix].satLayer, e.target.checked));
        document.getElementById(`${prefix}OpacitySlider`).addEventListener('input', (e) => { if (analysisState[prefix].satLayer) analysisState[prefix].satLayer.setOpacity(e.target.value); });
        
        document.getElementById(`toggle${prefix.charAt(0).toUpperCase() + prefix.slice(1)}PredMask`).addEventListener('change', (e) => map.toggleLayer(analysisState[prefix].predMaskLayer, e.target.checked));
        document.getElementById(`${prefix}PredMaskOpacitySlider`).addEventListener('input', (e) => { if (analysisState[prefix].predMaskLayer) analysisState[prefix].predMaskLayer.setOpacity(e.target.value); });
        
        document.getElementById(`toggle${prefix.charAt(0).toUpperCase() + prefix.slice(1)}PredGraph`).addEventListener('change', (e) => map.toggleLayer(analysisState[prefix].predGraphGroup, e.target.checked));
    });

    cloudCoverSlider.addEventListener('input', (e) => { cloudCoverValue.textContent = e.target.value; });
    cloudCoverSliderNIR.addEventListener('input', (e) => { cloudCoverValueNIR.textContent = e.target.value; });
    maxarCloudCoverSlider.addEventListener('input', (e) => { maxarCloudCoverValue.textContent = e.target.value; });
    
    document.querySelectorAll('.collapsible-header').forEach(header => {
        header.addEventListener('click', () => {
            const content = header.nextElementSibling;
            const arrow = header.querySelector('.arrow');
            content.style.display = content.style.display === 'block' ? 'none' : 'block';
            arrow.textContent = content.style.display === 'block' ? '▲' : '▼';
        });
    });

    L.Map.prototype.toggleLayer = function(layer, show) { if (layer) { if (show) this.addLayer(layer); else this.removeLayer(layer); } };

    // --- Final Initialization ---
    dataSourceSelect.dispatchEvent(new Event('change'));
});
