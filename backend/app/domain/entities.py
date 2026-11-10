import re

from pydantic import BaseModel, ConfigDict

# Fictional retailer format; this is a candidate format, not existence/ownership proof.
ORDER_PATTERN = re.compile(r"(?<![A-Za-z0-9])ST\d{12}(?![A-Za-z0-9])", re.IGNORECASE)


class OrderCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    value: str
    start: int
    end: int
    verified: bool = False


def extract_order_candidates(text: str) -> list[OrderCandidate]:
    return [
        OrderCandidate(value=m.group().upper(), start=m.start(), end=m.end())
        for m in ORDER_PATTERN.finditer(text)
    ]
