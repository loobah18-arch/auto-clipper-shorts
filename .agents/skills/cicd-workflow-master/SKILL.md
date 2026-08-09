---
name: cicd-workflow-master
description: GitHub Actions, CI/CD pipelines, container services, background runners, and automated release orchestration.
---

# CI/CD & GitHub Actions Master Playbook

This skill provides essential guidelines for constructing robust, self-healing GitHub Actions automation workflows.

## 1. Service Containers & Anti-Bot Helpers
- When running external helper servers (like PO token generators or Redis/Postgres) inside GitHub Actions:
  ```yaml
  services:
    bgutil-provider:
      image: brainicism/bgutil-ytdlp-pot-provider:latest
      ports:
        - 4416:4416
  ```
- Always include a healthcheck / ping verification step before running the main application to ensure services are ready:
  ```yaml
  - name: 🔍 Verify Service Health
    run: |
      for i in 1 2 3 4 5; do
        if curl -sf http://127.0.0.1:4416/ping; then break; fi
        sleep 3
      done
  ```

## 2. Automated Git State Persistence
- When pipelines modify local state (history JSONs, logs, databases), commit changes back to the repository using the official GitHub Actions bot:
  ```yaml
  - name: 💾 Commit & Push State
    if: always()
    run: |
      git config --global user.name "github-actions[bot]"
      git config --global user.email "github-actions[bot]@users.noreply.github.com"
      git pull --rebase origin main || true
      git add clip_history.json pipeline_debug.log || true
      git commit -m "🤖 State update [skip ci]" || echo "No changes"
      git push origin main || echo "Nothing to push"
  ```
- Always include `[skip ci]` in automated commits to prevent infinite trigger loops.
