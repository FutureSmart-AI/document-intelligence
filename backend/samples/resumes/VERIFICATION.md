# Hosted verification: synthetic resume sample

Run on **2026-10-25** against **app.futuresmart.ai** (ECS `fs-agent-knowledge:229`). The collection was created
under an internal FutureSmart account through the knowledge service API (`verify_hosted.py`) with the calls the product makes:
`POST /v1`, `PUT /config` with `field_template: "resume"` and `seed_optional_defaults: false`, one `POST /documents`
per PDF, and one `POST /chat` per question. The service's default chat model and chat config were used; charts are
on by default.

- Collection: **Sample: Candidate Resumes (synthetic)**, knowledge id `6377d5df-bc7a-4fbc-b95d-e1be00b890cd`,
  created 2026-10-25 11:14 IST. It stays in place as the synthetic test collection.
- Fields: the 13 `resume` fields plus the protected `summary` and `document_type` (15). The allowed values for
  `seniority_level` and `education_level` are stored (`restrict_to_choices: true`).
- Ingestion: 24/24 documents `completed`, none failed, all indexed 63 s after the last upload.
- Questions: **q01-q08 answered; q09-q12 were not run.** The account hit its daily chat limit
  (`429 quota_exceeded`, `max_messages_per_day` 51/50; resets 00:00 UTC). They are re-run after the reset and this
  file is updated.
- Scoring (`score.py`): `candidate_email`, `seniority_level`, `education_level` and `years_of_experience` are compared
  exactly (email lowercased, `alternatives` in ground_truth.json honoured). Every other field and every answer is judged
  on meaning by `gpt-5.4-mini` with structured output, **twice**. A field counts as correct only when both passes say
  so; disagreements are counted.

Raw evidence: `hosted_run/state.json` (ids), `hosted_run/extracted.json` (every document's metadata as the service
returned it), `hosted_run/answers.json` (every answer with its sources, chart output and session id),
`hosted_run/scores.json` (every judgement with its reason).

## Summary

**Extraction is strong.** Name, email, education level, locations, languages, education and skills are right on
24/24 resumes, across all six layouts including the two-column ones. Of 312 scored field values, the defects are:

- 2 years-of-experience miscounts;
- 1 seniority call;
- 1 dropped word in a certification;
- 1 wrong date range;
- 1 duplicated role;
- 1 null current role.

`industries` mostly over-lists rather than misses.

**Chat is the weak half.** Filter questions over one facet work: q01 8/8 and q07 5/5, both exact. Anything that
needs the whole collection does not:

- a count that does not add up (q04);
- a distribution over 10 of 24 people (q05);
- an answer that is the raw retrieved text, 22,555 characters with no answer in it (q06);
- a degree filter that misses 2 of 9 (q08).

Only one real chart object came back for the six questions that could chart, and none of the three that ask for a
chart got a correct one. Answers built from the aggregate path cite no sources.

### Field-level extraction accuracy

| Field | Correct (both judge passes) | Item recall | Judge passes disagreed |
|---|---|---|---|
| `candidate_email` | 24/24 | exact match | - |
| `seniority_level` | 23/24 | exact match | - |
| `education_level` | 24/24 | exact match | - |
| `years_of_experience` | 22/24 | exact match | - |
| `candidate_name` | 24/24 | 100% | 0 |
| `current_role` | 23/24 | 96% | 0 |
| `skills` | 24/24 | 100% | 0 |
| `certifications` | 23/24 | 95% | 1 |
| `locations` | 24/24 | 100% | 0 |
| `industries` | 16/24 | 90% | 4 |
| `languages` | 24/24 | 100% | 0 |
| `work_experience` | 22/24 | 98% | 1 |
| `education` | 24/24 | 100% | 0 |

### Every miss

| Resume | Layout | Field | Expected | Extracted / judge reason |
|---|---|---|---|---|
| r01 Aarav Mehta | sidebar_left | `certifications` | judge: correct/wrong | AWS Certified Machine - Specialty matches AWS Certified Machine Learning - Specialty by meaning despite the abbreviated wording. |
| r03 Daniel Okafor | header_band | `years_of_experience` | 16 | 14 |
| r03 Daniel Okafor | header_band | `work_experience` | judge: correct/partial | All three roles match on company, title, and years. Summaries differ in wording but describe the same jobs; one summary adds extra details not in ground truth, which is acceptable. |
| r06 Meera Iyer | classic | `industries` | judge: partial/partial | Matches e-commerce and edtech. Extracted also includes it services, which is extra and not in ground truth. |
| r10 Ishaan Chatterjee | compact | `current_role` | judge: missing/missing | Ground truth has current role 'Software Engineering Intern' but extracted value is null/absent. |
| r12 Michael Brennan | classic | `industries` | judge: partial/wrong | 'healthcare' matches, but 'healthcare saas' does not exactly match expected 'healthcare' and 'b2b saas' is extra and not in ground truth. |
| r13 Zoya Ahmed | header_band | `industries` | judge: correct/partial | E-commerce and fintech match. Extracted 'payments' is extra and true for the resume but not in ground truth, so it does not count as a match. |
| r14 Oliver Schmidt | sidebar_left | `industries` | judge: partial/partial | Mobility and insurance match; extracted also includes ride-sharing and micromobility, which are true resume-related industries but are not in ground truth. |
| r15 Kavya Reddy | compact | `years_of_experience` | 3 | 2 |
| r15 Kavya Reddy | compact | `industries` | judge: partial/partial | Healthcare matches; extra 'digital health' is not in the ground truth but is true for the resume. |
| r18 Rohan Deshpande | academic | `work_experience` | judge: partial/partial | Three expected roles match by company, title, and years. One extra duplicate entry for Indus Grid Energy / Principal Data Engineer (2020–present) is present, which is true but not in ground truth. |
| r19 Nadia Haddad | sidebar_left | `industries` | judge: partial/partial | Matched logistics. Missing cloud hosting. |
| r20 Vikram Singh | header_band | `seniority_level` | junior | mid_level |
| r22 Siddharth Rao | sidebar_right | `industries` | judge: partial/correct | Matched mobility and consulting. Extracted analytics consultancy is extra and not in ground truth, but it is plausible as a related industry term and does not invalidate the field. |
| r23 Hannah Becker | academic | `industries` | judge: partial/correct | Mobility matches. Extra extracted industry 'e-scooter and bike sharing' is not in the ground truth, but it is not a false item if taken as additional detail. |

