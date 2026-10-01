"""Escaped standalone reports over the exact stored result, including source freshness."""

import csv
import html
import io

from app.charts.builder import figure_html
from app.contracts.result import AnalysisResult


def html_report(results: list[AnalysisResult], statuses: dict[str, str] | None = None) -> str:
    statuses = statuses or {}
    parts = [
        '<!doctype html><html><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width,initial-scale=1">',
        "<title>Analysis report</title></head><body><main><h1>Analysis report</h1>",
    ]
    included_js = False
    if not results:
        parts.append("<p>No saved results.</p>")
    for result in results:
        parts += [
            "<section>",
            f"<h2>{_escape(result.question)}</h2>",
            f"<p>Outcome: {_escape(result.outcome.value)}. "
            f"Source status: {_escape(statuses.get(result.id, 'current'))}.</p>",
            f"<p>Calculated at {_escape(result.created_at.isoformat())}.</p>",
        ]
        if result.message:
            parts.append(f"<p>{_escape(result.message)}</p>")
        if result.clarification:
            parts.append(f"<p>{_escape(result.clarification.question)}</p>")
        parts += [f"<p>{_escape(sentence)}</p>" for sentence in result.answer_sentences]
        if result.table:
            parts += [
                "<table><thead><tr>",
                "".join(f"<th>{_escape(c.name)}</th>" for c in result.table.columns),
                "</tr></thead><tbody>",
            ]
            parts += [
                "<tr>" + "".join(f"<td>{_escape(v)}</td>" for v in row) + "</tr>"
                for row in result.table.rows
            ]
            parts.append("</tbody></table>")
            if result.chart:
                try:
                    parts.append(
                        figure_html(result.table, result.chart, include_js=not included_js)
                    )
                    included_js = True
                except ValueError:
                    parts.append("<p>The chart is unavailable; the calculated table is above.</p>")
        if result.meta:
            parts += [
                "<h3>Sources and versions</h3>",
                f"<pre>{_escape(result.meta.dataset_versions)}</pre>",
                f"<h3>SQL</h3><pre>{_escape(result.meta.sql)}</pre>",
                f"<p>Parameters: {_escape(result.meta.params)}</p>",
                "<h3>Caveats</h3><ul>",
            ]
            parts += [f"<li>{_escape(c.message)}</li>" for c in result.meta.caveats]
            parts.append("</ul>")
        if result.anomalies:
            parts += [
                "<h3>Anomalies</h3>",
                f"<p>{_escape(result.anomalies.status.value)}: "
                f"{_escape(result.anomalies.reason or '')}</p>",
            ]
            for flag in result.anomalies.flags:
                parts.append(
                    f"<p>{_escape(flag.row_key)}: {_escape(flag.metric)} = {flag.value}; "
                    f"median {flag.median}, MAD {flag.mad}, robust z {flag.robust_z}, "
                    f"threshold {flag.threshold}. Reference: {_escape(flag.reference)}.</p>"
                )
        parts.append("</section>")
    return "".join([*parts, "</main></body></html>"])


def csv_report(result: AnalysisResult) -> str:
    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\r\n")
    if result.table:
        writer.writerow([_safe(c.name) for c in result.table.columns])
        writer.writerows([_safe(v) for v in row] for row in result.table.rows)
    return output.getvalue()


def _escape(value):
    return html.escape("" if value is None else str(value))


def _safe(value):
    if value is None:
        return ""
    if isinstance(value, str) and (
        value.lstrip().startswith(("=", "+", "-", "@")) or value.startswith(("\t", "\r", "\n"))
    ):
        return "'" + value
    return value
