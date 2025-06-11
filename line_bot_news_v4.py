# ==============================================================================
# line_bot_final_merged.py
# 整合了 Line Bot、Selenium 新聞擷取、兩階段 LLM 摘要及本地測試模式的完整程式碼
# ==============================================================================

# --- Python Standard Libraries ---
import os
import platform 
import sys
import time
import json
import logging
import hashlib
import hmac
import base64
import re
import atexit
import argparse
from datetime import datetime, timedelta
import urllib.parse

# --- Third-party Libraries ---
import requests
from flask import Flask, request, jsonify
from dotenv import load_dotenv
from apscheduler.schedulers.background import BackgroundScheduler
import feedparser

# --- Newspaper3k for article scraping ---
from newspaper import Article, Config

# --- Selenium for dynamic content scraping ---
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from webdriver_manager.chrome import ChromeDriverManager


# ==============================================================================
# --- 環境設定、日誌與 Flask 初始化 ---
# ==============================================================================
load_dotenv()
app = Flask(__name__)

# --- 日誌設定 ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s: %(message)s [in %(pathname)s:%(lineno)d]',
    stream=sys.stdout
)

# --- 全域變數與常數 ---
BOT_NAMES = os.getenv("BOT_NAMES", "bot,機器人").split(",")
BOT_DEACTIVATED = os.getenv("BOT_DEACTIVATED", "False").lower() == "true"
OPENAI_COMPLETION_MODEL = os.getenv("OPENAI_COMPLETION_MODEL", "gpt-4o-mini")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
LINE_CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET")
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "https://api.openai.com")
TARGET_USER_ID_FOR_TESTING = os.getenv("TARGET_USER_ID_FOR_TESTING")

VISUAL_SEPARATION_DELAY = float(os.getenv("VISUAL_SEPARATION_DELAY", "1.0"))
DEFAULT_NEWS_KEYWORDS = "大型語言模型 OR LLM OR 生成式AI OR OpenAI OR Gemini OR Claude"
USER_PREFERENCES_FILE = "user_preferences.json"
MAX_MESSAGE_LENGTH = 4800
NEWS_FETCH_TARGET_COUNT = 7 # 從 RSS 中嘗試抓取的目標新聞數量
USER_AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36'

# --- 兩階段摘要的 LLM Prompt 設定 ---
PROMPT_FOR_INDIVIDUAL_SUMMARY = (
    "你是一位資深的新聞編輯，專長是快速提煉文章核心。請將以下提供的新聞內文，濃縮成一段不超過150字的客觀、精簡中文摘要。"
    "摘要應包含最關鍵的人物、事件、數據和結論。請直接輸出摘要內容，不要有任何開頭或結尾的客套話。"
)

PROMPT_FOR_FINAL_AGGREGATION = (
    "你是一位風趣幽默、知識淵博的科技新聞 Podcast 主持人。你的聽眾是 Line 用戶，他們喜歡輕鬆、易懂且帶有 Emoji 的內容。"
    "接下來我會提供數則「已經被精簡過的新聞摘要」。請你根據這些摘要，發揮你的主持風格，將它們整合成一篇連貫的談話性內容。"
    "你的任務是：\n"
    "1. 用生動的語氣開場，吸引聽眾注意。\n"
    "2. 將各則新聞摘要自然地串連起來，可以加上你的評論或觀點來銜接，但不要杜撰不存在的事實。\n"
    "3. 在提到每則新聞的重點後，請務必附上這則新聞的原始標題，格式如下：\n"
    "   - 標題：[原始新聞標題]\n"
    "4. 全程多使用 Emoji 來增加活潑感。\n"
    "5. 最後用一句話做個總結或給聽眾一句溫馨提醒。\n"
    "6. 嚴格根據我提供的摘要內容、標題和連結進行創作，不要引用外部資訊。\n"
    "7. 要嚴肅應對每則新聞的負面情緒。\n"
    "8. 最後結論要加註這是AI負責總結的內容，讀者應自行求證其正確性。\n"
)


# ==============================================================================
# --- 新聞擷取模組 (整合自 news_fetch.py) ---
# ==============================================================================
newspaper_config = Config()
newspaper_config.browser_user_agent = USER_AGENT
newspaper_config.request_timeout = 15
newspaper_config.memoize_articles = False

