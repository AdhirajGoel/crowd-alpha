"""Offline, permanent evidence records. No credentials or network access."""
import csv
import hashlib
import html
import json
from pathlib import Path
import shutil
import tempfile
from datetime import datetime, timezone


def read(path):
    return json.loads(path.read_text())


def write(path, data):
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify(folder):
    """Detect changed, missing, and unexpected files; hashes are not a signature."""
    manifest = read(folder / "integrity.json")
    expected = manifest["sha256"]
    actual = {p.relative_to(folder).as_posix(): digest(p)
              for p in folder.rglob("*") if p.is_file() and p != folder / "integrity.json"}
    if actual != expected:
        bad = sorted(k for k in actual.keys() | expected.keys() if actual.get(k) != expected.get(k))
        raise RuntimeError("Provenance integrity failed: " + ", ".join(bad[:10]))
    return len(actual)


def macro_sources(macro):
    """Resolve missing current sources to the supplied previous report, never shadow fresh data."""
    macro = macro or {}
    sources = {s["id"]: s for s in macro.get("sources", [])}
    for source in macro.get("previous_record", {}).get("sources", []):
        sources.setdefault(source["id"], dict(source, previous_report_snapshot=True))
    return list(sources.values())


def validate_claims(result, bundle, market, macro=None):
    web_ids = {s["id"] for s in macro_sources(macro)}
    known = {p["id"] for p in bundle["posts"] + bundle["context"]}
    rows = {(s, b["t"]) for s, bars in market["bars"].items() for b in bars}
    claims = result["claims"]
    if {c["paragraph"] for c in claims} != set(range(1, len(result["paragraphs"]) + 1)):
        raise RuntimeError("Provenance must cover every final paragraph")
    for c in claims:
        if set(c.get("web_source_ids", [])) - web_ids:
            raise RuntimeError("Provenance references an unknown web source")
        if set(c["post_ids"]) - known:
            raise RuntimeError("Provenance references an unknown post")
        if any((r["symbol"], r["timestamp"]) not in rows for r in c["market_rows"]):
            raise RuntimeError("Provenance references an unknown market row")
        if not c["explanation"].strip() or not c["claim"].strip():
            raise RuntimeError("Provenance explanation is empty")
        if c["kind"] != "inference" and not (c["post_ids"] or c["market_rows"] or c.get("web_source_ids")):
            raise RuntimeError("A factual provenance claim has no source")


def page(path, title, body):
    # Escape external content before calling; never embed source HTML or remote media.
    path.write_text('<!doctype html><html lang="en"><meta charset="utf-8">'
        '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; '
        'img-src \'self\' data:; style-src \'unsafe-inline\'">'
        f'<title>{html.escape(title)}</title><style>body{{font:16px Arial,sans-serif;'
        'max-width:1100px;margin:32px auto;padding:0 20px;line-height:1.45}}'
        'table{border-collapse:collapse;width:100%}td,th{border:1px solid #ccc;padding:6px;text-align:left}'
        'pre{white-space:pre-wrap;overflow-wrap:anywhere;font:inherit}'
        'img{max-width:650px;max-height:600px}article{border-top:1px solid #ccc;padding:16px 0}'
        ':target{background:#fff3b0}a{overflow-wrap:anywhere}</style>'
        f'<h1>{html.escape(title)}</h1>{body}</html>')


def row_id(symbol, timestamp):
    return hashlib.sha256((symbol + timestamp).encode()).hexdigest()[:20]


def brief_text(text):
    safe = html.escape(text)
    for tag in ("b", "i", "u"):
        safe = safe.replace(f"&lt;{tag}&gt;", f"<{tag}>").replace(f"&lt;/{tag}&gt;", f"</{tag}>")
    return safe


