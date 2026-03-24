"""
hcad_maps.py — Generate Folium choropleth heat-maps from HCAD score DataFrames.

Each map is saved as a self-contained static HTML file in static/hcad_maps/.
OpenStreetMap tiles, no API key required.

Usage (CLI):
    python -m src.hcad_maps          # generate all maps
    python -m src.hcad_maps price    # generate one map
"""
from __future__ import annotations

import json
import logging
import sys
import urllib.parse
from datetime import datetime
from pathlib import Path
from typing import Callable

import folium
import folium.plugins
import pandas as pd
import requests

from src.hcad_ingest import ingest, DB_PATH as INGEST_DB_PATH
from src.hcad_scores import all_scores, connect as scores_connect

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

OUT_DIR     = Path("static/hcad_maps")
GEO_CACHE   = Path("data/houston_zcta.geojson")
DB_PATH     = Path("data/hcad.duckdb")

# Houston city centre — default map centre
HOUSTON_LAT = 29.7604
HOUSTON_LNG = -95.3698

# ZIPs of primary interest — highlighted with a thicker border
FOCUS_ZIPS  = {"77021", "77088"}

# Rough bounding box for Harris County + surroundings
BBOX = (-95.9, 29.4, -94.9, 30.2)   # west, south, east, north

# ---------------------------------------------------------------------------
# GeoJSON helpers
# ---------------------------------------------------------------------------


ZIP_PROP = "ZCTA5CE10"   # property key in the Texas GeoJSON source

TEXAS_ZIPS_URL = (
    "https://raw.githubusercontent.com/OpenDataDE/State-zip-code-GeoJSON"
    "/master/tx_texas_zip_codes_geo.min.json"
)


def load_zcta_geojson(zip_codes: list[str]) -> dict:
    """Return GeoJSON FeatureCollection for the given ZIP codes.

    Checks the local cache first (data/houston_zcta.geojson), then downloads
    all Texas ZIP polygons and filters to ZIPs present in our HCAD data.
    """
    if GEO_CACHE.exists():
        log.info("Loading cached ZCTA GeoJSON …")
        with open(GEO_CACHE) as fh:
            return json.load(fh)

    log.info("Downloading Texas ZIP boundaries (~80 MB, cached after first run) …")
    resp = requests.get(TEXAS_ZIPS_URL, timeout=120)
    resp.raise_for_status()
    geojson = resp.json()

    # Normalise: add a plain "ZCTA5CE" alias so downstream code stays generic
    for f in geojson["features"]:
        f["properties"]["ZCTA5CE"] = f["properties"].get(ZIP_PROP, "")

    # Filter to ZIPs actually in our HCAD data
    zip_set = set(zip_codes)
    geojson["features"] = [
        f for f in geojson["features"]
        if f["properties"]["ZCTA5CE"] in zip_set
    ]
    log.info("  %d ZCTA polygons retained for Houston area", len(geojson["features"]))

    GEO_CACHE.parent.mkdir(parents=True, exist_ok=True)
    with open(GEO_CACHE, "w") as fh:
        json.dump(geojson, fh)
    log.info("  Cached to %s", GEO_CACHE)

    return geojson


def _enrich_geojson(geojson: dict, df: pd.DataFrame, zip_col: str = "zip") -> dict:
    """Merge DataFrame rows into each GeoJSON feature's properties dict."""
    lookup = df.set_index(zip_col).to_dict(orient="index")
    for feature in geojson["features"]:
        z = feature["properties"].get("ZCTA5CE")
        if z in lookup:
            feature["properties"].update({
                str(k): (None if pd.isna(v) else v)
                for k, v in lookup[z].items()
            })
    return geojson


# ---------------------------------------------------------------------------
# All-ZIP data builder  (merged across all 6 score DataFrames)
# ---------------------------------------------------------------------------

