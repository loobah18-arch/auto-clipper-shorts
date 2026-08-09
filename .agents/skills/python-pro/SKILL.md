---
name: python-pro
description: Expert Python engineering playbook for high-performance, asynchronous workflows, resilient subprocess management, type safety, and debugging.
---

# Python Pro Engineering Skill

This skill provides battle-tested guidelines and patterns for advanced Python application development.

## 1. Resilient Subprocess & Streaming
When executing external command-line tools (e.g., ffmpeg, yt-dlp, git):
- Always use `subprocess.run(cmd, capture_output=True, text=True, check=True)` for predictable synchronous calls.
- Catch `subprocess.CalledProcessError` specifically and inspect `e.stderr` and `e.stdout` for actionable failure causes.
- Use explicit non-zero exit handling and timeouts to prevent hung processes:
  ```python
  try:
      res = subprocess.run(cmd, timeout=120, capture_output=True, text=True, check=True)
  except subprocess.TimeoutExpired:
      log("Command timed out after 120s")
  except subprocess.CalledProcessError as e:
      log(f"Process failed (code {e.returncode}): {e.stderr}")
  ```

## 2. Robust HTTP & API Streaming
- Always specify timeouts on `urllib.request.urlopen(req, timeout=30)` or `requests.get(url, timeout=30)`.
- Stream large file downloads directly to disk in chunks to avoid out-of-memory errors:
  ```python
  with urllib.request.urlopen(req, timeout=60) as resp, open(dest_path, "wb") as f:
      shutil.copyfileobj(resp, f, length=64 * 1024)
  ```
- Use resilient retries with exponential backoff for third-party APIs (Groq, OpenAI, YouTube).

## 3. Type Safety & Validation
- Use modern Python type hints (`dict[str, Any]`, `list[Path]`, `tuple[str, int]`).
- Validate JSON structures defensively using `.get(key, default)` and schema checks before accessing nested keys.
