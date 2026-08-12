# Requirements & Specifications

## Functional Requirements
- **FR-1:** Generate standalone high-definition 1080x1920 60fps vertical short videos with Neural TTS narration in < 30 seconds.
- **FR-2:** Render Wolf avatar in 100% close-up on the left side with active mouth synchronization and realistic hand gestures appearing 1–2 times per video.
- **FR-3:** Render Lion reactor avatar on the right side with intelligent tone detection (shock reaction occurs at most once or 0 times on normal facts).
- **FR-4:** Generate neon word-level karaoke ASS subtitles positioned above the studio avatar panel.
- **FR-5:** Automatic YouTube Data API v3 upload when running on schedule, with `--dry-run` support for testing.
- **FR-6:** Multi-topic catalog rotation with history persistence in `clip_history.json`.

## Non-Functional Requirements
- **NFR-1 (Reliability):** Zero external 2-hour video downloads required for daily runs.
- **NFR-2 (CI/CD Speed):** Workflow execution time < 2 minutes on GitHub Actions standard runner.
- **NFR-3 (Safety):** Zero unauthorized changes to `main` branch, personal device files, or other repositories.