### Suggested questions on the hosted collection

| Q | Source label | Verdict (2 passes) | Named right | Wrong / missed | Numbers | Sources | Chart | Time |
|---|---|---|---|---|---|---|---|---|
| q01 Which candidates know Python and AWS and have at least 5 years of experience? | structured_fields | correct/correct | 8/8 | none | yes | 8 | no | 9.6s |
| q02 Who has hands-on experience building RAG or LLM applications? Cite what they built. | document_text | partial/partial | 4/7 | -Kenji Watanabe, Ishaan Chatterjee, Oliver Schmidt | yes | 4 | no | 25.6s |
| q03 Compare the strongest candidates for a Senior AI Engineer role (production LLM/RAG systems, 5+ years). Who would you shortlist and why? | hybrid | partial/partial | 3/3 | +Oliver Schmidt | yes | 4 | no | 28.9s |
| q04 How are candidates distributed by seniority level? Show it as a chart. | structured_fields | partial/partial | 4/6 | -junior, principal | no | 24 | no | 11.7s |
| q05 What is the distribution of years of experience across candidates? Show a chart. | structured_fields | wrong/wrong | 0/24 | -0-2: 4, 3-5: 6, 6-9: 6, 10-14: 5, 15+: 3 | no | 0 | yes | 34.6s |
| q06 What are the most common skills across all candidates? Chart the top 10. | structured_fields | wrong/wrong | 0/10 | +RELEVANT CHUNKS, Chunk 1 / Doc: c3066f27-fae7-4e9f-82bd-caa5c8323f0f, Chunk 2 / Doc: 5fb86d5c-bcb1-4108-9f86-c718389829d5, Chunk 3 / Doc: 52318c4a-4f1c-4f90-973d-c5db5c45ac2b, Chunk 4 / Doc: ffc60fa5 | yes | 0 | yes | 86.0s |
| q07 Which candidates are based in or open to Bengaluru? | structured_fields | correct/correct | 5/5 | none | yes | 0 | no | 26.6s |
| q08 Which candidates hold a master's degree or a PhD? | structured_fields | partial/partial | 7/9 | -Priya Raghunathan, Siddharth Rao | no | 0 | no | 23.1s |

### Answers as returned

**q01. Which candidates know Python and AWS and have at least 5 years of experience?**

> Candidates who know Python and AWS and have at least 5 years of experience:
> 
> - Aarav Mehta — 8 years
> - Anjali Nair — 6 years
> - Arjun Malhotra — 7 years
> - Nadia Haddad — 9 years
> - Oliver Schmidt — 10 years
> - Rahul Verma — 5 years
> - Rohan Deshpande — 15 years
> - Siddharth Rao — 13 years

Sources: r19-nadia-haddad.pdf, r01-aarav-mehta.pdf, r11-anjali-nair.pdf, r22-siddharth-rao.pdf, r14-oliver-schmidt.pdf, r05-rahul-verma.pdf, r18-rohan-deshpande.pdf, r16-arjun-malhotra.pdf
Chart: none

**q02. Who has hands-on experience building RAG or LLM applications? Cite what they built.**

> The candidates with explicit hands-on experience building RAG or LLM applications are:
> 
> - **Arjun Malhotra** — Built RAG and agent workflows answering questions over **1.2 million legal contracts** with clause-level citations. He also designed an LLM evaluation pipeline, implemented hybrid vector search, and shipped a meeting-notes summarization feature.
> - **Aarav Mehta** — Built and evaluated RAG assistants used by **2,000+ support agents**. He led LLM-based retrieval and RAG assistants for customer support and product search, reduced hallucinations, and fine-tuned a 7B model for catalogue attribute extraction.
> - **Tanvi Joshi** — Built RAG chatbots with **LangChain and FastAPI** that answer customer questions from help-centre articles, and added pgvector search to PostgreSQL.
> - **Siddharth Rao** — Led an LLM support assistant using RAG over **50,000 policy documents**, deflecting 35% of tickets. He also advises startups on RAG architecture and evaluation.

Sources: r24-tanvi-joshi.pdf, r16-arjun-malhotra.pdf, r01-aarav-mehta.pdf, r22-siddharth-rao.pdf
Chart: data.knowledge.chart_output

**q03. Compare the strongest candidates for a Senior AI Engineer role (production LLM/RAG systems, 5+ years). Who would you shortlist and why?**

> ## Shortlist
> 
> ### 1. Aarav Mehta — strongest overall fit
> - **8 years of experience** shipping ML and LLM-powered retrieval systems to production.
> - Led RAG assistants used by **2,000+ support agents**.
> - Reduced hallucinations from **11% to 3%** using retrieval evaluation and reranking.
> - Also brings model fine-tuning experience, including a 7B model with LoRA, plus AWS, Kubernetes, Docker, and MLflow.
> 
> Aarav offers the clearest evidence of mature production ownership and measurable LLM quality improvements.
> 
> ### 2. Arjun Malhotra — strongest hands-on RAG application fit
> - **7 years of experience**, including two years shipping LLM applications.
> - Builds RAG and agent workflows over **1.2 million legal contracts** with clause-level citations.
> - Designed evaluation gates for faithfulness and citation accuracy.
> - Improved answer accuracy from **71% to 88%** through hybrid search and a vector database.
> 
> Arjun is particularly strong for an application-focused Senior AI Engineer role requiring hands-on RAG engineering and systematic evaluation.
> 
> ### 3. Siddharth Rao — strong architecture-oriented option
> - **13 years of ML experience** and staff-level architecture leadership.
> - Leads a production RAG assistant over **50,000 policy documents**, deflecting **35% of tickets**.
> - Advises startups on RAG architecture and evaluation.
> 
> He is a strong addition for a role with substantial architecture or technical leadership responsibilities. However, his direct LLM/RAG experience is more recent, beginning in 2023.
> 
> ## Recommended shortlist
> 
> **Primary interviews:** Aarav Mehta and Arjun Malhotra.  
> Aarav has the strongest evidence of production maturity and measured impact, while Arjun has the deepest evidence of hands-on RAG application engineering and evaluation.
> 
> **Add Siddharth Rao** if the role is staff-level or architecture-heavy.
> 
> Oliver Schmidt meets the experience requirement and has strong production MLOps and LLM-platform experience, but his resume provides less direct evidence of owning RAG quality or LLM application outcomes.

