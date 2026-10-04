---
name: morning-executive-brief
description: Generates a single-page 8 AM morning executive briefing covering today's agenda, action-required emails, in-flight project milestones, and top priority tasks.
---

# Morning Executive Brief

Consolidates all high-friction morning checks into a single clean summary page so you start your day with coffee and zero tab fatigue.

## Core Modules

1. **📅 Today's Agenda & Critical Commitments**:
   - Time-blocked calendar view.
   - Any scheduled calls, deadlines, or hard delivery commitments.

2. **📬 High-Priority Communications**:
   - Important unread messages or notifications requiring your personal response.
   - Filter out noise, newsletters, and informational updates. Highlight action items.

3. **⚡ In-Flight Project Status**:
   - Current state of active coding tasks and cloud workflows from `memory/context.md`.
   - Running GitHub Actions workflows, background scripts, or deployment status.

4. **🎯 The Big 3 (Priority Focus Block)**:
   - The 3 highest-leverage tasks to accomplish today before opening new requests.

---

## Automation via Scheduler

To trigger this automatically at 8:00 AM every morning in Antigravity:
```text
/schedule CronExpression="0 8 * * *" Prompt="Generate my Morning Executive Brief for today. Pull my current project status from context.md, check any deadlines due this week, and outline the top 3 focus priorities for today." IsDaemon=true
```
