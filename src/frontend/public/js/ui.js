// js/ui.js
import {
    analysisState,
    resetWorkflow
} from './state.js';
import * as api from './apiService.js';
import {
    getMap,
    getOsmRoadsLayer,
    updateRectangleInfo,
    createCasedGeoJSON
} from './map.js';

// --- DOM Elements ---
const elements = {
    loader: document.getElementById('loader'),
    generateImagesBtn: document.getElementById('generateImagesBtn'),
    // generateGTMaskBtn: document.getElementById('generateGTMaskBtn'),
    updateRoadsBtn: document.getElementById('updateRoadsBtn'),
    compareRoadsBtn: document.getElementById('compareRoadsBtn'),
    startDateInput: document.getElementById('startDate'),
    midDateInput: document.getElementById('midDate'),
    endDateInput: document.getElementById('endDate'),
    satelliteSelect: document.getElementById('satelliteSelect'),
    sentinel2Options: document.getElementById('sentinel2Options'),
    cloudCoverSlider: document.getElementById('cloudCoverSlider'),
    cloudCoverValue: document.getElementById('cloudCoverValue'),
    sentinel1Options: document.getElementById('sentinel1Options'),
    polarizationSelect: document.getElementById('polarizationSelect'),
    gtMaskControls: document.getElementById('gtMaskControls'),
    damageAnalysisControls: document.getElementById('damageAnalysisControls'),
    drawnRectangle: null
};

// --- Loader ---
export const showLoader = (text = 'Processing...') => {
    elements.loader.textContent = text;
    elements.loader.style.display = 'flex';
};
export const hideLoader = () => elements.loader.style.display = 'none';


// --- Main Functions ---
async function updateRoads() {
    if (!elements.drawnRectangle) return;
    const bbox = elements.drawnRectangle.getBounds().toBBoxString();
    const types = getSelectedRoadTypes();
    if (!types) {
        getOsmRoadsLayer().clearLayers();
        return;
    }
    showLoader('Fetching OSM Roads...');
    try {
        const geojsonData = await api.getOsmRoads(bbox, types, elements.midDateInput.value);
        getOsmRoadsLayer().clearLayers().addData(geojsonData);
    } catch (error) {
        console.error('Error updating roads:', error);
        alert('Could not load OSM road data.');
    } finally {
        hideLoader();
    }
}

async function handleGenerateImages() {
    if (!elements.drawnRectangle) {
        alert('Please draw a rectangle on the map first.');
        return;
    }
    resetWorkflow(false, elements);
    const bbox = elements.drawnRectangle.getBounds().toBBoxString();
    const satellite = elements.satelliteSelect.value;
    const commonParams = {
        bbox: bbox,
        satellite: satellite,
        cloudy_pixel_percentage: satellite === 'sentinel_2' ? elements.cloudCoverSlider.value : undefined,
        polarization: satellite === 'sentinel_1' ? elements.polarizationSelect.value : undefined
    };

    try {
        showLoader('Fetching Pre-Event image...');
        const preImageResult = await api.downloadAndProcessImage('pre', { ...commonParams,
            start_date: elements.startDateInput.value,
            end_date: elements.midDateInput.value,
            target_date: elements.midDateInput.value
        });
        if (preImageResult) {
            Object.assign(analysisState.pre, preImageResult);
            analysisState.pre.satellite = satellite;
            analysisState.pre.satLayer = L.imageOverlay(preImageResult.imageUrl, preImageResult.bounds, {
                opacity: 0.8
            }).addTo(getMap());
            document.getElementById('preEventControls').style.display = 'block';
            document.getElementById('runPreDetectionBtn').disabled = false;
        }

        showLoader('Fetching Post-Event image...');
        const postImageResult = await api.downloadAndProcessImage('post', { ...commonParams,
            start_date: elements.midDateInput.value,
            end_date: elements.endDateInput.value,
            target_date: elements.midDateInput.value
        });
        if (postImageResult) {
            Object.assign(analysisState.post, postImageResult);
            analysisState.post.satellite = satellite;
            analysisState.post.satLayer = L.imageOverlay(postImageResult.imageUrl, postImageResult.bounds, {
                opacity: 0.8
            }).addTo(getMap());
            document.getElementById('postEventControls').style.display = 'block';
            document.getElementById('runPostDetectionBtn').disabled = false;
        }

        if (preImageResult || postImageResult) {
            // elements.generateGTMaskBtn.disabled = false;
            getMap().fitBounds(analysisState.pre.bounds || analysisState.post.bounds);
        } else {
            alert('Could not find any images for the selected dates and area.');
        }
    } catch (error) {
        console.error('Error generating images:', error);
        alert(`Error: ${error.message}`);
    } finally {
        hideLoader();
    }
}

