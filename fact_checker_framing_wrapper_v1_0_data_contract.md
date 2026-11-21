# Fact-Checker & Framing Wrapper: v1.0 Data Contract

**Version:** 1.0 (Decoupled Architecture)

**Target Architecture:** OpenRouter Structured Outputs (`json_schema` strict mode)

**Purpose:** Defines the typed JSON contract between the live retrieval/LLM pipeline and the frontend application, separating objective fact-checking (`claims`) from outlet-level media framing (`framing`).

---

## 1. Core Architectural Principle: Separation of Concerns

1. **`claims` = Objective Fact-Checker Domain:**
   * Evaluates verifiable statements extracted from the input (`SINGLE_CLAIM` or `ARTICLE`) against primary evidence.
   * Contains **zero** media-framing or outlet-spin metadata.
   * Scored by `salience_score` (`0.0` to `1.0`) and `centrality` (`CORE` vs. `SUPPORTING`) so the UI surfaces only the most critical facts first.
   * Each claim's `response` is capped at **one clear sentence** with `[1]` citation markers mapped to `sources[]`.

2. **`framing` = Outlet-Level Narrative Domain:**
   * Lives strictly at the **top level** alongside `claims`—never nested inside or coupled to individual `claim_id`s.
   * Evaluates how news outlets package, emphasize, or distort the **story/topic as a whole** through their headlines and editorial framing.

3. **Top-Level Macro Verdicts:**
   * `overall_verdict` summarizes the factual accuracy of the input.
   * `framing.verdict` summarizes the cross-outlet media framing landscape.

4. **Strict Enum Taxonomies ($\le$ 5 Categories):**
   * Both factuality scores and framing labels are locked to 5-value enums (including `UNVERIFIED` / `INSUFFICIENT_DATA`) to prevent hallucinated verdicts.

---

## 2. Finalized JSON Schema (v1.0)

