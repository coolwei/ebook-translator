# ebook-translator

## Quick Start

1. Put one or more EPUB files into `books/`.
2. Copy and edit the local config:

```bash
cp config.example.yaml config.yaml
```

3. Set the API key environment variable named by `provider.api_key_env`:

```bash
export TRANSLATION_API_KEY=sk-your-api-key
```

Or put it in local `.env` (do not commit this file):

```dotenv
TRANSLATION_API_KEY=sk-your-api-key
```

4. Preview without API calls:

```bash
ebook-translator start --dry-run
```

5. Trial translate only the first 10 pending segments:

```bash
ebook-translator start --limit 10
```

6. When the estimate and trial output look right, run the full guarded workflow:

```bash
ebook-translator start --yes
```

`ebook-translator start` reads `config.yaml` and `books/*.epub` by default. For each book it runs inspect, estimate, translate, retry-failed, retry-quality-failed, validate, report-missing, and export. It stops any book whose segment count exceeds `--max-segments` (default `300`) unless you raise the limit.

### Large-book safe mode

For long books or providers that frequently return 429 / empty content / `quality_failed`, use batched translation with cooldown and an optional rate-limit circuit breaker:

```powershell
python -m ebook_translator start --book books\Spy.epub --max-segments 2500 --batch-size 100 --cooldown-seconds 60 --yes
```

| 參數 | 說明 |
|---|---|
| `--max-segments N` | 本次處理範圍上限，避免超大書誤跑 |
| `--limit N` | 本次實際最多翻譯 N 段，適合測試 |
| `--batch-size N` | 每批最多處理 N 個未完成 segment |
| `--cooldown-seconds N` | 每批完成後等待 N 秒（`--dry-run` 不會 sleep） |
| `--stop-on-rate-limit-count N` | 累積 N 次 rate limit 類錯誤後安全停止（0 = 關閉） |

行為摘要：

- 適合大書，以及 provider 容易 429 / empty content / `quality_failed` 的情況。
- `batch-size` 越小越穩，但總時間更長。
- `cooldown-seconds` 可降低 provider 長時間連續請求壓力。
- `stop-on-rate-limit-count` 可避免硬跑浪費額度；達門檻後仍會保留 checkpoint，並匯出目前進度的 HTML / EPUB。
- 每批完成後自動執行 `validate`、`report-missing`、`export`；若該批出現 rate limit，會略過立即 retry，待之後再 `start` 或 `resume`。
- `--dry-run` 搭配 safe mode 時只顯示批次計畫，不翻譯、不 retry、不 export、不 sleep。

預覽批次計畫：

```powershell
python -m ebook_translator start --book books\Spy.epub --max-segments 2500 --batch-size 100 --cooldown-seconds 60 --dry-run
```

### Provider fallback（大書建議）

當 primary model 遇到 empty content、rate limit、`quality_failed`（如 `simplified_chinese`、`untranslated_text`）時，可設定 fallback 模型依序重試同一段：

```yaml
provider:
  model: "primary-model"
  fallback_models:
    - "fallback-model-1"
    - "fallback-model-2"
```

行為摘要：

- 正常時不切換；primary 成功則下一段繼續用 primary。
- 失敗時於**同一段**依序嘗試 fallback；若 fallback 成功，**下一段起沿用該模型**（不重置回 primary）。
- 所有模型都失敗才記錄 `failed` / `quality_failed`。
- `translations.jsonl` 會記錄實際使用的 `model`，以及 `fallback_from`、`fallback_attempt`（若有 fallback）。
- cache 重用不受目前模型影響；`reused_from_segment_id` 行為不變。

大書建議組合 safe mode + fallback：

```yaml
limits:
  rpm: 10
  concurrency: 1

provider:
  model: "primary-model"
  fallback_models:
    - "fallback-model-1"
    - "fallback-model-2"
```

```powershell
python -m ebook_translator start --book books\Spy.epub --max-segments 2500 --batch-size 100 --cooldown-seconds 60 --yes
```

### Translation log

