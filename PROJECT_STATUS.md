# PROJECT_STATUS.md

> 最後更新：2026-06-07
> 分支：`main`（與 `origin/main` 同步）
> 測試：見本輪 `python -m pytest tests/ -q`

---

## 1. 目前完成狀態

Phase 1（最小可用 CLI）與 Phase 1.5 / 部分 Phase 2 功能已完成並通過測試。

- 可讀取 EPUB、切段、產生確定性 segment ID、翻譯（mock / OpenAI 相容）、即時 checkpoint、resume、驗證、匯出雙語 EPUB + HTML。
- 已新增：翻譯快取、品質閘門（quality gate）、缺漏偵測、估算指令、批次 `start` 工作流、CLI 繁中介面、三種雙語閱讀樣式。
- **尚未實作**：large-book safe mode（P0，見待辦），詳見下方。

Phase 1 Definition of Done：**全部達成**。

---

## 2. 已完成功能列表

### Core pipeline
- EPUB reader（spine 解析、`SpineDocument`）
- EPUB writer（重新打包，保留結構）
- segmenter（確定性 ID `chapter:block:sha1`）
- renderer（雙語注入，三種樣式）
- checkpoint（每段即時持久化）
- resume（不重譯已完成段落）
- translation records append-only（`translations.jsonl`）
- `state.json` 更新
- export（`translated.epub` + `bilingual.html`）

### Provider
- `openai_compatible`（async httpx）
- `mock` provider（測試/離線用，`--mock`）
- provider 抽象介面（base.py）
- **`provider.fallback_models`**：primary 失敗時依序切換；成功後沿用該模型
- **`translations.jsonl` fallback metadata**：`fallback_from`、`fallback_attempt`

### Logging
- **`logging.enabled`**：是否寫入 translation log（預設 true）
- **`logging.file`**：全域 log 路徑（預設 `logs/translation.log`）
- **`logging.per_book`**：另寫 `outputs/<book>/translation.log`
- log 自動 redact API key / Bearer / Authorization / 私有 endpoint

### Quality gate（皆有測試）
- `simplified_chinese`
- `added_prefix`
- `markdown_fence`
- `explanation_prefix`
- `untranslated_text`
- 另含 empty / untranslated_ratio / html_integrity / length_ratio

### Segmenter 支援標籤
- `p`、`li`、`blockquote`、`h1`–`h6`、`td`、`th`、`figcaption`、`caption`、`dt`、`dd`、div 直接文字
- `figure` 已從 SKIP_CONTAINERS 移除 → `figcaption` 不再被跳過（有註解說明）

### Reading styles（`output.bilingual_style`）
- `simple`（預設，`.src` / `.trg` 並列）
- `note`（中文筆記模式，推薦）
- `compact`（緊湊長書模式）
- note mode markers 已驗證：`bilingual-block note-block`、`source-block`、`translation-block`、`source-label`、`translation-label`、`原文`、`譯文`

### CLI i18n（`cli.language`）
- `zh-TW`（預設）、`en`
- JSON 檔案 key 不受語言影響（僅 console 顯示翻譯）

---

## 3. 已驗證功能（本次，無呼叫真實 API）

| 驗證項目 | 結果 |
| --- | --- |
| `start --book books/example.epub --dry-run` | ✅ 3 章 / 13 段 / 455 字元，產生 estimate_report，無 API |
| `translate --config config.example.yaml --mock` | ✅ resume 13/13，0 errors |
| `validate outputs/example-book` | ✅ total 13、passed 13、warnings 0、**errors 0** |
| `report-missing outputs/example-book` | ✅ missing 0（報告為空陣列 `[]`） |
| `export outputs/example-book` | ✅ 產出 translated.epub + bilingual.html |
| bilingual.html note markers | ✅ 7 個 marker 全部存在 |
| Spy.epub `start --dry-run` | ✅ 與已知值完全一致（見第 5 節） |

---

## 4. CLI 指令列表

```
translate              翻譯 EPUB（依 config）
inspect                讀取 EPUB，報告 spine + segment 統計（不翻譯）
estimate               估算成本 / requests / tokens / 時間（不呼叫 API）
start                  books/ 內 EPUB 的完整工作流
resume                 恢復中斷的工作
retry-failed           只重試先前失敗的段落
retry-quality-failed   重譯品質檢查未過的段落
validate               對完成的工作執行驗證
export                 由完成的工作重新匯出雙語 EPUB / HTML
report-missing         產生 missing_translation_report.json
```

