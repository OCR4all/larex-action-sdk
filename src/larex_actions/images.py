from __future__ import annotations

from collections.abc import Mapping, Sequence
from io import BytesIO
from typing import Any

from PIL import Image

Point = tuple[float, float]
Box = tuple[int, int, int, int]


def extract_points(coords: object) -> list[Point]:
    """Extract polygon points from LAREX target geometry."""
    raw_points: object = coords.get("points") if isinstance(coords, Mapping) else coords

    if isinstance(raw_points, str):
        return [_parse_point_pair(pair) for pair in raw_points.split() if pair.strip()]

    if isinstance(raw_points, Sequence) and not isinstance(raw_points, bytes | bytearray | str):
        points: list[Point] = []
        for point in raw_points:
            parsed = _parse_point(point)
            if parsed is not None:
                points.append(parsed)
        return points

    return []


def bounding_box(
    coords: object,
    *,
    padding: int = 0,
    image_size: tuple[int, int] | None = None,
) -> Box:
    """Return the integer bounding box for LAREX polygon coordinates."""
    points = extract_points(coords)
    if not points:
        raise ValueError("coords does not contain polygon points")

    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    left = int(min(xs)) - padding
    top = int(min(ys)) - padding
    right = int(max(xs)) + padding
    bottom = int(max(ys)) + padding

    if image_size is not None:
        width, height = image_size
        left = max(0, min(left, width))
        right = max(0, min(right, width))
        top = max(0, min(top, height))
        bottom = max(0, min(bottom, height))

    if right <= left or bottom <= top:
        raise ValueError("coords produce an empty crop box")
    return left, top, right, bottom


def crop_image_bytes(
    image_bytes: bytes,
    coords: object,
    *,
    padding: int = 0,
    image_format: str | None = None,
) -> bytes:
    """Crop image bytes to the target polygon bounding box."""
    with Image.open(BytesIO(image_bytes)) as image:
        box = bounding_box(coords, padding=padding, image_size=image.size)
        cropped = image.crop(box)
        output = BytesIO()
        cropped.save(output, format=image_format or image.format or "PNG")
        return output.getvalue()


def _parse_point(point: object) -> Point | None:
    if isinstance(point, Mapping):
        x = _number(point.get("x"))
        y = _number(point.get("y"))
        return None if x is None or y is None else (x, y)

    if isinstance(point, Sequence) and not isinstance(point, bytes | bytearray | str):
        if len(point) < 2:
            return None
        x = _number(point[0])
        y = _number(point[1])
        return None if x is None or y is None else (x, y)

    if isinstance(point, str):
        return _parse_point_pair(point)

    return None


def _parse_point_pair(value: str) -> Point:
    x_value, y_value = value.split(",", 1)
    return float(x_value), float(y_value)


def _number(value: Any) -> float | None:
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str) and value.strip():
        return float(value)
    return None