# Which columns to pull from each score DataFrame for the ZIP insert card
_ALL_ZIP_COLS: dict[str, list[str]] = {
    "price":          ["zip", "median_price_per_sqft", "median_value", "median_sqft", "num_properties"],
    "yoy":            ["zip", "median_yoy_pct", "pct_appreciating", "pct_surging"],
    "investor":       ["zip", "investor_pct", "investor_owned"],
    "permits":        ["zip", "recent_permit_rate", "permits_since_2023", "new_construction"],
    "gentrification": ["zip", "gentrification_score"],
    "flip":           ["zip", "flip_score", "pct_below_rcn", "pct_unrenovated",
                       "median_building_age", "median_years_held"],
}


def build_all_zip_json(scores: dict[str, pd.DataFrame]) -> str:
    """Merge all score DataFrames by ZIP and return a JSON string.

    Shape: { "77021": { "median_price_per_sqft": 145, "median_yoy_pct": 8.2, ... }, ... }
    Embedded verbatim in each map HTML so the ZIP insert card has full cross-metric data.
    """
    merged: pd.DataFrame | None = None
    for name, cols in _ALL_ZIP_COLS.items():
        df = scores.get(name)
        if df is None:
            continue
        available = [c for c in cols if c in df.columns]
        subset = df[available].copy()
        if merged is None:
            merged = subset
        else:
            merged = merged.merge(subset, on="zip", how="outer")

    if merged is None:
        return "{}"

    result: dict[str, dict] = {}
    for _, row in merged.iterrows():
        z = str(row["zip"])
        result[z] = {
            k: (None if pd.isna(v) else round(float(v), 2))
            for k, v in row.items()
            if k != "zip"
        }
    # Escape </script> sequences that could break inline <script> tags
    return json.dumps(result, separators=(",", ":")).replace("</", "<\\/")


# ---------------------------------------------------------------------------
# Snapshot UI — insights panel + ZIP insert card + html2canvas export
# ---------------------------------------------------------------------------


