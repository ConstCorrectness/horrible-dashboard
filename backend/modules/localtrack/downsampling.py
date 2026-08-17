"""Time-series downsampling algorithms and smoothing filters.

Includes the Largest Triangle Three Buckets (LTTB) algorithm, which preserves
peaks, valleys, and structural features far better than naive decimation or
simple min/max bucket subsampling.
"""

from __future__ import annotations

import math
from typing import Sequence


def lttb(points: Sequence[tuple[float, float]], threshold: int) -> list[tuple[float, float]]:
    """Downsample (x, y) points using the Largest Triangle Three Buckets (LTTB) algorithm.

    Args:
        points: Sequence of (x, y) numeric tuples sorted by x.
        threshold: Desired number of points to return (must be >= 2).

    Returns:
        List of downsampled (x, y) tuples.
    """
    length = len(points)
    if threshold >= length or threshold <= 2:
        return list(points)

    sampled: list[tuple[float, float]] = []

    # Bucket size. Leave room for start and end data points.
    every = (length - 2) / (threshold - 2)

    # First point is always included
    a = 0
    sampled.append(points[a])

    for i in range(threshold - 2):
        # Calculate point average for next bucket (bucket c)
        avg_x = 0.0
        avg_y = 0.0
        avg_range_start = int(math.floor((i + 1) * every) + 1)
        avg_range_end = int(math.floor((i + 2) * every) + 1)
        avg_range_end = min(avg_range_end, length)

        avg_range_length = avg_range_end - avg_range_start
        if avg_range_length > 0:
            for j in range(avg_range_start, avg_range_end):
                avg_x += points[j][0]
                avg_y += points[j][1]
            avg_x /= avg_range_length
            avg_y /= avg_range_length
        else:
            avg_x, avg_y = points[min(avg_range_start, length - 1)]

        # Get the range for this bucket (bucket b)
        range_offs = int(math.floor(i * every) + 1)
        range_to = int(math.floor((i + 1) * every) + 1)

        # Point a
        point_a_x, point_a_y = points[a]

        max_area = -1.0
        next_a = range_offs

        for j in range(range_offs, min(range_to, length)):
            # Calculate triangle area over points a, point[j], and average point c
            pt_x, pt_y = points[j]
            area = abs(
                (point_a_x - avg_x) * (pt_y - point_a_y)
                - (point_a_x - pt_x) * (avg_y - point_a_y)
            ) * 0.5

            if area > max_area:
                max_area = area
                next_a = j

        sampled.append(points[next_a])
        a = next_a

    # Always include last point
    sampled.append(points[length - 1])
    return sampled


def ema_smooth(values: Sequence[float], weight: float = 0.6) -> list[float]:
    """Compute Exponential Moving Average (EMA) smoothing over a sequence of values.

    Args:
        values: Raw float sequence.
        weight: Smoothing factor between 0.0 (no smoothing) and 0.999 (heavy smoothing).

    Returns:
        Smoothed float list.
    """
    if not values or weight <= 0.0:
        return list(values)

    weight = min(weight, 0.99)
    smoothed: list[float] = []
    last = values[0]

    for v in values:
        if math.isnan(v) or math.isinf(v):
            smoothed.append(v)
            continue
        last = last * weight + (1 - weight) * v
        # Debias the early steps
        smoothed.append(last)

    return smoothed
