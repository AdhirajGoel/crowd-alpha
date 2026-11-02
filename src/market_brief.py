#!/usr/bin/env python3
"""Crowd Alpha: evidence-backed market briefs with persistent context."""
import argparse
import fcntl
import getpass
import hashlib
import html
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import ssl
import certifi
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
import provenance
import engine
import settings
import macro_data
import live_memory

CODE = Path(__file__).resolve().parent
WORK = Path(os.environ.get("CROWD_ALPHA_HOME", CODE.parent / ".local")).expanduser().resolve()
ROOT = WORK / "reports"
PT = ZoneInfo("America/Los_Angeles")
ET = ZoneInfo("America/New_York")
UTC = timezone.utc
ACCOUNTS = {}
REPORT_HOUR = 17
SERVICES = {"x": "x-bearer-token", "key": "alpaca-key-id", "secret": "alpaca-secret"}
FIELDS = "author_id,created_at,text,note_tweet,conversation_id,referenced_tweets,attachments,entities"
SYMBOL = re.compile(r"[A-Z][A-Z0-9.\-]{0,9}\Z")


def now():
    return datetime.now(UTC)


def stamp(value):
    return value.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def parse(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def read(path, default=None):
    return json.loads(path.read_text()) if path.exists() else default


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n")
    tmp.replace(path)


def secret(which):
    variable = {"x": "X_BEARER_TOKEN", "key": "APCA_API_KEY_ID", "secret": "APCA_API_SECRET_KEY"}[which]
    if os.environ.get(variable, "").strip():
        return os.environ[variable].strip()
    result = subprocess.run([
        "/usr/bin/security", "find-generic-password", "-a", getpass.getuser(),
        "-s", "crowd-alpha." + SERVICES[which], "-w",
    ], capture_output=True, text=True, timeout=30)
    if result.returncode or not result.stdout.strip():
        raise RuntimeError(f"Set {variable} or enroll its Crowd Alpha Keychain item using --credentials")
    return result.stdout.strip()


def initialize():
    global ACCOUNTS, PT, REPORT_HOUR
    config = settings.load(WORK / "config.json")
    ACCOUNTS = config["accounts"]
    PT = ZoneInfo(config["timezone"])
    REPORT_HOUR = config.get("hour", 17)
    ROOT.mkdir(parents=True, exist_ok=True)
    return config


def request(url, headers, params=None, record=None):
    started = stamp(now())
    endpoint = url
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={**headers, "User-Agent": "crowd-alpha/1"})
    with urllib.request.urlopen(req, timeout=60, context=ssl.create_default_context(cafile=certifi.where())) as response:
        data = json.load(response)
        if record:
            save(record, data)
            save(record.with_suffix(".request.json"), {
                "endpoint": endpoint, "parameters": params or {}, "started_at": started,
                "completed_at": stamp(now()), "status": response.status,
                "provider_date": response.headers.get("Date"),
                "response_file": record.name,
                "response_sha256": provenance.digest(record),
            })
        return data


def budget_reserve(ledger, month, amount, limit):
    used = ledger.get(month, 0.0)
    if used + amount > limit + 1e-8:
        raise RuntimeError(f"X monthly budget would exceed ${limit:.2f}; collection stopped")
    ledger[month] = round(used + amount, 6)


def x_resources(data, path):
    """Validate provider envelopes before treating resource counts as billable facts."""
    if not isinstance(data, dict):
        raise RuntimeError("Malformed X response; reservation retained")
    posts, includes, errors = data.get("data", []), data.get("includes", {}), data.get("errors", [])
    if (not isinstance(posts, list) or not isinstance(includes, dict) or not isinstance(errors, list)
            or any(not isinstance(p, dict) or not isinstance(p.get("id"), str) or not p["id"] for p in posts)
            or any(not isinstance(e, dict) for e in errors)
            or any(not isinstance(rows, list) or any(not isinstance(r, dict) for r in rows)
                   for rows in includes.values())):
        raise RuntimeError("Malformed X response; reservation retained")
    if path == "/tweets/search/recent":
        if errors:
            raise RuntimeError("X returned partial search errors; cannot claim complete coverage; reservation retained")
        meta = data.get("meta")
        if (not isinstance(meta, dict) or type(meta.get("result_count")) is not int
                or meta["result_count"] != len(posts)
                or ("next_token" in meta and (not isinstance(meta["next_token"], str) or not meta["next_token"]))
                or any(not isinstance(p.get(key), str) for p in posts for key in ("author_id", "created_at", "text"))):
            raise RuntimeError("Malformed X search response; reservation retained")
    elif "data" not in data and not errors:
        raise RuntimeError("Malformed X lookup response; reservation retained")
    return len(posts) + sum(len(rows) for rows in includes.values())


def reject_x_response(cache):
    if not cache.exists():
        return
    rejected = cache.parent / "rejected-responses"
    rejected.mkdir(exist_ok=True)
    destination = Path(tempfile.mkdtemp(prefix=cache.stem + "-", dir=rejected))
    shutil.move(str(cache), destination / cache.name)
    metadata = cache.with_suffix(".request.json")
    if metadata.exists():
        shutil.move(str(metadata), destination / metadata.name)


class XReader:
    def __init__(self, config):
        self.token = secret("x")
        self.config = config

    def get(self, path, params, maximum, record):
        ledger_path = WORK / "x-budget.json"
        # Reserve before each attempt; interrupted/uncertain requests keep the reservation.
        # Conservative: assume every returned media object costs as much as a post.
        for attempt in range(4):
            month = now().astimezone(PT).strftime("%Y-%m")
            ledger = read(ledger_path, {})
            cost = self.config["x_resource_usd"]
            reserve = maximum * cost
            budget_reserve(ledger, month, reserve, self.config["x_monthly_usd"])
            save(ledger_path, ledger)
            try:
                result = request("https://api.x.com/2" + path,
                                 {"Authorization": "Bearer " + self.token}, params, record)
            except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, ConnectionError) as e:
                code = getattr(e, "code", None)
                if attempt < 3 and (code is None or code == 429 or code >= 500):
                    time.sleep(min(30, 2 ** (attempt + 1)))
                    continue
                raise RuntimeError(f"X retrieval failed ({code or 'network error'}); budget reservation retained") from None
            try:
                actual = x_resources(result, path)
                if actual > maximum:
                    raise RuntimeError("Unexpected X resource count; reservation retained; inspect before retry")
            except RuntimeError:
                reject_x_response(record)
                raise
            ledger[month] = round(ledger[month] - reserve + actual * cost, 6)
            save(ledger_path, ledger)
            return result


def normalize(post):
    return {
        "id": post["id"], "author": ACCOUNTS.get(post.get("author_id"), "external-context"),
        "author_id": post.get("author_id"),
        "created_at": post.get("created_at"),
        "text": post.get("note_tweet", post.get("note_post", {})).get("text", post.get("text", "")),
        "references": post.get("referenced_tweets", post.get("referenced_posts", [])),
        "media_keys": post.get("attachments", {}).get("media_keys", []),
    }


def valid_cached_source(path):
    if path.parent.parent.parent == WORK / "provenance":
        try:
            provenance.verify(path.parent.parent)
        except (RuntimeError, ValueError, KeyError, OSError, TypeError, AttributeError):
            return False
    return True


