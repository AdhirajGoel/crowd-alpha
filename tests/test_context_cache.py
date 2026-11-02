"""Exact-ID context reuse saves paid reads without replacing fresh primary searches."""
import tempfile
from pathlib import Path
from unittest.mock import patch
import market_brief as m


def post(pid, created="2026-10-01T12:00:00Z", refs=()):
    return {"id": pid, "author_id": next(iter(m.ACCOUNTS)), "created_at": created,
            "text": "Historical text", "referenced_tweets": [{"id": x, "type": "quoted"} for x in refs]}


with tempfile.TemporaryDirectory() as tmp, patch.object(m, "WORK", Path(tmp)):
    work = Path(tmp)
    old = work / "provenance/2026-10-01/source/posts.json"
    m.save(old, {"end": "2026-10-01T23:59:40Z", "posts": [m.normalize(post("10"))], "context": []})
    old_bytes = old.read_bytes()
    # A later report and a post created after the cutoff must not leak into a backfill.
    m.save(work / "runs/2026-10-03/posts.json", {"end": "2026-10-03T23:59:40Z",
           "posts": [m.normalize(post("11"))], "context": []})
    m.save(work / "live/queries/earlier/posts.json", {"end": "2026-10-01T23:59:40Z",
           "posts": [m.normalize(post("11", created="2026-10-03T00:00:00Z"))], "context": []})
    run = work / "runs/2026-10-02"
    manifest = {"start": "2026-10-01T23:59:40Z", "end": "2026-10-02T23:59:40Z"}
    m.save(run / "x-page-000.json", {"data": [post("20", refs=("10", "11"))]})
    with patch.object(m, "secret", return_value="test"), \
         patch.object(m.XReader, "get", return_value={"data": [post("11")]}) as fetch:
        bundle = m.collect(run, manifest, {})
        assert {p["id"] for p in bundle["context"]} == {"10", "11"}
        assert fetch.call_count == 1
        assert fetch.call_args.args[0] == "/tweets" and fetch.call_args.args[1]["ids"] == "11"
        assert bundle["context_reuse"][0]["post_ids"] == ["10"]
        assert bundle["context_reuse"][0]["source_sha256"] == m.provenance.digest(old)
    with patch.object(m, "secret", side_effect=AssertionError("No credentials needed for cache")), \
         patch.object(m.XReader, "get", side_effect=AssertionError("No paid retry")):
        assert m.collect(run, manifest, {}) == bundle
        # A partially completed run retains its original context-page numbering.
        (run / "posts.json").unlink()
        m.save(run / "context-0000.json", {"data": [post("10"), post("11")]})
        bundle = m.collect(run, manifest, {})
        assert {p["id"] for p in bundle["context"]} == {"10", "11"}
        assert bundle["context_reuse"] == []
    assert old.read_bytes() == old_bytes

    # New windows still perform a primary search, while known parent text is reused.
    fresh = work / "live/queries/fresh"
    with patch.object(m, "secret", return_value="test"), \
         patch.object(m.XReader, "get", return_value={"data": [post("21", refs=("10",))]}) as fetch:
        bundle = m.collect(fresh, manifest, {})
        assert fetch.call_count == 1 and fetch.call_args.args[0] == "/tweets/search/recent"
        assert bundle["context"][0]["id"] == "10"
    # Cached empty search results do not trigger requests or credential access.
    empty = work / "runs/empty"
    m.save(empty / "x-page-000.json", {"data": [], "meta": {}})
    with patch.object(m, "secret", side_effect=AssertionError("No credentials needed")):
        assert m.collect(empty, manifest, {})["posts"] == []

print("PASS: context reuse, cutoff isolation, retry caches, fresh search, provenance, no credential access on cache hits")

