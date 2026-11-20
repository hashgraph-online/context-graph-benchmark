"""Pinned o200k ordinary tokens. Acquisition is an explicit separate operation."""

import base64
import importlib.metadata
import heapq
from functools import lru_cache
from pathlib import Path
import urllib.request

from .jsonio import digest, require, write_json, write_new

VOCAB_URL = "https://openaipublic.blob.core.windows.net/encodings/o200k_base.tiktoken"
VOCAB_SHA = "446a9538cb6c348e3516120d7c08b09f57c36495e2acfffe59a5bf8b0cfb1a2d"
# Pattern from openai/tiktoken 0.12.0, MIT; see third-party/tiktoken-LICENSE.
PATTERN = "|".join([
    r"[^\r\n\p{L}\p{N}]?[\p{Lu}\p{Lt}\p{Lm}\p{Lo}\p{M}]*[\p{Ll}\p{Lm}\p{Lo}\p{M}]+(?i:'s|'t|'re|'ve|'m|'ll|'d)?",
    r"[^\r\n\p{L}\p{N}]?[\p{Lu}\p{Lt}\p{Lm}\p{Lo}\p{M}]+[\p{Ll}\p{Lm}\p{Lo}\p{M}]*(?i:'s|'t|'re|'ve|'m|'ll|'d)?",
    r"\p{N}{1,3}", r" ?[^\s\p{L}\p{N}]+[\r\n/]*", r"\s*[\r\n]+", r"\s+(?!\S)", r"\s+",
])


def prepare(directory, source=None):
    directory = Path(directory)
    require(not directory.exists(), "tokenizer destination exists")
    if source is None:
        with urllib.request.urlopen(VOCAB_URL, timeout=60) as response:
            require(response.geturl() == VOCAB_URL, "unexpected vocabulary redirect")
            payload = response.read(8 * 1024 * 1024 + 1)
    else:
        with Path(source).open("rb") as stream:
            payload = stream.read(8 * 1024 * 1024 + 1)
    require(len(payload) <= 8 * 1024 * 1024 and digest(payload) == VOCAB_SHA,
            "vocabulary hash mismatch")
    directory.mkdir(parents=True)
    write_new(directory / "o200k_base.tiktoken", payload)
    counter = Counter(directory)
    write_json(directory / "identity.json", counter.identity)
    return counter.identity


class Counter:
    def __init__(self, directory):
        import regex
        require(importlib.metadata.version("regex") == "2026.11.7", "unqualified tokenizer pretokenizer")
        directory = Path(directory)
        path = directory / "o200k_base.tiktoken"
        require(path.is_file() and not path.is_symlink(), "prepare tokenizer explicitly before running")
        with path.open("rb") as stream:
            payload = stream.read(8 * 1024 * 1024 + 1)
        require(digest(payload) == VOCAB_SHA, "vocabulary hash mismatch")
        ranks = {}
        for line in payload.splitlines():
            token, rank = line.split()
            ranks[base64.b64decode(token, validate=True)] = int(rank)
        require(len(ranks) == 199998, "invalid vocabulary inventory")
        self.ranks = ranks
        self.pattern = regex.compile(PATTERN, flags=regex.VERSION0)
        self.directory = directory
        self.identity = {"id": "o200k-base-ordinary.v1", "implementation": "cgb-python-bpe+regex-" + importlib.metadata.version("regex"),
                         "version": "1", "vocabulary_sha256": VOCAB_SHA,
                         "pattern_sha256": digest(PATTERN.encode()), "mode": "ordinary"}

    def count(self, text):
        require(isinstance(text, str), "token counter requires text")
        require(len(text.encode()) <= 8 * 1024 * 1024, "counter input limit")
        return sum(self._cached_piece(raw) if len(raw) <= 4096 else self._piece(raw)
                   for raw in (match.group().encode() for match in self.pattern.finditer(text)))

    @lru_cache(maxsize=4096)
    def _cached_piece(self, raw):
        return self._piece(raw)

    def _piece(self, raw):
        if raw in self.ranks:
            return 1
        parts = [bytes([byte]) for byte in raw]
        length = len(parts)
        previous, following = list(range(-1, length - 1)), list(range(1, length + 1))
        generation = [0] * length
        alive = [True] * length
        queue = []

        def push(left):
            if left < 0 or not alive[left]:
                return
            right = following[left]
            if right >= length:
                return
            rank = self.ranks.get(parts[left] + parts[right])
            if rank is not None:
                heapq.heappush(queue, (rank, left, generation[left], right, generation[right]))

        for left in range(length - 1):
            push(left)
        count = length
        while queue:
            _, left, left_generation, right, right_generation = heapq.heappop(queue)
            if not alive[left] or not alive[right] or following[left] != right or generation[left] != left_generation or generation[right] != right_generation:
                continue
            parts[left] += parts[right]
            alive[right] = False
            generation[left] += 1
            following[left] = following[right]
            if following[right] < length:
                previous[following[right]] = left
            count -= 1
            push(previous[left])
            push(left)
        return count