Sources: r01-aarav-mehta.pdf, r22-siddharth-rao.pdf, r14-oliver-schmidt.pdf, r16-arjun-malhotra.pdf
Chart: none

**q04. How are candidates distributed by seniority level? Show it as a chart.**

> | Seniority level | Candidates | Share |
> |---|---:|---:|
> | Senior | 7 | 29.2% |
> | Mid-level | 6 | 25.0% |
> | Junior | 4 | 16.7% |
> | Principal | 3 | 12.5% |
> | Lead | 2 | 8.3% |
> | Executive | 1 | 4.2% |
> | **Total** | **24** | **100%** |
> 
> ```text
> Senior     ██████████████  7
> Mid-level  ████████████    6
> Junior     ████████        4
> Principal  ██████          3
> Lead       ████            2
> Executive  ██              1
> ```

Sources: r04-sneha-kulkarni.pdf, r07-kenji-watanabe.pdf, r09-lucas-fernandez.pdf, r19-nadia-haddad.pdf, r10-ishaan-chatterjee.pdf, r20-vikram-singh.pdf, r12-michael-brennan.pdf, r01-aarav-mehta.pdf, r21-emily-carter.pdf, r24-tanvi-joshi.pdf, r11-anjali-nair.pdf, r06-meera-iyer.pdf, r03-daniel-okafor.pdf, r22-siddharth-rao.pdf, r23-hannah-becker.pdf, r14-oliver-schmidt.pdf, r13-zoya-ahmed.pdf, r05-rahul-verma.pdf, r18-rohan-deshpande.pdf, r16-arjun-malhotra.pdf, r02-priya-raghunathan.pdf, r17-grace-liu.pdf, r08-fatima-sheikh.pdf, r15-kavya-reddy.pdf
Chart: none

**q05. What is the distribution of years of experience across candidates? Show a chart.**

> The retrieved profiles provide years-of-experience data for **10 candidates**, not the full 24. The available distribution is:
> 
> | Years of experience | Candidates |
> |---:|---:|
> | 2 | 2 |
> | 4 | 2 |
> | 5 | 1 |
> | 7 | 2 |
> | 8 | 1 |
> | 11 | 1 |
> | 18 | 1 |
> 
> ### Chart
> 
> ```text
> Years       Candidates
> 2           ██ 2
> 4           ██ 2
> 5           █  1
> 7           ██ 2
> 8           █  1
> 11          █  1
> 18          █  1
> ```
> 
> A complete distribution and chart for all 24 candidates cannot be verified because the experience values for the remaining 14 candidates are unavailable.

Sources: none
Chart: data.knowledge.chart_output

**q06. What are the most common skills across all candidates? Chart the top 10.**