def cached_context(run, wanted, cutoff):
    """Reuse exact-ID historical text, never substitute cached posts for a fresh search."""
    context, sources = {}, []
    for path in sorted(run.glob("context-*.json")):
        context.update({p["id"]: normalize(p) for p in read(path).get("data", []) if p["id"] in wanted})
    paths = (sorted((WORK / "provenance").glob("*/source/posts.json"), reverse=True)
             + sorted((WORK / "runs").glob("*/posts.json"), reverse=True)
             + sorted((WORK / "live/queries").glob("*/posts.json"), reverse=True))
    for path in paths:
        if not wanted - context.keys():
            break
        if path.parent == run or not valid_cached_source(path):
            continue
        bundle = read(path)
        if not bundle.get("end") or parse(bundle["end"]) > parse(cutoff):
            continue
        reused = []
        for post in bundle.get("posts", []) + bundle.get("context", []):
            if (post["id"] in wanted and post["id"] not in context
                    and post.get("created_at") and parse(post["created_at"]) <= parse(cutoff)):
                context[post["id"]] = post
                reused.append(post["id"])
        if reused:
            sources.append({"source_file": str(path), "source_sha256": provenance.digest(path),
                            "source_cutoff": bundle["end"], "post_ids": reused})
    return context, sources


def collect(run, manifest, config, accounts=None):
    accounts = ACCOUNTS if accounts is None else accounts
    target = run / "posts.json"
    if target.exists():
        return read(target)
    api = None
    posts, token, page = {}, None, 0
    while True:
        cache = run / f"x-page-{page:03}.json"
        try:
            data = read(cache)
        except ValueError:
            reject_x_response(cache)
            raise RuntimeError("Malformed X cached search JSON; reservation retained") from None
        if not cache.exists():
            if api is None:
                api = XReader(config)
            params = {"query": "(" + " OR ".join("from:" + a for a in accounts.values()) + ")",
                      "start_time": manifest["start"], "end_time": manifest["end"],
                      "max_results": 100, "tweet.fields": FIELDS}
            if token:
                params["next_token"] = token
            data = api.get("/tweets/search/recent", params, 100, cache)
        try:
            x_resources(data, "/tweets/search/recent")
        except RuntimeError:
            reject_x_response(cache)
            raise
        for p in data.get("data", []):
            if p.get("author_id") not in accounts:
                raise RuntimeError("Account ID differs from saved roster; verify the handle before proceeding")
            posts[p["id"]] = normalize(p)
        token = data.get("meta", {}).get("next_token")
        if not token:
            break
        page += 1
    missing = sorted({r["id"] for p in posts.values() for r in p["references"]} - posts.keys())
    context, reused = cached_context(run, set(missing), manifest["end"])
    for i in range(0, len(missing), 100):
        cache = run / f"context-{i:04}.json"
        data = read(cache)
        if data is None:
            ids = [pid for pid in missing[i:i + 100] if pid not in context]
            if not ids:
                continue
            if api is None:
                api = XReader(config)
            data = api.get("/tweets", {"ids": ",".join(ids), "tweet.fields": FIELDS}, len(ids), cache)
        context.update({p["id"]: normalize(p) for p in data.get("data", [])})
    unavailable = sorted(set(missing) - context.keys())
    bundle = {"start": manifest["start"], "end": manifest["end"],
              "posts": sorted(posts.values(), key=lambda p: (p["created_at"], p["id"])),
              "context": list(context.values()), "unavailable_context_ids": unavailable,
              "coverage": {a: sum(p["author"] == a for p in posts.values()) for a in accounts.values()},
              "context_reuse": reused}
    save(target, bundle)
    print(f"Collected {len(posts)} posts across {len(accounts)} accounts; {len(context)} context posts", flush=True)
    return bundle


def obj(properties):
    return {"type": "object", "properties": properties, "required": list(properties), "additionalProperties": False}


def arr(items):
    return {"type": "array", "items": items}


STRING = {"type": "string"}
TRIAGE = obj({"posts": arr(obj({"id": STRING, "relevant": {"type": "boolean"},
    "image_post_ids": arr(STRING), "image_reason": STRING, "symbols": arr(STRING)}))})
CALL = obj({"source_id": STRING, "symbol": STRING,
    "direction": {"type": "string", "enum": ["bullish", "bearish"]},
    "horizon_sessions": {"type": "integer", "minimum": 1, "maximum": 30},
    "thesis": STRING, "condition": STRING})
CLAIM = obj({"paragraph": {"type": "integer", "minimum": 1}, "claim": STRING,
    "kind": {"type": "string", "enum": ["observed", "source_report", "inference"]},
    "post_ids": arr(STRING), "web_source_ids": arr(STRING), "market_rows": arr(obj({"symbol": STRING, "timestamp": STRING})),
    "explanation": STRING})
HORIZON_VIEW = obj({"horizon": {"type": "string", "enum": ["today", "near_term", "medium_term"]},
    "thesis": STRING, "supporting_evidence": arr(STRING), "competing_view": STRING,
    "would_strengthen": arr(STRING), "would_invalidate": arr(STRING), "uncertainty": STRING})
LIVE_VIEW = obj({"horizons": arr(HORIZON_VIEW), "macro_outlook": STRING,
    "stock_watchlist": arr(obj({"symbol": STRING, "view": STRING, "condition": STRING, "invalidation": STRING})),
    "change_summary": STRING, "unresolved_questions": arr(STRING)})
BRIEF = obj({"paragraphs": arr(STRING), "evidence_ids": arr(STRING), "claims": arr(CLAIM),
    "account_memory": arr(obj({"account": STRING, "note": STRING})), "new_calls": arr(CALL), "market_view": LIVE_VIEW})


def agent(run, name, instruction, data, schema, config, images=(), effort="high"):
    return engine.generate(run, name, instruction, data, schema, config, images)


def triage(run, bundle, config):
    if not bundle["posts"]:
        return {"posts": []}
    result = agent(run, "triage", "Read every selected-account post. Classify market relevance, and identify "
        "US stock/ETF symbols explicitly discussed (no guessing tickers). Request an image ONLY when the "
        "post text or its quoted/reply context suggests a chart, position screenshot, research table, or other "
        "image is needed to understand it. Image-only posts without that textual indication are not selected. "
        "image_post_ids must identify supplied posts/context with media_keys. Exclude sports/memes/personal "
        "images. For each primary post return exactly one assessment, its id, relevant, image_post_ids, "
        "a brief image_reason (empty if none), and symbols. Do not assess context as primary posts.",
        bundle, TRIAGE, config, effort="medium")
    expected = {p["id"] for p in bundle["posts"]}
    ids = [p["id"] for p in result["posts"]]
    if len(ids) != len(set(ids)) or set(ids) != expected:
        (run / "triage.json").unlink(missing_ok=True)
        raise RuntimeError("Triage did not cover each primary post exactly once; retry required")
    with_media = {p["id"] for p in bundle["posts"] + bundle["context"] if p["media_keys"]}
    for p in result["posts"]:
        if (set(p["image_post_ids"]) - with_media or
                (p["image_post_ids"] and (not p["relevant"] or not p["image_reason"].strip()))):
            raise RuntimeError("Invalid image selection from triage")
    return result