chrome_options = Options()
chrome_options.add_argument("--headless")
chrome_options.add_argument("--disable-gpu")
chrome_options.add_argument("--no-sandbox")
chrome_options.add_argument("--disable-dev-shm-usage")
chrome_options.add_argument("--window-size=1024x400")
chrome_options.add_argument(f"user-agent={USER_AGENT}")

def get_real_url(google_news_url):
    try:
        response = requests.head(google_news_url, allow_redirects=True, timeout=15)
        return response.url
    except requests.RequestException as e:
        logging.warning(f"    [錯誤] 無法解析跳轉連結 {google_news_url}: {e}")
        return None

def fetch_article_with_selenium(url):
    logging.info(f"    [備援] 嘗試使用 Selenium 抓取動態內容: {url[:70]}...")
    driver = None
    try:
        # ==================== 智慧判斷作業系統並設定 Service ====================
        current_os = platform.system()
        logging.info(f"    [Selenium 設定] 偵測到目前作業系統為: {current_os}")

        if current_os == "Linux":
            # 在 Linux 環境 (例如您的 ARM 伺-服器)，使用手動指定路徑
            # 這個路徑通常是透過 `sudo apt-get install chromium-chromedriver` 安裝的
            chromedriver_path = "/usr/bin/chromedriver"
            logging.info(f"    [Selenium 設定] Linux 系統，使用手動指定的路徑: {chromedriver_path}")
            
            # 檢查路徑是否存在，如果不存在則給出清晰的錯誤提示
            if not os.path.exists(chromedriver_path):
                logging.error(f"    [Selenium 嚴重錯誤] 在 Linux 系統上找不到指定的 chromedriver: {chromedriver_path}")
                logging.error("    請確認是否已執行 `sudo apt-get install chromium-chromedriver`，或路徑是否正確。")
                return None # 中止此函數的執行
                
            service = Service(executable_path=chromedriver_path)
            
        else:
            # 在非 Linux 環境 (例如 Windows, macOS)，使用 webdriver-manager 自動管理
            logging.info("    [Selenium 設定] 非 Linux 系統，使用 webdriver-manager 自動下載/管理 driver。")
            service = Service(ChromeDriverManager().install())
        # ======================================================================
        
        driver = webdriver.Chrome(service=service, options=chrome_options)
        driver.get(url)
        time.sleep(4)
        return driver.page_source
    except Exception as e:
        logging.error(f"    [Selenium 錯誤] 抓取 {url[:70]}... 失敗: {e}")
        return None
    finally:
        if driver:
            driver.quit()

def fetch_and_parse_articles(custom_query=None, limit=NEWS_FETCH_TARGET_COUNT):
    query_to_use = custom_query.strip() if custom_query and custom_query.strip() else DEFAULT_NEWS_KEYWORDS
    encoded_query = urllib.parse.quote_plus(query_to_use)
    rss_url = f"https://news.google.com/rss/search?q={encoded_query}&hl=zh-TW&gl=TW&ceid=TW:zh-Hant"
    
    logging.info(f">>> 開始從 Google News RSS 取得新聞列表 (關鍵字: '{query_to_use}')")
    feed = feedparser.parse(rss_url)

    if feed.bozo:
        logging.error(f"無法解析 RSS feed。錯誤資訊: {feed.bozo_exception}")
        return []

    successful_articles = []
    processed_urls = set()
    
    logging.info(f">>> 找到 {len(feed.entries)} 則新聞，開始逐一爬取內文，目標 {limit} 則...")

    for entry in feed.entries:
        if len(successful_articles) >= limit:
            break

        logging.info(f"--- 正在處理: {entry.title}")
        
        real_url = get_real_url(entry.link)
        if not real_url or real_url in processed_urls:
            logging.warning("    [跳過] 無法取得真實 URL 或 URL 重複。")
            continue
        
        processed_urls.add(real_url)

        try:
            article = Article(real_url, language='zh', config=newspaper_config)
            article.download()
            article.parse()
            
            if len(article.text) < 200:
                logging.warning("    [警告] 標準方法抓取內容過短，啟用 Selenium 備援。")
                html_content = fetch_article_with_selenium(real_url)
                if html_content:
                    article.download(input_html=html_content)
                    article.parse()

            if article.title and len(article.text) > 200:
                logging.info(f"    [成功] 已取得文章: {article.title}")
                successful_articles.append({
                    'title': article.title,
                    'text': article.text,
                    'url': real_url,
                    'source': entry.source.title if hasattr(entry, 'source') and hasattr(entry.source, 'title') else "未知來源"
                })
            else:
                logging.warning(f"    [失敗] 使用所有方法後，仍無法解析出足夠內文。URL: {real_url}")

        except Exception as e:
            logging.error(f"    [失敗] 處理新聞時發生未預期錯誤。 URL: {real_url}, 原因: {e}", exc_info=True)
        finally:
            time.sleep(1)

    logging.info(f">>> 新聞內文擷取完成，共成功取得 {len(successful_articles)} 篇。")
    return successful_articles


