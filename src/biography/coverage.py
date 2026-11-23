"""Conservative source coverage; this is not a semantic fact-recall metric."""

import re

STOP = {
    "я",
    "мой",
    "моя",
    "мои",
    "меня",
    "мне",
    "наш",
    "наша",
    "он",
    "она",
    "они",
    "мы",
    "вы",
    "и",
    "а",
    "но",
    "в",
    "во",
    "на",
    "с",
    "со",
    "к",
    "от",
    "до",
    "за",
    "из",
    "у",
    "по",
    "это",
    "потом",
    "году",
    "год",
    "года",
    "же",
    "так",
    "ну",
}
KIN = {"отец", "мать", "брат", "сестра", "сын", "дочь", "муж", "жена", "коллега"}


def uncovered_sources(segments, data, respondents_only=False):
    evidence = {}
    for fact in data.facts:
        for e in fact.evidence:
            evidence.setdefault(e.segment_id, []).append(e.quote)
    for person in data.people:
        for m in person.mentions:
            evidence.setdefault(m.segment_id, []).append(m.text)
    result = []
    for s in segments:
        if s.speaker_role.value == "interviewer":
            continue
        if respondents_only and s.speaker_role.value != "respondent":
            continue
        spans = []
        for quote in evidence.get(s.segment_id, []):
            start = s.text_raw.find(quote)
            if start >= 0:
                spans.append((start, start + len(quote)))
        meaningful = []
        for token in re.finditer(r"\w+", s.text_raw):
            word = token.group().casefold()
            if word in STOP or (len(word) < 3 and word != "не"):
                continue
            if not any(a <= token.start() and token.end() <= b for a, b in spans):
                meaningful.append(word)
        if len(meaningful) >= 2 or any(
            w in KIN or any(ch.isdigit() for ch in w) for w in meaningful
        ):
            result.append(s)
    return result
