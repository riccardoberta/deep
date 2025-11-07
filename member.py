from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Optional


@dataclass
class Member:
    surname: str
    name: str
    scopus_id: str
    unit: Optional[str] = None
    unige_id: Optional[str] = None
    scopus: Optional[Dict[str, object]] = field(default=None, repr=False)
    unige: Optional[Dict[str, object]] = field(default=None, repr=False)