def cached_photos(run, wanted):
    photos, sources = {}, []
    cutoff = read(run / "manifest.json", {}).get("end")
    if not cutoff or not wanted:
        return photos, sources
    paths = (sorted((WORK / "provenance").glob("*/source/images.json"), reverse=True)
             + sorted((WORK / "runs").glob("*/images.json"), reverse=True))
    for path in paths:
        if not wanted.keys() - photos.keys():
            break
        if path.parent == run or not valid_cached_source(path):
            continue
        source_cutoff = read(path.parent / "manifest.json", {}).get("end")
        if not source_cutoff or parse(source_cutoff) > parse(cutoff):
            continue
        reused = []
        for photo in read(path).get("photos", []):
            # Legacy archives lack media keys and cannot support exact-image reuse.
            key = photo.get("media_key")
            if not key or key not in wanted or key in photos:
                continue
            original = path.parent / "images" / Path(photo["path"]).name
            if not original.is_file() or provenance.digest(original) != photo.get("sha256"):
                continue
            destination = run / "images" / (hashlib.sha256(key.encode()).hexdigest()[:20] + ".jpg")
            destination.parent.mkdir(exist_ok=True)
            shutil.copy2(original, destination)
            photos[key] = {**photo, "path": str(destination), "post_ids": sorted(wanted[key])}
            reused.append(key)
        if reused:
            sources.append({"source_file": str(path), "source_sha256": provenance.digest(path),
                            "source_cutoff": source_cutoff, "media_keys": reused})
    return photos, sources


def images_for(run, selection, config):
    target = run / "images.json"
    if target.exists():
        return read(target)
    ids = sorted({i for p in selection["posts"] for i in p["image_post_ids"]})
    bundle = read(run / "posts.json", {})
    keys_by_post = {p["id"]: p["media_keys"] for p in bundle.get("posts", []) + bundle.get("context", [])
                    if p["id"] in ids}
    wanted = {}
    for pid, keys in keys_by_post.items():
        for key in keys:
            wanted.setdefault(key, set()).add(pid)
    photos, reused = cached_photos(run, wanted)
    unavailable, api = [], None
    if ids:
        for i in range(0, len(ids), 100):
            batch = ids[i:i + 100]
            cache = run / f"image-lookup-{i:04}.json"
            data = read(cache)
            if data is None:
                batch = [pid for pid in batch if not keys_by_post.get(pid)
                         or not set(keys_by_post[pid]) <= photos.keys()]
                if not batch:
                    continue
                if api is None:
                    api = XReader(config)
                data = api.get("/tweets", {"ids": ",".join(batch), "tweet.fields": "attachments",
                    "expansions": "attachments.media_keys", "media.fields": "type,url,alt_text"}, 5 * len(batch), cache)
            media = {m["media_key"]: m for m in data.get("includes", {}).get("media", [])}
            for p in data.get("data", []):
                for key in p.get("attachments", {}).get("media_keys", []):
                    m = media.get(key, {})
                    if m.get("type") != "photo" or not m.get("url"):
                        unavailable.append({"post_id": p["id"], "reason": "not an available still image"})
                        continue
                    url = urllib.parse.urlparse(m["url"])
                    if url.scheme != "https" or url.hostname != "pbs.twimg.com":
                        raise RuntimeError("Unexpected media host")
                    path = run / "images" / (hashlib.sha256(key.encode()).hexdigest()[:20] + ".jpg")
                    path.parent.mkdir(exist_ok=True)
                    if not path.exists():
                        payload = None
                        for attempt in range(4):
                            try:
                                req = urllib.request.Request(m["url"], headers={"User-Agent": "crowd-alpha/1"})
                                with urllib.request.urlopen(req, timeout=60, context=ssl.create_default_context(cafile=certifi.where())) as r:
                                    if urllib.parse.urlparse(r.geturl()).hostname != "pbs.twimg.com":
                                        raise RuntimeError("Unexpected media redirect")
                                    payload = r.read(20 * 1024 * 1024 + 1)
                                break
                            except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, ConnectionError) as e:
                                code = getattr(e, "code", None)
                                if code in (401, 403, 404, 410):
                                    unavailable.append({"post_id": p["id"], "reason": f"source image unavailable (HTTP {code})"})
                                    break
                                if attempt < 3 and (code is None or code == 429 or code >= 500):
                                    time.sleep(2 ** attempt)
                                    continue
                                raise RuntimeError(f"Image retrieval failed ({code or 'network timeout'}); cached images retained") from None
                        if payload is None:
                            continue
                        if len(payload) > 20 * 1024 * 1024:
                            raise RuntimeError("Source image exceeds 20 MiB")
                        from PIL import Image
                        import io
                        Image.open(io.BytesIO(payload)).verify()
                        path.write_bytes(payload)
                    photo = photos.setdefault(key, {"path": str(path), "post_ids": [], "alt_text": m.get("alt_text", ""),
                        "source_url": m["url"], "media_key": key, "sha256": provenance.digest(path)})
                    if p["id"] not in photo["post_ids"]:
                        photo["post_ids"].append(p["id"])
            returned = {p["id"] for p in data.get("data", [])}
            unavailable.extend({"post_id": p, "reason": "image post unavailable"} for p in set(batch) - returned
                               if not keys_by_post.get(p) or not set(keys_by_post[p]) <= photos.keys())
    result = {"photos": list(photos.values()), "unavailable": unavailable, "photo_reuse": reused}
    save(target, result)
    print(f"Selected {len(photos)} unique images from {len(ids)} posts", flush=True)
    return result


def completed_market_bars(bars, start, end):
    # Apply the timestamp window independently of the completed-session cutoff.
    current = end.astimezone(ET)
    result = {}
    for symbol, values in bars.items():
        rows = {b["t"]: b for b in values if start <= parse(b["t"]) <= end
                and (parse(b["t"]).astimezone(ET).date() < current.date() or current.hour >= 16)}
        if rows:
            result[symbol] = sorted(rows.values(), key=lambda b: b["t"])
    if not result.get("SPY"):
        raise RuntimeError("Alpaca returned no completed SPY bars; market data cannot be verified")
    return result


def market_data(run, symbols, manifest):
    target = run / "market.json"
    end = min(parse(manifest["end"]), now() - timedelta(minutes=16))
    if target.exists():
        result = read(target)
        end = min(end, parse(result["as_of"]))
        bars = completed_market_bars(result["bars"], end - timedelta(days=120), end)
        updated = dict(result, bars=bars, as_of=stamp(end), missing_symbols=sorted(symbols - bars.keys()))
        if updated != result:
            save(target, updated)
        return updated
    headers = {"APCA-API-KEY-ID": secret("key"), "APCA-API-SECRET-KEY": secret("secret")}
    start = end - timedelta(days=120)
    feed, bars, page_token = "sip", {}, None
    for page in range(200):
        params = {"symbols": ",".join(sorted(symbols)), "timeframe": "1Day",
                  "start": stamp(start), "end": stamp(end), "adjustment": "all",
                  "feed": feed, "sort": "asc", "limit": 10000}
        if page_token:
            params["page_token"] = page_token
        for attempt in range(4):
            try:
                data = request("https://data.alpaca.markets/v2/stocks/bars", headers, params,
                               run / f"alpaca-page-{page:03}.json")
                break
            except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, ConnectionError) as e:
                code = getattr(e, "code", None)
                if code == 403 and feed == "sip" and not bars:
                    feed = params["feed"] = "iex"
                    continue
                if attempt < 3 and (code is None or code == 429 or code >= 500):
                    time.sleep(2 ** attempt)
                    continue
                raise RuntimeError(f"Alpaca historical bars failed ({code or 'network timeout'})") from None
        else:
            raise RuntimeError("Alpaca retries exhausted")
        for symbol, values in data.get("bars", {}).items():
            bars.setdefault(symbol, []).extend(values)
        page_token = data.get("next_page_token")
        if not page_token:
            break
    else:
        raise RuntimeError("Alpaca pagination exceeded expected bounds")
    bars = completed_market_bars(bars, start, end)
    result = {"feed": feed, "adjustment": "all", "as_of": stamp(end), "bars": bars,
              "missing_symbols": sorted(symbols - bars.keys()),
              "limitations": "Adjusted daily OHLC; unavailable symbols remain unverified. No option P&L or intraday trigger ordering inferred."}
    save(target, result)
    print(f"Alpaca: {sum(map(len, bars.values()))} daily OHLC bars, {len(bars)} symbols, {feed} feed", flush=True)
    return result


