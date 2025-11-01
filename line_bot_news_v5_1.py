# ==============================================================================
# line_bot_v5.py
# 最終整合版本
#
# 功能亮點:
# - 被動監聽群組對話，透過 /bot 指令觸發，實現上下文理解。
# - 支援群組內用戶名稱獲取與快取。
# - 整合了「一次性新聞查詢」與「持久化訂閱管理」的完整指令系統。
# - 所有耗時任務 (新聞處理) 均在背景執行緒中處理。
# - 定時推播任務採用「任務鏈」模式序列化執行，保護後端 LLM 伺服器。
# - Selenium Driver 能夠智慧判斷作業系統，適應不同部署環境。
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
# from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
# from webdriver_manager.chrome import ChromeDriverManager
from selenium.common.exceptions import WebDriverException

# (在檔案頂部，與其他 import 放在一起)
from concurrent.futures import ThreadPoolExecutor, as_completed

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
BOT_TRIGGER_WORD = os.getenv("BOT_TRIGGER_WORD", "/bot")
OPENAI_COMPLETION_MODEL = os.getenv("OPENAI_COMPLETION_MODEL", "gpt-4o-mini")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
LINE_CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET")
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "https://api.openai.com")
TARGET_USER_ID_FOR_TESTING = os.getenv("TARGET_USER_ID_FOR_TESTING")

VISUAL_SEPARATION_DELAY = float(os.getenv("VISUAL_SEPARATION_DELAY", "1.0"))
DEFAULT_NEWS_KEYWORDS = "大型語言模型 OR LLM OR 生成式AI OR OpenAI OR Gemini OR Claude"
USER_PREFERENCES_FILE = "user_preferences.json"
CONVERSATION_HISTORY_FILE = "conversation_history.json"
MAX_HISTORY_MESSAGES = int(os.getenv("MAX_HISTORY_MESSAGES", "50"))
NEWS_FETCH_TARGET_COUNT = 7
USER_AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36'

NEWS_CACHE_FILE = "news_cache.json"
NEWS_SUMMARY_CACHE_SECONDS = 3600 * 4  # 4 小時

# --- 用戶個人資料快取 (in-memory) ---
USER_PROFILE_CACHE = {}
USER_PROFILE_CACHE_SECONDS = 7200  # 快取 2 小時

MAX_MESSAGE_LENGTH = int(os.getenv("MAX_MESSAGE_LENGTH", "4800"))

NEWS_FETCH_MAX_WORKERS=4

# --- 兩階段摘要的 LLM Prompt 設定 ---
PROMPT_FOR_INDIVIDUAL_SUMMARY = (
    "你是一位資深的新聞編輯，專長是快速提煉文章核心。請將以下提供的新聞內文，濃縮成一段不超過150字的客觀、精簡中文摘要。"
    "摘要應包含最關鍵的人物、事件、數據和結論。請直接輸出摘要內容，不要有任何開頭或結尾的客套話。"
)

current_date = datetime.now().strftime("%Y-%m-%d %H:%M")
PROMPT_FOR_FINAL_AGGREGATION = (
    f"今天日期是 {current_date}。\n"
    "你是一位風趣幽默、知識淵博的科技新聞 Podcast 主持人。你的聽眾是 Line 用戶，他們喜歡輕鬆、易懂且帶有 Emoji 的內容。"
    "接下來我會提供數則「已經被精簡過的新聞摘要」。請你根據這些摘要，發揮你的主持風格，將它們整合成一篇連貫的談話性內容。"
    "請只整合最新新聞摘要，過舊的請忽略。"
    "你的任務是：\n"
    "1. 用生動的語氣開場，吸引聽眾注意。\n"
    "2. 將各則新聞摘要自然地串連起來，可以加上你的評論或觀點來銜接，但不要杜撰不存在的事實。\n"
    "3. 在提到每則新聞的重點後，請務必附上這則新聞的原始標題，格式如下：\n"
    "   - 標題：[原始新聞標題]\n"
    "4. 全程多使用 Emoji 來增加活潑感。\n"
    "5. 要嚴肅應對每則有負面情緒的新聞例如災難與傷亡。\n"
    "6. 最後結論要加註這是AI生成的內容，讀者應注意正確性。\n"
    "7. 總結的回答字數限制在500字以下以符合通訊軟體的限制。\n"
)

# --- 機器人指令幫助訊息 ---
HELP_MESSAGE = """
哈囉！👋 我是你的 AI 助理！

你可以透過 `/bot` 指令與我互動。

📰【新聞功能】
🔹 `/bot 新聞`
   立即取得一篇 AI 主題的新聞摘要。
🔹 `/bot 新聞 關鍵字:你想看的內容`
   立即查詢特定主題的新聞。
🔹 `/bot 訂閱`
   訂閱每日 AI 新聞推播。
🔹 `/bot 訂閱 [你的主題]`
   訂閱每日特定主題的新聞。
🔹 `/bot 查看訂閱`
   看看你目前訂閱了什麼。
🔹 `/bot 取消訂閱`
   取消每日新聞推播。

💬【隨意聊天】
除了新聞，也可以隨時用 `/bot` 問我任何問題喔！
範例：`/bot 幫我規劃一下週末行程`
"""

# ==============================================================================
# --- 新聞擷取模組 ---
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
        headers = {"User-Agent": USER_AGENT}
        # 用 GET 且允許跳轉；不下載整頁內容
        with requests.get(google_news_url, headers=headers, allow_redirects=True, timeout=20, stream=True) as r:
            return r.url
    except requests.RequestException as e:
        logging.warning(f"[錯誤] 解析跳轉連結失敗 {google_news_url}: {e}")
        return None