def _build_snapshot_ui(
    df: pd.DataFrame,
    metric_col: str,
    map_title: str,
    fmt: Callable[[float], str],
    metric_key: str,
    all_zip_json: str = "{}",
) -> str:
    """Return the HTML/JS string injected into each Folium map.

    Produces:
      • Right panel  — always-visible insights: focus ZIPs + top 5 + median
      • Left card    — ZIP insert: appears when user clicks a ZIP polygon,
                       shows ALL metrics for that ZIP (cross-map data)
      • Snap button  — top-right; keyboard shortcut S triggers same action
    """
    valid = df.dropna(subset=[metric_col]).copy()
    valid["_rank"] = valid[metric_col].rank(ascending=False, method="min").astype(int)
    total = len(valid)
    houston_median = valid[metric_col].median()
    ts = datetime.now().strftime("%b %d, %Y")

    def _zip_row(zip_code: str, is_focus: bool = False) -> str:
        row = valid[valid["zip"] == zip_code]
        if row.empty:
            icon = "📍" if is_focus else ""
            cls  = "focus-row" if is_focus else ""
            return (
                f'<tr class="{cls}"><td class="td-zip">{icon} {zip_code}</td>'
                f'<td class="td-val" colspan="2" style="opacity:.5">no data</td></tr>'
            )
        r    = row.iloc[0]
        rank = int(r["_rank"])
        pct  = int(100 * (1 - (rank - 1) / max(total, 1)))
        icon = "📍" if is_focus else ""
        cls  = "focus-row" if is_focus else ""
        return (
            f'<tr class="{cls}">'
            f'<td class="td-zip">{icon} {zip_code}</td>'
            f'<td class="td-val">{fmt(r[metric_col])}</td>'
            f'<td class="td-rank">#{rank}&nbsp;/{total}<br>'
            f'<span style="opacity:.6;font-size:9px">{pct}th %ile</span></td>'
            f"</tr>"
        )

    focus_rows = "\n".join(_zip_row(z, is_focus=True) for z in sorted(FOCUS_ZIPS))
    top5 = valid.nsmallest(5, "_rank")
    top5_rows = "\n".join(
        f'<tr><td class="td-zip">{row["zip"]}</td>'
        f'<td class="td-val">{fmt(row[metric_col])}</td>'
        f'<td class="td-rank">#{int(row["_rank"])}</td></tr>'
        for _, row in top5.iterrows()
    )

    js_key = metric_key.replace("'", "\\'")

    return f"""
<!-- ═══ HCAD Snapshot UI ═══ -->
<style>
  /* ── shared panel base ── */
  .hcad-card {{
    position: fixed; z-index: 1500;
    background: rgba(15,23,42,0.93); color: #e2e8f0;
    border-radius: 10px; border: 1px solid rgba(255,255,255,.12);
    padding: 14px 14px 10px;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    font-size: 12px; line-height: 1.4;
    box-shadow: 0 8px 28px rgba(0,0,0,.55);
    backdrop-filter: blur(6px);
  }}
  /* ── right insights panel ── */
  #hcad-panel {{ bottom: 36px; right: 10px; width: 262px; }}
  /* ── left ZIP insert card ── */
  #zip-insert  {{ bottom: 36px; left: 10px; width: 250px; display: none; }}
  #zip-insert.visible {{ display: block; }}

  .panel-eyebrow {{
    font-size: 9px; text-transform: uppercase; letter-spacing: 1.2px;
    opacity: .5; margin-bottom: 3px;
  }}
  .panel-title {{
    font-size: 13px; font-weight: 700; margin-bottom: 10px;
    border-bottom: 1px solid rgba(255,255,255,.1); padding-bottom: 8px;
  }}
  .section-label {{
    font-size: 9px; text-transform: uppercase; letter-spacing: 1px;
    opacity: .5; margin: 8px 0 4px;
  }}
  .hcad-card table {{ width: 100%; border-collapse: collapse; }}
  .focus-row {{ background: rgba(37,99,235,.18); border-radius: 4px; }}
  .focus-row .td-zip {{ font-weight: 600; color: #93c5fd; }}
  .td-zip  {{ padding: 3px 4px 3px 2px; white-space: nowrap; }}
  .td-val  {{ padding: 3px 4px; font-weight: 600; text-align: right;
               color: #fbbf24; white-space: nowrap; }}
  .td-rank {{ padding: 3px 2px 3px 4px; text-align: right;
               opacity: .75; font-size: 10px; white-space: nowrap; }}
  .median-row {{
    font-size: 11px; display: flex; justify-content: space-between;
    padding: 4px 0; border-top: 1px solid rgba(255,255,255,.08); margin-top: 6px;
  }}
  .panel-footer {{
    margin-top: 6px; font-size: 9px; opacity: .4;
    border-top: 1px solid rgba(255,255,255,.06); padding-top: 6px;
  }}
  /* ── ZIP insert specific ── */
  #zi-header {{
    display: flex; align-items: flex-start; justify-content: space-between;
    margin-bottom: 8px; border-bottom: 1px solid rgba(255,255,255,.1); padding-bottom: 8px;
  }}
  #zi-zip {{ font-size: 22px; font-weight: 800; color: #38bdf8; letter-spacing: -.5px; }}
  #zi-close {{
    background: none; border: none; color: rgba(255,255,255,.4);
    font-size: 16px; cursor: pointer; padding: 0; line-height: 1;
    transition: color .15s;
  }}
  #zi-close:hover {{ color: #fff; }}
  .zi-row {{ display: flex; justify-content: space-between; padding: 3px 0;
              border-bottom: 1px solid rgba(255,255,255,.04); }}
  .zi-label {{ opacity: .65; font-size: 11px; }}
  .zi-val   {{ font-weight: 700; font-size: 11px; color: #fbbf24; }}
  /* ── snapshot button ── */
  #snap-btn {{
    position: fixed; top: 80px; right: 10px; z-index: 1600;
    background: #1e40af; color: #fff; border: none; border-radius: 7px;
    padding: 7px 13px; font-size: 12px; font-weight: 600; cursor: pointer;
    box-shadow: 0 3px 10px rgba(0,0,0,.4);
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    transition: background .15s; display: flex; align-items: center; gap: 5px;
  }}
  #snap-btn:hover   {{ background: #1d4ed8; }}
  #snap-btn:active  {{ background: #1e3a8a; }}
  #snap-btn:disabled {{ opacity: .6; cursor: wait; }}
  #snap-shortcut {{ opacity: .55; font-size: 10px; font-weight: 400;
                    background: rgba(255,255,255,.15); border-radius: 3px;
                    padding: 1px 4px; }}
  .snap-spinner {{
    display: none; width: 12px; height: 12px;
    border: 2px solid rgba(255,255,255,.3); border-top-color: #fff;
    border-radius: 50%; animation: hcad-spin .6s linear infinite;
  }}
  @keyframes hcad-spin {{ to {{ transform: rotate(360deg); }} }}
</style>

<!-- ── Right: Insights panel ── -->
<div id="hcad-panel" class="hcad-card">
  <div class="panel-eyebrow">HCAD · Houston SFR · 2026</div>
  <div class="panel-title">{map_title}</div>
  <div class="section-label">Your Focus ZIPs</div>
  <table>{focus_rows}</table>
  <div class="section-label">Top 5 ZIPs</div>
  <table>{top5_rows}</table>
  <div class="median-row">
    <span style="opacity:.6">Houston Median</span>
    <span style="font-weight:700;color:#fbbf24">{fmt(houston_median)}</span>
  </div>
  <div class="panel-footer">
    SFR &amp; Condo only (A1/A2) &nbsp;·&nbsp; Generated {ts}<br>
    Source: HCAD Public Data &nbsp;·&nbsp; house-hunter
  </div>
</div>

<!-- ── Left: ZIP insert card (hidden until ZIP clicked) ── -->
<div id="zip-insert" class="hcad-card">
  <div id="zi-header">
    <div>
      <div class="panel-eyebrow">Selected ZIP</div>
      <div id="zi-zip">—</div>
    </div>
    <button id="zi-close" onclick="closeZipInsert()" title="Dismiss (Esc)">✕</button>
  </div>
  <div id="zi-rows"></div>
  <div class="panel-footer" style="margin-top:8px">
    Click another ZIP to update &nbsp;·&nbsp; Esc to close
  </div>
</div>

<!-- ── Snapshot button ── -->
<button id="snap-btn" onclick="takeSnapshot()">
  <span id="snap-icon">📷</span>
  <span id="snap-label">Save PNG</span>
  <span id="snap-shortcut">S</span>
  <span class="snap-spinner" id="snap-spin"></span>
</button>

<script src="https://html2canvas.hertzen.com/dist/html2canvas.min.js"></script>
<script>
(function () {{

  /* ── All-ZIP cross-metric data ── */
  var HCAD_ALL = {all_zip_json};

  /* ── Metric display config (label, JS formatter) ── */
  var METRICS = [
    {{ k: 'median_price_per_sqft', label: '💰 Price / sqft',
       fmt: function(v) {{ return '$' + Math.round(v).toLocaleString() + '/sqft'; }} }},
    {{ k: 'median_value',          label: '🏷 Median Value',
       fmt: function(v) {{ return '$' + Math.round(v).toLocaleString(); }} }},
    {{ k: 'median_yoy_pct',        label: '📈 YOY Change',
       fmt: function(v) {{ return (v >= 0 ? '+' : '') + v.toFixed(1) + '%'; }} }},
    {{ k: 'investor_pct',          label: '🏢 Investor %',
       fmt: function(v) {{ return v.toFixed(1) + '% owned'; }} }},
    {{ k: 'recent_permit_rate',    label: '🔨 Permit Rate',
       fmt: function(v) {{ return v.toFixed(1) + ' /100 props'; }} }},
    {{ k: 'gentrification_score',  label: '🌆 Gentrif. Score',
       fmt: function(v) {{ return Math.round(v) + ' / 100'; }} }},
    {{ k: 'flip_score',            label: '🔄 Flip Score',
       fmt: function(v) {{ return Math.round(v) + ' / 100'; }} }},
    {{ k: 'pct_below_rcn',         label: '📉 Below RCN',
       fmt: function(v) {{ return v.toFixed(1) + '% of stock'; }} }},
    {{ k: 'pct_unrenovated',       label: '🏚 Unrenovated',
       fmt: function(v) {{ return v.toFixed(1) + '% (10+ yrs)'; }} }},
    {{ k: 'median_building_age',   label: '🗓 Bldg Age',
       fmt: function(v) {{ return Math.round(v) + ' yrs'; }} }},
    {{ k: 'median_sqft',           label: '📐 Median Sqft',
       fmt: function(v) {{ return Math.round(v).toLocaleString() + ' sqft'; }} }},
    {{ k: 'num_properties',        label: '🏘 # Properties',
       fmt: function(v) {{ return Math.round(v).toLocaleString(); }} }},
  ];

  /* ── ZIP insert card ── */
  window.showZipInsert = function(zip) {{
    var data = HCAD_ALL[zip];
    if (!data) return;

    document.getElementById('zi-zip').textContent = zip;

    var html = '';
    METRICS.forEach(function(m) {{
      var v = data[m.k];
      if (v == null) return;
      html += '<div class="zi-row">'
            + '<span class="zi-label">' + m.label + '</span>'
            + '<span class="zi-val">'   + m.fmt(v)  + '</span>'
            + '</div>';
    }});
    document.getElementById('zi-rows').innerHTML = html || '<div style="opacity:.5;font-size:11px">No cross-metric data</div>';

    var card = document.getElementById('zip-insert');
    card.classList.add('visible');
  }};

  window.closeZipInsert = function() {{
    document.getElementById('zip-insert').classList.remove('visible');
  }};

  /* ── Leaflet click handler: attach to all GeoJSON feature layers ── */
  function initClickHandlers() {{
    var mapEl = document.querySelector('.folium-map');
    if (!mapEl) return;
    var mapObj = window[mapEl.id];
    if (!mapObj) return;

    var attached = new Set();
    mapObj.eachLayer(function(layer) {{
      if (typeof layer.eachLayer !== 'function') return;
      layer.eachLayer(function(fl) {{
        if (!fl.feature) return;
        var zip = fl.feature.properties && fl.feature.properties.ZCTA5CE;
        if (!zip || attached.has(zip)) return;
        fl.on('click', function(e) {{
          L.DomEvent.stopPropagation(e);
          showZipInsert(zip);
        }});
        attached.add(zip);
      }});
    }});
  }}

  // Give Leaflet time to finish rendering layers
  setTimeout(initClickHandlers, 600);

  /* ── Keyboard shortcuts ── */
  document.addEventListener('keydown', function(e) {{
    // Skip if focus is in a text field
    if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return;
    if (e.key === 'Escape') {{ closeZipInsert(); return; }}
    // S — snapshot (not Ctrl+S which is browser save)
    if ((e.key === 's' || e.key === 'S') && !e.ctrlKey && !e.metaKey) {{
      e.preventDefault();
      takeSnapshot();
    }}
  }});

  /* ── Snapshot ── */
  var btn   = document.getElementById('snap-btn');
  var icon  = document.getElementById('snap-icon');
  var label = document.getElementById('snap-label');
  var sc    = document.getElementById('snap-shortcut');
  var spin  = document.getElementById('snap-spin');

  window.takeSnapshot = function() {{
    btn.disabled = true;
    icon.textContent  = '';
    label.textContent = 'Capturing…';
    sc.style.display  = 'none';
    spin.style.display = 'block';

    requestAnimationFrame(function() {{
      html2canvas(document.body, {{
        useCORS:        true,
        allowTaint:     false,
        scale:          1.5,
        backgroundColor:'#f8fafc',
        logging:        false,
        ignoreElements: function(el) {{ return el.id === 'snap-btn'; }},
        onclone: function(d) {{
          var p = d.getElementById('hcad-panel');
          if (p) p.style.display = 'block';
          var zi = d.getElementById('zip-insert');
          if (zi) zi.style.display = zi.classList.contains('visible') ? 'block' : 'none';
        }}
      }}).then(function(canvas) {{
        var today = new Date().toISOString().slice(0, 10);
        var a = document.createElement('a');
        a.download = 'hcad-{js_key}-' + today + '.png';
        a.href = canvas.toDataURL('image/png');
        a.click();
        resetBtn();
      }}).catch(function(err) {{
        console.error('html2canvas:', err);
        label.textContent = '❌ Failed';
        setTimeout(resetBtn, 2500);
      }});
    }});
  }};

  function resetBtn() {{
    btn.disabled = false;
    icon.textContent  = '📷';
    label.textContent = 'Save PNG';
    sc.style.display  = '';
    spin.style.display = 'none';
  }}

}})();
</script>
<!-- ═══ end snapshot UI ═══ -->
"""