> 
> RELEVANT CHUNKS:
> [Chunk 1 | Doc: c3066f27-fae7-4e9f-82bd-caa5c8323f0f] File Name: r17-grace-liu.pdf
> 
> Extra Info: file title: Grace Liu 
> 
> 
> Extracted Text: Grace Liu
> Data Scientist
> grace.liu@example.net · Singapore
> PROFILE
> Data scientist with 4 years of experience in payments fraud and NLP for customer support.
> EXPERIENCE
> Data Scientist
> Mangrove Pay, Singapore - Digital wallet (fintech)
> 2022 - Present
> Builds fraud-detection models on BigQuery features; prevented an estimated $3.1M in losses in
> 2025.
> Fine-tuned a transformer classifier that routes support tickets in 6 languages.
> EDUCATION
> M.S., Business Analytics
> Straits Institute of Technology, 2022
> B.Sc., Economics
> Straits Institute of Technology, 2021
> SKILLS
> python
> sql
> scikit-learn
> nlp
> transformers
> gcp
> bigquery
> experimentation
> tableau
> CERTIFICATIONS
> Google Cloud Professional Data Engineer
> LANGUAGES
> English
> Mandarin
> [Chunk 2 | Doc: 5fb86d5c-bcb1-4108-9f86-c718389829d5] File Name: r12-michael-brennan.pdf
> 
> Extra Info: file title: Michael Brennan 
> 
> 
> Extracted Text: Michael Brennan
> VP of Engineering
> michael.brennan@example.org · +1 512-555-0142 · Austin, TX
> PROFILE
> Engineering executive with 18 years of experience, the last nine leading engineering organisations of 40-
> 120 people in healthcare SaaS.
> EXPERIENCE
> VP of Engineering
> Tallgrass Health, Austin, TX - Clinical scheduling SaaS (healthcare)
> 2021 - Present
> Runs a 120-person engineering organisation across 14 teams and a $22M budget.
> Led the platform consolidation onto AWS and Kubernetes; infrastructure cost down 31%.
> Director of Engineering
> Tallgrass Health, Austin, TX - Clinical scheduling SaaS (healthcare)
> Grew the engineering team from 40 to 85 and launched the patient mobile app.
> Engineering Manager
> Cobaltline Software, Dallas, TX - B2B workflow software (saas)
> Managed two backend teams building workflow automation services in Go.
> Senior Software Engineer
> Cobaltline Software, Dallas, TX - B2B workflow software (saas)
> Built the document routing engine and its public API.
> 2017 - 2021
> 2013 - 2017
> 2008 - 2013
> EDUCATION
> M.S., Computer Science
> Lone Prairie University, 2008
> B.S., Computer Science
> Lone Prairie University, 2006
> SKILLS
> engineering leadership
> system design
> aws
> hiring
> budgeting
> platform strategy
> go
> kubernetes
> LANGUAGES
> English
> [Chunk 3 | Doc: 52318c4a-4f1c-4f90-973d-c5db5c45ac2b] File Name: r19-nadia-haddad.pdf
> 
> Extra Info: file title: Nadia 
> 
> 
> Extracted Text: Nadia
> Haddad
> Senior Site Reliability
> Engineer
> CONTACT
> nadia.haddad@example.org
> Dubai
> SKILLS
> kubernetes
> gcp
> aws
> terraform go
> python
> prometheus
> incident management
> linux
> helm
> CERTIFICATIONS
> Certified Kubernetes
> Administrator (CKA)
> Google Professional
> Cloud DevOps Engineer
> L ANGUAGES
> Arabic
> English
> French
> PROFILE
> Site reliability engineer with 9 years of experience running Kubernetes
> platforms on GCP and AWS and leading incident response for logistics
> systems.
> E XPERIENCE
> Senior Site Reliability Engineer
> Sandpiper Logistics, Dubai - Last-mile delivery network (logistics)
> 2021 - Present
> Leads reliability for the dispatch platform (99.95% SLO) and runs the
> incident-response rotation.
> Wrote the Go operator that autoscales routing workers on GKE.
> Site Reliability Engineer
> Sandpiper Logistics, Dubai - Last-mile delivery network (logistics)
> 2019 - 2021
> Moved 40 services to Kubernetes with Helm charts and Terraform.
> Systems Administrator
> Oasisnet Hosting, Amman - Web hosting provider (cloud hosting)
> 2017 - 2019
> Administered 600 Linux servers and Prometheus monitoring for hosting
> customers.
> EDUCATION
> B.Sc., Computer Science
> Levant Polytechnic University, 2017
> [Chunk 4 | Doc: ffc60fa5-abec-4276-a0c7-e9b556b75a18] File Name: r04-sneha-kulkarni.pdf
> 
> Extra Info: file title: Sneha Kulkarni 
> 
> 
> Extracted Text: Sneha Kulkarni
> Data Analyst
> PROFILE
> sneha.kulkarni@example.com · Pune
> Data analyst with 1 year of experience turning retail sales data into Power BI dashboards and weekly business reviews.
> SKILLS
> sql
> excel
> power bi
> python
> pandas
> data visualization
> E XPERIENCE
> Data Analyst
> Tarang Retail, Pune - Apparel retail chain (retail)
> Builds Power BI dashboards and weekly sales reviews for 120 stores.
> Automated the inventory ageing report with Python and pandas, saving 6 hours a week.
> Analytics Intern
> Tarang Retail, Pune - Apparel retail chain (retail)
> Cleaned point-of-sale data and built Excel reports during a 3-month summer internship.
> 2025 - Present
> 2024 - 2024
> EDUCATION
> B.Sc., Statistics
> Sahyadri College of Science, 2025
> CERTIFICATIONS
> Google Data Analytics Professional Certificate
> L ANGUAGES
> English
> Marathi
> Hindi
> [Chunk 5 | Doc: 1ec00067-e043-483f-9200-d4e9fd164e70] File Name: r05-rahul-verma.pdf
> 
> Extra Info: file title: Rahul Verma 
> 
> 
> Extracted Text: Rahul Verma
> DevOps Engineer
> PROFILE
> DevOps engineer with 5 years of experience automating AWS
> infrastructure with Terraform and running Kubernetes platforms for
> SaaS products.
> EXPERIENCE
> DevOps Engineer
> Zentrix Cloud Services, Hyderabad - B2B SaaS platform (saas)
> 2023 - Present
> Runs the EKS platform and Terraform-managed AWS infrastructure
> for 60 microservices.
> Built Prometheus and Grafana alerting that cut mean time to detect
> from 25 to 4 minutes.
> Associate DevOps Engineer
> Orbitwave Software, Hyderabad - HR software vendor (saas)
> 2021 - 2023
> Maintained Jenkins CI/CD pipelines and Ansible-based server
> configuration.
> EDUCATION
> B.Tech, Information Technology
> Golconda Institute of Engineering, 2021
> CONTACT
> rahul.verma@example.org
> Hyderabad
> SKILLS
> aws
> terraform
> kubernetes
> docker
> jenkins
> python
> bash
> prometheus
> grafana
> ansible
> CERTIFICATIONS
> AWS Certified DevOps
> Engineer - Professional
> Certified Kubernetes
> Administrator (CKA)
> LANGUAGES
> English
> Hindi
> Telugu
> [Chunk 6 | Doc: 0212b241-52c8-4e0e-be49-66c600cd287e] File Name: r21-emily-carter.pdf
> 
> Extra Info: file title: Emily Carter 
> 
> 
> Extracted Text: Emily Carter
> Analytics Engineer
> PROFILE
> emily.carter@example.com · +1 416-555-0187 · Toronto
> Analytics engineer with 5 years of experience modelling retail and banking data in dbt and Snowflake and building
> Looker self-serve analytics.
> SKILLS
> sql
> dbt
> python
> looker
> snowflake
> airflow data modeling
> git
> E XPERIENCE
> Analytics Engineer
> Maplecrest Retail, Toronto - Home goods retailer (retail)
> Maintains 300 dbt models in Snowflake and the Looker explores used by 200 staff.
> Data Analyst
> Northlake Bank, Toronto - Regional bank (banking)
> Produced SQL reporting on deposits and loan portfolios; automated it with Airflow.
> 2023 - Present
> 2021 - 2023
> EDUCATION
> B.Sc., Mathematics
> Lakeshore University, 2021
> CERTIFICATIONS
> dbt Analytics Engineering Certification
> L ANGUAGES
> English
> French
> [Chunk 7 | Doc: f2842159-4a08-49db-80ec-824ad8d383f4] File Name: r24-tanvi-joshi.pdf
> 
> Extra Info: file title: Tanvi Joshi 
> 
> 
> Extracted Text: Tanvi Joshi
> Junior AI Engineer
> CONTACT
> tanvi.joshi@example.net
> Ahmedabad (open to Remote)
> SKILLS
> python
> javascript
> react
> fastapi
> langchain
> rag
> postgresql
> docker
> L ANGUAGES
> English
> Gujarati
> Hindi
> PROFILE
> AI engineer with 2 years of experience, moving from full-stack web
> development to building RAG chatbots and document question-answering
> with LangChain and FastAPI.
> E XPERIENCE
> Junior AI Engineer
> Kesar Labs, Ahmedabad - Customer support software (saas)
> 2025 - Present
> Builds RAG chatbots with LangChain and FastAPI that answer customer
> questions from help-centre articles.
> Added pgvector search to the existing PostgreSQL database.
> Associate Software Engineer
> Kesar Labs, Ahmedabad - Customer support software (saas)
> 2024 - 2025
> Built React screens and REST endpoints for the support dashboard.
> EDUCATION
> B.E., Information Technology
> Sabarmati Institute of Engineering, 2024
> [Chunk 8 | Doc: dae2d1ae-830e-4378-929e-cd75d79afda8] File Name: r14-oliver-schmidt.pdf
> 
> Extra Info: file title: Oliver 
> 
> 
> Extracted Text: Oliver
> Schmidt
> Lead MLOps Engineer
> CONTACT
> oliver.schmidt@example.net
> Berlin
> SKILLS
> python
> kubernetes
> mlflow kubeflow aws
> gcp
> terraform docker
> feature stores
> llmops
> CERTIFICATIONS
> Google Professional
> Machine Learning
> Engineer
> Certified Kubernetes
> Administrator (CKA)
> L ANGUAGES
> German
> English
> PROFILE
> MLOps lead with 10 years of experience taking machine learning and LLM
> services from notebook to production on AWS and GCP.
> E XPERIENCE
> Lead MLOps Engineer
> Glasswing Mobility, Berlin - Ride-sharing and micromobility (mobility)
> 2022 - Present
> Leads the 6-person ML platform team that deploys pricing, ETA and LLM
> support models.
> Built the feature store and model registry on Kubeflow and MLflow;
> deploy time from 2 weeks to 1 day.
> Senior Machine Learning Engineer
> Glasswing Mobility, Berlin - Ride-sharing and micromobility (mobility)
> 2019 - 2022
> Productionised demand-forecasting models on GCP with automated
> retraining.
> Data Engineer
> Feldmark Versicherung, Hamburg - Insurance group (insurance)
> 2016 - 2019
> Built the claims data warehouse feeding actuarial models.
> EDUCATION
> M.Sc., Informatics
> Technische Hochschule Rabenfeld, 2016
> [Chunk 9 | Doc: 643eb2f0-ecce-419f-96f7-744810d0bd18] File Name: r06-meera-iyer.pdf
> 
> Extra Info: file title: Meera Iyer 
> 
> 
> Extracted Text: Meera Iyer
> Senior Product Manager
> meera.iyer@example.com · Mumbai
> PROFILE
> Product manager with 11 years of professional experience: two years as a software engineer, then nine
> years leading consumer product teams in e-commerce and edtech.
> EXPERIENCE
> Senior Product Manager
> Vidyanta Learning, Mumbai - Online learning platform (edtech)
> Owns the learner experience roadmap for 3 million monthly learners.
> Ran 40+ A/B tests that lifted course completion by 18%.
> Product Manager
> Basketly, Mumbai - Grocery delivery app (e-commerce)
> Managed the checkout and payments product; reduced cart abandonment by 9%.
> Software Engineer
> Sunfield Infotech, Pune - IT services firm
> Developed Java web applications for enterprise clients.
> 2021 - Present
> 2017 - 2021
> 2013 - 2015
> EDUCATION
> MBA, Marketing
> Konkan School of Management, 2017
> B.E., Computer Engineering
> Deccan Plateau Engineering College, 2013
> SKILLS
> product strategy
> roadmapping
> a/b testing
> sql
> user research
> agile
> jira
> stakeholder management
> product analytics
> CERTIFICATIONS
> Certified Scrum Product Owner (CSPO)
> LANGUAGES
> English
> Hindi
> Marathi
> [Chunk 10 | Doc: 1e9c7615-3729-4fef-a19b-9b9569e5057a] File Name: r15-kavya-reddy.pdf
> 
> Extra Info: file title: Kavya Reddy 
> 
> 
> Extracted Text: Kavya Reddy
> Software Engineer II
> PROFILE
> kavya.reddy@example.org · Bengaluru
> Backend engineer building Java and Spring Boot microservices for a digital health platform.
> SKILLS
> java
> spring boot
> postgresql
> redis microservices
> aws
> docker
> rest apis
> E XPERIENCE
> Software Engineer II
> Haldi Health, Bengaluru - Digital health platform (healthcare)
> Owns the appointment-booking microservices (Java, Spring Boot, PostgreSQL, Redis).
> Cut p95 booking latency from 900 ms to 180 ms.
> Software Engineer I
> Haldi Health, Bengaluru - Digital health platform (healthcare)
> Built REST APIs for patient records deployed with Docker on AWS.
> EDUCATION
> B.Tech, Electronics and Communication Engineering
> Krishna Delta Institute of Technology, 2023
> L ANGUAGES
> English
> Telugu
> Kannada
> 2025 - Present
> 2023 - 2025
> [Chunk 11 | Doc: unknown] File Name: r07-kenji-watanabe.pdf
> 
> Extra Info: file title: Kenji Watanabe 
> 
> 
> Extracted Text: Kenji Watanabe
> AI Research Engineer
> kenji.watanabe@example.net · Tokyo (open to Remote)
> Profile
> Research engineer with 4 years of industry experience training and evaluating large language
> models. PhD on retrieval-augmented language models; currently works on RLHF and long-context
> evaluation.
> Education
> Ph.D., Computer Science
> Kanto Institute of Science, 2022
> M.Eng., Information Engineering
> Kanto Institute of Science, 2018
> Experience
> AI Research Engineer
> Sorahana Labs, Tokyo - AI research lab (artiﬁcial intelligence)
> 2022 - Present
> Trains and evaluates 1B-13B parameter language models on a 512-GPU cluster.
> Built the long-context retrieval evaluation suite used to gate every model release.
> Wrote custom CUDA kernels that sped up attention by 1.4x.
> Research Intern
> Sorahana Labs, Tokyo - AI research lab (artiﬁcial intelligence)
> 2021 - 2021
> Prototyped a retrieval-augmented question-answering model (summer internship).
> Skills
> python
> pytorch
> jax
> transformers
> llm pretraining
> rlhf
> cuda
> distributed training
> rag
> llm evaluation
> Languages
> Japanese
> English
> [Chunk 12 | Doc: unknown] File Name: r09-lucas-fernandez.pdf
> 
> Extra Info: file title: Lucas Fernandez 
> 
> 
> Extracted Text: Lucas Fernandez
> Senior Frontend Engineer
> lucas.fernandez@example.com · Madrid
> PROFILE
> Frontend engineer with 7 years of experience building accessible React and Next.js applications for travel and SaaS
> companies.
> EXPERIENCE
> Senior Frontend Engineer
> Brisa Travel Tech, Madrid - Online travel booking (travel)
> 2022 - Present
> Leads the Next.js rebuild of the ﬂight booking web app; Core Web Vitals now green on 95% of pages.
> Made checkout WCAG 2.2 AA compliant.
> Frontend Developer
> Nodoverde Studio, Barcelona - Product studio building SaaS tools (saas)
> Built React and GraphQL dashboards for 12 SaaS clients.
> 2019 - 2022
> EDUCATION
> Bachelor's, Computer Engineering
> Universidad de Valdemora, 2019
> SKILLS
> typescript
> react
> next.js
> graphql
> node.js
> css
> frontend testing
> accessibility webpack
> LANGUAGES
> Spanish
> English
> Catalan
> [Chunk 13 | Doc: unknown] File Name: r10-ishaan-chatterjee.pdf
> 
> Extra Info: file title: Ishaan Chatterjee 
> 
> 
> Extracted Text: Ishaan Chatterjee
> Computer Science Graduate
> PROFILE
> ishaan.chatterjee@example.net · Kolkata (open to Bengaluru, Remote)
> 2026 computer science graduate looking for an entry-level software or machine learning role. Open to Bengaluru or
> remote.
> SKILLS
> python
> c++ data structures machine learning
> sql
> git
> flask
> langchain
> E XPERIENCE
> Software Engineering Intern
> Quillstone Labs, Remote - Developer tools startup (software)
> Built Flask API endpoints and unit tests during a two-month summer internship.
> 2025 - 2025
> PROJECTS
> CampusQA: a RAG chatbot over 400 pages of college course notes, built with LangChain and a local vector store.
> Route planner in C++ using Dijkstra's algorithm for the campus shuttle.
> EDUCATION
> B.Tech, Computer Science and Engineering
> Hooghly Riverside Institute of Technology, 2026
> L ANGUAGES
> English
> Bengali
> Hindi
> [Chunk 14 | Doc: unknown] File Name: r20-vikram-singh.pdf
> 
> Extra Info: file title: Vikram Singh 
> 
> 
> Extracted Text: Vikram Singh
> Associate Product Manager
> vikram.singh@example.net · Bengaluru
> PROFILE
> Associate product manager with 2 years of experience on learner onboarding and growth for an edtech app.
> EXPERIENCE
> Associate Product Manager
> Gyaanpath Learning, Bengaluru - Test-prep app (edtech)
> 2024 - Present
> Owns learner onboarding; redesigned the ﬁrst-week ﬂow in Figma and lifted day-7 retention by 11%.
> Launched the referral programme, now 14% of new sign-ups.
> EDUCATION
> B.Tech, Mechanical Engineering
> Aravalli Institute of Technology, 2024
> SKILLS
> product discovery
> sql
> ﬁgma
> jira
> user research
> product analytics wireframing
> LANGUAGES
> English
> Hindi
> [Chunk 15 | Doc: unknown] File Name: r01-aarav-mehta.pdf
> 
> Extra Info: file title: Aarav Mehta 
> 
> 
> Extracted Text: Aarav Mehta
> Senior Machine Learning
> Engineer
> CONTACT
> aarav.mehta@example.com
> Bengaluru
> SKILLS
> python
> pytorch
> aws
> langchain
> rag
> llm fine-tuning
> docker
> kubernetes mlflow
> sql
> CERTIFICATIONS
> AWS Certified Machine
> Learning - Specialty
> L ANGUAGES
> English
> Hindi
> Kannada
> PROFILE
> Machine learning engineer with 8 years of experience shipping
> recommendation, ranking and LLM-powered retrieval systems to production
> on AWS. Built and evaluated retrieval-augmented generation (RAG)
> assistants used by 2,000+ support agents.
> E XPERIENCE
> Senior Machine Learning Engineer
> Lumora Commerce, Bengaluru - Online marketplace (e-commerce)
> 2022 - Present
> Leads a 4-person team building LLM-based retrieval and RAG assistants
> for customer support and product search.
> Cut assistant hallucination rate from 11% to 3% with a retrieval
> evaluation harness and reranking.
> Fine-tuned a 7B open-weight model with LoRA for catalogue attribute
> extraction.
> Machine Learning Engineer
> Lumora Commerce, Bengaluru - Online marketplace (e-commerce)
> 2020 - 2022
> Built and deployed product recommendation and ranking models serving
> 30M sessions a month.
> Moved model training to Kubernetes with MLflow tracking.
> Data Scientist
> Brightpath Lending, Bengaluru - Digital lending startup (fintech)
> 2018 - 2020
> Developed credit-risk scoring models for small-business loans using
> gradient boosting.
> Automated weekly model monitoring reports in SQL and Python.
> EDUCATION
> M.Tech, Computer Science
> Western Ghats Institute of Technology, 2018
> B.E., Information Science
> Malnad College of Engineering Studies, 2016
> [Chunk 16 | Doc: unknown] File Name: r11-anjali-nair.pdf
> 
> Extra Info: file title: Anjali Nair 
> 
> 
> Extracted Text: Anjali Nair
> Senior Data Engineer
> PROFILE
> Data engineer with 6 years of experience building batch and
> streaming pipelines on AWS, Snowflake and Airflow for logistics and
> SaaS companies. Relocating to Bengaluru.
> EXPERIENCE
> Senior Data Engineer
> Pelican Freight Analytics, Kochi - Freight visibility platform (logistics)
> 2023 - Present
> Owns streaming shipment-tracking pipelines (Kafka, Spark
> Structured Streaming) landing in Snowflake.
> Introduced dbt models and data contracts; failed loads dropped by
> 70%.
> Data Engineer
> Coral Bay Systems, Kochi - Subscription billing SaaS (saas)
> 2020 - 2023
> Built Airflow batch pipelines from AWS RDS and S3 into the
> analytics warehouse.
> EDUCATION
> B.Tech, Computer Science
> Malabar Coast College of Engineering, 2020
> CONTACT
> anjali.nair@example.com
> Kochi (open to Bengaluru)
> SKILLS
> python
> spark
> airflow
> sql
> snowflake
> dbt
> aws
> kafka
> data modeling
> scala
> CERTIFICATIONS
> AWS Certified Data
> Engineer - Associate
> SnowPro Core Certification
> LANGUAGES
> English
> Malayalam
> Hindi
> [Chunk 17 | Doc: unknown] File Name: r03-daniel-okafor.pdf
> 
> Extra Info: file title: Daniel Okafor 
> 
> 
> Extracted Text: Daniel Okafor
> Principal Software Engineer
> daniel.okafor@example.net · +44 7700 900123 · London
> PROFILE
> Principal engineer who designs high-throughput payment systems. Deep experience in distributed systems, event
> streaming and platform reliability; mentors staﬀ engineers across four teams.
> EXPERIENCE
> Principal Software Engineer
> Halcyon Payments, London - Card payments processor (ﬁntech)
> 2019 - Present
> Sets the architecture for the card-authorisation platform (4,000 TPS, 99.99% availability).
> Led the migration from a monolith to Go and Java services on Kubernetes with Kafka event streams.
> Staﬀ Software Engineer
> Halcyon Payments, London - Card payments processor (ﬁntech)
> Designed the double-entry ledger and settlement services on PostgreSQL and Kafka.
> Software Engineer
> Ridgeway Telecom, Manchester - Mobile network operator (telecommunications)
> Built billing and SIM provisioning services in Java for 6 million subscribers.
> 2016 - 2019
> 2010 - 2016
> EDUCATION
> B.Eng., Computer Engineering
> University of Eastmere, 2010
> SKILLS
> java
> go
> kubernetes
> aws
> postgresql
> kafka
> distributed systems
> system design
> terraform grpc
> CERTIFICATIONS
> AWS Certiﬁed Solutions Architect - Professional
> Certiﬁed Kubernetes Administrator (CKA)
> LANGUAGES
> English
> Yoruba
> [Chunk 18 | Doc: unknown] File Name: r22-siddharth-rao.pdf
> 
> Extra Info: file title: Siddharth Rao 
> 
> 
> Extracted Text: CONTACT
> siddharth.rao@example.org
> Hyderabad (open to Remote)
> SKILLS
> python
> tensorflow
> pytorch
> recommender systems
> rag
> llms
> spark
> aws
> kubernetes mlops
> LANGUAGES
> English
> Telugu
> Hindi
> Siddharth Rao
> Staff Machine Learning Engineer
> PROFILE
> Machine learning engineer with 13 years of experience in
> recommender systems and, since 2023, LLM and RAG systems. Holds
> a full-time staff role and, in parallel, a part-time consulting practice
> advising startups on RAG architecture.
> EXPERIENCE
> Staff Machine Learning Engineer
> Tejas Mobility, Hyderabad - Ride-hailing platform (mobility)
> 2021 - Present
> Sets ML architecture for rider-driver matching across 30 cities.
> Leads the LLM support-assistant program: RAG over 50k policy
> documents, deflecting 35% of tickets.
> ML Consultant
> Independent, Remote - Part-time consulting practice (consulting)
> 2023 - Present
> Advises early-stage startups on RAG architecture and evaluation
> (part-time, alongside the staff role).
> Senior Machine Learning Engineer
> Tejas Mobility, Hyderabad - Ride-hailing platform (mobility)
> 2018 - 2021
> Built the TensorFlow ETA prediction and surge-pricing models.
> Data Scientist
> Arrowroot Analytics, Hyderabad - Analytics consultancy (consulting)
> 2013 - 2018
> Built recommender systems for retail clients on Spark.
> EDUCATION
> M.S., Computer Science
> Godavari Institute of Science and Technology, 2013
> [Chunk 19 | Doc: unknown] File Name: r23-hannah-becker.pdf
> 
> Extra Info: file title: Hannah Becker 
> 
> 
> Extracted Text: Hannah Becker
> Product Analyst
> hannah.becker@example.com · Munich
> Profile
> Product analyst with 4 years of experience in mobility apps. Took a gap year after graduation to
> volunteer on a rural education project in Peru.
> Education
> B.Sc., Economics
> Isar Valley University, 2021
> Experience
> Product Analyst
> Alpenrot Mobility, Munich - E-scooter and bike sharing (mobility)
> 2022 - Present
> Analyses rider behaviour in Amplitude and SQL; runs 3-4 app experiments a month.
> Built the pricing-elasticity model behind the 2025 subscription launch.
> Gap Year
> 2021-2022: Volunteer teacher, rural education project, Cusco region, Peru (unpaid).
> Skills
> sql
> python
> amplitude
> a/b testing
> product analytics
> tableau
> statistics
> Languages
> German
> English
> Spanish
> [Chunk 20 | Doc: unknown] File Name: r13-zoya-ahmed.pdf
> 
> Extra Info: file title: Zoya Ahmed 
> 
> 
> Extracted Text: Zoya Ahmed
> Senior Business Analyst
> zoya.ahmed@example.com · New Delhi (open to Gurugram)
> PROFILE
> Business analyst with 7 years of experience in payments and e-commerce analytics. Returned to work in 2022 after
> a one-year career break to care for a family member.
> EXPERIENCE
> Senior Business Analyst
> Saﬀronkart, Gurugram - Online fashion marketplace (e-commerce)
> 2022 - Present
> Leads pricing and promotions analytics; built the Looker promotion-ROI model used in every campaign review.
> Designed the A/B testing guardrails adopted by the growth team.
> Business Analyst
> Mintfield Payments, New Delhi - Merchant payments company (ﬁntech)
> Forecast merchant transaction volumes and reported on payment success rates in Power BI.
> 2018 - 2021
> CAREER BREAK
> Jul 2021 - Jun 2022: Planned career break to care for a family member. Completed an online Python for analytics
> course during this period.
> EDUCATION
> MBA, Business Analytics
> Yamuna School of Business, 2018
> B.A., Economics
> Indraprastha Arts and Commerce College, 2016
> SKILLS
> sql
> python
> power bi
> excel
> stakeholder management
> forecasting
> a/b testing
> looker
> LANGUAGES
> English
> Hindi
> Urdu

