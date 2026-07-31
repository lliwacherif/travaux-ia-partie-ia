"""Import the official V2 catalog into the isolated, versioned V3 library.

The importer never updates V2.  Compatible 20-line Travaux packs can be
copied as DRAFT records for later review.  Only explicitly curated packs may
be published, and publication requires embeddings, a human UUID and a passed
regression flag.
"""

from __future__ import annotations

import argparse
import hashlib
import re
import unicodedata
import uuid
from collections.abc import Iterable
from datetime import date, datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

from openai import OpenAI
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session

from app.core.config import settings
from app.v3.curated_library import CURATED_PACKS, CuratedPackSpec, SourceLineRef
from app.v3.models import (
    PriceVersion,
    QuotePack,
    QuotePackLine,
    TechnicalDependency,
    TradeCatalog,
    VatRule,
)
from app.v3.publication import (
    fallback_coverage_for,
    issues_as_dicts,
    load_dependencies,
    load_pack_snapshots,
    validate_publication,
)
from app.v3.ssot import EMBEDDING_MODEL, Flow
from app.v3.trace import stable_hash

LIBRARY_VERSION = "LIB-V3.1-2026-07-31.1"
NAMESPACE = uuid.UUID("d46a21c0-6d12-4d7a-96c6-2b50a8f01465")


def _uuid(kind: str, key: str) -> uuid.UUID:
    return uuid.uuid5(NAMESPACE, f"{kind}:{key}")


def _fold(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value.casefold())
    return " ".join(
        re.sub(
            r"[^\w]+",
            " ",
            "".join(ch for ch in decomposed if not unicodedata.combining(ch)),
        ).split()
    )


def _trade_code(label: str) -> str:
    explicit = {
        "charpente metallique": "CHARPENTE_METALLIQUE",
        "charpente bois ossature": "CHARPENTE_BOIS",
        "charpente bois": "CHARPENTE_BOIS",
        "cuisine": "CUISINE",
    }
    folded = _fold(label)
    if folded in explicit:
        return explicit[folded]
    return re.sub(r"[^A-Z0-9]+", "_", folded.upper()).strip("_")[:100]


def _unit(value: str) -> str:
    folded = _fold(value)
    aliases = {
        "m2": "M2",
        "m": "M2" if "²" in value else "ML",
        "ml": "ML",
        "metre lineaire": "ML",
        "metres lineaires": "ML",
        "m3": "M3",
        "u": "UNIT",
        "unite": "UNIT",
        "unites": "UNIT",
        "forfait": "FORFAIT",
        "h": "HOUR",
        "heure": "HOUR",
        "jour": "DAY",
        "tonne": "TONNE",
        "tonnes": "TONNE",
        "t": "TONNE",
    }
    if "m²" in value.casefold():
        return "M2"
    if "m³" in value.casefold():
        return "M3"
    resolved = aliases.get(folded)
    if resolved is None:
        raise ValueError(f"UNSUPPORTED_OFFICIAL_UNIT:{value}")
    return resolved


def _source_line(connection: Any, ref: SourceLineRef) -> dict[str, Any]:
    if ref.kind == "BPU":
        row = connection.execute(
            text(
                """
                SELECT id AS source_id, corps_metier, designation,
                       prix_unitaire_ht, unite, taux_tva_defaut, description
                FROM bpu_items
                WHERE id = :source_id
                """
            ),
            {"source_id": ref.source_id},
        ).mappings().one_or_none()
        if row is None:
            raise ValueError(f"OFFICIAL_BPU_LINE_MISSING:{ref.source_id}")
        return dict(row)

    row = connection.execute(
        text(
            """
            SELECT line.value->>'code' AS source_id,
                   pack.corps_metier,
                   line.value->>'designation' AS designation,
                   (line.value->>'prix_unitaire_ht')::numeric AS prix_unitaire_ht,
                   line.value->>'unite' AS unite,
                   COALESCE(
                       (line.value->>'taux_tva_defaut')::numeric, 20
                   ) AS taux_tva_defaut,
                   pack.description
            FROM packs_travaux AS pack
            CROSS JOIN LATERAL jsonb_array_elements(pack.pack_json) AS line(value)
            WHERE pack.code_pack = :pack_code
              AND line.value->>'code' = :source_id
            """
        ),
        {"pack_code": ref.pack_code, "source_id": ref.source_id},
    ).mappings().one_or_none()
    if row is None:
        raise ValueError(
            f"OFFICIAL_PACK_LINE_MISSING:{ref.pack_code}:{ref.source_id}"
        )
    return dict(row)