def fetch_article_with_selenium(url):
    logging.info(f"    [備援] Selenium 抓取: {url[:70]}...")
    driver = None
    try:
        driver = webdriver.Chrome(options=chrome_options)
        driver.set_page_load_timeout(20)
        driver.get(url)
        time.sleep(2.5)
        return driver.page_source
    except Exception as e:
        logging.error(f"    [Selenium 錯誤] {e}", exc_info=True)
        return None
    finally:
        if driver:
            try: driver.quit()
            except Exception: pass

def fetch_and_parse_articles(custom_query=None, limit=NEWS_FETCH_TARGET_COUNT):
    """
    *** 已升級 v2：採用平行化處理來加速新聞抓取 ***
    """
    query_to_use = custom_query.strip() if custom_query and custom_query.strip() else DEFAULT_NEWS_KEYWORDS
    encoded_query = urllib.parse.quote_plus(query_to_use)
    rss_url = f"https://news.google.com/rss/search?q={encoded_query}&hl=zh-TW&gl=TW&ceid=TW:zh-Hant"
    
    logging.info(f">>> 開始從 Google News RSS 取得新聞列表 (關鍵字: '{query_to_use}')")
    feed = feedparser.parse(rss_url)

    if feed.bozo:
        logging.error(f"無法解析 RSS feed。錯誤資訊: {feed.bozo_exception}")
        return []

    # 內部輔助函式，用於處理單一文章的完整抓取流程
    def _process_single_entry(entry):
        logging.info(f"  [執行緒] 開始處理: {entry.title}")
        real_url = get_real_url(entry.link)
        if not real_url:
            logging.warning(f"  [執行緒] 跳過: 無法取得真實 URL for {entry.title}")
            return None
        
        try:
            article = Article(real_url, language='zh', config=newspaper_config)
            article.download()
            article.parse()
            
            if len(article.text) < 200:
                logging.warning(f"  [執行緒] 內容過短，為 {entry.title} 啟用 Selenium。")
                html_content = fetch_article_with_selenium(real_url)
                if html_content:
                    article.download(input_html=html_content)
                    article.parse()

            if article.title and len(article.text) > 200:
                logging.info(f"  [執行緒] 成功取得: {article.title}")
                return {
                    'title': article.title,
                    'text': article.text,
                    'url': real_url,
                    'source': entry.source.title if hasattr(entry, 'source') and hasattr(entry.source, 'title') else "未知來源"
                }
            else:
                logging.warning(f"  [執行緒] 失敗: 無法為 {entry.title} 解析足夠內文。")
                return None
        except Exception as e:
            logging.error(f"  [執行緒] 處理 {entry.title} 時發生未預期錯誤: {e}", exc_info=True)
            return None

    # --- 平行化處理核心 ---
    successful_articles = []
    processed_urls = set()
    
    # 我們只處理前 limit * 2 數量的條目，以防很多條目都失敗
    entries_to_process = feed.entries[:limit * 2]
    
    # max_workers 可以根據您的伺服器性能調整，8 是一個比較合理的起始值
    try:
        max_workers = int(os.getenv("NEWS_FETCH_MAX_WORKERS", "4"))
    except ValueError:
        max_workers = 4 # 如果 .env 中的值不是數字，則使用安全的預設值
    
    logging.info(f"啟動 ThreadPoolExecutor，最大平行度 (max_workers) 設為: {max_workers}")

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_entry = {executor.submit(_process_single_entry, entry): entry for entry in entries_to_process}
        
        for future in as_completed(future_to_entry):
            if len(successful_articles) >= limit:
                # 如果已經達到目標數量，我們可以取消還在運行的未來任務
                # 這裡為了簡單起見，我們只跳出迴圈，讓它們繼續運行完
                break

            try:
                result = future.result()
                if result and result['url'] not in processed_urls:
                    successful_articles.append(result)
                    processed_urls.add(result['url'])
            except Exception as exc:
                entry_title = future_to_entry[future].title
                logging.error(f"  [主執行緒] 處理 '{entry_title}' 的任務產生了異常: {exc}")

    logging.info(f">>> 平行化新聞內文擷取完成，共成功取得 {len(successful_articles)} 篇。")
    return successful_articles[:limit] # 最後再裁切一次，確保數量不超過 limit

def _extract_assistant_text_from_response(resp_json: dict) -> str:
    if not resp_json or "choices" not in resp_json or not resp_json["choices"]:
        return ""
    ch0 = resp_json["choices"][0]
    msg = ch0.get("message", {}) or {}

    # 1) content 可能是字串或 parts
    content = msg.get("content")
    if isinstance(content, str) and content.strip():
        return content.strip()
    if isinstance(content, list):
        parts = []
        for p in content:
            if isinstance(p, str):
                parts.append(p)
            elif isinstance(p, dict):
                txt = p.get("text") or p.get("output_text") or p.get("data") or p.get("value") or ""
                if txt: parts.append(txt)
        if parts:
            return "".join(parts).strip()

    # 2) 兼容舊的 choices[0].text
    legacy = ch0.get("text")
    if isinstance(legacy, str) and legacy.strip():
        return legacy.strip()

    # 3) content 不可用 → 試著從 reasoning_content 的 Draft 抽
    rc = msg.get("reasoning_content")
    draft = _extract_draft_from_reasoning(rc or "")
    if draft:
        return draft

    # 4) 最後才看環境開關，是否整包丟回
    if os.getenv("ALLOW_REASONING_FALLBACK", "false").lower() == "true" and isinstance(rc, str) and rc.strip():
        return rc.strip()

    return ""



