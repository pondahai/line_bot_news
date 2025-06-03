# 個性化 LLM 新聞彙整 Line Bot

這是一個基於 Python Flask 的 Line Bot 專案，它實現了以下主要功能：

1.  **個性化每日新聞彙整與推播**：
    *   定時（預設每日早上9點）從 Google News RSS 抓取新聞。
    *   用戶可以通過指令 "訂閱新聞 [我的關鍵字]" 來設定自己感興趣的新聞主題，如果未指定則使用預設關鍵字 (如 LLM, AI 等)。
    *   使用大型語言模型 (如 OpenAI GPT 系列，或本地部署的 LLM) 對抓取到的新聞進行摘要和整理。
    *   在摘要中包含新聞的原始標題、來源和 **直接輸出的原文 URL** (Line 會自動轉為可點擊連結)。
    *   將彙整後的新聞摘要主動推播給已訂閱的 Line 用戶。
    *   支援在新聞摘要生成過程中，如果 LLM 輸出思考過程 (使用 `<think>` 標籤)，則將思考過程與正式摘要分開發送，並帶有視覺分離延遲。
    *   當用戶成功訂閱或更新新聞關鍵字後，會立即觸發一次基於新設定的新聞推播。

2.  **互動式聊天機器人**：
    *   用戶可以通過特定的關鍵字 (在 `.env` 中配置的 `BOT_NAMES`) 觸發與 LLM 的對話。
    *   支援解析 LLM 回應中的 `<think>` 標籤，將思考過程和正式回答分開，並帶有視覺分離延遲，以提升用戶體驗。

3.  **用戶訂閱與偏好管理**：
    *   用戶可以通過向 Bot 發送指令 (如 "訂閱新聞 [關鍵字]" / "訂閱新聞" / "取消訂閱新聞") 來管理新聞推播的訂閱狀態和新聞關鍵字。
    *   用戶加 Bot 好友時，會收到訂閱引導提示。
    *   用戶訂閱狀態和自定義新聞關鍵字持久化儲存 (目前使用 `user_preferences.json` 檔案)。

4.  **訊息處理**：
    *   自動將過長的訊息（LLM 回應、新聞摘要、思考過程）分割成多條適合 Line 發送的片段。

## 功能特性

*   **定時任務**：使用 APScheduler 執行每日新聞抓取和推播。
*   **LLM 整合**：通過 API 與 OpenAI (或本地/其他雲端 LLM) 進行交互，用於對話和新聞摘要。
*   **Line Messaging API 互動**：接收 webhook 事件，發送回覆 (Reply) 和主動推播 (Push) 訊息。
*   **個性化新聞源**：用戶可自訂新聞搜尋關鍵字。
*   **環境變數配置**：所有敏感資訊和重要配置（API Keys, Bot 名稱, Timeout 時間等）均通過 `.env` 文件管理。
*   **詳細日誌記錄**：方便追蹤和調試。
*   **啟動時執行選項**：可配置在應用啟動時立即執行一次新聞推播任務，方便測試。
*   **適應本地 LLM**：支援通過環境變數配置較長的 API Timeout 時間。

## 技術棧

*   **後端框架**: Python, Flask
*   **Line Bot API 互動**: 直接與 Line Messaging API 進行 HTTP 請求。
*   **LLM API**: OpenAI API (或本地/其他雲端 LLM 的 API 接口)
*   **任務排程**: APScheduler
*   **新聞來源**: Google News RSS (通過 `feedparser` 解析)
*   **HTTP 請求**: `requests` 庫
*   **環境變數管理**: `python-dotenv`
*   **數據儲存**: JSON 文件 (用於用戶偏好)

## 環境準備與安裝

1.  **Python 環境**: 確保已安裝 Python 3.8 或更高版本。
2.  **虛擬環境 (推薦)**:
    ```bash
    python -m venv venv
    source venv/bin/activate  # Linux/macOS
    # venv\Scripts\activate    # Windows
    ```
3.  **安裝依賴**:
    ```bash
    pip install Flask python-dotenv requests feedparser APScheduler click
    ```
    (如果使用了 Flask CLI 命令，則需要 `click`)

