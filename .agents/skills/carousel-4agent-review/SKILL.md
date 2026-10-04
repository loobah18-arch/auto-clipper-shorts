---
name: carousel-4agent-review
description: Multi-agent 4-reviewer pipeline for carousels and social posts (Voice & Tone, Fact-Checking, Bored Scroller Hook, and Visual Layout).
---

# 4-Agent Carousel Review Pipeline

This skill runs an automated 4-agent peer review on any carousel draft or social post before you publish it or even review it yourself.

## Reviewer Agents

### Agent 1: Voice & Tone Auditor
* **Goal**: Protect your authentic human voice and eliminate all AI markers.
* **Checks**:
  - Scans for banned AI vocabulary: "game-changer", "unleash", "delve", "testament", "streamline", "leverage", "in today's fast-paced world", "buckle up".
  - Enforces punctuation rules: Flags em dashes (`—`), excessive exclamation marks, or over-structured robotic parallelism.
  - Rhythm & Cadence: Ensures sentences vary between short, punchy statements (3-6 words) and natural rhythm. Flags corporate jargon.
* **Output**: Exact line replacements that restore an organic, human voice.

### Agent 2: Fact-Checker & Metric Verifier
* **Goal**: Zero hallucinated numbers or sloppy assertions.
* **Checks**:
  - Audits every statistic, dollar figure, percentage, year, and benchmark claim.
  - Flags claims presented as fact without verifiable source or clear mathematical logic (e.g. "grew by 400% in 2 days").
  - Identifies misleading correlations or ambiguous data points.
* **Output**: Verification status for each number, suggested corrections, or warnings to remove unsubstantiated claims.

### Agent 3: Bored Scroller Hook Judge
* **Goal**: Stop the scroll in 1.2 seconds flat.
* **Checks**:
  - Ruthlessly critiques Slide 1 (Hook) and Slide 2 (Re-hook/Stakes) from the perspective of an impatient user skimming on a mobile feed.
  - Checks if the title is generic ("5 Tips For X") vs high-curiosity / high-tension ("The $40,000 mistake 90% of builders make").
  - Evaluates the "So What?" factor: Why does the reader care right now?
  - Scores Hook Power on a scale of 1 to 10.
* **Output**: Hook rating + 3 alternative, high-tension hooks engineered for maximum CTR and drop-off reduction.

### Agent 4: Design & Layout Auditor
* **Goal**: Ensure effortless mobile visual scanability and zero text overflow.
* **Checks**:
  - Slide density: Flags slides exceeding 30-35 words (walls of text kill swipes).
  - Visual hierarchy: Ensures 1 main focal idea per slide (Hero statement > supporting 2-line detail).
  - Swipe momentum: Checks that each slide ends with psychological momentum or an open loop leading into the next slide.
  - Spacing & Line breaks: Formats lines with comfortable vertical breathing room (no 6-line text blocks).
* **Output**: Formatted slide-by-slide layout recommendations and text trims.

---

## Execution Workflow

When a carousel or draft is submitted:
1. Run all 4 reviewer audits concurrently.
2. Produce a consolidated **Audit Card** with the 4 verdicts.
3. Present the **Final Polished Draft**: A fully revised, ready-to-publish version incorporating all fixes.
