# ebook-translator

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

### 驗證翻譯結果

```bash
ebook-translator validate outputs/<書名>
```

### 重新匯出 EPUB

```bash
ebook-translator export outputs/<書名> --config config.yaml
```

## 輸出結構

```
outputs/<書名>/
├── translated.epub        # 雙語 EPUB
├── bilingual.html         # 雙語 HTML 預覽
├── segments.jsonl         # 所有待翻譯段落
├── translations.jsonl     # 翻譯結果（append-only）
├── state.json             # 作業狀態
├── validation_report.json # 驗證報告
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
