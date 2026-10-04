---
name: skill-builder
description: Converts any repeated user workflow, SOP, or instructions into a persistent Antigravity and OpenCode skill in ~/.agents/skills/.
---

# Repeat-to-Skill Builder (SOP Engine)

Rule: **If you do anything more than twice, turn it into a skill.**

This skill automates the creation and validation of new skills so that your future assistants remember the exact procedure without re-explaining.

## Creation Protocol

1. **Capture the Workflow**:
   - Extract the goal, input requirements, step-by-step procedure, constraints, and target output format.
2. **Standardize into `SKILL.md`**:
   - Save directly into `~/.agents/skills/<skill-name>/SKILL.md`.
   - Ensure clean YAML frontmatter:
     ```yaml
     ---
     name: <kebab-case-name>
     description: <Concise, high-clarity 1-line description of what and when to activate>
     ---
     ```
   - Structure:
     - Purpose & Activation Triggers
     - Step-by-Step Execution Rules
     - Quality Checks & Edge Cases
     - Strict Constraints (what NOT to do)
     - Expected Output Format
3. **Verify Compatibility**:
   - Validate frontmatter syntax to ensure both Antigravity (`agy`) and OpenCode auto-discover and load it cleanly.
