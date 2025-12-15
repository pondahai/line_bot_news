# Line AI 新聞助理與聊天機器人 (v5)

這是一個功能強大的 Line 聊天機器人，整合了 Selenium 網頁爬蟲、大型語言模型 (LLM) 以及非同步背景任務處理。它不僅能作為一個具備上下文記憶的聊天夥伴，還能主動為使用者抓取、摘要並推送客製化的新聞內容。

## ✨ 核心功能

- **進階對話系統**:
  - **被動監聽**: 在群組中，Bot 會默默記錄所有公開對話，以建立完整的對話上下文。
  - **指令觸發**: 透過在訊息開頭使用 `/bot` 指令來與 Bot 互動，避免干擾正常聊天。
  - **上下文理解**: 能夠理解群組中多人、連續的對話，提供更貼切的回應。
  - **用戶識別**: 可獲取群組成員的顯示名稱，讓對話歷史更具可讀性。

- **智慧新聞服務**:
  - **一次性查詢**: 使用 `/bot 新聞` 立即獲取最新 AI 新聞，或用 `/bot 新聞 關鍵字:主題` 查詢特定新聞。
  - **持久化訂閱**: 使用 `/bot 訂閱 [主題]` 來設定每日定時新聞推播，並可隨時查看或取消訂閱。
  - **兩階段摘要**: 採用先進的兩階段摘要流程，先精簡單篇文章，再彙整成風格化的 Podcast 內容，兼顧品質與效率。
  - **強大爬蟲**: 整合 `Newspaper3k` 與 `Selenium`，能夠處理動態載入的網頁，提高新聞抓取成功率。

- **健壯的系統架構**:
  - **非同步處理**: 所有耗時任務（新聞抓取、LLM 呼叫）均在背景執行緒中處理，確保 Line Webhook 即時回應。
  - **序列化任務鏈**: 定時推播任務會為每個訂閱者建立一個序列化的任務鏈，避免同時向後端服務發送大量請求，保護伺服器。
  - **跨平台部署**: 自動偵測作業系統，在開發環境 (Windows/macOS) 和生產環境 (Linux/ARM) 之間無縫切換 Selenium Driver 設定。

## 🛠️ 安裝與設定

### 1. 前置需求
- Python 3.8 或更高版本
- Google Chrome 或 Chromium 瀏覽器
- (Linux/ARM 平台) `libxml2-dev`, `libxslt1-dev`, `python3-dev`, `chromium-chromedriver`

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
    *若在 ARM 平台安裝 `lxml` 失敗，請先執行 `sudo apt-get install -y libxml2-dev libxslt1-dev python3-dev`*

4.  **設定環境變數**
    - 複製 `.env.example` (如果有的話) 或手動建立一個 `.env` 檔案。
    - 填入以下必要的憑證與設定：
      ```ini
      # Line Bot 憑證
      LINE_CHANNEL_ACCESS_TOKEN="YOUR_LINE_CHANNEL_ACCESS_TOKEN"
      LINE_CHANNEL_SECRET="YOUR_LINE_CHANNEL_SECRET"

      # OpenAI / 自架 LLM 伺服器憑證與設定
      OPENAI_API_KEY="YOUR_API_KEY"
      OPENAI_BASE_URL="https://api.openai.com"
      OPENAI_COMPLETION_MODEL="gpt-4o-mini"
      
      # ⚠️ 更多完整設定與功能開關，請務必參考專案中的 `.env.example` 檔案 ⚠️
      # 該檔案中包含所有可用的環境變數及其詳細中文說明。
      ```

5.  **設定 Line Developer Console**
    - **Use webhooks**: `Enabled`
    - **Auto-reply messages**: `Disabled`
    - **Greeting messages**: `Enabled` (並貼上歡迎訊息)

## 🚀 執行程式

### 本地測試模式
此模式會執行完整的新聞抓取與摘要流程，並將結果印在終端機上，適合在不啟動 Web 服務的情況下進行功能測試。
```bash
# 測試預設 AI 主題新聞
python line_bot_news_v5_2.py --test-news

# 測試自訂主題新聞，並限制只處理 3 篇文章
python line_bot_news_v5_2.py --test-news --keywords "Apple Vision Pro" --limit 3
```