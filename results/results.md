| Mutation | N | Exact line (ours / panic) | ±1 line (ours / panic) | Exact repair | Equivalent repair* | Valid repair | Avg errors reported (ours / panic) | Single diagnostic |
|---|---|---|---|---|---|---|---|---|
| drop_semicolon | 60 | 100% / 100% | 100% / 100% | 100% | 100% | 100% | 1.00 / 1.85 | 100% |
| drop_close_paren | 60 | 100% / 100% | 100% / 100% | 95% | 95% | 100% | 1.00 / 3.10 | 100% |
| drop_close_brace | 60 | 100% / 40% | 100% / 53% | 100% | 100% | 100% | 1.00 / 1.92 | 100% |
| drop_open_paren | 60 | 100% / 100% | 100% / 100% | 98% | 98% | 100% | 1.00 / 1.23 | 100% |
| extra_token | 60 | 100% / 100% | 100% / 100% | 88% | 88% | 100% | 1.00 / 1.30 | 100% |
| keyword_typo | 60 | 100% / 100% | 100% / 100% | 100% | 100% | 100% | 1.00 / 1.68 | 100% |
| drop_operator | 60 | 100% / 100% | 100% / 100% | 17% | 100% | 100% | 1.00 / 1.00 | 100% |
| random_delete | 60 | 100% / 98% | 100% / 98% | 82% | 90% | 100% | 1.00 / 1.83 | 100% |
| **ALL** | 480 | 100% / 92% | 100% / 94% | 85% | 96% | 100% | 1.00 / 1.74 | 100% |

\* Equivalent repair: identical token sequence up to interchangeable tokens (any operator for a deleted operator, any type for a deleted type).

Confidence calibration (share of correct repairs at each confidence level):

| Confidence | N | Correct repair |
|---|---|---|
| high | 306 | 99% |
| medium | 135 | 95% |
| low | 39 | 79% |