_CONCEPTS: tuple[tuple[tuple[str, ...], str, str], ...] = (
    (("etude", "plans de fabrication"), "concevoir", "étude d'exécution"),
    (("fabrication en atelier",), "fabriquer", "fabrication en atelier"),
    (("anticorrosion", "antirouille"), "protéger", "protection anticorrosion"),
    (("transport", "livraison"), "transporter", "éléments de charpente"),
    (("levage", "grue", "nacelle"), "lever", "éléments de charpente"),
    (("poteau",), "fournir et poser", "poteaux"),
    (("poutre",), "fournir et poser", "poutres principales"),
    (("panne",), "fournir et poser", "pannes"),
    (("chevron",), "fournir et poser", "chevrons"),
    (("contrevent",), "fournir et poser", "contreventements"),
    (("platine", "ancrage"), "fournir et poser", "platines d'ancrage"),
    (("assemblage", "boulonn"), "assembler", "structure"),
    (("reglage", "calage", "mise a niveau"), "régler", "structure"),
    (("ferme",), "fabriquer et poser", "fermes"),
    (("traitement preventif", "insecticide", "fongicide"), "traiter", "pièces de charpente"),
    (("murs porteurs", "fixation"), "fixer", "charpente sur murs porteurs"),
    (("support pour couverture", "liteaux", "voliges"), "préparer", "support de couverture en tuiles"),
    (("controle", "verification", "essais"), "contrôler", "structure"),
    (("nettoyage", "evacuation"), "nettoyer", "chantier"),
    (("implantation", "tracage"), "implanter", "ouvrage"),
    (("protection de chantier", "balisage", "protection des sols"), "préparer", "chantier"),
    (("depose", "dépose"), "déposer", "ancienne cuisine"),
    (("meuble bas", "meubles bas"), "fournir et poser", "meubles bas"),
    (("meuble haut", "meubles hauts"), "fournir et poser", "meubles hauts"),
    (("plan de travail",), "fournir et poser", "plan de travail"),
    (("plinthe",), "fournir et poser", "plinthes"),
    (("silicone", "etancheite", "étanchéité"), "étanchéifier", "joints"),
    (("evier", "robinet", "robinetterie"), "fournir et poser", "sanitaires cuisine"),
    (("credence", "crédence"), "fournir et poser", "crédence"),
)


def _semantic_metadata(
    designation: str,
    *,
    phase: str,
    trade_code: str,
) -> tuple[str, str, str, list[str], str]:
    folded = _fold(designation)
    matches: list[tuple[str, str]] = []
    for terms, action, object_family in _CONCEPTS:
        if any(term in folded for term in terms):
            matches.append((action, object_family))
    if matches:
        action, object_family = matches[0]
    elif phase == "SETUP":
        action, object_family = "préparer", "chantier"
    elif phase == "FINISH":
        action, object_family = "finaliser", "ouvrage"
    else:
        action, object_family = "réaliser", designation
    if trade_code == "CHARPENTE_METALLIQUE":
        material = "acier"
    elif trade_code == "CHARPENTE_BOIS":
        material = "bois"
    elif trade_code == "CUISINE":
        material = "agencement"
    else:
        material = "standard"
    capabilities = {
            action,
            object_family,
            material,
            *(
                object_
                for _action, object_ in matches
            ),
            *(
                matched_action
                for matched_action, _object in matches
            ),
            *(
                token
                for token in (
                    "poteaux",
                    "poutres principales",
                    "pannes",
                    "chevrons",
                    "contreventements",
                    "platines d'ancrage",
                    "fermes",
                    "levage",
                    "assemblage",
                    "protection anticorrosion",
                    "transport",
                    "réglage",
                    "bois traité",
                )
                if _fold(token) in folded
            ),
        }
    if "fabrication" in folded:
        capabilities.update({"fabriquer", "fabrication en atelier"})
    if "charpente traditionnelle" in folded:
        capabilities.update({"construire", "charpente traditionnelle"})
    if "charpente metallique" in folded or "structure metallique" in folded:
        capabilities.update({"charpente métallique", "structure métallique"})
    if "charpente" in folded and trade_code == "CHARPENTE_BOIS":
        capabilities.update({"charpente", "charpente traditionnelle"})
    if "couverture" in folded or "liteau" in folded or "volige" in folded:
        capabilities.update(
            {"préparer", "support de couverture en tuiles"}
        )
    if trade_code == "CHARPENTE_METALLIQUE":
        capabilities.update({"charpente métallique", "structure métallique"})
    if trade_code == "CHARPENTE_BOIS":
        capabilities.update(
            {"charpente", "charpente traditionnelle", "pièces de charpente"}
        )
    tags = sorted(capabilities)
    replacement_group = f"{trade_code}:{_fold(object_family).replace(' ', '_')}"
    return action, object_family, material, tags, replacement_group