import re, json

# 常見的 Draft 標記樣式
_DRAFT_PATTERNS = [
    r'Draft[:：]\s*[\"“](.+?)[\"”]\s*$',              # Draft: "...."
    r'Draft[:：]\s*```(?:\w+)?\n(.+?)\n```',          # Draft: ``` ... ```
    r'###\s*Draft\s*\n(.+)$',                         # Markdown 標題 Draft
    r'草稿[:：]\s*(.+)$',                              # 中文「草稿:」
    r'最終稿[:：]\s*(.+)$',                            # 中文「最終稿:」
]

def _extract_draft_from_reasoning(reasoning: str) -> str:
    if not reasoning:
        return ""
    text = reasoning.strip()

    # 1) 先嘗試嚴格的 Draft 標記
    for pat in _DRAFT_PATTERNS:
        m = re.search(pat, text, flags=re.S)
        if m and m.group(1):
            draft = m.group(1).strip()
            # 去掉後面可能接的「字數統計/Count:」
            draft = re.split(r'\n(?:Count|字|characters)[:：]', draft)[0].strip()
            return draft

    # 2) 有些會輸出 JSON，把 "summary" 放在 reasoning 裡
    jm = re.search(r'\{.*\}', text, flags=re.S)
    if jm:
        try:
            obj = json.loads(jm.group(0))
            for key in ("summary", "final", "output", "answer"):
                if isinstance(obj.get(key, ""), str) and obj[key].strip():
                    return obj[key].strip()
        except Exception:
            pass

    # 3) 退而求其次：抓最後一段看起來像完整中文句子的內容
    sent = re.findall(r'[\u4e00-\u9fff，、；：：「」『』（）()A-Za-z0-9%\- ]+[。.!?]', text)
    if sent:
        return sent[-1].strip()

    return ""

# ==============================================================================
# --- OpenAI & LLM 互動模組 ---
# ==============================================================================
def call_openai_api(messages, model=OPENAI_COMPLETION_MODEL, max_tokens=4000, temperature=0.7):
    if not OPENAI_API_KEY:
        logging.error("OPENAI_API_KEY is not set.")
        return "抱歉，API Key 未設定，無法處理您的請求。"
    headers = {"Authorization": f"Bearer {OPENAI_API_KEY}", "Content-Type": "application/json", "ngrok-skip-browser-warning": "true"}
    data = {"model": model, "messages": messages, "max_tokens": max_tokens, "temperature": temperature}
    try:
        response = requests.post(f"{OPENAI_BASE_URL}/v1/chat/completions", headers=headers, json=data, timeout=980)
        response.raise_for_status()
        logging.info(response.json())
#         content = response.json()["choices"][0]["message"]["content"].strip()
        resp_json = response.json()
        logging.info(str(resp_json))  # 建議保留以便除錯

        content = _extract_assistant_text_from_response(resp_json)
        if (not content or not content.strip()) and os.getenv("ALLOW_REASONING_FALLBACK", "false").lower() == "true":
            content = str(resp_json)
        
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

def generate_chat_response(context_id, prompt_text):
    system_prompt = (
        "你是一個在 Line 群組或私聊中的聊天機器人。你的回答要精簡、口語化，使用台灣常用的繁體中文。"
        "你會收到一段包含多人對話的歷史紀錄，每句話前面可能會標示發言者。請完完全全根據完整的上下文進行回答。"
        "請根據我們的對話歷史來回應所有問題。忽略任何外部知識或新主題，也不要根據已知記憶，只使用提供的上下文內容生成答案。"
        "如果答案需要思考步驟，請將思考過程用 <think> 和 </think> 標籤包起來。"
    )
    context_history = CONVERSATION_HISTORY.get(context_id, [])
    messages_for_api = [{"role": "system", "content": system_prompt}] + context_history
    bot_response = call_openai_api(messages_for_api)
    return bot_response

# ==============================================================================
# --- 新聞摘要與整合模組 ---
# ==============================================================================
def summarize_news_flow(articles_data):
    if not articles_data: return "今天沒有抓取到相關新聞可供摘要。"
    logging.info("--- 開始第一階段摘要：逐篇精簡 ---")
    individual_summaries = []
    for i, article in enumerate(articles_data):
        logging.info(f"  正在摘要第 {i+1}/{len(articles_data)} 篇: {article['title']}")
        content_to_summarize = article['text'][:8000]
        user_prompt = f"新聞標題：{article['title']}\n\n新聞內文：\n{content_to_summarize}"
        raw_summary = call_openai_api([{"role": "system", "content": PROMPT_FOR_INDIVIDUAL_SUMMARY}, {"role": "user", "content": user_prompt}], model=os.getenv("OPENAI_COMPLETION_MODEL", "gpt-4o-mini"), max_tokens=3500, temperature=0.2)
        
        logging.info(f"  user_prompt: {user_prompt}")
        logging.info(f"  raw_summary: {raw_summary}")
        
        if raw_summary.startswith("抱歉，"):
            logging.warning(f"  [跳過] 第 {i+1} 篇新聞摘要失敗: {raw_summary}")
            continue
        think_pattern = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
        cleaned_summary = re.sub(think_pattern, '', raw_summary).strip()
        if len(raw_summary) != len(cleaned_summary): logging.info(f"  已清理掉 <think> 標籤。")
        individual_summaries.append({'title': article['title'], 'url': article['url'], 'summary': cleaned_summary})
        logging.info(f"  摘要完成，長度: {len(cleaned_summary)} 字")
        logging.info(f"  等待30秒 避免LLM速率限制")
        time.sleep(30) # 降低LLM速率
    if not individual_summaries: return "抱歉，今日新聞摘要生成過程發生問題，無法產出內容。"
    logging.info("--- 開始第二階段摘要：彙整生成 Podcast 內容 ---")
    summaries_for_prompt = [f"新聞 {i+1}:\n標題: {item['title']}\n摘要內容: {item['summary']}\n---" for i, item in enumerate(individual_summaries)]
    final_user_prompt = "\n".join(summaries_for_prompt)
    final_summary = call_openai_api([{"role": "system", "content": PROMPT_FOR_FINAL_AGGREGATION}, {"role": "user", "content": final_user_prompt}], model=os.getenv("OPENAI_COMPLETION_MODEL", "gpt-4o"), max_tokens=3000, temperature=0.7)
    return final_summary

