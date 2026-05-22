"""Create an interactive choropleth map for checking heat demand results."""

import json
from pathlib import Path

import geopandas as gpd
import pandas as pd


def _read_heat_demand(path: str, max_steps: int) -> pd.DataFrame:
    demand = pd.read_parquet(path)
    demand.index = pd.to_datetime(demand.index)
    demand = demand.sort_index()
    if len(demand) > max_steps:
        demand = demand.resample("D").mean()
    return demand


def _read_shapes(path: str, shape_ids: list[str]) -> gpd.GeoDataFrame:
    shapes = gpd.read_parquet(path)
    required = {"shape_id", "geometry"}
    missing = required.difference(shapes.columns)
    if missing:
        raise ValueError(f"Missing required shape columns: {sorted(missing)}")

    shapes = shapes.copy()
    shapes["shape_id"] = shapes["shape_id"].astype(str).str.replace(".", "-", regex=False)
    if "country_id" not in shapes.columns:
        shapes["country_id"] = shapes["shape_id"]

    shapes = shapes[shapes["shape_id"].isin(shape_ids)]
    if shapes.empty:
        raise ValueError("No shapes match the heat demand columns.")

    shapes = shapes.to_crs("EPSG:4326")
    return shapes[["shape_id", "country_id", "geometry"]]


def _write_html(
    output_path: str,
    shapes: gpd.GeoDataFrame,
    demand: pd.DataFrame,
) -> None:
    common_ids = [col for col in demand.columns.astype(str) if col in set(shapes.shape_id)]
    demand = demand[common_ids]
    shapes = shapes[shapes.shape_id.isin(common_ids)]

    if demand.empty:
        raise ValueError("No overlapping shape IDs between shapes and heat demand data.")

    geojson = json.loads(shapes.to_json())
    timestamps = [ts.isoformat() for ts in demand.index]
    values = {
        shape_id: [None if pd.isna(value) else round(float(value), 6) for value in demand[shape_id]]
        for shape_id in common_ids
    }
    finite_values = demand.to_numpy().ravel()
    finite_values = finite_values[pd.notna(finite_values)]
    max_value = float(finite_values.max()) if len(finite_values) else 0

    html = HTML_TEMPLATE.format(
        geojson=json.dumps(geojson),
        timestamps=json.dumps(timestamps),
        values=json.dumps(values),
        max_value=json.dumps(max_value),
    )

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    Path(output_path).write_text(html)


HTML_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Heat Demand</title>
  <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
  <style>
    html, body {{ height: 100%; margin: 0; font-family: system-ui, sans-serif; }}
    body {{ display: grid; grid-template-rows: 1fr auto; }}
    #map {{ min-height: 0; }}
    #controls {{
      display: grid;
      grid-template-columns: auto 1fr auto;
      gap: 12px;
      align-items: center;
      padding: 10px 14px;
      border-top: 1px solid #ddd;
      background: #fff;
    }}
    #timeSlider {{ width: 100%; }}
    .legend {{
      background: white;
      padding: 8px;
      border: 1px solid #ccc;
      line-height: 1.2;
    }}
    .legendBar {{
      height: 10px;
      width: 160px;
      margin: 6px 0;
      background: linear-gradient(to right, #f7fbff, #6baed6, #08306b);
    }}
  </style>
</head>
<body>
  <div id="map"></div>
  <div id="controls">
    <button id="prev" type="button">Prev</button>
    <input id="timeSlider" type="range" min="0" max="0" value="0">
    <button id="next" type="button">Next</button>
    <strong id="timestamp"></strong>
  </div>
  <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
  <script>
    const geojson = {geojson};
    const timestamps = {timestamps};
    const values = {values};
    const maxValue = {max_value};

    const map = L.map('map');
    const layer = L.geoJSON(geojson, {{
      smoothFactor: 0,
      style: feature => styleFeature(feature, 0),
      onEachFeature: (feature, layer) => {{
        layer.bindTooltip('');
      }}
    }}).addTo(map);

    map.fitBounds(layer.getBounds(), {{ padding: [20, 20] }});
    L.tileLayer('https://{{s}}.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png', {{
      maxZoom: 18,
      attribution: '&copy; OpenStreetMap contributors'
    }}).addTo(map);

    const legend = L.control({{ position: 'bottomright' }});
    legend.onAdd = () => {{
      const div = L.DomUtil.create('div', 'legend');
      div.innerHTML = `<strong>Heat demand</strong><div class="legendBar"></div><span>0</span><span style="float:right">${{formatValue(maxValue)}}</span>`;
      return div;
    }};
    legend.addTo(map);

    const slider = document.getElementById('timeSlider');
    slider.max = Math.max(0, timestamps.length - 1);
    slider.addEventListener('input', () => update(Number(slider.value)));
    document.getElementById('prev').addEventListener('click', () => update(Math.max(0, Number(slider.value) - 1)));
    document.getElementById('next').addEventListener('click', () => update(Math.min(timestamps.length - 1, Number(slider.value) + 1)));

    function color(value) {{
      if (value == null || maxValue <= 0) return '#f0f0f0';
      const ratio = Math.max(0, Math.min(1, value / maxValue));
      if (ratio < 0.5) {{
        return interpolate('#f7fbff', '#6baed6', ratio * 2);
      }}
      return interpolate('#6baed6', '#08306b', (ratio - 0.5) * 2);
    }}

    function interpolate(a, b, t) {{
      const ah = parseInt(a.slice(1), 16), bh = parseInt(b.slice(1), 16);
      const ar = ah >> 16, ag = (ah >> 8) & 255, ab = ah & 255;
      const br = bh >> 16, bg = (bh >> 8) & 255, bb = bh & 255;
      const rr = ar + t * (br - ar), rg = ag + t * (bg - ag), rb = ab + t * (bb - ab);
      return '#' + ((1 << 24) + (rr << 16) + (rg << 8) + rb).toString(16).slice(1);
    }}

    function styleFeature(feature, step) {{
      const shapeId = feature.properties.shape_id;
      return {{
        color: '#555',
        weight: 1,
        fillColor: color(values[shapeId]?.[step]),
        fillOpacity: 0.75
      }};
    }}

    function update(step) {{
      slider.value = step;
      document.getElementById('timestamp').textContent = timestamps[step] || '';
      layer.eachLayer(region => {{
        const shapeId = region.feature.properties.shape_id;
        const value = values[shapeId]?.[step];
        region.setStyle(styleFeature(region.feature, step));
        region.setTooltipContent(`${{shapeId}}<br>${{formatValue(value)}}`);
      }});
    }}

    function formatValue(value) {{
      if (value == null) return 'n/a';
      return Number(value).toLocaleString(undefined, {{ maximumFractionDigits: 3 }});
    }}

    update(0);
  </script>
</body>
</html>
"""


if __name__ == "__main__":
    heat_demand = _read_heat_demand(
        snakemake.input.heat_demand,
        max_steps=snakemake.params.max_steps,
    )
    shape_ids = heat_demand.columns.astype(str).tolist()
    shapes = _read_shapes(snakemake.input.shapes, shape_ids)
    _write_html(snakemake.output[0], shapes, heat_demand)