def _inject_snapshot_ui(
    m: folium.Map,
    df: pd.DataFrame,
    metric_col: str,
    map_title: str,
    fmt: Callable[[float], str],
    metric_key: str,
    all_zip_json: str = "{}",
) -> None:
    """Inject the full snapshot UI into a Folium map in-place."""
    html = _build_snapshot_ui(df, metric_col, map_title, fmt, metric_key, all_zip_json)
    m.get_root().html.add_child(folium.Element(html))


# ---------------------------------------------------------------------------
# Base map factory
# ---------------------------------------------------------------------------


def _base_map(title: str) -> folium.Map:
    m = folium.Map(
        location=[HOUSTON_LAT, HOUSTON_LNG],
        zoom_start=10,
        tiles="CartoDB positron",
        attr="CartoDB | © OpenStreetMap contributors",
    )
    # Compact title box
    title_html = f"""
    <div style="
        position:fixed; top:12px; left:60px; z-index:1000;
        background:rgba(255,255,255,0.92); padding:8px 14px;
        border-radius:6px; border:1px solid #ccc;
        font-family:sans-serif; font-size:15px; font-weight:600;
        box-shadow:2px 2px 6px rgba(0,0,0,.15);
    ">{title}</div>
    """
    m.get_root().html.add_child(folium.Element(title_html))
    return m


