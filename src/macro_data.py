"""Free official macro sources, captured locally before the agent reads them."""
import csv
import hashlib
import html
import io
import json
import re
import shutil
import urllib.request
import ssl
import certifi
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from decimal import Decimal
from email.utils import parsedate_to_datetime
from zoneinfo import ZoneInfo

HOSTS = {"home.treasury.gov", "api.bls.gov", "www.bea.gov", "www.federalreserve.gov"}


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n")
    tmp.replace(path)


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def plain_text(raw):
    raw = re.sub(r"<!--.*?-->", " ", raw, flags=re.S)
    raw = re.sub(r"<(script|style|nav)\b[^>]*>.*?</\1>", " ", raw, flags=re.S | re.I)
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", raw))).strip()


def treasury_rows(raw, cutoff):
    rows = []
    for row in csv.DictReader(io.StringIO(raw.lstrip("\ufeff"))):
        day = datetime.strptime(row["Date"], "%m/%d/%Y").date()
        if day <= cutoff and all(row.get(f"{n} Yr", "").strip() not in ("", "N/A") for n in (2, 5, 10)):
            rows.append({"date": str(day), **{f"{n}y": float(row[f"{n} Yr"]) for n in (2, 5, 10)}})
    return sorted(rows, key=lambda r: r["date"])


def cpi_summary(data, cutoff):
    if data.get("status") != "REQUEST_SUCCEEDED":
        raise ValueError("BLS did not return a successful result")
    output = {}
    for series in data["Results"]["series"]:
        observations = {f'{x["year"]}-{x["period"][1:]}': x for x in series["data"]
                        if re.fullmatch(r"M(0[1-9]|1[0-2])", x["period"])
                        and f'{x["year"]}-{x["period"][1:]}' <= str(cutoff)[:7]}
        latest = max(observations)
        previous_year = str(int(latest[:4]) - 1) + latest[4:]
        base, current = observations[previous_year], observations[latest]
        output[series["seriesID"]] = {"period": latest, "index": current,
            "year_ago_index": base, "year_over_year_pct": float(((Decimal(current["value"]) /
                Decimal(base["value"]) - 1) * 100).quantize(Decimal("0.1"))),
            "formula": "(current index / index for same month one year earlier - 1) * 100",
            "adjustment": "not seasonally adjusted"}
    if set(output) != {"CUUR0000SA0", "CUUR0000SA0L1E"}:
        raise ValueError("Missing headline or core CPI series")
    return output