Sources: none
Chart: data.knowledge.chart_output

**q07. Which candidates are based in or open to Bengaluru?**

> Candidates based in or open to Bengaluru are:
> 
> - Aarav Mehta
> - Anjali Nair
> - Ishaan Chatterjee
> - Kavya Reddy
> - Vikram Singh

Sources: none
Chart: data.knowledge.chart_output

**q08. Which candidates hold a master's degree or a PhD?**

> Candidates holding a master’s degree or PhD:
> 
> - Kenji Watanabe — Ph.D. in Computer Science; M.Eng. in Information Engineering
> - Michael Brennan — M.S. in Computer Science
> - Aarav Mehta — M.Tech in Computer Science
> - Meera Iyer — MBA in Marketing
> - Zoya Ahmed — MBA in Business Analytics
> - Oliver Schmidt — M.Sc. in Informatics
> - Grace Liu — M.S. in Business Analytics

Sources: none
Chart: data.knowledge.chart_output

## Gaps found

Ranked by what they do to the sample experience. Each is observed on this run; the evidence is in `hosted_run/`.

1. **Raw retrieval context returned as the answer (q06).** For "What are the most common skills across all
   candidates? Chart the top 10." the `response` is the prompt context itself: a `RELEVANT CHUNKS:` header and 20
   `[Chunk n | Doc: ...] ... Extracted Text:` blocks, 22,555 characters and no answer (86 s). A user sees a
   wall of resume text. Session `answers.json` → `q06.session_id`.