# ---------------------------------------------------------------------------
# Choropleth builder
# ---------------------------------------------------------------------------


def _choropleth(
    m: folium.Map,
    geojson: dict,
    df: pd.DataFrame,
    value_col: str,
    legend_name: str,
    color_scale: str = "YlOrRd",
    tooltip_fields: list[tuple[str, str]] | None = None,
) -> None:
    """Add a choropleth + hover tooltip layer to map `m`."""
    folium.Choropleth(
        geo_data=geojson,
        name=legend_name,
        data=df,
        columns=["zip", value_col],
        key_on="feature.properties.ZCTA5CE",
        fill_color=color_scale,
        fill_opacity=0.65,
        line_opacity=0.3,
        line_color="white",
        legend_name=legend_name,
        nan_fill_color="#cccccc",
        nan_fill_opacity=0.3,
        highlight=True,
    ).add_to(m)

    # Enrich GeoJSON with all data columns for tooltip
    enriched = _enrich_geojson(json.loads(json.dumps(geojson)), df)

    # Tooltip fields: (json_key, display_alias)
    fields  = [f for f, _ in tooltip_fields] if tooltip_fields else [value_col]
    aliases = [a for _, a in tooltip_fields] if tooltip_fields else [legend_name]
    fields  = ["ZCTA5CE"] + fields
    aliases = ["ZIP:"] + aliases

    folium.GeoJson(
        enriched,
        style_function=lambda _: {
            "fillOpacity": 0,
            "weight": 0,
        },
        highlight_function=lambda _: {
            "fillColor": "#ffff00",
            "fillOpacity": 0.15,
            "weight": 2,
            "color": "#333",
        },
        tooltip=folium.GeoJsonTooltip(
            fields=fields,
            aliases=aliases,
            localize=True,
            sticky=True,
            style="font-family:sans-serif;font-size:13px;",
        ),
    ).add_to(m)

    # Bold outline for focus ZIPs
    focus_features = [
        f for f in enriched["features"]
        if f["properties"].get("ZCTA5CE") in FOCUS_ZIPS
    ]
    if focus_features:
        folium.GeoJson(
            {"type": "FeatureCollection", "features": focus_features},
            style_function=lambda _: {
                "fillOpacity": 0,
                "weight": 3,
                "color": "#1a1aff",
                "dashArray": "6 3",
            },
            tooltip=folium.GeoJsonTooltip(
                fields=fields,
                aliases=aliases,
                localize=True,
                sticky=True,
                style="font-family:sans-serif;font-size:13px;font-weight:bold;",
            ),
            name="Focus ZIPs (77021 / 77088)",
        ).add_to(m)

    folium.LayerControl(collapsed=False).add_to(m)