def archive(run, pdf, work):
    """Publish an immutable dated snapshot only after its hash check succeeds."""
    result, bundle, market = (read(run / name) for name in ("final.json", "posts.json", "market.json"))
    macro = read(run / "macro.json") if (run / "macro.json").exists() else None
    validate_claims(result, bundle, market, macro)
    destination = work / "provenance" / run.name
    if destination.exists():
        verify(destination)
        if digest(destination / "source/final.json") != digest(run / "final.json") or digest(destination / pdf.name) != digest(pdf):
            raise RuntimeError("Existing provenance differs; refusing to overwrite its evidence")
        return destination
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".building-", dir=destination.parent) as tmp:
        folder = Path(tmp) / run.name
        folder.mkdir()
        shutil.copytree(run, folder / "source", ignore=shutil.ignore_patterns("*.tmp", "__pycache__"))
        shutil.copy2(pdf, folder / pdf.name)
        source = folder / "source"
        manifest = read(source / "manifest.json")
        esc = html.escape
        images_path = source / "images.json"
        images = read(images_path) if images_path.exists() else {"photos": []}
        pictures = {}
        for photo in images["photos"]:
            relative = "source/images/" + Path(photo["path"]).name
            for pid in photo["post_ids"]:
                pictures.setdefault(pid, []).append((relative, photo.get("alt_text", "")))
        selection = read(source / "triage.json") if (source / "triage.json").exists() else {"posts": []}
        selected = {p["id"]: p for p in selection["posts"]}
        available_ids = {p["id"] for p in bundle["posts"] + bundle["context"]}
        posts_html = '<p><a href="index.html">Brief evidence index</a>. Use Find to search text or account names.</p>'
        for group in ("posts", "context"):
            posts_html += f'<h2>{esc(group.title())}</h2>'
            for p in sorted(bundle[group], key=lambda p: (p["author"], p.get("created_at") or "", p["id"])):
                pid = p["id"]
                posts_html += f'<article id="p{esc(pid)}"><b>@{esc(p["author"])} | {esc(str(p.get("created_at")))}</b>'
                posts_html += f'<p>Post {esc(pid)}</p><pre>{esc(p["text"])}</pre>'
                for ref in p["references"]:
                    target = (f'<a href="#p{esc(ref["id"])}">{esc(ref["id"])}</a>' if ref["id"] in available_ids
                              else esc(ref["id"]) + ' (not retrieved; context retrieval is one level deep)')
                    posts_html += f'<p>{esc(ref["type"])}: {target}</p>'
                if pid in selected:
                    t = selected[pid]
                    posts_html += f'<p>Triage: {"relevant" if t["relevant"] else "not relevant"}. Image reason: {esc(t["image_reason"])}</p>'
                for path, alt in pictures.get(pid, []):
                    posts_html += f'<p><a href="{esc(path)}">Saved image</a></p><img loading="lazy" src="{esc(path)}" alt="{esc(alt)}">'
                posts_html += '</article>'
        page(folder / "posts.html", "Retrieved posts and selected images", posts_html)

        fields = ["symbol", "t", "o", "h", "l", "c", "v", "n", "vw"]
        market_html = '<p><a href="index.html">Brief evidence index</a> | <a href="market.csv">Download CSV</a></p>'
        market_html += f'<p>Feed: {esc(market["feed"])}. Adjustment: {esc(market["adjustment"])}. Cutoff: {esc(market["as_of"])}. Timestamps are provider values. {esc(market["limitations"])}</p>'
        market_html += '<p>o/h/l/c = open/high/low/close; v = volume; n = trade count; vw = volume-weighted price.</p>'
        market_html += '<table><tr>' + ''.join(f'<th>{s}</th>' for s in fields) + '</tr>'
        calculations = {}
        with (folder / "market.csv").open("w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fields)
            writer.writeheader()
            for symbol, bars in sorted(market["bars"].items()):
                for b in bars:
                    values = {k: b.get(k, "") for k in fields}
                    values["symbol"] = symbol
                    writer.writerow(values)
                    market_html += f'<tr id="{row_id(symbol, b["t"])}">' + ''.join(f'<td>{esc(str(values[k]))}</td>' for k in fields) + '</tr>'
                if bars:
                    calculations[symbol] = {str(n): {
                        "formula": "(end_close / start_close - 1) * 100",
                        "start_timestamp": bars[-n-1]["t"], "end_timestamp": bars[-1]["t"],
                        "start_close": bars[-n-1]["c"], "end_close": bars[-1]["c"],
                        "percent": round((bars[-1]["c"] / bars[-n-1]["c"] - 1) * 100, 3)
                    } for n in (1, 5, 20, 30) if len(bars) > n}
        page(folder / "market.html", "Alpaca daily market data", market_html + '</table>')
        write(folder / "calculations.json", calculations)

        if macro:
            macro_html = '<p><a href="index.html">Brief evidence index</a></p>'
            macro_html += '<p>' + esc(macro["limitations"]) + '</p>'
            for item in macro_sources(macro):
                macro_html += f'<article id="{esc(item["id"])}"><h2>{esc(item["title"])}</h2>'
                macro_html += f'<p>URL: {esc(item["url"])}<br>Retrieved: {esc(item["retrieved_at"])}</p>'
                if item.get("previous_report_snapshot"):
                    macro_html += '<p>Previous report snapshot; current retrieval unavailable. The dated source text below is preserved in <a href="source/macro.json">macro.previous_record</a>, not a fresh observation.</p>'
                else:
                    macro_html += f'<p><a href="source/{esc(item["raw_file"])}">Raw response</a> | <a href="source/{esc(item["text_file"])}">Readable snapshot</a></p>'
                macro_html += f'<pre>{esc(item["text"])}</pre></article>'
            macro_html += '<h2>Retrieval limitations</h2><pre>' + esc(json.dumps(macro["errors"], indent=2)) + '</pre>'
            page(folder / "macro.html", "Bonds, inflation and Fed evidence", macro_html)

        limitations = []
        if not list(source.glob("alpaca-page-*.json")):
            limitations.append("No Alpaca response envelopes are included. market.json contains the retained rows and identifies their feed.")
        if not list(source.glob("*.request.json")):
            limitations.append("No X request metadata is included. posts.json contains the retained posts; manifest.json records the collection window.")
        if not (source / "runtime").exists():
            limitations.append("No contemporaneous runtime snapshot is included. Inspect the source directory for the evidence and generation records retained by this run.")
        if (source / "provenance-audit.json").exists():
            limitations.append("This trial's claim mapping was generated retrospectively against the published text and saved evidence. It is not a record of the original model's internal reasoning.")
        write(folder / "record.json", {"version": 1, "archived_at": datetime.now(timezone.utc).isoformat(),
              "run": run.name, "pdf": pdf.name, "window": manifest, "limitations": limitations,
              "integrity_note": "SHA-256 detects changes relative to this local manifest; it is not a provider signature or independent proof of truth."})
        body = f'<p><a href="{esc(pdf.name)}">Exact archived PDF</a> | <a href="posts.html">All posts and images</a> | <a href="market.html">Alpaca rows</a> | <a href="market.csv">CSV</a> | <a href="calculations.json">Return calculations</a></p>'
        if macro:
            body += '<p><a href="macro.html">Official bonds, inflation and Fed sources</a></p>'
        body += f'<p>Collection: {esc(manifest["start"])} through {esc(manifest["end"])} (UTC). These records survive weekly cleanup. All links point to local files.</p>'
        body += '<p>Evidence mappings are agent-generated and can be checked below. A source report records what an account claimed; an inference is the agent’s interpretation. Neither is independently established just by being archived.</p>'
        for note in limitations:
            body += f'<p><b>Record limitation:</b> {esc(note)}</p>'
        for number, paragraph in enumerate(result["paragraphs"], 1):
            body += f'<h2>Paragraph {number}</h2><p>{brief_text(paragraph)}</p>'
            for c in result["claims"]:
                if c["paragraph"] != number:
                    continue
                body += f'<article><b>{esc(c["kind"])}: {esc(c["claim"])}</b><p>{esc(c["explanation"])}</p>'
                links = [f'<a href="posts.html#p{esc(pid)}">Post {esc(pid)}</a>' for pid in c["post_ids"]]
                links += [f'<a href="market.html#{row_id(r["symbol"], r["timestamp"])}">{esc(r["symbol"])} {esc(r["timestamp"])}</a>' for r in c["market_rows"]]
                links += [f'<a href="macro.html#{esc(i)}">Web source: {esc(i)}</a>' for i in c.get("web_source_ids", [])]
                body += '<p>' + ' | '.join(links) + '</p></article>'
        body += '<h2>Complete files</h2><p>Includes full API response JSON, normalized inputs, triage decisions, selected images, model prompts and outputs, and memory snapshots where available. Credentials and request authorization headers are never recorded.</p><ul>'
        for p in sorted(source.rglob("*")):
            if p.is_file():
                relative = p.relative_to(folder).as_posix()
                body += f'<li><a href="{esc(relative)}">{esc(relative)}</a></li>'
        body += '</ul><p><a href="integrity.json">SHA-256 file manifest</a> | <a href="record.json">Archive metadata and limitations</a></p>'
        page(folder / "index.html", "Crowd Alpha evidence - " + run.name, body)
        write(folder / "integrity.json", {"sha256": {p.relative_to(folder).as_posix(): digest(p)
              for p in sorted(folder.rglob("*")) if p.is_file()}})
        verify(folder)
        folder.rename(destination)
    return destination
