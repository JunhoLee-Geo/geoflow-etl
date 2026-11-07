import geopandas as gpd
import numpy as np
from shapely import get_coordinates

from geoflow.exceptions import QualityError


def transform(frame: gpd.GeoDataFrame, target_crs: str) -> gpd.GeoDataFrame:
    """Reproject without geometry repair or implicit row removal."""
    try:
        transformed = frame.to_crs(target_crs)
        for geometry in transformed.geometry:
            if not np.isfinite(get_coordinates(geometry)).all() or not geometry.is_valid:
                raise QualityError("Reprojection produced unusable geometry; source retained")
        return transformed
    except QualityError:
        raise
    except Exception as exc:
        raise QualityError("Reprojection failed; source retained") from exc