# ---------------------------------------------------------------------------
# Individual map generators
# ---------------------------------------------------------------------------


def map_price(geojson: dict, df: pd.DataFrame, all_zip_json: str = "{}") -> folium.Map:
    title = "Houston SFR — Median Price / sqft"
    m = _base_map(title)
    _choropleth(
        m, geojson, df,
        value_col="median_price_per_sqft",
        legend_name="Median $/sqft",
        color_scale="YlOrRd",
        tooltip_fields=[
            ("median_price_per_sqft", "Median $/sqft:"),
            ("median_value",          "Median Value ($):"),
            ("median_sqft",           "Median Sqft:"),
            ("num_properties",        "# Properties:"),
        ],
    )
    _inject_snapshot_ui(m, df, "median_price_per_sqft", "Median Price / sqft",
                        lambda v: f"${v:,.0f}/sqft", "price", all_zip_json)
    return m


def map_yoy(geojson: dict, df: pd.DataFrame, all_zip_json: str = "{}") -> folium.Map:
    title = "Houston SFR — Year-over-Year Value Change (%)"
    m = _base_map(title)
    _choropleth(
        m, geojson, df,
        value_col="median_yoy_pct",
        legend_name="Median YOY Change (%)",
        color_scale="RdYlGn",
        tooltip_fields=[
            ("median_yoy_pct",      "Median YOY %:"),
            ("pct_appreciating",    "% Appreciating:"),
            ("pct_surging",         "% Rising >10%:"),
            ("num_properties",      "# Properties:"),
        ],
    )
    _inject_snapshot_ui(m, df, "median_yoy_pct", "YOY Value Change",
                        lambda v: f"{v:+.1f}%", "yoy", all_zip_json)
    return m