def bar_day(bar):
    return parse(bar["t"]).astimezone(ET).date().isoformat()


def evaluate(memory, market):
    for call in memory["calls"]:
        if call["status"] != "pending" or call["condition"]:
            continue
        if now() - parse(call.get("captured_at", stamp(now()))) > timedelta(days=90):
            call.update(status="unverified", reason="Outcome data unavailable after 90 days")
            continue
        bars = market["bars"].get(call["symbol"], [])
        base = next((b for b in bars if bar_day(b) == call["reference_day"]), None)
        future = [b for b in bars if bar_day(b) > call["reference_day"]]
        if not base or len(future) < call["horizon_sessions"]:
            continue
        end = future[call["horizon_sessions"] - 1]
        change = end["c"] / base["c"] - 1
        signed = change if call["direction"] == "bullish" else -change
        call.update(status="observed", outcome_day=bar_day(end), return_pct=round(change * 100, 4),
                    direction_result="flat" if abs(change) < 0.001 else ("supported" if signed > 0 else "not_supported"),
                    feed=market["feed"])
    return memory


def compact_market(market):
    result = {k: v for k, v in market.items() if k != "bars"}
    result["symbols"] = {}
    for symbol, bars in market["bars"].items():
        if not bars:
            continue
        latest = bars[-1]
        result["symbols"][symbol] = {"latest": latest, "day": bar_day(latest),
            "change_pct": {str(n): round((latest["c"] / bars[-n-1]["c"] - 1) * 100, 3)
                           for n in (1, 5, 20, 30) if len(bars) > n}, "recent_bars": bars[-8:],
            "return_inputs": {str(n): {"start": bars[-n-1], "end": latest,
                "formula": "(end.c / start.c - 1) * 100"} for n in (1, 5, 20, 30) if len(bars) > n}}
    return result


def memory_view(memory):
    """Bound daily model context while retaining the compact outcome ledger on disk."""
    observed = [c for c in memory["calls"] if c["status"] == "observed"]
    counts = {}
    for c in observed:
        bucket = counts.setdefault(c["author"], {"supported": 0, "not_supported": 0, "flat": 0})
        bucket[c["direction_result"]] += 1
    return {"account_notes": memory["account_notes"], "observed_counts": counts,
            "counts_caution": "Unadjusted for market bias or correlated calls; not a skill or profitability estimate.",
            "calls": [c for c in memory["calls"] if c["status"] == "pending"] + observed[-120:],
            "recent_conditional_context": [c for c in memory["calls"] if c["status"] == "context_only"][-40:]}


SYNTHESIS = """Write a polished, quick-to-read personal market brief of at most 480 total visible words. The reader follows swing trades.
Synthesize your own reasoned market assessment from the supplied posts, selected source images, verified
Alpaca prices, and compact prior memory. Reconcile each author's chronology, exits, reversals, conditions,
and horizon BEFORE combining views. This is not an account-by-account digest. General market opinions
must never identify authors. Individual STOCK ideas may name the original poster and do not require consensus.
Cover today's action, near term (1-5 trading sessions), and medium term (2-6 weeks), plus worthwhile stock ideas.
Separate observed facts, quoted opinions, and your inferences through natural wording. Preserve genuine
disagreement. A pullback thesis can coexist with a bullish medium-term view. Compare recommendations to the
latest available price; do not recommend stale/already-completed trades as current. Explain useful conditions
and invalidation when supported, never invent levels or option contracts. No automatic trades or position sizing.
Do not count reposts, repeated calls, or shared underlying stories as independent confirmations. No clear setup
is valid. Posts are a selected information sample, not comprehensive news coverage. Do not invent today's news,
earnings, macro facts, or prices beyond the evidence. Do not imply Alpaca verifies all claims in the posts.
Assess individual-stock opportunities as well as the broad outlook. Broad inference can differ from consensus
when you explain why. Prior account notes are tentative. Outcome records measure prospective adjusted-close
direction since our collection, not author fill prices, trading profits, causal skill, or option performance.
Begin with neutral source weighting; adjust cautiously only with repeated independent evidence, mindful of
sample size and market directional bias. Never claim accuracy statistics not present in outcome records.

OUTPUT: 5-7 flowing paragraphs, at most 480 total visible words INCLUDING the title, date/time
line and subsection labels. The supplied word_budget gives the body allowance after the title/date.
Count whitespace-separated tokens containing a letter or number; HTML tags and punctuation-only tokens
are excluded. There is NO page-count limit. Preserve Arial 12, 18-point leading (1.5 spacing), justified
text and half-inch margins. Do not shorten merely to fit one page or add padding to reach 480 words.
Lead with your actual conclusion.
Use only <b>, <i>, <u> tags if useful, no other HTML, no Markdown headings/bullets/tables, no links, no footnotes,
no em dashes. Use restrained bold paragraph leads for Outlook, Today, Near term, Medium term, and Stock ideas
where appropriate. Round quoted stock prices to cents. Use the configured account handles when attributing stock ideas. Write about markets, not the processing workflow: avoid repetitive
'supplied', 'verified', 'Alpaca', and 'source snapshots'. Preserve material uncertainty in natural language.
Avoid boilerplate disclaimers, decorative formatting,
mechanical recaps, hype, and repeated rhetorical contrasts. Include material missing/stale-data limitations
briefly if they affect the conclusion. Evidence IDs go ONLY into evidence_ids, never in the prose. This list may contain supplied post IDs or macro source IDs; claim-level references must use the appropriate post_ids or web_source_ids field.
Communicate source-chart insights in text; chart images need not appear in the brief.

Always include one brief paragraph headed <b>Bonds, inflation & Fed:</b>, usually 2-3 sentences
(roughly 40-70 words). Use the supplied official web snapshots: 2-, 5-, and 10-year Treasury par yields,
latest headline/core CPI and PCE as relevant, and the latest FOMC statement. State the yield observation
date if it differs from the brief's date, and identify inflation's reference month. Do not confuse bond
ETF prices with yields or par yields with real-time tradable bond quotes. Yields can reprice daily;
inflation releases and Fed decisions are slower-moving. Compare macro.changes_since_previous_brief:
on quiet days retain a concise baseline rather than repeating a long explanation. Expand only for a
material release or repricing, while preserving the overall 480-word limit. Distinguish the Fed's stated
policy from your conditional outlook. Do not invent market-implied cut odds, future rate decisions,
consensus forecasts, or claim a release is new merely because it was fetched today. Missing sources must
remain unverified and stale releases must be dated. Reconcile official macro evidence with X opinions;
avoid duplicating bond commentary elsewhere. Redistribute the word budget across subsections as useful.

LIVE MARKET MEMORY: Return market_view separately from the reading brief; this internal record has
no 480-word limit. Maintain exactly three horizons: today, near_term (1-5 sessions), medium_term (2-6 weeks).
For each, retain the thesis, concrete supporting evidence, competing interpretation, what strengthens or
invalidates it, and uncertainty. Include macro outlook, stock watchlist with conditions, unresolved questions,
and a concise change_summary explaining what changed since live_memory.current_view and why (or that it
is unchanged). Preserve important unresolved context across days, re-evaluate old conditions against the
supplied evidence, and expire time-specific ideas rather than treating yesterday's intraday levels as live.
Use evidence in the current inputs and the cited previous archived view; do not invent new facts for memory.
Discussion notes distinguish user hypotheses from agent conclusions. Reconcile and challenge both, never
promote a user hypothesis to fact merely because it was remembered. Do not invent holdings or preferences.
The structured view can retain useful evidence and reasoning omitted from the short PDF. The archive retains
all inputs and the prior view, so explain historical dependencies. Shortening must preserve market_view,
account_memory and new_calls unchanged; only the reading paragraphs and their claim mappings are shortened.

MEMORY: Return a compact note for every configured account about their current thesis, horizon, specialties, and
usefulness. Notes must be paraphrases, max 90 words per account, preserving earlier unresolved context and
clearly dating stale views; silence does not reaffirm a view. Do not paste full posts into persistent memory.
new_calls: only the account's own explicit directional US stock/ETF predictions, never a repost or another
person's quoted call, with an explicitly stated 1-30 trading-session
horizon, supported by a supplied primary post. Do not guess a horizon for 'soon', current positions, or vague
conviction. A week means 5 trading sessions. Exclude expired calls and statements about moves already completed.
condition must accurately preserve any trigger, target, stop, or other qualification; such calls remain
unscored for automatic directional outcomes. Pure unconditional direction gets an empty condition.
Calls can be empty. Return source_id, symbol, direction, horizon_sessions, thesis (compact paraphrase), condition.
"""
PROVENANCE_PROMPT = """
PROVENANCE: Include claims covering EVERY factual assertion and material inference in EVERY final paragraph.
Use one-based paragraph numbers and split distinct assertions so each is manually checkable. Use observed
for official macro or Alpaca facts/calculations, source_report for unverified author/chart claims, inference for your reasoning.
List supporting post_ids (including image-bearing/context posts), web_source_ids from macro.sources
(use [] for claims without web evidence), and market_rows with exact supplied symbol
and provider timestamp. Calculations must cite both endpoint rows and state the formula and inputs in the
explanation. For forecasts/levels, identify the source rather than suggesting Alpaca confirms the prediction.
For inferences explain the evidence and uncertainty concisely; identify any dependence on prior memory.
Explicitly flag unsupported assertions or contradictory evidence instead of inventing a supporting citation.
Explain chronology when a later exit/update supersedes an earlier post. Cite all independent views underlying
an overlap judgment, and identify reposts as dependent. This audit stays outside the PDF; there is no word cap.
"""
SYNTHESIS += PROVENANCE_PROMPT
SYNTHESIS += "\nThe PDF renderer supplies the title, evidence cutoff and reconstruction date. Return only market section paragraphs; do not repeat this metadata in paragraphs or claim mappings. Every observed/source_report claim must cite supplied post_ids, web_source_ids or market_rows."
SYNTHESIS += "\nWhen a macro source is absent from macro.sources, its ID may refer to the same source in macro.previous_record.sources. Explicitly date that older observation and disclose the current retrieval gap. Current sources always take precedence for a shared ID. Do not cite IDs found only in older nested records or remembered prose."