# ==============================================================================
# --- OpenAI & LLM 互動模組 ---
# ==============================================================================

def call_openai_api(system_prompt, user_prompt, model=OPENAI_COMPLETION_MODEL, max_tokens=4000, temperature=0.7):
    if not OPENAI_API_KEY:
        logging.error("OPENAI_API_KEY is not set.")
        return "抱歉，API Key 未設定，無法處理您的請求。"

    headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "Content-Type": "application/json",
        "ngrok-skip-browser-warning": "true"
        }
    data = { "model": model, "messages": [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}], "max_tokens": max_tokens, "temperature": temperature }
    
    try:
        response = requests.post(f"{OPENAI_BASE_URL}/v1/chat/completions", headers=headers, json=data, timeout=980)
        response.raise_for_status()
        content = response.json()["choices"][0]["message"]["content"].strip()
        logging.info(f"OpenAI API 呼叫成功，模型: {model}，回應長度: {len(content)}")
        return content
    except requests.exceptions.Timeout:
        logging.error(f"OpenAI API request timed out. Model: {model}")
        return f"抱歉，請求 OpenAI ({model}) 服務超時。"
    except requests.exceptions.RequestException as e:
        logging.error(f"OpenAI API request error: {e}. Model: {model}")
        return f"抱歉，連接 OpenAI ({model}) 服務時發生錯誤。"
    except (KeyError, IndexError, TypeError) as e:
        response_text = response.text if 'response' in locals() else 'N/A'
        logging.error(f"OpenAI API response format error: {e} - Response: {response_text}")
        return f"抱歉，OpenAI ({model}) 回應格式有問題。"
    except Exception as e:
        logging.error(f"Unexpected error in call_openai_api: {e}", exc_info=True)
        return "抱歉，生成回覆時發生未知錯誤。"

def generate_chat_response(prompt_text):
    system_prompt = (
        "你是一個通訊軟體的聊天機器人。回答要精簡、口語化，使用台灣常用的繁體中文。"
        "如果答案需要思考步驟，請將思考過程用 <think> 和 </think> 標籤包起來。"
    )
    return call_openai_api(system_prompt, prompt_text)


# ==============================================================================
# --- 新聞摘要與整合模組 (兩階段摘要) ---
# ==============================================================================