兩種叫用方式皆可：`ebook-translator <cmd>`（console script）或 `python -m ebook_translator <cmd>`。
Windows 啟動器 `start-ebook-translator.bat` 已改用較穩健的 `python -m ebook_translator`。

---

## 5. Spy.epub 狀態（dry-run / estimate，未翻譯）

| 指標 | 數值 |
| --- | --- |
| 章節數 | 36 |
| 段落數 | 1980 |
| 原文字元數 | 332,071 |
| 預估 input tokens | 110,690（rough chars/3） |
| 預估 request 數 | 1,980 |
| 預估最短時間（rpm=10） | 198.0 min |
| 含 retry overhead | 217.8 min |
| 超過 max_segments? | 否（1980 < 2500；但**預設 300 會被擋下**，需 `--max-segments 2500`） |
| 警告 | `long_runtime`（>60 min） |

> 與先前已知數值完全一致，無差異。
> 建議：超過 1500 段的書應使用 large-book safe mode（見待辦 P0）。

---

## 6. 安全狀態

- ✅ `.env`、`config.yaml`、`books/`、`outputs/`、`logs/`、`*.epub`、`*.log`、`translations.jsonl`、`.sisyphus/` 皆在 `.gitignore`。
- ✅ 無真實 API key 被追蹤；secret scan 僅命中假 key（README `sk-your-api-key`、測試 `sk-test` / `Bearer sk-test`）。
- ✅ `config.example.yaml` 已提交，`config.yaml` 未被追蹤。
- ✅ API key 一律由環境變數 `TRANSLATION_API_KEY` 載入，未硬編碼。

---

## 7. 已知問題

1. Spy.epub 等大書仍可能因 provider 品質不穩而出現部分 `failed` / `missing`；建議搭配 **safe mode + `fallback_models` + translation log** 長時間追蹤與恢復。
2. `missing_translation_report.json` 內容為**裸陣列** `[]`，而非含 `missing_count` 欄位的物件；count 僅在 CLI 輸出計算。屬輕微設計落差，非 bug。
3. token 估算為粗估（`chars/3`），非真實 tokenizer（P1）。
4. `ebook-translator` console script 在某些 shell（如本工具的 git-bash）PATH 未含 venv Scripts 時找不到；`python -m ebook_translator` 永遠可用。非程式 bug。

> 本次檢查**未發現程式 bug**，亦未修改任何 `.py` 程式碼。

---

## 8. 待辦事項（依優先序）

### P0 — large-book safe mode
原因：Spy.epub 大量翻譯時出現大量 rate limit / empty content / simplified_chinese。

目標用法：
```
ebook-translator start --book books\Spy.epub --max-segments 2500 \
  --batch-size 100 --cooldown-seconds 60 --yes
```
需求：
- [ ] `--batch-size`
- [ ] `--cooldown-seconds`
- [ ] `--stop-on-rate-limit-count`
- [ ] 每批執行 validate / report-missing / export
- [ ] 批次間 cooldown
- [ ] rate limit 過多時停止
- [ ] 可 resume
- [ ] 無呼叫真實 API 的測試

### P1 — patch-segment
provider 反覆回傳 empty content 時，提供人工補翻指令。

### P1 — true tokenizer estimate
以真實 tokenizer 取代 `chars/3` 粗估，提升成本估算準確度。

### P2 — glossary / terminology consistency
### P2 — chapter context / style guide
### ~~P3 — multi-model fallback~~（已完成：見 `provider.fallback_models`）
### P3 — GUI / Web UI（CLI 穩定後再做）

---

## 9. 建議下一步

1. **先實作 P0 large-book safe mode**，這是讓 Spy.epub 等大書能可靠全量翻譯的關鍵；先寫 mock 測試覆蓋分批 / cooldown / 停止 / resume 邏輯。
2. 將 `missing_translation_report.json` 包成 `{ "missing_count": N, "items": [...] }` 物件，與其他報告一致（小改動，順手做）。
3. 把 `start` 預設 `--max-segments`（目前 300）與 safe mode 文件化於 README，避免使用者被預設值擋下而困惑。
4. P1 patch-segment 與 true tokenizer estimate 可在 P0 之後接續。
