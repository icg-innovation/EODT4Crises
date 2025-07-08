// js/apiService.js
const API_BASE_URL = 'http://localhost:4000';

async function fetchAPI(endpoint, errorMessage) {
    const response = await fetch(`${API_BASE_URL}${endpoint}`);
    if (!response.ok) {
        const errorData = await response.json().catch(() => ({
            error: 'Unknown error'
        }));
        throw new Error(errorData.error || errorMessage);
    }
    return response.json();
}

export function getOsmRoads(bbox, types, date) {
    const params = new URLSearchParams({
        bbox,
        types,
        date
    });
    return fetchAPI(`/api/get_roads?${params.toString()}`, 'Failed to fetch OSM roads');
}

export async function downloadAndProcessImage(prefix, params) {
    const allParams = { ...params,
        prefix
    };
    const query = new URLSearchParams(Object.entries(allParams).filter(([_, v]) => v != null)).toString();

    await fetchAPI(`/api/download_satellite_image?${query}`, `Could not download ${prefix}-event image`);
    return await fetchAPI(`/api/process_satellite_image?satellite=${params.satellite}&prefix=${prefix}`, `Could not process ${prefix}-event image`);
}

export function generateOsmMask(bbox, types, image_bounds) {
    const url = `/api/generate_osm_mask?bbox=${bbox}&types=${types}&image_bounds=${image_bounds}`;
    return fetchAPI(url, 'GT Mask generation failed');
}

export function getPredictedRoads(imageUrl, prefix) {
    const url = `/api/get_predicted_roads?image_url=${imageUrl}&prefix=${prefix}`;
    return fetchAPI(url, `Detection failed for ${prefix}-event`);
}

export function compareRoads(bbox, types) {
    const url = `/api/compare_roads?bbox=${bbox}&types=${types}`;
    return fetchAPI(url, 'Damage analysis failed');
}