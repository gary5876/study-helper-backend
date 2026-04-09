"""Builds Anthropic API prompts for each generation stage."""
from __future__ import annotations

import json


_LANG_INSTRUCTION = {
    "ko": "모든 출력(term, definition, question, explanation, hint 등)은 반드시 한국어로 작성하세요. JSON 키 이름은 영어 그대로 유지하세요.",
    "en": "Write all output fields (term, definition, question, explanation, hint, etc.) in English.",
}

NOTES_SYSTEM = (
    "You are an expert study coach creating structured learning materials. "
    "Your output must be valid JSON only — no markdown fences, no extra text. "
    "Base everything strictly on the provided document. Do NOT invent or hallucinate concepts."
)

MCQ_SYSTEM = (
    "You are an expert educational assessment designer specializing in rigorous exam questions. "
    "Your output must be valid JSON only — no markdown fences, no extra text. "
    "Every question must genuinely test understanding, not mere recall. "
    "Distractors must be plausible and require careful reasoning to eliminate."
)

FILL_SYSTEM = (
    "You are an expert at creating fill-in-the-blank study exercises. "
    "Your output must be valid JSON only — no markdown fences, no extra text. "
    "Blanks must target key terms from the document, not filler words."
)

_MCQ_LEVEL_GUIDE = """\
LEVEL DEFINITIONS (assign level 1–5 to each question):
  Level 1 — Basic recall: reproduce a term, name, or isolated fact verbatim.
  Level 2 — Comprehension: explain or paraphrase a concept in your own words.
  Level 3 — Application (minimum exam level): apply a concept to a concrete scenario; simple inference.
  Level 4 — Analysis (standard exam): compare/contrast multiple concepts, identify causes, evaluate trade-offs.
  Level 5 — Synthesis/Critical (hard exam): design solutions, expose edge cases, integrate multiple concepts, argue for/against.

DISTRIBUTION: ~8% level-1, ~12% level-2, ~20% level-3, ~35% level-4, ~25% level-5
(Round to nearest whole question. Most questions should be level 4–5.)

QUESTION TYPE:
  "concept"     — tests theoretical knowledge: definitions, principles, mechanisms.
  "application" — tests practical use: problem-solving, calculation, scenario analysis.\
"""

_FILL_LEVEL_GUIDE = """\
LEVEL DEFINITIONS (assign level 1–5 to each question):
  Level 1 — Recall a single term or label directly stated in the document.
  Level 2 — Complete a definition or explanatory phrase.
  Level 3 — Fill a term that requires understanding the surrounding context.
  Level 4 — Fill a technical term embedded in a comparative or analytical sentence.
  Level 5 — Fill a term in a complex sentence requiring synthesis of multiple concepts.

DISTRIBUTION: ~10% level-1, ~15% level-2, ~25% level-3, ~35% level-4, ~15% level-5

QUESTION TYPE:
  "concept"     — the blank is a theoretical term or principle.
  "application" — the blank is a result, method, or outcome in a practical context.\
"""


def build_notes_prompt(document_text: str, lang: str = "ko") -> tuple[str, str]:
    """Return (system_prompt, user_prompt) for study notes generation."""
    lang_note = _LANG_INSTRUCTION.get(lang, _LANG_INSTRUCTION["ko"])
    user_prompt = f"""{lang_note}

Generate structured study notes from the document below.

OUTPUT FORMAT (JSON only):
{{
  "key_concepts": [
    {{"id": "<uuid>", "term": "<term>", "definition": "<precise def>", "importance": "high|medium|low"}}
  ],
  "sections": [
    {{"title": "<title>", "summary": "<2-3 sentence summary>", "bullets": ["<bullet>", ...]}}
  ],
  "glossary": [
    {{"term": "<term>", "brief_def": "<one-line definition>"}}
  ]
}}

RULES:
- importance "high" only for concepts that recur or are definitionally central
- definitions must be precise, not vague paraphrases
- bullets should be complete, standalone statements
- include 5-15 key_concepts, 1 section per document section, 5-20 glossary entries

DOCUMENT:
{document_text}
"""
    return NOTES_SYSTEM, user_prompt


