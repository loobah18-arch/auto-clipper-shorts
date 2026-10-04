---
name: brand-deck-generator
description: Converts strategy documents and outlines into slide decks with baked-in brand design tokens and an automated text overflow guard.
---

# Brand Deck Generator with Overflow Guard

Takes a raw strategy memo, bullet points, or document and converts it directly into a clean, presentation-ready deck (HTML/CSS slide deck, Marp markdown, or visual slide outline).

## Design System Tokens

* **Aspect Ratio**: 16:9 widescreen.
* **Palette**:
  - Primary Dark: `#0D1117` (Deep Obsidian Canvas)
  - Text Primary: `#F0F6FC` (High-contrast Off-White)
  - Text Secondary: `#8B949E` (Muted Slate for subheadings)
  - Accent / Focus: `#58A6FF` (Electric Cobalt) or `#3FB950` (Emerald Green)
* **Typography Hierarchy**:
  - Slide Header: Bold, uppercase tracking, max 7 words.
  - Subheader / Takeaway: Sentence case, 1-line thesis.
  - Body Content: 3-column bento card grid or split-screen metric.

---

## The Automated Overflow Guard

Before any slide is presented, it runs through the **Slide Overflow Linter**:

* **Slide Title**: Maximum 8 words / 50 characters.
* **Core Takeaway**: Maximum 15 words / 90 characters.
* **Bullet Points**: Maximum 3-4 bullets per slide.
* **Word Density per Bullet**: Maximum 12 words per bullet.
* **Total Slide Word Count**: Maximum 45 words per content slide.

> If any slide exceeds these thresholds, the linter automatically condenses the prose, strips filler words, or splits the idea across two sequential slides with a clean visual transition.