2. **Collection-wide aggregates are incomplete or wrong.** None of these is an extraction problem: the extracted
   values are right, and the aggregate over them is not.
   - **q05** says "the retrieved profiles provide years-of-experience data for 10 candidates, not the full 24",
     though all 24 documents carry `years_of_experience`.
   - **q04** lists senior 7, mid-level 6, junior 4, principal 3, lead 2, executive 1. That sums to 23 while the
     answer says 24. The extracted values are mid-level 7, junior 4, lead 2, principal 3.
   - **q08** names 7 of the 9 candidates whose extracted `education_level` is masters/phd, missing Priya
     Raghunathan and Siddharth Rao.
3. **Charts.** Only q05 and q06 produced a `chart_response`, and neither is right.
   - **q05:** a per-person bar of the 10 retrieved people, not a distribution.
   - **q06:** category `python` four times at value 4, `aws` three times at 3, and so on. The true count for
     python is 18.
   - **q04** asks for a chart, gets none, and draws ASCII bars in the text instead.
   - q02, q07 and q08 got `direct_response` ("a chart is not suitable"), which is right for those questions. q07's
     message also asks the user to "provide the row data".
   - Charts are the di-chart-verify session's area; recorded here as observed.
4. **Aggregate and filter answers cite no sources.** q05-q08 return 0 `relevant_sources`. q01, q02 and q03 do cite.
   The sample promise is "answers with sources".
