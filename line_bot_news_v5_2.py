# ==============================================================================
# line_bot_v5_2.py
# 循序執行實驗版本
#
# 版本亮點 (v5 -> v5_2):
# - 新聞抓取流程重構：由平行處理改為循序執行。
# - 實施「工作範疇」的 Selenium 實例管理模式。
# - 在單次新聞抓取任務中，只啟動一次瀏覽器，並重複使用該實例處理所有文章。
# - 此修改旨在透過避免反覆啟動瀏覽器的開銷來提升效能，並降低系統資源的瞬間負載。
# ==============================================================================

# --- Python Standard Libraries ---
import os
import platform
import sys
import time
import uuid
import json
import logging
import hashlib
import hmac
import base64
import re
import atexit
import argparse
from datetime import datetime, timedelta, timezone
import urllib.parse
from urllib.parse import urlparse, parse_qs, unquote


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
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import WebDriverException, TimeoutException

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
NEWS_FETCH_TARGET_COUNT = 6
USER_AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36'

NEWS_CACHE_FILE = "news_cache.json"
NEWS_SUMMARY_CACHE_SECONDS = 3600 * 4  # 4 小時

# --- 用戶個人資料快取 (in-memory) ---
USER_PROFILE_CACHE = {}
USER_PROFILE_CACHE_SECONDS = 7200  # 快取 2 小時

MAX_MESSAGE_LENGTH = int(os.getenv("MAX_MESSAGE_LENGTH", "4800"))

# --- 兩階段摘要的 LLM Prompt 設定 ---
PROMPT_FOR_INDIVIDUAL_SUMMARY = (
    "你是一位資深的新聞編輯，專長是快速提煉文章核心。請將以下提供的新聞內文，濃縮成一段不超過150字的客觀、精簡中文摘要。"
    "摘要應包含最關鍵的人物、事件、數據和結論。請直接輸出摘要內容，不要有任何開頭或結尾的客套話。"
)

