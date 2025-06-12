# Line AI 新聞助理與聊天機器人 (v5)

這是一個功能強大、架構健壯的 Line 聊天機器人，整合了 Selenium 網頁爬蟲、大型語言模型 (LLM)、非同步背景任務處理以及多層快取機制。它不僅能作為一個具備上下文記憶的聊天夥伴，還能主動為使用者抓取、摘要並推送客製化的新聞內容。

## ✨ 核心功能

- **進階對話系統**:
  - **被動監聽**: 在群組中，Bot 會默默記錄所有公開對話，以建立完整的對話上下文。
  - **指令觸發**: 透過在訊息開頭使用 `/bot` 指令來與 Bot 互動，避免干擾正常聊天。
  - **上下文理解**: 能夠理解群組中多人、連續的對話，提供更貼切的回應。
  - **用戶識別**: 可獲取群組成員的顯示名稱，並帶有快取機制，讓對話歷史更具可讀性。
  - **可配置的思考過程**: 可在 `.env` 中設定是否顯示 LLM 的思考過程，方便除錯。

- **智慧新聞服務**:
  - **一次性查詢**: 使用 `/bot 新聞` 立即獲取最新 AI 新聞，或用 `/bot 新聞 [主題]` 查詢特定新聞。
  - **持久化訂閱**: 使用 `/bot 訂閱 [主題]` 來設定每日定時新聞推播，並可隨時查看或取消訂閱。
  - **兩階段摘要**: 採用先進的兩階段摘要流程，先精簡單篇文章，再彙整成風格化的 Podcast 內容，兼顧品質與效率。
  - **新聞摘要快取**: 對已生成的新聞摘要進行快取，在一小時內重複請求可實現秒級回應，大幅節省 API 成本。
  - **時間戳記**: 所有新聞摘要都會附上生成時間，讓用戶了解資訊的時效性。

- **健壯的系統架構**:
  - **混合式任務處理**:
    - **即時查詢**: 採用同步處理，配合快取機制，最大化利用免費的 Line Reply API。
    - **定時推播**: 採用非同步背景任務，不阻塞主程式。
  - **序列化任務鏈**: 定時推播任務會為每個訂閱者建立一個序列化的任務鏈，避免同時向後端服務發送大量請求，保護伺服器。
  - **平行化爬蟲**: 新聞抓取過程採用平行化處理，可透過 `.env` 設定平行度，顯著提升抓取速度。
  - **跨平台部署**: 自動偵測作業系統，在開發環境 (Windows/macOS) 和生產環境 (Linux/ARM) 之間無縫切換 Selenium Driver 設定。

## 🛠️ 安裝與設定

### 1. 前置需求
- Python 3.8 或更高版本
- Google Chrome 或 Chromium 瀏覽器
- **Linux/ARM 平台**:
  ```bash
  sudo apt-get update
  sudo apt-get install -y libxml2-dev libxslt1-dev python3-dev chromium-chromedriver
  ```

### 2. 安裝步驟
1.  **克隆專案庫**
    ```bash
    git clone [你的專案庫 URL]
    cd [專案目錄]
    ```

2.  **建立虛擬環境**
    ```bash
    python3 -m venv venv
    source venv/bin/activate
    ```

3.  **安裝依賴套件**
    ```bash
    pip install -r requirements.txt
    ```
    *若 `lxml` 安裝失敗，請確保前置需求已安裝，或嘗試 `pip install "lxml[html_clean]"`*

4.  **設定環境變數 (`.env` 檔案)**
    - 建立一個 `.env` 檔案，並填入以下必要的憑證與設定：
      ```ini
      # --- Line Bot 憑證 ---
      LINE_CHANNEL_ACCESS_TOKEN="YOUR_LINE_CHANNEL_ACCESS_TOKEN"
      LINE_CHANNEL_SECRET="YOUR_LINE_CHANNEL_SECRET"

      # --- OpenAI / 自架 LLM 伺服器憑證與設定 ---
      OPENAI_API_KEY="YOUR_API_KEY_OR_A_PLACEHOLDER"
      OPENAI_BASE_URL="https://api.openai.com/v1" # 或你的 ngrok/自架 URL
      OPENAI_COMPLETION_MODEL="gpt-4o-mini"

      # --- Bot 行為設定 ---
      BOT_TRIGGER_WORD="/bot"
      MAX_HISTORY_MESSAGES=50
      SHOW_THINKING_PROCESS="false" # 設為 true 以顯示 LLM 思考過程

      # --- 效能與快取設定 ---
      NEWS_FETCH_MAX_WORKERS=4  # 新聞抓取的最大平行數量
      NEWS_SUMMARY_CACHE_SECONDS=3600  # 新聞摘要快取時間 (秒)
      USER_PROFILE_CACHE_SECONDS=7200  # 用戶名稱快取時間 (秒)

      # --- 開發與測試用設定 (可選) ---
      TARGET_USER_ID_FOR_TESTING="YOUR_OWN_USER_ID"
      RUN_JOB_ON_STARTUP="false" # 設為 true 可在啟動時執行一次新聞推播
      ```

5.  **建立初始資料檔案**
    - 在專案根目錄手動建立兩個空的 JSON 檔案：
      ```bash
      echo "{}" > user_preferences.json
      echo "{}" > conversation_history.json
      echo "{}" > news_cache.json
      ```

6.  **設定 Line Developer Console**
    - **Messaging API 分頁**:
      - **Use webhooks**: `Enabled` (啟用)
      - **Auto-reply messages**: `Disabled` (停用)
      - **Greeting messages**: `Enabled` (啟用，並貼上歡迎訊息)

## 🚀 執行程式

### 本地測試模式
此模式會執行完整的新聞抓取與摘要流程，並將結果印在終端機上，適合在不啟動 Web 服務的情況下進行功能測試。
```bash
# 測試預設 AI 主題新聞
python line_bot_v5.py --test-news

# 測試自訂主題新聞，並限制只處理 3 篇文章
python line_bot_v5.py --test-news --keywords "Apple Vision Pro" --limit 3
```

### 生產/Web 伺服器模式
使用 Gunicorn (推薦) 或直接執行 Python 檔案來啟動 Line Bot 服務。
```bash
# 推薦使用 Gunicorn (請先 pip install gunicorn)
gunicorn --workers 4 --bind 0.0.0.0:5000 line_bot_v5:app

# 或者直接執行 (僅適合開發)
python line_bot_v5.py
```

## 📝 指令列表

-   `/bot [任何問題]`：與 AI 進行一般對話。
-   `/bot help` 或 `/bot 幫助`: 顯示此幫助訊息。

---
### 📰 新聞功能

-   `/bot 新聞`: 立即查詢 AI 主題新聞。
-   `/bot 新聞 [主題]`: 立即查詢特定主題新聞。
-   `/bot 新聞 關鍵字:[主題]`: (效果同上)
-   `/bot 訂閱`: 訂閱每日 AI 主題新聞推播。
-   `/bot 訂閱 [主題]`: 訂閱每日特定主題的新聞。
-   `/bot 查看訂閱`: 查看目前的訂閱狀態與主題。
-   `/bot 取消訂閱`: 取消每日新聞推播。

---