async function handleGenerateGTMask() {
    const imageState = analysisState.pre.rawBounds ? analysisState.pre : analysisState.post;
    if (!imageState.rawBounds) {
        alert('Generate a satellite image first.');
        return;
    }
    showLoader('Generating Ground Truth mask...');
    try {
        const {
            lat_min,
            lat_max,
            lon_min,
            lon_max
        } = imageState.rawBounds;
        const image_bounds = `${lat_min},${lat_max},${lon_min},${lon_max}`;
        const data = await api.generateOsmMask(
            elements.drawnRectangle.getBounds().toBBoxString(),
            getSelectedRoadTypes(),
            image_bounds
        );
        if (analysisState.gtMaskLayer) getMap().removeLayer(analysisState.gtMaskLayer);
        analysisState.gtMaskLayer = L.imageOverlay(data.maskUrl, imageState.bounds, {
            opacity: 0.7
        }).addTo(getMap());
        elements.gtMaskControls.style.display = 'block';
    } catch (error) {
        console.error("GT Mask Error:", error);
        alert(`GT Mask generation failed: ${error.message}`);
    } finally {
        hideLoader();
    }
}

async function runDetection(prefix) {
    if (!analysisState[prefix].imageUrl) {
        alert(`Generate the ${prefix}-event satellite image first.`);
        return;
    }
    showLoader(`Running ${prefix}-event detection...`);
    try {
        const data = await api.getPredictedRoads(analysisState[prefix].imageUrl, prefix);

        if (analysisState[prefix].predGraphGroup) getMap().removeLayer(analysisState[prefix].predGraphGroup);
        if (analysisState[prefix].predMaskLayer) getMap().removeLayer(analysisState[prefix].predMaskLayer);

        const styles = {
            pre: {
                color: '#00ffff',
                weight: 3
            },
            post: {
                color: '#ff00ff',
                weight: 3
            }
        };
        const casingStyle = {
            color: '#000000',
            weight: styles[prefix].weight + 2,
            opacity: 0.7
        };

        analysisState[prefix].predGraphGroup = createCasedGeoJSON(data.geojson, styles[prefix], casingStyle).addTo(getMap());
        analysisState[prefix].predMaskLayer = L.imageOverlay(data.maskUrl, analysisState[prefix].bounds, {
            opacity: 0.7
        }).addTo(getMap());
        document.getElementById(`${prefix}DetectionControls`).style.display = 'block';

        if (analysisState.pre.predGraphGroup && analysisState.post.predGraphGroup) {
            elements.compareRoadsBtn.disabled = false;
        }
    } catch (error) {
        console.error(`${prefix} Detection Error:`, error);
        alert(`Detection failed: ${error.message}`);
    } finally {
        hideLoader();
    }
}

async function handleCompareRoads() {
    if (!analysisState.pre.predGraphGroup || !analysisState.post.predGraphGroup) {
        alert('Please run both pre and post-event detection first.');
        return;
    }
    showLoader('Analyzing road damage...');
    try {
        const data = await api.compareRoads(elements.drawnRectangle.getBounds().toBBoxString(), getSelectedRoadTypes());

        if (analysisState.damageLayerGroup) getMap().removeLayer(analysisState.damageLayerGroup);

        const casingStyle = {
            color: '#000000',
            weight: 7,
            opacity: 0.8
        };
        const mainStyle = {
            color: '#FFD700',
            weight: 5,
            opacity: 1
        };

        analysisState.damageLayerGroup = createCasedGeoJSON(data.geojson, mainStyle, casingStyle).addTo(getMap());
        elements.damageAnalysisControls.style.display = 'block';

        // Hide other prediction layers to focus on damage
        getMap().toggleLayer(analysisState.pre.predGraphGroup, false);
        document.getElementById('togglePrePredGraph').checked = false;
        getMap().toggleLayer(analysisState.post.predGraphGroup, false);
        document.getElementById('togglePostPredGraph').checked = false;

    } catch (error) {
        console.error('Damage Analysis Error:', error);
        alert(`Damage analysis failed: ${error.message}`);
    } finally {
        hideLoader();
    }
}


// --- Helpers ---
const getSelectedRoadTypes = () => Array.from(document.querySelectorAll('.road-type-filter:checked')).map(cb => cb.value).join(',');

