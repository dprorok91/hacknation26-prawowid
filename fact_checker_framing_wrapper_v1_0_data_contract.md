# Fact-Checker & Framing Wrapper: v1.0 Data Contract

**Version:** 1.0 (MVP)  
**Target Architecture:** OpenRouter Structured Outputs (`json_schema` strict mode)  
**Purpose:** Defines the typed JSON contract between the live retrieval/LLM pipeline and the frontend application.

---

## 1. Agreed Architectural Decisions

1. **Dual Input Support:** The backend accepts either a **single short claim** (e.g., a tweet or quote) or a **full web article/URL** (decomposed into multiple claims). In both cases, the output uses the exact same schema—a single tweet simply produces a `claims` array of length `1`.
2. **Controversy Flag Removed:** Dropped completely to keep the model focused on verifiable truth and concrete framing techniques rather than subjective controversy scoring.
3. **Top-Level Verdicts for Both Factuality & Framing:** The root object provides an overarching `verdict` for both objective accuracy and overall media framing, alongside concise natural-language takeaways explaining *what* held up and *what* was spun.
4. **Claim Centrality / Salience (`salience_score`):** Rather than overloading the user with 50 trivial statements from a long article, the LLM extracts only key claims and assigns a `salience_score` (`0.0` to `1.0`) plus a `centrality` tier (`CORE` vs. `SUPPORTING`). This lets the frontend show the top 3–4 core claims by default and collapse minor ones. *(Note: Named `salience_score` rather than `sentiment_score` so the LLM scores narrative importance rather than emotional positivity/negativity).*
5. **1-Sentence Response Limit:** Each claim's `response` is strictly capped at **one clear sentence** with inline `[1]` citation markers mapped to a structured `sources` list.
6. **Strict Enum Taxonomies ($\le$ 5 Categories):** Both claim truth scores and framing labels are locked to 5-value enums (including `UNVERIFIED` / `INSUFFICIENT_DATA`) to prevent hallucinated verdicts.
7. **Delivery Mode (Batch Now, Stream-Ready Later):** Keys are ordered top-down (`overall_verdict` $\rightarrow$ `overall_framing` $\rightarrow$ `claims`) so the frontend can ingest full `.json` files today and transition to partial JSON streaming later without changing the data model.

---

## 2. Finalized JSON Schema (v1.0)