def _quantity_metadata(unit: str) -> tuple[str, str | None, str | None, dict[str, Any] | None]:
    if unit == "M2":
        return "PROJECT:global_area_m2", None, None, None
    if unit == "ML":
        return (
            "PACK_DEFAULT",
            "EXPLICIT",
            "V2_EXPLICIT_LENGTH_V1",
            {},
        )
    return "PACK_DEFAULT", None, None, None


def _embed(client: OpenAI, values: Iterable[str]) -> list[list[float]]:
    texts = list(values)
    response = client.embeddings.create(
        model=settings.V3_OPENAI_EMBEDDING_MODEL,
        input=texts,
        dimensions=1536,
    )
    return [list(item.embedding) for item in response.data]


def _upsert_vat_rules(
    session: Session,
    now: datetime,
    *,
    publish: bool,
) -> None:
    definitions = (
        ("FR_STANDARD_20", Decimal("20"), "TVA normale France"),
        ("FR_CATALOG_10", Decimal("10"), "TVA rénovation catalogue"),
        ("FR_CATALOG_5_5", Decimal("5.5"), "TVA rénovation énergétique"),
        ("FR_CATALOG_0", Decimal("0"), "TVA exonérée"),
    )
    for rule_id, rate, label in definitions:
        record = session.get(VatRule, (rule_id, 1))
        if record is None:
            record = VatRule(vat_rule_id=rule_id, version=1)
            session.add(record)
        record.country = "FR"
        record.label = label
        record.rate = rate
        record.applicability_rule = (
            {"fallback": True}
            if rule_id == "FR_STANDARD_20"
            else {"catalog_rate": float(rate)}
        )
        record.effective_from = date(2025, 1, 1)
        record.effective_to = None
        record.content_hash = stable_hash(
            {"id": rule_id, "version": 1, "rate": str(rate)}
        )
        if publish:
            record.status = "PUBLISHED"
            record.published_at = now
        elif record.status != "PUBLISHED":
            record.status = "DRAFT"
            record.published_at = None


def _vat_rule_id(rate: Decimal) -> str:
    if rate == Decimal("20"):
        return "FR_STANDARD_20"
    if rate == Decimal("10"):
        return "FR_CATALOG_10"
    if rate == Decimal("5.5"):
        return "FR_CATALOG_5_5"
    if rate == Decimal("0"):
        return "FR_CATALOG_0"
    return "FR_STANDARD_20"