def summarize_news_flow(articles_data):
    if not articles_data:
        logging.info("沒有文章可供摘要。")
        return "今天沒有抓取到相關新聞可供摘要。"

    # --- Stage 1: Individual Summaries ---
    logging.info("--- 開始第一階段摘要：逐篇精簡 ---")
    individual_summaries = []
    for i, article in enumerate(articles_data):
        logging.info(f"  正在摘要第 {i+1}/{len(articles_data)} 篇: {article['title']}")
        content_to_summarize = article['text'][:8000]
        user_prompt = f"新聞標題：{article['title']}\n\n新聞內文：\n{content_to_summarize}"
        
        raw_summary = call_openai_api(
            system_prompt=PROMPT_FOR_INDIVIDUAL_SUMMARY, user_prompt=user_prompt,
            model=os.getenv("OPENAI_COMPLETION_MODEL", "gpt-4o-mini"),
            max_tokens=4500, temperature=0.2
        )
        
        if raw_summary.startswith("抱歉，"):
            logging.warning(f"  [跳過] 第 {i+1} 篇新聞摘要失敗: {raw_summary}")
            continue

        # *** 修改開始 ***
        # 使用正規表示式來移除 <think>...</think> 區塊
        # re.DOTALL 讓 '.' 可以匹配換行符，re.IGNORECASE 忽略大小寫
        think_pattern = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
        cleaned_summary = re.sub(think_pattern, '', raw_summary).strip()
        
        # 增加日誌，方便觀察清理效果
        if len(raw_summary) != len(cleaned_summary):
             logging.info(f"  已清理掉 <think> 標籤。原始長度: {len(raw_summary)}, 清理後長度: {len(cleaned_summary)}")
        # *** 修改結束 ***

        individual_summaries.append({'title': article['title'], 'url': article['url'], 'summary': cleaned_summary})
        logging.info(f"  摘要完成，長度: {len(cleaned_summary)} 字")
        print(individual_summaries[-1])
        time.sleep(1)

    if not individual_summaries:
        logging.warning("所有新聞在第一階段摘要都失敗了。")
        return "抱歉，今日新聞摘要生成過程發生問題，無法產出內容。"

    # --- Stage 2: Final Aggregation ---
    logging.info("--- 開始第二階段摘要：彙整生成 Podcast 內容 ---")
    summaries_for_prompt = [f"新聞 {i+1}:\n標題: {item['title']}\n摘要內容: {item['summary']}\n---" for i, item in enumerate(individual_summaries)]
    final_user_prompt = "\n".join(summaries_for_prompt)
    
    final_summary = call_openai_api(
        system_prompt=PROMPT_FOR_FINAL_AGGREGATION, user_prompt=final_user_prompt,
        model=os.getenv("OPENAI_COMPLETION_MODEL", "gpt-4o"),
        max_tokens=3000, temperature=0.7
    )
    return final_summary


# ==============================================================================
# --- Line Bot 基礎功能 ---
# ==============================================================================

def load_user_preferences():
    try:
        with open(USER_PREFERENCES_FILE, "r", encoding='utf-8') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}

def save_user_preferences(preferences_data):
    try:
        with open(USER_PREFERENCES_FILE, "w", encoding='utf-8') as f:
            json.dump(preferences_data, f, ensure_ascii=False, indent=4)
        logging.info(f"用戶偏好已儲存。共 {len(preferences_data)} 筆記錄。")
    except Exception as e:
        logging.error(f"儲存用戶偏好設定失敗: {e}")

USER_PREFERENCES = load_user_preferences()

def validate_signature(request_body_bytes, signature_header):
    if not LINE_CHANNEL_SECRET: return True
    hash_obj = hmac.new(LINE_CHANNEL_SECRET.encode('utf-8'), request_body_bytes, hashlib.sha256)
    generated_signature = base64.b64encode(hash_obj.digest()).decode('utf-8')
    return hmac.compare_digest(generated_signature, signature_header)

def split_long_message(text, limit=MAX_MESSAGE_LENGTH):
    messages = []
    if not text or not text.strip(): return []
    if len(text) <= limit: return [text.strip()]
    
    chunks = []
    current_chunk = ""
    paragraphs = text.split('\n')
    for para in paragraphs:
        if len(current_chunk) + len(para) + 1 <= limit:
            current_chunk += para + '\n'
        else:
            if current_chunk:
                chunks.append(current_chunk.strip())
            # 如果單一段落就超過長度，強制分割
            if len(para) > limit:
                for i in range(0, len(para), limit):
                    chunks.append(para[i:i+limit])
            else:
                current_chunk = para + '\n'
    if current_chunk:
        chunks.append(current_chunk.strip())

    if len(chunks) > 1:
        for i, chunk in enumerate(chunks):
            page_indicator = f"({i+1}/{len(chunks)})\n"
            messages.append(page_indicator + chunk)
    else:
        messages = chunks
        
    return [m for m in messages if m]