def verify_brief(result, bundle, macro_required=False, macro=None):
    if macro_required and sum(p.startswith("<b>Bonds, inflation & Fed:</b>") for p in result["paragraphs"]) != 1:
        raise RuntimeError("Brief must contain one bonds, inflation and Fed subsection")
    if "market_view" in result:
        horizons = [h["horizon"] for h in result["market_view"]["horizons"]]
        if sorted(horizons) != ["medium_term", "near_term", "today"]:
            raise RuntimeError("Market memory must cover each horizon exactly once")
    known = {p["id"] for p in bundle["posts"] + bundle["context"]}
    known.update(s["id"] for s in provenance.macro_sources(macro))
    unknown = set(result["evidence_ids"]) - known
    if unknown:
        raise RuntimeError("Brief cites unknown evidence IDs: " + ", ".join(sorted(unknown)))
    if {x["account"] for x in result["account_memory"]} != set(ACCOUNTS.values()):
        raise RuntimeError("Account memory must cover every configured account")
    for paragraph in result["paragraphs"]:
        if re.search(r"https?://|www\.|\]\(|[\u2014]|<(?!(?:/?(?:b|i|u))>)[^>]+>", paragraph):
            raise RuntimeError("Brief contains prohibited links, formatting, or em dashes")
    primary = {p["id"] for p in bundle["posts"]}
    if any(c["source_id"] not in primary or not SYMBOL.fullmatch(c["symbol"]) for c in result["new_calls"]):
        raise RuntimeError("A memory call lacks a valid source or symbol")
    reposts = {p["id"] for p in bundle["posts"] if any(r["type"] == "retweeted" for r in p["references"])}
    if any(c["source_id"] in reposts for c in result["new_calls"]):
        raise RuntimeError("A repost cannot establish an account's original prediction")


def next_memory(memory, result, bundle, market, manifest):
    memory["account_notes"] = result["account_memory"]
    sources = {p["id"]: p for p in bundle["posts"]}
    keys = {(c["source_id"], c["symbol"], c["direction"], c["horizon_sessions"]) for c in memory["calls"]}
    for c in result["new_calls"]:
        key = (c["source_id"], c["symbol"], c["direction"], c["horizon_sessions"])
        bars = market["bars"].get(c["symbol"], [])
        if key in keys or not bars:
            continue
        c = dict(c, author=sources[c["source_id"]]["author"], captured_at=manifest["end"],
                 reference_day=bar_day(bars[-1]), reference_close=bars[-1]["c"],
                 status="context_only" if c["condition"] else "pending")
        memory["calls"].append(c)
        keys.add(key)
    memory["updated_at"] = stamp(now())
    return memory


def display_paragraphs(result, manifest):
    cut = parse(manifest["end"]).astimezone(PT)
    date_line = f"{cut.strftime('%B %d, %Y')} | As of {cut.strftime('%I:%M %p %Z').lstrip('0')}"
    if manifest.get("reconstructed_at"):
        date_line += " | Reconstructed " + parse(manifest["reconstructed_at"]).astimezone(PT).strftime('%B %d, %Y')
    return ["<b>Crowd Alpha</b> | Market intelligence from the people you follow.", f"<i>{date_line}</i>"] + result["paragraphs"]


def word_count(paragraphs):
    text = html.unescape(re.sub(r"</?(?:b|i|u)>", "", " ".join(paragraphs)))
    return sum(any(c.isalnum() for c in token) for token in text.split())


def layout_brief(result, manifest):
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.enums import TA_JUSTIFY
    from reportlab.platypus import Paragraph
    fontdir = Path("/System/Library/Fonts/Supplemental")
    for name, filename in [("Arial", "Arial.ttf"), ("Arial-Bold", "Arial Bold.ttf"),
                           ("Arial-Italic", "Arial Italic.ttf"), ("Arial-BoldItalic", "Arial Bold Italic.ttf")]:
        pdfmetrics.registerFont(TTFont(name, str(fontdir / filename)))
    pdfmetrics.registerFontFamily("Arial", normal="Arial", bold="Arial-Bold", italic="Arial-Italic", boldItalic="Arial-BoldItalic")
    style = ParagraphStyle("brief", fontName="Arial", fontSize=12, leading=18, spaceAfter=7, alignment=TA_JUSTIFY)
    paragraphs = display_paragraphs(result, manifest)
    flow=[]
    for text in paragraphs:
        safe = html.escape(text, quote=False)
        for tag in ("b", "i", "u"):
            safe = safe.replace(f"&lt;{tag}&gt;", f"<{tag}>").replace(f"&lt;/{tag}&gt;", f"</{tag}>")
        flow.append(Paragraph(safe, style))
    count=word_count(paragraphs)
    return flow, {"total_words":count, "word_limit":480, "excess_words":max(0,count-480),
                  "body_allowance":480-word_count(paragraphs[:2]), "page_limit":None,
                  "font_size_pt":12,"leading_pt":18,"width_pt":540,"paragraph_gap_pt":7}


