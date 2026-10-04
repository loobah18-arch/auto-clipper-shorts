---
name: viral-outlier-analyzer
description: Analyzes creator posts, calculates outlier engagement multipliers, and reverse-engineers winning hooks, formats, and comment triggers.
---

# Viral Outlier Content Analyzer

This skill replaces content guesswork by quantitatively identifying and deconstructing extreme outlier posts from benchmark creators.

## Methodology

### 1. Benchmark & Multiplier Calculation
* Pull or ingest the last 15–30 posts of the specified creator or niche.
* Calculate the creator's **Baseline Engagement** (median views, likes, or comments).
* Calculate the **Outlier Multiplier** for each post:
  $$\text{Multiplier} = \frac{\text{Post Performance}}{\text{Creator Median Baseline}}$$
* Filter for top outliers ($\ge 2.5\times$ baseline, flagging viral home runs $\ge 5\times$).

### 2. The 4-Part Winning Anatomy Deconstruction
For each top-ranked outlier, dissect:

1. **The Hook Architecture (First 3 seconds / First line):**
   - Pattern Interrupt: What expectation was broken?
   - Emotional Tension: Fear of missing out, contrarian take, undeniable social proof, or intense curiosity gap.
   - Pacing: Word count and speed of the opening hook.

2. **The Format & Structure:**
   - Framework used: (e.g. Teardown, Story-to-Lesson, Step-by-Step System, Warning/Mistake).
   - Visual rhythm: Slide progression or video cut frequency.
   - Information density: How complex concepts were simplified into digestible bites.

3. **The Comment Trigger (The virality engine):**
   - What specific question, controversy, opinion split, or free resource offer caused users to stop and comment?
   - High comment-to-like ratio analysis: Why did it prompt debate instead of just passive scrolling?

4. **Actionable Replication Blueprint:**
   - A direct, plug-and-play template tailored to your specific niche, keeping the psychological mechanism intact while changing the subject matter.

---

## Output Format

1. **Outlier Scoreboard:** Table of top posts with views, engagement, and outlier multipliers.
2. **Deep-Dive Deconstructions:** Breakdown of the #1 and #2 winning outliers.
3. **Ready-to-Use Concept Bank:** 3 actionable post/carousel/short concepts using the proven winning mechanics.