def map_investor(geojson: dict, df: pd.DataFrame, all_zip_json: str = "{}") -> folium.Map:
    title = "Houston SFR — Investor / LLC Ownership (%)"
    m = _base_map(title)
    _choropleth(
        m, geojson, df,
        value_col="investor_pct",
        legend_name="Investor Ownership (%)",
        color_scale="PuRd",
        tooltip_fields=[
            ("investor_pct",       "Investor %:"),
            ("investor_owned",     "Investor-Owned:"),
            ("num_properties",     "# Properties:"),
        ],
    )
    _inject_snapshot_ui(m, df, "investor_pct", "Investor / LLC Ownership",
                        lambda v: f"{v:.1f}% investor-owned", "investor", all_zip_json)
    return m


def map_permits(geojson: dict, df: pd.DataFrame, all_zip_json: str = "{}") -> folium.Map:
    title = "Houston — Recent Permit Activity (since 2023)"
    m = _base_map(title)
    _choropleth(
        m, geojson, df,
        value_col="recent_permit_rate",
        legend_name="Permits / 100 Properties (2023+)",
        color_scale="Blues",
        tooltip_fields=[
            ("recent_permit_rate",   "Recent Permit Rate %:"),
            ("permits_since_2023",   "Permits Since 2023:"),
            ("new_construction",     "New Construction:"),
            ("remodel_permits",      "Remodels:"),
            ("total_props",          "# Properties:"),
        ],
    )
    _inject_snapshot_ui(m, df, "recent_permit_rate", "Permit Surge (2023+)",
                        lambda v: f"{v:.1f} permits/100 props", "permits", all_zip_json)
    return m