翻譯時會持續寫入結構化 log，方便長時間追蹤進度與 fallback 行為：

```yaml
logging:
  enabled: true
  level: info
  file: logs/translation.log   # 全域 log
  per_book: true               # 另寫 outputs/<book>/translation.log
```

每筆記錄包含 `segment_id`、`model`、`status`、錯誤類型、fallback 資訊、`duration_ms` 等。log **不會**輸出 API key、Bearer token、Authorization 或完整私有 endpoint。

## CLI 語言設定

CLI 輸出支援繁體中文（zh-TW）和英文（en）模式。預設為繁體中文。

在 `config.yaml` 中加入以下設定：

```yaml
cli:
  language: zh-TW
  use_unicode_symbols: true
```

### 繁中 CLI 範例

當 `cli.language: zh-TW` 時，CLI 輸出顯示為繁體中文：

```
============================================================
翻譯估算（未呼叫 API）
============================================================
書籍標題                    : My Book
輸入路徑                    : books/my-book.epub
章節數                      : 10
段落數                      : 150
原文字元數                  : 45000
報告已儲存至 outputs/my-book/estimate_report.json
```

### 英文 CLI 範例

當 `cli.language: en` 時，CLI 輸出維持英文格式：

```
============================================================
translation_estimate_title
============================================================
book_title                 : My Book
input_path                 : books/my-book.epub
chapter_count              : 10
segment_count              : 150
report_saved_to outputs/my-book/estimate_report.json
```

## Phase 5: Translation Cache, Quality Gate, Force Controls

翻譯流程預設會優先保護已完成成果並降低 API 成本：

- `completed` segment 預設不會重翻。
- 相同 `source_hash` 若已有 `completed` 且 quality checks 乾淨的翻譯，後續相同原文會直接重用 cache，不呼叫 provider。
- cache hit 仍會 append 一筆新的 `translations.jsonl` record，並以 `reused_from_segment_id` 追蹤來源 segment。
- `failed` 與 `quality_failed` record 不會被 cache 重用。
- `validate`、`export` 都以每個 segment 最新的 record 為準。

Provider 回傳譯文後會先通過 quality gate，通過才會標記為 `completed`。目前 gate 會拒絕：

- `simplified_chinese`：輸出含簡體字。
- `added_prefix`：模型自行加上章節、段落或括號式前綴。
- `markdown_fence`：輸出含 Markdown code fence。
- `explanation_prefix`：輸出以說明文字開頭，例如 `Here is the translation:`。
- `untranslated_text`：譯文 ASCII 字母比例 ≥ 0.75 且原文為英文，表示 AI 原文回顯未翻譯。

命中 quality gate 的 record 會標記為 `quality_failed`，不算 completed，也不會進 cache。

重試與重翻控制：

```bash
# 只重試 failed segments，不重翻 completed
ebook-translator retry-failed outputs/<book> --config config.yaml --limit 5

# 只重試 quality_failed 或舊 completed 但 quality check 不乾淨的 segments
ebook-translator retry-quality-failed outputs/<book> --config config.yaml --limit 5

# 強制重翻全部 segments，append 新 record，不覆蓋舊 record
ebook-translator translate --config config.yaml --force

# 只強制重翻指定 segment；可重複或用逗號分隔
ebook-translator translate --config config.yaml --force-segment 0000:0001:abcd
ebook-translator translate --config config.yaml --force-segment 0000:0001:abcd,0000:0002:ef01
```

雙語 EPUB 翻譯 CLI 工具。將 EPUB 電子書翻譯成原文在上、譯文在下的雙語格式。

## 功能

- 讀取 EPUB，依脊椎（spine）順序提取文字段落
- 使用 OpenAI 相容 API 翻譯
- 每完成一個 segment 立即寫入 checkpoint，支援中斷後 resume
- 輸出雙語 EPUB 及 bilingual.html 預覽
- 提供基礎翻譯品質驗證報告

## 安裝

需要 Python 3.11+。

```bash
pip install -e ".[dev]"
```

## 設定

複製範例設定檔並依需求調整：

