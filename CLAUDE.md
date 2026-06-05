# CLAUDE.md

## Project Goal

Build a command-line ebook translation tool.

The tool translates EPUB ebooks into bilingual EPUB output, inspired by Immersive Translate bilingual mode:

* Original paragraph first
* Translated paragraph directly below
* Preserve ebook structure as much as possible
* Preserve chapters, images, links, footnotes, and basic formatting
* No GUI in the first phase
* Configuration is file-based
* Translation tasks must be resumable after interruption
* Model provider, API endpoint, model name, request RPM, concurrency, and context length must be configurable

Primary user language: Traditional Chinese, Taiwan usage.

---

## Core Requirements

### 1. Input / Output

Initial scope:

* Input: `.epub`
* Output:

  * bilingual `.epub`
  * intermediate normalized HTML files
  * machine-readable job state
  * translation logs

Output layout:

```txt
outputs/
└─ <book-name>/
   ├─ translated.epub
   ├─ bilingual.html
   ├─ segments.jsonl
   ├─ translations.jsonl
   ├─ state.json
   ├─ validation_report.json
   └─ logs/
      ├─ run.log
      └─ errors.log
```

---

### 2. Bilingual Display Format

Each paragraph should be rendered as:

```html
<p class="src">Original text...</p>
<p class="trg">譯文...</p>
```

For block-level content:

```html
<div class="bilingual-block" data-segment-id="...">
  <p class="src">Original paragraph</p>
  <p class="trg">Translated paragraph</p>
</div>
```

Default CSS should make source and translation readable:

```css
.bilingual-block {
  margin-bottom: 1em;
}

.src {
  opacity: 0.78;
}

.trg {
  margin-top: 0.25em;
  font-weight: 400;
}
```

Do not destroy existing ebook CSS unless necessary.

---

### 3. Configuration

Use YAML config.

Example:

```yaml
project:
  name: ebook-translator
  output_dir: outputs
  jobs_dir: jobs

input:
  path: books/example.epub

translation:
  source_language: auto
  target_language: zh-TW
  bilingual_mode: original_above_translation
  preserve_html_tags: true
  preserve_footnotes: true
  skip_existing: true

provider:
  type: openai_compatible
  base_url: "https://example.com/v1"
  api_key_env: "TRANSLATION_API_KEY"
  model: "gpt-4.1-mini"
  timeout_seconds: 120

limits:
  rpm: 30
  concurrency: 2
  max_context_tokens: 32000
  max_output_tokens: 4000
  max_chars_per_chunk: 6000

resume:
  enabled: true
  checkpoint_every_segments: 1
  retry_failed: true
  max_retries: 3

logging:
  level: info
  save_prompt: false
  save_response: true

quality:
  validate_empty_translation: true
  validate_untranslated_ratio: true
  validate_html_integrity: true
  max_length_ratio: 3.0
```

Rules:

* Never hardcode API keys.
* API keys must be loaded from environment variables.
* `config.example.yaml` must be committed.
* Real config files containing secrets must not be committed.

---

## CLI Design

Expected commands:

```bash
ebook-translator init --input books/example.epub --config config.yaml
ebook-translator translate --config config.yaml
ebook-translator resume --job jobs/<job-id>
ebook-translator validate --job jobs/<job-id>
ebook-translator export --job jobs/<job-id>
```

Minimum first version can support:

```bash
ebook-translator translate --config config.yaml
```

---

## Pipeline Design

The translation process should follow this pipeline:

1. Load config
2. Read EPUB
3. Extract spine documents
4. Normalize HTML
5. Segment translatable blocks
6. Assign deterministic segment IDs
7. Save `segments.jsonl`
8. Create or load job state
9. Translate pending segments
10. Save each translation immediately
11. Validate translation results
12. Inject bilingual blocks into HTML
13. Repack EPUB
14. Write final report

---

## Segment ID Rules

Each translatable block must have a deterministic ID.

Recommended ID format:

```txt
<chapter_index>:<block_index>:<sha1_prefix>
```

Example:

```txt
0003:0042:a18f22d9
```

The hash should be based on normalized source text.

This allows:

* Resume
* Skip unchanged segments
* Detect changed source
* Cache translation
* Retry failed segments only

---

## Job State

Use JSON or SQLite.

For phase 1, JSONL + state JSON is acceptable.

Example `state.json`:

```json
{
  "job_id": "20260605-ebook-example",
  "input_path": "books/example.epub",
  "status": "running",
  "total_segments": 1200,
  "completed_segments": 430,
  "failed_segments": 2,
  "created_at": "2026-06-05T00:00:00+08:00",
  "updated_at": "2026-06-05T00:30:00+08:00"
}
```

Each translation record should be append-only:

```json
{
  "segment_id": "0003:0042:a18f22d9",
  "source_hash": "a18f22d9...",
  "status": "completed",
  "source": "Original text...",
  "translation": "譯文...",
  "model": "gpt-4.1-mini",
  "attempt": 1,
  "created_at": "2026-06-05T00:00:00+08:00"
}
```

Avoid relying only on in-memory state.

Every completed translation must be persisted immediately.

---

## Translation Prompt Requirements

The translator should use a strict instruction prompt.

Default translation behavior:

* Translate into Traditional Chinese used in Taiwan
* Preserve meaning, tone, paragraph structure, names, and terminology
* Do not summarize
* Do not omit content
* Do not add explanations
* Preserve inline HTML tags
* Preserve placeholders, URLs, numbers, and code
* Return only translated content

