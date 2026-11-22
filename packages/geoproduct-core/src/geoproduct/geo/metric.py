"""Distances, areas, lengths and buffers in metres.

Measurements use a local equirectangular projection centred on the feature being measured.
Centring keeps the error second-order in the feature's size: well under 1% for anything up
to tens of kilometres across, which covers the street- and city-scale work this toolkit is
for. It is not meant for continental-scale geometry.
"""

import math

import numpy as np
import shapely
from numpy.typing import NDArray
from shapely.geometry import Point
from shapely.geometry.base import BaseGeometry

EARTH_RADIUS_M = 6_371_008.8
"""Mean Earth radius (IUGG)."""


def haversine_m(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = phi2 - phi1
    dlmb = math.radians(_wrap(lon2 - lon1))
    h = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlmb / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(min(1.0, math.sqrt(h)))


def metres_to_degrees(metres: float, lat: float) -> float:
    """A distance in degrees that covers ``metres`` in every direction at this latitude."""
    cos_lat = max(math.cos(math.radians(lat)), 0.01)
    return math.degrees(metres / EARTH_RADIUS_M) / cos_lat


class LocalProjection:
    """Equirectangular projection centred on (lon0, lat0), in metres."""

    def __init__(self, lon0: float, lat0: float) -> None:
        self.lon0 = lon0
        self.lat0 = lat0
        self._kx = math.radians(1) * EARTH_RADIUS_M * math.cos(math.radians(lat0))
        self._ky = math.radians(1) * EARTH_RADIUS_M

    @classmethod
    def around(cls, geom: BaseGeometry) -> "LocalProjection":
        centre = geom.centroid if not geom.is_empty else Point(0, 0)
        return cls(float(centre.x), float(centre.y))

    def forward(self, geom: BaseGeometry) -> BaseGeometry:
        return shapely.transform(geom, self._forward)

    def inverse(self, geom: BaseGeometry) -> BaseGeometry:
        return shapely.transform(geom, self._inverse)

    def _forward(self, coords: NDArray[np.float64]) -> NDArray[np.float64]:
        x = (np.mod(coords[:, 0] - self.lon0 + 180.0, 360.0) - 180.0) * self._kx
        y = (coords[:, 1] - self.lat0) * self._ky
        return np.column_stack([x, y])

    def _inverse(self, coords: NDArray[np.float64]) -> NDArray[np.float64]:
        lon = coords[:, 0] / self._kx + self.lon0
        lat = coords[:, 1] / self._ky + self.lat0
        return np.column_stack([np.mod(lon + 180.0, 360.0) - 180.0, lat])


def distance_m(a: BaseGeometry, b: BaseGeometry) -> float:
    """Shortest distance between two geometries, in metres (0 if they touch)."""
    projection = LocalProjection.around(shapely.union(a.centroid, b.centroid))
    return float(projection.forward(a).distance(projection.forward(b)))


def area_m2(geom: BaseGeometry) -> float:
    return float(LocalProjection.around(geom).forward(geom).area)


def length_m(geom: BaseGeometry) -> float:
    """Length of lines, or perimeter of polygons."""
    return float(LocalProjection.around(geom).forward(geom).length)


def buffer(geom: BaseGeometry, metres: float) -> BaseGeometry:
    projection = LocalProjection.around(geom)
    return projection.inverse(projection.forward(geom).buffer(metres))


def circle(lon: float, lat: float, radius_m: float) -> BaseGeometry:
    projection = LocalProjection(lon, lat)
    return projection.inverse(Point(0, 0).buffer(radius_m))


def _wrap(degrees: float) -> float:
    return (degrees + 180.0) % 360.0 - 180.0
