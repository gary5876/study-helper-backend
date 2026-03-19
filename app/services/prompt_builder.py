"""Builds Anthropic API prompts for each generation stage."""
from __future__ import annotations

import json


NOTES_SYSTEM = (
    "You are an expert study coach creating structured learning materials. "
    "Your output must be valid JSON only — no markdown fences, no extra text. "
    "Base everything strictly on the provided document. Do NOT invent or hallucinate concepts."
)

MCQ_SYSTEM = (
    "You are an expert educational assessment designer. "
    "Your output must be valid JSON only — no markdown fences, no extra text. "
    "Every question must test understanding, not mere recall. "
    "Distractors must be plausible, not obviously wrong."
)

FILL_SYSTEM = (
    "You are an expert at creating fill-in-the-blank study exercises. "
    "Your output must be valid JSON only — no markdown fences, no extra text. "
    "Blanks must target key terms from the document, not filler words."
)


def build_notes_prompt(document_text: str) -> tuple[str, str]:
    """Return (system_prompt, user_prompt) for study notes generation."""
    user_prompt = f"""Generate structured study notes from the document below.

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
) -> tuple[str, str]:
    """Return (system_prompt, user_prompt) for MCQ generation."""
    concept_list = json.dumps(
        [{"id": c["id"], "term": c["term"]} for c in notes_json.get("key_concepts", [])],
        ensure_ascii=False,
    )
    user_prompt = f"""Generate exactly {mcq_count} multiple-choice questions based on the document and study notes.

AVAILABLE CONCEPT IDs (use these for concept_id field):
{concept_list}

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
      "difficulty": "easy|medium|hard"
    }}
  ]
}}

DISTRIBUTION: ~30% easy, ~50% medium, ~20% hard
RULES:
- Each option must be distinct — no duplicate option text
- Explanation must reference specific content from the document
- Questions must test understanding, not just memorization
- Every question MUST have a concept_id from the list above

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
) -> tuple[str, str]:
    """Return (system_prompt, user_prompt) for fill-in-blank generation."""
    concept_list = json.dumps(
        [{"id": c["id"], "term": c["term"]} for c in notes_json.get("key_concepts", [])],
        ensure_ascii=False,
    )
    user_prompt = f"""Generate exactly {fill_count} fill-in-the-blank questions from the document.

AVAILABLE CONCEPT IDs:
{concept_list}

OUTPUT FORMAT (JSON only):
{{
  "questions": [
    {{
      "id": "<uuid>",
      "sentence_with_blank": "<full sentence with ___ for the blank>",
      "answer": "<exact word or phrase>",
      "acceptable_variants": ["<synonym or alternate phrasing>"],
      "hint": "<brief hint that doesn't give away the answer>",
      "concept_id": "<concept ID from list above>"
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
    mcq = max(10, min(30, section_count * 3))
    fill = max(8, min(20, section_count * 2))
    return mcq, fill