Share this schema directly with your backend partner to enforce via OpenRouter's `response_format: { type: "json_schema", strict: true }`.

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "title": "FactCheckAndFramingPayload",
  "type": "object",
  "required": ["meta", "overall_verdict", "claims", "framing"],
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
          "description": "1-2 sentences explaining the overall factual verdict (what holds up vs. what breaks down)."
        }
      }
    },
    "claims": {
      "type": "array",
      "description": "Purely factual verification items, ordered descending by salience_score.",
      "items": {
        "type": "object",
        "required": [
          "id",
          "text",
          "salience_score",
          "centrality",
          "score",
          "response",
          "sources"
        ],
        "additionalProperties": false,
        "properties": {
          "id": {
            "type": "string",
            "description": "Unique identifier, e.g., 'claim-1', 'claim-2'."
          },
          "text": {
            "type": "string",
            "description": "The isolated, verifiable claim text."
          },
          "salience_score": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "description": "Importance of this claim to the core thesis of the input (1.0 = central thesis, 0.1 = minor detail)."
          },
          "centrality": {
            "type": "string",
            "enum": ["CORE", "SUPPORTING"],
            "description": "CORE if salience_score >= 0.75; SUPPORTING otherwise."
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
          }
        }
      }
    },
    "framing": {
      "type": "object",
      "description": "Top-level outlet framing analysis across the story/topic as a whole.",
      "required": ["verdict", "summary", "outlets"],
      "additionalProperties": false,
      "properties": {
        "verdict": {
          "type": "string",
          "enum": ["BALANCED", "NEUTRAL", "SELECTIVE", "SENSATIONALIZED", "INVERTED"]
        },
        "summary": {
          "type": "string",
          "description": "1-2 sentences explaining how media outlets frame the story overall."
        },
        "outlets": {
          "type": "array",
          "description": "Outlet-level coverage and headline framing evaluations.",
          "items": {
            "type": "object",
            "required": ["source", "headline", "url", "framing_verdict", "framing_note"],
            "additionalProperties": false,
            "properties": {
              "source": {
                "type": "string",
                "description": "News outlet name."
              },
              "headline": {
                "type": "string",
                "description": "Actual headline from live search."
              },
              "url": {
                "type": "string",
                "description": "Direct URL to the outlet's article."
              },
              "framing_verdict": {
                "type": "string",
                "enum": ["BALANCED", "NEUTRAL", "SELECTIVE", "SENSATIONALIZED", "INVERTED"]
              },
              "framing_note": {
                "type": "string",
                "description": "1-sentence note explaining how this outlet's headline/angle frames the story."
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

## 3. Enum Definitions & Rubric

### A. Factuality Score (`overall_verdict.score` & `claims[].score`)

| Enum Value | Definition |
| :--- | :--- |
| `TRUE` | Accurate and corroborated by reliable primary sources with no material omissions. |
| `MOSTLY_TRUE` | Core assertion is accurate, though minor details lack precision or require brief context. |
| `MOSTLY_FALSE` | Contains an element of truth (e.g., a real statistic) but uses it to support an inaccurate conclusion. |
| `FALSE` | Directly contradicted by verifiable evidence or primary records. |
| `UNVERIFIED` | Insufficient live data, breaking event, or purely subjective opinion that cannot be fact-checked. |

### B. Outlet Framing Verdict (`framing.verdict` & `framing.outlets[].framing_verdict`)

| Enum Value | Definition |
| :--- | :--- |
| `BALANCED` | Presents key facts proportionately alongside relevant trade-offs or caveats. |
| `NEUTRAL` | Straightforward, matter-of-fact reporting with minimal editorial slant. |
| `SELECTIVE` | Highlights one true slice of the story while omitting crucial context or counter-evidence. |
| `SENSATIONALIZED` | Uses emotionally charged, exaggerated, or alarmist language beyond what the facts warrant. |
| `INVERTED` | Frames the story in a way that implies the opposite of the underlying facts. |

---

## 4. Mock `.json` Payloads

### Mock A: Single Tweet (`SINGLE_CLAIM`)

```json
{
  "meta": {
    "input_type": "SINGLE_CLAIM",
    "topic_summary": "A viral post claiming NASA's James Webb Space Telescope proved a rocky exoplanet 40 light-years away can support liquid oceans.",
    "total_claims_extracted": 2
  },
  "overall_verdict": {
    "score": "MOSTLY_TRUE",
    "summary": "JWST detected hints of water vapor near rocky exoplanet GJ 486 b, though its 800°F surface temperature makes liquid oceans impossible."
  },
  "claims": [
    {
      "id": "claim-1",
      "text": "NASA's James Webb Space Telescope detected traces of water vapor near a rocky exoplanet roughly 40 light-years away (GJ 486 b).",
      "salience_score": 0.96,
      "centrality": "CORE",
      "score": "MOSTLY_TRUE",
      "response": "JWST detected hints of water vapor around rocky exoplanet GJ 486 b (26 light-years away), though astronomers noted the signal could also originate from cool starspots on its host star rather than a planetary atmosphere [1][2].",
      "sources": [
        {
          "id": 1,
          "name": "NASA Webb Mission Team",
          "url": "https://science.nasa.gov/missions/webb/webb-finds-water-vapor-but-from-a-rocky-planet-or-its-star/"
        },
        {
          "id": 2,
          "name": "The Astrophysical Journal Letters",
          "url": "https://iopscience.iop.org/journal/2041-8205"
        }
      ]
    },
    {
      "id": "claim-2",
      "text": "The discovery proves the exoplanet can support liquid oceans right now.",
      "salience_score": 0.91,
      "centrality": "CORE",
      "score": "FALSE",
      "response": "GJ 486 b has an estimated surface temperature of roughly 800 degrees Fahrenheit (430 degrees Celsius), making liquid water oceans physically impossible on its surface [1].",
      "sources": [
        {
          "id": 1,
          "name": "NASA Webb Mission Team",
          "url": "https://science.nasa.gov/missions/webb/webb-finds-water-vapor-but-from-a-rocky-planet-or-its-star/"
        }
      ]
    }
  ],
  "framing": {
    "verdict": "SENSATIONALIZED",
    "summary": "While science outlets highlighted the ambiguity between planetary atmosphere and starspot interference, viral social posts and tabloid headlines extrapolated water vapor into habitability.",
    "outlets": [
      {
        "source": "Space.com",
        "headline": "James Webb Space Telescope spots hints of water on scorching rocky exoplanet",
        "url": "https://www.space.com/james-webb-space-telescope-rocky-exoplanet-water-vapor",
        "framing_verdict": "BALANCED",
        "framing_note": "Accurately pairs the water vapor detection with the planet's extreme heat and starspot caveat."
      },
      {
        "source": "Daily Star Science",
        "headline": "Alien ocean world found by NASA telescope just steps away from Earth",
        "url": "https://www.dailystar.co.uk/news/weird-news/nasa-webb-ocean-planet-discovery",
        "framing_verdict": "INVERTED",
        "framing_note": "Presents an 800°F inhospitable rock as a liquid-ocean world."
      },
      {
        "source": "Reuters Science",
        "headline": "Webb telescope detects water vapor around rocky planet, or maybe its star",
        "url": "https://www.reuters.com/lifestyle/science/webb-telescope-detects-water-vapor-2023-05-01/",
        "framing_verdict": "NEUTRAL",
        "framing_note": "Directly reflects the dual hypothesis stated in the peer-reviewed study."
      }
    ]
  }
}
```

### Mock B: Multi-Claim Article (`ARTICLE`)

```json
{
  "meta": {
    "input_type": "ARTICLE",
    "topic_summary": "Policy analysis examining traffic, commuter demographics, retail spending, and air-quality effects after downtown congestion pricing launches.",
    "total_claims_extracted": 34
  },
  "overall_verdict": {
    "score": "MOSTLY_TRUE",
    "summary": "Downtown traffic reductions and transit commuter ratios are verified by municipal audits, while claims of retail collapse and total pollution elimination are unsupported."
  },
  "claims": [
    {
      "id": "claim-1",
      "text": "Downtown congestion pricing zones in cities like London, Stockholm, and New York reduced central business district vehicle traffic by 10% to 20% within the first year.",
      "salience_score": 0.97,
      "centrality": "CORE",
      "score": "TRUE",
      "response": "Municipal transport audits in Stockholm, London, and early Manhattan tolling data confirm sustained central-zone traffic reductions between 12% and 20% following launch [1][2].",
      "sources": [
        {
          "id": 1,
          "name": "Metropolitan Transportation Authority (MTA) Traffic Data",
          "url": "https://new.mta.info/project/CBDTP"
        },
        {
          "id": 2,
          "name": "Transport for London (TfL) Congestion Charge Evaluation",
          "url": "https://tfl.gov.uk/modes/driving/congestion-charge"
        }
      ]
    },
    {
      "id": "claim-2",
      "text": "Over 85% of outer-borough and suburban commuters traveling into Manhattan's central business district commute by public transit rather than private car.",
      "salience_score": 0.93,
      "centrality": "CORE",
      "score": "TRUE",
      "response": "U.S. Census Bureau and Tri-State Transportation Campaign analyses show roughly 85% to 88% of outer-borough workers commuting into Manhattan's core use subway, bus, or commuter rail, while under 2% of low-income outer-borough residents drive into the zone [3].",
      "sources": [
        {
          "id": 3,
          "name": "Community Service Society / U.S. Census Commuter Analysis",
          "url": "https://www.cssny.org/news/entry/congestion-pricing-outer-borough-workers"
        }
      ]
    },
    {
      "id": "claim-3",
      "text": "Retail foot traffic and restaurant spending inside congestion zones collapsed by 30% due to drivers avoiding downtown businesses.",
      "salience_score": 0.90,
      "centrality": "CORE",
      "score": "FALSE",
      "response": "Credit card transaction logs and pedestrian counter data in Stockholm, London, and Manhattan showed stable or slightly higher downtown retail foot traffic after toll implementation, though specific auto-dependent businesses saw localized dips [1][4].",
      "sources": [
        {
          "id": 1,
          "name": "MTA Central Business District Economic Tracker",
          "url": "https://new.mta.info/project/CBDTP"
        },
        {
          "id": 4,
          "name": "Regional Plan Association (RPA) Retail Study",
          "url": "https://rpa.org/work/reports/congestion-pricing-economic-impacts"
        }
      ]
    },
    {
      "id": "claim-4",
      "text": "Congestion pricing permanently eliminated particulate air pollution across surrounding residential neighborhoods.",
      "salience_score": 0.84,
      "centrality": "CORE",
      "score": "MOSTLY_FALSE",
      "response": "While central-zone nitrogen dioxide and tailpipe emissions declined measurably, studies found mixed air-quality outcomes along peripheral highway corridors where rerouted truck traffic increased [2][5].",
      "sources": [
        {
          "id": 2,
          "name": "Transport for London Air Quality Monitoring",
          "url": "https://tfl.gov.uk/modes/driving/congestion-charge"
        },
        {
          "id": 5,
          "name": "Environmental Defense Fund Urban Air Study",
          "url": "https://www.edf.org/airquality"
        }
      ]
    }
  ],
  "framing": {
    "verdict": "SELECTIVE",
    "summary": "News outlets rely on accurate underlying metrics but select sharply different slices of the story—pro-transit outlets emphasize aggregate commuter percentages and bus speeds, while anti-toll outlets spotlight peripheral truck rerouting and edge-case driver costs.",
    "outlets": [
      {
        "source": "The New York Times",
        "headline": "Subway riders see faster buses and cleaner streets in early congestion pricing data",
        "url": "https://www.nytimes.com/2025/02/10/nyregion/congestion-pricing-traffic-transit.html",
        "framing_verdict": "SELECTIVE",
        "framing_note": "Centers the 85% transit commuter majority while giving minimal space to shift workers lacking rail access."
      },
      {
        "source": "New York Post",
        "headline": "Midtown diners and small shops slam congestion toll as working drivers get crushed",
        "url": "https://nypost.com/2025/02/12/metro/midtown-businesses-slam-congestion-toll/",
        "framing_verdict": "SENSATIONALIZED",
        "framing_note": "Uses anecdotal merchant quotes to imply broad economic collapse contradicted by aggregate card data."
      },
      {
        "source": "Gothamist",
        "headline": "Manhattan traffic drops 15%, but Bronx advocates warn of highway truck spillover",
        "url": "https://gothamist.com/news/manhattan-traffic-drops-bronx-highway-spillover",
        "framing_verdict": "BALANCED",
        "framing_note": "Pairs core traffic reductions with verified peripheral air-quality trade-offs."
      }
    ]
  }
}
```