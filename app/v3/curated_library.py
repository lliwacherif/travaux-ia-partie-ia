"""Declarative curated V3 packs made exclusively from official V2 rows.

This module intentionally stores references, not copied labels or prices.  The
importer resolves every reference against the read-only V2 database and reports
missing or incompatible source material instead of fabricating replacements.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

SourceKind = Literal["PACK_LINE", "BPU"]


@dataclass(frozen=True, slots=True)
class SourceLineRef:
    """A stable reference to one official V2 pack or BPU line."""

    kind: SourceKind
    source_id: str
    pack_code: str | None = None

    @property
    def key(self) -> str:
        if self.kind == "PACK_LINE":
            return f"PACK_LINE:{self.pack_code}:{self.source_id}"
        return f"BPU:{self.source_id}"


@dataclass(frozen=True, slots=True)
class CuratedPackSpec:
    pack_code: str
    title: str
    trade_label: str
    setup: tuple[SourceLineRef, ...]
    core: tuple[SourceLineRef, ...]
    finish: tuple[SourceLineRef, ...]
    exclusion_tags: tuple[str, ...] = ()
    required_coverage: tuple[str, ...] = ()

    @property
    def all_lines(self) -> tuple[SourceLineRef, ...]:
        return self.setup + self.core + self.finish


def pack_line(pack_code: str, line_code: str) -> SourceLineRef:
    return SourceLineRef("PACK_LINE", line_code, pack_code)


def bpu(item_id: str) -> SourceLineRef:
    return SourceLineRef("BPU", item_id)


METAL_STRUCTURE_COMPLETE = CuratedPackSpec(
    pack_code="V3-MET-STRUCTURE-COMPLETE",
    title="Structure métallique complète",
    trade_label="Charpente métallique",
    setup=tuple(
        pack_line("MET-PACK-001", code)
        for code in ("CM-001", "CM-002", "CM-003")
    ),
    core=tuple(
        bpu(f"BIBLIO-{number}")
        for number in (
            "02636",
            "02637",
            "02638",
            "02641",
            "02642",
            "02643",
            "02644",
            "02645",
            "02646",
            "02648",
            "02649",
            "02650",
            "02680",
            "02681",
        )
    ),
    finish=tuple(
        bpu(f"BIBLIO-{number}") for number in ("02678", "02679", "02684")
    ),
    exclusion_tags=("foundations", "bardage"),
    required_coverage=(
        "études d'exécution",
        "fabrication en atelier",
        "poteaux",
        "poutres",
        "pannes",
        "contreventements",
        "transport",
        "levage",
        "assemblage",
        "protection anticorrosion",
    ),
)


CHARPENTE_BOIS_EXTENSION = CuratedPackSpec(
    pack_code="V3-CHARPENTE-BOIS-EXTENSION",
    title="Charpente bois traditionnelle pour extension",
    trade_label="Charpente bois – Ossature",
    setup=tuple(
        pack_line("CHAR-PACK-001", code)
        for code in ("CHB-001", "CHB-002", "CHB-003")
    ),
    core=(
        # Official source-pack lines: the structural members and controls.
        *(pack_line("CHAR-PACK-001", f"CHB-{number:03d}") for number in range(11, 18)),
        # Official BPU rows: explicit farms, treatment, assembly, and lifting.
        bpu("CHA-15-FOU"),
        bpu("CHA-15-POS"),
        bpu("CHA-02-FOU"),
        bpu("CHA-02-POS"),
        bpu("CHA-16-FOU"),
        bpu("CHA-16-POS"),
        bpu("BIBLIO-00002"),
    ),
    finish=tuple(
        pack_line("CHAR-PACK-001", code)
        for code in ("CHB-018", "CHB-019", "CHB-020")
    ),
    exclusion_tags=("depose", "demontage"),
    required_coverage=(
        "fermes",
        "pannes",
        "chevrons",
        "contreventement",
        "bois traité",
        "assemblage",
        "levage",
        "fixation",
        "murs porteurs",
        "support pour couverture en tuiles",
    ),
)


CUISINE_RENOVATION_DROITE = CuratedPackSpec(
    pack_code="V3-CUIS-RENOVATION-DROITE",
    title="Rénovation cuisine droite",
    trade_label="Cuisine",
    setup=tuple(
        pack_line("CUIS-PACK-002", code)
        for code in ("CUS-001", "CUS-002", "CUS-003")
    ),
    core=tuple(
        pack_line("CUIS-PACK-002", f"CUS-{number:03d}")
        for number in range(4, 18)
    ),
    finish=tuple(
        pack_line("CUIS-PACK-002", code)
        for code in ("CUS-018", "CUS-019", "CUS-020")
    ),
    exclusion_tags=("electromenager", "carrelage_sol"),
    required_coverage=(
        "dépose meubles",
        "meubles bas",
        "meubles hauts",
        "plan de travail",
        "plinthes",
        "étanchéité",
        "nettoyage",
    ),
)


CURATED_PACKS = (
    METAL_STRUCTURE_COMPLETE,
    CHARPENTE_BOIS_EXTENSION,
    CUISINE_RENOVATION_DROITE,
)

# Captured from the 904-row V2 source catalog.  It is deliberately a compact
# summary so unit tests can verify migration policy without a real database.
V2_PACK_GEOMETRY_SUMMARY = {
    ("TRAVAUX", 19): 4,
    ("TRAVAUX", 20): 591,
    ("TRAVAUX", 21): 33,
    ("DEPANNAGE", 8): 276,
}


__all__ = [
    "CHARPENTE_BOIS_EXTENSION",
    "CUISINE_RENOVATION_DROITE",
    "CURATED_PACKS",
    "CuratedPackSpec",
    "METAL_STRUCTURE_COMPLETE",
    "SourceLineRef",
    "V2_PACK_GEOMETRY_SUMMARY",
]
