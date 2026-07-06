"""山名模糊比對與山岳資料庫載入。"""

from __future__ import annotations

import json
import unicodedata
from dataclasses import dataclass, field
from importlib import resources
from typing import Any


def normalize(s: str) -> str:
    """全形/半形、ヶ/ケ/が岳 正規化 + 小寫化，供山名比對。"""
    s = unicodedata.normalize("NFKC", s).strip().lower()
    return s.replace("ヶ", "ケ").replace("が岳", "ケ岳").replace("ガ岳", "ケ岳")


@dataclass
class Mountain:
    raw: dict[str, Any]

    @property
    def id(self) -> str:
        return self.raw["id"]

    @property
    def name(self) -> str:
        return self.raw["name_ja"]

    @property
    def aliases(self) -> list[str]:
        return self.raw.get("aliases", [self.name])

    @property
    def elevation(self) -> int:
        return self.raw["elevation"]

    @property
    def lat(self) -> float:
        return self.raw["lat"]

    @property
    def lon(self) -> float:
        return self.raw["lon"]

    @property
    def difficulty(self) -> str:
        return self.raw.get("difficulty", "中級")

    @property
    def area_hint(self) -> str:
        return self.raw.get("area_hint", "")

    @property
    def maitabi_area_names(self) -> list[str]:
        return self.raw.get("maitabi", {}).get("area_names", [])

    @property
    def maitabi_title_keywords(self) -> list[str]:
        return self.raw.get("maitabi", {}).get("title_keywords", [])

    @property
    def trailheads(self) -> list[str]:
        return self.raw.get("trailheads", [])

    @property
    def yamap(self) -> dict[str, Any]:
        return self.raw.get("yamap", {})

    @property
    def itineraries(self) -> list[dict[str, Any]]:
        return self.raw.get("itineraries", [])

    @property
    def huts(self) -> list[dict[str, Any]]:
        return self.raw.get("huts", [])


@dataclass
class MountainDB:
    mountains: list[Mountain] = field(default_factory=list)

    @classmethod
    def load(cls) -> "MountainDB":
        text = (
            resources.files("yama.data").joinpath("mountains.json").read_text("utf-8")
        )
        data = json.loads(text)
        return cls(mountains=[Mountain(m) for m in data["mountains"]])

    def find(self, query: str) -> Mountain | None:
        """先精確比對 alias，再做雙向子字串比對。"""
        q = normalize(query)
        if not q:
            return None
        for m in self.mountains:
            if any(normalize(a) == q for a in m.aliases):
                return m
        for m in self.mountains:
            if any(q in normalize(a) or normalize(a) in q for a in m.aliases):
                return m
        return None
