"""HTTP adapter behind a narrow injectable port; only acquisition depends on it."""

from pathlib import Path
from typing import Protocol
from urllib import request

from .config import METADATA_RESPONSE_LIMIT, USER_AGENT


class ArchiveTransport(Protocol):
    def metadata(self, url: str) -> bytes: ...
    def content_length(self, url: str) -> int: ...
    def download(self, url: str, destination: Path, expected_size: int) -> int: ...


class HttpArchiveTransport:
    """Anonymous GET/HEAD only; bounded response reads and no upload capability."""

    def metadata(self, url: str) -> bytes:
        query = request.Request(url, headers={"User-Agent": USER_AGENT})
        with request.urlopen(query, timeout=45) as response:
            data = response.read(METADATA_RESPONSE_LIMIT + 1)
        if len(data) > METADATA_RESPONSE_LIMIT:
            raise ValueError("Metadata response exceeds 4 MiB limit")
        return data

    def content_length(self, url: str) -> int:
        with request.urlopen(
            request.Request(url, method="HEAD"), timeout=45
        ) as response:
            return int(response.headers["Content-Length"])

    def download(self, url: str, destination: Path, expected_size: int) -> int:
        received = 0
        with (
            request.urlopen(url, timeout=45) as response,
            destination.open("xb") as output,
        ):
            if int(response.headers.get("Content-Length", -1)) != expected_size:
                raise ValueError("GET length differs from reserved size")
            while received < expected_size:
                block = response.read(min(65536, expected_size - received))
                if not block:
                    raise ValueError("Truncated download")
                output.write(block)
                received += len(block)
        return received