current_date = datetime.now().strftime("%Y-%m-%d %H:%M")
PROMPT_FOR_FINAL_AGGREGATION = (
    f"今天日期是 {current_date}。\n"
    "你是一位風趣幽默、知識淵博的新聞 Podcast 主持人。你的聽眾是 Line 用戶，他們喜歡輕鬆、易懂且帶有 Emoji 的內容。"
    "接下來我會提供數則「附有發布日期的精簡新聞摘要」。請你根據這些摘要，發揮你的主持風格，將它們整合成一篇連貫的談話性內容。"
    "你的任務是：\n"
    "1. 用生動的語氣開場，吸引聽眾注意。\n"
    "2. 將各則新聞摘要自然地串連起來，你可以根據新聞的發布日期（例如使用『昨天』、『今天早上』等詞彙）來增加時效感，但不要杜撰不存在的事實。\n"
    "3. 在提到每則新聞的重點後，請務必附上這則新聞的原始標題，格式如下：\n"
    "   - 標題：[原始新聞標題] - 發布時間：[新聞發布時間]\n"
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
# --- 新聞擷取模組 (v5_2 Refactored) ---
# ==============================================================================
newspaper_config = Config()
newspaper_config.browser_user_agent = USER_AGENT
newspaper_config.request_timeout = 15
newspaper_config.memoize_articles = False

# --- Selenium Options (集中管理) ---
chrome_options = Options()
# 在 Docker 或無 GUI 環境中，務必啟用 headless
if os.getenv("SELENIUM_HEADLESS", "true").lower() == "true":
    chrome_options.add_argument("--headless=new")
chrome_options.add_argument("--no-sandbox")
chrome_options.add_argument("--disable-dev-shm-usage")
chrome_options.add_argument("--disable-gpu")
chrome_options.add_argument("--disable-extensions")
chrome_options.add_argument("--window-size=1280,2400")
chrome_options.page_load_strategy = "eager" # 加快頁面返回速度
chrome_options.add_experimental_option("prefs", {
    "profile.managed_default_content_settings.images": 2, # 不載入圖片
})

# --- Selenium Helper Functions ---

def _dom_is_stable(driver, min_text_len=500, settle_checks=3, interval=0.6, overall_timeout=20):
    """
    回傳 True 當 DOM 文字長度穩定（連續 settle_checks 次幾乎不再成長），
    並且長度超過 min_text_len。避免在 SPA/React 還在掛載時就讀空白。
    """
    t0 = time.time()
    last_len = -1
    stable = 0
    while time.time() - t0 < overall_timeout:
        try:
            txt_len = driver.execute_script("return (document.body && document.body.innerText) ? document.body.innerText.length : 0;")
        except WebDriverException:
            txt_len = 0
        if last_len >= 0 and abs(txt_len - last_len) < 30 and txt_len >= min_text_len:
            stable += 1
            if stable >= settle_checks:
                return True
        else:
            stable = 0
        last_len = txt_len
        time.sleep(interval)
    return False

def _get_outer_html(driver):
    try:
        return driver.execute_script("return document.documentElement ? document.documentElement.outerHTML : ''") or ""
    except WebDriverException:
        return ""

def _try_all_iframes_html(driver, max_frames=10):
    """
    有些新聞站把正文放在 iframe。這裡會把所有 iframe outerHTML 拼起來。
    若跨網域不能進入某些 iframe，會自動跳過。
    """
    htmls = []
    try:
        frames = driver.find_elements(By.CSS_SELECTOR, "iframe")
    except WebDriverException:
        frames = []
    if not frames:
        return ""
    for idx, frame in enumerate(frames[:max_frames]):
        try:
            driver.switch_to.frame(frame)
            time.sleep(0.2)
            try:
                WebDriverWait(driver, 5).until(EC.presence_of_element_located((By.TAG_NAME, "body")))
            except Exception:
                pass
            _dom_is_stable(driver, min_text_len=200, settle_checks=2, interval=0.4, overall_timeout=6)
            htmls.append(_get_outer_html(driver))
        except Exception:
            pass
        finally:
            driver.switch_to.default_content()
    return "\n<!-- IFRAME_JOIN_BOUNDARY -->\n".join([h for h in htmls if h])

def _get_page_html_with_driver(driver: webdriver.Chrome, url: str, min_text_len: int = 700) -> str:
    """
    使用一個【已存在】的 driver 實例來抓取指定 URL 的 HTML 內容。
    這是 v5_2 重構後的核心 Selenium 互動函式。
    """
    logging.info(f"    [Selenium] Using existing driver to fetch: {url[:70]}...")
    driver.get(url)

    try:
        WebDriverWait(driver, 20).until(EC.presence_of_element_located((By.TAG_NAME, "body")))
    except TimeoutException:
        logging.warning("    [Selenium] Timed out waiting for <body> tag.")
        # 即使超時，我們還是嘗試抓取內容

    try:
        driver.execute_script("window.scrollTo(0, 600);")
        time.sleep(0.2)
        driver.execute_script("window.scrollTo(0, 0);")
    except Exception:
        pass

    _ = _dom_is_stable(driver, min_text_len=min_text_len, settle_checks=3, interval=0.6, overall_timeout=25)

    html = _get_outer_html(driver)
    text_len = 0
    try:
        text_len = driver.execute_script("return (document.body && document.body.innerText) ? document.body.innerText.length : 0;")
    except Exception:
        pass

    if text_len < min_text_len:
        logging.info(f"    [Selenium] Page text length ({text_len}) is short, trying to extract from iframes.")
        iframe_html = _try_all_iframes_html(driver, max_frames=12)
        if iframe_html:
            html = html + "\n<!-- CONCAT IFRAME HTML BELOW -->\n" + iframe_html

    logging.info(f"    [Selenium] Fetched from {driver.current_url}. Main text length: {text_len}.")
    return html or ""


def get_real_url(google_news_url):
    try:
        headers = {"User-Agent": USER_AGENT}
        with requests.get(google_news_url, headers=headers, allow_redirects=True, timeout=20, stream=True) as r:
            return r.url
    except requests.RequestException as e:
        logging.warning(f"[錯誤] 解析跳轉連結失敗 {google_news_url}: {e}")
        # 如果請求失敗，嘗試從 URL 參數解析
        try:
            pu = urlparse(google_news_url)
            if pu.netloc.endswith("news.google.com"):
                qs = parse_qs(pu.query)
                if "url" in qs and qs["url"]:
                    return unquote(qs["url"][0])
        except Exception:
            pass
        return google_news_url # 返回原始 URL 作為備援

def fetch_and_parse_articles(custom_query=None, limit=NEWS_FETCH_TARGET_COUNT):
    """
    *** v5_2 版本：採用循序執行，重複使用單一 Selenium 實例來加速 ***
    """
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
    entries_to_process = feed.entries[:limit * 2]

    # --- 循序處理核心 ---
    # 在所有任務開始前，只啟動一次瀏覽器。
    # 使用 "with" 陳述式確保瀏覽器在區塊結束後一定會被關閉。
    logging.info("初始化單一瀏覽器實例以供本次任務重複使用...")
    try:
        with webdriver.Chrome(options=chrome_options) as driver:
            driver.set_page_load_timeout(60)
            driver.set_script_timeout(60)
            logging.info("瀏覽器實例已啟動，開始循序處理新聞條目。")
            
            for i, entry in enumerate(entries_to_process):
                if len(successful_articles) >= limit:
                    logging.info("已達到目標新聞數量，提前結束抓取。")
                    break

                logging.info(f"  [循序處理 {i+1}/{len(entries_to_process)}] 開始處理: {entry.title}")
                real_url = get_real_url(entry.link)
                if not real_url or real_url in processed_urls:
                    logging.warning(f"  跳過: 無法取得真實 URL 或 URL 重複 for {entry.title}")
                    continue
                
                try:
                    article = Article(real_url, language='zh', config=newspaper_config)
                    article.download()
                    article.parse()
                    
                    if len(article.text) < 200:
                        logging.warning(f"  內容過短，為 '{entry.title}' 啟用 Selenium 備援抓取。")
                        html_content = _get_page_html_with_driver(driver, real_url)
                        if html_content:
                            article.download(input_html=html_content)
                            article.parse()

                    if article.title and len(article.text) > 50:
                        publish_date = None
                        # 優先使用 newspaper3k 從網頁解析的日期，通常更準確
                        if hasattr(article, 'publish_date') and article.publish_date:
                            publish_date = article.publish_date.astimezone() # 轉換為帶有本地時區的 datetime 物件
                        # 如果網頁上沒有日期，使用 RSS feed 的 pubDate 作為備援
                        elif hasattr(entry, 'published_parsed') and entry.published_parsed:
                            # entry.published_parsed 是 time.struct_time，需要轉換
                            # 注意：原始時間是 GMT，我們需要處理時區
                            dt_gmt = datetime.fromtimestamp(time.mktime(entry.published_parsed), tz=timezone.utc)
                            publish_date = dt_gmt.astimezone() # 轉換為本地時區
                            
                        logging.info(f"  成功取得: {article.title} (發布於: {publish_date.strftime('%Y-%m-%d %H:%M') if publish_date else '未知'})")
                        successful_articles.append({
                            'title': article.title,
                            'text': article.text,
                            'url': real_url,
                            'source': entry.source.title if hasattr(entry, 'source') and hasattr(entry.source, 'title') else "未知來源",
                            'publish_date': publish_date  # 將日期物件儲存起來
                        })
                        processed_urls.add(real_url)
                    else:
                        logging.warning(f"  失敗: 無法為 {entry.title} 解析足夠內文。")
                except Exception as e:
                    logging.error(f"  處理 {entry.title} 時發生未預期錯誤: {e}", exc_info=False) # exc_info=False 避免過多日誌

    except WebDriverException as e:
        logging.critical(f"WebDriver 實例啟動或執行時發生嚴重錯誤，本次抓取中止: {e}", exc_info=True)
        # 如果 driver 啟動失敗，回傳空列表
        return []

    logging.info(f">>> 循序新聞內文擷取完成，共成功取得 {len(successful_articles)} 篇。")
    # --- 新增的過濾與排序邏輯 ---
    if successful_articles:
        now = datetime.now().astimezone()
        # 可以將天數設定為環境變數，例如 3 天
        days_limit = int(os.getenv("NEWS_FETCH_DAYS_LIMIT", "3"))
        time_threshold = now - timedelta(days=days_limit)
        
        original_count = len(successful_articles)
        
        # 1. 過濾掉沒有日期或太舊的文章
        successful_articles = [
            art for art in successful_articles 
            if art.get('publish_date') and art['publish_date'] > time_threshold
        ]
        
        # 2. 根據發布日期由新到舊排序
        successful_articles.sort(key=lambda x: x.get('publish_date'), reverse=True)
        
        filtered_count = len(successful_articles)
        logging.info(f"日期過濾完成: 從 {original_count} 篇篩選出 {filtered_count} 篇近 {days_limit} 天內的新聞。")
        
    return successful_articles[:limit]

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
        resp_json = response.json()
#         logging.info(str(resp_json)) 

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
        individual_summaries.append({'title': article['title'], 'url': article['url'], 'summary': cleaned_summary,
            'publish_date': article.get('publish_date')})
        logging.info(f"  摘要完成，長度: {len(cleaned_summary)} 字")
        logging.info(f"  等待30秒 避免LLM速率限制")
        time.sleep(30) # 降低LLM速率
    if not individual_summaries: return "抱歉，今日新聞摘要生成過程發生問題，無法產出內容。"
    logging.info("--- 開始第二階段摘要：彙整生成 Podcast 內容 ---")
#     summaries_for_prompt = [f"新聞 {i+1}:\n標題: {item['title']}\n摘要內容: {item['summary']}\n---" for i, item in enumerate(individual_summaries)]
    summaries_for_prompt = []
    for i, item in enumerate(individual_summaries):
        # 格式化日期，如果不存在則給予提示
        date_str = item['publish_date'].strftime("%Y-%m-%d") if item.get('publish_date') else "日期未知"
        prompt_line = (
            f"新聞 {i+1} (發布於: {date_str}):\n"
            f"標題: {item['title']}\n"
            f"摘要內容: {item['summary']}\n---"
        )
        summaries_for_prompt.append(prompt_line)
    
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
NEWS_CACHE = load_json_data(NEWS_CACHE_FILE) 

def validate_signature(request_body_bytes, signature_header):
    if not LINE_CHANNEL_SECRET: return True
    hash_obj = hmac.new(LINE_CHANNEL_SECRET.encode('utf-8'), request_body_bytes, hashlib.sha256)
    generated_signature = base64.b64encode(hash_obj.digest()).decode('utf-8')
    return hmac.compare_digest(generated_signature, signature_header)

def _utf16_len(s: str) -> int:
    return len(s.encode('utf-16-le')) // 2

def _slice_by_utf16(s: str, max_units: int):
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
    limit = limit or 5000

    if _utf16_len(text) <= limit:
        return [text.strip()]
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
MIN_PUSH_INTERVAL_SEC = float(os.getenv("LINE_MIN_PUSH_INTERVAL_SEC", "1.2"))

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

    is_first_replied = False
    if reply_token_or_none:
        try:
            payload = {"replyToken": reply_token_or_none, "messages": [{"type": "text", "text": str(text_messages_list[0])}]}
            r = requests.post("https://api.line.me/v2/bot/message/reply", headers=headers, json=payload, timeout=20)
            r.raise_for_status()
            is_first_replied = True
        except requests.exceptions.RequestException as e:
            logging.error(f"Reply 失敗，改用 Push：{e}")

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
    log_prefix = "即時請求" if is_immediate_push else "排程推播"
    logging.info(f"[{log_prefix}] 開始為用戶 {user_id} 處理新聞請求...")
    
    theme_name = user_custom_keywords if user_custom_keywords else "預設 AI 主題"
    cache_key = user_custom_keywords if user_custom_keywords else "__DEFAULT__"
    current_time = time.time()

    if cache_key in NEWS_CACHE:
        cached_item = NEWS_CACHE[cache_key]
        cache_age = current_time - cached_item.get("timestamp", 0)
        
        if cache_age < NEWS_SUMMARY_CACHE_SECONDS:
            logging.info(f"新聞快取命中！(關鍵字: '{cache_key}', 年齡: {int(cache_age)}秒)")
            cached_reply_content = cached_item.get("reply_content")
            if cached_reply_content:
                final_reply = f"這份新聞摘要根據「{theme_name}」主題產生（從快取提供😊）\n\n{cached_reply_content}"
                send_line_messages(user_id, reply_token, split_long_message(final_reply))
                return

    logging.info(f"新聞快取未命中或已過期 (關鍵字: '{cache_key}')，執行完整新聞摘要流程。")
    articles = fetch_and_parse_articles(custom_query=user_custom_keywords, limit=NEWS_FETCH_TARGET_COUNT)
    if not articles:
        send_line_messages(user_id, reply_token, [f"抱歉，目前未能根據您的關鍵字「{theme_name}」找到可成功擷取的新聞。"])
        return

    final_summary_raw = summarize_news_flow(articles)
    if not final_summary_raw or final_summary_raw.startswith("抱歉，"):
        send_line_messages(user_id, reply_token, [final_summary_raw or "抱歉，今日新聞摘要生成異常，內容為空。"])
        return

    parsed_result = handle_llm_response_with_think(final_summary_raw)
    thinking_messages = parsed_result["thinking_messages"]
    formal_messages = parsed_result["formal_messages"]

    final_formal_reply_for_cache = ""
    if formal_messages:
        generation_time = datetime.fromtimestamp(current_time)
        time_str = generation_time.strftime("%Y-%m-%d %H:%M")
        full_formal_text = "\n".join(formal_messages)
        final_formal_reply_for_cache = f"產生於 {time_str}\n\n{full_formal_text}"
    
    if final_formal_reply_for_cache:
        NEWS_CACHE[cache_key] = {
            "timestamp": current_time,
            "reply_content": final_formal_reply_for_cache
        }
        save_json_data(NEWS_CACHE, NEWS_CACHE_FILE)
        logging.info(f"已更新新聞快取 (關鍵字: '{cache_key}')。")

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
    think_pattern = re.compile(r"<think>(.*?)</think>", re.DOTALL | re.IGNORECASE)
    result = {"thinking_messages": [], "formal_messages": []}
    show_thinking = os.getenv("SHOW_THINKING_PROCESS", "false").lower() == "true"
    fallback_on_empty = os.getenv("FALLBACK_ON_EMPTY", "true").lower() == "true"
    match = think_pattern.search(llm_full_response or "")
    if match:
        thinking_text = (match.group(1) or "").strip()
        formal_text = (llm_full_response[match.end():] or "").strip()
        if thinking_text and show_thinking:
            result["thinking_messages"] = split_long_message(f"⚙️ 我的思考過程：\n{thinking_text}")
        if formal_text:
            result["formal_messages"] = split_long_message(formal_text)
        else:
            if fallback_on_empty:
                cleaned = think_pattern.sub("", llm_full_response or "").strip()
                if cleaned:
                    result["formal_messages"] = split_long_message(cleaned)
                else:
                    raw = (llm_full_response or "").strip()
                    if raw:
                        result["formal_messages"] = split_long_message(raw)
    else:
        cleaned = (llm_full_response or "").strip()
        if cleaned:
            result["formal_messages"] = split_long_message(cleaned)
        elif fallback_on_empty:
            result["formal_messages"] = split_long_message(llm_full_response or "")
    return result

def handle_text_message_event(context_id, user_id, reply_token, user_text):
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

    if main_command in ["新聞", "news", "新聞摘要"]:
        logging.info("偵測到「新聞一次性查詢」指令。")
        final_keywords = None; user_input_part = command_text[len(main_command):].strip()
        if user_input_part:
            if user_input_part.lower().startswith("關鍵字:"): final_keywords = user_input_part[len("關鍵字:"):].strip()
            else: final_keywords = user_input_part
        else:
            user_pref = USER_PREFERENCES.get(context_id, {});
            if user_pref.get("subscribed_news") and user_pref.get("news_keywords"): final_keywords = user_pref.get("news_keywords")
        if not final_keywords: final_keywords = None
        # 使用背景執行緒處理，避免 webhook 超時
        thread = ThreadPoolExecutor(max_workers=1)
        thread.submit(generate_and_push_news_for_user, user_id=context_id, user_custom_keywords=final_keywords, is_immediate_push=True, reply_token=reply_token)
        # 立刻回覆一個處理中訊息
        send_line_messages(context_id, reply_token, ["收到！正在為您客製化新聞摘要，請稍候... 🚀"])


    elif main_command == "訂閱":
        logging.info("偵測到「訂閱」指令。")
        keywords_to_subscribe = command_text[len(main_command):].strip()
        user_pref = USER_PREFERENCES.get(context_id, {}); user_pref["subscribed_news"] = True; user_pref["news_keywords"] = keywords_to_subscribe or None
        reply_msg = f"✅ 設定成功！已為您訂閱每日新聞，主題為：「{keywords_to_subscribe or '預設 AI 主題'}」。"
        USER_PREFERENCES[context_id] = user_pref; save_json_data(USER_PREFERENCES, USER_PREFERENCES_FILE)
        send_line_messages(context_id, reply_token, [reply_msg])

    elif main_command == "查看訂閱":
        user_pref = USER_PREFERENCES.get(context_id, {}); reply_msg = "您目前尚未訂閱每日新聞喔。"
        if user_pref.get("subscribed_news"): subscribed_keywords = user_pref.get("news_keywords", "預設 AI 主題"); reply_msg = f"您目前的訂閱狀態為：\n- 狀態：已訂閱 ✅\n- 主題：「{subscribed_keywords}」"
        send_line_messages(context_id, reply_token, [reply_msg])

    elif main_command == "取消訂閱":
        user_pref = USER_PREFERENCES.get(context_id, {}); user_pref["subscribed_news"] = False; USER_PREFERENCES[context_id] = user_pref
        save_json_data(USER_PREFERENCES, USER_PREFERENCES_FILE); send_line_messages(context_id, reply_token, ["☑️ 好的，已為您取消每日新聞訂閱。"])
        
    else:
        logging.info("作為一般聊天問題處理。")
        llm_response = generate_chat_response(context_id, command_text)
        
        parsed_result = handle_llm_response_with_think(llm_response)
        thinking_messages = parsed_result["thinking_messages"]
        formal_messages = parsed_result["formal_messages"]
        messages_to_send = thinking_messages + formal_messages
        send_line_messages(context_id, reply_token, messages_to_send)
        
        if not llm_response.startswith("抱歉，"):
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