with tempfile.TemporaryDirectory() as tmp, patch.object(m, "WORK", Path(tmp)):
    work = Path(tmp)
    source = work / "provenance/2026-10-01/source"
    original = source / "images/photo.jpg"
    original.parent.mkdir(parents=True)
    original.write_bytes(b"saved image bytes")
    m.save(source / "manifest.json", {"end": "2026-10-01T23:59:40Z"})
    m.save(source / "images.json", {"photos": [{"path": "/removed/run/images/photo.jpg",
           "post_ids": ["10"], "media_key": "photo", "alt_text": "chart",
           "source_url": "https://pbs.twimg.com/media/test", "sha256": m.provenance.digest(original)}]})
    # Legacy archives predate media keys and hashes; they must not block cache misses.
    legacy = work / "provenance/2026-09-24/source"
    m.save(legacy / "manifest.json", {"end": "2026-09-24T23:59:40Z"})
    m.save(legacy / "images.json", {"photos": [{"path": "/removed/legacy.jpg",
           "post_ids": ["9"], "alt_text": "old chart"}]})
    legacy_bytes = (legacy / "images.json").read_bytes()
    def image_run(name, keys):
        run = work / "runs" / name
        m.save(run / "manifest.json", {"end": "2026-10-02T23:59:40Z"})
        m.save(run / "posts.json", {"posts": [{**m.normalize(post("20")), "media_keys": keys}], "context": []})
        return run
    selection = {"posts": [{"image_post_ids": ["20"]}]}
    with patch.object(m, "secret", side_effect=AssertionError("No credentials needed")), \
         patch.object(m.XReader, "get", side_effect=AssertionError("No paid image lookup")), \
         patch.object(m.urllib.request, "urlopen", side_effect=AssertionError("No image download")):
        result = m.images_for(image_run("cached", ["photo"]), selection, {})
        photo = result["photos"][0]
        assert Path(photo["path"]).read_bytes() == original.read_bytes()
        assert photo["post_ids"] == ["20"]
        assert result["photo_reuse"][0]["media_keys"] == ["photo"]
    # An uncached attachment still needs a lookup; a partial photo cache is insufficient.
    with patch.object(m, "secret", return_value="test"), \
         patch.object(m.XReader, "get", return_value={"data": []}) as fetch:
        result = m.images_for(image_run("partial", ["photo", "unknown"]), selection, {})
        assert fetch.call_count == 1 and fetch.call_args.args[1]["ids"] == "20"
        assert result["unavailable"][0]["post_id"] == "20"
        assert (legacy / "images.json").read_bytes() == legacy_bytes
    # A cached response for only the paid subset must not label reused photos unavailable.
    retry = image_run("retry", ["photo"])
    bundle = m.read(retry / "posts.json")
    bundle["posts"].append({**m.normalize(post("21")), "media_keys": ["unknown"]})
    m.save(retry / "posts.json", bundle)
    m.save(retry / "image-lookup-0000.json", {"data": []})
    with patch.object(m, "secret", side_effect=AssertionError("No credentials needed")):
        result = m.images_for(retry, {"posts": [{"image_post_ids": ["20", "21"]}]}, {})
        assert [x["post_id"] for x in result["unavailable"]] == ["21"]
    # A future snapshot or corrupt cached file cannot suppress retrieval.
    for name in ("future", "corrupt"):
        isolated = work / name
        m.save(isolated / "provenance/old/source/manifest.json", {"end":
               "2026-10-03T23:59:40Z" if name == "future" else "2026-10-01T23:59:40Z"})
        m.save(isolated / "provenance/old/source/images.json", m.read(source / "images.json"))
        bad = isolated / "provenance/old/source/images/photo.jpg"
        bad.parent.mkdir()
        bad.write_bytes(original.read_bytes() if name == "future" else b"changed")
        with patch.object(m, "WORK", isolated), patch.object(m, "secret", return_value="test"), \
             patch.object(m.XReader, "get", return_value={"data": []}) as fetch:
            result = m.images_for(image_run(name, ["photo"]), selection, {})
            assert fetch.call_count == 1 and result["photos"] == []

print("PASS: archived chart reuse, preserved bytes and attribution, partial/future/corrupt cache fallback")
