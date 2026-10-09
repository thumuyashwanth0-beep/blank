# `loanserve` Python Package

This package contains the LoanServe implementation. The repository root README explains project setup and how to run the service; this page links to implementation-level notes for each subpackage.

| Package | What it owns | Guide |
| --- | --- | --- |
| `api` | HTTP endpoints, schema validation, access control, middleware, and API application persistence. | [API implementation](api/README.md) |
| `core` | Loan domain classes, lending rules, calculations, validation, and domain errors. | [Core implementation](core/README.md) |
| `data_access` | CSV cleaning and storage, SQLite loading, and analytical database reports. | [Data access implementation](data_access/README.md) |
| `risk_models` | Feature preparation, classifier training and evaluation, threshold choice, and review queues. | [Risk model implementation](risk_models/README.md) |
| `message_intelligence` | Message normalization, classification, safety checks, extraction, summaries, and escalation. | [Message intelligence implementation](message_intelligence/README.md) |
| `retrieval` | Policy PDF parsing, chunking, vector indexing/search, and citation helpers. | [Retrieval implementation](retrieval/README.md) |
| `assistant` | LangGraph workflow, tool selection/execution, approval interrupt, and thread checkpointing. | [Assistant implementation](assistant/README.md) |

`config/constants.py` holds shared product rules, paths, and runtime settings. Package `__init__.py` files identify Python import packages; they do not perform application startup.
