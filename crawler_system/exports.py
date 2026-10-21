import csv
from io import StringIO
import json

from .papers import bibtex


def export_items(items, format):
    if format == "json":
        return json.dumps(items, ensure_ascii=False, indent=2), "application/json", "json"
    if format == "bibtex":
        return "\n\n".join(item.get("bibtex") or bibtex(item) for item in items if item.get("kind") == "paper"), "text/plain", "bib"
    if format == "csv":
        file = StringIO()
        writer = csv.writer(file)
        writer.writerow(["id", "kind", "title", "url", "doi", "published", "text_status", "content", "abstract", "subtitle_text"])
        for item in items:
            fields = [str(item.get(key) or "") for key in ("id", "kind", "title", "url", "doi", "published", "text_status", "content", "abstract", "subtitle_text")]
            writer.writerow(["'" + value if value.lstrip().startswith(("=", "+", "-", "@")) else value for value in fields])
        return "\ufeff" + file.getvalue(), "text/csv", "csv"
    if format == "markdown":
        output = []
        for item in items:
            output.append(f"# {item['title']}\n\nSource: {item['url']}\n\nType: {item.get('kind', '')} · Collected at: {item.get('fetched_at', '')}\n\n" +
                          ("## Abstract\n\n" + item["abstract"] + "\n\n" if item.get("abstract") else "") +
                          ("## Content\n\n" + item["content"] + "\n\n" if item.get("content") else "") +
                          ("## Subtitles\n\n" + item["subtitle_text"] + "\n\n" if item.get("subtitle_text") else ""))
        return "\n\n---\n\n".join(output), "text/markdown", "md"
    raise ValueError("Unsupported export format")