# ==============================================================================
# --- Line Bot 基礎功能與資料處理 ---
# ==============================================================================
def load_json_data(file_path):
    try:
        with open(file_path, "r", encoding='utf-8') as f: return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError): return {}

def save_json_data(data, file_path):
    try:
        with open(file_path, "w", encoding='utf-8') as f: json.dump(data, f, ensure_ascii=False, indent=4)
    except Exception as e: logging.error(f"儲存檔案 {file_path} 失敗: {e}")

USER_PREFERENCES = load_json_data(USER_PREFERENCES_FILE)
CONVERSATION_HISTORY = load_json_data(CONVERSATION_HISTORY_FILE)
NEWS_CACHE = load_json_data(NEWS_CACHE_FILE) # <-- 新增這一行

def validate_signature(request_body_bytes, signature_header):
    if not LINE_CHANNEL_SECRET: return True
    hash_obj = hmac.new(LINE_CHANNEL_SECRET.encode('utf-8'), request_body_bytes, hashlib.sha256)
    generated_signature = base64.b64encode(hash_obj.digest()).decode('utf-8')
    return hmac.compare_digest(generated_signature, signature_header)

def _utf16_len(s: str) -> int:
    # LINE 以 UTF-16 code units 計數
    return len(s.encode('utf-16-le')) // 2

def _slice_by_utf16(s: str, max_units: int):
    # 逐字累進，確保不把 surrogate pair 切斷
    buf, acc = [], 0
    for ch in s:
        u = _utf16_len(ch)
        if acc + u > max_units:
            yield ''.join(buf)
            buf, acc = [ch], u
        else:
            buf.append(ch); acc += u
    if buf: yield ''.join(buf)
    
def split_long_message(text, limit=None):
    if not text or not text.strip():
        return []
    # 以 5000 code units 為預設上限（官方文檔）
    # https://developers.line.biz/en/docs/partner-docs/development-guidelines/
    limit = limit or 5000

    if _utf16_len(text) <= limit:
        return [text.strip()]

    # 先按段落切，再用 UTF-16 安全切片
    messages = []
    current = ""
    for para in text.split('\n'):
        if _utf16_len(current + para + '\n') <= limit:
            current += para + '\n'
        else:
            if current:
                messages.append(current.strip()); current = ""
            if _utf16_len(para) > limit:
                messages.extend(list(_slice_by_utf16(para, limit)))
            else:
                current = para + '\n'
    if current:
        messages.append(current.strip())

    if len(messages) > 1:
        messages = [f"({i+1}/{len(messages)})\n{m}" for i, m in enumerate(messages)]
    return messages

LAST_PUSH_TS = 0
MIN_PUSH_INTERVAL_SEC = float(os.getenv("LINE_MIN_PUSH_INTERVAL_SEC", "1.2"))  # 視方案微調

def _throttle():
    global LAST_PUSH_TS
    now = time.time()
    gap = now - LAST_PUSH_TS
    if gap < MIN_PUSH_INTERVAL_SEC:
        time.sleep(MIN_PUSH_INTERVAL_SEC - gap)
    LAST_PUSH_TS = time.time()

def send_line_messages(context_id, reply_token_or_none, text_messages_list):
    if not text_messages_list: return
    headers = {"Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}", "Content-Type": "application/json"}

    def _push_one(msg_text):
        _throttle()
        payload = {"to": context_id, "messages": [{"type": "text", "text": str(msg_text)}]}
        r = requests.post("https://api.line.me/v2/bot/message/push", headers=headers, json=payload, timeout=20)
        if r.status_code == 429:
            logging.warning("Push 429，將延遲重試一次...")
            time.sleep(MIN_PUSH_INTERVAL_SEC * 2.5)
            _throttle()
            r = requests.post("https://api.line.me/v2/bot/message/push", headers=headers, json=payload, timeout=20)
        r.raise_for_status()

    # 先嘗試 reply
    is_first_replied = False
    if reply_token_or_none:
        try:
            payload = {"replyToken": reply_token_or_none, "messages": [{"type": "text", "text": str(text_messages_list[0])}]}
            r = requests.post("https://api.line.me/v2/bot/message/reply", headers=headers, json=payload, timeout=20)
            r.raise_for_status()
            is_first_replied = True
        except requests.exceptions.RequestException as e:
            logging.error(f"Reply 失敗，改用 Push：{e}")

    # 其餘用 Push
    start = 1 if is_first_replied else 0
    for i in range(start, len(text_messages_list)):
        try:
            _push_one(text_messages_list[i])
        except requests.exceptions.RequestException as e:
            logging.error(f"Push 失敗 part {i+1}：{e}")
            break
        