def send_line_messages(user_id, reply_token_or_none, text_messages_list):
    if not text_messages_list:
        logging.warning(f"沒有訊息可發送給 {user_id}")
        return

    headers = {"Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}", "Content-Type": "application/json"}
    
    # 處理第一則訊息，優先使用 reply token
    first_message_sent = False
    if reply_token_or_none:
        payload = {"replyToken": reply_token_or_none, "messages": [{"type": "text", "text": str(text_messages_list[0])}]}
        try:
            response = requests.post("https://api.line.me/v2/bot/message/reply", headers=headers, json=payload, timeout=20)
            response.raise_for_status()
            logging.info(f"Reply message sent to user {user_id}. Content: {str(text_messages_list[0])[:50]}...")
            first_message_sent = True
        except requests.exceptions.RequestException as e:
            logging.error(f"Failed to send Reply message to {user_id}: {e} - Response: {e.response.text if e.response else 'N/A'}")
    
    # 後續訊息或沒有 reply token 的情況，使用 push API
    start_index = 1 if first_message_sent else 0
    for i in range(start_index, len(text_messages_list)):
        if not user_id:
            logging.error("Cannot push message: user_id is missing.")
            continue
        
        # 加入延遲避免發送過快
        if i > 0 or first_message_sent:
            time.sleep(VISUAL_SEPARATION_DELAY)
            
        payload = {"to": user_id, "messages": [{"type": "text", "text": str(text_messages_list[i])}]}
        try:
            response = requests.post("https://api.line.me/v2/bot/message/push", headers=headers, json=payload, timeout=20)
            response.raise_for_status()
            logging.info(f"Push message sent to user {user_id} (Part {i+1}/{len(text_messages_list)}). Content: {str(text_messages_list[i])[:50]}...")
        except requests.exceptions.RequestException as e:
            logging.error(f"Failed to send Push message to {user_id}: {e} - Response: {e.response.text if e.response else 'N/A'}")


# ==============================================================================
# --- 核心業務邏輯與 Webhook 事件處理 ---
# ==============================================================================

def generate_and_push_news_for_user(user_id, user_custom_keywords=None, is_immediate_push=False, test_limit=None, reply_token=None):
    """為指定用戶獲取、摘要並推播新聞的完整流程"""
    log_prefix = "即時請求" if is_immediate_push else "排程推播"
    logging.info(f"[{log_prefix}] 開始為用戶 {user_id} 產生新聞...")
    
    # 步驟 1: 抓取文章
    articles = fetch_and_parse_articles(
        custom_query=user_custom_keywords, 
        limit=test_limit if test_limit is not None else NEWS_FETCH_TARGET_COUNT
    )

    if not articles:
        keywords_msg = f"「{user_custom_keywords}」" if user_custom_keywords else "預設主題"
        message_to_send = f"抱歉，目前未能根據您的關鍵字 {keywords_msg} 找到可成功擷取的新聞。要不要換個關鍵字試試看？"
        send_line_messages(user_id, reply_token, [message_to_send])
        logging.info(f"[{log_prefix}] 沒有抓到文章，已通知用戶 {user_id}。")
        return
    
    # 步驟 2: 執行兩階段摘要，獲取最終可能包含 <think> 的字串
    final_summary_with_think = summarize_news_flow(articles)
    
    # *** 修改開始 ***
    # 步驟 3: 使用新的共用函式來處理並發送最終摘要
    if not final_summary_with_think or final_summary_with_think.startswith("抱歉，"):
        logging.error(f"[{log_prefix}] 最終摘要為空或生成失敗，無法發送給 {user_id}。")
        # 如果是即時請求，用 reply_token 回覆；否則用 push
        send_line_messages(user_id, reply_token, [final_summary_with_think or "抱歉，今日新聞摘要生成異常，內容為空。"])
        return

    # 將最終摘要交給新的處理函式，它會自動處理 <think> 標籤並發送
    handle_llm_response_with_think(user_id, reply_token, final_summary_with_think)
    # *** 修改結束 ***

    logging.info(f"[{log_prefix}] 已完成對用戶 {user_id} 的新聞推送。")