def _import_curated_pack(
    *,
    v2_connection: Any,
    session: Session,
    embeddings: OpenAI,
    spec: CuratedPackSpec,
    publish: bool,
    approved_by: uuid.UUID | None,
    regression_passed: bool,
    now: datetime,
) -> uuid.UUID:
    trade_code = _trade_code(spec.trade_label)
    pack_id = _uuid("pack", f"{LIBRARY_VERSION}:{spec.pack_code}")
    source_lines: list[tuple[str, int, SourceLineRef, dict[str, Any]]] = []
    for phase, references in (
        ("SETUP", spec.setup),
        ("CORE", spec.core),
        ("FINISH", spec.finish),
    ):
        for slot_index, reference in enumerate(references):
            source_lines.append(
                (phase, slot_index, reference, _source_line(v2_connection, reference))
            )

    line_texts = [
        " ".join(
            (
                source["designation"],
                source.get("description") or "",
                spec.title,
                spec.trade_label,
            )
        )
        for _phase, _slot, _ref, source in source_lines
    ]
    pack_text = " ".join(
        (spec.title, spec.trade_label, *spec.required_coverage, *line_texts)
    )
    vectors = _embed(embeddings, [pack_text, *line_texts])

    trade = session.get(TradeCatalog, trade_code)
    if trade is None:
        trade = TradeCatalog(trade_code=trade_code)
        session.add(trade)
    trade.flow = Flow.TRAVAUX.value
    trade.label = spec.trade_label
    trade.active = True
    trade.version = 1
    trade.catalog_version = LIBRARY_VERSION
    trade.status = "PUBLISHED" if publish else "DRAFT"
    trade.content_hash = stable_hash(
        {"code": trade_code, "label": spec.trade_label, "flow": "TRAVAUX"}
    )
    trade.published_at = now if publish else None
    session.flush()

    pack = session.get(QuotePack, pack_id)
    if pack is None:
        pack = QuotePack(pack_id=pack_id)
        session.add(pack)
    pack.pack_code = spec.pack_code
    pack.flow = Flow.TRAVAUX.value
    pack.trade_code = trade_code
    pack.service_code = None
    pack.title = spec.title
    pack.version = 1
    pack.library_version = LIBRARY_VERSION
    pack.status = "PUBLISHED" if publish else "DRAFT"
    pack.searchable_text = pack_text
    pack.embedding = vectors[0]
    pack.embedding_model = EMBEDDING_MODEL
    pack.exclusion_tags = list(spec.exclusion_tags)
    pack.required_coverage = list(spec.required_coverage)
    pack.fallback_rank = 1
    pack.content_hash = stable_hash(
        {"code": spec.pack_code, "sources": [ref.key for ref in spec.all_lines]}
    )
    pack.source_hash = stable_hash([ref.key for ref in spec.all_lines])
    pack.approved_by = approved_by if publish else None
    pack.regression_passed = regression_passed if publish else False
    pack.publication_evidence = (
        {
            "approved_by": str(approved_by),
            "regression_passed": regression_passed,
            "embedding_model": EMBEDDING_MODEL,
            "source_count": len(spec.all_lines),
            "published_at": now.isoformat(),
        }
        if publish
        else {}
    )
    pack.published_at = now if publish else None
    session.flush()

    for vector, (phase, slot_index, reference, source) in zip(
        vectors[1:], source_lines, strict=True
    ):
        source_key = reference.key
        line_id = _uuid("line", f"{spec.pack_code}:{phase}:{slot_index}:{source_key}")
        price_id = _uuid("price", source_key)
        unit = _unit(str(source["unite"]))
        price_cents = int(
            (Decimal(str(source["prix_unitaire_ht"])) * Decimal("100")).quantize(
                Decimal("1"), rounding=ROUND_HALF_UP
            )
        )
        vat_rate = Decimal(str(source.get("taux_tva_defaut") or 20))
        vat_rule_id = _vat_rule_id(vat_rate)
        action, object_family, material, tags, replacement_group = _semantic_metadata(
            str(source["designation"]),
            phase=phase,
            trade_code=trade_code,
        )
        quantity_rule, linear_mode, linear_formula, linear_params = (
            _quantity_metadata(unit)
        )
        dependency_id = f"DEP:{spec.pack_code}:{phase}:{slot_index:02d}"

        price = session.get(PriceVersion, (price_id, 1))
        if price is None:
            price = PriceVersion(price_id=price_id, version=1)
            session.add(price)
        price.price_code = f"PRICE:{source_key}"[:150]
        price.unit = unit
        price.unit_price_cents = price_cents
        price.currency = "EUR"
        price.effective_from = date(2025, 1, 1)
        price.effective_to = None
        price.source_ref = source_key
        price.library_version = LIBRARY_VERSION
        price.status = "PUBLISHED" if publish else "DRAFT"
        price.content_hash = stable_hash(
            {"source": source_key, "unit": unit, "cents": price_cents}
        )
        price.published_at = now if publish else None

        dependency = session.get(TechnicalDependency, (dependency_id, 1))
        if dependency is None:
            dependency = TechnicalDependency(
                dependency_id=dependency_id,
                version=1,
            )
            session.add(dependency)
        dependency.trade_code = trade_code
        dependency.label = f"Dépendance technique officielle — {source['designation']}"
        dependency.applicability_rule = {
            "pack_code": spec.pack_code,
            "phase": phase,
            "source_ref": source_key,
        }
        dependency.active = True
        dependency.status = "PUBLISHED" if publish else "DRAFT"
        dependency.content_hash = stable_hash(dependency.applicability_rule)
        dependency.published_at = now if publish else None

        line = session.get(QuotePackLine, line_id)
        if line is None:
            line = QuotePackLine(line_id=line_id)
            session.add(line)
        line.pack_id = pack_id
        line.version = 1
        line.library_version = LIBRARY_VERSION
        line.phase = phase
        line.slot_index = slot_index
        line.designation = str(source["designation"])
        line.normalized_action = action
        line.object_family = object_family
        line.material_family = material
        line.searchable_text = " ".join(
            (line.designation, action, object_family, material, *tags)
        )
        line.embedding = vector
        line.embedding_model = EMBEDDING_MODEL
        line.synonym_tags = tags
        line.capability_tags = tags
        line.exclusion_tags = list(spec.exclusion_tags)
        line.technical_dependency_ids = [dependency_id]
        line.unit = unit
        line.quantity_rule = quantity_rule
        line.linear_measurement_mode = linear_mode
        line.linear_formula_id = linear_formula
        line.linear_params = linear_params
        line.quantity_precision = 3
        line.rounding_step = None
        line.default_quantity = Decimal("1")
        line.price_id = price_id
        line.price_version = 1
        line.vat_rule_id = vat_rule_id
        line.vat_rule_version = 1
        line.replacement_group = replacement_group if phase == "CORE" else None
        line.replaceable = phase == "CORE"
        line.active = True
        line.status = "PUBLISHED" if publish else "DRAFT"
        line.content_hash = stable_hash(
            {
                "source": source_key,
                "phase": phase,
                "slot": slot_index,
                "designation": line.designation,
            }
        )
        line.published_at = now if publish else None

    trade.fallback_pack_id = pack_id
    session.flush()
    return pack_id


