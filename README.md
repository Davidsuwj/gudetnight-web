# 股得Night：AI 財經 Shorts 自動化控制台

一個以 **FastAPI + Playwright + FFmpeg** 組成的 Windows／WSL 工作流，把財經題目轉成可人工審核或全自動執行的 YouTube 內容：

```text
題目／需求
  → ChatGPT 產生 TOPIC + CONTEXT1/2/3
  → VoAI 文字轉語音
  → ChatGPT 產生 6 張直式視覺
  → Pillow 疊加標題與遮罩
  → FFmpeg 合成 9:16 Shorts
  → YouTube Studio 上傳與公開狀態驗證
  →（選用）Amazon 商品留言與 idempotency 驗證
```

這個 repository 公開的是**流程編排、瀏覽器自動化邏輯、商品資料驗證、測試與前端**。任何 API token、OAuth 憑證、cookie、Chrome profile、頻道登入狀態、Amazon Associates 帳號資料、生成影音與實際任務紀錄都不在版控中。

> 本專案主要是可研究、可改造的實作參考。網站 DOM 隨時可能變動；使用前請先在測試帳號與私人影片上驗證 selector。

---

## 功能

### 內容工作流

- 自訂題目或完整 prompt
- 產生 `TOPIC`、`CONTEXT1`、`CONTEXT2`、`CONTEXT3`
- 一般 YouTube 與 9:16 Shorts 兩種輸出
- 自動模式或逐階段人工核准
- 任務狀態、log、artifact 與失敗重試
- ChatGPT 回覆完成檢查、圖片 rate-limit fallback、部分圖片續跑
- 使用 Pillow 製作深色遮罩、金色標題與直式封面
- 使用 FFmpeg 合併音訊、圖片與影片

### Amazon 商品延伸

- 從 Amazon 搜尋結果讀取 ASIN、價格與頁面上的熱門證據
- 只接受 `https://www.amazon.com/dp/<ASIN>` canonical URL
- 只接受由 Amazon SiteStripe 取得的 `https://amzn.to/...` 短鏈
- 不自行拼接 affiliate tag
- 以 ASIN 與 broad product type 進行歷史去重
- 舊的窄分類（例如 `business_monitor_32_4k`）會正規化成 `monitor`
- 商品留言固定為兩行：

```text
商品核心名稱
https://amzn.to/...
```

- 商品口播最後一句固定為：

```text
請參考留言處商品連結
```

- 留言 retry 使用 `SHA-256(video ID + affiliate URL)` 做 idempotency fingerprint
- 商品影片說明預設加入 Amazon Associates 必要揭露：

```text
As an Amazon Associate I earn from qualifying purchases.
```

### 安全設計

- 專用 CDP Chrome profile 與日常 Chrome 分離
- 登入資訊只保存在本機 Chrome user-data directory
- `.env`、cookie、OAuth、credential JSON、jobs、影音與 screenshots 全部忽略
- `.env.example` 只提供 placeholder
- YouTube uploader 以 adapter 路徑載入，不在程式中硬編碼帳密
- 公開前應掃描 staged files 與全部 Git history；只刪除目前檔案不足以移除舊 secret

---

## 架構

```text
gudetnight_web/
├── app.py                         # FastAPI 表單、job 建立、核准、重試、狀態頁
├── worker.py                      # script/audio/video/upload/comment stage orchestrator
├── affiliate_product.py           # Amazon schema、SiteStripe、候選與歷史去重
├── run_podcast.py                 # 獨立 Podcast CLI 編排入口
├── podcast/
│   ├── browser.py                 # CDP browser/context/page helper
│   ├── chatgpt.py                 # JSON 取得、解析與修正
│   ├── config.py                  # 可由環境變數覆寫的路徑／語音設定
│   ├── media.py                   # FFmpeg MP3/MP4 處理
│   ├── utils.py                   # JSON 與 console utilities
│   └── voai.py                    # VoAI TTS UI automation
├── tests/                         # regression tests
├── start_chrome_cdp.bat           # Windows 專用可視 Chrome 啟動器
├── run_frontend.sh                # WSL idempotent FastAPI launcher
├── .env.example                   # 無憑證設定範例
├── SECURITY.md
└── jobs/                          # 執行時產物；永不進 Git
```