def map_gentrification(geojson: dict, df: pd.DataFrame, all_zip_json: str = "{}") -> folium.Map:
    title = "Houston — Gentrification Score (0–100)"
    m = _base_map(title)
    _choropleth(
        m, geojson, df,
        value_col="gentrification_score",
        legend_name="Gentrification Score",
        color_scale="YlOrRd",
        tooltip_fields=[
            ("gentrification_score",  "Gentrif. Score:"),
            ("median_yoy_pct",        "Median YOY %:"),
            ("recent_permit_rate",    "Permit Rate %:"),
            ("investor_pct",          "Investor %:"),
        ],
    )
    _inject_snapshot_ui(m, df, "gentrification_score", "Gentrification Score",
                        lambda v: f"{v:.0f} / 100", "gentrification", all_zip_json)
    return m


def map_flip(geojson: dict, df: pd.DataFrame, all_zip_json: str = "{}") -> folium.Map:
    title = "Houston — Flip Potential Score (0–100)"
    m = _base_map(title)
    _choropleth(
        m, geojson, df,
        value_col="flip_score",
        legend_name="Flip Potential Score",
        color_scale="YlGn",
        tooltip_fields=[
            ("flip_score",            "Flip Score:"),
            ("pct_below_rcn",         "% Below Replacement Cost:"),
            ("pct_unrenovated",       "% Unrenovated (10+ yrs):"),
            ("median_building_age",   "Median Bldg Age (yrs):"),
            ("median_years_held",     "Median Years Held:"),
            ("median_price_per_sqft", "Median $/sqft:"),
        ],
    )
    _inject_snapshot_ui(m, df, "flip_score", "Flip Potential Score",
                        lambda v: f"{v:.0f} / 100", "flip", all_zip_json)
    return m


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


MAP_REGISTRY: dict[str, tuple[str, callable]] = {
    "price":          ("price_per_sqft.html",       map_price),
    "yoy":            ("yoy_change.html",            map_yoy),
    "investor":       ("investor_activity.html",     map_investor),
    "permits":        ("permit_surge.html",           map_permits),
    "gentrification": ("gentrification_score.html",  map_gentrification),
    "flip":           ("flip_potential.html",         map_flip),
}


def generate_all(score_names: list[str] | None = None) -> dict[str, Path]:
    """Generate HTML map files. Returns {score_name: output_path}."""
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    # --- Load / ingest DB ---
    if not DB_PATH.exists():
        log.info("DB not found — running ingestion first …")
        ingest(db_path=DB_PATH)

    con = scores_connect()
    scores = all_scores(con)
    con.close()

    # --- Build cross-metric ZIP data blob (embedded in every map) ---
    log.info("Building cross-metric ZIP data …")
    all_zip_json = build_all_zip_json(scores)

    # --- Fetch ZCTA GeoJSON ---
    all_zips = sorted(
        set().union(*(set(df["zip"].astype(str)) for df in scores.values() if "zip" in df.columns))
    )
    geojson = load_zcta_geojson(all_zips)

    # --- Render maps ---
    targets = score_names or list(MAP_REGISTRY.keys())
    results: dict[str, Path] = {}

    score_df_map = {
        "price":          scores["price"],
        "yoy":            scores["yoy"],
        "investor":       scores["investor"],
        "permits":        scores["permits"],
        "gentrification": scores["gentrification"],
        "flip":           scores["flip"],
    }

    for name in targets:
        if name not in MAP_REGISTRY:
            log.warning("Unknown map name: %s — skipping", name)
            continue
        fname, fn = MAP_REGISTRY[name]
        df = score_df_map[name]
        log.info("Generating %s …", fname)
        m = fn(geojson, df, all_zip_json)
        out = OUT_DIR / fname
        m.save(str(out))
        log.info("  Saved → %s", out)
        results[name] = out

    return results


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")
    requested = sys.argv[1:] or None
    paths = generate_all(requested)
    print("\nGenerated maps:")
    for name, p in paths.items():
        print(f"  {name:16s} → {p}")


if __name__ == "__main__":
    main()
