"""Server-rendered SVG for the completion-rate trend: no chart library, so
nothing extra for a low-end phone to download or run.

One series, one hue (validated against the page surface), thin columns with
rounded data ends anchored to the baseline, a 2 px gap between columns,
recessive gridlines at 50 % and 100 %. A day with nothing due is a gap, not a
0 % column. Each column carries a <title> tooltip; the template also offers
the same numbers as a table.
"""

from __future__ import annotations

from django.utils.formats import date_format

WIDTH = 300
HEIGHT = 130
LEFT = 26  # gutter for the y-axis labels, so they never sit on a column
TOP = 8
BOTTOM = 20
GAP = 2
RADIUS = 2
FONT = 9  # viewBox units; the figure's max width keeps this ~10-15 px on screen


def _column_path(x: float, y: float, w: float, base: float) -> str:
    """A column with its top (data end) rounded and its foot square on the
    baseline."""
    r = min(RADIUS, w / 2, base - y)
    return (
        f"M{x:.2f},{base:.2f} V{y + r:.2f} Q{x:.2f},{y:.2f} {x + r:.2f},{y:.2f} "
        f"H{x + w - r:.2f} Q{x + w:.2f},{y:.2f} {x + w:.2f},{y + r:.2f} V{base:.2f} Z"
    )


def completion_chart(series: list[dict], highlight_last: int = 7) -> dict:
    n = len(series)
    slot = (WIDTH - LEFT) / n
    bar_w = slot - GAP
    plot_h = HEIGHT - TOP - BOTTOM
    base = TOP + plot_h

    columns = []
    for i, point in enumerate(series):
        x = LEFT + i * slot + GAP / 2
        day = date_format(point["day"], "D j M")
        if point["rate"] is None:
            columns.append({"gap": True, "x": x, "w": bar_w, "title": f"{day}: nothing due"})
            continue
        # A 0 % day still gets a 1 px sliver so it reads as data, not a gap.
        h = max(plot_h * point["rate"] / 100, 1)
        columns.append(
            {
                "gap": False,
                "path": _column_path(x, base - h, bar_w, base),
                "title": f"{day}: {point['done']} of {point['total']} done ({point['rate']}%)",
            }
        )

    return {
        "width": WIDTH,
        "height": HEIGHT,
        "base": base,
        "left": LEFT,
        "font": FONT,
        "grid": [
            {"y": TOP, "label_y": TOP + FONT / 3, "label": "100%"},
            {"y": TOP + plot_h / 2, "label_y": TOP + plot_h / 2 + FONT / 3, "label": "50%"},
        ],
        "axis_label_x": LEFT - 3,
        "columns": columns,
        "window_x": LEFT + (n - highlight_last) * slot,
        "window_w": highlight_last * slot,
        "first_label": date_format(series[0]["day"], "j M"),
        "last_label": date_format(series[-1]["day"], "j M"),
        "label_y": HEIGHT - 4,
    }
