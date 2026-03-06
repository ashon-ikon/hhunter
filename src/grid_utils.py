"""Utilities for deterministic square-grid assignment over snapshot listings."""

from __future__ import annotations

import math
from dataclasses import dataclass

import pandas as pd

DEFAULT_CELL_SIZE_M = 400.0
METERS_PER_DEGREE_LAT = 111_320.0


@dataclass(frozen=True)
class GridSpec:
    cell_size_m: float
    lat_origin: float
    lng_origin: float
    x_min_m: float
    y_min_m: float
    cols: int
    rows: int


def _meters_per_degree_lng(lat_origin: float) -> float:
    return METERS_PER_DEGREE_LAT * math.cos(math.radians(lat_origin))


def project_to_local_meters(
    lat: pd.Series,
    lng: pd.Series,
    lat_origin: float,
    lng_origin: float,
) -> tuple[pd.Series, pd.Series]:
    meters_per_degree_lng = _meters_per_degree_lng(lat_origin)
    x_m = (lng - lng_origin) * meters_per_degree_lng
    y_m = (lat - lat_origin) * METERS_PER_DEGREE_LAT
    return x_m, y_m


def inverse_project_to_lat_lng(
    x_m: float,
    y_m: float,
    lat_origin: float,
    lng_origin: float,
) -> tuple[float, float]:
    meters_per_degree_lng = _meters_per_degree_lng(lat_origin)
    lat = lat_origin + (y_m / METERS_PER_DEGREE_LAT)
    lng = lng_origin + (x_m / meters_per_degree_lng)
    return lat, lng


def grid_spec_for_listings(df: pd.DataFrame, cell_size_m: float = DEFAULT_CELL_SIZE_M) -> GridSpec:
    coords = df[["lat", "lng"]].dropna()
    if coords.empty:
        raise ValueError("Listings are missing lat/lng coordinates required for grid analysis.")

    lat_origin = float(coords["lat"].min())
    lng_origin = float(coords["lng"].min())
    x_m, y_m = project_to_local_meters(coords["lat"], coords["lng"], lat_origin, lng_origin)
    x_min_m = math.floor(float(x_m.min()) / cell_size_m) * cell_size_m
    y_min_m = math.floor(float(y_m.min()) / cell_size_m) * cell_size_m
    cols = int(math.floor((float(x_m.max()) - x_min_m) / cell_size_m) + 1)
    rows = int(math.floor((float(y_m.max()) - y_min_m) / cell_size_m) + 1)
    return GridSpec(
        cell_size_m=float(cell_size_m),
        lat_origin=lat_origin,
        lng_origin=lng_origin,
        x_min_m=x_min_m,
        y_min_m=y_min_m,
        cols=max(cols, 1),
        rows=max(rows, 1),
    )


def assign_grid_fields(df: pd.DataFrame, spec: GridSpec) -> pd.DataFrame:
    work = df.copy()
    has_coords = work["lat"].notna() & work["lng"].notna()
    work["grid_id"] = pd.NA
    work["grid_row"] = pd.NA
    work["grid_col"] = pd.NA
    work["grid_centroid_lat"] = pd.NA
    work["grid_centroid_lng"] = pd.NA

    if not has_coords.any():
        return work

    x_m, y_m = project_to_local_meters(
        work.loc[has_coords, "lat"],
        work.loc[has_coords, "lng"],
        spec.lat_origin,
        spec.lng_origin,
    )
    grid_col = ((x_m - spec.x_min_m) / spec.cell_size_m).floordiv(1).astype("Int64")
    grid_row = ((y_m - spec.y_min_m) / spec.cell_size_m).floordiv(1).astype("Int64")

    centroid_lat: list[float] = []
    centroid_lng: list[float] = []
    grid_ids: list[str] = []
    for row, col in zip(grid_row.tolist(), grid_col.tolist()):
        center_x = spec.x_min_m + ((int(col) + 0.5) * spec.cell_size_m)
        center_y = spec.y_min_m + ((int(row) + 0.5) * spec.cell_size_m)
        lat, lng = inverse_project_to_lat_lng(center_x, center_y, spec.lat_origin, spec.lng_origin)
        centroid_lat.append(lat)
        centroid_lng.append(lng)
        grid_ids.append(f"g{int(spec.cell_size_m)}_r{int(row):03d}_c{int(col):03d}")

    work.loc[has_coords, "grid_id"] = grid_ids
    work.loc[has_coords, "grid_row"] = grid_row
    work.loc[has_coords, "grid_col"] = grid_col
    work.loc[has_coords, "grid_centroid_lat"] = centroid_lat
    work.loc[has_coords, "grid_centroid_lng"] = centroid_lng
    return work


def grid_cell_polygon(spec: GridSpec, grid_row: int, grid_col: int) -> list[list[float]]:
    x0 = spec.x_min_m + (grid_col * spec.cell_size_m)
    y0 = spec.y_min_m + (grid_row * spec.cell_size_m)
    x1 = x0 + spec.cell_size_m
    y1 = y0 + spec.cell_size_m
    corners = [
        inverse_project_to_lat_lng(x0, y0, spec.lat_origin, spec.lng_origin),
        inverse_project_to_lat_lng(x1, y0, spec.lat_origin, spec.lng_origin),
        inverse_project_to_lat_lng(x1, y1, spec.lat_origin, spec.lng_origin),
        inverse_project_to_lat_lng(x0, y1, spec.lat_origin, spec.lng_origin),
        inverse_project_to_lat_lng(x0, y0, spec.lat_origin, spec.lng_origin),
    ]
    return [[lng, lat] for lat, lng in corners]