4.  **設定 Line Bot Channel**:
    *   前往 [Line Developers Console](https://developers.line.biz/)。
    *   建立一個 Messaging API Channel。
    *   獲取 `Channel access token (long-lived)` 和 `Channel secret`。

5.  **準備 LLM API**:
    *   如果你使用 OpenAI，請申請 API Key。
    *   如果你使用本地或其他 LLM，確保其 API 接口已準備就緒。

6.  **建立 `.env` 配置文件**:
    在專案根目錄下建立一個名為 `.env` 的文件，並填入以下內容（替換為你自己的值）：
    ```env
    # Line Bot 設定
    LINE_CHANNEL_ACCESS_TOKEN=YOUR_LINE_CHANNEL_ACCESS_TOKEN
    LINE_CHANNEL_SECRET=YOUR_LINE_CHANNEL_SECRET

    # LLM API 設定
    OPENAI_API_KEY=YOUR_LLM_API_KEY # 不一定是OpenAI，取決於你用的LLM
    OPENAI_COMPLETION_MODEL=your_llm_model_name # 例如 gpt-3.5-turbo, 或本地模型的標識符
    OPENAI_BASE_URL=https_your_llm_api_base_url # 例如 https://api.openai.com 或本地服務的 URL (http://localhost:8000)
    OPENAI_API_TIMEOUT=600 # LLM API 請求超時時間（秒），本地LLM可能需要更長

    # Bot 行為設定
    BOT_NAMES=小幫手,AI助手 # 你的機器人名稱，用逗號分隔
    BOT_DEACTIVATED=False 
    DEFAULT_NEWS_KEYWORDS="大型語言模型 OR LLM OR 生成式AI" # 預設新聞關鍵字
    VISUAL_SEPARATION_DELAY=1.0 # CoT思考與正式回答間的視覺分離延遲（秒）

    # 測試推播用 (可選)
    TARGET_USER_ID_FOR_TESTING=YOUR_OWN_LINE_USER_ID 

    # 啟動時執行新聞任務 (True/False)
    RUN_JOB_ON_STARTUP=True 
    ```

## 運行方式

### 1. 本地開發運行

```bash
python your_bot_script_name.py  # 例如：python line_bot_final_v5.py
```
伺服器預設會在 `http://0.0.0.0:5000` 上運行。

### 2. 設定 Webhook URL

使用 `ngrok` 或類似工具將本地伺服器暴露到公網：
```bash
ngrok http 5000
```
獲取 `ngrok` 提供的 HTTPS URL，然後在 Line Developers Console 中你的 Channel 設定頁面，將 "Webhook URL" 設置為 `https://your-ngrok-url.ngrok-free.app/webhook` 並啟用 Webhook。

## 檔案結構 (主要檔案)

*   `your_bot_script_name.py`: 主應用程式邏輯。
*   `.env`: 環境變數配置文件 (需自行建立)。
*   `user_preferences.json`: 儲存用戶訂閱狀態和新聞關鍵字 (自動生成)。
*   `requirements.txt` (可選): 通過 `pip freeze > requirements.txt` 生成。

## 主要功能模塊說明

*   **Webhook 處理 (`@app.route('/webhook')`, `handle_text_message_event`)**:
    處理 Line 事件，包括用戶訊息（觸發對話或訂閱指令）、關注/取消關注事件。
*   **新聞處理 (`fetch_llm_news_from_google_rss`, `summarize_news_with_llm`)**:
    根據預設或用戶自定義關鍵字獲取新聞，並使用 LLM 進行摘要。
*   **排程任務 (`daily_news_push_job`, APScheduler setup)**:
    每日定時為訂閱用戶生成並推播個性化新聞彙整。
*   **單用戶新聞推送 (`generate_and_push_news_for_user`)**:
    封裝了為指定用戶獲取、摘要、CoT處理並推送新聞的邏輯，供定時任務和即時觸發調用。
*   **訊息發送與分割 (`send_line_messages`, `split_long_message`)**:
    統一處理向 Line 發送訊息，並自動分割長訊息。
*   **用戶偏好管理 (`load_user_preferences`, `save_user_preferences`)**:
    通過 JSON 文件管理用戶的訂閱狀態和新聞關鍵字。

## 與 Bot 互動指令

*   **對話**: `@你的Bot名稱 [你的問題]` (例如: `@小幫手 今天天氣如何？`)
*   **訂閱預設新聞**: `訂閱新聞`
*   **訂閱自定義新聞**: `訂閱新聞 [你想關注的關鍵字，用空格分隔]` (例如: `訂閱新聞 Python 人工智慧`)
*   **取消訂閱新聞**: `取消訂閱新聞`

## 未來可能的改進方向

*   **資料庫整合**: 使用 SQLite 或更大型資料庫替代 JSON 文件，以支援更大用戶量和更複雜查詢。
*   **Line Bot SDK**: 考慮遷移到使用官方 `line-bot-sdk-python`。
*   **更豐富的訊息類型**: 使用 Flex Message、Quick Reply 等。
*   **進階關鍵字管理**: 允許用戶管理多組關鍵字，或排除某些關鍵字。
*   **錯誤監控與告警**: 整合 Sentry 等。
*   **部署優化**: Docker 容器化，使用 Gunicorn/Uvicorn。
*   **非同步處理**: 對於耗時的 LLM 調用或新聞處理，考慮使用任務隊列 (如 Celery) 實現非同步，避免阻塞 Webhook 回應。

## 貢獻

歡迎提交 Pull Requests 或 Issues。

## 授權

(可選，例如 MIT License)
