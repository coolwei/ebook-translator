# AGENT.md

## Role

Your job is to implement the user’s requested code changes for this project.
**Claude Code will review your work.**

Do not redesign the project.
Do not add unrelated features.
Do not push unless explicitly told.

---

## Project

```txt
Project: Ebook Translator
Path: G:\Projects\ebook-translator
CLI: python -m ebook_translator
Test: python -m pytest tests/ -q --tb=short
Branch: main
```

---

## Start Every Task With

```bat
cd /d G:\Projects\ebook-translator
git status
git log --oneline -10
python -m pytest tests/ -q --tb=short
```

If the working tree is not clean, report it before editing.
Do not overwrite unrelated user changes.

---

## Core Rules

1. Do exactly what the user asked.
2. Keep the diff small.
3. Reuse existing architecture.
4. Add or update tests for behavior changes.
5. Do not call real APIs in tests.
6. Do not commit secrets.
7. Do not push unless explicitly told.

---

## Never Commit

```txt
.env
config.yaml
books/
outputs/
logs/
mcps/
API keys
Bearer tokens
Authorization headers
private endpoints
personal URLs
```

Before finishing, run:

```bat
git status
git diff --stat
git diff
git ls-files .env config.yaml books outputs logs mcps
git grep -n "Bearer " .
git grep -n "Authorization" .
git grep -n "sk-" .
```

Fake test keys like `sk-test` are acceptable only if clearly fake.

---

## Files Usually Involved

```txt
src/ebook_translator/cli.py
src/ebook_translator/config.py
src/ebook_translator/translator.py
src/ebook_translator/scheduler.py
src/ebook_translator/safe_mode.py
src/ebook_translator/fallback.py
src/ebook_translator/translation_log.py
src/ebook_translator/quality.py
src/ebook_translator/checkpoint.py
src/ebook_translator/i18n.py
tests/
README.md
config.example.yaml
PROJECT_STATUS.md
```

Inspect relevant files before editing.

---

## Preserve Existing Features

Do not break:

```txt
EPUB reading
segment extraction
OpenAI-compatible provider
checkpoint / resume
translation cache
retry-failed
retry-quality-failed
quality gate
missing report
bilingual HTML / EPUB export
Traditional Chinese CLI
Windows launcher
large-book safe mode
provider fallback
translation logs
```

---

## Safe Mode Must Preserve

```txt
--batch-size
--cooldown-seconds
--stop-on-rate-limit-count
--limit
--max-segments
--dry-run
```

Rules:

```txt
dry-run must not translate/export/sleep
cooldown only between batches
rate limit stop must preserve checkpoint
completed segments must not be retranslated
each batch should export current progress
```

---

## Provider Fallback Must Preserve

Config format:

```yaml
provider:
  model: "primary-model"
  fallback_models:
    - "fallback-model-1"
    - "fallback-model-2"
```

Rules:

```txt
success keeps current model
failure tries fallback in order
fallback success becomes sticky for next segment
all models fail before recording failed / quality_failed
401 / 403 / 404 / context length fail fast
cache reuse must not depend on model name
```

Fallback triggers:

```txt
empty content
rate limit
429
openai_error
timeout
5xx
quality_failed
simplified_chinese
untranslated_text
markdown_fence
added_prefix
explanation_prefix
```

---

## Translation Logs Must Be Safe

Allowed log data:

```txt
timestamp
book
segment_id
batch_index
model
status
error summary
fallback_from
fallback_to
attempt
duration_ms
```

Never log:

```txt
API key
Bearer token
Authorization header
full request headers
private endpoint URL
```

If log redaction changes, add tests.

---

## Config / CLI / Docs Rules

When adding config fields:

1. Keep backward compatibility.
2. Add safe defaults.
3. Update `config.example.yaml`.
4. Update README.
5. Add tests.

When adding CLI text:

1. Update zh-TW and en i18n.
2. Keep output concise.

---

## Required Final Checks

Always run:

```bat
python -m pytest tests/ -q --tb=short
git status
git diff --stat
```

If CLI behavior changed, also run:

```bat
python -m ebook_translator start --help
```

---

## Commit Rules

Commit only if:

```txt
tests pass
diff is clean
no secrets
no unrelated files
```

Use commit messages like:

```txt
feat: ...
fix: ...
docs: ...
test: ...
chore: ...
```

Do not push unless explicitly told.

---

## Final Report Format

```txt
完成狀態：
- 通過 / 未通過 / 部分通過

修改檔案：
- ...

實作摘要：
- ...

測試結果：
- ...

安全檢查：
- ...

是否 commit：
- 是 / 否
- commit hash:

是否 push：
- 是 / 否

Claude Code review 重點：
- ...

風險 / 注意事項：
- ...
```

---

## Stop Conditions

Stop and report if:

```txt
tests fail and cause is unclear
working tree has unrelated changes
task requires real API key
task requires committing ignored files
large architecture rewrite seems necessary
security risk is found
```

---

## Priority

```txt
Correctness > safety > tests > small diff > speed
```
