import json
from itertools import zip_longest
import re
from urllib.parse import urlencode, quote, urlsplit, unquote

from .errors import CrawlerError
from .network import Network
from .parsers import clean_text, xml_root, parse_pdf
from .store import normalize_doi


def bibtex(item):
    def escape(value):
        return str(value or "").replace("\\", "\\textbackslash{}").replace("{", "\\{").replace("}", "\\}").replace("%", "\\%").replace("&", "\\&").replace("_", "\\_").replace("\n", " ")
    key = re.sub(r"[^a-zA-Z0-9]", "", item.get("doi") or item.get("title", "paper"))[:50] or "paper"
    fields = {"title": item.get("title"), "author": " and ".join(item.get("authors", [])), "year": item.get("published", "")[:4], "doi": item.get("doi"), "url": item["url"], "journal": item.get("metadata", {}).get("journal")}
    return "@article{" + key + ",\n" + ",\n".join(f"  {k} = {{{escape(v)}}}" for k, v in fields.items() if v) + "\n}"


def arxiv_items(data):
    root = xml_root(data)
    atom, arxiv = "{http://www.w3.org/2005/Atom}", "{http://arxiv.org/schemas/atom}"
    items = []
    for entry in root.findall(atom + "entry"):
        identifier = (entry.findtext(atom + "id") or "").replace("http://", "https://")
        if not identifier or "api/errors" in identifier:
            continue
        pdf = next((link.get("href") for link in entry.findall(atom + "link") if link.get("title") == "pdf" or link.get("type") == "application/pdf"), "")
        pdf = (pdf or identifier.replace("/abs/", "/pdf/")).replace("http://", "https://")
        item = {"kind": "paper", "url": identifier, "title": " ".join((entry.findtext(atom + "title") or "").split()),
                "authors": [author.findtext(atom + "name") or "" for author in entry.findall(atom + "author")],
                "abstract": " ".join((entry.findtext(atom + "summary") or "").split()), "content": "",
                "doi": entry.findtext(arxiv + "doi") or "", "published": entry.findtext(atom + "published") or "",
                "metadata": {"arxiv_id": identifier.rsplit("/", 1)[-1], "categories": [tag.get("term") for tag in entry.findall(atom + "category")], "journal": entry.findtext(arxiv + "journal_ref") or ""},
                "assets": [{"url": pdf, "name": "Paper PDF", "kind": "pdf"}], "extractor": "arxiv-api", "text_status": "abstract", "warnings": []}
        item["bibtex"] = bibtex(item)
        items.append(item)
    return items


def crossref_item(record):
    date = next((record.get(key, {}).get("date-parts", [[]])[0] for key in ("published", "published-print", "published-online", "created") if record.get(key, {}).get("date-parts")), [])
    doi = normalize_doi(record.get("DOI"))
    item = {"kind": "paper", "url": "https://doi.org/" + doi, "doi": doi, "title": (record.get("title") or [doi])[0],
            "authors": [" ".join(filter(None, [a.get("given"), a.get("family")])) or a.get("name", "") for a in record.get("author", [])],
            "content": "", "abstract": clean_text(record.get("abstract", "")), "published": "-".join(str(part).zfill(2) if index else str(part) for index, part in enumerate(date)),
            "metadata": {"journal": (record.get("container-title") or [""])[0], "publisher": record.get("publisher", ""), "type": record.get("type", ""), "licenses": record.get("license", [])},
            "assets": [{"url": link["URL"], "kind": "pdf", "name": "Publisher PDF"} for link in record.get("link", []) if link.get("URL") and "pdf" in link.get("content-type", "")],
            "extractor": "crossref-api", "text_status": "abstract" if record.get("abstract") else "metadata_only", "warnings": []}
    item["bibtex"] = bibtex(item)
    return item


def search_papers(query, limit=8, network=None):
    network = network or Network()
    groups, errors = [], []
    arxiv_query = query if re.match(r"^(all|ti|au|abs|cat|id):", query) else "all:" + query
    sources = [
        ("arxiv", "https://export.arxiv.org/api/query?" + urlencode({"search_query": arxiv_query, "max_results": limit}), arxiv_items),
        ("crossref", "https://api.crossref.org/works?" + urlencode({"query": query, "rows": min(100, limit * 3)}), lambda data: [crossref_item(row) for row in json.loads(data)["message"]["items"] if row.get("type") != "component"]),
    ]
    for source, url, parser in sources:
        try:
            groups.append(parser(network.fetch(url).body))
        except Exception as error:
            errors.append({"channel": source, **(error.as_dict() if isinstance(error, CrawlerError) else {"code": "PAPER_API_ERROR", "message": str(error), "action": "Retry paper search later"})})
    seen, result = set(), []
    items = (item for row in zip_longest(*groups) for item in row if item is not None)
    for item in items:
        key = normalize_doi(item.get("doi")) or item["url"]
        if key not in seen:
            result.append(item)
            seen.add(key)
    return {"items": result[:limit], "errors": errors}


def collect_paper(value, network=None, full_text=True):
    network = network or Network()
    if "arxiv.org/" in value:
        identifier = unquote(urlsplit(value).path).replace("/abs/", "").replace("/pdf/", "").removesuffix(".pdf").strip("/")
        if not re.fullmatch(r"(?:\d{4}\.\d{4,5}|[a-zA-Z-]+(?:\.[A-Z]{2})?/\d{7})(?:v\d+)?", identifier):
            raise CrawlerError("ARXIV_ID", "Cannot identify arXiv paper ID", "Use paper abs or pdf URL")
        papers = arxiv_items(network.fetch("https://export.arxiv.org/api/query?" + urlencode({"id_list": identifier})).body)
        if not papers:
            raise CrawlerError("PAPER_NOT_FOUND", "arXiv paper not found")
        item = papers[0]
    else:
        doi = normalize_doi(unquote(value))
        if not re.fullmatch(r"10\.\d{4,9}/\S+", doi):
            raise CrawlerError("INVALID_DOI", "Invalid DOI format")
        record = json.loads(network.fetch("https://api.crossref.org/works/" + quote(doi, safe="")).body)["message"]
        item = crossref_item(record)
    if full_text and item["assets"]:
        try:
            pdf = network.fetch(item["assets"][0]["url"])
            if not pdf.body.startswith(b"%PDF-"):
                raise CrawlerError("PDF_UNAVAILABLE", "PDF link returned a webpage or verification page")
            parsed = parse_pdf(pdf.body, pdf.url)
            item.update(content=parsed["content"], text_status=parsed["text_status"], truncated=parsed["truncated"])
            item["metadata"]["pages"] = parsed["metadata"]["pages"]
            item["warnings"].extend(parsed["warnings"])
        except Exception as error:
            item["warnings"].append(f"Paper metadata saved; full text not yet retrieved: {error}")
    return item

