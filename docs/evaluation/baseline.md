# Offline evaluation baseline

Dataset version `2026-10-19.1`. `K = 5`. Measured on 2026-10-19 by:

```bash
cd apps/api
uv run python -m nexus.evaluation --suite offline --output /tmp/nexus-eval-offline.json
```

These numbers score the frozen fixture rank lists. They do not measure production retrieval.

## Aggregate

| Metric | Value |
| --- | --- |
| Recall@5 | 0.833333 |
| Precision@5 | 0.240000 |
| MRR@5 | 0.705556 |
| Defined cases | 15 |
| Undefined cases | 1 |

Citation provenance and grounding structure matched the fixture expectations (10 citation cases, 12 grounding cases). Retrieval scores, including the zero-recall cases, did not affect the exit code. The command exited 0.

## Measured artifact

```json
{
  "citations": {
    "cases": [
      {
        "case_id": "c01-supported",
        "citation_valid": true,
        "error_codes": [],
        "expect_citation_valid": true,
        "support_label": "supported"
      },
      {
        "case_id": "c02-spurious",
        "citation_valid": true,
        "error_codes": [],
        "expect_citation_valid": true,
        "support_label": "unsupported"
      },
      {
        "case_id": "c03-missing",
        "citation_valid": false,
        "error_codes": [
          "missing_evidence"
        ],
        "expect_citation_valid": false,
        "support_label": "supported"
      },
      {
        "case_id": "c04-foreign",
        "citation_valid": false,
        "error_codes": [
          "foreign_research"
        ],
        "expect_citation_valid": false,
        "support_label": "supported"
      },
      {
        "case_id": "c05-malformed",
        "citation_valid": false,
        "error_codes": [
          "malformed_id"
        ],
        "expect_citation_valid": false,
        "support_label": "supported"
      },
      {
        "case_id": "c06-pdf",
        "citation_valid": true,
        "error_codes": [],
        "expect_citation_valid": true,
        "support_label": "supported"
      },
      {
        "case_id": "c07-web",
        "citation_valid": true,
        "error_codes": [],
        "expect_citation_valid": true,
        "support_label": "supported"
      },
      {
        "case_id": "c08-unknown",
        "citation_valid": false,
        "error_codes": [
          "unknown_source"
        ],
        "expect_citation_valid": false,
        "support_label": "unsupported"
      },
      {
        "case_id": "c09-duplicate",
        "citation_valid": false,
        "error_codes": [
          "duplicate_id"
        ],
        "expect_citation_valid": false,
        "support_label": "supported"
      },
      {
        "case_id": "c10-scope",
        "citation_valid": false,
        "error_codes": [
          "foreign_research"
        ],
        "expect_citation_valid": false,
        "support_label": "supported"
      }
    ]
  },
  "dataset_version": "2026-10-19.1",
  "grounding": [
    {
      "case_id": "g01-supported",
      "error_code": null,
      "excerpt_count": 1,
      "expect_structurally_valid": true,
      "gold_label": "supported",
      "structural_valid": true
    },
    {
      "case_id": "g02-spurious",
      "error_code": null,
      "excerpt_count": 1,
      "expect_structurally_valid": true,
      "gold_label": "unsupported",
      "structural_valid": true
    },
    {
      "case_id": "g03-insufficient",
      "error_code": null,
      "excerpt_count": 1,
      "expect_structurally_valid": true,
      "gold_label": "insufficient_context",
      "structural_valid": true
    },
    {
      "case_id": "g04-contradictory",
      "error_code": null,
      "excerpt_count": 2,
      "expect_structurally_valid": true,
      "gold_label": "contradictory",
      "structural_valid": true
    },
    {
      "case_id": "g05-uncertain",
      "error_code": null,
      "excerpt_count": 1,
      "expect_structurally_valid": true,
      "gold_label": "insufficient_context",
      "structural_valid": true
    },
    {
      "case_id": "g06-paraphrase",
      "error_code": null,
      "excerpt_count": 1,
      "expect_structurally_valid": true,
      "gold_label": "supported",
      "structural_valid": true
    },
    {
      "case_id": "g07-pdf",
      "error_code": null,
      "excerpt_count": 1,
      "expect_structurally_valid": true,
      "gold_label": "supported",
      "structural_valid": true
    },
    {
      "case_id": "g08-wrong-number",
      "error_code": null,
      "excerpt_count": 1,
      "expect_structurally_valid": true,
      "gold_label": "unsupported",
      "structural_valid": true
    },
    {
      "case_id": "g09-foreign",
      "error_code": "foreign_research",
      "excerpt_count": 0,
      "expect_structurally_valid": false,
      "gold_label": "supported",
      "structural_valid": false
    },
    {
      "case_id": "g10-unknown",
      "error_code": "unknown_citation",
      "excerpt_count": 0,
      "expect_structurally_valid": false,
      "gold_label": "supported",
      "structural_valid": false
    },
    {
      "case_id": "g11-multi",
      "error_code": null,
      "excerpt_count": 2,
      "expect_structurally_valid": true,
      "gold_label": "supported",
      "structural_valid": true
    },
    {
      "case_id": "g12-conflict-noise",
      "error_code": null,
      "excerpt_count": 3,
      "expect_structurally_valid": true,
      "gold_label": "contradictory",
      "structural_valid": true
    }
  ],
  "k": 5,
  "report_version": "1",
  "retrieval": {
    "aggregate": {
      "defined_cases": 15,
      "mrr_at_k": "0.705556",
      "precision_at_k": "0.240000",
      "recall_at_k": "0.833333",
      "undefined_cases": 1
    },
    "cases": [
      {
        "case_id": "q01-single",
        "gold_count": 1,
        "mrr_at_k": "1.000000",
        "precision_at_k": "0.200000",
        "recall_at_k": "1.000000"
      },
      {
        "case_id": "q02-multi",
        "gold_count": 3,
        "mrr_at_k": "1.000000",
        "precision_at_k": "0.600000",
        "recall_at_k": "1.000000"
      },
      {
        "case_id": "q03-paraphrase",
        "gold_count": 2,
        "mrr_at_k": "0.500000",
        "precision_at_k": "0.400000",
        "recall_at_k": "1.000000"
      },
      {
        "case_id": "q04-no-gold",
        "gold_count": 0,
        "mrr_at_k": null,
        "precision_at_k": null,
        "recall_at_k": null
      },
      {
        "case_id": "q05-miss",
        "gold_count": 1,
        "mrr_at_k": "0.000000",
        "precision_at_k": "0.000000",
        "recall_at_k": "0.000000"
      },
      {
        "case_id": "q06-short",
        "gold_count": 1,
        "mrr_at_k": "1.000000",
        "precision_at_k": "0.200000",
        "recall_at_k": "1.000000"
      },
      {
        "case_id": "q07-duplicate",
        "gold_count": 1,
        "mrr_at_k": "1.000000",
        "precision_at_k": "0.200000",
        "recall_at_k": "1.000000"
      },
      {
        "case_id": "q08-rank3",
        "gold_count": 1,
        "mrr_at_k": "0.333333",
        "precision_at_k": "0.200000",
        "recall_at_k": "1.000000"
      },
      {
        "case_id": "q09-half",
        "gold_count": 2,
        "mrr_at_k": "1.000000",
        "precision_at_k": "0.200000",
        "recall_at_k": "0.500000"
      },
      {
        "case_id": "q10-pdf",
        "gold_count": 1,
        "mrr_at_k": "1.000000",
        "precision_at_k": "0.200000",
        "recall_at_k": "1.000000"
      },
      {
        "case_id": "q11-web",
        "gold_count": 1,
        "mrr_at_k": "1.000000",
        "precision_at_k": "0.200000",
        "recall_at_k": "1.000000"
      },
      {
        "case_id": "q12-past-k",
        "gold_count": 1,
        "mrr_at_k": "0.000000",
        "precision_at_k": "0.000000",
        "recall_at_k": "0.000000"
      },
      {
        "case_id": "q13-both-pdf",
        "gold_count": 2,
        "mrr_at_k": "1.000000",
        "precision_at_k": "0.400000",
        "recall_at_k": "1.000000"
      },
      {
        "case_id": "q14-late-pair",
        "gold_count": 2,
        "mrr_at_k": "0.250000",
        "precision_at_k": "0.400000",
        "recall_at_k": "1.000000"
      },
      {
        "case_id": "q15-noise-hit",
        "gold_count": 1,
        "mrr_at_k": "1.000000",
        "precision_at_k": "0.200000",
        "recall_at_k": "1.000000"
      },
      {
        "case_id": "q16-second-only",
        "gold_count": 1,
        "mrr_at_k": "0.500000",
        "precision_at_k": "0.200000",
        "recall_at_k": "1.000000"
      }
    ],
    "k": 5
  },
  "suite": "offline"
}
```
