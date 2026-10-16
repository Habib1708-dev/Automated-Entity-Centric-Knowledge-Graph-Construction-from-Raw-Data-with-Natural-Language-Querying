# Reference verification (2026-10-16)

Sources used: Crossref REST API (`api.crossref.org/works/<doi>`, the registrar record behind each DOI), the arXiv API, ACL Anthology, NeurIPS proceedings pages, iclr.cc, IJCAI, W3C, and official documentation pages. dblp could not be used (it served a bot challenge); ACM DL, IEEE Xplore, Springer and Taylor & Francis pages blocked automated fetches, so their DOI records were checked on Crossref and OpenAlex instead.

| # | key | title | verified at | note |
|---|---|---|---|---|
| 1 | hogan2021knowledge | Knowledge Graphs | https://api.crossref.org/works/10.1145/3447772 ; https://arxiv.org/abs/2003.02320 | ACM CSUR 54(4), article 71, pp. 71:1–71:37. 18 authors. |
| 2 | ji2022survey | A Survey on Knowledge Graphs: Representation, Acquisition, and Applications | https://api.crossref.org/works/10.1109/TNNLS.2021.3070843 ; https://arxiv.org/abs/2002.00388 | IEEE TNNLS 33(2):494–514, 2022. Correct. |
| 3 | pan2024unifying | Unifying Large Language Models and Knowledge Graphs: A Roadmap | https://api.crossref.org/works/10.1109/TKDE.2024.3352100 ; https://arxiv.org/abs/2306.08302 | IEEE TKDE 36(7):3580–3599, 2024. Authors: Pan, Luo, Wang, Chen, Wang, Wu. |
| 4 | lewis2020retrieval | Retrieval-Augmented Generation for Knowledge-Intensive NLP Tasks | https://papers.nips.cc/paper/2020/hash/6b493230205f780e1bc26945df7481e5-Abstract.html | NeurIPS 2020, vol. 33. No page range on the proceedings page, so none given. |
| 5 | edge2024local | From Local to Global: A Graph RAG Approach to Query-Focused Summarization | https://arxiv.org/abs/2404.16130 | arXiv 2404.16130, 10 authors. Correct. |
| 6 | gutierrez2024hipporag | HippoRAG: Neurobiologically Inspired Long-Term Memory for Large Language Models | https://papers.nips.cc/paper_files/paper/2024/hash/6ddc001d07ca4f319af96a3024f6dbd1-Abstract-Conference.html ; https://mlanthology.org/neurips/2024/gutierrez2024neurips-hipporag | NeurIPS 2024 main track, DOI 10.52202/079017-1902. First author's full name is Bernal Jiménez Gutiérrez. |
| 7 | peng2026graph | Graph Retrieval-Augmented Generation: A Survey | https://api.crossref.org/works/10.1145/3777378 ; https://arxiv.org/abs/2408.08921 | **Correction**: now published in ACM Transactions on Information Systems 44(2):1–52 (online 23 Dec 2025, print issue 2026), DOI 10.1145/3777378. The key uses 2026, the print year. Cite as arXiv 2024 only if you prefer the preprint. |
| 8 | yao2023react | ReAct: Synergizing Reasoning and Acting in Language Models | https://iclr.cc/virtual/2023/poster/11003 ; https://arxiv.org/abs/2210.03629 | ICLR 2023 (top 5% poster). OpenReview id WE_vluYUL-X (given on the iclr.cc page). |
| 9 | schick2023toolformer | Toolformer: Language Models Can Teach Themselves to Use Tools | https://proceedings.neurips.cc/paper/2023/hash/d842425e4bf79ba039352da0f658a906-Abstract.html | **Correction**: full title added. The NeurIPS 2023 author list has 9 authors (it adds Eric Hambro, who is missing from arXiv v1). DOI 10.52202/075280-2997, vol. 36. |
| 10 | robertson2009probabilistic | The Probabilistic Relevance Framework: BM25 and Beyond | https://nowpublishers.com/article/Details/INR-019 (via search listing) ; https://api.crossref.org/works/10.1561/1500000019 | FnTIR 3(4):333–389, 2009, as the publisher's listing and standard indexes give it. Crossref's record says vol. 4, issue 1–2, pp. 1–174, which looks wrong, so the publisher's numbers are used. |
| 11 | karpukhin2020dense | Dense Passage Retrieval for Open-Domain Question Answering | https://api.crossref.org/works/10.18653/v1/2020.emnlp-main.550 | EMNLP 2020, pp. 6769–6781. |
| 12 | cormack2009reciprocal | Reciprocal Rank Fusion outperforms Condorcet and individual Rank Learning Methods | https://api.crossref.org/works/10.1145/1571941.1572114 ; OpenAlex record (abstract) | SIGIR '09, pp. 758–759 (a 2-page poster paper). Third author is spelled Büttcher (Crossref: Buettcher). |
| 13 | nogueira2019passage | Passage Re-ranking with BERT | https://arxiv.org/abs/1901.04085 | arXiv 2019. Correct. |
| 14 | sun2023chatgpt | Is ChatGPT Good at Search? Investigating Large Language Models as Re-Ranking Agents | https://api.crossref.org/works/10.18653/v1/2023.emnlp-main.923 | EMNLP 2023, pp. 14918–14937. Correct. |
| 15a | chen2024m3 | M3-Embedding: Multi-Linguality, Multi-Functionality, Multi-Granularity Text Embeddings Through Self-Knowledge Distillation | https://api.crossref.org/works/10.18653/v1/2024.findings-acl.137 ; https://arxiv.org/abs/2402.03216 | **Correction**: the published title starts "M3-Embedding:" (the "BGE M3-Embedding" title appears only on the model card). Venue: Findings of ACL 2024, pp. 2318–2335. First author is spelled Jianlv (arXiv) / Jianlyu (Crossref). |
| 15b | baai2024bgererankerv2m3 | BAAI/bge-reranker-v2-m3 model card | https://huggingface.co/BAAI/bge-reranker-v2-m3 | A cross-encoder reranker, 0.6B parameters, built on bge-m3. The card asks users to cite arXiv:2312.15503 (Li et al., "Making Large Language Models A Better Foundation For Dense Retrieval") and the M3 paper. Only the M3 paper is included here; add 2312.15503 if you want to follow the card exactly. |
| 16 | fellegi1969theory | A Theory for Record Linkage | https://api.crossref.org/works/10.1080/01621459.1969.10501049 | JASA 64(328):1183–1210. Correct. |
| 17 | christen2012data | Data Matching: Concepts and Techniques for Record Linkage, Entity Resolution, and Duplicate Detection | https://api.crossref.org/works/10.1007/978-3-642-31164-2 ; https://www.springer.com/9783642430015 | Springer, Data-Centric Systems and Applications series, 2012. Full subtitle added. |
| 18 | papadakis2020blocking | Blocking and Filtering Techniques for Entity Resolution: A Survey | https://api.crossref.org/works/10.1145/3377455 | ACM CSUR 53(2), pp. 1–42 (Crossref gives "A Survey" as the subtitle). Authors: Papadakis, Skoutas, Thanos, Palpanas. |
| 19 | moreau2013provdm | PROV-DM: The PROV Data Model | https://www.w3.org/TR/prov-dm/ | W3C Recommendation, 30 April 2013. Editors Luc Moreau (Southampton) and Paolo Missier (Newcastle). |
| 20 | yu2018spider | Spider: A Large-Scale Human-Labeled Dataset for Complex and Cross-Domain Semantic Parsing and Text-to-SQL Task | https://api.crossref.org/works/10.18653/v1/D18-1425 | EMNLP 2018, pp. 3911–3921, 12 authors. |
| 21 | li2023bird | Can LLM Already Serve as A Database Interface? A BIg Bench for Large-Scale Database Grounded Text-to-SQLs | https://arxiv.org/abs/2305.03111 ; https://mlanthology.org/neurips/2023/li2023neurips-llm | NeurIPS 2023, **Datasets and Benchmarks track** (the PDF is filed as Datasets_and_Benchmarks). 18 authors on arXiv (mlanthology leaves out Rongyu Cao). No pages. |
| 22 | chen2020hybridqa | HybridQA: A Dataset of Multi-Hop Question Answering over Tabular and Textual Data | https://api.crossref.org/works/10.18653/v1/2020.findings-emnlp.91 | Findings of EMNLP 2020, pp. 1026–1036. |
| 23 | zhu2021tatqa | TAT-QA: A Question Answering Benchmark on a Hybrid of Tabular and Textual Content in Finance | https://api.crossref.org/works/10.18653/v1/2021.acl-long.254 | ACL-IJCNLP 2021 (long papers), pp. 3277–3287. |
| 24 | zheng2023judging | Judging LLM-as-a-Judge with MT-Bench and Chatbot Arena | https://proceedings.neurips.cc/paper_files/paper/2023/hash/91f18a1287b398d378ef22505bf41832-Abstract-Datasets_and_Benchmarks.html | NeurIPS 2023, vol. 36, Datasets and Benchmarks track. Correct. |
| 25 | es2024ragas | RAGAs: Automated Evaluation of Retrieval Augmented Generation | https://api.crossref.org/works/10.18653/v1/2024.eacl-demo.16 | EACL 2024 System Demonstrations, pp. 150–158. The published title is styled "RAGAs" (arXiv: "Ragas"). |
| 26 | mcnemar1947note | Note on the Sampling Error of the Difference Between Correlated Proportions or Percentages | https://api.crossref.org/works/10.1007/BF02295996 | Psychometrika 12(2):153–157, 1947. |
| 27 | wilson1927probable | Probable Inference, the Law of Succession, and Statistical Inference | https://api.crossref.org/works/10.1080/01621459.1927.10502953 | JASA 22(158):209–212, 1927. |
| 28a | neo4j2026graphrag | GraphRAG for Python (neo4j-graphrag) documentation | https://neo4j.com/docs/neo4j-graphrag-python/current/ | @misc, urldate 2026-10-16. |
| 28b | neo4j2026llmgraphbuilder | Neo4j LLM Knowledge Graph Builder | https://neo4j.com/labs/genai-ecosystem/llm-graph-builder/ | @misc, urldate 2026-10-16. |
| 29 | llamaindex2026propertygraph | Using a Property Graph Index | https://developers.llamaindex.ai/python/framework/module_guides/indexing/lpg_index_guide/ | The old docs.llamaindex.ai URL now redirects here (301). |
| 30a | angles2008survey | Survey of Graph Database Models | https://api.crossref.org/works/10.1145/1322432.1322433 | ACM CSUR 40(1), pp. 1–39, 2008. |
| 30b | angles2023pgschema | PG-Schema: Schemas for Property Graphs | https://api.crossref.org/works/10.1145/3589778 | Suggested as the newer property-graph reference: Proc. ACM Manag. Data (SIGMOD) 1(2), pp. 1–25, 2023, 21 authors. |
| 31 | banko2007open | Open Information Extraction from the Web | https://www.ijcai.org/Abstract/07/429 ; https://mlanthology.org/ijcai/2007/banko2007ijcai-open | IJCAI 2007, pp. 2670–2676. 5 authors (the 4th is Matthew Broadhead). **Do not use DOI 10.1145/1409360.1409378**: mlanthology attaches it, but Crossref shows it belongs to the 2008 CACM article by Etzioni, Banko, Soderland and Weld. |
| 32 | rahm2001survey | A survey of approaches to automatic schema matching | https://api.crossref.org/works/10.1007/s007780100057 | VLDB Journal 10(4):334–350, 2001 (some indexes give 335–350; Crossref and OpenAlex give 334). |
| 33 | zhu2024llms | LLMs for knowledge graph construction and reasoning: recent capabilities and future opportunities | https://api.crossref.org/works/10.1007/s11280-024-01297-w | World Wide Web 27(5), article 58, 2024. 9 authors. |
| 34 | hevner2004design | Design Science in Information Systems Research | https://api.crossref.org/works/10.2307/25148625 | MIS Quarterly 28(1):75–105, 2004. |
| 35 | shen2015entity | Entity Linking with a Knowledge Base: Issues, Techniques, and Solutions | https://api.crossref.org/works/10.1109/TKDE.2014.2327028 | IEEE TKDE 27(2):443–460, 2015. |
| 36 | dietterich1998approximate | Approximate Statistical Tests for Comparing Supervised Classification Learning Algorithms | https://api.crossref.org/works/10.1162/089976698300017197 | Neural Computation 10(7):1895–1923, 1998. |
| 37 | gao2023retrieval | Retrieval-Augmented Generation for Large Language Models: A Survey | https://arxiv.org/abs/2312.10997 | arXiv 2023, 10 authors, still marked "Ongoing Work". |
| 38 | wang2024survey | A survey on large language model based autonomous agents | https://api.crossref.org/works/10.1007/s11704-024-40231-1 | Frontiers of Computer Science 18(6), article 186345, 2024. 13 authors. |
| 39a | auer2007dbpedia | DBpedia: A Nucleus for a Web of Open Data | https://api.crossref.org/works/10.1007/978-3-540-76298-0_52 | ISWC 2007 + ASWC 2007, LNCS 4825, pp. 722–735. |
| 39b | lehmann2015dbpedia | DBpedia – A large-scale, multilingual knowledge base extracted from Wikipedia | https://api.crossref.org/works/10.3233/SW-140134 | Semantic Web 6(2):167–195, 2015. 11 authors. |
| 39c | vrandecic2014wikidata | Wikidata: a free collaborative knowledgebase | https://api.crossref.org/works/10.1145/2629489 | CACM 57(10):78–85, 2014. |
| 40 | sequeda2024benchmark | A Benchmark to Understand the Role of Knowledge Graphs on Large Language Model's Accuracy for Question Answering on Enterprise SQL Databases | https://api.crossref.org/works/10.1145/3661304.3661901 ; https://arxiv.org/abs/2311.07509 | **Correction**: peer-reviewed version at GRADES-NDA 2024 (ACM), pp. 1–12. The arXiv preprint is from 2023. |
| 41 | ozsoy2025text2cypher | Text2Cypher: Bridging Natural Language and Graph Databases | https://aclanthology.org/2025.genaik-1.11 ; https://arxiv.org/abs/2412.10064 | **Correction**: published at the GenAIK workshop (COLING 2025), pp. 100–108, Abu Dhabi. Authors: Ozsoy, Messallem, Besga, Minneci. |
| 42 | wei2022chain | Chain-of-Thought Prompting Elicits Reasoning in Large Language Models | https://proceedings.neurips.cc/paper/2022/hash/9d5609613524ecf4f15af0f7b31abca4-Abstract-Conference.html | NeurIPS 2022, vol. 35, DOI 10.52202/068431-1800. |
| 43 | liu2024lost | Lost in the Middle: How Language Models Use Long Contexts | https://api.crossref.org/works/10.1162/tacl_a_00638 | TACL 12:157–173, 2024. |
| 44 | xu2024large | Large language models for generative information extraction: a survey | https://api.crossref.org/works/10.1007/s11704-024-40555-y ; https://arxiv.org/abs/2312.17617 | Frontiers of Computer Science 18(6), article 186357, 2024 (arXiv comment: "You can cite the FCS version"). |
| +1 | sun2024thinkongraph | Think-on-Graph: Deep and Responsible Reasoning of Large Language Model on Knowledge Graph | https://arxiv.org/abs/2307.07697 | ICLR 2024 (arXiv comment "Accepted by ICLR 2024"). |
| +2 | luo2024reasoning | Reasoning on Graphs: Faithful and Interpretable Large Language Model Reasoning | https://arxiv.org/abs/2310.01061 | ICLR 2024 (arXiv comment). |
| +3 | jiang2023structgpt | StructGPT: A General Framework for Large Language Model to Reason over Structured Data | https://api.crossref.org/works/10.18653/v1/2023.emnlp-main.574 | EMNLP 2023, pp. 9237–9251. Reasons over KGs, tables and databases through tool interfaces. |
| +4 | zhang2024extract | Extract, Define, Canonicalize: An LLM-based Framework for Knowledge Graph Construction | https://api.crossref.org/works/10.18653/v1/2024.emnlp-main.548 | EMNLP 2024, pp. 9820–9836. |
| +5 | bruch2023analysis | An Analysis of Fusion Functions for Hybrid Retrieval | https://api.crossref.org/works/10.1145/3596512 | ACM TOIS 42(1), pp. 1–35, 2023. Studies hybrid lexical + semantic fusion (convex combination vs RRF). |
| +6 | kollegger2025agentic | Agentic Knowledge Graph Construction (DeepLearning.AI short course) | https://www.deeplearning.ai/short-courses/agentic-knowledge-graph-construction/ ; https://community.deeplearning.ai/t/new-course-enroll-in-agentic-knowledge-graph-construction/877852 | Instructor: Andreas Kollegger (Neo4j), the only instructor named. Announced 27 Aug 2025. Uses Google's ADK. Data: product and supplier CSVs plus product reviews. The course page itself does not name the furniture domain or list the exact CSV files (products, assemblies, parts, suppliers), so that detail is unverified. |
| +7 | google2026adk | Agent Development Kit (ADK) documentation | https://adk.dev/ | google.github.io/adk-docs/ now redirects (301) to adk.dev. Open-source framework from Google. |

## Dropped

None. Every candidate was confirmed.

## Corrections to the candidate list

- **#7 Peng et al.** is no longer only an arXiv paper: it is published in ACM TOIS (2025/2026).
- **#9 Toolformer**: the NeurIPS version has 9 authors (adds Eric Hambro).
- **#10 BM25**: 3(4):333–389. Crossref's metadata disagrees.
- **#15 BGE M3**: the title is "M3-Embedding: …" in Findings of ACL 2024, not "BGE M3-Embedding".
- **#21 BIRD**: it is in the Datasets and Benchmarks track.
- **#30**: PG-Schema (SIGMOD 2023) is added alongside Angles & Gutierrez 2008.
- **#31 Banko**: has no DOI. The DOI that circulates for it belongs to a different CACM article.
- **#40 Sequeda et al.**: peer-reviewed at GRADES-NDA 2024.
- **#41 Text2Cypher**: peer-reviewed at the GenAIK workshop 2025.
- **#44**: Xu et al. is published in Frontiers of Computer Science 2024.