### Job stage

```text
script → audio → video → upload → comment（只有商品 Shorts）
```

每個 job 會在 `jobs/<job_id>/state.json` 保存狀態。人工模式會在每個 stage 完成後暫停；auto 模式則繼續執行下一階段。

---

## 系統需求

建議環境：

- Windows 10/11
- WSL2（可選；前端可以跑在 WSL，瀏覽器 worker 跑 Windows Python）
- Python 3.11+
- Google Chrome
- FFmpeg
- 可正常使用的 ChatGPT、VoAI、YouTube Studio 帳號
- 若使用商品流程：Amazon Associates 帳號與 SiteStripe 權限

Python dependencies：

```bash
python -m pip install -r requirements.txt
python -m playwright install chromium
```

確認 FFmpeg：

```bash
ffmpeg -version
```

---

## 設定

### 1. 複製設定範例

不要把真實值寫回 `.env.example`：

```bash
cp .env.example .env
```

本專案刻意不自動讀取 `.env`，避免不透明地載入 secret。可在啟動 shell 中設定環境變數，或自行整合 `python-dotenv`。

PowerShell 範例：

```powershell
$env:GUDETNIGHT_WINDOWS_PROJECT_DIR = "C:\path\to\gudetnight_web"
$env:GUDETNIGHT_WORKSPACE = "C:\path\to\workspace"
$env:GUDETNIGHT_DOWNLOADS_DIR = "C:\path\to\workspace\Downloads"
$env:GUDETNIGHT_LOGO_PATH = "C:\path\to\workspace\logo.jpg"
$env:FFMPEG_PATH = "C:\ffmpeg\bin\ffmpeg.exe"
$env:YOUTUBE_ACCOUNT_EMAIL = "your-channel-account@example.com"
$env:YOUTUBE_CHANNEL_ID = "UC_REPLACE_WITH_YOUR_CHANNEL_ID"
$env:GUDETNIGHT_YOUTUBE_UPLOADER = "C:\path\to\youtube_uploader.py"
```

主要環境變數：

| 變數 | 用途 | 是否敏感 |
|---|---|---:|
| `GUDETNIGHT_WINDOWS_PROJECT_DIR` | WSL 呼叫 Windows worker 時的專案路徑 | 否，但不建議公開個人路徑 |
| `GUDETNIGHT_WORKSPACE` | prompt、logo、下載資料工作區 | 否 |
| `GUDETNIGHT_DOWNLOADS_DIR` | TTS 與暫存下載 | 否 |
| `GUDETNIGHT_SCREENSHOT_DIR` | 失敗截圖 | 可能含私人畫面，不可 commit |
| `GUDETNIGHT_LOGO_PATH` | 一般影片靜態封面 | 否 |
| `FFMPEG_PATH` | FFmpeg executable | 否 |
| `GUDETNIGHT_CDP_URL` | 可視 Chrome CDP endpoint | 否；不要對外網開放 |
| `YOUTUBE_ACCOUNT_EMAIL` | Studio account selector | 個資，不建議 commit |
| `YOUTUBE_CHANNEL_ID` | Studio content list channel ID | 通常公開資訊 |
| `GUDETNIGHT_YOUTUBE_UPLOADER` | uploader adapter 的本機路徑 | 否 |
| `AMAZON_ASSOCIATES_DISCLOSURE` | 合規揭露文字 | 否 |

### 2. 啟動專用 CDP Chrome

在 Windows 執行：

```bat
start_chrome_cdp.bat
```

啟動參數包含：

```text
--remote-debugging-port=9222
--user-data-dir=%USERPROFILE%\chrome-cdp-profile
```

第一次啟動後，在這個**專用 Chrome 視窗**登入：

