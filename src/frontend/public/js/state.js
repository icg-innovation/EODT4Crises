// js/state.js
import {
    hideLoader
} from './ui.js';
import {
    clearDrawnItems,
    clearOsmRoadsLayer,
    removeLayer
} from './map.js';

export let analysisState = {
    gtMaskLayer: null,
    damageLayerGroup: null,
    pre: {
        satLayer: null,
        predMaskLayer: null,
        predGraphGroup: null,
        imageUrl: null,
        bounds: null,
        rawBounds: null,
        satellite: null
    },
    post: {
        satLayer: null,
        predMaskLayer: null,
        predGraphGroup: null,
        imageUrl: null,
        bounds: null,
        rawBounds: null,
        satellite: null
    }
};

export function resetWorkflow(fullReset = false, elements) {
    if (analysisState.gtMaskLayer) removeLayer(analysisState.gtMaskLayer);
    if (analysisState.damageLayerGroup) removeLayer(analysisState.damageLayerGroup);
    analysisState.gtMaskLayer = null;
    analysisState.damageLayerGroup = null;

    elements.gtMaskControls.style.display = 'none';
    elements.damageAnalysisControls.style.display = 'none';
    // elements.generateGTMaskBtn.disabled = true;
    elements.compareRoadsBtn.disabled = true;

    ['pre', 'post'].forEach(prefix => {
        if (analysisState[prefix].satLayer) removeLayer(analysisState[prefix].satLayer);
        if (analysisState[prefix].predMaskLayer) removeLayer(analysisState[prefix].predMaskLayer);
        if (analysisState[prefix].predGraphGroup) removeLayer(analysisState[prefix].predGraphGroup);

        analysisState[prefix] = {
            satLayer: null,
            predMaskLayer: null,
            predGraphGroup: null,
            imageUrl: null,
            bounds: null,
            rawBounds: null,
            satellite: null
        };
        document.getElementById(`${prefix}EventControls`).style.display = 'none';
        document.getElementById(`${prefix}DetectionControls`).style.display = 'none';
        document.getElementById(`run${prefix.charAt(0).toUpperCase() + prefix.slice(1)}DetectionBtn`).disabled = true;
    });

    // Reset UI elements to their default state
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
        elements.generateImagesBtn.disabled = true;
        clearDrawnItems();
        clearOsmRoadsLayer();
        elements.drawnRectangle = null;
    }
    hideLoader();
}