@app.route('/webhook', methods=['POST'])
def webhook():
    signature = request.headers.get("X-Line-Signature")
    body_bytes = request.get_data()
    if not validate_signature(body_bytes, signature):
        logging.error("Webhook: Invalid signature.")
        return jsonify({"status": "invalid signature"}), 400
    
    try:
        data = request.json
        for event in data.get("events", []):
            event_type = event.get("type")
            source = event.get("source", {})
            user_id = source.get("userId")
            reply_token = event.get("replyToken")
            
            logging.info(f"收到事件: type={event_type}, user_id={user_id}")

            if event_type == "message" and event.get("message", {}).get("type") == "text":
                handle_text_message_event(user_id, reply_token, event["message"]["text"])
            
            elif event_type == "follow":
                if user_id and reply_token:
                    user_pref = USER_PREFERENCES.get(user_id, {})
                    user_pref["subscribed_news"] = True
                    user_pref["news_keywords"] = None # 預設使用全局關鍵字
                    USER_PREFERENCES[user_id] = user_pref
                    save_user_preferences(USER_PREFERENCES)
                    send_line_messages(user_id, reply_token, ["感謝您加我好友！我將嘗試每日為您推播AI相關新聞彙整。輸入「訂閱新聞 [您的關鍵字]」可自訂主題，或輸入「取消訂閱新聞」來取消推播。"])

            elif event_type == "unfollow":
                if user_id in USER_PREFERENCES:
                    logging.info(f"用戶 {user_id} 已封鎖/刪除好友。")
                    USER_PREFERENCES[user_id]["subscribed_news"] = False
                    save_user_preferences(USER_PREFERENCES)

        return jsonify({"status": "success"}), 200
    except Exception as e:
        logging.error(f"處理 webhook 時發生錯誤: {e}", exc_info=True)
        return jsonify({"status": "error", "message": str(e)}), 500

def handle_llm_response_with_think(user_id, reply_token, llm_full_response):
    """
    處理帶有 <think> 標籤的 LLM 回應，並將其分離發送。
    這是一個可共用的函式，用於聊天和新聞摘要。
    """
    think_pattern = re.compile(r"<think>(.*?)</think>", re.DOTALL | re.IGNORECASE)
    match = think_pattern.search(llm_full_response)
    
    reply_token_has_been_used = False

    if match:
        thinking_process_text = match.group(1).strip()
        formal_reply_text = llm_full_response[match.end():].strip()
        logging.info(f"CoT found for {user_id}. Thinking: '{thinking_process_text[:30]}...', Formal: '{formal_reply_text[:30]}...'")
        
        # 發送思考過程
        if thinking_process_text:
            think_chunks = split_long_message(f"⚙️ 我的思考過程：\n{thinking_process_text}")
            if think_chunks:
                send_line_messages(user_id, reply_token, think_chunks)
                reply_token_has_been_used = True
        
        # 如果有思考過程，等待一下再發送正式回覆
        if reply_token_has_been_used and formal_reply_text:
            logging.info(f"Delaying {VISUAL_SEPARATION_DELAY}s before sending formal reply to {user_id}.")
            time.sleep(VISUAL_SEPARATION_DELAY)
        
        # 發送正式回覆
        if formal_reply_text:
            formal_chunks = split_long_message(formal_reply_text)
            # 如果 reply_token 已用過，這裡必須傳入 None
            send_line_messages(user_id, None if reply_token_has_been_used else reply_token, formal_chunks)
        elif not thinking_process_text: # 只有 <think></think> 標籤但內容為空
            send_line_messages(user_id, reply_token, ["嗯...我好像什麼都沒想到。"])

    else:
        # 沒有 <think> 標籤，直接發送完整回應
        logging.info(f"No CoT found in LLM response for {user_id}.")
        response_chunks = split_long_message(llm_full_response)
        if not response_chunks and llm_full_response.strip():
            response_chunks = [llm_full_response.strip()]
        elif not response_chunks:
            response_chunks = ["我目前沒有回應。"]
        send_line_messages(user_id, reply_token, response_chunks)
        