def get_user_profile(context_id, user_id):
    cache_key = (context_id, user_id)
    current_time = time.time()
    if cache_key in USER_PROFILE_CACHE and current_time - USER_PROFILE_CACHE[cache_key]['timestamp'] < USER_PROFILE_CACHE_SECONDS:
        return USER_PROFILE_CACHE[cache_key]['displayName']
    if context_id.startswith('G') or context_id.startswith('R'): url = f"https://api.line.me/v2/bot/group/{context_id}/member/{user_id}"
    elif context_id.startswith('U'): url = f"https://api.line.me/v2/bot/profile/{user_id}"
    else: return "未知用戶"
    headers = {"Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}"}
    try:
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        profile_data = response.json()
        display_name = profile_data.get("displayName", "無名氏")
        USER_PROFILE_CACHE[cache_key] = {"displayName": display_name, "timestamp": current_time}
        logging.info(f"透過 API 取得用戶 {user_id} 的名稱: {display_name}，並已更新快取。")
        return display_name
    except requests.exceptions.RequestException as e:
        logging.warning(f"無法獲取用戶 {user_id} 的個人資料: {e}")
        return "某位成員"

# ==============================================================================
# --- 核心業務邏輯與 Webhook 事件處理 ---
# ==============================================================================
def generate_and_push_news_for_user(user_id, user_custom_keywords=None, is_immediate_push=False, reply_token=None):
    """
    *** 已升級 v6：增加了主題回溯功能，並保持快取結構不變 ***
    """
    log_prefix = "即時請求" if is_immediate_push else "排程推播"
    logging.info(f"[{log_prefix}] 開始為用戶 {user_id} 處理新聞請求...")
    
    # *** 確定主題名稱和快取鍵 ***
    theme_name = user_custom_keywords if user_custom_keywords else "預設 AI 主題"
    cache_key = user_custom_keywords if user_custom_keywords else "__DEFAULT__"
    current_time = time.time()

    # --- 步驟 1: 檢查快取 ---
    if cache_key in NEWS_CACHE:
        cached_item = NEWS_CACHE[cache_key]
        cache_age = current_time - cached_item.get("timestamp", 0)
        
        if cache_age < NEWS_SUMMARY_CACHE_SECONDS:
            logging.info(f"新聞快取命中！(關鍵字: '{cache_key}', 年齡: {int(cache_age)}秒)")
            cached_reply_content = cached_item.get("reply_content")
            if cached_reply_content:
                # 在快取內容前，加上主題和 "從快取提供" 的提示
                final_reply = f"這份新聞摘要根據「{theme_name}」主題產生（從快取提供😊）\n\n{cached_reply_content}"
                send_line_messages(user_id, reply_token, split_long_message(final_reply))
                return

    # --- 步驟 2: 如果快取未命中，執行完整流程 ---
    logging.info(f"新聞快取未命中或已過期 (關鍵字: '{cache_key}')，執行完整新聞摘要流程。")
    articles = fetch_and_parse_articles(custom_query=user_custom_keywords, limit=NEWS_FETCH_TARGET_COUNT)
    if not articles:
        send_line_messages(user_id, reply_token, [f"抱歉，目前未能根據您的關鍵字「{theme_name}」找到可成功擷取的新聞。"])
        return

    final_summary_raw = summarize_news_flow(articles)
    if not final_summary_raw or final_summary_raw.startswith("抱歉，"):
        send_line_messages(user_id, reply_token, [final_summary_raw or "抱歉，今日新聞摘要生成異常，內容為空。"])
        return

    # --- 步驟 3: 對 LLM 原始輸出進行格式化 ---
    # 注意：我們不再將主題或思考過程存入快取，只存最核心的、帶時間戳記的正式回覆
    parsed_result = handle_llm_response_with_think(final_summary_raw)
    thinking_messages = parsed_result["thinking_messages"]
    formal_messages = parsed_result["formal_messages"]

    # 準備要發送和儲存的正式內容
    final_formal_reply_for_cache = ""
    if formal_messages:
        generation_time = datetime.fromtimestamp(current_time)
        time_str = generation_time.strftime("%Y-%m-%d %H:%M")
        full_formal_text = "\n".join(formal_messages)
        # 這是要存入快取的內容，只包含時間戳和摘要
        final_formal_reply_for_cache = f"產生於 {time_str}\n\n{full_formal_text}"
    
    # --- 步驟 4: 儲存新的快取 ---
    if final_formal_reply_for_cache:
        NEWS_CACHE[cache_key] = {
            "timestamp": current_time,
            "reply_content": final_formal_reply_for_cache # 儲存不含主題的、帶時間戳記的內容
        }
        save_json_data(NEWS_CACHE, NEWS_CACHE_FILE)
        logging.info(f"已更新新聞快取 (關鍵字: '{cache_key}')。")

    # --- 步驟 5: 組合最終發送給用戶的完整訊息 ---
    # 在要發送的內容前，才加上主題
    final_reply_for_user = f"這份新聞摘要根據「{theme_name}」主題產生\n\n{final_formal_reply_for_cache}"
    
    messages_to_send = thinking_messages + split_long_message(final_reply_for_user)
    send_line_messages(user_id, reply_token, messages_to_send)
    
    logging.info(f"[{log_prefix}] 已完成對用戶 {user_id} 的新聞推送。")

