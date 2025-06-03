# line_bot_final_v5.py (建議檔名)

from flask import Flask, request, jsonify
import requests
import os
from dotenv import load_dotenv
import hashlib
import hmac
import base64
import logging
import feedparser
from apscheduler.schedulers.background import BackgroundScheduler
import atexit
import re 
import time 
from datetime import datetime, timedelta
import json # 新增：用於處理 JSON 檔案
import urllib.parse # 新增：用於 URL 編碼用戶關鍵字

# --- 環境與日誌設定 ---
load_dotenv()
app = Flask(__name__)
app.logger.setLevel(logging.INFO)
if not app.logger.handlers or not any(isinstance(h, logging.StreamHandler) for h in app.logger.handlers):
    if not app.debug: 
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s: %(message)s [in %(pathname)s:%(lineno)d]'))
        app.logger.addHandler(handler)

# --- 全域變數與常數 ---
BOT_NAMES = os.getenv("BOT_NAMES", "").split(",")
BOT_DEACTIVATED = os.getenv("BOT_DEACTIVATED", "False").lower() == "true"
OPENAI_COMPLETION_MODEL = os.getenv("OPENAI_COMPLETION_MODEL", "gpt-3.5-turbo")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
LINE_CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET")
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "https://api.openai.com")
TARGET_USER_ID_FOR_TESTING = os.getenv("TARGET_USER_ID_FOR_TESTING")
VISUAL_SEPARATION_DELAY = float(os.getenv("VISUAL_SEPARATION_DELAY", "1.0"))
DEFAULT_NEWS_KEYWORDS = "大型語言模型 OR LLM OR 生成式AI OR OpenAI OR Gemini OR Claude" # 預設新聞關鍵字

USER_PREFERENCES_FILE = "user_preferences.json" # 替換 subscribers.txt
MAX_MESSAGE_LENGTH = 4800

