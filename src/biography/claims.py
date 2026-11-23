"""Conservative normalization of explicitly cited, competing single-valued claims."""

import re
from collections import defaultdict

from .schemas import Conflict, DateInfo


def preserve_competing_claims(data):
    result = data.model_copy(deep=True)
    expanded = []
    for fact in result.facts:
        variants = []
        if fact.predicate == "birth_year" and len(fact.evidence) > 1:
            for evidence in fact.evidence:
                text = evidence.quote.casefold()
                years = re.findall(r"(?<!\d)[12]\d{3}(?!\d)", text)
                if (
                    len(years) == 1
                    and sum(text.count(root) for root in ("родил", "рожд")) == 1
                ):
                    variants.append((years[0], evidence))
        if len({year for year, e in variants}) >= 2:
            for index, (year, evidence) in enumerate(variants, 1):
                f = fact.model_copy(deep=True)
                f.fact_id = fact.fact_id + f"_variant_{index}"
                f.value = year
                f.evidence = [evidence]
                f.requires_review = True
                text = evidence.quote.casefold()
                approximate = (
                    any(
                        x in text
                        for x in (
                            "примерно",
                            "возможно",
                            "около",
                            "не помню",
                            "приблизительно",
                        )
                    )
                    or fact.certainty != "explicit"
                )
                match = re.search(
                    r"(?:примерно\s+|около\s+|приблизительно\s+)?(?:в\s+)?"
                    + year
                    + r"(?:\s+году)?",
                    evidence.quote,
                    flags=re.I,
                )
                value = match.group() if match else year
                if "возможно" in text:
                    value = "возможно, " + value
                f.date = DateInfo(
                    value=value, precision="year", approximate=approximate
                )
                f.certainty = "approximate" if approximate else "explicit"
                expanded.append(f)
            result.uncertainties.append(
                "Сохранены отдельные версии года рождения из разных цитат: "
                + fact.fact_id
            )
        else:
            expanded.append(fact)
    # Only comparable attributes of the same identified person establish this automatic flag.
    grouped = defaultdict(list)
    for fact in expanded:
        fact.has_conflict = False
        fact.conflict_group_id = None
        if fact.subject_person_id and fact.predicate in {
            "birth_year",
            "date_of_birth",
            "place_of_birth",
            "full_name",
        }:
            grouped[(fact.subject_person_id, fact.predicate)].append(fact)
    conflicts = []
    for (person, predicate), facts in grouped.items():
        if len({f.value.casefold().strip() for f in facts}) < 2:
            continue
        group = Conflict(
            conflict_group_id=f"conflict_normalized_{len(conflicts) + 1:04d}",
            fact_ids=[f.fact_id for f in facts],
            explanation="Возможное расхождение сведений об одном лице; необходимо проверить обе версии по источникам.",
        )
        conflicts.append(group)
        for f in facts:
            f.has_conflict = True
            f.conflict_group_id = group.conflict_group_id
            f.requires_review = True
    if result.conflicts and not conflicts:
        result.uncertainties.append(
            "Модель заявила противоречие, но сравнимые утверждения одного лица не установлены. Требуется ручная проверка."
        )
    result.facts = expanded
    result.conflicts = conflicts
    return result