def generate_news_for_single_user_job(user_id, keywords, remaining_users, is_immediate=False):
    with app.app_context():
        log_prefix = "背景即時請求" if is_immediate else "背景排程推播"
        logging.info(f"[{log_prefix}] 任務鏈啟動，為用戶 {user_id} 產生新聞...")
        try:
            generate_and_push_news_for_user(user_id=user_id, user_custom_keywords=keywords, is_immediate_push=is_immediate, reply_token=None)
        except Exception as e:
            logging.error(f"[{log_prefix}] 背景任務為用戶 {user_id} 產生新聞時發生未預期錯誤: {e}", exc_info=True)
        finally:
            if remaining_users:
                next_user_id, next_user_keywords = remaining_users[0]
                next_remaining_users = remaining_users[1:]
                run_time = datetime.now(scheduler.timezone) + timedelta(seconds=10)
                job_id = f"scheduled_chain_{next_user_id}_{int(run_time.timestamp())}"
                scheduler.add_job(generate_news_for_single_user_job, 'date', run_date=run_time, args=[next_user_id, next_user_keywords, next_remaining_users, False], id=job_id)
                logging.info(f"任務鏈：為用戶 {user_id} 的任務已完成，已註冊下一個任務給 {next_user_id}。")
            else:
                logging.info(f"任務鏈：為用戶 {user_id} 的任務已完成，任務鏈結束。")

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
            source, event_type, reply_token = event.get("source", {}), event.get("type"), event.get("replyToken")
            source_type = source.get("type")
            context_id = source.get(f'{source_type}Id') if source_type else None
            if not context_id: continue
            logging.info(f"收到事件: type={event_type}, source_type={source_type}, context_id={context_id}")
            if event_type == "message" and event.get("message", {}).get("type") == "text":
                handle_text_message_event(context_id=context_id, user_id=source.get('userId'), reply_token=reply_token, user_text=event["message"]["text"])
            elif event_type == "follow":
                user_pref = USER_PREFERENCES.get(context_id, {})
                user_pref["subscribed_news"] = True
                USER_PREFERENCES[context_id] = user_pref
                save_json_data(USER_PREFERENCES, USER_PREFERENCES_FILE)
                send_line_messages(context_id, reply_token, ["感謝您加我好友！輸入 `/bot 幫助` 可以查看所有指令喔。"])
            elif event_type == "unfollow" and context_id in USER_PREFERENCES:
                USER_PREFERENCES[context_id]["subscribed_news"] = False
                save_json_data(USER_PREFERENCES, USER_PREFERENCES_FILE)
        return jsonify({"status": "success"}), 200
    except Exception as e:
        logging.error(f"處理 webhook 時發生錯誤: {e}", exc_info=True)
        return jsonify({"status": "error", "message": str(e)}), 500

def handle_llm_response_with_think(llm_full_response):
    """
    解析帶有 <think> 的回應；若正式內容為空，且開啟備援，則「全取」直接回傳可用內容。
    """
    think_pattern = re.compile(r"<think>(.*?)</think>", re.DOTALL | re.IGNORECASE)

    result = {
        "thinking_messages": [],
        "formal_messages": []
    }

    show_thinking = os.getenv("SHOW_THINKING_PROCESS", "false").lower() == "true"
    fallback_on_empty = os.getenv("FALLBACK_ON_EMPTY", "true").lower() == "true"

    # 先抓 <think>
    match = think_pattern.search(llm_full_response or "")
    if match:
        thinking_text = (match.group(1) or "").strip()
        formal_text = (llm_full_response[match.end():] or "").strip()

        if thinking_text and show_thinking:
            result["thinking_messages"] = split_long_message(f"⚙️ 我的思考過程：\n{thinking_text}")

        if formal_text:
            result["formal_messages"] = split_long_message(formal_text)
        else:
            # 正式內容空 → 啟動備援
            if fallback_on_empty:
                # 方案A：移除 <think> 後的完整文本
                cleaned = think_pattern.sub("", llm_full_response or "").strip()
                if cleaned:
                    result["formal_messages"] = split_long_message(cleaned)
                else:
                    # 方案B：真的都空，最後就把原字串硬塞（極端保險）
                    raw = (llm_full_response or "").strip()
                    if raw:
                        result["formal_messages"] = split_long_message(raw)
            # 若未開啟備援且空，就維持空（由上層判斷）
    else:
        # 沒有 <think>，直接當正式內容
        cleaned = (llm_full_response or "").strip()
        if cleaned:
            result["formal_messages"] = split_long_message(cleaned)
        elif fallback_on_empty:
            # 仍然空就原樣回傳（基本不會發生，但保險）
            result["formal_messages"] = split_long_message(llm_full_response or "")

    return result