5. **RAG question under-recalls (q02, document text).** It names 4 of 7: Arjun, Aarav, Siddharth and Tanvi are right.
   It misses Kenji Watanabe (RAG QA prototype, retrieval eval suite), Ishaan Chatterjee (CampusQA student RAG
   project) and Oliver Schmidt (deploys LLM support models). It cited 4 sources, so retrieval stopped short.
6. **Extraction does not know today's date.** Years computed from "2010 - Present" / "2023 - Present" came out 14
   instead of 16 (r03) and 2 instead of 3 (r15). The extraction prompt in fs-agent-workers
   (`celery_utils/enrichment/prompts.py`) gives the model no reference date. Every resume whose years must be
   computed is exposed. The fix belongs in the workers repo.
7. **Single extraction slips.**
   - r01: certification read as "AWS Certified Machine - Specialty", dropping "Learning". The line wraps in the
     two-column sidebar.
   - r03: the Staff role is dated 2010-2019 instead of 2016-2019.
   - r18: the current Principal role is listed twice.
   - r10: `current_role` is null for a fresher whose only role is an internship.
   - r20: an Associate PM with 2 years is rated `mid_level`.
   - `industries` adds true-but-unrequested labels such as "payments" and "digital health" on 8 resumes, and drops
     "cloud hosting" once.