def collect(run, cutoff, previous=None, historical_source=None, historical=False):
    target = run / "macro.json"
    if target.exists():
        return json.loads(target.read_text())
    folder = run / "macro"
    folder.mkdir(parents=True, exist_ok=True)
    day = cutoff.astimezone(ZoneInfo("America/New_York")).date()
    sources, errors = [], []

    def saved_source(identifier):
        if historical_source is None:
            raise ValueError(f"No pre-cutoff {identifier} snapshot available")
        saved = json.loads((historical_source / "macro.json").read_text())
        item = next((s for s in saved["sources"] if s["id"] == identifier
                     and datetime.fromisoformat(s["retrieved_at"].replace("Z", "+00:00")) <= cutoff), None)
        if item is None:
            raise ValueError(f"No pre-cutoff {identifier} snapshot available")
        for key in ("raw_file", "text_file"):
            shutil.copy2(historical_source / item[key], run / item[key])
        request_path = historical_source / "macro" / (identifier + ".request.json")
        if request_path.exists():
            shutil.copy2(request_path, folder / request_path.name)
        sources.append(dict(item, historical_snapshot=True))

    def fetch(name, url, body=None):
        if urllib.parse.urlparse(url).scheme != "https" or urllib.parse.urlparse(url).hostname not in HOSTS:
            raise ValueError("Unexpected macro source host")
        headers = {"User-Agent": "crowd-alpha/1"}
        payload = None
        if body is not None:
            headers["Content-Type"] = "application/json"
            payload = json.dumps(body).encode()
        metadata = {"url": url, "parameters": body, "started_at": datetime.now(timezone.utc).isoformat()}
        try:
            with urllib.request.urlopen(urllib.request.Request(url, data=payload, headers=headers), timeout=30, context=ssl.create_default_context(cafile=certifi.where())) as response:
                if urllib.parse.urlparse(response.geturl()).hostname not in HOSTS:
                    raise ValueError("Unexpected macro redirect")
                raw = response.read(8_000_001)
                if len(raw) > 8_000_000:
                    raise ValueError("Macro source exceeds 8 MB")
                # Raw HTML is saved as text, so inspecting it cannot execute source scripts.
                (folder / (name + ".raw.txt")).write_bytes(raw)
                metadata.update(status=response.status, response_url=response.geturl(),
                    content_type=response.headers.get("Content-Type"),
                    sha256=hashlib.sha256(raw).hexdigest())
        except Exception as e:
            metadata["error"] = str(e)
            raise
        finally:
            metadata["completed_at"] = datetime.now(timezone.utc).isoformat()
            save(folder / (name + ".request.json"), metadata)
        return raw.decode("utf-8-sig"), metadata

    def add(identifier, title, text, metadata, summary=None):
        (folder / (identifier + ".txt")).write_text(text)
        sources.append({"id": identifier, "title": title, "url": metadata["url"],
            "retrieved_at": metadata["completed_at"], "text": text, "summary": summary,
            "raw_file": f"macro/{identifier}.raw.txt", "text_file": f"macro/{identifier}.txt",
            "fingerprint": fingerprint({k: v for k, v in summary.items() if k != "age_calendar_days"} if summary is not None else text)})

    try:
        # Include the preceding year around New Year so changes have a prior session.
        years = [day.year - 1, day.year] if day.month == 1 and day.day <= 10 else [day.year]
        rows = []
        for year in years:
            url = f"https://home.treasury.gov/resource-center/data-chart-center/interest-rates/daily-treasury-rates.csv/{year}/all?type=daily_treasury_yield_curve&field_tdr_date_value={year}&page&_format=csv"
            name = "treasury" if year == day.year else "treasury-previous-year"
            raw, meta = fetch(name, url)
            rows.extend(treasury_rows(raw, day))
        rows = sorted({r["date"]: r for r in rows}.values(), key=lambda r: r["date"])
        if not rows:
            raise ValueError("No Treasury yield observations available by cutoff")
        latest = rows[-1]
        summary = {"latest": latest, "recent_rows": rows[-6:], "unit": "percent per year, Treasury daily par yields",
                   "changes_bp": {str(n): {k: round((latest[k] - rows[-n-1][k]) * 100, 2)
                     for k in ("2y", "5y", "10y")} for n in (1, 5) if len(rows) > n},
                   "change_formula": "(latest yield percent - earlier yield percent) * 100 = basis points",
                   "age_calendar_days": (day - datetime.fromisoformat(latest["date"]).date()).days}
        add("treasury", "Treasury 2-, 5-, and 10-year par yields", json.dumps(summary, indent=2), meta, summary)
    except Exception as e:
        errors.append({"source": "treasury", "error": str(e)})

    try:
        if historical:
            # Observation month alone cannot prove that a release was public by cutoff.
            saved_source("cpi")
        else:
            raw, meta = fetch("cpi", "https://api.bls.gov/publicAPI/v2/timeseries/data/", {
                "seriesid": ["CUUR0000SA0", "CUUR0000SA0L1E"], "startyear": str(day.year - 2), "endyear": str(day.year)})
            summary = cpi_summary(json.loads(raw), day)
            add("cpi", "BLS CPI-U: headline and excluding food/energy", json.dumps(summary, indent=2), meta, summary)
    except Exception as e:
        errors.append({"source": "cpi", "error": str(e)})

    try:
        raw, _ = fetch("fed-feed", "https://www.federalreserve.gov/feeds/press_monetary.xml")
        items = [x for x in ET.fromstring(raw).findall("./channel/item")
                 if "issues FOMC statement" in x.findtext("title", "")
                 and parsedate_to_datetime(x.findtext("pubDate")) <= cutoff]
        item = max(items, key=lambda x: parsedate_to_datetime(x.findtext("pubDate")))
        raw, meta = fetch("fed", item.findtext("link"))
        text = plain_text(raw)
        start = text.index("For release at")
        text = text[start:]
        text = text.split("Last Update:")[0]
        if "target range" not in text:
            raise ValueError("Fed statement did not contain the policy rate discussion")
        add("fed", "Latest FOMC statement, published " + item.findtext("pubDate"), text, meta)
    except Exception as e:
        errors.append({"source": "fed", "error": str(e)})

    try:
        raw, _ = fetch("pce-index", "https://www.bea.gov/data/personal-consumption-expenditures-price-index")
        links = re.findall(r'href="([^"<>]*?/news/\d{4}/personal-income-and-outlays-[^"<>]+)"', raw)
        if not links:
            raise ValueError("BEA current release link missing")
        url = urllib.parse.urljoin("https://www.bea.gov", html.unescape(links[0]))
        raw, meta = fetch("pce", url)
        text = plain_text(raw)
        text = text[text.index("EMBARGOED UNTIL"):]
        text = text.split("Related Data Tables")[0]
        if "PCE price index" not in text:
            raise ValueError("BEA release missing inflation discussion")
        release = re.search(r"\b([A-Z][a-z]+ \d{1,2}, \d{4})\b", text[:200])
        if historical and (not release or datetime.strptime(release[1], "%B %d, %Y").replace(
                hour=8, minute=30, tzinfo=ZoneInfo("America/New_York")) > cutoff):
            saved_source("pce")
        else:
            add("pce", "BEA Personal Income and Outlays release", text, meta)
    except Exception as e:
        errors.append({"source": "pce", "error": str(e)})

    previous = previous or {}
    fingerprints = {s["id"]: s["fingerprint"] for s in sources}
    changes = {k: ("first observation" if k not in previous.get("fingerprints", {}) else
                    "unchanged" if v == previous["fingerprints"][k] else "changed") for k, v in fingerprints.items()}
    result = {"as_of": cutoff.isoformat(), "sources": sources, "errors": errors,
              "fingerprints": fingerprints, "changes_since_previous_brief": changes,
              "previous_record": {k: v for k, v in previous.items() if k != "previous_record"},
              "limitations": "Daily par yields are benchmarks, not ETF prices or live executable yields. Inflation periods and release dates differ. No futures-implied Fed probabilities collected. Latest web pages are snapshots at retrieval, not point-in-time historical reconstructions."}
    if historical:
        result["limitations"] += " Retrospective report: Treasury rows and Fed/PCE publication dates are cutoff-filtered; CPI uses a saved pre-cutoff snapshot and may omit intervening releases. Later revisions cannot be excluded from newly retrieved historical data. Do not describe a saved older release as the latest unless independently established."
    save(target, result)
    print(f"Macro: {len(sources)} official sources captured; {len(errors)} unavailable", flush=True)
    return result