For HTML-aware translation, preserve tags.

Example system prompt:

```txt
You are a professional ebook translator.
Translate the provided text into Traditional Chinese used in Taiwan.
Preserve meaning, tone, paragraph structure, terminology, names, numbers, URLs, code, and inline HTML tags.
Do not summarize.
Do not omit content.
Do not add explanations.
Return only the translated result.
```

---

## Context Strategy

Do not send the whole book as context.

Use:

* Current segment
* Optional previous 1–3 translated/source segments
* Optional chapter title
* Optional glossary
* Optional book-level style guide
* Optional chapter summary in later phases

Configurable context options:

```yaml
context:
  include_chapter_title: true
  previous_segments: 2
  include_glossary: true
  include_style_guide: true
```

---

## Rate Limit / Scheduling

Implement a scheduler that respects:

* requests per minute
* concurrency
* max retries
* timeout
* exponential backoff
* provider errors

Required behavior:

* 429 should retry with backoff
* 5xx should retry with backoff
* timeout should retry
* invalid API key should fail fast
* invalid model should fail fast
* context length exceeded should split chunk smaller

---

## Provider Abstraction

Create a provider interface.

```python
class TranslationProvider:
    async def translate(self, request: TranslationRequest) -> TranslationResponse:
        ...
```

Initial implementation:

* OpenAI-compatible chat completions

Future providers:

* Anthropic
* Gemini
* local OpenAI-compatible server
* New API router
* OpenRouter

Do not tie the pipeline to one provider.

---

## Validation

After translation, run validation.

Minimum checks:

* Empty translation
* Translation identical to source
* Suspiciously short translation
* Suspiciously long translation
* Broken HTML tags
* Missing placeholders
* Missing URLs
* Unclosed tags
* Segment count mismatch

Validation output:

```txt
outputs/<book-name>/validation_report.json
```

Validation should not silently modify content unless explicitly configured.

---

## Logging

Use two levels of logs:

### Human-readable logs

```txt
outputs/<book-name>/logs/run.log
outputs/<book-name>/logs/errors.log
```

Should include:

* started job
* loaded config
* EPUB metadata
* segment count
* translation progress
* retries
* failures
* export result

### Machine-readable logs

Use JSONL:

```txt
segments.jsonl
translations.jsonl
```

Every segment and translation result should be recoverable from these files.

---

## Error Handling

Expected failure cases:

* invalid EPUB
* malformed HTML
* API timeout
* rate limit
* provider returns empty content
* context length exceeded
* interrupted process
* corrupted state file
* output EPUB packaging failure

The tool should fail safely.

Partial progress must be preserved.

---

## Development Phases

### Phase 1 — Minimal Working CLI

Goal:

* Read EPUB
* Extract text blocks
* Translate paragraphs
* Save progress
* Resume interrupted jobs
* Export bilingual EPUB

Deliverables:

* CLI command
* config loader
* EPUB reader/writer
* segmenter
* OpenAI-compatible provider
* checkpoint system
* basic validation
* example config

### Phase 2 — Quality and Stability

Goal:

* Better HTML preservation
* Translation cache
* glossary
* style guide
* better retry behavior
* validation report
* failed segment retry command

### Phase 3 — Advanced Translation Quality

Goal:

* chapter summary context
* terminology consistency
* name glossary extraction
* side-by-side HTML preview
* manual correction workflow

### Phase 4 — GUI / Web UI

Do not implement GUI before the CLI pipeline is stable.

---

## Testing Requirements

Add tests for:

* config parsing
* EPUB extraction
* HTML segmentation
* segment ID stability
* checkpoint resume
* provider mock response
* retry behavior
* bilingual HTML injection
* validation checks

Use mocked provider responses in tests.

Do not call real APIs in unit tests.

---

## Security Requirements

* Never commit real API keys
* `.env`, real config files, logs with responses, and translated copyrighted books should be gitignored by default
* Provide `config.example.yaml`
* Use environment variables for secrets
* Avoid logging full prompts unless explicitly enabled

Recommended `.gitignore`:

```gitignore
.env
*.env
config.yaml
books/
outputs/
jobs/
*.epub
*.log
```

---

## Coding Style

Use Python.

Recommended stack:

* Python 3.11+
* typer or click for CLI
* pydantic for config validation
* httpx for async API calls
* beautifulsoup4 or lxml for HTML parsing
* ebooklib for EPUB read/write
* tenacity or custom retry logic
* tiktoken or provider-specific tokenizer where available

Prefer simple, maintainable code over clever abstractions.

---

## Important Implementation Notes

* Preserve EPUB spine order.
* Do not translate navigation files unless explicitly needed.
* Do not translate CSS, JS, image alt text in phase 1 unless configured.
* Avoid merging unrelated paragraphs.
* Avoid splitting inside inline HTML tags.
* Keep source and translation tightly paired.
* Always write completed segment results immediately.
* Resume should not retranslate completed segments unless `force: true`.
* Failed segments should be retryable without restarting the entire job.
* The final EPUB should remain readable even if some segments failed; failed segments can show source only or a warning depending on config.

---

## Definition of Done for Phase 1

Phase 1 is done when:

1. A valid EPUB can be loaded.
2. Paragraphs can be extracted into deterministic segments.
3. Segments can be translated using an OpenAI-compatible endpoint.
4. Progress is saved after each segment.
5. The process can be interrupted and resumed.
6. A bilingual EPUB is exported.
7. Basic validation report is generated.
8. No real secrets are committed.
9. README includes setup and usage instructions.
