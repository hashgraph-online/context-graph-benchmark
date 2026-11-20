"""Escaped, self-contained HTML rendered from the same result as JSON."""

from html import escape
import json


def html(result):
    rows = []
    for adapter, budgets in result["adapters"].items():
        for budget, summary in budgets.items():
            values = [adapter, budget, summary["evidence_score"], summary["graph_score"],
                      summary["context_tokens"], summary["statuses"], summary["violations"], summary["compliant"]]
            rows.append("<tr>" + "".join("<td>" + escape(str(value)) + "</td>" for value in values) + "</tr>")
    return ("<!doctype html><html lang='en'><meta charset='utf-8'><title>Context benchmark scorecard</title>"
            "<style>body{font:16px system-ui;max-width:1200px;margin:2rem auto;padding:1rem}td,th{padding:.5rem;border:1px solid #ddd}table{border-collapse:collapse}pre{white-space:pre-wrap;overflow-wrap:anywhere}</style>"
            "<h1>Context benchmark scorecard</h1><p>Suite: " + escape(result["suite"]["id"]) +
            "; evidence class: <strong>" + escape(result["evidence_class"]) +
            "</strong>. Comparative qualification: false. Intervals, if present, are descriptive.</p>"
            "<table><thead><tr>" + "".join("<th>" + s + "</th>" for s in
            ["Engine + adapter", "Budget", "Evidence score", "Graph score", "Context tokens", "Statuses", "Violations", "Compliant"]) +
            "</tr></thead><tbody>" + "".join(rows) + "</tbody></table><h2>Complete result</h2><pre>" +
            escape(json.dumps(result, ensure_ascii=False, indent=2)) + "</pre></html>").encode()