def import_library(
    *,
    publish_curated: bool,
    approved_by: uuid.UUID | None,
    regression_passed: bool,
) -> list[str]:
    if publish_curated and (approved_by is None or not regression_passed):
        raise ValueError(
            "Publishing requires --approved-by and --regression-passed"
        )
    api_key = settings.V3_OPENAI_API_KEY or settings.OPENAI_API_KEY
    if not api_key:
        raise ValueError("OpenAI key is required to create official embeddings")

    v2_engine = create_engine(str(settings.SYNC_DATABASE_URL), future=True)
    v3_engine = create_engine(str(settings.V3_SYNC_DATABASE_URL), future=True)
    embedding_client = OpenAI(api_key=api_key)
    now = datetime.now(timezone.utc)
    imported: list[str] = []
    try:
        with v2_engine.connect() as v2_connection, Session(v3_engine) as session:
            _upsert_vat_rules(session, now, publish=publish_curated)
            for spec in CURATED_PACKS:
                _import_curated_pack(
                    v2_connection=v2_connection,
                    session=session,
                    embeddings=embedding_client,
                    spec=spec,
                    publish=publish_curated,
                    approved_by=approved_by,
                    regression_passed=regression_passed,
                    now=now,
                )
                imported.append(spec.pack_code)
            session.flush()

            if publish_curated:
                connection = session.connection()
                snapshots = load_pack_snapshots(
                    connection,
                    library_version=LIBRARY_VERSION,
                    pack_codes=imported,
                )
                prices, vats = load_dependencies(connection, snapshots)
                fallback_coverage = fallback_coverage_for(connection, snapshots)
                failures: dict[str, list[dict[str, Any]]] = {}
                for snapshot in snapshots:
                    issues = validate_publication(
                        snapshot,
                        prices=prices,
                        vat_rules=vats,
                        fallback_coverage=fallback_coverage,
                        approved_by=approved_by,
                        regression_passed=regression_passed,
                    )
                    if issues:
                        failures[snapshot.pack_code] = issues_as_dicts(issues)
                if failures:
                    raise ValueError(f"PUBLICATION_GATE_FAILED:{failures}")
            session.commit()
    finally:
        v2_engine.dispose()
        v3_engine.dispose()
    return imported


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Import the official curated V3 library from read-only V2."
    )
    parser.add_argument("--publish-curated", action="store_true")
    parser.add_argument("--approved-by", type=uuid.UUID)
    parser.add_argument("--regression-passed", action="store_true")
    args = parser.parse_args()
    imported = import_library(
        publish_curated=args.publish_curated,
        approved_by=args.approved_by,
        regression_passed=args.regression_passed,
    )
    print(
        {
            "library_version": LIBRARY_VERSION,
            "imported": imported,
            "status": "PUBLISHED" if args.publish_curated else "DRAFT",
        }
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