# --- 用戶偏好設定 (JSON 儲存) ---
def load_user_preferences():
    try:
        with open(USER_PREFERENCES_FILE, "r", encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        return {} # 文件不存在，返回空字典
    except json.JSONDecodeError:
        app.logger.error(f"Error decoding JSON from {USER_PREFERENCES_FILE}. Returning empty dict.")
        return {} # JSON 格式錯誤

def save_user_preferences(preferences_data):
    with app.app_context():
        try:
            with open(USER_PREFERENCES_FILE, "w", encoding='utf-8') as f:
                json.dump(preferences_data, f, ensure_ascii=False, indent=4) # indent for readability
            app.logger.info(f"User preferences saved. Total users with prefs: {len(preferences_data)}")
        except Exception as e:
            app.logger.error(f"Error saving user preferences: {e}")

# 程式啟動時載入一次，後續操作直接修改此字典並保存
USER_PREFERENCES = load_user_preferences()
app.logger.info(f"Loaded {len(USER_PREFERENCES)} user preferences records.")


# --- Line 簽名驗證 (與之前相同) ---
def validate_signature(request_body_bytes, signature_header):
    if not LINE_CHANNEL_SECRET:
        app.logger.warning("LINE_CHANNEL_SECRET is not set. Skipping signature validation for dev.")
        return True 
    hash_obj = hmac.new(LINE_CHANNEL_SECRET.encode('utf-8'), request_body_bytes, hashlib.sha256)
    generated_signature = base64.b64encode(hash_obj.digest()).decode('utf-8')
    return hmac.compare_digest(generated_signature, signature_header)

# --- OpenAI API 互動 (與之前相同) ---
def generate_chat_response(prompt_text):
    # ... (程式碼不變) ...
    if not OPENAI_API_KEY:
        app.logger.error("OPENAI_API_KEY is not set.")
        return "抱歉，我目前無法處理您的請求 (API Key 未設定)。"
    headers = {"Authorization": f"Bearer {OPENAI_API_KEY}", "Content-Type": "application/json"}
    system_prompt_content = (
        "你是一個通訊軟體的聊天機器人。在回答時，如果需要思考步驟，請將思考過程用 <think> 和 </think> 標籤包起來。"
        "例如：<think>第一步：分析問題。\n第二步：查找資料。</think>這是正式的回答。"
        "回答要精簡、口語化，使用台灣常用的繁體中文。"
    )
    data = { "model": OPENAI_COMPLETION_MODEL, "messages": [{"role": "system", "content": system_prompt_content},{"role": "user", "content": prompt_text}], "max_tokens": 2000, "temperature": 0.7 }
    try:
        response = requests.post(f"{OPENAI_BASE_URL}/v1/chat/completions", headers=headers, json=data, timeout=900) 
        response.raise_for_status()
        content = response.json()["choices"][0]["message"]["content"].strip()
        app.logger.info(f"OpenAI response received (first 100 chars): {content[:100]}")
        return content
    except requests.exceptions.Timeout:
        app.logger.error("OpenAI API request timed out.")
        return "抱歉，請求 OpenAI 服務超時了，請稍後再試。"
    except requests.exceptions.RequestException as e:
        app.logger.error(f"OpenAI API request error: {e}")
        return "抱歉，連接 OpenAI 服務時發生錯誤。"
    except (KeyError, IndexError, TypeError) as e: 
        app.logger.error(f"OpenAI API response format error: {e} - Response: {response.text if 'response' in locals() else 'N/A'}")
        return "抱歉，OpenAI 回應的格式有問題。"
    except Exception as e:
        app.logger.error(f"Unexpected error in generate_chat_response: {e}")
        return "抱歉，我無法生成回覆。"


# --- 訊息分割與發送 (與之前相同) ---
def split_long_message(text, limit=MAX_MESSAGE_LENGTH):
    # ... (程式碼不變) ...
    messages = []
    if not text or not text.strip(): return []
    current_chunk = ""
    lines = text.splitlines(keepends=True) 
    for line in lines:
        if len(current_chunk) + len(line) <= limit: current_chunk += line
        else:
            if not current_chunk and len(line) > limit:
                start = 0
                while start < len(line):
                    messages.append(line[start:start+limit])
                    start += limit
                current_chunk = "" 
            elif current_chunk: 
                messages.append(current_chunk)
                current_chunk = line 
                if len(current_chunk) > limit: 
                    start = 0
                    temp_line_chunk = current_chunk 
                    current_chunk = "" 
                    while start < len(temp_line_chunk):
                        messages.append(temp_line_chunk[start:start+limit])
                        start += limit
            else: current_chunk = line
    if current_chunk.strip(): messages.append(current_chunk)
    if not messages and text.strip(): messages.append(text)
    if len(messages) > 1:
        final_messages_with_pages = []
        for i, msg_chunk in enumerate(messages):
            page_indicator = f"({i+1}/{len(messages)})\n"
            msg_chunk_str = str(msg_chunk).strip()
            if not msg_chunk_str: continue 
            if len(page_indicator) + len(msg_chunk_str) <= MAX_MESSAGE_LENGTH: final_messages_with_pages.append(page_indicator + msg_chunk_str)
            else: 
                if len(msg_chunk_str) <= MAX_MESSAGE_LENGTH: final_messages_with_pages.append(msg_chunk_str)
                else: final_messages_with_pages.append(msg_chunk_str[:MAX_MESSAGE_LENGTH - 20] + "...(內容過長)")
        return final_messages_with_pages
    return [m.strip() for m in messages if m.strip()]


def send_line_messages(user_id, reply_token_or_none, text_messages_list):
    # ... (程式碼不變) ...
    if not text_messages_list: app.logger.warning(f"No messages to send to user {user_id if user_id else 'unknown'}.") ; return
    first_message_sent_with_reply = False
    for i, msg_content_raw in enumerate(text_messages_list):
        msg_content = str(msg_content_raw).strip()
        if not msg_content: app.logger.debug(f"Skipping empty message chunk for user {user_id if user_id else 'unknown'}.") ; continue
        payload = {"messages": [{"type": "text", "text": msg_content}]}
        headers = {"Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}","Content-Type": "application/json"}
        api_type = "" ; target_log_id = ""
        if reply_token_or_none and not first_message_sent_with_reply and i == 0:
            url = "https://api.line.me/v2/bot/message/reply" ; payload["replyToken"] = reply_token_or_none
            api_type = "Reply" ; target_log_id = reply_token_or_none[:10] + "..." ; first_message_sent_with_reply = True
        else:
            if not user_id: app.logger.error("Cannot push message: user_id is missing for a push operation.") ; continue 
            url = "https://api.line.me/v2/bot/message/push" ; payload["to"] = user_id
            api_type = "Push" ; target_log_id = user_id
            if i > 0 and first_message_sent_with_reply : time.sleep(0.1) 
            elif i > 0 : time.sleep(0.3)
        try:
            response = requests.post(url, headers=headers, json=payload, timeout=20) 
            response.raise_for_status()
            app.logger.info(f"{api_type} message sent to {target_log_id} (part {i+1}/{len(text_messages_list)}) Content: {msg_content[:30]}...")
        except requests.exceptions.RequestException as e:
            response_text_log = response.text if 'response' in locals() and hasattr(response, 'text') else 'N/A'
            app.logger.error(f"Failed to send {api_type} message to {target_log_id}: {e} - Response: {response_text_log}")
        except Exception as ex:
            app.logger.error(f"Unexpected error sending {api_type} message to {target_log_id}: {ex}")


# --- 新聞聚合與摘要 ---
def fetch_llm_news_from_google_rss(custom_query=None): # *** 修改：接受 custom_query ***
    query_to_use = DEFAULT_NEWS_KEYWORDS # 預設關鍵字
    if custom_query and custom_query.strip():
        query_to_use = custom_query.strip()
        app.logger.info(f"Using custom keywords for news fetch: '{query_to_use}'")
    else:
        app.logger.info(f"Using default keywords for news fetch: '{query_to_use}'")

    # URL 編碼關鍵字
    encoded_query = urllib.parse.quote_plus(query_to_use)
    rss_url = f"https://news.google.com/rss/search?q={encoded_query}&hl=zh-TW&gl=TW&ceid=TW:zh-Hant"
    
    app.logger.info(f"Fetching news from Google RSS: {rss_url}")
    articles_data = []
    try:
        feed = feedparser.parse(rss_url)
        if feed.bozo:
            app.logger.warning(f"Malformed feed from {rss_url}. Reason: {feed.bozo_exception}")
        
        for entry in feed.entries[:7]: 
             articles_data.append({
                "title": entry.title,
                "description": entry.summary if hasattr(entry, 'summary') else "N/A",
                "url": entry.link,
                "source": entry.source.title if hasattr(entry, 'source') and hasattr(entry.source, 'title') else "Google News"
            })
        app.logger.info(f"Fetched {len(articles_data)} articles using query '{query_to_use}'.")
    except Exception as e:
        app.logger.error(f"Error fetching or parsing Google RSS feed with query '{query_to_use}': {e}")
    return articles_data

def summarize_news_with_llm(articles_data):
    # ... (與之前版本相同，System Prompt 應引導輸出連結，可選引導 <think>) ...
    if not articles_data: return "根據您指定的關鍵字，今天沒有抓取到相關新聞。" # 修改提示
    if not OPENAI_API_KEY:
        app.logger.error("OPENAI_API_KEY is not set for news summarization.")
        return "抱歉，新聞摘要服務暫時無法使用 (API Key 未設定)。"

    news_texts_for_prompt = []
    for i, article in enumerate(articles_data):
        news_texts_for_prompt.append(f"新聞{i+1}:\n標題: {article['title']}\n簡述: {article.get('description', '無簡述')}\n來源: {article.get('source', '未知來源')}\n連結: {article['url']}\n---")
    prompt_content = "\n\n".join(news_texts_for_prompt)
    
    system_prompt_news = ( # 你可以按需修改此 prompt
        "你是一位專業的新聞編輯。在進行摘要的思考時，如果需要，請將你的思考步驟用 <think> 和 </think> 標籤包起來。然後再給出正式的新聞摘要。"
        "正式的新聞摘要應為Line用戶彙整一份簡明扼要的新聞摘要。"
        "請挑選出3-5條最重要或最有趣的資訊，以條列式呈現，每條摘要後都應有一個清晰的分隔（例如使用 '---'）。"
        "對於每一條摘要，請在摘要內容之後，另起數行分別清楚列出該新聞的：\n"
        "1. 原始標題 (格式：標題：[新聞標題])\n"
        "2. 新聞來源 (格式：來源：[新聞來源])\n"
        "3. 原始新聞的 URL (格式：連結：[新聞URL])\n"
        "如果摘要內容本身已非常清楚地提及了完整標題，則「標題：」行可省略，但「來源：」和「連結：」行必須提供。"
        "最後可以加上一句總結或溫馨提醒。風格要親切且易於閱讀，使用台灣常用的繁體中文。"
    )
    user_prompt = f"請彙整以下新聞（每條新聞末尾已包含其 URL）：\n{prompt_content}"
    headers = {"Authorization": f"Bearer {OPENAI_API_KEY}", "Content-Type": "application/json"}
    data = { "model": OPENAI_COMPLETION_MODEL, "messages": [{"role": "system", "content": system_prompt_news}, {"role": "user", "content": user_prompt}], "max_tokens": 1800, "temperature": 0.5 }
    try:
        response = requests.post(f"{OPENAI_BASE_URL}/v1/chat/completions", headers=headers, json=data, timeout=1200)
        response.raise_for_status()
        summary = response.json()["choices"][0]["message"]["content"].strip()
        app.logger.info(f"News summary generated. Length: {len(summary)}")
        if "http" not in summary and "https" not in summary: app.logger.warning("LLM news summary might be missing URLs.")
        return summary
    except requests.exceptions.Timeout:
        app.logger.error("OpenAI API request (news summarization) timed out.")
        return "抱歉，新聞摘要生成超時，請稍後再試。"
    except requests.exceptions.RequestException as e:
        app.logger.error(f"OpenAI API error during news summarization: {e}")
        return "抱歉，新聞摘要生成失敗 (網路問題)。"
    except (KeyError, IndexError, TypeError) as e:
        response_text_log = response.text if 'response' in locals() and hasattr(response, 'text') else 'N/A'
        app.logger.error(f"OpenAI API response format error (news summarization): {e} - Response: {response_text_log}")
        return "抱歉，新聞摘要生成失敗 (回應格式問題)。"
    except Exception as e:
        app.logger.error(f"Unexpected error in summarize_news_with_llm: {e}")
        return "抱歉，新聞摘要生成遇到未知錯誤。"

# --- Webhook 事件處理 ---
@app.route('/webhook', methods=['POST'])
def webhook():
    # ... (與之前版本相同，除了 Follow Event 中的 SUBSCRIBED_USERS 邏輯) ...
    signature = request.headers.get("X-Line-Signature") ; body_bytes = request.get_data()
    if not signature: app.logger.warning("Webhook: Missing X-Line-Signature header.") ; return jsonify({"status": "error", "message": "Missing signature"}), 400
    if not validate_signature(body_bytes, signature): app.logger.error("Webhook: Invalid signature.") ; return jsonify({"status": "invalid signature"}), 400
    try:
        data = request.json ; app.logger.debug(f"Webhook received data: {data}")
        for event in data.get("events", []):
            event_type = event.get("type") ; source = event.get("source", {})
            user_id = source.get("userId") ; reply_token = event.get("replyToken") 
            app.logger.info(f"Processing event: type={event_type}, user_id={user_id}")
            if event_type == "message":
                message_data = event.get("message", {}) ; message_type = message_data.get("type")
                if message_type == "text": handle_text_message_event(user_id, reply_token, message_data.get("text", ""))
            elif event_type == "follow": # *** 修改 Follow 事件處理 ***
                if user_id and reply_token:
                    app.logger.info(f"User {user_id} followed.")
                    user_pref = USER_PREFERENCES.get(user_id, {}) # 獲取現有偏好或空字典
                    user_pref["subscribed_news"] = True # 預設加好友即訂閱
                    # 不在此處設定預設關鍵字，讓用戶通過指令設定或使用全局預設
                    if "news_keywords" not in user_pref: # 如果之前沒有設定過關鍵字
                         user_pref["news_keywords"] = None # 標記為使用預設
                    USER_PREFERENCES[user_id] = user_pref
                    save_user_preferences(USER_PREFERENCES)
                    send_line_messages(user_id, reply_token, ["感謝您加我好友！我將嘗試每日為您推播LLM與AI相關新聞彙整。輸入「訂閱新聞 [您的關鍵字]」可自訂新聞主題，或輸入「訂閱新聞」使用預設主題。輸入「取消訂閱新聞」可取消推播。"])
            elif event_type == "unfollow": # *** 修改 Unfollow 事件處理 ***
                if user_id:
                    app.logger.info(f"User {user_id} unfollowed/blocked.")
                    if user_id in USER_PREFERENCES:
                        USER_PREFERENCES[user_id]["subscribed_news"] = False # 標記為不訂閱，但保留偏好
                        save_user_preferences(USER_PREFERENCES)
                        # 或者直接刪除用戶記錄：
                        # del USER_PREFERENCES[user_id]
                        # save_user_preferences(USER_PREFERENCES)
        return jsonify({"status": "success"}), 200
    except Exception as e:
        app.logger.error(f"Error processing webhook: {e}", exc_info=True)
        return jsonify({"status": "error", "message": str(e)}), 500

def generate_and_push_news_for_user(user_id, user_custom_keywords=None, is_immediate_push=False):
    """
    為指定用戶獲取、摘要並推播新聞。
    :param user_id: 要推播的用戶 ID。
    :param user_custom_keywords: 用戶的自定義關鍵字，如果為 None 則使用預設。
    :param is_immediate_push: 標記是否為即時觸發的推播 (用於日誌或微小行為差異)。
    """
    with app.app_context(): # 確保在 Flask 上下文中
        log_prefix = "ImmediatePush" if is_immediate_push else "ScheduledPush"
        app.logger.info(f"{log_prefix}: Starting news generation for user {user_id} with keywords: '{user_custom_keywords if user_custom_keywords else 'DEFAULT'}'")

        articles = fetch_llm_news_from_google_rss(user_custom_keywords)
        if not articles:
            message_to_send = f"抱歉，目前未能根據您的關鍵字「{user_custom_keywords if user_custom_keywords else '預設主題'}」找到相關新聞。"
            if not user_custom_keywords: # 如果是預設主題還沒新聞
                 message_to_send = "抱歉，目前預設主題下暫無新聞。"
            send_line_messages(user_id, None, [message_to_send]) # 即時推播，沒有 reply_token
            app.logger.info(f"{log_prefix}: No articles fetched for user {user_id}.")
            return

        llm_full_summary_output = summarize_news_with_llm(articles)
        app.logger.info(f"{log_prefix}: Raw summary for user {user_id} (first 50 chars): {llm_full_summary_output[:50]}...")
        # app.logger.debug(f"{log_prefix}: Full Raw summary for user {user_id} for CoT check: '{llm_full_summary_output}'") # 可選的詳細日誌

        think_pattern = re.compile(r"<think>(.*?)</think>", re.DOTALL | re.IGNORECASE)
        match = think_pattern.search(llm_full_summary_output)
        summary_think_text = ""
        summary_formal_text = ""

        if match:
            summary_think_text = match.group(1).strip()
            summary_formal_text = llm_full_summary_output[match.end():].strip()
        else:
            summary_formal_text = llm_full_summary_output.strip()

        if not summary_think_text and not summary_formal_text:
            app.logger.warning(f"{log_prefix}: News summary for user {user_id} is empty after CoT parsing.")
            send_line_messages(user_id, None, ["今日新聞摘要生成異常，請稍後。"])
            return

        sent_think_part_for_user = False
        if summary_think_text:
            think_chunks = split_long_message(f"⚙️ 新聞摘要思考過程：\n{summary_think_text}")
            if think_chunks:
                send_line_messages(user_id, None, think_chunks) # 即時推播，沒有 reply_token
                sent_think_part_for_user = True
                if summary_formal_text: # 只有在思考和正式摘要都存在時才延遲
                    app.logger.info(f"{log_prefix}: Delaying {VISUAL_SEPARATION_DELAY}s for visual separation for user {user_id}")
                    time.sleep(VISUAL_SEPARATION_DELAY)
        
        if summary_formal_text:
            formal_chunks = split_long_message(summary_formal_text)
            if formal_chunks:
                send_line_messages(user_id, None, formal_chunks) # 即時推播，沒有 reply_token
        elif not sent_think_part_for_user : # 如果思考部分也沒發送，且正式摘要也為空
            send_line_messages(user_id, None, ["今日新聞摘要內容目前為空。"])
        
        app.logger.info(f"{log_prefix}: News push finished for user {user_id}.")
        
def handle_text_message_event(user_id, reply_token, user_text_original):
    if not user_id or not reply_token:
        app.logger.warning("handle_text_message_event: Missing user_id or reply_token.")
        return

    user_text_stripped = user_text_original.strip()
    user_text_lower = user_text_stripped.lower()
    app.logger.info(f"Handling text from {user_id}: '{user_text_original}'")

    # --- 訂閱/取消訂閱指令 (*** 修改 ***) ---
    subscribe_command = "訂閱新聞"
    unsubscribe_command = "取消訂閱新聞"
    
    user_pref = USER_PREFERENCES.get(user_id, {}) # 獲取或初始化用戶偏好

    if user_text_lower.startswith(subscribe_command):
        keywords_from_user  = user_text_stripped[len(subscribe_command):].strip()
        user_pref["subscribed_news"] = True
        keywords_to_store_and_push = None # 初始化用於儲存和即時推播的關鍵字
        if keywords_from_user : # 如果用戶提供了關鍵字
            user_pref["news_keywords"] = keywords_from_user 
            keywords_to_store_and_push = keywords_from_user # 用戶指定了關鍵字
#             keywords_for_immediate_push = keywords_from_user # 即時推播也用這個
            reply_msg = f"已為您訂閱每日新聞，關鍵字為：「{keywords_from_user}」。若要取消，請輸入「取消訂閱新聞」。"
            app.logger.info(f"User {user_id} subscribed to news with keywords: '{keywords_from_user}'")
        else: # 使用預設關鍵字
            user_pref["news_keywords"] = None # None 表示使用預設
            reply_msg = f"已為您訂閱每日新聞（使用預設主題）。輸入「訂閱新聞 [您的關鍵字]」可自訂。若要取消，請輸入「取消訂閱新聞」。"
            app.logger.info(f"User {user_id} subscribed to news with default keywords.")
        USER_PREFERENCES[user_id] = user_pref
        save_user_preferences(USER_PREFERENCES)
        send_line_messages(user_id, reply_token, [reply_msg])
        
        # 然後觸發一次即時新聞推播 (使用 push API)
        # 這裡需要確保 generate_and_push_news_for_user 不會嘗試使用 reply_token
        app.logger.info(f"Triggering immediate news push for user {user_id} with keywords: '{keywords_to_store_and_push if keywords_to_store_and_push else 'DEFAULT'}'")
        try:
            generate_and_push_news_for_user(user_id, keywords_to_store_and_push, True)
        except Exception as e:
            app.logger.error(f"Error triggering immediate news push for user {user_id}: {e}")
            # 可以考慮再推播一條失敗訊息給用戶
            # send_line_messages(user_id, None, ["抱歉，即時新聞摘要生成失敗，但您的訂閱已成功。"])
            send_line_messages(user_id, None, ["抱歉，即時新聞摘要生成失敗，但您的訂閱已成功。"])
        
        return
        
    elif user_text_lower == unsubscribe_command:
        user_pref["subscribed_news"] = False
        # 我們可以保留 news_keywords 偏好，或將其設為 None
        # user_pref["news_keywords"] = None 
        USER_PREFERENCES[user_id] = user_pref
        save_user_preferences(USER_PREFERENCES)
        send_line_messages(user_id, reply_token, ["已為您取消訂閱每日新聞。"])
        app.logger.info(f"User {user_id} unsubscribed from news.")
        return

    # --- 對話機器人邏輯 (與之前版本相同，CoT 處理) ---
    is_triggered_by_bot_name = any(user_text_original.lower().startswith(name.lower()) for name in BOT_NAMES if name)
    if BOT_DEACTIVATED or not is_triggered_by_bot_name:
        if BOT_DEACTIVATED: app.logger.info("Bot is deactivated.")
        else: app.logger.info(f"Message '{user_text_original[:20]}...' not for bot (trigger: {BOT_NAMES}).")
        return

    processed_message_for_llm = user_text_original
    for bot_name in BOT_NAMES:
        if bot_name and user_text_original.lower().startswith(bot_name.lower()):
            processed_message_for_llm = user_text_original[len(bot_name):].strip()
            break
    if not processed_message_for_llm:
        send_line_messages(user_id, reply_token, ["嗨！有什麼可以幫您的嗎？"])
        return

    app.logger.info(f"Sending to LLM for user {user_id}: '{processed_message_for_llm}'")
    llm_full_response = generate_chat_response(processed_message_for_llm)
    app.logger.debug(f"RAW LLM Response for {user_id} (for CoT check): '{llm_full_response}'")
    think_pattern = re.compile(r"<think>(.*?)</think>", re.DOTALL | re.IGNORECASE)
    match = think_pattern.search(llm_full_response)
    reply_token_has_been_used = False
    if match:
        thinking_process_text = match.group(1).strip() ; formal_reply_text = llm_full_response[match.end():].strip()
        app.logger.info(f"CoT found for {user_id}. Thinking: '{thinking_process_text[:30]}...', Formal: '{formal_reply_text[:30]}...'")
        if thinking_process_text:
            think_chunks = split_long_message(f"⚙️ 我的思考過程：\n{thinking_process_text}")
            if think_chunks: send_line_messages(user_id, reply_token, think_chunks) ; reply_token_has_been_used = True
        if reply_token_has_been_used and formal_reply_text: 
            app.logger.info(f"Delaying {VISUAL_SEPARATION_DELAY}s before sending formal reply to {user_id}.") ; time.sleep(VISUAL_SEPARATION_DELAY)
        if formal_reply_text:
            formal_chunks = split_long_message(formal_reply_text)
            if formal_chunks: send_line_messages(user_id, None if reply_token_has_been_used else reply_token, formal_chunks)
        elif not thinking_process_text: send_line_messages(user_id, reply_token, ["嗯...我好像什麼都沒想到（即使有思考標籤）。"])
    else: 
        app.logger.info(f"No CoT found in LLM response for {user_id}.")
        response_chunks = split_long_message(llm_full_response)
        if not response_chunks and llm_full_response.strip(): response_chunks = [llm_full_response.strip()]
        elif not response_chunks: response_chunks = ["我目前沒有回應。"]
        send_line_messages(user_id, reply_token, response_chunks)

# --- APScheduler 初始化 ---
scheduler = BackgroundScheduler(timezone="Asia/Taipei", daemon=True)

# --- 排程任務定義 (*** 修改 ***) ---
def daily_news_push_job():
    with app.app_context():
        app.logger.info("APScheduler: Starting daily_news_push_job...")
        
        current_user_prefs = load_user_preferences() 
        
        # 準備要處理的用戶列表：(user_id, keywords_to_use)
        # keywords_to_use 如果是 None，表示使用預設
        users_to_process = []

        # 1. 添加所有活躍訂閱者
        for uid, prefs in current_user_prefs.items():
            if prefs.get("subscribed_news", False):
                users_to_process.append((uid, prefs.get("news_keywords"))) # news_keywords 可能是 None

        # 2. 處理測試用戶
        if TARGET_USER_ID_FOR_TESTING:
            # 檢查測試用戶是否已經在列表中 (作為活躍訂閱者)
            is_test_user_already_listed = any(u[0] == TARGET_USER_ID_FOR_TESTING for u in users_to_process)
            
            if not users_to_process: # 如果沒有活躍訂閱者，則只處理測試用戶
                app.logger.info(f"APScheduler: No active subscribers. Processing only for test user {TARGET_USER_ID_FOR_TESTING}.")
                test_user_specific_prefs = current_user_prefs.get(TARGET_USER_ID_FOR_TESTING, {})
                users_to_process.append((TARGET_USER_ID_FOR_TESTING, test_user_specific_prefs.get("news_keywords")))
            elif not is_test_user_already_listed: # 如果有活躍訂閱者，但測試用戶不在其中
                app.logger.info(f"APScheduler: Adding test user {TARGET_USER_ID_FOR_TESTING} to processing list for default news.")
                test_user_specific_prefs = current_user_prefs.get(TARGET_USER_ID_FOR_TESTING, {})
                # 除非測試用戶有自己的偏好，否則這裡可以假定測試用戶總是想看預設新聞，或者他自己設定的
                users_to_process.append((TARGET_USER_ID_FOR_TESTING, test_user_specific_prefs.get("news_keywords")))


        if not users_to_process:
            app.logger.info("APScheduler: No users (subscribers or test user) to push news to.")
            return
            
        app.logger.info(f"APScheduler: Preparing to push news to {len(users_to_process)} user entries.")

        processed_users_count = 0
        for user_id, keywords_for_this_user in users_to_process: # 現在 keywords_for_this_user 就是該用戶應使用的關鍵字 (或None)
            app.logger.info(f"APScheduler: Processing user {user_id} with keywords: '{keywords_for_this_user if keywords_for_this_user else 'DEFAULT'}'")

            # ***************************************************************
            # *** 核心修改：調用通用的新聞處理和推送函數 ***
            # ***************************************************************
            try:
                generate_and_push_news_for_user(user_id, keywords_for_this_user, is_immediate_push=False)
            except Exception as e_push:
                app.logger.error(f"APScheduler: Error during generate_and_push_news_for_user for {user_id}: {e_push}", exc_info=True)
            
            processed_users_count += 1
            # 用戶間的延遲 (如果還有下一個用戶)
            if len(users_to_process) > 1 and processed_users_count < len(users_to_process):
                delay_between_users = 1.0 # 你可以調整這個值，例如 0.7 或 1.0
                app.logger.info(f"APScheduler: Delaying {delay_between_users}s before processing next user.")
                time.sleep(delay_between_users)
        
        app.logger.info("APScheduler: daily_news_push_job finished.")


# --- 應用程式退出時關閉排程器 ---
def shutdown_scheduler_on_exit():
    if scheduler.running:
        scheduler.shutdown(wait=False)
        app.logger.info("APScheduler shut down.")

# --- 主應用程式啟動部分 (與之前版本相同，包含 RUN_JOB_ON_STARTUP) ---
if __name__ == '__main__':
    # ... (環境變數檢查與 RUN_JOB_ON_STARTUP 邏輯不變) ...
    required_env_vars = ['LINE_CHANNEL_ACCESS_TOKEN', 'LINE_CHANNEL_SECRET', 'OPENAI_API_KEY']
    missing_vars = [var for var in required_env_vars if not os.getenv(var)]
    if missing_vars: app.logger.critical(f"CRITICAL: Missing required environment variables: {', '.join(missing_vars)}. Exiting.") ; exit(1)
    if not BOT_NAMES or not BOT_NAMES[0]: app.logger.warning("BOT_NAMES environment variable is not set or is empty.")
    RUN_JOB_ON_STARTUP = os.getenv("RUN_JOB_ON_STARTUP", "False").lower() == "true"
    app.logger.info(f"RUN_JOB_ON_STARTUP setting is: {RUN_JOB_ON_STARTUP}")

    if not scheduler.running:
        app.logger.info("APScheduler is not running. Proceeding with initialization and job adding.")
        try:
            job_configs = [] # 改名為 configs
            job_configs.append({"func": daily_news_push_job, "trigger": "cron", "hour": 9, "minute": 0, "id": "daily_news_cron", "name": "Daily News Cron"})
            # job_configs.append({"func": daily_news_push_job, "trigger": "interval", "minutes": 3, "id": "news_job_test_interval", "name": "News Test Interval"}) # 測試用
            if RUN_JOB_ON_STARTUP:
                run_now_time = datetime.now(scheduler.timezone) + timedelta(seconds=15)
                app.logger.info(f"Calculated run_now_time for startup job: {run_now_time.strftime('%Y-%m-%d %H:%M:%S %Z')}")
                job_configs.append({"func": daily_news_push_job, "trigger": "date", "run_date": run_now_time, "id": "daily_news_on_startup", "name": "Immediate News on Startup"})
            for job_config in job_configs:
                try:
                    job_params = {k: v for k, v in job_config.items() if k != 'name'}
                    job_params['replace_existing'] = True
                    scheduler.add_job(**job_params)
                    app.logger.info(f"Scheduled job '{job_config.get('name', job_config['id'])}'.")
                except Exception as job_add_err:
                    app.logger.error(f"Failed to add job '{job_config.get('name', job_config['id'])}': {job_add_err}")
            scheduler.start()
            app.logger.info("APScheduler started.")
            atexit.register(shutdown_scheduler_on_exit)
        except Exception as e:
            app.logger.error(f"Failed to initialize or start APScheduler: {e}")
    else:
        app.logger.info("APScheduler is already running.")
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)
