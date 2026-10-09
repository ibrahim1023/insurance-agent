import cv2
import numpy as np


def valid_quad(value):
    if value is None:
        return None
    try:
        quad = np.asarray(value, dtype=np.float32)
    except (ValueError, TypeError):
        return None
    if quad.shape != (4, 2) or not np.isfinite(quad).all():
        return None
    if not cv2.isContourConvex(quad) or abs(cv2.contourArea(quad)) < 1e-6:
        return None
    return quad


def measure_spine_cm(marker_quad: np.ndarray | None, spine_quad: np.ndarray, *, same_plane: bool) -> tuple[float, float] | None:
    if same_plane is not True:
        return None
    marker = valid_quad(marker_quad)
    spine = valid_quad(spine_quad)
    if marker is None or spine is None:
        return None
    destination = np.array([[0, 0], [21, 0], [21, 29.7], [0, 29.7]], dtype=np.float32)
    transform = cv2.getPerspectiveTransform(marker, destination)
    points = np.column_stack((spine, np.ones(4))) @ transform.T
    if not np.isfinite(points).all() or np.any(np.abs(points[:, 2]) < 1e-9):
        return None
    rectified = points[:, :2] / points[:, 2, None]
    thickness = (np.linalg.norm(rectified[1] - rectified[0]) + np.linalg.norm(rectified[2] - rectified[3])) / 2
    height = (np.linalg.norm(rectified[3] - rectified[0]) + np.linalg.norm(rectified[2] - rectified[1])) / 2
    if not np.isfinite([height, thickness]).all() or min(height, thickness) <= 0:
        return None
    return float(height), float(thickness)
