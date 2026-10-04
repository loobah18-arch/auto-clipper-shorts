---
name: competitor-radar
description: Automated competitive intelligence tracking news, funding, product changes, and user sentiment on Reddit, YouTube, and X, showing only what changed since last run.
---

# Competitor Radar (Diff-Based Intel)

This skill tracks competitor ecosystems and produces an executive diff so you only spend time reading what actually changed since the last check.

## Monitored Signal Channels

1. **Product Launches & Changelogs**: Feature updates, SDK releases, UI redesigns, pricing plan adjustments.
2. **Corporate & Funding News**: Seed/Series rounds, acquisitions, leadership changes, major partnerships.
3. **Real User Sentiment (Reddit, YouTube, X)**:
   - What users love (features getting organic praise).
   - What users hate (breaking bugs, pricing backlash, unfulfilled feature requests).
   - Pain points you can capitalize on immediately.

## Diff State Protocol

* **State File**: `~/.agents/data/competitor_radar_state.json`
* **Workflow**:
  1. Load existing state snapshot (competitor profiles, last known headlines, timestamps).
  2. Search latest signals using web search and developer queries.
  3. Compute the **Delta (Diff)**: Filter out duplicate headlines and previously seen discussions.
  4. Save updated snapshot to `~/.agents/data/competitor_radar_state.json`.

---

## Output Format

### 🚨 What Changed (Delta Report)
* **[Competitor Name] - Product & Tech Updates:**
  - `[NEW]` Summary of feature or pricing change.
* **[Competitor Name] - Funding & Corporate:**
  - `[NEW]` Summary of funding or strategic announcement.
* **[Competitor Name] - Ground-Truth User Sentiment:**
  - **The Praises**: What customers are raving about.
  - **The Complaints / Flaws**: Frustrations and complaints discovered on Reddit / X.

### 🎯 Strategic Exploitation Opportunity
One concrete, actionable counter-move you can take this week based on their weaknesses or user complaints.