Share this schema directly with your backend partner to enforce via OpenRouter's `response_format: { type: "json_schema", strict: true }`.

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "title": "FactCheckAndFramingPayload",
  "type": "object",
  "required": ["meta", "overall_verdict", "overall_framing", "claims"],
  "additionalProperties": false,
  "properties": {
    "meta": {
      "type": "object",
      "required": ["input_type", "topic_summary", "total_claims_extracted"],
      "additionalProperties": false,
      "properties": {
        "input_type": {
          "type": "string",
          "enum": ["SINGLE_CLAIM", "ARTICLE"],
          "description": "Distinguishes a standalone tweet/statement from a multi-claim article."
        },
        "topic_summary": {
          "type": "string",
          "description": "Neutral 1-sentence summary of the subject matter."
        },
        "total_claims_extracted": {
          "type": "integer",
          "description": "Total key claims evaluated (recommended cap: max 8-10 for articles)."
        }
      }
    },
    "overall_verdict": {
      "type": "object",
      "required": ["score", "summary"],
      "additionalProperties": false,
      "properties": {
        "score": {
          "type": "string",
          "enum": ["TRUE", "MOSTLY_TRUE", "MOSTLY_FALSE", "FALSE", "UNVERIFIED"]
        },
        "summary": {
          "type": "string",
          "description": "1-2 sentences explaining the macro verdict (e.g., what parts are accurate vs. where the claim breaks down)."
        }
      }
    },
    "overall_framing": {
      "type": "object",
      "required": ["verdict", "summary"],
      "additionalProperties": false,
      "properties": {
        "verdict": {
          "type": "string",
          "enum": ["BALANCED", "SELECTIVE", "SENSATIONALIZED", "MISLEADING_FRAMING", "INSUFFICIENT_DATA"]
        },
        "summary": {
          "type": "string",
          "description": "1-2 sentences explaining how coverage frames the story (e.g., which angle is emphasized or omitted)."
        }
      }
    },
    "claims": {
      "type": "array",
      "description": "Ordered descending by salience_score so the most critical claims appear first.",
      "items": {
        "type": "object",
        "required": [
          "id",
          "text",
          "salience_score",
          "centrality",
          "score",
          "response",
          "sources",
          "framing"
        ],
        "additionalProperties": false,
        "properties": {
          "id": {
            "type": "string",
            "description": "Unique identifier, e.g., 'c1', 'c2'."
          },
          "text": {
            "type": "string",
            "description": "The isolated, verifiable claim text."
          },
          "salience_score": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "description": "Importance of this claim to the core thesis of the input (1.0 = central thesis, 0.1 = minor tangential detail)."
          },
          "centrality": {
            "type": "string",
            "enum": ["CORE", "SUPPORTING"],
            "description": "CORE if salience_score >= 0.75 (primary claims to surface immediately); SUPPORTING otherwise."
          },
          "score": {
            "type": "string",
            "enum": ["TRUE", "MOSTLY_TRUE", "MOSTLY_FALSE", "FALSE", "UNVERIFIED"]
          },
          "response": {
            "type": "string",
            "description": "STRICTLY ONE SENTENCE explaining the fact-check verdict with inline source markers like [1], [2]."
          },
          "sources": {
            "type": "array",
            "items": {
              "type": "object",
              "required": ["id", "name", "url"],
              "additionalProperties": false,
              "properties": {
                "id": {
                  "type": "integer",
                  "description": "Matches inline citation marker [1], [2]."
                },
                "name": {
                  "type": "string",
                  "description": "Publisher or primary source name."
                },
                "url": {
                  "type": "string",
                  "description": "Direct URL to the source."
                }
              }
            }
          },
          "framing": {
            "type": "object",
            "required": ["verdict", "note", "outlets"],
            "additionalProperties": false,
            "properties": {
              "verdict": {
                "type": "string",
                "enum": ["BALANCED", "SELECTIVE", "SENSATIONALIZED", "MISLEADING_FRAMING", "INSUFFICIENT_DATA"]
              },
              "note": {
                "type": "string",
                "description": "1-sentence explanation of how outlets frame this specific claim."
              },
              "outlets": {
                "type": "array",
                "items": {
                  "type": "object",
                  "required": ["source", "headline", "framing_tag"],
                  "additionalProperties": false,
                  "properties": {
                    "source": {
                      "type": "string",
                      "description": "News outlet name."
                    },
                    "headline": {
                      "type": "string",
                      "description": "Actual headline or framing snippet from the live search."
                    },
                    "framing_tag": {
                      "type": "string",
                      "enum": ["BALANCED", "SELECTIVE", "SENSATIONALIZED", "MISLEADING_FRAMING", "INSUFFICIENT_DATA"]
                    }
                  }
                }
              }
            }
          }
        }
      }
    }
  }
}
```

---

## 3. Enum Definitions & Rubric (For Prompt & UI Consistency)

### A. Factuality Score (`overall_verdict.score` & `claims[].score`)
| Enum Value | Definition |
| :--- | :--- |
| `TRUE` | Accurate and corroborated by reliable primary sources with no material omissions. |
| `MOSTLY_TRUE` | Core assertion is accurate, though minor details lack precision or require brief context. |
| `MOSTLY_FALSE` | Contains an element of truth (e.g., a real statistic) but uses it to support an inaccurate conclusion. |
| `FALSE` | Directly contradicted by verifiable evidence or primary records. |
| `UNVERIFIED` | Insufficient live data, breaking event, or purely subjective opinion that cannot be fact-checked. |

### B. Framing Verdict (`overall_framing.verdict`, `claims[].framing.verdict`, `outlets[].framing_tag`)
| Enum Value | Definition |
| :--- | :--- |
| `BALANCED` | Presents key facts proportionately without loaded language or major omissions. |
| `SELECTIVE` | Highlights one true aspect of the story while omitting crucial context or counter-evidence (cherry-picking). |
| `SENSATIONALIZED` | Uses emotionally charged, exaggerated, or alarmist language beyond what the facts warrant. |
| `MISLEADING_FRAMING` | Presents the facts in a way that implies the opposite of reality or attributes false intent. |
| `INSUFFICIENT_DATA` | Too few live external headlines found to evaluate cross-outlet framing. |

---

## 4. Mock `.json` Payloads for Immediate UI Development

You can use these two mock files immediately to build and test both input modes (a viral tweet vs. a multi-claim news article).

### Mock A: Single Tweet / Claim (`SINGLE_CLAIM`)
```json
{
  "meta": {
    "input_type": "SINGLE_CLAIM",
    "topic_summary": "A viral social media post claiming a new EU regulation bans all private vehicle repairs starting next month.",
    "total_claims_extracted": 1
  },
  "overall_verdict": {
    "score": "MOSTLY_FALSE",
    "summary": "While the EU did adopt new End-of-Life Vehicle rules, they only apply to scrapping unrepairable wrecks and do not ban private owners from repairing their cars."
  },
  "overall_framing": {
    "verdict": "MISLEADING_FRAMING",
    "summary": "Viral posts and partisan blogs frame routine recycling criteria as a sweeping ban on DIY car ownership, whereas mainstream outlets report it as an industrial waste measure."
  },
  "claims": [
    {
      "id": "c1",
      "text": "The EU has passed a law banning citizens from repairing their own cars starting next month.",
      "salience_score": 1.0,
      "centrality": "CORE",
      "score": "MOSTLY_FALSE",
      "response": "The EU End-of-Life Vehicles regulation sets criteria for when commercial yards must scrap severely damaged cars, explicitly exempting private DIY repairs and vintage vehicles [1][2].",
      "sources": [
        {
          "id": 1,
          "name": "European Commission Official Press Release",
          "url": "https://ec.europa.eu/commission/presscorner/example"
        },
        {
          "id": 2,
          "name": "Reuters Fact Check",
          "url": "https://www.reuters.com/fact-check/example"
        }
      ],
      "framing": {
        "verdict": "MISLEADING_FRAMING",
        "note": "Aggregator blogs invert the regulation's scope by equating commercial salvage rules with a personal repair ban.",
        "outlets": [
          {
            "source": "EuroAuto Watch",
            "headline": "Brussels Bureaucrats Outlaw Driveway Car Repairs in Radical Green Push",
            "framing_tag": "MISLEADING_FRAMING"
          },
          {
            "source": "Reuters",
            "headline": "EU vehicle recycling rules do not ban owners from fixing their cars",
            "framing_tag": "BALANCED"
          },
          {
            "source": "Politico Europe",
            "headline": "Car lobby clashes with Brussels over stricter end-of-life scrapping rules",
            "framing_tag": "SELECTIVE"
          }
        ]
      }
    }
  ]
}
```

### Mock B: Multi-Claim Web Article (`ARTICLE`)
```json
{
  "meta": {
    "input_type": "ARTICLE",
    "topic_summary": "Article reporting on a 15% municipal transit fare hike and claiming ridership has collapsed due to safety concerns.",
    "total_claims_extracted": 3
  },
  "overall_verdict": {
    "score": "MOSTLY_TRUE",
    "summary": "The fare increase figures and budget deficit numbers are accurate, but the claim that ridership collapsed due to crime omits post-pandemic remote work trends."
  },
  "overall_framing": {
    "verdict": "SELECTIVE",
    "summary": "Coverage focuses heavily on fare hikes and isolated crime incidents while omitting that new low-income subsidies keep prices unchanged for 40% of riders."
  },
  "claims": [
    {
      "id": "c1",
      "text": "Metro transit fares will increase by 15% starting January 1 to close a $120M budget shortfall.",
      "salience_score": 0.95,
      "centrality": "CORE",
      "score": "TRUE",
      "response": "Board minutes confirm a 15% base fare hike taking effect January 1 to address a verified $120 million operating deficit [1].",
      "sources": [
        {
          "id": 1,
          "name": "Metropolitan Transit Authority Budget Report",
          "url": "https://mta.example.gov/budget-2026"
        }
      ],
      "framing": {
        "verdict": "SELECTIVE",
        "note": "Most headlines lead with the 15% hike while omitting that discounted passes for low-income riders remain frozen.",
        "outlets": [
          {
            "source": "City Daily Herald",
            "headline": "Commuters Hit With Steep 15% Transit Tax Hike Ahead of New Year",
            "framing_tag": "SENSATIONALIZED"
          },
          {
            "source": "Metro Public Radio",
            "headline": "Transit Board Approves 15% Base Fare Hike, Freezes Low-Income Pass Rates",
            "framing_tag": "BALANCED"
          }
        ]
      }
    },
    {
      "id": "c2",
      "text": "Ridership dropped 30% over the past two years primarily because commuters feel unsafe.",
      "salience_score": 0.85,
      "centrality": "CORE",
      "score": "MOSTLY_FALSE",
      "response": "While ridership remains 30% below pre-2020 baselines, transit surveys attribute over 75% of that drop to hybrid remote work rather than crime [2].",
      "sources": [
        {
          "id": 2,
          "name": "Urban Mobility Institute Annual Survey",
          "url": "https://urbanmobility.example.org/survey"
        }
      ],
      "framing": {
        "verdict": "MISLEADING_FRAMING",
        "note": "Opinion-heavy outlets link overall ridership decline exclusively to crime statistics.",
        "outlets": [
          {
            "source": "The Evening Chronicle",
            "headline": "Lawless Subways Drive 30% of Riders Away From Public Transit",
            "framing_tag": "MISLEADING_FRAMING"
          },
          {
            "source": "Civic Data Journal",
            "headline": "Remote Work Keeps Transit Ridership 30% Below Pre-Pandemic Levels",
            "framing_tag": "BALANCED"
          }
        ]
      }
    },
    {
      "id": "c3",
      "text": "The transit board meeting lasted over four hours before the final vote.",
      "salience_score": 0.2,
      "centrality": "SUPPORTING",
      "score": "TRUE",
      "response": "Official livestream timestamps confirm the public hearing ran for four hours and twelve minutes [1].",
      "sources": [
        {
          "id": 1,
          "name": "Metropolitan Transit Authority Budget Report",
          "url": "https://mta.example.gov/budget-2026"
        }
      ],
      "framing": {
        "verdict": "INSUFFICIENT_DATA",
        "note": "Procedural detail not covered in external headlines.",
        "outlets": []
      }
    }
  ]
}
```

---

## 5. Implementation Notes for Your Backend Partner

* **Prompt Instruction for Claim Filtering:** Instruct the LLM: *"Extract at most 8 claims from articles (1 for single tweets). Prioritize claims central to the headline and thesis (`salience_score` $\ge$ 0.75). Ignore trivial background details unless they directly affect factual accuracy."*
* **Strict 1-Sentence Enforcement:** In the system prompt, specify that `claims[].response` must be a single grammatical sentence under 35 words ending with citation brackets (e.g., `[1]`).
* **Future Streaming Readiness:** Because `meta`, `overall_verdict`, and `overall_framing` appear at the top of the JSON object and `claims` are sorted descending by `salience_score`, switching to streamed JSON later will automatically render the verdicts and highest-importance claims first.