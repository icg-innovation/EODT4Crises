document.addEventListener('DOMContentLoaded', () => {
    // --- Leaflet Map Setup & Configuration ---
    const roadColors = {
        osm: getComputedStyle(document.documentElement).getPropertyValue('--osm-road-color').trim(),
        preEvent: getComputedStyle(document.documentElement).getPropertyValue('--pre-event-color').trim(),
        postEvent: getComputedStyle(document.documentElement).getPropertyValue('--post-event-color').trim(),
        damage: getComputedStyle(document.documentElement).getPropertyValue('--damage-color').trim(),
        casing: getComputedStyle(document.documentElement).getPropertyValue('--casing-color').trim()
    };

    const map = L.map('map', { zoomSnap: 0.25, zoomDelta: 0.25 }).setView([39.217, -76.528], 15);
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
    const isLocal = window.location.hostname === 'localhost' || window.location.hostname === '127.0.0.1';
    
    const API_BASE_URL = isLocal ? 'http://localhost:4000/api' : '/eodt4crises/api';
    const STATIC_BASE_URL = isLocal ? 'http://localhost:4000' : '/eodt4crises';

    let drawnRectangle = null;
    let analysisState = {
        gtMaskLayer: null,
        damageLayerGroup: null,
        pre: { satLayer: null, predMaskLayer: null, predGraphGroup: null, imageUrl: null, bounds: null, rawBounds: null, satellite: null },
        post: { satLayer: null, predMaskLayer: null, predGraphGroup: null, imageUrl: null, bounds: null, rawBounds: null, satellite: null },
        gpkg: { layers: [] }
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
    const localOptionsContainer = document.getElementById('localOptionsContainer');
    
    const geoPackageUploadInput = document.getElementById('geoPackageUpload');
    const preEventUploadInput = document.getElementById('preEventUpload');
    const postEventUploadInput = document.getElementById('postEventUpload');
    const disableNormalizationCheckbox = document.getElementById('disableNormalizationCheckbox');

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
    const greyscaleToggle = document.getElementById('greyscaleToggle');

    // --- Satellite Data Configuration ---
    const SATELLITE_OPTIONS_MAP = {
        gee: [ { value: 'sentinel_2', text: 'Sentinel-2' }, { value: 'sentinel_2_nir', text: 'Sentinel-2 NIR' }, { value: 'sentinel_1', text: 'Sentinel-1' } ],
        maxar: [ { value: 'maxar_imagery', text: 'Maxar Imagery' } ],
        local: [ { value: 'sentinel_2', text: 'Sentinel-2' }, { value: 'sentinel_2_nir', text: 'Sentinel-2 NIR' },
                 { value: 'sentinel_1', text: 'Sentinel-1' }, { value: 'maxar_imagery', text: 'Maxar Imagery' },
                 { value: 'capella', text: 'Capella' }]
    };

    // --- Helper Functions ---
    const showLoader = (text = 'Processing...') => { loader.textContent = text; loader.style.display = 'flex'; };
    const hideLoader = () => loader.style.display = 'none';

    const downloadLinks = {
        osmGeoJSON: document.getElementById('download-osm-geojson'),
        gtMask: document.getElementById('download-gt-mask'),
        preSat: document.getElementById('download-pre-sat'),
        preGeoTiff: document.getElementById('download-pre-geotiff'),
        preMask: document.getElementById('download-pre-mask'),
        preGeoJSON: document.getElementById('download-pre-geojson'),
        postSat: document.getElementById('download-post-sat'),
        postGeoTiff: document.getElementById('download-post-geotiff'),
        postMask: document.getElementById('download-post-mask'),
        postGeoJSON: document.getElementById('download-post-geojson'),
        damageGeoJSON: document.getElementById('download-damage-geojson'),
    };
    
    function resetPreLayers() {
        if (analysisState.pre.satLayer) analysisState.pre.satLayer.remove();
        if (analysisState.pre.predMaskLayer) analysisState.pre.predMaskLayer.remove();
        if (analysisState.pre.predGraphGroup) analysisState.pre.predGraphGroup.remove();
        analysisState.pre = { satLayer: null, predMaskLayer: null, predGraphGroup: null, imageUrl: null, bounds: null, rawBounds: null, satellite: null };
        preSatControls.style.display = 'none';
        document.getElementById('preDetectionControls').style.display = 'none';
        document.getElementById('preImageDateDisplay').innerHTML = '';
        checkWorkflowState();
    }
    
    function resetPostLayers() {
        if (analysisState.post.satLayer) analysisState.post.satLayer.remove();
        if (analysisState.post.predMaskLayer) analysisState.post.predMaskLayer.remove();
        if (analysisState.post.predGraphGroup) analysisState.post.predGraphGroup.remove();
        analysisState.post = { satLayer: null, predMaskLayer: null, predGraphGroup: null, imageUrl: null, bounds: null, rawBounds: null, satellite: null };
        postSatControls.style.display = 'none';
        document.getElementById('postDetectionControls').style.display = 'none';
        document.getElementById('postImageDateDisplay').innerHTML = '';
        checkWorkflowState();
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
        drawnItems.clearLayers();
        drawnRectangle = event.layer;
        drawnItems.addLayer(drawnRectangle);
        updateRectangleInfo(drawnRectangle);
        updateRoads();
        checkWorkflowState();
    });

    map.on(L.Draw.Event.EDITED, (event) => {
        drawnItems.eachLayer(layer => { drawnRectangle = layer; updateRectangleInfo(layer); });
        updateRoads();
        checkWorkflowState();
    });

    map.on(L.Draw.Event.DELETED, () => {
        drawnRectangle = null;
        checkWorkflowState();
    });

    // --- OSM Roads Logic ---
    let osmRoadsLayer = L.geoJSON(null, { style: () => ({ color: roadColors.osm, weight: 2, opacity: 0.8, dashArray: '5, 10' }) }).addTo(map);
    const getSelectedRoadTypes = () => Array.from(document.querySelectorAll('.road-type-filter:checked')).map(cb => cb.value).join(',');
    const updateRoads = async () => {
        if (!drawnRectangle) { osmRoadsLayer.clearLayers(); return; }
        const bbox = drawnRectangle.getBounds().toBBoxString();
        const types = getSelectedRoadTypes();
        const historicalDate = startDateInput.value;
        if (!types) { osmRoadsLayer.clearLayers(); return; }
        showLoader('Fetching OSM Roads...');
        try {
            const params = new URLSearchParams({ bbox: bbox, types: types, date: historicalDate });
            const response = await fetch(`${API_BASE_URL}/get_roads?${params.toString()}`);
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
            checkWorkflowState();
        }
    };
    updateRoadsBtn.addEventListener('click', updateRoads);

    // --- GeoPackage Upload Logic ---
    // Helper to create a UI row for a gpkg layer with controls (visibility, opacity, color, delete)
    function createGpkgLayerRow(layerObj) {
        const container = document.getElementById('gpkgLayersContainer');
        const placeholder = document.getElementById('gpkgLayersPlaceholder');
        if (placeholder) placeholder.style.display = 'none';

        const row = document.createElement('div');
        row.className = 'gpkg-layer-row';

        // Title row (top)
        const left = document.createElement('div');
        left.className = 'gpkg-layer-left';
        const name = document.createElement('div');
        name.textContent = layerObj.name;
        left.appendChild(name);

        // Controls row (bottom): eyeball, colour, trash
        const right = document.createElement('div');
        right.className = 'gpkg-layer-right';

        const eyeBtn = document.createElement('button');
        eyeBtn.className = 'gpkg-eye-btn';
        eyeBtn.title = 'Toggle visibility';
        eyeBtn.innerHTML = '👁️';
        eyeBtn.addEventListener('click', () => {
            const currentlyVisible = map.hasLayer(layerObj.layer);
            if (currentlyVisible) {
                map.removeLayer(layerObj.layer);
                eyeBtn.classList.add('hidden');
            } else {
                map.addLayer(layerObj.layer);
                eyeBtn.classList.remove('hidden');
            }
        });

        const colorInput = document.createElement('input');
        colorInput.type = 'color';
        colorInput.value = layerObj.color || '#ffc107';
        colorInput.title = 'Layer color';
        colorInput.addEventListener('input', (e) => {
            try { layerObj.layer.setStyle({ color: e.target.value }); } catch (err) { }
        });

        const deleteBtn = document.createElement('button');
        deleteBtn.title = 'Remove layer';
        deleteBtn.className = 'gpkg-delete-btn';
        deleteBtn.innerHTML = '🗑️';
        deleteBtn.addEventListener('click', () => {
            // remove from map and from state
            layerObj.layer.remove();
            row.remove();
            analysisState.gpkg.layers = analysisState.gpkg.layers.filter(l => l !== layerObj);
            if (analysisState.gpkg.layers.length === 0) {
                const ph = document.getElementById('gpkgLayersPlaceholder'); if (ph) ph.style.display = 'block';
                document.getElementById('gpkgLayerControls').style.display = 'none';
            }
        });

        right.appendChild(eyeBtn);
        right.appendChild(colorInput);
        right.appendChild(deleteBtn);

        row.appendChild(left);
        row.appendChild(right);

        container.appendChild(row);
    }

    geoPackageUploadInput.addEventListener('change', async function() {
        if (this.files.length === 0) return;
        const file = this.files[0];

        showLoader(`Uploading ${file.name}...`);
        const formData = new FormData();
        formData.append('file', file);

        try {
            const response = await fetch(`${API_BASE_URL}/upload_geopackage`, {
                method: 'POST',
                body: formData
            });

            if (!response.ok) {
                const errorData = await response.json();
                throw new Error(errorData.details || errorData.error || 'Failed to process GeoPackage.');
            }

            const geojsonData = await response.json();

            // Create a Leaflet layer for the uploaded geopackage
            const gpkgLayer = L.geoJSON(geojsonData, {
                style: function(feature) {
                    return { color: '#ffc107', weight: 2, opacity: 0.8, fillOpacity: 0.2 };
                }
            }).addTo(map);

            const layerObj = { name: file.name, layer: gpkgLayer, opacity: 0.8, color: '#ffc107' };
            analysisState.gpkg.layers.push(layerObj);

            // Show controls container
            document.getElementById('gpkgLayerControls').style.display = 'block';
            createGpkgLayerRow(layerObj);

            // fit to the newly added layer bounds if available
            try { map.fitBounds(gpkgLayer.getBounds()); } catch (e) { }

        } catch (error) {
            console.error('GeoPackage Upload Error:', error);
            alert(`Could not load GeoPackage: ${error.message}`);
        } finally {
            hideLoader();
            this.value = ''; // Reset file input
        }
    });

    // --- Dynamic UI Logic ---
    dataSourceSelect.addEventListener('change', () => {
        const selectedProvider = dataSourceSelect.value;
        document.querySelectorAll('.provider-options').forEach(el => el.style.display = 'none');
        if (selectedProvider === 'gee') geeOptionsContainer.style.display = 'block';
        else if (selectedProvider === 'maxar') maxarOptionsContainer.style.display = 'block';
        else if (selectedProvider === 'local') localOptionsContainer.style.display = 'block';
        updateSatelliteOptions(selectedProvider);
        checkWorkflowState();
    });

    satelliteSelect.addEventListener('change', () => {
        const selectedSatellite = satelliteSelect.value;
        const selectedProvider = dataSourceSelect.value;
        document.querySelectorAll('.satellite-options').forEach(el => el.style.display = 'none');
        if (selectedProvider !== 'local') {
            if (selectedSatellite === 'sentinel_1') sentinel1Options.style.display = 'block';
            else if (selectedSatellite === 'sentinel_2') sentinel2Options.style.display = 'block';
            else if (selectedSatellite === 'sentinel_2_nir') sentinel2NIROptions.style.display = 'block';
            else if (selectedSatellite === 'maxar_imagery') maxarImageryOptions.style.display = 'block';
            else if (selectedSatellite === 'capella') capellaOptions.style.display = 'block';
        }
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
    
    preEventUploadInput.addEventListener('change', function() { handleLocalUpload('pre', this); });
    postEventUploadInput.addEventListener('change', function() { handleLocalUpload('post', this); });

    async function handleLocalUpload(prefix, fileInput) {
        if (fileInput.files.length === 0) return;
        const selectedSatellite = satelliteSelect.value;
        if (!selectedSatellite) {
            alert('Please select the satellite type from the dropdown before uploading.');
            fileInput.value = '';
            return;
        }
        if (prefix === 'pre') resetPreLayers(); else resetPostLayers();
        const file = fileInput.files[0];
        const formData = new FormData();
        formData.append('file', file);
        formData.append('prefix', prefix);
        showLoader(`Uploading and processing ${prefix}-event image...`);
        try {
            const uploadRes = await fetch(`${API_BASE_URL}/upload_image`, { method: 'POST', body: formData });
            if (!uploadRes.ok) throw new Error((await uploadRes.json()).error);
            const uploadData = await uploadRes.json();

            if (uploadData.rawTiffUrl) {
                const tiffLinkElement = (prefix === 'pre') ? downloadLinks.preGeoTiff : downloadLinks.postGeoTiff;
                tiffLinkElement.href = STATIC_BASE_URL + uploadData.rawTiffUrl;
                tiffLinkElement.download = `${prefix}_event_satellite_uploaded.tif`;
                tiffLinkElement.classList.remove('disabled');
            }
            
            let processUrl = `${API_BASE_URL}/process_satellite_image?satellite=${selectedSatellite}&prefix=${prefix}`;
            if (disableNormalizationCheckbox && disableNormalizationCheckbox.checked) {
                processUrl += `&disable_normalization=true`;
            }
            const processRes = await fetch(processUrl);
            if (!processRes.ok) throw new Error((await processRes.json()).error);
            const processData = await processRes.json();

            Object.assign(analysisState[prefix], processData);
            analysisState[prefix].satellite = selectedSatellite;
            analysisState[prefix].satLayer = L.imageOverlay(STATIC_BASE_URL + processData.imageUrl, processData.bounds, { opacity: 1.0 }).addTo(map);
            document.getElementById(`${prefix}ImageDateDisplay`).innerHTML = `<b>Image Date:</b> ${uploadData.imageDate}`;
            
            const pngLinkElement = (prefix === 'pre') ? downloadLinks.preSat : downloadLinks.postSat;
            pngLinkElement.href = STATIC_BASE_URL + processData.imageUrl;
            pngLinkElement.download = `${prefix}_event_satellite_processed.png`;
            pngLinkElement.classList.remove('disabled');

            const imageBounds = L.latLngBounds(processData.bounds);
            map.fitBounds(imageBounds);
            const satControls = (prefix === 'pre') ? preSatControls : postSatControls;
            satControls.style.display = 'block';
            checkWorkflowState();
        } catch (error) {
            console.error(`Error during local upload for ${prefix}-event:`, error);
            alert(`Local image processing failed: ${error.message}`);
        } finally {
            hideLoader();
            fileInput.value = '';
        }
    }

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
                checkWorkflowState();
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
                checkWorkflowState();
            } else {
                alert('Could not find any images for the selected dates and area.');
            }
        } catch (error) { console.error('Error generating images:', error); alert(`Error: ${error.message}`);
        } finally { hideLoader(); }
    });

    function buildApiRequestBody(prefix) {
        if (!drawnRectangle) return null;
        const bbox = drawnRectangle.getBounds().toBBoxString();
        const source_provider = dataSourceSelect.value;
        const satellite = satelliteSelect.value;
        let credentials = {};
        if (source_provider === 'gee') {
            credentials.project_id = geeProjectInput.value.trim().replace(/["']/g, "");
            if (!credentials.project_id) { alert('Please enter a Google Earth Engine Project ID.'); return null; }
        } else if (source_provider === 'maxar') {
            credentials.api_key = maxarApiKeyInput.value.trim();
            if (!credentials.api_key) { alert('Please enter your Maxar API Key.'); return null; }
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
            const downloadRes = await fetch(`${API_BASE_URL}/download_satellite_image`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(params)
            });
            if (!downloadRes.ok) throw new Error((await downloadRes.json()).error);
            const downloadData = await downloadRes.json();

            if (downloadData.rawTiffUrl) {
                const tiffLinkElement = (prefix === 'pre') ? downloadLinks.preGeoTiff : downloadLinks.postGeoTiff;
                tiffLinkElement.href = STATIC_BASE_URL + downloadData.rawTiffUrl;
                tiffLinkElement.download = `${prefix}_event_satellite.tif`;
                tiffLinkElement.classList.remove('disabled');
            }

            showLoader(`Processing ${prefix}-event image...`);
            let processUrl = `${API_BASE_URL}/process_satellite_image?satellite=${params.options.satellite}&prefix=${prefix}`;
            if (downloadData.stac_bbox) {
                processUrl += `&stac_bbox=${downloadData.stac_bbox.join(',')}`;
            }
            const processRes = await fetch(processUrl);
            if (!processRes.ok) throw new Error((await processRes.json()).error);
            const processData = await processRes.json();
            Object.assign(analysisState[prefix], processData);
            analysisState[prefix].satellite = params.options.satellite;
            analysisState[prefix].satLayer = L.imageOverlay(STATIC_BASE_URL + processData.imageUrl, processData.bounds, { opacity: 1.0 }).addTo(map);
            document.getElementById(`${prefix}ImageDateDisplay`).innerHTML = `<b>Image Date:</b> ${downloadData.imageDate}`;
            
            const pngLinkElement = (prefix === 'pre') ? downloadLinks.preSat : downloadLinks.postSat;
            pngLinkElement.href = STATIC_BASE_URL + processData.imageUrl;
            pngLinkElement.download = `${prefix}_event_satellite_processed.png`;
            pngLinkElement.classList.remove('disabled');

            return true;
        } catch (error) {
            console.error(`Error for ${prefix}-event image:`, error);
            alert(`Could not process ${prefix}-event image: ${error.message}`);
            return false;
        }
    }

    function checkWorkflowState() {
        const provider = dataSourceSelect.value;
        const isRemoteProvider = provider !== 'local';
        generatePreImagesBtn.disabled = !(isRemoteProvider && drawnRectangle);
        generatePostImagesBtn.disabled = !(isRemoteProvider && drawnRectangle);
        document.getElementById('runPreDetectionBtn').disabled = !(analysisState.pre.satLayer && drawnRectangle);
        document.getElementById('runPostDetectionBtn').disabled = !(analysisState.post.satLayer && drawnRectangle);
        generateGTMaskBtn.disabled = !(analysisState.pre.satLayer && drawnRectangle && osmRoadsLayer.getLayers().length > 0);
        compareRoadsBtn.disabled = !(analysisState.pre.predGraphGroup && analysisState.post.predGraphGroup);
    }

    generateGTMaskBtn.addEventListener('click', async () => {
        const imageState = analysisState.pre.rawBounds ? analysisState.pre : analysisState.post;
        if (!imageState.rawBounds || !drawnRectangle) {
            alert('A pre-event image must be loaded and an area drawn first.');
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
            const res = await fetch(`${API_BASE_URL}/generate_osm_mask`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ osm_data: osmData, image_bounds: image_bounds })
            });
            if (!res.ok) throw new Error((await res.json()).error);
            const data = await res.json();
            if (analysisState.gtMaskLayer) analysisState.gtMaskLayer.remove();
            analysisState.gtMaskLayer = L.imageOverlay(STATIC_BASE_URL + data.maskUrl, imageState.bounds, { opacity: 1.0 });
            gtMaskControls.style.display = 'block';
            document.getElementById('toggleGtMask').checked = false;
            downloadLinks.gtMask.href = STATIC_BASE_URL + data.maskUrl;
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
        if (!analysisState[prefix].imageUrl || !drawnRectangle) { 
            alert(`The ${prefix}-event satellite image must be loaded and an area drawn first.`); 
            return; 
        }
        showLoader(`Running ${prefix}-event detection...`);
        try {
            const bbox = drawnRectangle.getBounds().toBBoxString();
            // First: if this case had saved prediction assets, prefer them and skip ML
            const caseMaskUrl = analysisState[prefix].predictedMaskStaticUrl || null;
            const caseGraphUrl = analysisState[prefix].predictedGraphStaticUrl || null;
            if (caseMaskUrl || caseGraphUrl) {
                // Load saved mask overlay if present
                if (caseMaskUrl) {
                    if (analysisState[prefix].predMaskLayer) analysisState[prefix].predMaskLayer.remove();
                    const maskBounds = analysisState[prefix].bounds || [[drawnRectangle.getSouth(), drawnRectangle.getWest()], [drawnRectangle.getNorth(), drawnRectangle.getEast()]];
                    analysisState[prefix].predMaskLayer = L.imageOverlay(STATIC_BASE_URL + caseMaskUrl, maskBounds, { opacity: 1.0 }).addTo(map);
                    const maskLink = (prefix === 'pre') ? downloadLinks.preMask : downloadLinks.postMask;
                    maskLink.href = STATIC_BASE_URL + caseMaskUrl;
                    maskLink.download = `${prefix}_event_prediction_mask.png`;
                    maskLink.classList.remove('disabled');
                }

                // Load saved graph if available (prefer a .geojson next to .p, else skip)
                if (caseGraphUrl) {
                    // If graph URL ends with .p try to fetch a same-named .geojson
                    let geojsonCandidate = caseGraphUrl;
                    if (geojsonCandidate.endsWith('.p')) geojsonCandidate = geojsonCandidate.replace(/\.p$/, '.geojson');
                    try {
                        const gjRes = await fetch(STATIC_BASE_URL + geojsonCandidate);
                        if (gjRes.ok) {
                            const gj = await gjRes.json();
                            if (analysisState[prefix].predGraphGroup) analysisState[prefix].predGraphGroup.remove();
                            const casingStyle = { color: roadColors.casing, weight: 7, opacity: 0.8 };
                            const mainStyle = { color: (prefix === 'pre' ? roadColors.preEvent : roadColors.postEvent), weight: 3, opacity: 1 };
                            const casingLayer = L.geoJSON(gj, { style: casingStyle });
                            const mainLayer = L.geoJSON(gj, { style: mainStyle });
                            analysisState[prefix].predGraphGroup = L.featureGroup([casingLayer, mainLayer]).addTo(map);
                            const geojsonLink = (prefix === 'pre') ? downloadLinks.preGeoJSON : downloadLinks.postGeoJSON;
                            const blob = new Blob([JSON.stringify(gj, null, 2)], { type: 'application/json' });
                            geojsonLink.href = URL.createObjectURL(blob);
                            geojsonLink.download = `${prefix}_event_predicted_roads.geojson`;
                            geojsonLink.classList.remove('disabled');
                        } else {
                            console.warn('No geojson found alongside saved graph; skipping graph display.');
                        }
                    } catch (err) {
                        console.warn('Error fetching saved graph geojson:', err);
                    }
                }

                document.getElementById(`${prefix}DetectionControls`).style.display = 'block';
                document.getElementById(`toggle${prefix.charAt(0).toUpperCase() + prefix.slice(1)}PredMask`).checked = false;
                checkWorkflowState();
                hideLoader();
                return; // skip backend inference
            }

            // No saved outputs: fall back to backend inference request
            const params = new URLSearchParams({ prefix: prefix, bbox: bbox });
            const staticTiff = analysisState[prefix].rawTiffStaticPath;
            if (staticTiff) params.set('image', staticTiff);
            const res = await fetch(`${API_BASE_URL}/get_predicted_roads?${params.toString()}`);

            if (!res.ok) {
                const errorData = await res.json();
                throw new Error(errorData.details || errorData.error);
            }

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
            const maskBounds = data.bounds || analysisState[prefix].bounds;
            analysisState[prefix].predMaskLayer = L.imageOverlay(STATIC_BASE_URL + data.maskUrl, maskBounds, { opacity: 1.0 });
            document.getElementById(`${prefix}DetectionControls`).style.display = 'block';
            document.getElementById(`toggle${prefix.charAt(0).toUpperCase() + prefix.slice(1)}PredMask`).checked = false;
            checkWorkflowState();
            const maskLink = (prefix === 'pre') ? downloadLinks.preMask : downloadLinks.postMask;
            maskLink.href = STATIC_BASE_URL + data.maskUrl;
            maskLink.download = `${prefix}_event_prediction_mask.png`;
            maskLink.classList.remove('disabled');
            const geojsonLink = (prefix === 'pre') ? downloadLinks.preGeoJSON : downloadLinks.postGeoJSON;
            const blob = new Blob([JSON.stringify(data.geojson, null, 2)], { type: 'application/json' });
            geojsonLink.href = URL.createObjectURL(blob);
            geojsonLink.download = `${prefix}_event_predicted_roads.geojson`;
            geojsonLink.classList.remove('disabled');
        } catch (error) { 
            console.error(`${prefix} Detection Error:`, error); 
            alert(`${prefix.charAt(0).toUpperCase() + prefix.slice(1)} Detection failed:\n\n${error.message}`);
        } finally { 
            hideLoader(); 
        }
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
            const res = await fetch(`${API_BASE_URL}/compare_roads`, {
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

    document.getElementById('baseMapToggle').addEventListener('change', (e) => map.toggleLayer(baseMapLayer, e.target.checked));
    document.getElementById('roadToggle').addEventListener('change', (e) => map.toggleLayer(osmRoadsLayer, e.target.checked));
    greyscaleToggle.addEventListener('change', (e) => map.getContainer().classList.toggle('greyscale-enabled', e.target.checked));
    document.getElementById('gtMaskOpacitySlider').addEventListener('input', (e) => { if (analysisState.gtMaskLayer) analysisState.gtMaskLayer.setOpacity(e.target.value); });
    document.getElementById('toggleDamageLayer').addEventListener('change', (e) => map.toggleLayer(analysisState.damageLayerGroup, e.target.checked));

    // Per-layer GeoPackage controls are created dynamically; no global gpkg controls to wire here.

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

    // Load case studies metadata (static JSON) and then initialize provider UI
    loadCaseStudiesJson().catch(() => {});
    dataSourceSelect.dispatchEvent(new Event('change'));

    // --- Case Studies (loaded from static JSON if available) ---
    let CASE_STUDIES = null;

    async function loadCaseStudiesJson() {
        try {
            const res = await fetch('static/case_studies/case_studies.json');
            if (!res.ok) throw new Error('Failed to fetch case_studies.json');
            const json = await res.json();
            CASE_STUDIES = json.cases || [];
        } catch (err) {
            console.warn('Could not load case_studies.json, falling back to embedded list.', err);
            // fallback to a minimal built-in list to ensure UI still works
            CASE_STUDIES = [
                {
                    id: 'baltimore_bridge', title: 'Baltimore Bridge Collapse', bbox: '-76.535,39.211,-76.522,39.223', mid_date: '2024-03-26', pre_start: '2024-02-01', post_end: '2024-05-01', satellite: 'sentinel_2',
                    assets: { pre_png: '/static/satellite_image_pre_1758716786.png', post_png: '/static/satellite_image_post_1758716830.png', predicted_mask_pre: '/static/predicted_mask_pre_1758719359.png', saved_graph_prefix: 'pre' }
                }
            ];
        }
        renderCaseStudies();
    }

    function renderCaseStudies() {
        const list = document.getElementById('caseStudyList');
        list.innerHTML = '';
        (CASE_STUDIES || []).forEach(study => {
            const li = document.createElement('li');
            li.style.marginBottom = '6px';
            const a = document.createElement('a');
            a.href = '#';
            a.textContent = study.title || study.id;
            a.style.display = 'block';
            a.style.padding = '6px';
            a.style.borderRadius = '4px';
            a.style.background = '#f8f9fa';
            a.addEventListener('click', (e) => { e.preventDefault(); selectCaseStudy(study); });
            li.appendChild(a);
            list.appendChild(li);
        });
    }

    async function selectCaseStudy(study) {
        // set dates and satellite
        startDateInput.value = study.pre_start;
        midDateInput.value = study.mid_date;
        endDateInput.value = study.post_end;
        satelliteSelect.value = study.satellite;
        satelliteSelect.dispatchEvent(new Event('change'));

        // draw rectangle on map
        try {
            const coords = study.bbox.split(',').map(Number);
            const sw = L.latLng(coords[1], coords[0]);
            const ne = L.latLng(coords[3], coords[2]);
            if (drawnRectangle) { drawnItems.clearLayers(); drawnRectangle = null; }
            drawnRectangle = L.rectangle([sw, ne], { color: '#007bff' });
            drawnItems.addLayer(drawnRectangle);
            map.fitBounds(drawnRectangle.getBounds());
            updateRoads();
            checkWorkflowState();
        } catch (err) { console.error('Error drawing rectangle for case study', err); }

        // For case studies: prefer remote provider flow (no local saved images/masks).
        // Set the data source to GEE by default (cases typically use Sentinel-2)
        try {
            dataSourceSelect.value = study.provider || 'gee';
        } catch (e) { dataSourceSelect.value = 'gee'; }
        dataSourceSelect.dispatchEvent(new Event('change'));

        try {
            satelliteSelect.value = study.satellite || 'sentinel_2';
        } catch (e) { satelliteSelect.value = 'sentinel_2'; }
        satelliteSelect.dispatchEvent(new Event('change'));

        // Clear any per-case local asset references so the UI uses the normal remote image flow
        delete analysisState.pre.casePrePng;
        delete analysisState.pre.rawTiffStaticPath;
        delete analysisState.pre.predictedMaskStaticUrl;
        delete analysisState.pre.predictedGraphStaticUrl;
        delete analysisState.post.casePostPng;
        delete analysisState.post.rawTiffStaticPath;
        delete analysisState.post.predictedMaskStaticUrl;
        delete analysisState.post.predictedGraphStaticUrl;

        // Ensure the generate buttons' default event listeners handle the fetch (they are defined elsewhere)
        checkWorkflowState();
        
        

        // Try to fetch OSM roads for the case area (using midDate)
        try { await updateRoads(); } catch(e) { console.warn('OSM fetch failed in case study selection', e); }

        // Do not auto-load predicted roads/masks here. The user will click "Run Pre-Event Detection" or
        // "Run Post-Event Detection" to load model outputs; that flow remains unchanged and uses the
        // existing `runDetection(prefix)` function which calls the backend.
    }

    renderCaseStudies();
});