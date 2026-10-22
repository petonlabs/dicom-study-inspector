"""Bounded archive listing persistence and pure S3 XML parsing."""

import urllib.parse
import xml.etree.ElementTree as ET

from .artifacts import inside, sha
from .config import S3_ROOT

NS = {"s": "http://s3.amazonaws.com/doc/2006-03-01/"}


def listing_url(prefix, delimiter, limit):
    params = {"list-type": "2", "prefix": prefix, "max-keys": limit}
    if delimiter:
        params["delimiter"] = delimiter
    return S3_ROOT + "?" + urllib.parse.urlencode(params)


def object_url(key):
    return S3_ROOT + urllib.parse.quote(key, safe="/")


def parse_object(element):
    return {
        "key": element.find("s:Key", NS).text,
        "size": int(element.find("s:Size", NS).text),
        "etag": element.find("s:ETag", NS).text,
    }


def parse_listing(data):
    root = ET.fromstring(data)
    prefixes = [p.text for p in root.findall("s:CommonPrefixes/s:Prefix", NS)]
    objects = [parse_object(e) for e in root.findall("s:Contents", NS)]
    return prefixes, objects, root.find("s:IsTruncated", NS).text == "true"


class Catalog:
    def __init__(self, state, transport):
        self.state, self.transport = state, transport

    def listing(self, prefix, delimiter=None, limit=200):
        url = listing_url(prefix, delimiter, limit)
        records = self.state.ledger["listings"]
        previous = next((r for r in records if r["url"] == url), None)
        data = self.cached(previous) if previous else self.fetch(url)
        return parse_listing(data)

    def cached(self, record):
        path = inside(self.state.out, record["path"])
        if sha(path) != record["sha256"]:
            raise ValueError("Cached listing changed")
        return path.read_bytes()

    def fetch(self, url):
        records = self.state.ledger["listings"]
        relative = f"catalog/{len(records):03d}.xml"
        path = inside(self.state.out, relative)
        data = self.transport.metadata(url)
        path.write_bytes(data)
        records.append({"url": url, "path": relative, "sha256": sha(path)})
        self.state.save()
        return data