def handle_text_message_event(user_id, reply_token, user_text):
    user_text_stripped = user_text.strip()
    user_text_lower = user_text_stripped.lower()
    
    subscribe_command = "訂閱新聞"
    unsubscribe_command = "取消訂閱新聞"
    user_pref = USER_PREFERENCES.get(user_id, {})

    if user_text_stripped.startswith(subscribe_command):
        keywords_from_user = user_text_stripped[len(subscribe_command):].strip()
        user_pref["subscribed_news"] = True
        
        if keywords_from_user:
            user_pref["news_keywords"] = keywords_from_user
            reply_msg = f"好的👌！已為您訂閱每日新聞，關鍵字為：「{keywords_from_user}」。我馬上為您整理一份最新的！"
        else:
            user_pref["news_keywords"] = None
            reply_msg = f"好的👌！已為您訂閱每日新聞（使用預設主題）。我馬上為您整理一份最新的！"
        
        USER_PREFERENCES[user_id] = user_pref
        save_user_preferences(USER_PREFERENCES)
        
        # 立即觸發新聞推播，這裡傳入 reply_token
        # 新的 generate_and_push_news_for_user 函式會正確處理它
        generate_and_push_news_for_user(user_id, user_pref["news_keywords"], is_immediate_push=True, reply_token=reply_token)
        return

    elif user_text_lower == unsubscribe_command:
        user_pref["subscribed_news"] = False
        USER_PREFERENCES[user_id] = user_pref
        save_user_preferences(USER_PREFERENCES)
        send_line_messages(user_id, reply_token, ["已為您取消訂閱每日新聞。江湖再見！"])
        return

    # --- 一般對話機器人邏輯 ---
    is_triggered = any(user_text_lower.startswith(name.lower()) for name in BOT_NAMES if name)
    if BOT_DEACTIVATED or not is_triggered:
        return

    prompt_for_llm = user_text_stripped
    for name in BOT_NAMES:
        if name and user_text_lower.startswith(name.lower()):
            prompt_for_llm = user_text_stripped[len(name):].strip()
            break
    
    if not prompt_for_llm:
        send_line_messages(user_id, reply_token, ["嗨！有什麼事嗎？"])
        return

    llm_response = generate_chat_response(prompt_for_llm)

    # *** 修改開始 ***
    # 直接呼叫新的共用函式來處理回覆
    handle_llm_response_with_think(user_id, reply_token, llm_response)
    # *** 修改結束 ***


# ==============================================================================
# --- 排程與應用啟動 ---
# ==============================================================================
scheduler = BackgroundScheduler(timezone="Asia/Taipei", daemon=True)

def daily_news_push_job():
    with app.app_context():
        logging.info("APScheduler: 開始執行每日新聞推播任務...")
        current_prefs = load_user_preferences()
        
        users_to_push = []
        for uid, prefs in current_prefs.items():
            if prefs.get("subscribed_news", False):
                users_to_push.append((uid, prefs.get("news_keywords")))

        if TARGET_USER_ID_FOR_TESTING:
            is_test_user_subscribed = any(u[0] == TARGET_USER_ID_FOR_TESTING for u in users_to_push)
            if not is_test_user_subscribed:
                test_user_keywords = current_prefs.get(TARGET_USER_ID_FOR_TESTING, {}).get("news_keywords")
                users_to_push.append((TARGET_USER_ID_FOR_TESTING, test_user_keywords))
                logging.info(f"APScheduler: 將測試用戶 {TARGET_USER_ID_FOR_TESTING} 加入推播列表。")

        if not users_to_push:
            logging.info("APScheduler: 沒有需要推播的用戶。")
            return
            
        logging.info(f"APScheduler: 準備推播新聞給 {len(users_to_push)} 位用戶。")

        for user_id, keywords in users_to_push:
            try:
                generate_and_push_news_for_user(user_id, keywords, is_immediate_push=False)
                time.sleep(5) # 避免對 API 和網站造成太大壓力
            except Exception as e:
                logging.error(f"APScheduler: 為用戶 {user_id} 推播新聞時發生錯誤: {e}", exc_info=True)
        
        logging.info("APScheduler: 每日新聞推播任務執行完畢。")

def shutdown_scheduler_on_exit():
    if scheduler.running:
        scheduler.shutdown(wait=False)
        logging.info("APScheduler shut down.")