```bash
cp config.example.yaml config.yaml
```

設定 API key 環境變數（名稱與 `config.yaml` 中 `provider.api_key_env` 一致）：

```bash
export TRANSLATION_API_KEY=sk-your-api-key
```

或建立 `.env` 檔（不要 commit）：

```
TRANSLATION_API_KEY=sk-your-api-key
```

## 使用方式

> 也可用 `python -m ebook_translator <command>` 執行（免設定 PATH）。

### 檢視 EPUB（不翻譯）

先確認 spine 章節數與 segment 數量：

```bash
ebook-translator inspect --config config.yaml
```

### 估算成本與時間（不呼叫 API）

在翻譯前估算請求數、token 粗估、RPM 下限時間與風險，不會呼叫任何 provider：

```bash
ebook-translator estimate --config config.yaml
```

輸出 console 摘要並寫入 `outputs/<書名>/estimate_report.json`。token 為粗估
（mixed 文字 `字元數 / 3`，output ≈ input × 0.8），結構保留未來替換真實 tokenizer 的空間。
若有段落超過 `limits.max_chars_per_chunk`、預估時間過長或書籍過大，會列出對應 warning。

### 翻譯

```bash
ebook-translator translate --config config.yaml
```

輸出會放在 `outputs/<書名>/` 目錄下。

離線冒煙測試（使用內建 mock provider，不呼叫真實 API）：

```bash
# 產生小型測試 EPUB
python scripts/make_example_epub.py

# 用 mock provider 跑完整流程；--limit 可只翻前 N 段
ebook-translator translate --config config.example.yaml --mock
ebook-translator translate --config config.example.yaml --mock --limit 5
```

### 中斷後繼續

```bash
ebook-translator resume outputs/<書名> --config config.yaml
```

已完成的 segment 不會重翻。

### 只重試失敗的段落

```bash
ebook-translator retry-failed outputs/<書名> --config config.yaml --limit 2
```

只會重試先前 `failed` 的 segment，不會重翻已 `completed` 的段落。重試成功後會
append 新的 record，validate 時以最新成功結果為準（舊的 failed record 不再算錯誤）。

### 驗證翻譯結果

```bash
ebook-translator validate outputs/<書名>
```

驗證項目包含基本檢查（空譯文、與原文相同、長度比例、HTML 標籤、遺漏 URL）以及
翻譯品質檢查：

- `simplified_chinese`：偵測簡體字（要求繁體中文台灣用語）
- `added_prefix`：偵測模型自行加上的章節／括號前綴，例如 `【第一章：…】`
- `markdown_fence`：偵測 Markdown 程式碼圍欄 ` ``` `
- `explanation_prefix`：偵測「翻譯如下」「譯文：」等說明前綴
- `untranslated_text`：偵測 AI 原文回顯（ASCII 比例過高，表示未翻譯）

### 排查漏翻段落

```bash
ebook-translator report-missing outputs/<書名> --config config.yaml
```

**用途**：掃描原始 EPUB 中所有可見文字 block，對照 `segments.jsonl` 與
`translations.jsonl`，找出哪些段落沒有成功產生譯文。

**特點**：

- 不呼叫任何 provider，**不需要 API key**。
- 輸出 `outputs/<書名>/missing_translation_report.json`（每次執行覆蓋）。
- Console 直接顯示統計摘要。

**輸出範例**：

```
Missing translation report: outputs/example-book/missing_translation_report.json
  total_checked_blocks : 13
  missing_count        : 1
  breakdown by reason:
    translation_failed            : 1
```

**常見 reason 代碼**：

| reason | 說明 | 建議行動 |
|---|---|---|
| `translation_failed` | Segment 存在，但 provider 失敗（空內容 / timeout / 5xx） | `retry-failed` |
| `quality_failed` | Segment 存在，品質閘門拒絕（簡體 / 未翻譯等） | `retry-quality-failed` |
| `no_translation_record` | Segment 存在，但完全沒有 translation record | `retry-failed` |
| `no_segment_extracted` | Segmenter 未抽取此 block（tag 覆蓋不足） | 更新 segmenter 後重新翻譯 |

**建議排查流程**：

```bash
# 1. 驗證翻譯品質
ebook-translator validate outputs/<書名>