def handle_text_message_event(context_id, user_id, reply_token, user_text):
    """
    v6 版本，修正了聊天模式下對 handle_llm_response_with_think 的處理 bug。
    """
    # ... (記錄歷史的邏輯保持不變) ...
    display_name = get_user_profile(context_id, user_id)
    if context_id.startswith(('G', 'R')): formatted_message_content = f"{display_name}: {user_text}"
    else: formatted_message_content = user_text
    history = CONVERSATION_HISTORY.get(context_id, [])
    history.append({"role": "user", "content": formatted_message_content})
    if len(history) > MAX_HISTORY_MESSAGES: history = history[-MAX_HISTORY_MESSAGES:]
    CONVERSATION_HISTORY[context_id] = history
    save_json_data(CONVERSATION_HISTORY, CONVERSATION_HISTORY_FILE)
    logging.info(f"已記錄訊息到 {context_id}。當前歷史長度: {len(history)}")

    user_text_stripped = user_text.strip()
    if not user_text_stripped.startswith(BOT_TRIGGER_WORD): return

    command_text = user_text_stripped[len(BOT_TRIGGER_WORD):].strip()
    if not command_text or command_text.lower() in ["help", "幫助", "指令"]:
        send_line_messages(context_id, reply_token, [HELP_MESSAGE.strip()]); return

    cmd_parts = command_text.lower().split()
    main_command = cmd_parts[0] if cmd_parts else ""

    # ... (新聞和訂閱的 if/elif 區塊保持不變) ...
    if main_command in ["新聞", "news", "新聞摘要"]:
        # ... (此處邏輯不變)
        logging.info("偵測到「新聞一次性查詢」指令 (同步模式)。")
        final_keywords = None; user_input_part = command_text[len(main_command):].strip()
        if user_input_part:
            if user_input_part.lower().startswith("關鍵字:"): final_keywords = user_input_part[len("關鍵字:"):].strip()
            else: final_keywords = user_input_part
        else:
            user_pref = USER_PREFERENCES.get(context_id, {});
            if user_pref.get("subscribed_news") and user_pref.get("news_keywords"): final_keywords = user_pref.get("news_keywords")
        if not final_keywords: final_keywords = None
        generate_and_push_news_for_user(user_id=context_id, user_custom_keywords=final_keywords, is_immediate_push=True, reply_token=reply_token)

    elif main_command == "訂閱":
        # ... (此處邏輯不變)
        logging.info("偵測到「訂閱」指令。")
        keywords_to_subscribe = command_text[len(main_command):].strip()
        user_pref = USER_PREFERENCES.get(context_id, {}); user_pref["subscribed_news"] = True; user_pref["news_keywords"] = keywords_to_subscribe or None
        reply_msg = f"✅ 設定成功！已為您訂閱每日新聞，主題為：「{keywords_to_subscribe or '預設 AI 主題'}」。"
        USER_PREFERENCES[context_id] = user_pref; save_json_data(USER_PREFERENCES, USER_PREFERENCES_FILE)
        send_line_messages(context_id, reply_token, [reply_msg])

    elif main_command == "查看訂閱":
        # ... (此處邏輯不變)
        user_pref = USER_PREFERENCES.get(context_id, {}); reply_msg = "您目前尚未訂閱每日新聞喔。"
        if user_pref.get("subscribed_news"): subscribed_keywords = user_pref.get("news_keywords", "預設 AI 主題"); reply_msg = f"您目前的訂閱狀態為：\n- 狀態：已訂閱 ✅\n- 主題：「{subscribed_keywords}」"
        send_line_messages(context_id, reply_token, [reply_msg])

    elif main_command == "取消訂閱":
        # ... (此處邏輯不變)
        user_pref = USER_PREFERENCES.get(context_id, {}); user_pref["subscribed_news"] = False; USER_PREFERENCES[context_id] = user_pref
        save_json_data(USER_PREFERENCES, USER_PREFERENCES_FILE); send_line_messages(context_id, reply_token, ["☑️ 好的，已為您取消每日新聞訂閱。"])
        
    # --- 3. 一般聊天 (修正對 handle_llm_response_with_think 的處理) ---
    else:
        logging.info("作為一般聊天問題處理。")
        llm_response = generate_chat_response(context_id, command_text)
        
        # *** 修改核心 ***
        # 1. 先呼叫 handle_llm_response_with_think 來解析 LLM 回應
        parsed_result = handle_llm_response_with_think(llm_response)
        
        # 2. 從返回的字典中取出思考和正式訊息
        thinking_messages = parsed_result["thinking_messages"]
        formal_messages = parsed_result["formal_messages"]
        
        # 3. 將它們合併成一個最終要發送的列表
        messages_to_send = thinking_messages + formal_messages
        
        # 4. 發送這個列表
        send_line_messages(context_id, reply_token, messages_to_send)
        
        # 5. 發送成功後，才更新歷史紀錄
        if not llm_response.startswith("抱歉，"):
            # 我們只儲存正式回覆的部分到歷史紀錄
            # 注意：這裡的 formal_messages 是一個列表，我們需要將它合併回字串
            cleaned_bot_response = "\n".join(formal_messages)
            
            history.append({"role": "assistant", "content": cleaned_bot_response})
            if len(history) > MAX_HISTORY_MESSAGES:
                history = history[-MAX_HISTORY_MESSAGES:]
            CONVERSATION_HISTORY[context_id] = history
            save_json_data(CONVERSATION_HISTORY, CONVERSATION_HISTORY_FILE)       

# ==============================================================================
# --- 排程與應用啟動 ---
# ==============================================================================
scheduler = BackgroundScheduler(timezone="Asia/Taipei", daemon=True)

