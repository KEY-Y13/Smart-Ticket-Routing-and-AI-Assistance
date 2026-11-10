import hashlib
import math
import re
from collections import Counter


def digest(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def chunks(text, size=300, overlap=40):
    text = text.replace("\r\n", "\n").replace("\x00", "").strip()
    return [text[i : i + size] for i in range(0, len(text), size - overlap)]


def tokens(text):
    # Chinese character bigrams avoid relying on an unversioned segmentation dictionary.
    runs = re.findall(r"[\u4e00-\u9fff]+|[a-zA-Z0-9_]+", text.casefold())
    out = []
    for run in runs:
        out.extend(
            [run]
            if run.isascii() or len(run) == 1
            else [run[i : i + 2] for i in range(len(run) - 1)]
        )
    return out


def bm25(query, documents, k1=1.5, b=0.75):
    bags = [Counter(tokens(text)) for text in documents]
    lengths = [sum(bag.values()) for bag in bags]
    average = sum(lengths) / max(len(bags), 1)
    result = [0.0] * len(bags)
    for term in set(tokens(query)):
        frequency = sum(term in bag for bag in bags)
        inverse = math.log(1 + (len(bags) - frequency + 0.5) / (frequency + 0.5))
        for i, bag in enumerate(bags):
            tf = bag[term]
            if tf:
                result[i] += (
                    inverse * tf * (k1 + 1) / (tf + k1 * (1 - b + b * lengths[i] / max(average, 1)))
                )
    return result


def rrf(*rankings, k=60):
    scores = {}
    for ranking in rankings:
        for rank, item in enumerate(ranking, 1):
            scores[item] = scores.get(item, 0) + 1 / (k + rank)
    return sorted(scores, key=lambda item: (-scores[item], item))


INJECTION = re.compile(
    r"ignore\s+(all\s+)?(previous|system)|system\s*prompt|忽略.{0,8}(指令|规则)|泄露.{0,8}(密钥|密码)|<\|.*?\|>",
    re.I,
)


def suspicious(text):
    return bool(INJECTION.search(text))