// --- Event Listener Setup ---
export function setupEventListeners(mapInstance, drawnItems) {
    // --- Map Events ---
    mapInstance.on(L.Draw.Event.CREATED, (event) => {
        resetWorkflow(true, elements);
        elements.drawnRectangle = event.layer;
        drawnItems.addLayer(elements.drawnRectangle);
        elements.generateImagesBtn.disabled = false;
        updateRoads();
        updateRectangleInfo(elements.drawnRectangle);
    });

    mapInstance.on(L.Draw.Event.EDITED, (event) => {
        resetWorkflow(true, elements);
        event.layers.eachLayer(layer => {
            elements.drawnRectangle = layer;
            updateRectangleInfo(layer);
        });
        if (elements.drawnRectangle) {
            elements.generateImagesBtn.disabled = false;
            updateRoads();
        }
    });

    mapInstance.on(L.Draw.Event.DELETED, () => {
        resetWorkflow(true, elements);
    });


    // --- Button Clicks ---
    elements.updateRoadsBtn.addEventListener('click', updateRoads);
    elements.generateImagesBtn.addEventListener('click', handleGenerateImages);
    // elements.generateGTMaskBtn.addEventListener('click', handleGenerateGTMask);
    elements.compareRoadsBtn.addEventListener('click', handleCompareRoads);
    document.getElementById('runPreDetectionBtn').addEventListener('click', () => runDetection('pre'));
    document.getElementById('runPostDetectionBtn').addEventListener('click', () => runDetection('post'));

    // --- Toggles & Sliders ---
    document.getElementById('baseMapToggle').addEventListener('change', (e) => mapInstance.toggleLayer(getMap()._layers[Object.keys(getMap()._layers)[0]], e.target.checked));
    document.getElementById('roadToggle').addEventListener('change', (e) => mapInstance.toggleLayer(getOsmRoadsLayer(), e.target.checked));
    document.getElementById('toggleGtMask').addEventListener('change', (e) => mapInstance.toggleLayer(analysisState.gtMaskLayer, e.target.checked));
    document.getElementById('gtMaskOpacitySlider').addEventListener('input', (e) => {
        if (analysisState.gtMaskLayer) analysisState.gtMaskLayer.setOpacity(e.target.value);
    });
    document.getElementById('toggleDamageLayer').addEventListener('change', (e) => mapInstance.toggleLayer(analysisState.damageLayerGroup, e.target.checked));
    document.getElementById('damageOpacitySlider').addEventListener('input', (e) => {
        if (analysisState.damageLayerGroup) analysisState.damageLayerGroup.setOpacity(e.target.value);
    });

    ['pre', 'post'].forEach(prefix => {
        document.getElementById(`toggle${prefix.charAt(0).toUpperCase() + prefix.slice(1)}Sat`).addEventListener('change', (e) => mapInstance.toggleLayer(analysisState[prefix].satLayer, e.target.checked));
        document.getElementById(`${prefix}OpacitySlider`).addEventListener('input', (e) => {
            if (analysisState[prefix].satLayer) analysisState[prefix].satLayer.setOpacity(e.target.value);
        });
        document.getElementById(`toggle${prefix.charAt(0).toUpperCase() + prefix.slice(1)}PredMask`).addEventListener('change', (e) => mapInstance.toggleLayer(analysisState[prefix].predMaskLayer, e.target.checked));
        document.getElementById(`${prefix}PredMaskOpacitySlider`).addEventListener('input', (e) => {
            if (analysisState[prefix].predMaskLayer) analysisState[prefix].predMaskLayer.setOpacity(e.target.value);
        });
        document.getElementById(`toggle${prefix.charAt(0).toUpperCase() + prefix.slice(1)}PredGraph`).addEventListener('change', (e) => mapInstance.toggleLayer(analysisState[prefix].predGraphGroup, e.target.checked));
    });

    // --- Other UI ---
    document.querySelectorAll('.collapsible-header').forEach(header => {
        header.addEventListener('click', () => {
            const content = header.nextElementSibling;
            const arrow = header.querySelector('.arrow');
            const isActive = content.style.display === 'block';
            content.style.display = isActive ? 'none' : 'block';
            arrow.textContent = isActive ? '▼' : '▲';
        });
    });

    elements.satelliteSelect.addEventListener('change', (e) => {
        const selected = e.target.value;
        elements.sentinel1Options.style.display = selected === 'sentinel_1' ? 'block' : 'none';
        elements.sentinel2Options.style.display = selected === 'sentinel_2' ? 'block' : 'none';
    });

    elements.cloudCoverSlider.addEventListener('input', (e) => {
        elements.cloudCoverValue.textContent = e.target.value;
    });

    // --- Initial Date Setup ---
    const today = new Date();
    const oneMonthAgo = new Date(new Date().setMonth(today.getMonth() - 1));
    const threeMonthsAgo = new Date(new Date().setMonth(today.getMonth() - 3));
    elements.startDateInput.value = threeMonthsAgo.toISOString().split('T')[0];
    elements.midDateInput.value = oneMonthAgo.toISOString().split('T')[0];
    elements.endDateInput.value = today.toISOString().split('T')[0];
}