# ==============================================================================
# --- ✨ 本地測試模式 (修改後版本) ✨ ---
# ==============================================================================
def run_test_mode(keywords, limit):
    """執行本地測試流程，並模擬最終發送行為"""
    print("\n" + "="*50)
    print("🚀 進入本地測試模式 🚀")
    print("="*50 + "\n")
    
    test_keywords = keywords if keywords else None
    test_limit = limit if limit is not None else NEWS_FETCH_TARGET_COUNT

    print(f"[*] 測試參數:")
    print(f"    - 關鍵字: '{test_keywords if test_keywords else '預設關鍵字'}'")
    print(f"    - 處理文章上限: {test_limit}\n")

    articles = fetch_and_parse_articles(custom_query=test_keywords, limit=test_limit)
    
    if not articles:
        print("\n[!] 測試中止：未能成功擷取任何新聞內文。")
        return
        
    final_summary_with_think = summarize_news_flow(articles)
    
    # *** 修改開始 ***
    # 我們不再直接 print 原始摘要，而是模擬 handle_llm_response_with_think 的行為
    
    print("\n" + "="*50)
    print("📦 模擬訊息發送 (預覽發送給 Line 的最終內容)")
    print("="*50)

    think_pattern = re.compile(r"<think>(.*?)</think>", re.DOTALL | re.IGNORECASE)
    match = think_pattern.search(final_summary_with_think)
    
    if match:
        thinking_process_text = match.group(1).strip()
        formal_reply_text = final_summary_with_think[match.end():].strip()
        
        # 模擬發送思考過程
        if thinking_process_text:
            think_chunks = split_long_message(f"⚙️ 我的思考過程：\n{thinking_process_text}")
            print(f"--- 偵測到思考過程 (共 {len(think_chunks)} 則訊息) ---")
            for i, chunk in enumerate(think_chunks):
                print(f"--- [思考訊息 {i+1}] ---\n{chunk}")
            print("-" * 20)

        # 模擬發送正式回覆
        if formal_reply_text:
            formal_chunks = split_long_message(formal_reply_text)
            print(f"--- 正式回覆 (共 {len(formal_chunks)} 則訊息) ---")
            for i, chunk in enumerate(formal_chunks):
                print(f"--- [正式訊息 {i+1}] ---\n{chunk}")
        
    else:
        # 沒有 <think> 標籤，直接模擬發送完整回應
        print("--- 未偵測到思考過程，直接發送 ---")
        response_chunks = split_long_message(final_summary_with_think)
        for i, chunk in enumerate(response_chunks):
            print(f"--- [訊息 {i+1}] ---\n{chunk}")
            
    # *** 修改結束 ***

    print("\n" + "="*50)
    print("✅ 測試流程結束 ✅")


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="Line Bot and News Fetcher")
    parser.add_argument('--test-news', action='store_true', help='Run in local test mode for news fetching and summarization.')
    parser.add_argument('--keywords', type=str, default=None, help='Keywords for news fetching in test mode.')
    parser.add_argument('--limit', type=int, default=None, help='Number of articles to process in test mode.')
    args = parser.parse_args()

    if args.test_news:
        run_test_mode(args.keywords, args.limit)
    else:
        logging.info("🚀 啟動 Flask Web 伺服器模式 🚀")
        
        required_env_vars = ['LINE_CHANNEL_ACCESS_TOKEN', 'LINE_CHANNEL_SECRET', 'OPENAI_API_KEY']
        missing_vars = [var for var in required_env_vars if not os.getenv(var)]
        if missing_vars:
            logging.critical(f"CRITICAL: Missing required environment variables: {', '.join(missing_vars)}. Exiting.")
            exit(1)

        if not scheduler.get_jobs():
            # 設定排程任務
            # 1. 每日 09:00 的固定排程
            scheduler.add_job(daily_news_push_job, 'cron', hour=9, minute=0, id='daily_news_cron', replace_existing=True)
            logging.info("已設定每日 09:00 的新聞推播排程。")
            
            # 2. 每 480 分鐘 (8 小時) 的間隔排程
            scheduler.add_job(daily_news_push_job, 'interval', minutes=480, id='news_interval_job', replace_existing=True)
            logging.info("已設定每 480 分鐘執行一次新聞推播排程。")
            
            # 3. 程式啟動時立即執行一次的選項
            if os.getenv("RUN_JOB_ON_STARTUP", "False").lower() == "true":
                run_now_time = datetime.now(scheduler.timezone) + timedelta(seconds=15)
                scheduler.add_job(daily_news_push_job, 'date', run_date=run_now_time, id='startup_news_push')
                logging.info(f"已設定在 15 秒後執行一次新聞推播任務。")
        
        if not scheduler.running:
            scheduler.start()
            logging.info("APScheduler started.")
            atexit.register(shutdown_scheduler_on_exit)
        
        port = int(os.environ.get("PORT", 5000))
        app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)