def build_mcq_prompt(
    document_text: str,
    notes_json: dict,
    mcq_count: int,
    lang: str = "ko",
) -> tuple[str, str]:
    """Return (system_prompt, user_prompt) for MCQ generation."""
    concept_list = json.dumps(
        [{"id": c["id"], "term": c["term"]} for c in notes_json.get("key_concepts", [])],
        ensure_ascii=False,
    )
    lang_note = _LANG_INSTRUCTION.get(lang, _LANG_INSTRUCTION["ko"])
    user_prompt = f"""{lang_note}

Generate exactly {mcq_count} multiple-choice questions based on the document and study notes.

AVAILABLE CONCEPT IDs (use these for concept_id field):
{concept_list}

{_MCQ_LEVEL_GUIDE}

OUTPUT FORMAT (JSON only):
{{
  "questions": [
    {{
      "id": "<uuid>",
      "question": "<question ending with ?>",
      "options": {{"A": "<option>", "B": "<option>", "C": "<option>", "D": "<option>"}},
      "correct_answer": "A|B|C|D",
      "explanation": "<why the correct answer is right, referencing the document>",
      "concept_id": "<one of the concept IDs above>",
      "level": 1|2|3|4|5,
      "question_type": "concept|application"
    }}
  ]
}}

RULES:
- Each option must be distinct — no duplicate option text
- Explanation must reference specific content from the document
- Every question MUST have a concept_id from the list above
- Aim for the level distribution above; do NOT cluster everything at level 3

STUDY NOTES (for context):
{json.dumps(notes_json, ensure_ascii=False, indent=2)}

DOCUMENT:
{document_text}
"""
    return MCQ_SYSTEM, user_prompt


def build_fill_prompt(
    document_text: str,
    notes_json: dict,
    fill_count: int,
    lang: str = "ko",
) -> tuple[str, str]:
    """Return (system_prompt, user_prompt) for fill-in-blank generation."""
    concept_list = json.dumps(
        [{"id": c["id"], "term": c["term"]} for c in notes_json.get("key_concepts", [])],
        ensure_ascii=False,
    )
    lang_note = _LANG_INSTRUCTION.get(lang, _LANG_INSTRUCTION["ko"])
    user_prompt = f"""{lang_note}

Generate exactly {fill_count} fill-in-the-blank questions from the document.

AVAILABLE CONCEPT IDs:
{concept_list}

{_FILL_LEVEL_GUIDE}

OUTPUT FORMAT (JSON only):
{{
  "questions": [
    {{
      "id": "<uuid>",
      "sentence_with_blank": "<full sentence with ___ for the blank>",
      "answer": "<exact word or phrase>",
      "acceptable_variants": ["<synonym or alternate phrasing>"],
      "hint": "<brief hint that doesn't give away the answer>",
      "concept_id": "<concept ID from list above>",
      "level": 1|2|3|4|5,
      "question_type": "concept|application"
    }}
  ]
}}

RULES:
- Use exactly one ___ per sentence
- Blanks must be key terms (nouns, verbs, technical terms) — never articles or prepositions
- The answer must appear verbatim in the document
- acceptable_variants should include common synonyms that are also correct
- hint should be a category or definition clue, not the answer itself

STUDY NOTES (for context):
{json.dumps(notes_json, ensure_ascii=False, indent=2)}

DOCUMENT:
{document_text}
"""
    return FILL_SYSTEM, user_prompt


def calculate_question_counts(section_count: int) -> tuple[int, int]:
    """Return (mcq_count, fill_count) based on document size."""
    mcq = max(10, min(20, section_count * 3))
    fill = max(6, min(15, section_count * 2))
    return mcq, fill