def daily_news_push_job():
    with app.app_context():
        logging.info("APScheduler: 任務鏈啟動器開始執行...")
        users_to_push = [(uid, prefs.get("news_keywords")) for uid, prefs in load_json_data(USER_PREFERENCES_FILE).items() if prefs.get("subscribed_news")]
        if TARGET_USER_ID_FOR_TESTING and not any(u[0] == TARGET_USER_ID_FOR_TESTING for u in users_to_push):
            users_to_push.append((TARGET_USER_ID_FOR_TESTING, load_json_data(USER_PREFERENCES_FILE).get(TARGET_USER_ID_FOR_TESTING, {}).get("news_keywords")))
        if not users_to_push:
            logging.info("APScheduler: 啟動器發現沒有需要處理的用戶。")
            return
        logging.info(f"APScheduler: 啟動器準備啟動一個包含 {len(users_to_push)} 位用戶的任務鏈。")
        first_user_id, first_user_keywords = users_to_push[0]
        remaining_users = users_to_push[1:]
        job_id = f"scheduled_chain_{first_user_id}_{int(time.time())}"
        scheduler.add_job(generate_news_for_single_user_job, 'date', run_date=datetime.now(scheduler.timezone) + timedelta(seconds=5), args=[first_user_id, first_user_keywords, remaining_users, False], id=job_id)
        logging.info(f"APScheduler: 任務鏈的第一個任務已註冊給 {first_user_id}，啟動器任務結束。")

def shutdown_scheduler_on_exit():
    if scheduler.running: scheduler.shutdown(wait=False)

def _debug_test_call_openai_api():
    """
    最小化測試：直接用你現有的 call_openai_api 測試通路。
    會列印 base_url、model、回應長度與前 300 字；若失敗會輸出錯誤細節。
    """
    try:
        base = os.getenv("OPENAI_BASE_URL", "https://api.openai.com")
        model = os.getenv("OPENAI_COMPLETION_MODEL", "gpt-4o-mini")
        key = os.getenv("OPENAI_API_KEY")

        print("[TEST] OPENAI_BASE_URL =", base)
        print("[TEST] OPENAI_COMPLETION_MODEL =", model)
        print("[TEST] OPENAI_API_KEY set? ", "YES" if key else "NO")

        messages = [
            {"role": "system", "content": "你是簡潔助理，回覆不超過20字。"},
            {"role": "user", "content": "回覆：OK 即表示API可用。"}
        ]
        out = call_openai_api(messages, model=model, max_tokens=1164, temperature=0.0)
        if out is None:
            print("[TEST] call_openai_api 回傳 None")
        else:
            print("[TEST] 回傳長度 =", len(out))
            print("[TEST] 內容前1300字：", (out or "")[:1300])

        # 額外檢查：空字串或僅空白
        if not out or not str(out).strip():
            print("[TEST][WARN] 回應為空字串！可能是 proxy / base_url / body 格式 / 模型名錯誤。")

    except Exception as e:
        print("[TEST][ERROR] 例外：", repr(e))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="Line Bot and News Fetcher")
    parser.add_argument('--test-news', action='store_true', help='Run in local test mode for news fetching and summarization.')
    parser.add_argument('--test-openai', action='store_true', help='Quickly test call_openai_api pipeline.')
    parser.add_argument('--keywords', type=str, default=None, help='Keywords for news fetching in test mode.')
    parser.add_argument('--limit', type=int, default=None, help='Number of articles to process in test mode.')
    args = parser.parse_args()

    if args.test_openai:
        _debug_test_call_openai_api()
        sys.exit(0)

    if args.test_news:
        def run_test_mode(keywords, limit):
            print("="*50 + "\n🚀 進入本地測試模式 🚀\n" + "="*50)
            articles = fetch_and_parse_articles(custom_query=keywords, limit=limit or NEWS_FETCH_TARGET_COUNT)
            if not articles:
                print("[!] 測試中止：未能成功擷取任何新聞內文。")
                return
            final_summary = summarize_news_flow(articles)
            print("\n" + "="*50 + "\n🎧 最終 Podcast 風格摘要 🎧\n" + "="*50)
            print(final_summary)
            print("\n" + "="*50 + "\n✅ 測試流程結束 ✅\n" + "="*50)
        run_test_mode(args.keywords, args.limit)
    else:
        logging.info("🚀 啟動 Flask Web 伺服器模式 🚀")
        required_env_vars = ['LINE_CHANNEL_ACCESS_TOKEN', 'LINE_CHANNEL_SECRET', 'OPENAI_API_KEY']
        if any(not os.getenv(var) for var in required_env_vars):
            logging.critical(f"CRITICAL: Missing required environment variables: {', '.join(v for v in required_env_vars if not os.getenv(v))}. Exiting.")
            exit(1)
        if not scheduler.get_jobs():
            scheduler.add_job(daily_news_push_job, 'cron', hour=9, minute=0, id='daily_news_cron_morning', replace_existing=True)
            scheduler.add_job(daily_news_push_job, 'cron', hour=16, minute=0, id='daily_news_cron_afternoon', replace_existing=True)
#             scheduler.add_job(daily_news_push_job, 'interval', minutes=960, id='news_interval_job', replace_existing=True)
            logging.info("已設定每日 09:00 和每日 16:00 的新聞推播排程。")
            if os.getenv("RUN_JOB_ON_STARTUP", "False").lower() == "true":
                scheduler.add_job(daily_news_push_job, 'date', run_date=datetime.now(scheduler.timezone) + timedelta(seconds=15), id='startup_news_push')
                logging.info(f"已設定在 15 秒後執行一次新聞推播任務。")
        if not scheduler.running:
            scheduler.start()
            logging.info("APScheduler started.")
            atexit.register(shutdown_scheduler_on_exit)
        port = int(os.environ.get("PORT", 5000))
        app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)
