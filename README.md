# EODT4Crises - Earth Observation Data Tools for Crisis Response

EODT4Crises is an interactive web application for analyzing road infrastructure damage using satellite imagery and machine learning. The system provides automated detection and comparison of road networks before and after crisis events using various satellite data sources.

## Features

- **Multi-source Satellite Data**: Support for Google Earth Engine, Maxar, and local imagery
- **AI-powered Road Detection**: Advanced machine learning models for road segmentation and graph extraction
- **Interactive Web Interface**: User-friendly map interface for selecting areas of interest
- **Before/After Analysis**: Compare road networks pre- and post-crisis events
- **Damage Assessment**: Automated identification of damaged or destroyed road segments
- **Export Capabilities**: Download results as images and GeoPackage files

## Architecture

### Backend (`src/backend/`)
- **Flask API Server**: RESTful API endpoints for image processing and analysis
- **Image Providers**: Pluggable data source connectors (GEE, Maxar, Local files)
- **Data Processing**: ML models for road detection and graph extraction
- **Model Files**: Pre-trained weights and configuration files

### Frontend (`src/frontend/`)
- **Interactive Map**: Leaflet-based web interface
- **Case Studies**: Pre-configured crisis scenarios
- **Real-time Processing**: Dynamic image fetching and analysis

## Quick Start

### Prerequisites
- Python 3.9+
- Docker (optional)
- Node.js (for frontend development)

### Installation

1. **Clone the repository**
   ```bash
   git clone https://github.com/icg-innovation/EODT4Crises.git
   cd EODT4Crises
   ```

2. **Install Python dependencies**
   ```bash
   pip install -r requirements.txt
   ```

3. **Download model files**
   Place the required model files in `src/backend/model_files/`:
   - `sam_vit_b_01ec64.pth`
   - `spacenet_custom.yaml`
   - `spacenet_vitb_256_e10.ckpt`

### Running the Application

#### Using Docker (Recommended)
```bash
docker-compose up --build
```

#### Manual Setup
1. **Start the backend**
   ```bash
   cd src/backend
   python app.py
   ```

2. **Serve the frontend**
   ```bash
   cd src/frontend
   # Use any web server, e.g.:
   python -m http.server 8080
   ```

3. **Access the application**
   Open your browser to `http://localhost:8080`

## Usage

1. **Select an Area**: Draw a rectangle on the map to define your area of interest
2. **Set Time Periods**: Configure pre-event and post-event date ranges
3. **Choose Data Source**: Select satellite imagery provider and parameters
4. **Generate Analysis**: Process imagery to detect road networks
5. **Compare Results**: Visualize changes and identify damage
6. **Export Data**: Download results for further analysis

## Configuration

### Environment Variables
- `GEE_PROJECT`: Google Earth Engine project ID
- `MAXAR_API_KEY`: Maxar imagery API key

### Model Configuration
Model parameters can be adjusted in the YAML configuration files located in `src/backend/model_files/`.

## API Endpoints

- `GET /api/satellite_image`: Fetch satellite imagery
- `POST /api/process_image`: Process imagery for road detection
- `GET /api/compare_roads`: Compare pre/post road networks
- `GET /api/download/*`: Download processed results

## Development

### Code Quality
The codebase follows Python PEP 8 standards and includes:
- Comprehensive logging throughout the application
- Error handling and validation
- Modular, extensible architecture
- Clean separation of concerns

### Testing
```bash
# Run backend tests
cd src/backend/data_processing
python -m pytest

# Run specific test modules
python -m unittest graph_utils.TestGraphUtils
```

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## Contributing

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/new-feature`)
3. Commit your changes (`git commit -am 'Add new feature'`)
4. Push to the branch (`git push origin feature/new-feature`)
5. Create a Pull Request

## Support

For issues and questions, please open an issue on the GitHub repository.

## Citation

If you use this software in your research, please cite:

```bibtex
@software{eodt4crises,
  title = {EODT4Crises: Earth Observation Data Tools for Crisis Response},
  author = {ICG Innovation},
  year = {2025},
  url = {https://github.com/icg-innovation/EODT4Crises}
}
```