8. **The create wizard drops the template's allowed values (product path, not this run).** `GET /v1/field-templates`
   (`get_field_template_catalog`) sends `field_name`, `description`, `field_type` and `schema`, but not
   `sample_values`, `restrict_to_choices` or `max_items`. The wizard (`document-intelligence-ui`
   `src/data/createCollection.ts`) re-creates fields from that list. A resume collection made in the UI therefore
   extracts `seniority_level`/`education_level` with "Must be exactly one of the allowed values" and no allowed values,
   and loses the 10-skill cap. This run avoided it by seeding with `field_template: "resume"`, which is what sample
   provisioning should do.
9. **The shared daily chat limit gates verification.** The verifying account allows 50 chat messages a day (counter
   shared by every session working on DI). This run stopped at the 51st. A sample collection under a real user is
   subject to the same limit.

### Answer-source labels

`questions.json` labels each question `structured_fields`, `document_text` or `hybrid`. On this run the
single-facet structured questions (q01, q07) work. The multi-row aggregate structured questions (q04-q06, q08) are
the failures above. The document-text question (q02) under-recalls. The hybrid comparison (q03) is the best answer of the run: it
shortlists Aarav, Arjun and Siddharth with sound reasons and explicitly sets Oliver Schmidt aside. The judge's
"+Oliver Schmidt" counts a considered-and-rejected mention as an inclusion, so its `partial` understates q03.