# 2. 找出漏翻段落
ebook-translator report-missing outputs/<書名> --config config.yaml

# 3a. 補翻 provider 失敗的段落
ebook-translator retry-failed outputs/<書名> --config config.yaml

# 3b. 重翻品質不佳（AI 回傳英文 / 簡體 / 前綴）的段落
ebook-translator retry-quality-failed outputs/<書名> --config config.yaml

# 4. 重新匯出 EPUB
ebook-translator export outputs/<書名> --config config.yaml
```

### 重新匯出 EPUB

```bash
ebook-translator export outputs/<書名> --config config.yaml
```

### 閱讀樣式

透過 `output.bilingual_style` 設定雙語輸出版面，在 `config.yaml` 加入：

```yaml
output:
  bilingual_style: note        # simple｜note｜compact
  show_source: true            # 是否顯示原文
  source_opacity: 0.55         # 原文透明度（note/compact 模式）
  add_translation_label: true  # 是否顯示「原文／譯文」標籤
```

**三種樣式比較**：

| 樣式 | 特性 | 適合場景 |
|---|---|---|
| `simple` | 原始 `.src`/`.trg` 並列，最輕量（預設） | 程式相容性優先、舊版行為 |
| `note` | 譯文為主，原文淡化；寬行高、左側 border | 中文閱讀、電子墨水閱讀器 |
| `compact` | 緊湊間距，較小字體 | 長書、節省空間 |

**note 模式 HTML 結構**：

```html
<div class="bilingual-block note-block" data-segment-id="0001:0002:a3b4c5d6">
  <div class="source-block">
    <div class="block-label source-label">原文</div>
    <p class="src">The quick brown fox jumps over the lazy dog.</p>
  </div>
  <div class="translation-block">
    <div class="block-label translation-label">譯文</div>
    <p class="trg">那隻敏捷的棕色狐狸跳過了那條懶惰的狗。</p>
  </div>
</div>
```

## 輸出結構

```
outputs/<書名>/
├── translated.epub               # 雙語 EPUB
├── bilingual.html                # 雙語 HTML 預覽
├── segments.jsonl                # 所有待翻譯段落
├── translations.jsonl            # 翻譯結果（append-only）
├── state.json                    # 作業狀態
├── validation_report.json        # 驗證報告
├── missing_translation_report.json  # 漏翻報告（report-missing 產生）
└── logs/
    ├── run.log
    └── errors.log
```

## 雙語顯示格式

```html
<div class="bilingual-block" data-segment-id="...">
  <p class="src">Original paragraph</p>
  <p class="trg">譯文段落</p>
</div>
```

## 執行測試

```bash
pytest tests/ -v
```

測試使用 mock provider，不呼叫真實 API。

## 設定說明

主要欄位：

| 欄位 | 說明 |
|---|---|
| `provider.base_url` | OpenAI 相容 API 的基礎 URL |
| `provider.api_key_env` | 存放 API key 的環境變數名稱 |
| `provider.model` | 使用的模型名稱 |
| `limits.rpm` | 每分鐘最大請求數 |
| `limits.concurrency` | 同時進行的最大請求數 |
| `resume.retry_failed` | 是否重試失敗的 segment |
| `resume.max_retries` | 最大重試次數 |

## 安全性

- 不要 commit 真實 API key
- `config.yaml`、`.env`、`books/`、`outputs/` 已加入 `.gitignore`
- API key 只從環境變數讀取

## Windows 一鍵啟動

雙擊 `start-ebook-translator.bat` 或在 PowerShell 執行：

```powershell
.\start-ebook-translator.bat
```

首次使用需先建立虛擬環境：

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -e ".[dev]"
```

選單提供常用操作：查看說明、估算成本、翻譯、驗證、匯出等。API key 仍建議透過環境變數或 `.env` 管理，不要寫進 `.bat` 檔。