1. `https://chatgpt.com`
2. `https://app.voai.ai`
3. `https://studio.youtube.com`
4. 若使用商品流程：`https://www.amazon.com`

重要事項：

- 不要把 `chrome-cdp-profile` 放進專案目錄。
- 不要上傳 profile、cookie、Local Storage 或 Playwright storage state。
- CDP 只應監聽 localhost，不應經 Tunnel 或公開 IP 對外開放。
- Chrome 136+ 不允許對預設 profile 使用 remote debugging，因此必須使用獨立 `--user-data-dir`。

### 3. YouTube uploader adapter

`worker.py` 會從 `GUDETNIGHT_YOUTUBE_UPLOADER` 載入一個本機 Python module。該 module 必須提供：

```python
async def upload_youtube(title: str, desc: str, video: str) -> dict:
    return {
        "youtube_url": "https://youtu.be/VIDEO_ID",
        "public_visible": True,
    }
```

這樣做是為了把可重用 orchestrator 與帳號專用的 Studio selector 分離。adapter 可以使用 YouTube Data API，也可以透過已登入 CDP Chrome 操作 YouTube Studio；不要把 OAuth refresh token 或 cookie 寫進 adapter。

若沒有 adapter，script/audio/video 仍可執行，但 upload stage 會明確停止並回報缺少檔案。

---

## 啟動前端

### Windows Python

```powershell
python -m uvicorn app:app --host 127.0.0.1 --port 8898
```

### WSL

```bash
export GUDETNIGHT_WINDOWS_PROJECT_DIR='C:\path\to\gudetnight_web'
./run_frontend.sh
```

開啟：

```text
http://127.0.0.1:8898
```

不建議直接監聽公網介面。若需要遠端操作，請自行加入 authentication 與 TLS；目前 UI 沒有內建帳號系統。

---

## 建立任務

前端欄位：

- `Input Prompt`：題目、受眾、重點與限制
- `target_type`：一般 YouTube 或 Shorts
- `approval_mode`：manual 或 auto
- `img_prompt`：選用的生圖風格
- `product_json`：選用的 Amazon 商品 manifest

商品 manifest 範例：

```json
{
  "asin": "B000000000",
  "name": "Example 4K Monitor",
  "amazon_url": "https://www.amazon.com/dp/B000000000",
  "affiliate_url": "https://amzn.to/example",
  "price": "$299.99",
  "popularity_evidence": "Page-visible popularity evidence captured at selection time",
  "relevance_reason": "Large display for multi-window research",
  "product_type": "monitor"
}
```

不要捏造價格、折扣、庫存、評價、銷量或功能。頁面資料會變動，manifest 只代表擷取當下。

---

## Amazon 選品 CLI

範例：

```powershell
python affiliate_product.py select `
  --query "32 inch 4k business monitor eye care" `
  --reason "適合需要閱讀財報與多視窗研究的觀眾" `
  --history "C:\private-state\product_history.jsonl" `
  --output "C:\private-state\selected_product.json" `
  --product-type monitor