def render(result, manifest, destination):
    from reportlab.platypus import BaseDocTemplate, Frame, PageTemplate
    from pypdf import PdfReader
    flow, metrics = layout_brief(result, manifest)
    if metrics["excess_words"]:
        return False
    tmp = destination.with_suffix(".tmp.pdf")
    document=BaseDocTemplate(str(tmp),pagesize=(612,792),leftMargin=36,rightMargin=36,topMargin=36,bottomMargin=36,
        author="", title="Crowd Alpha - "+parse(manifest["end"]).astimezone(PT).date().isoformat())
    frame=Frame(36,36,540,720,leftPadding=0,rightPadding=0,topPadding=0,bottomPadding=0)
    document.addPageTemplates(PageTemplate(id="brief",frames=[frame]))
    document.build(flow)
    reader=PdfReader(str(tmp))
    if not reader.pages or any(page.get("/Annots") for page in reader.pages):
        raise RuntimeError("PDF validation failed: empty document or link annotations")
    tmp.replace(destination)
    return True


def cleanup(current):
    previous = read(WORK / "cleanup.json", {})
    if previous and current - parse(previous["at"]) < timedelta(days=7):
        return
    cutoff = current - timedelta(days=7)
    for path in (WORK / "runs").glob("*"):
        manifest = read(path / "manifest.json", {})
        if manifest and parse(manifest["end"]) < cutoff:
            record = WORK / "provenance" / path.name
            # Never delete the only copy of evidence, including incomplete/failed runs.
            if not record.exists():
                continue
            provenance.verify(record)
            # An archive must contain the exact current source files before temporary copies go.
            if any(not (record / "source" / p.relative_to(path)).is_file() or
                   provenance.digest(p) != provenance.digest(record / "source" / p.relative_to(path))
                   for p in path.rglob("*") if p.is_file()):
                continue
            shutil.rmtree(path)
    log = WORK / "schedule.log"
    if log.exists() and log.stat().st_size > 1_000_000:
        lines = log.read_text(errors="replace").splitlines()[-500:]
        log.write_text("\n".join(lines) + "\n")
    save(WORK / "cleanup.json", {"at": stamp(current)})


def self_check():
    ledger = {}
    budget_reserve(ledger, "2026-09", 29.5, 30)
    try:
        budget_reserve(ledger, "2026-09", 0.51, 30)
    except RuntimeError:
        pass
    else:
        raise AssertionError("budget crossed its cap")
    assert ledger["2026-09"] == 29.5
    bars = [{"t": f"2026-09-{d:02}T04:00:00Z", "c": c} for d, c in [(21, 100), (22, 98), (23, 96)]]
    call = {"status": "pending", "condition": "", "symbol": "SPY", "reference_day": "2026-09-21",
            "horizon_sessions": 2, "direction": "bearish"}
    m = {"calls": [dict(call), dict(call, condition="only below 99"), dict(call, horizon_sessions=5)]}
    evaluate(m, {"bars": {"SPY": bars}, "feed": "sip"})
    assert m["calls"][0]["direction_result"] == "supported" and m["calls"][0]["return_pct"] == -4
    assert m["calls"][1]["status"] == m["calls"][2]["status"] == "pending"
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "check.pdf"
        assert render({"paragraphs": ["<b>View.</b> No clear setup. " * 8]}, {"end": "2026-09-25T00:00:00Z"}, p)
        assert not render({"paragraphs": ["Too long. " * 2000]}, {"end": "2026-09-25T00:00:00Z"}, p)
        global WORK
        original_work = WORK
        WORK = Path(tmp) / ".work"
        try:
            old, fresh = WORK / "runs" / "old", WORK / "runs" / "fresh"
            save(old / "manifest.json", {"end": stamp(now() - timedelta(days=9))})
            save(fresh / "manifest.json", {"end": stamp(now())})
            save(WORK / "memory.json", {"sentinel": True})
            cleanup(now())
            assert old.exists() and fresh.exists() and p.exists()  # No archive: preserve evidence.
            (WORK / "cleanup.json").unlink()
            record = WORK / "provenance" / "old"
            shutil.copytree(old, record / "source")
            save(record / "integrity.json", {"sha256": {"source/manifest.json": provenance.digest(old / "manifest.json")}})
            cleanup(now())
            assert not old.exists() and record.exists() and fresh.exists() and p.exists()
            assert provenance.verify(record) == 1
            (record / "source/manifest.json").write_text("tampered")
            try:
                provenance.verify(record)
            except RuntimeError:
                pass
            else:
                raise AssertionError("Tampered evidence was accepted")
            assert read(WORK / "memory.json") == {"sentinel": True}
        finally:
            WORK = original_work
    print("Self-check passed: budget, prospective outcomes, conditional abstention, PDF word ceiling, cleanup retention and provenance tampering")


def resolve_codex(config):
    # Preserve the runner's preflight boundary for either engine.
    return engine.resolve(config)


def daily_manifest(day):
    date = datetime.strptime(day, "%Y-%m-%d")
    end = date.replace(hour=REPORT_HOUR, tzinfo=PT) - timedelta(seconds=20)
    start = (date - timedelta(days=1)).replace(hour=REPORT_HOUR, tzinfo=PT) - timedelta(seconds=20)
    return {"due_day": day, "start": stamp(start), "end": stamp(end)}


def prior_record(cutoff):
    candidates = []
    for record in (WORK / "provenance").glob("*"):
        source = record / "source/manifest.json"
        try:
            if source.exists() and parse(read(source)["end"]) < cutoff:
                candidates.append((read(source)["end"], record.name, record))
        except (ValueError, KeyError, OSError):
            continue
    for _, _, record in sorted(candidates, reverse=True):
        try:
            provenance.verify(record)
            return record
        except (RuntimeError, ValueError, KeyError, OSError):
            continue
    return None


def publish_record(record, due_day):
    provenance.verify(record)
    metadata = read(record / "record.json")
    output = ROOT / metadata["pdf"]
    if not output.exists() or provenance.digest(output) != provenance.digest(record / metadata["pdf"]):
        temporary = output.with_suffix(".tmp.pdf")
        shutil.copy2(record / metadata["pdf"], temporary)
        temporary.replace(output)
    live_memory.publish(WORK, record)
    state = read(WORK / "status.json", {})
    manifest = read(record / "source/manifest.json")
    cutoff = manifest["end"]
    # Backfilled history must never roll the current account memory backwards.
    if due_day >= state.get("last_success_due_day", "") and cutoff >= read(WORK / "live/current.json", {}).get("as_of", ""):
        save(WORK / "memory.json", read(record / "source/memory-after.json"))
        macro = read(record / "source/macro.json")
        if macro:
            save(WORK / "macro-latest.json", {"as_of": macro["as_of"], "fingerprints": macro["fingerprints"]})
    if not manifest.get("ad_hoc") and due_day >= state.get("last_success_due_day", ""):
        save(WORK / "status.json", {"last_success_due_day": due_day, "completed_at": stamp(now()),
             "pdf": str(output), "provenance": str(record / "index.html"), "state": "complete"})


