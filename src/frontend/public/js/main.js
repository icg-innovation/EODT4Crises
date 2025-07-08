// js/main.js
import {
    initMap
} from './map.js';
import {
    setupEventListeners
} from './ui.js';

document.addEventListener('DOMContentLoaded', () => {
    // Initialize the map and get instances of the map and drawnItems layer
    const {
        map,
        drawnItems
    } = initMap();

    // Set up all the UI event listeners, passing the map instances they might need
    setupEventListeners(map, drawnItems);
});