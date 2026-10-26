# Metadata-aware question answering — ground-truth harness

Asks a fixed question set on a hosted collection and records, per question,
what the answer was built from: every RAG tool call with its raw arguments
(from the turn's LangSmith trace), the filter the executor applied, document
counts, `total_match_count`, completeness, chunk calls and the sources shown.
The answer is then graded against an expected answer computed from the
collection's metadata (two LLM judge passes; disagreement is reported).

```sh
# 1. expected answers, computed from ground truth + the hosted extraction
python questions.py --ground-truth ../../backend/samples/resumes/ground_truth.json \
    --extracted <extracted.json> --out qset.json
# 2. ask (fresh session per question)
DI_API_KEY_FILE=... LANGSMITH_KEY_FILE=... DI_OWNER_ID=<owner email> python run_hosted.py \
    --questions qset.json --knowledge-id <collection id> --out run.json
# 3. grade
OPENAI_KEY_FILE=... python grade.py --questions qset.json --run run.json --out graded.json
```

Structured questions are graded against the EXTRACTED metadata (the population
the answering layer can see); where the resume ground truth differs, the
question carries `truth_differs` so an extraction error is not reported as a
QA failure. Text questions (`passage`, `multi_doc_synthesis`, `hybrid`) carry
hand-written facts from the source resumes.

Keys are read from files and never printed. The deterministic regressions for
the defects this harness found live in `tests/unit/test_metadata_population.py`.
