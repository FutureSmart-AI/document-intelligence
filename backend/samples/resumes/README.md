# Synthetic resume sample collection

24 fictional resumes for the Document Intelligence "try it with sample data" experience: sign in, open the
sample collection, see each candidate's extracted fields in a table, ask questions with sources, and see charts.

**Everything here is invented.** No real person, employer or school appears. Emails use the reserved
`example.com` / `example.org` / `example.net` domains, and the three phone numbers use ranges reserved for fiction
(US 555-01xx, UK 07700 900xxx). Certifications and technologies (AWS, Kubernetes, CKA...) are real names because a
resume without them is not realistic. Do **not** replace these files with real resumes: the eval resumes in
`fs-agent-workers/testing/eval_data` and the internal resume test collections belong to real people.

## Files

| Path | What it is |
|---|---|
| `pdf/rNN-<name>.pdf` | The 24 resumes, one A4 page each. **This is what gets ingested.** |
| `html/rNN-<name>.html` | The rendered source of each PDF (six layouts, see below). |
| `source/candidates.py` | The single source of truth: printed content plus the ground-truth judgements. |
| `ground_truth.json` | Per resume, the exact values the `resume` field template should extract. |
| `questions.json` | 12 suggested questions with answers computed from `ground_truth.json`. |
| `build.py` | Regenerates html/, pdf/ (headless Chrome), ground_truth.json and questions.json. |
| `verify_hosted.py` | Creates a collection on a hosted service, ingests the PDFs, reads extractions, asks the questions. |
| `score.py` | Scores a hosted run against the ground truth (exact for mechanical fields, LLM judge for the rest). |
| `hosted_run/` | The committed results of the hosted verification run (state, extractions, answers, scores). |
| `VERIFICATION.md` | The hosted verification tables and the gaps found. |

## `ground_truth.json`

```json
{
  "meta": {"reference_date": "2026-10", "template": "resume", "fields": ["candidate_name", "..."], "count": 24},
  "resumes": [
    {
      "id": "r01",
      "file": "pdf/r01-aarav-mehta.pdf",
      "layout": "sidebar_left",
      "fields": {
        "candidate_name": "Aarav Mehta",
        "candidate_email": "aarav.mehta@example.com",
        "current_role": "Senior Machine Learning Engineer",
        "seniority_level": "senior",
        "years_of_experience": 8,
        "skills": ["python", "pytorch", "aws", "..."],
        "education_level": "masters",
        "certifications": ["AWS Certified Machine Learning - Specialty"],
        "locations": ["Bengaluru"],
        "industries": ["e-commerce", "fintech"],
        "languages": ["english", "hindi", "kannada"],
        "work_experience": [{"company": "Lumora Commerce", "title": "...", "start_year": 2022, "end_year": null, "summary": "..."}],
        "education": [{"institution": "...", "degree": "M.Tech", "field_of_study": "Computer Science", "graduation_year": 2018}]
      },
      "alternatives": {},
      "notes": null
    }
  ]
}
```

`fields` holds exactly the 13 fields of the `resume` template (`backend/utils/config/enrichment_config.py`,
`DOMAIN_FIELD_TEMPLATES["resume"]`). `alternatives` lists other values a careful reader would also accept where
a resume is genuinely ambiguous (for example the career switcher's years of experience); `notes` says why.
Years of experience are as of October 2026.

## `questions.json`

Each question has `id`, `question`, `answer_source` (`structured_fields`, `document_text` or `hybrid`: where the
answer should come from), `why`, `expected` (computed from the ground truth by `build.py`), and `chart` when the
question asks for one. Use the `question` strings as the collection's suggested questions.

## The set at a glance

- Roles: data science, software (backend, frontend, principal), AI/ML engineering and research, MLOps, data
  engineering, analytics, product, DevOps/SRE, and one VP of Engineering.
- Seniority: 5 junior, 6 mid-level, 7 senior, 3 lead, 2 principal, 1 executive; 0 to 18 years of experience.
- Locations: Bengaluru, Pune, Hyderabad, Chennai, Mumbai, Delhi/Gurugram, Kochi, Kolkata, Ahmedabad, London, Madrid,
  Berlin, Munich, Tokyo, Singapore, Dubai, Austin, Toronto.
- Hard cases on purpose: a career switcher (teacher to data scientist, r08), a career break (r13), a gap year
  (r23), concurrent overlapping roles (r22), a fresher with only an internship (r10), an MBA break between
  engineering and product (r06), and two resumes whose years must be computed from dates (r03, r15).
- Layouts: `classic` (serif, centred header), `compact` (dense sans), `header_band` (coloured header),
  `academic` (education first), `sidebar_left` and `sidebar_right` (two-column).

## Rebuild

```bash
python build.py            # needs Chrome; CHROME=/path/to/chrome to override
python build.py --no-pdf   # html + json only
```

## Provisioning a sample collection

Create the collection with the template's full field configuration so the allowed values for `seniority_level`
and `education_level` reach the extractor:

```
POST /v1                         {"owner_id": ..., "collection_name": ...}
PUT  /v1/{knowledge_id}/config   {"collection_name": ..., "service_description": ...,
                                  "field_template": "resume", "seed_optional_defaults": false}
POST /v1/{knowledge_id}/documents   multipart file=<each pdf>
```

That yields the 13 resume fields plus the protected `summary` and `document_type`. See `verify_hosted.py`.