```

流程會：

1. 讀取本機 history 的已用 ASIN／品類。
2. 若 broad product type 已用，開 Amazon 前就拒絕。
3. 掃描搜尋結果中的 ASIN、價格與熱門證據。
4. 開啟候選商品頁。
5. 從登入帳號可見的 SiteStripe UI 取得 `amzn.to` 短鏈。
6. 寫入 manifest 與本機 history。

`product_history.jsonl` 是執行狀態，不應公開。若有多個排程同時執行，請在排程器層序列化 selector；目前 JSONL append 不取代跨程序 transaction lock。

---

## 輸出

典型 job：

```text
jobs/<job_id>/
├── state.json
├── worker.log
├── podcast_output.json
├── audio_full.mp3
├── gpt_images/
│   ├── raw_01.png
│   └── shorts_image_01.jpg
├── slides.txt
├── shorts_slides.mp4
└── shorts_final.mp4
```

所有輸出皆由 `.gitignore` 排除。

---

## 測試

執行全部 regression tests：

```bash
pytest -q
```

只測商品流程：

```bash
pytest tests/test_affiliate_recommendation.py -q
```

語法檢查：

```bash
python -m py_compile app.py worker.py affiliate_product.py run_podcast.py podcast/*.py
```

測試涵蓋：

- 商品 schema、價格解析與 SiteStripe URL 驗證
- ASIN／broad product type 去重
- 精確兩行留言格式
- 固定商品 CTA
- comment fingerprint 與 video ID extraction
- ChatGPT response completion／resilience
- 生圖 rate-limit fallback
- 部分圖片 resume

Web UI 自動化仍需要登入 session，因此 unit tests 不會登入或實際發布影片。

---

## 排程建議

排程器只負責提交 job；敏感登入狀態仍留在專用 Chrome profile。建議：

1. 排程前先做 CDP health check。
2. 同一時間只執行一個 Amazon selector，避免 history race。
3. 新聞題目先做 normalized fingerprint，包含公開、私人及歷史 jobs。
4. 商品需同時比對 ASIN 與 broad product type。
5. 上傳後從 YouTube Studio 讀回 video ID 與 visibility。
6. 留言送出後重新讀回 DOM，再把 fingerprint 標記為 verified。
7. 完成後關閉 automation 新開的分頁，但不要殺掉使用者既有 Chrome。

---

## 合規與使用限制

- 這不是投資建議工具；內容需由發布者自行查核。
- 使用第三方網站自動化前，請確認帳號權限、服務條款與所在地法律。
- Amazon affiliate 連結需由有權限的 Associates 帳號取得。
- 不要隱瞞佣金關係；美國受眾通常需要清楚、顯著且接近推薦內容的揭露。
- 不能把一般 Amazon URL 冒充分潤連結，也不能自行猜 affiliate tag。
- 不要使用未授權商標、商品圖、音訊或影片素材。
- 先以私人／不公開影片 smoke test，再啟用 public automation。

---

## 常見問題

### 找不到 CDP Chrome

- 確認 `start_chrome_cdp.bat` 已執行。
- 開啟 `http://127.0.0.1:9222/json/version` 應回傳本機 Chrome metadata。
- 檢查是否誤連另一個 headless Chrome。
- 不要把 CDP port 對外公開。

### Chrome 已開但仍顯示未登入

必須在使用相同 `--user-data-dir` 的 Chrome 視窗登入；日常 Chrome 的預設 profile 不會自動共享登入狀態。

### FFmpeg 找不到

設定 `FFMPEG_PATH`，或把 `ffmpeg` 加入 PATH。

### upload stage 找不到 uploader

設定 `GUDETNIGHT_YOUTUBE_UPLOADER`，並確認 module 提供 async `upload_youtube()`。

### SiteStripe 沒有出現

- 確認登入的是有 SiteStripe 權限的 Amazon Associates 帳號。
- 確認商品頁不是 CAPTCHA、錯誤頁或地區跳轉頁。
- SiteStripe DOM 可能更新，需要重新檢查 selector。

### UI selector 突然失效

先保存不含 cookie/token 的 DOM 結構與錯誤訊息，再更新 selector。不要把完整頁面 dump 或登入後 screenshot 直接提交到 public issue。

---

## Secret hygiene checklist

公開／提交前至少執行：

```bash
git status --short
git diff --cached --name-only
git grep -n -I -E 'gh[pousr]_[A-Za-z0-9]{20,}|sk-[A-Za-z0-9_-]{20,}|AIza[0-9A-Za-z_-]{30,}|BEGIN .*PRIVATE KEY'
git log --all --oneline
```

若 secret 曾存在於任何 commit：

1. 立即 revoke／rotate。
2. 使用 `git filter-repo`、BFG 或乾淨 orphan history 移除所有 references。
3. force-push 所有受影響 refs。
4. 重新 clone，對完整 history 再掃描。
5. 公開前用未登入環境驗證 repository。

---

## License

MIT，詳見 [LICENSE](LICENSE)。
