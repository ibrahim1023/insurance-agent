import importlib

import numpy as np
import pytest


def measure(marker, spine, same_plane=True):
    return importlib.import_module("preflight.measure").measure_spine_cm(
        marker, spine, same_plane=same_plane,
    )


def test_rectified_known_rectangle():
    marker = np.array([[0, 0], [210, 0], [210, 297], [0, 297]], dtype=float)
    spine = np.array([[240, 10], [260, 10], [260, 250], [240, 250]], dtype=float)
    height, thickness = measure(marker, spine)
    assert height == pytest.approx(24, abs=0.1)
    assert thickness == pytest.approx(2, abs=0.1)


def test_oblique_same_plane_rectangle():
    marker = np.array([[50, 80], [134, 101], [163.7, 279.2], [79.7, 258.2]])
    spine = np.array([[140, 114], [148, 116], [172, 260], [164, 258]])
    height, thickness = measure(marker, spine)
    assert height == pytest.approx(24, abs=0.1)
    assert thickness == pytest.approx(2, abs=0.1)


@pytest.mark.parametrize("marker", [None, np.zeros((4, 2)), np.array([[0, 0], [1, 0], [1, 0], [0, 0]])])
def test_missing_or_degenerate_marker(marker):
    spine = np.array([[240, 10], [260, 10], [260, 250], [240, 250]], dtype=float)
    assert measure(marker, spine) is None


def test_other_plane():
    marker = np.array([[0, 0], [210, 0], [210, 297], [0, 297]], dtype=float)
    spine = np.array([[240, 10], [260, 10], [260, 250], [240, 250]], dtype=float)
    assert measure(marker, spine, same_plane=False) is None


def test_nonfinite_corners():
    marker = np.array([[0, 0], [210, 0], [210, 297], [0, float("nan")]])
    spine = np.array([[240, 10], [260, 10], [260, 250], [240, 250]], dtype=float)
    assert measure(marker, spine) is None
