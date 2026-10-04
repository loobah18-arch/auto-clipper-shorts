---
name: correction-learner
description: Automatically commits user corrections, stylistic feedback, and negative constraints into persistent memory (USER.md and patterns.md) to ensure zero-repeat mistakes.
---

# Zero-Repeat Correction & Memory Learner

Rule: **Never give the same feedback twice.**

When you correct an output, specify a stylistic ban (e.g. "no em dashes", "never start with 'in this fast-paced world'"), or adjust a process preference, this skill immediately writes it into long-term persistent memory.

## Storage Hierarchy

1. **User Preferences & Bans -> `USER.md` (`~/.config/opencode/memory/USER.md`)**:
   - Authoritative personal rules.
   - Punctuation preferences (e.g. em-dash bans).
   - Tone, voice, formatting laws that must never be violated.
2. **Operational Learnings & Pitfalls -> `patterns.md` (`~/.config/opencode/memory/patterns.md`)**:
   - Technical gotchas, tool quirks, and domain patterns.
3. **Current RAM & In-Flight Status -> `context.md` (`~/.config/opencode/memory/context.md`)**:
   - Active work, next steps. Must remain < 3KB.

## Auto-Learner Behavior

* Whenever the user says "Don't do X again", "From now on always Y", or corrects a mistake:
  1. Acknowledge the correction concisely.
  2. Append the rule into the appropriate section in `USER.md` or `patterns.md`.
  3. Reference the rule in all subsequent turns.