def run_job(scheduled=False, stage="all", test_run=False, day=None):
    config = read(WORK / "config.json")
    current = now()
    local = current.astimezone(PT)
    due_day = day or (local.date() if local.hour >= REPORT_HOUR or test_run else (local - timedelta(days=1)).date()).isoformat()
    state = read(WORK / "status.json", {})
    record_id = due_day
    if test_run:
        revision = 1
        while (WORK / "runs" / f"{due_day}-r{revision}").exists() or (WORK / "provenance" / f"{due_day}-r{revision}").exists():
            revision += 1
        record_id = f"{due_day}-r{revision}"
        print(f"Explicit fresh test run: {record_id}", flush=True)
    # No retroactive first launch before this project's installation date.
    if scheduled and due_day < config["installed_date"]:
        return
    if day is None and not test_run and state.get("last_success_due_day", "") >= due_day:
        if state.get("provenance"):
            live_memory.publish(WORK, Path(state["provenance"]).parent)
        return
    record = WORK / "provenance" / record_id
    if record.exists():
        publish_record(record, due_day)
        return
    # Check on every attempt, even when a failed runtime snapshot already exists.
    # A broken local dependency must not spend the X budget before failing.
    config, codex_version = resolve_codex(config)
    cleanup(current)
    run = WORK / "runs" / record_id
    run.mkdir(parents=True, exist_ok=True)
    manifest = read(run / "manifest.json")
    if not manifest:
        if test_run:
            end = current - timedelta(seconds=20)
            manifest = {"due_day": due_day, "start": stamp(end - timedelta(hours=24)), "end": stamp(end), "ad_hoc": True}
        else:
            manifest = daily_manifest(due_day)
        save(run / "manifest.json", manifest)
    historical = parse(manifest["end"]).astimezone(PT).date() < local.date()
    if historical and not manifest.get("reconstructed_at"):
        manifest["reconstructed_at"] = stamp(current)
        save(run / "manifest.json", manifest)
    runtime_files = ("market_brief.py", "provenance.py", "engine.py", "settings.py", "macro_data.py", "live_memory.py", "writing.md")
    revision = hashlib.sha256("".join(provenance.digest(CODE / n) for n in runtime_files).encode()).hexdigest()[:16]
    runtime = run / "runtime" / revision
    if not runtime.exists():
        runtime.mkdir(parents=True)
        for name in runtime_files:
            shutil.copy2(CODE / name, runtime / name)
        save(runtime / "environment.json", {"python": sys.version, "executable": sys.executable,
             "engine_version": codex_version, "engine": config["engine"], "model": config["model"],
             "accounts": ACCOUNTS, "timezone": str(PT), "captured_at": stamp(now())})
    try:
        bundle = collect(run, manifest, config)
        if stage == "collect":
            return
        selection = triage(run, bundle, config)
        if stage == "triage":
            return
        images = images_for(run, selection, config)
        previous = prior_record(parse(manifest["end"]))
        if previous:
            provenance.verify(previous)
        memory = read(previous / "source/memory-after.json", {"account_notes": [], "calls": []}) if previous else {"account_notes": [], "calls": []}
        symbols = {"SPY", "QQQ", "IWM"}
        symbols.update(s for p in selection["posts"] if p["relevant"] for s in p["symbols"] if SYMBOL.fullmatch(s))
        symbols.update(c["symbol"] for c in memory["calls"] if c["status"] == "pending")
        market = market_data(run, symbols, manifest)
        previous_macro = read(previous / "source/macro.json", {}) if previous else {}
        macro = macro_data.collect(run, parse(manifest["end"]), previous_macro,
                                   historical_source=previous / "source" if historical and previous else None,
                                   historical=historical)
        if (run / "memory-before.json").exists():
            memory = read(run / "memory-before.json")
        else:
            save(run / "memory-incoming.json", memory)
            memory = evaluate(memory, market)
            save(run / "memory-before.json", memory)
        if stage == "market":
            return
        if not (run / "live-before.json").exists():
            context = live_memory.context(WORK, cutoff=parse(manifest["end"]))
            context["current_view"] = read(previous / "source/live-after.json", {}) if previous else {}
            save(run / "live-before.json", context)
        live_before = read(run / "live-before.json")
        data = {"posts": bundle, "triage": selection, "images": images, "market": compact_market(market),
                "memory": memory_view(memory), "accounts": list(ACCOUNTS.values()), "macro": macro, "live_memory": live_before,
                "word_budget": layout_brief({"paragraphs": []}, manifest)[1], "collection_window": manifest,
                "historical_policy": "Use only evidence available by the collection cutoff. Later retrieval is a retrospective reconstruction. Do not use later market outcomes or newer memory. Explicitly date stale macro snapshots; missing historical releases remain unverified."}
        result = agent(run, "brief", SYNTHESIS, data, BRIEF, config, [p["path"] for p in images["photos"]])
        verify_brief(result, bundle, macro_required=True, macro=macro)
        output = ROOT / (record_id + "-market-brief.pdf")
        candidate = run / output.name
        for attempt in range(4):
            _, metrics = layout_brief(result, manifest)
            save(run / "layout.json", metrics)
            if render(result, manifest, candidate):
                break
            if attempt == 3:
                raise RuntimeError("Brief still exceeds 480 total words; no PDF published")
            prior_memory = {k: result[k] for k in ("market_view", "account_memory", "new_calls")}
            result = agent(run, f"shorten-{attempt}", SYNTHESIS + "\nThe supplied draft exceeds 480 total words. "
                f"Remove at least {metrics['excess_words']} words from its paragraphs. "
                "Preserve useful conditions and uncertainty; update claim mappings. Keep market_view, account_memory and new_calls unchanged.",
                {"draft": result, "word_budget": metrics}, BRIEF, config)
            if any(result[k] != value for k, value in prior_memory.items()):
                raise RuntimeError("Shortening changed persistent memory; review required")
            verify_brief(result, bundle, macro_required=True, macro=macro)
        else:
            raise RuntimeError("Brief still exceeds 480 total words; no PDF published")
        provenance.validate_claims(result, bundle, market, macro)
        memory = next_memory(memory, result, bundle, market, manifest)
        if historical:
            for call in memory["calls"]:
                if call.get("captured_at") == manifest["end"]:
                    call.update(status="context_only", reconstructed_at=stamp(current))
        save(run / "memory-after.json", memory)
        save(run / "final.json", result)
        save(run / "live-after.json", {"as_of": manifest["end"], "record": run.name,
             "previous_record": live_before.get("current_view", {}).get("record"), "view": result["market_view"]})
        provenance.archive(run, candidate, WORK)
        publish_record(WORK / "provenance" / record_id, due_day)
        print(f"Published {output}", flush=True)
    except Exception as error:
        reject_cached_draft(run, error)
        raise


def pending_days(config, current):
    local = current.astimezone(PT)
    last = local.date() if local.hour >= REPORT_HOUR else (local - timedelta(days=1)).date()
    completed = {}
    for record in sorted((WORK / "provenance").glob("*")):
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}(?:-r[1-9]\d*)?", record.name):
            continue
        try:
            metadata = read(record / "record.json", {})
        except (ValueError, OSError):
            metadata = {}
        if metadata and not metadata.get("window", {}).get("ad_hoc"):
            day = metadata.get("window", {}).get("due_day", record.name[:10])
            old = completed.get(day)
            if old is None or metadata.get("archived_at", "") > old[0]:
                completed[day] = (metadata.get("archived_at", ""), record)
    missing = []
    date = datetime.strptime(config["installed_date"], "%Y-%m-%d").date()
    while date <= last:
        day = date.isoformat()
        if day in completed:
            record = completed[day][1]
            try:
                provenance.verify(record)
                output = ROOT / read(record / "record.json")["pdf"]
                if not output.exists() or provenance.digest(output) != provenance.digest(record / output.name):
                    publish_record(record, day)
            except (RuntimeError, ValueError, KeyError, OSError):
                missing.append(day)
        else:
            missing.append(day)
        date += timedelta(days=1)
    return missing


