document.addEventListener('DOMContentLoaded', () => {
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

    const API_BASE_URL = 'http://localhost:4000';
    let drawnRectangle = null;

    // MODIFIED: We now use ...Group to hold the two layers (casing + main line)
    let analysisState = {
        gtMaskLayer: null,
        damageLayerGroup: null, 
        pre: { satLayer: null, predMaskLayer: null, predGraphGroup: null, imageUrl: null, bounds: null, rawBounds: null, satellite: null },
        post: { satLayer: null, predMaskLayer: null, predGraphGroup: null, imageUrl: null, bounds: null, rawBounds: null, satellite: null }
    };

    const loader = document.getElementById('loader');
    const generateImagesBtn = document.getElementById('generateImagesBtn');
    const generateGTMaskBtn = document.getElementById('generateGTMaskBtn');
    const updateRoadsBtn = document.getElementById('updateRoadsBtn');
    const compareRoadsBtn = document.getElementById('compareRoadsBtn');
    const startDateInput = document.getElementById('startDate');
    const midDateInput = document.getElementById('midDate');
    const endDateInput = document.getElementById('endDate');
    const satelliteSelect = document.getElementById('satelliteSelect');
    const sentinel1Options = document.getElementById('sentinel1Options');
    const sentinel2Options = document.getElementById('sentinel2Options');
    const sentinel2NIROptions = document.getElementById('sentinel2NIROptions');
    const cloudCoverSlider = document.getElementById('cloudCoverSlider');
    const cloudCoverValue = document.getElementById('cloudCoverValue');
    const cloudCoverSliderNIR = document.getElementById('cloudCoverSliderNIR');
    const cloudCoverValueNIR = document.getElementById('cloudCoverValueNIR');
    const gtMaskControls = document.getElementById('gtMaskControls');
    const damageAnalysisControls = document.getElementById('damageAnalysisControls');

    const showLoader = (text = 'Processing...') => { loader.textContent = text; loader.style.display = 'flex'; };
    const hideLoader = () => loader.style.display = 'none';

    const resetWorkflow = (fullReset = false) => {
        if (analysisState.gtMaskLayer) analysisState.gtMaskLayer.remove();
        if (analysisState.damageLayerGroup) analysisState.damageLayerGroup.remove();
        analysisState.gtMaskLayer = null;
        analysisState.damageLayerGroup = null;

        gtMaskControls.style.display = 'none';
        damageAnalysisControls.style.display = 'none';
        generateGTMaskBtn.disabled = true;
        compareRoadsBtn.disabled = true;

        ['pre', 'post'].forEach(prefix => {
            if (analysisState[prefix].satLayer) analysisState[prefix].satLayer.remove();
            if (analysisState[prefix].predMaskLayer) analysisState[prefix].predMaskLayer.remove();
            if (analysisState[prefix].predGraphGroup) analysisState[prefix].predGraphGroup.remove();

            analysisState[prefix] = { satLayer: null, predMaskLayer: null, predGraphGroup: null, imageUrl: null, bounds: null, rawBounds: null, satellite: null };
            document.getElementById(`${prefix}EventControls`).style.display = 'none';
            document.getElementById(`${prefix}DetectionControls`).style.display = 'none';
            document.getElementById(`run${prefix.charAt(0).toUpperCase() + prefix.slice(1)}DetectionBtn`).disabled = true;
        });

        document.getElementById('toggleGtMask').checked = true;
        document.getElementById('gtMaskOpacitySlider').value = 0.7;
        document.getElementById('togglePreSat').checked = true;
        document.getElementById('preOpacitySlider').value = 0.8;
        document.getElementById('togglePrePredMask').checked = true;
        document.getElementById('prePredMaskOpacitySlider').value = 0.7;
        document.getElementById('togglePrePredGraph').checked = true;
        document.getElementById('togglePostSat').checked = true;
        document.getElementById('postOpacitySlider').value = 0.8;
        document.getElementById('togglePostPredMask').checked = true;
        document.getElementById('postPredMaskOpacitySlider').value = 0.7;
        document.getElementById('togglePostPredGraph').checked = true;
        document.getElementById('toggleDamageLayer').checked = true;
        document.getElementById('damageOpacitySlider').value = 0.9;

        if (fullReset) {
            generateImagesBtn.disabled = true;
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
    const oneMonthAgo = new Date(new Date().setMonth(today.getMonth() - 1));
    const twoMonthsAgo = new Date(new Date().setMonth(today.getMonth() - 2));
    startDateInput.value = twoMonthsAgo.toISOString().split('T')[0];
    midDateInput.value = oneMonthAgo.toISOString().split('T')[0];
    endDateInput.value = today.toISOString().split('T')[0];

    map.on(L.Draw.Event.CREATED, (event) => {
        resetWorkflow(true);
        drawnRectangle = event.layer;
        drawnItems.addLayer(drawnRectangle);
        generateImagesBtn.disabled = false;
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
            generateImagesBtn.disabled = false;
            updateRoads();
        }
    });

    map.on(L.Draw.Event.DELETED, () => resetWorkflow(true));

    // CORRECTED: OSM roads are thinner and dashed
    let osmRoadsLayer = L.geoJSON(null, { 
        style: () => ({ color: '#ff0000', weight: 2, opacity: 0.8, dashArray: '5, 10' }) 
    }).addTo(map);

    const getSelectedRoadTypes = () => Array.from(document.querySelectorAll('.road-type-filter:checked')).map(cb => cb.value).join(',');
    
    const updateRoads = async () => {
        if (!drawnRectangle) return;
        const bbox = drawnRectangle.getBounds().toBBoxString();
        const types = getSelectedRoadTypes();
        const historicalDate = midDateInput.value;
        if (!types) { osmRoadsLayer.clearLayers(); return; }
        showLoader('Fetching OSM Roads...');
        try {
            const params = new URLSearchParams({ bbox: bbox, types: types, date: historicalDate });
            const response = await fetch(`${API_BASE_URL}/api/get_roads?${params.toString()}`);
            if (!response.ok) throw new Error('Failed to fetch OSM roads');
            osmRoadsLayer.clearLayers().addData(await response.json());
        } catch (error) { console.error('Error updating roads:', error); alert('Could not load OSM road data.');
        } finally { hideLoader(); }
    };
    updateRoadsBtn.addEventListener('click', updateRoads);

    generateImagesBtn.addEventListener('click', async () => {
        if (!drawnRectangle) { alert('Please draw a rectangle on the map first.'); return; }
        resetWorkflow();
        const bbox = drawnRectangle.getBounds().toBBoxString();
        const satellite = satelliteSelect.value;
        let cloudCover = undefined;
        if (satellite === 'sentinel_2') {
            cloudCover = cloudCoverSlider.value;
        } else if (satellite === 'sentinel_2_nir') {
            cloudCover = cloudCoverSliderNIR.value;
        }

        const commonParams = {
            bbox: bbox,
            satellite: satellite,
            cloudy_pixel_percentage: cloudCover,
            polarization: satellite === 'sentinel_1' ? polarizationSelect.value : undefined
        };
        try {
            showLoader('Fetching Pre-Event image...');
            const preImageResult = await fetchAndProcessImage('pre', { ...commonParams, start_date: startDateInput.value, end_date: midDateInput.value, target_date: midDateInput.value });
            if (preImageResult) {
                document.getElementById('preEventControls').style.display = 'block';
                document.getElementById('runPreDetectionBtn').disabled = false;
            }
            showLoader('Fetching Post-Event image...');
            const postImageResult = await fetchAndProcessImage('post', { ...commonParams, start_date: midDateInput.value, end_date: endDateInput.value, target_date: midDateInput.value });
            if (postImageResult) {
                document.getElementById('postEventControls').style.display = 'block';
                document.getElementById('runPostDetectionBtn').disabled = false;
            }
            if (preImageResult || postImageResult) {
                generateGTMaskBtn.disabled = false;
                map.fitBounds(analysisState.pre.bounds || analysisState.post.bounds);
            } else {
                alert('Could not find any images for the selected dates and area.');
            }
        } catch (error) { console.error('Error generating images:', error); alert(`Error: ${error.message}`);
        } finally { hideLoader(); }
    });

    generateGTMaskBtn.addEventListener('click', async () => {
        const imageState = analysisState.pre.rawBounds ? analysisState.pre : analysisState.post;
        if (!imageState.rawBounds) { alert('Generate a satellite image first.'); return; }
        if (!drawnRectangle) { alert('Error: The drawn rectangle reference was lost. Please draw a new one.'); return; }
        showLoader('Generating Ground Truth mask...');
        try {
            const bbox = drawnRectangle.getBounds().toBBoxString();
            const types = getSelectedRoadTypes();
            const { lat_min, lat_max, lon_min, lon_max } = imageState.rawBounds;
            const image_bounds = `${lat_min},${lat_max},${lon_min},${lon_max}`;
            const url = `${API_BASE_URL}/api/generate_osm_mask?bbox=${bbox}&types=${types}&image_bounds=${image_bounds}`;
            const res = await fetch(url);
            if (!res.ok) throw new Error((await res.json()).error);
            const data = await res.json();
            if (analysisState.gtMaskLayer) analysisState.gtMaskLayer.remove();
            const bounds = imageState.bounds;
            analysisState.gtMaskLayer = L.imageOverlay(data.maskUrl, bounds, { opacity: 0.7 }).addTo(map);
            gtMaskControls.style.display = 'block';
        } catch (error) { console.error("GT Mask Error:", error); alert(`GT Mask generation failed: ${error.message}`);
        } finally { hideLoader(); }
    });

    async function fetchAndProcessImage(prefix, params) {
        const allParams = { ...params, prefix: prefix };
        const query = new URLSearchParams(Object.entries(allParams).filter(([_, v]) => v != null)).toString();
        try {
            const downloadRes = await fetch(`${API_BASE_URL}/api/download_satellite_image?${query}`);
            if (!downloadRes.ok) throw new Error((await downloadRes.json()).error);
            const downloadData = await downloadRes.json();
            showLoader(`Processing ${prefix}-event image...`);
            const processRes = await fetch(`${API_BASE_URL}/api/process_satellite_image?satellite=${params.satellite}&prefix=${prefix}`);
            if (!processRes.ok) throw new Error((await processRes.json()).error);
            const processData = await processRes.json();
            Object.assign(analysisState[prefix], processData);
            analysisState[prefix].satellite = params.satellite;
            analysisState[prefix].satLayer = L.imageOverlay(processData.imageUrl, processData.bounds, { opacity: 0.8 }).addTo(map);
            document.getElementById(`${prefix}ImageDateDisplay`).innerHTML = `<b>Image Date:</b> ${downloadData.imageDate}`;
            return true;
        } catch (error) {
            console.error(`Error for ${prefix}-event image:`, error);
            alert(`Could not process ${prefix}-event image: ${error.message}`);
            return false;
        }
    }

    // CORRECTED: This function now correctly creates two layers for casing.
    async function runDetection(prefix) {
        if (!analysisState[prefix].imageUrl) { alert(`Generate the ${prefix}-event satellite image first.`); return; }
        showLoader(`Running ${prefix}-event detection...`);
        try {
            const res = await fetch(`${API_BASE_URL}/api/get_predicted_roads?image_url=${analysisState[prefix].imageUrl}&prefix=${prefix}`);
            if (!res.ok) throw new Error((await res.json()).error);
            const data = await res.json();

            if (analysisState[prefix].predGraphGroup) analysisState[prefix].predGraphGroup.remove();
            if (analysisState[prefix].predMaskLayer) analysisState[prefix].predMaskLayer.remove();
            
            // Define the styles for casing and the main line
            const styles = {
                pre: { color: '#00ffff', weight: 3 },  // Cyan for pre-event
                post: { color: '#ff00ff', weight: 3 } // Magenta for post-event
            };
            const casingStyle = { color: '#000000', weight: styles[prefix].weight + 2, opacity: 0.7 };
            const mainStyle = { ...styles[prefix], opacity: 1 };

            // Create two separate GeoJSON layers
            const casingLayer = L.geoJSON(data.geojson, { style: casingStyle });
            const mainLayer = L.geoJSON(data.geojson, { style: mainStyle });

            // Group them together to be treated as a single entity
            analysisState[prefix].predGraphGroup = L.featureGroup([casingLayer, mainLayer]).addTo(map);

            analysisState[prefix].predMaskLayer = L.imageOverlay(data.maskUrl, analysisState[prefix].bounds, { opacity: 0.7 }).addTo(map);
            document.getElementById(`${prefix}DetectionControls`).style.display = 'block';

            if (analysisState.pre.predGraphGroup && analysisState.post.predGraphGroup) {
                compareRoadsBtn.disabled = false;
            }
        } catch (error) { console.error(`${prefix} Detection Error:`, error); alert(`Detection failed: ${error.message}`);
        } finally { hideLoader(); }
    }
    
    // CORRECTED: This function also uses the two-layer method for casing.
    compareRoadsBtn.addEventListener('click', async () => {
        if (!drawnRectangle) { alert('Please draw an area of interest first.'); return; }
        if (!analysisState.pre.predGraphGroup || !analysisState.post.predGraphGroup) {
            alert('Please run both pre and post-event detection first.'); return;
        }

        showLoader('Analyzing road damage...');
        try {
            const bbox = drawnRectangle.getBounds().toBBoxString();
            const types = getSelectedRoadTypes();
            const url = `${API_BASE_URL}/api/compare_roads?bbox=${bbox}&types=${types}`;
            
            const res = await fetch(url);
            if (!res.ok) throw new Error((await res.json()).error);
            const data = await res.json();

            if (analysisState.damageLayerGroup) analysisState.damageLayerGroup.remove();
            
            // Define styles for the damaged roads layer
            const casingStyle = { color: '#000000', weight: 7, opacity: 0.8 };
            const mainStyle = { color: '#FFD700', weight: 5, opacity: 1 };

            // Create the two layers and group them
            const casingLayer = L.geoJSON(data.geojson, { style: casingStyle });
            const mainLayer = L.geoJSON(data.geojson, { style: mainStyle });
            analysisState.damageLayerGroup = L.featureGroup([casingLayer, mainLayer]).addTo(map);
            
            damageAnalysisControls.style.display = 'block';

            // --- UI improvement to focus the user ---
            if (analysisState.pre.predGraphGroup) map.removeLayer(analysisState.pre.predGraphGroup);
            document.getElementById('togglePrePredGraph').checked = false;
            if (analysisState.post.predGraphGroup) map.removeLayer(analysisState.post.predGraphGroup);
            document.getElementById('togglePostPredGraph').checked = false;
            map.addLayer(analysisState.damageLayerGroup);
            document.getElementById('toggleDamageLayer').checked = true;

        } catch (error) {
            console.error('Damage Analysis Error:', error);
            alert(`Damage analysis failed: ${error.message}`);
        } finally {
            hideLoader();
        }
    });

    document.getElementById('runPreDetectionBtn').addEventListener('click', () => runDetection('pre'));
    document.getElementById('runPostDetectionBtn').addEventListener('click', () => runDetection('post'));

    document.getElementById('baseMapToggle').addEventListener('change', (e) => map.toggleLayer(baseMapLayer, e.target.checked));
    document.getElementById('roadToggle').addEventListener('change', (e) => map.toggleLayer(osmRoadsLayer, e.target.checked));
    document.getElementById('toggleGtMask').addEventListener('change', (e) => map.toggleLayer(analysisState.gtMaskLayer, e.target.checked));
    document.getElementById('gtMaskOpacitySlider').addEventListener('input', (e) => { if (analysisState.gtMaskLayer) analysisState.gtMaskLayer.setOpacity(e.target.value); });

    // MODIFIED: Opacity slider now acts on the group
    document.getElementById('toggleDamageLayer').addEventListener('change', (e) => map.toggleLayer(analysisState.damageLayerGroup, e.target.checked));
    document.getElementById('damageOpacitySlider').addEventListener('input', (e) => { if (analysisState.damageLayerGroup) analysisState.damageLayerGroup.setOpacity(e.target.value); });


    L.Map.prototype.toggleLayer = (layer, show) => { if (layer) { if (show) map.addLayer(layer); else map.removeLayer(layer); } };
    
    ['pre', 'post'].forEach(prefix => {
        document.getElementById(`toggle${prefix.charAt(0).toUpperCase() + prefix.slice(1)}Sat`).addEventListener('change', (e) => map.toggleLayer(analysisState[prefix].satLayer, e.target.checked));
        document.getElementById(`${prefix}OpacitySlider`).addEventListener('input', (e) => { if (analysisState[prefix].satLayer) analysisState[prefix].satLayer.setOpacity(e.target.value); });
        
        document.getElementById(`toggle${prefix.charAt(0).toUpperCase() + prefix.slice(1)}PredMask`).addEventListener('change', (e) => map.toggleLayer(analysisState[prefix].predMaskLayer, e.target.checked));
        document.getElementById(`${prefix}PredMaskOpacitySlider`).addEventListener('input', (e) => { if (analysisState[prefix].predMaskLayer) analysisState[prefix].predMaskLayer.setOpacity(e.target.value); });
        
        // MODIFIED: Toggle and opacity now act on the ...Group
        document.getElementById(`toggle${prefix.charAt(0).toUpperCase() + prefix.slice(1)}PredGraph`).addEventListener('change', (e) => map.toggleLayer(analysisState[prefix].predGraphGroup, e.target.checked));
    });

    document.querySelectorAll('.collapsible-header').forEach(header => {
        header.addEventListener('click', () => {
            const content = header.nextElementSibling;
            const arrow = header.querySelector('.arrow');
            const isActive = content.style.display === 'block';
            content.style.display = isActive ? 'none' : 'block';
            arrow.textContent = isActive ? '▼' : '▲';
        });
    });

    satelliteSelect.addEventListener('change', (e) => {
        const selected = e.target.value;
        sentinel1Options.style.display = selected === 'sentinel_1' ? 'block' : 'none';
        sentinel2Options.style.display = selected === 'sentinel_2' ? 'block' : 'none';
        sentinel2NIROptions.style.display = selected === 'sentinel_2_nir' ? 'block' : 'none';
    });

    cloudCoverSlider.addEventListener('input', (e) => {
        cloudCoverValue.textContent = e.target.value;
    });

    cloudCoverSliderNIR.addEventListener('input', (e) => {
        cloudCoverValueNIR.textContent = e.target.value;
    });
});