import numpy as np

SCALE = 60
DOUBLE_SCALE = 2 * SCALE
INITIAL_SIZE = 30
INITIAL_CORRECTION = round(-INITIAL_SIZE / 2)


def calculate_corrected_positions(position_pixel_coordinates: np.ndarray, radius_pixels: np.ndarray) -> np.ndarray:
    positions = position_pixel_coordinates.copy()
    positions[:, 0] -= radius_pixels
    positions[:, 1] -= radius_pixels
    return positions