def notify_failure(day, error):
    message = f"{day} report pending: {str(error)[:230]}. Automatic retries continue; see .local/queue.json."
    try:
        result = subprocess.run(["/usr/bin/osascript", "-e", "display notification " + json.dumps(message, ensure_ascii=False)
                        + ' with title "Crowd Alpha needs attention"'], capture_output=True, timeout=15)
        return result.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False  # A notification failure must not stop source capture or later days.


def reject_cached_draft(run, error):
    """Preserve rejected model responses, then allow a corrected response on retry."""
    stages = []
    if (run / "brief.json").exists() and not (run / "final.json").exists():
        stages = ["brief"] + [p.stem for p in run.glob("shorten-*.json") if re.fullmatch(r"shorten-\d+", p.stem)]
    elif str(error).startswith(("Triage", "Invalid image selection")):
        stages = ["triage"]
    if not stages:
        return
    folder = run / "rejected-drafts"
    folder.mkdir(exist_ok=True)
    for name in stages:
        destination = Path(tempfile.mkdtemp(prefix=name + "-", dir=folder))
        for suffix in (".json", "-execution.json", "-prompt.txt", "-schema.json", "-agent.log"):
            source = run / (name + suffix)
            if source.exists():
                shutil.move(str(source), destination / source.name)
        save(run / (name + "-feedback.json"), {"error": str(error)[:400], "at": stamp(now()),
             "preserved_response": str(destination.relative_to(run))})


def run_pending(force=False):
    config = read(WORK / "config.json")
    days = pending_days(config, now())
    queue = read(WORK / "queue.json", {"days": {}})
    queue["pending_days"] = days
    for day, entry in list(queue["days"].items()):
        if day not in days:
            queue["days"][day] = {"state": "complete", "verified_at": stamp(now())}
        elif entry.get("state") == "running":
            entry["state"] = "pending"
    def persist():
        queue["updated_at"] = stamp(now())
        save(WORK / "queue.json", queue)
        status = read(WORK / "status.json", {})
        status.update(pending_days=list(queue["pending_days"]),
                      state="recovering" if queue["pending_days"] else "complete")
        if not queue["pending_days"]:
            for key in ("error", "failed_at", "attempt_day", "attempts"):
                status.pop(key, None)
        save(WORK / "status.json", status)
    def ready(day):
        retry = queue["days"].get(day, {}).get("retry_at")
        return force or not retry or parse(retry) <= now()
    def failed(day, stage, error):
        entry = queue["days"].setdefault(day, {})
        signature = stage + ": " + str(error)[:400]
        last_notice = entry.get("notified_at")
        should_notify = not force and (entry.get("notified_error") != signature or not last_notice
                         or now() - parse(last_notice) >= timedelta(days=1))
        count = entry.get("attempts", 0) + 1
        entry.update(state="failed", stage=stage, attempts=count, error=str(error)[:400],
                     failed_at=stamp(now()), retry_at=stamp(now() + timedelta(minutes=min(360, 30 * 2 ** min(count - 1, 4)))))
        persist()
        print(f"{day} {stage} failed; retained in queue: {error}", file=sys.stderr, flush=True)
        if should_notify and notify_failure(day, error):
            entry.update(notified_error=signature, notified_at=stamp(now()))
            persist()
    persist()
    # Capture every outstanding day's expiring evidence before spending time on models.
    # Model outages cannot prevent new days from being collected.
    captured = []
    for day in days:
        if not ready(day):
            continue
        try:
            run = WORK / "runs" / day
            run.mkdir(parents=True, exist_ok=True)
            manifest = read(run / "manifest.json") or daily_manifest(day)
            save(run / "manifest.json", manifest)
            if not (run / "posts.json").exists() and parse(manifest["start"]) < now() - timedelta(days=7):
                raise RuntimeError("X window exceeds recent-search retention and no complete cache exists; historical source recovery required")
            collect(run, manifest, config)
            historical = parse(manifest["end"]).astimezone(PT).date() < now().astimezone(PT).date()
            previous = prior_record(parse(manifest["end"]))
            if previous:
                provenance.verify(previous)
            macro_data.collect(run, parse(manifest["end"]),
                read(previous / "source/macro.json", {}) if previous else {},
                historical_source=previous / "source" if historical and previous else None, historical=historical)
            captured.append(day)
        except Exception as error:
            failed(day, "capture", error)
    for day in captured:
        queue["days"].setdefault(day, {}).update(state="running", stage="report", started_at=stamp(now()))
        persist()
        try:
            run_job(day=day)
            queue["days"][day] = {"state": "complete", "completed_at": stamp(now())}
            queue["pending_days"].remove(day)
        except Exception as error:
            failed(day, "report", error)
        persist()
    queue["pending_days"] = pending_days(config, now())
    persist()
    status = read(WORK / "status.json", {})
    status["pending_days"] = queue["pending_days"]
    status["state"] = "needs_attention" if queue["pending_days"] else "complete"
    save(WORK / "status.json", status)
    if queue["pending_days"]:
        print("Still queued: " + ", ".join(queue["pending_days"]), file=sys.stderr, flush=True)
        return False
    print("Every due date has a verified report; no gaps remain.", flush=True)
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__, epilog="Setup and demo commands: --setup, --credentials, --resolve-accounts, --check, --smoke-engine, --schedule, --unschedule, --demo. Use --setup --help for their options; see docs/setup.md for the walkthrough.")
    parser.add_argument("--scheduled", action="store_true")
    parser.add_argument("--test-run", action="store_true", help="Retrieve a fresh rolling 24-hour snapshot; does not complete a scheduled daily window")
    parser.add_argument("--backfill", action="store_true", help="Retry every unfinished due date immediately, preserving each historical cutoff")
    parser.add_argument("--self-check", action="store_true")
    parser.add_argument("--verify-provenance", metavar="YYYY-MM-DD[-rN]")
    parser.add_argument("--stage", choices=["all", "collect", "triage", "market"], default="all")
    args = parser.parse_args()
    os.umask(0o077)
    if args.test_run and (args.scheduled or args.backfill):
        parser.error("Use --test-run separately from scheduled/backfill mode")
    if args.verify_provenance:
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}(?:-r[1-9]\d*)?", args.verify_provenance):
            parser.error("Expected a dated provenance record, optionally followed by -rN")
        datetime.strptime(args.verify_provenance.split("-r")[0], "%Y-%m-%d")
        record = WORK / "provenance" / args.verify_provenance
        count = provenance.verify(record)
        print(f"Verified {count} archived files: {record}")
        return
    if args.self_check:
        self_check()
        return
    WORK.mkdir(parents=True, exist_ok=True)
    with (WORK / "run.lock").open("w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        try:
            initialize()
            if args.scheduled or args.backfill:
                if not run_pending(force=args.backfill):
                    raise SystemExit(1)
            else:
                run_job(args.scheduled, args.stage, args.test_run)
        except Exception as e:
            status = read(WORK / "status.json", {})
            status.update(state="failed", failed_at=stamp(now()), error=str(e)[:400])
            save(WORK / "status.json", status)
            print(f"Crowd Alpha failed: {e}", file=sys.stderr, flush=True)
            notice = status.get("runner_notice", {})
            if not args.backfill and (notice.get("error") != str(e)[:400] or not notice.get("at")
                    or now() - parse(notice["at"]) >= timedelta(days=1)):
                if notify_failure("Runner", e):
                    status["runner_notice"] = {"error": str(e)[:400], "at": stamp(now())}
                    save(WORK / "status.json", status)
            raise SystemExit(1)


if __name__ == "__main__":
    main()
