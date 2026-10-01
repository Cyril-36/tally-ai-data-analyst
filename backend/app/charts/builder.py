"""Plotly encodings validated against a stored result; no independent calculation."""

import html
import json
import math
import uuid

import plotly.graph_objects as go
import plotly.io as pio
from plotly.offline import get_plotlyjs

from app.contracts.result import ChartSpec, ChartType, ColumnRole, ResultTable


def validate_chart(table: ResultTable, spec: ChartSpec) -> ChartSpec:
    columns = {c.name: c for c in table.columns}
    required = [spec.x, *spec.y, *([spec.series] if spec.series else [])]
    if any(name not in columns for name in required):
        raise ValueError("Chart columns must exist in the result table.")
    if len(set(spec.y)) != len(spec.y) or spec.series in spec.y:
        raise ValueError("Chart encodings must not repeat metrics.")
    if any(columns[name].role != ColumnRole.metric for name in spec.y):
        raise ValueError("Chart y columns must be metrics.")
    rows = [dict(zip(columns, row, strict=True)) for row in table.rows]
    for name in spec.y:
        if any(r[name] is not None and not _numeric(r[name]) for r in rows):
            raise ValueError("Chart metrics must be finite numbers.")
    if spec.series and columns[spec.series].role == ColumnRole.metric:
        raise ValueError("Series must be a grouping column.")
    if spec.type in {ChartType.line, ChartType.area} and columns[spec.x].role != ColumnRole.time:
        raise ValueError("Line charts require a time column.")
    if spec.type in {ChartType.bar, ChartType.pie} and columns[spec.x].role == ColumnRole.metric:
        raise ValueError("Category charts require a grouping column.")
    if spec.type in {ChartType.line, ChartType.area, ChartType.bar, ChartType.pie}:
        keys = [
            (
                json.dumps(r[spec.x], default=str),
                json.dumps(r[spec.series], default=str) if spec.series else "",
            )
            for r in rows
        ]
        if len(set(keys)) != len(keys):
            raise ValueError(
                "Use a series column or narrower grouping to avoid duplicate categories."
            )
    if spec.type == ChartType.pie:
        if len(spec.y) != 1 or spec.series:
            raise ValueError("Pie charts require one metric and no series.")
        values = [r[spec.y[0]] for r in rows if r[spec.y[0]] is not None]
        if not values or any(v < 0 for v in values) or sum(values) <= 0:
            raise ValueError("Pie charts require nonnegative values with a positive total.")
    if spec.type in {ChartType.scatter, ChartType.histogram} and (
        not any(_numeric(r[spec.x]) for r in rows)
        or any(r[spec.x] is not None and not _numeric(r[spec.x]) for r in rows)
    ):
        raise ValueError("This chart requires numeric x values.")
    return spec


def default_chart(table: ResultTable) -> ChartSpec | None:
    metrics = [c.name for c in table.columns if c.role == ColumnRole.metric]
    dimensions = [c.name for c in table.columns if c.role == ColumnRole.dimension]
    times = [c.name for c in table.columns if c.role == ColumnRole.time]
    if not metrics or not table.rows:
        return None
    if times:
        spec = ChartSpec(
            type=ChartType.line,
            x=times[0],
            y=metrics[:4],
            series=dimensions[0] if len(dimensions) == 1 else None,
            title="Results over time",
        )
    elif dimensions:
        spec = ChartSpec(
            type=ChartType.bar,
            x=dimensions[0],
            y=metrics[:4],
            series=dimensions[1] if len(dimensions) == 2 else None,
            title="Results by group",
        )
    elif len(metrics) >= 2 and len(table.rows) > 1:
        spec = ChartSpec(
            type=ChartType.scatter, x=metrics[0], y=[metrics[1]], title="Metric comparison"
        )
    else:
        return None
    try:
        return validate_chart(table, spec)
    except ValueError:
        return None


def _figure(table, spec):
    validate_chart(table, spec)
    names = [c.name for c in table.columns]
    rows = [dict(zip(names, row, strict=True)) for row in table.rows]
    fig = go.Figure()
    if spec.type == ChartType.pie:
        fig.add_trace(
            go.Pie(
                labels=[_label(r[spec.x]) for r in rows],
                values=[r[spec.y[0]] for r in rows],
                sort=False,
            )
        )
    elif spec.type == ChartType.histogram:
        fig.add_trace(go.Histogram(x=[r[spec.x] for r in rows], name=html.escape(spec.x)))
    else:
        groups = list(dict.fromkeys(r[spec.series] for r in rows)) if spec.series else [None]
        for metric in spec.y:
            for group in groups:
                points = [r for r in rows if not spec.series or r[spec.series] == group]
                if spec.type in {ChartType.line, ChartType.area}:
                    points.sort(key=lambda r: str(r[spec.x]))
                name = html.escape(metric + (f" ({group})" if spec.series else ""))
                args = {
                    "x": [_label(r[spec.x]) for r in points],
                    "y": [r[metric] for r in points],
                    "name": name,
                }
                if spec.type == ChartType.bar:
                    fig.add_trace(go.Bar(**args))
                else:
                    fig.add_trace(
                        go.Scatter(
                            **args,
                            mode="markers" if spec.type == ChartType.scatter else "lines+markers",
                            fill="tozeroy" if spec.type == ChartType.area else None,
                        )
                    )
    fig.update_layout(
        title=html.escape(spec.title),
        xaxis_title=html.escape(spec.x),
        yaxis_title=html.escape(", ".join(spec.y)),
    )
    return fig


def figure_json(table: ResultTable, spec: ChartSpec) -> dict:
    return json.loads(pio.to_json(_figure(table, spec)))


def figure_html(table: ResultTable, spec: ChartSpec, include_js: bool = True) -> str:
    element = "chart-" + uuid.uuid4().hex
    payload = json.dumps(figure_json(table, spec)).replace("<", "\\u003c").replace(">", "\\u003e")
    library = f"<script>{get_plotlyjs()}</script>" if include_js else ""
    return (
        library
        + f'<div id="{element}"></div><script>const p_{element.replace("-", "_")}={payload};'
        + f'Plotly.newPlot("{element}",p_{element.replace("-", "_")}.data,'
        + f"p_{element.replace('-', '_')}.layout,{{responsive:true}});</script>"
    )


def _numeric(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _label(value):
    return html.escape(value) if isinstance(value, str) else value
