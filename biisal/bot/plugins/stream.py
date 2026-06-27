import re
import os
import asyncio
import json
import logging
from pathlib import Path
from biisal.bot import StreamBot
from biisal.utils.database import Database
from biisal.utils.human_readable import humanbytes
from biisal.vars import Var
from urllib.parse import quote_plus
from pyrogram import filters, Client
from pyrogram.enums import ParseMode
from pyrogram.errors import FloodWait
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from biisal.utils.file_properties import get_name, get_hash, get_media_from_message
from helper_func import encode, get_message_id, decode, get_messages
from biisal.utils.thumbnail_extractor import extract_thumbnail_from_middle
from biisal.utils.github_uploader import upload_image_to_github

db = Database(Var.DATABASE_URL, Var.name)
CUSTOM_CAPTION = os.environ.get("CUSTOM_CAPTION", None)
PROTECT_CONTENT = os.environ.get('PROTECT_CONTENT', "False") == "True"
DISABLE_CHANNEL_BUTTON = os.environ.get("DISABLE_CHANNEL_BUTTON", None) == 'True'
GIT_TOKEN = os.environ.get('GIT_TOKEN', '')
THUMB_API = os.environ.get('THUMB_API', '')
GITHUB_OWNER_REPO = os.environ.get('GITHUB_OWNER_REPO', 'sunday2212/webreadme4')

MY_PASS = None

_batch_sessions = {}
_fwd_sessions = {}
_fbatch_sessions = {}
_FBATCH_CHUNK = 100
_FBATCH_DELAY = 0.4


def sanitize_caption(text: str) -> str:
    if not text:
        return text
    text = re.sub(r'<[^>]+>', '', text)
    text = re.sub(r'@[\w_]+', '', text)
    text = re.sub(r'(?:https?://|t\.me/|telegram\.me/)[^\s]+', '', text)
    text = re.sub(r'\s*#\w+', '', text)
    text = re.sub(r'\s+', ' ', text.strip())
    return text


async def create_intermediate_link(message: Message):
    media = get_media_from_message(message)
    if not media:
        raise ValueError("No media found in message")

    caption = ""
    if message.caption:
        caption = sanitize_caption(message.caption.html)
    if not caption or not caption.strip():
        filename = getattr(media, 'file_name', None) or get_name(message)
        if filename:
            caption = sanitize_caption(filename)
    if not caption or not caption.strip():
        import secrets
        caption = f"file_{secrets.token_hex(4)}"

    message_data = {
        'message_id': message.id,
        'file_name': getattr(media, 'file_name', None) or get_name(message),
        'file_size': getattr(media, 'file_size', 0),
        'mime_type': getattr(media, 'mime_type', 'application/octet-stream'),
        'caption': caption,
        'from_chat_id': message.chat.id,
        'file_unique_id': getattr(media, 'file_unique_id', '')
    }

    current_domain = Var.get_current_domain()
    token = await db.store_temp_file(message_data, domain=current_domain)
    base_url = Var.get_base_url()
    intermediate_link = f"{base_url}prepare/{token}"
    return intermediate_link, caption


async def create_intermediate_link_for_batch(message: Message, folder_name: str = None, client: Client = None, shared_thumbnail_url: str = None):
    try:
        media = get_media_from_message(message)
        if not media:
            raise ValueError("No media found in message")

        caption = ""
        if message.caption:
            caption = sanitize_caption(message.caption.html)
        if not caption or not caption.strip():
            filename = getattr(media, 'file_name', None) or get_name(message)
            if filename:
                caption = sanitize_caption(filename)
        if not caption or not caption.strip():
            import secrets
            caption = f"file_{secrets.token_hex(4)}"

        message_data = {
            'message_id': message.id,
            'file_name': getattr(media, 'file_name', None) or get_name(message),
            'file_size': getattr(media, 'file_size', 0),
            'mime_type': getattr(media, 'mime_type', 'application/octet-stream'),
            'caption': caption,
            'from_chat_id': message.chat.id,
            'file_unique_id': getattr(media, 'file_unique_id', '')
        }

        mime_type = getattr(media, 'mime_type', '')
        thumbnail_url = shared_thumbnail_url

        if not shared_thumbnail_url and mime_type and mime_type.startswith('video/') and folder_name and THUMB_API and client:
            temp_video_path = None
            thumbnail_path = None
            try:
                temp_dir = Path("/tmp/batch_videos")
                temp_dir.mkdir(exist_ok=True)
                import secrets as sec
                temp_video_path = str(temp_dir / f"video_{sec.token_hex(8)}.mp4")
                await client.download_media(message, file_name=temp_video_path)
                thumbnail_path = await extract_thumbnail_from_middle(temp_video_path)
                thumbnail_url = await upload_image_to_github(
                    image_path=thumbnail_path,
                    github_token=THUMB_API,
                    folder_name=folder_name,
                    title_name=caption
                )
                logging.info(f"Thumbnail uploaded: {thumbnail_url}")
            except Exception as thumb_error:
                thumb_err_str = str(thumb_error)
                logging.error(f"Thumbnail failed for '{caption}': {thumb_err_str}")
                if not message_data.get('_thumb_error'):
                    message_data['_thumb_error'] = thumb_err_str
            finally:
                try:
                    if temp_video_path and os.path.exists(temp_video_path):
                        os.remove(temp_video_path)
                    if thumbnail_path and os.path.exists(thumbnail_path):
                        os.remove(thumbnail_path)
                except Exception as cleanup_error:
                    logging.error(f"Cleanup error: {cleanup_error}")

        if thumbnail_url:
            message_data['thumbnail_url'] = thumbnail_url

        current_domain = Var.get_current_domain()
        base_url = Var.get_base_url()

        if current_domain:
            token = await db.store_temp_file(message_data, domain=current_domain)
            stream_link = f"{base_url}prepare/{token}?type=stream"
            download_link = f"{base_url}prepare/{token}?type=download"
            result = {
                "title": caption,
                "streamingUrl": stream_link,
                "downloadUrl": download_link
            }
        else:
            token_web = await db.store_temp_file(message_data, domain='web')
            token_webx = await db.store_temp_file(message_data, domain='webx')
            result = {
                "title": caption,
                "streamingUrl": f"{Var.URL_WEB}prepare/{token_web}?type=stream",
                "streamingUrlx": f"{Var.URL_WEBX}prepare/{token_webx}?type=stream",
                "downloadUrl": f"{Var.URL_WEB}prepare/{token_web}?type=download",
                "downloadUrlx": f"{Var.URL_WEBX}prepare/{token_webx}?type=download"
            }

        if thumbnail_url:
            result["thumbnailUrl"] = thumbnail_url

        thumb_err = message_data.get('_thumb_error')
        if thumb_err:
            result["_thumb_error"] = thumb_err

        return result
    except Exception as e:
        raise ValueError(f"Failed to create intermediate links: {str(e)}")


async def create_pdf_download_links(message: Message):
    media = get_media_from_message(message)
    if not media:
        raise ValueError("No media found in message")

    title = ""
    if message.caption:
        title = sanitize_caption(message.caption.html)
    if not title or not title.strip():
        filename = getattr(media, 'file_name', None) or get_name(message)
        if filename:
            title = sanitize_caption(filename)
    if not title or not title.strip():
        import secrets as _sec
        title = f"pdf_{_sec.token_hex(4)}"

    message_data = {
        'message_id': message.id,
        'file_name': getattr(media, 'file_name', None) or get_name(message),
        'file_size': getattr(media, 'file_size', 0),
        'mime_type': getattr(media, 'mime_type', 'application/pdf'),
        'caption': title,
        'from_chat_id': message.chat.id,
        'file_unique_id': getattr(media, 'file_unique_id', '')
    }

    current_domain = Var.get_current_domain()
    if current_domain:
        token = await db.store_temp_file(message_data, domain=current_domain)
        base_url = Var.get_base_url()
        download_link = f"{base_url}prepare/{token}?type=download"
        if current_domain == 'web':
            return {"title": title, "pdf_downloadUrl": download_link}
        else:
            return {"title": title, "pdf_downloadUrlx": download_link}
    else:
        token_web = await db.store_temp_file(message_data, domain='web')
        token_webx = await db.store_temp_file(message_data, domain='webx')
        return {
            "title": title,
            "pdf_downloadUrl": f"{Var.URL_WEB}prepare/{token_web}?type=download",
            "pdf_downloadUrlx": f"{Var.URL_WEBX}prepare/{token_webx}?type=download"
        }


async def process_message(msg, json_output, skipped_messages, folder_name=None, client=None, shared_thumbnail_url=None):
    try:
        if not (msg.document or msg.video or msg.audio):
            return

        is_pdf = (
            msg.document and
            getattr(msg.document, 'mime_type', '') == 'application/pdf'
        )

        if is_pdf:
            pdf_data = await create_pdf_download_links(msg)
            json_output.append(pdf_data)
            return

        intermediate_data = await create_intermediate_link_for_batch(msg, folder_name, client, shared_thumbnail_url)
        json_output.append(intermediate_data)

    except Exception as e:
        file_name = get_name(msg) or "Unknown"
        skipped_messages.append({
            "id": msg.id,
            "file_name": file_name,
            "reason": str(e)
        })


def generate_lecture_html(json_filename: str, github_dest_folder: str = '') -> str:
    parts = [p for p in github_dest_folder.strip('/').split('/') if p]
    depth = max(len(parts) - 2, 0)
    prefix = '../' * depth if depth > 0 else './'
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Lectures</title>
  <link rel="stylesheet" href="{prefix}styles.css">
  <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.0.0/css/all.min.css">
</head>
<body>
  <div id="lectureList"><p>Loading...</p></div>
  <script src="{prefix}access-control.js"></script>
  <script src="{prefix}block.js"></script>
  <script src="{prefix}error-handler/link-checker.js"></script>
  <script>
    fetch('{json_filename}')
      .then(r => r.json())
      .then(data => {{
        const list = document.getElementById('lectureList');
        list.innerHTML = '';
        (data.lectures || []).forEach(l => {{
          const d = document.createElement('div');
          d.innerHTML = '<h3>' + l.title + '</h3>' +
            (l.streamingUrl ? '<a href="' + l.streamingUrl + '">Stream</a> ' : '') +
            (l.downloadUrl ? '<a href="' + l.downloadUrl + '">Download</a>' : '');
          list.appendChild(d);
        }});
      }});
  </script>
  <script src="{prefix}stream-player-utils.js"></script>
  <script src="{prefix}theme.js"></script>
</body>
</html>"""


async def upload_to_github(file_content: str, file_path: str, commit_message: str, token: str, branch: str = None):
    import base64
    import aiohttp

    try:
        if not token:
            return False, "GIT_TOKEN is empty or not set"

        normalized = file_path.strip().lstrip('/').rstrip('/')
        parts = normalized.split('/', 2)
        if len(parts) < 3:
            return False, f"Invalid path format: '{file_path}'"

        owner, repo, path = parts[0], parts[1], parts[2]
        api_url = f"https://api.github.com/repos/{owner}/{repo}/contents/{path}"
        content_encoded = base64.b64encode(file_content.encode('utf-8')).decode('utf-8')
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github.v3+json"
        }

        async with aiohttp.ClientSession() as session:
            params = {'ref': branch} if branch else {}
            sha = None
            async with session.get(api_url, headers=headers, params=params) as resp_get:
                if resp_get.status == 200:
                    data = await resp_get.json()
                    sha = data.get('sha')
                elif resp_get.status == 401:
                    return False, "GitHub token is invalid or expired (401)"
                elif resp_get.status == 403:
                    text = await resp_get.text()
                    return False, f"GitHub 403 Forbidden: {text[:300]}"

            payload = {"message": commit_message or "Add file via bot", "content": content_encoded}
            if sha:
                payload["sha"] = sha
            if branch:
                payload["branch"] = branch

            async with session.put(api_url, headers=headers, json=payload) as resp_put:
                resp_text = await resp_put.text()
                if resp_put.status in (200, 201):
                    return True, None
                elif resp_put.status == 401:
                    return False, "GitHub token invalid/expired (401)"
                elif resp_put.status == 403:
                    return False, f"GitHub 403 Forbidden: {resp_text[:300]}"
                elif resp_put.status == 404:
                    return False, f"GitHub 404 — repo '{owner}/{repo}' not found"
                elif resp_put.status == 422:
                    return False, f"GitHub 422 Unprocessable: {resp_text[:300]}"
                else:
                    return False, f"HTTP {resp_put.status}: {resp_text[:400]}"

    except Exception as e:
        return False, f"Exception: {type(e).__name__}: {e}"


@StreamBot.on_message(filters.private & filters.user(list(Var.ADMIN_IDS)) & filters.command('batch'))
async def batch_command(client: Client, message: Message):
    user_id = message.from_user.id
    _batch_sessions[user_id] = {'state': 'waiting_folder', 'data': {}}
    await message.reply_text(
        "📁 Enter the destination folder path:\n\n"
        "Format: path/to/folder\n"
        "Example: 1234xxx/marrow/anatomy\n\n"
        "This is where JSON/HTML files will be uploaded."
    )


@StreamBot.on_message(
    filters.private & filters.user(list(Var.ADMIN_IDS)) & filters.text
    & ~filters.command(['batch', 'fbatch', 'fwd', 'start', 'gen', 'users', 'broadcast', 'ping', 'root', 'checkenv', 'ban', 'unban'])
)
async def batch_conversation_handler(client: Client, message: Message):
    user_id = message.from_user.id

    batch_session = _batch_sessions.get(user_id)
    if batch_session:
        if batch_session['state'] == 'waiting_folder':
            folder_path = message.text.strip()
            github_dest_folder = f"{GITHUB_OWNER_REPO}/{folder_path}"
            _batch_sessions[user_id] = {
                'state': 'waiting_links',
                'data': {'github_dest_folder': github_dest_folder}
            }
            await message.reply_text(
                "📝 Send the links with subjects in this format:\n\n"
                "ANATOMY\n"
                "F - https://t.me/c/2024354927/237364\n"
                "L - https://t.me/c/2024354927/237366\n\n"
                "Each subject should have F (first) and L (last) message links."
            )
        elif batch_session['state'] == 'waiting_links':
            github_dest_folder = batch_session['data']['github_dest_folder']
            del _batch_sessions[user_id]
            await _run_batch_processing(client, message, github_dest_folder)
        return

    fbatch_session = _fbatch_sessions.get(user_id)
    if fbatch_session:
        if fbatch_session['state'] == 'waiting_range':
            del _fbatch_sessions[user_id]
            await _run_fbatch_scan(client, message)
        return

    fwd_session = _fwd_sessions.get(user_id)
    if fwd_session:
        if fwd_session['state'] == 'waiting_links':
            del _fwd_sessions[user_id]
            await _run_fwd_processing(client, message)
        return


async def _run_batch_processing(client: Client, message: Message, github_dest_folder: str):
    try:
        links_text = message.text.strip()
        subjects_data = []
        current_subject = None
        current_first = None
        current_last = None

        for line in links_text.split('\n'):
            line = line.strip()
            if not line:
                continue
            if not line.startswith('F -') and not line.startswith('L -'):
                if current_subject and current_first and current_last:
                    subjects_data.append({
                        'subject': current_subject,
                        'first': current_first,
                        'last': current_last
                    })
                current_subject = line
                current_first = None
                current_last = None
            elif line.startswith('F -'):
                current_first = line.replace('F -', '').strip()
            elif line.startswith('L -'):
                current_last = line.replace('L -', '').strip()

        if current_subject and current_first and current_last:
            subjects_data.append({
                'subject': current_subject,
                'first': current_first,
                'last': current_last
            })

        if not subjects_data:
            await message.reply("❌ No valid subjects found. Check the format.")
            return

        git_token = os.environ.get('GIT_TOKEN', '')
        if not git_token:
            await message.reply(
                "❌ GIT_TOKEN not found in environment variables.\n\n"
                "Add your GitHub Personal Access Token with repo permissions."
            )
            return

        status_msg = await message.reply_text(
            f"🚀 Starting batch processing for {len(subjects_data)} subjects..."
        )

        success_count = 0
        fail_count = 0

        for idx, subject_info in enumerate(subjects_data, 1):
            subject_name = subject_info['subject']
            json_output = []
            skipped_messages = []

            try:
                class MockMessage:
                    def __init__(self, text):
                        self.text = text
                        self.forward_from_chat = None
                        self.forward_sender_name = None

                f_msg_id = await get_message_id(client, MockMessage(subject_info['first']))
                s_msg_id = await get_message_id(client, MockMessage(subject_info['last']))

                if not f_msg_id or not s_msg_id:
                    fail_count += 1
                    await message.reply_text(
                        f"❌ [{idx}/{len(subjects_data)}] {subject_name}\n"
                        f"Invalid message IDs — could not resolve:\n"
                        f"F: {subject_info['first']}\nL: {subject_info['last']}"
                    )
                    await status_msg.edit_text(
                        f"⏳ Progress: {idx}/{len(subjects_data)} | ✅ {success_count} | ❌ {fail_count}"
                    )
                    continue

                start_id = min(f_msg_id, s_msg_id)
                end_id = max(f_msg_id, s_msg_id)
                total_messages = end_id - start_id + 1

                await status_msg.edit_text(
                    f"🔄 [{idx}/{len(subjects_data)}] Processing: {subject_name}\n"
                    f"Messages: {total_messages} | ✅ {success_count} | ❌ {fail_count}"
                )

                batch_size = 50
                shared_thumbnail_url = None
                thumb_warning = None

                for batch_start in range(start_id, end_id + 1, batch_size):
                    batch_end = min(batch_start + batch_size - 1, end_id)
                    msg_ids = list(range(batch_start, batch_end + 1))

                    try:
                        messages = await get_messages(client, msg_ids)
                    except Exception:
                        messages = []
                        for msg_id in msg_ids:
                            try:
                                msg = (await get_messages(client, [msg_id]))[0]
                                messages.append(msg)
                            except:
                                messages.append(None)

                    for msg in messages:
                        if not msg:
                            skipped_messages.append({"id": "Unknown", "file_name": "Unknown", "reason": "Message not found"})
                            continue
                        thumbnail_folder = subject_name.lower().replace(" ", "_")
                        await process_message(msg, json_output, skipped_messages, thumbnail_folder, client, shared_thumbnail_url)
                        if json_output:
                            last_entry = json_output[-1]
                            if not shared_thumbnail_url and 'thumbnailUrl' in last_entry:
                                shared_thumbnail_url = last_entry['thumbnailUrl']
                            if not thumb_warning and '_thumb_error' in last_entry:
                                thumb_warning = last_entry.pop('_thumb_error')
                            elif '_thumb_error' in last_entry:
                                last_entry.pop('_thumb_error')

                clean_output = [{k: v for k, v in e.items() if k != '_thumb_error'} for e in json_output]
                output_data = {
                    "subjectName": subject_name.lower().replace(" ", ""),
                    "lectures": clean_output,
                    "skipped": skipped_messages
                }

                json_filename = f"{subject_name}.json"
                json_content = json.dumps(output_data, indent=4, ensure_ascii=False)
                github_file_path = f"{github_dest_folder}/{json_filename}".replace('//', '/')
                upload_success, upload_error = await upload_to_github(json_content, github_file_path, f"Add {json_filename}", git_token)

                html_filename = f"{subject_name}.html"
                html_content = generate_lecture_html(json_filename, github_dest_folder)
                github_html_path = f"{github_dest_folder}/{html_filename}".replace('//', '/')
                html_upload_success, html_upload_error = await upload_to_github(html_content, github_html_path, f"Add {html_filename}", git_token)

                if upload_success:
                    success_count += 1
                    notes = []
                    if thumb_warning:
                        notes.append(f"⚠️ Thumb: {thumb_warning[:150]}")
                    if not html_upload_success:
                        notes.append(f"⚠️ HTML failed: {(html_upload_error or '')[:150]}")
                    note_text = "\n" + "\n".join(notes) if notes else ""
                    await status_msg.edit_text(
                        f"✅ [{idx}/{len(subjects_data)}] {subject_name} done!\n"
                        f"Lectures: {len(clean_output)} | Skipped: {len(skipped_messages)}{note_text}\n\n"
                        f"Overall: ✅ {success_count} | ❌ {fail_count}"
                    )
                else:
                    fail_count += 1
                    await message.reply_text(
                        f"❌ [{idx}/{len(subjects_data)}] {subject_name} — upload failed\n\n"
                        f"📁 {github_file_path}\n🔍 {upload_error}"
                    )
                    await status_msg.edit_text(
                        f"⏳ [{idx}/{len(subjects_data)}] {subject_name} failed\n"
                        f"Overall: ✅ {success_count} | ❌ {fail_count}"
                    )

                await asyncio.sleep(1)

            except Exception as e:
                fail_count += 1
                logging.error(f"Exception processing {subject_name}: {e}", exc_info=True)
                await message.reply_text(f"❌ [{idx}/{len(subjects_data)}] {subject_name}\n{type(e).__name__}: {e}")
                await status_msg.edit_text(
                    f"⏳ [{idx}/{len(subjects_data)}] {subject_name} failed\n"
                    f"Overall: ✅ {success_count} | ❌ {fail_count}"
                )
                continue

        await message.reply_text(
            f"🏁 Batch complete!\n"
            f"Total: {len(subjects_data)} | ✅ Success: {success_count} | ❌ Failed: {fail_count}"
        )
        await status_msg.edit_text(f"✅ Done — {success_count}/{len(subjects_data)} uploaded.")

    except Exception as e:
        logging.error(f"Fatal error in _run_batch_processing: {e}", exc_info=True)
        await message.reply(f"❌ Fatal error: {type(e).__name__}: {e}")


def _raw_chat(chat_id: int) -> str:
    s = str(abs(chat_id))
    return s[3:] if s.startswith("100") else s


def _supergroup_msg_url(chat_id: int, topic_id: int, msg_id: int) -> str:
    return f"https://t.me/c/{_raw_chat(chat_id)}/{topic_id}/{msg_id}"


def _get_topic_id_from_msg(msg) -> "int | None":
    if getattr(msg, "forum_topic_created", None) is not None:
        return msg.id
    for attr in ("reply_to_top_message_id", "message_thread_id"):
        tid = getattr(msg, attr, None)
        if tid:
            return int(tid)
    reply_to = getattr(msg, "reply_to", None)
    if reply_to:
        for attr in ("reply_to_top_id", "reply_to_msg_id"):
            tid = getattr(reply_to, attr, None)
            if tid:
                return int(tid)
    return None


def _parse_fbatch_range(raw: str):
    raw = raw.strip()
    parts = re.split(r'-(?=https?://)', raw, maxsplit=1)
    if len(parts) != 2:
        return None
    start_link, end_link = parts[0].strip(), parts[1].strip()

    def _extract(link: str):
        m = re.match(r"https?://t\.me/c/(\d+)/(\d+)/(\d+)", link)
        if m:
            return int(f"-100{m.group(1)}"), int(m.group(2)), int(m.group(3))
        m = re.match(r"https?://t\.me/c/(\d+)/(\d+)", link)
        if m:
            n = int(m.group(2))
            return int(f"-100{m.group(1)}"), n, n
        return None, None, None

    s_chat, s_topic, s_msg = _extract(start_link)
    e_chat, e_topic, e_msg = _extract(end_link)
    if s_chat is None or e_chat is None or s_chat != e_chat:
        return None
    return s_chat, s_topic, s_msg, e_topic, e_msg


async def _get_chat_latest_msg_id(client: Client, chat_id: int) -> int:
    try:
        async for msg in client.get_chat_history(chat_id, limit=1):
            return msg.id
    except Exception:
        pass
    return 0


async def _scan_forum_topics(client, chat_id, start_topic, scan_start, end_topic, scan_end, status_msg):
    topics = {}
    valid_topic_ids = set()
    phase1_end = max(end_topic, scan_end)
    phase1_limit = phase1_end - start_topic + 100

    try:
        await status_msg.edit_text(f"🔍 Phase 1/2 — discovering topics {start_topic} → {end_topic}…")
    except Exception:
        pass

    try:
        async for msg in client.get_chat_history(chat_id, limit=phase1_limit, offset_id=phase1_end + 1):
            if msg.id < start_topic:
                break
            is_topic_creation = (
                getattr(msg, "forum_topic_created", None) is not None
                or getattr(msg, "new_forum_topic", None) is not None
            )
            if is_topic_creation and start_topic <= msg.id <= end_topic:
                valid_topic_ids.add(msg.id)
                topics[msg.id] = {"min": None, "max": None, "name": None}
    except Exception as exc:
        logging.warning(f"fbatch phase1 error: {exc}")

    latest_id = await _get_chat_latest_msg_id(client, chat_id)
    if latest_id <= 0:
        latest_id = scan_end + 3000

    wide_start = min(start_topic, scan_start)
    wide_end = max(scan_end, latest_id)

    try:
        await status_msg.edit_text(f"🔍 Phase 2/2 — scanning {wide_start}→{wide_end}…")
    except Exception:
        pass

    offset_id = wide_end + 1
    while offset_id > wide_start:
        batch = []
        try:
            async for msg in client.get_chat_history(chat_id, limit=_FBATCH_CHUNK, offset_id=offset_id):
                batch.append(msg)
                if msg.id <= wide_start:
                    break
        except FloodWait as fw:
            await asyncio.sleep(fw.value + 1)
            continue
        except Exception as exc:
            logging.warning(f"fbatch phase2 error: {exc}")
            break

        if not batch:
            break

        for msg in batch:
            if msg.id < wide_start:
                continue
            if (getattr(msg, "forum_topic_created", None) is not None
                    or getattr(msg, "new_forum_topic", None) is not None):
                continue
            has_content = bool(
                msg.text or msg.media or msg.document or msg.video
                or msg.audio or msg.photo or msg.voice
                or msg.video_note or msg.sticker or msg.animation
            )
            if not has_content:
                continue
            tid = _get_topic_id_from_msg(msg)
            if tid is None:
                continue
            if valid_topic_ids:
                if tid not in valid_topic_ids:
                    continue
            else:
                if not (start_topic <= tid <= end_topic):
                    continue

            mid = msg.id
            if tid not in topics:
                topics[tid] = {"min": mid, "max": mid, "name": None}
            else:
                if mid < topics[tid]["min"]:
                    topics[tid]["min"] = mid
                if mid > topics[tid]["max"]:
                    topics[tid]["max"] = mid

        oldest_id = batch[-1].id
        if oldest_id <= wide_start:
            break
        offset_id = oldest_id
        await asyncio.sleep(_FBATCH_DELAY)

    return {tid: info for tid, info in topics.items() if info["min"] is not None}


async def _fetch_topic_names(client, chat_id, topics):
    for tid in list(topics.keys()):
        try:
            msg = await client.get_messages(chat_id, tid)
            if msg and not getattr(msg, "empty", True):
                ftc = getattr(msg, "forum_topic_created", None)
                if ftc:
                    name = getattr(ftc, "name", None) or getattr(ftc, "title", None)
                    if name:
                        topics[tid]["name"] = name
        except Exception:
            pass
        await asyncio.sleep(0.2)


@StreamBot.on_message(filters.private & filters.user(list(Var.ADMIN_IDS)) & filters.command('fbatch'))
async def fbatch_command(client: Client, message: Message):
    user_id = message.from_user.id
    _fbatch_sessions[user_id] = {'state': 'waiting_range'}
    await message.reply_text(
        "📋 Forum Topic Scanner\n\n"
        "Send the **first topic link** and **last topic link** of the range:\n\n"
        "FORMAT: FIRST_TOPIC_LINK-LAST_TOPIC_LINK\n\n"
        "Example: `https://t.me/c/3950094573/5-https://t.me/c/3950094573/9`"
    )


async def _run_fbatch_scan(client: Client, message: Message):
    raw = message.text.strip()
    parsed = _parse_fbatch_range(raw)
    if not parsed:
        await message.reply_text(
            "❌ Could not parse the link range.\n\n"
            "Format: `https://t.me/c/CHATID/TOPIC1-https://t.me/c/CHATID/TOPIC2`"
        )
        return

    chat_id, start_topic, scan_start, end_topic, scan_end = parsed
    if end_topic < start_topic:
        await message.reply_text("❌ End topic ID must be ≥ start topic ID.")
        return

    status_msg = await message.reply_text(
        f"🔍 Forum Topic Scanner started\n"
        f"Topics: {start_topic} → {end_topic}\n"
        f"⏳ Phase 1: discovering topics…"
    )

    try:
        topics = await _scan_forum_topics(client, chat_id, start_topic, scan_start, end_topic, scan_end, status_msg)
    except Exception as exc:
        logging.error(f"fbatch scan error: {exc}", exc_info=True)
        await status_msg.edit_text(f"❌ Scan failed: {type(exc).__name__}: {exc}")
        return

    if not topics:
        await status_msg.edit_text(f"⚠️ No forum topics found in range {start_topic} → {end_topic}.")
        return

    try:
        await status_msg.edit_text(f"✅ Found {len(topics)} topic(s) — fetching names…")
        await _fetch_topic_names(client, chat_id, topics)
    except Exception as exc:
        logging.warning(f"fbatch name fetch error: {exc}")

    sorted_topics = sorted(topics.items(), key=lambda kv: kv[0])
    group_lines = [f"✅ {len(topics)} topics | range {start_topic}→{end_topic}\n"]
    for tid, info in sorted_topics:
        name = info["name"] or f"Topic {tid}"
        f_link = _supergroup_msg_url(chat_id, tid, info["min"])
        l_link = _supergroup_msg_url(chat_id, tid, info["max"])
        group_lines.append(f"{name}:- {f_link}-{l_link}")

    chunk_msgs = []
    current_chunk = []
    current_len = 0
    for line in group_lines:
        if current_len + len(line) + 1 > 4000 and current_chunk:
            chunk_msgs.append("\n".join(current_chunk))
            current_chunk = [line]
            current_len = len(line)
        else:
            current_chunk.append(line)
            current_len += len(line) + 1
    if current_chunk:
        chunk_msgs.append("\n".join(current_chunk))

    for chunk in chunk_msgs:
        try:
            await message.reply_text(chunk, disable_web_page_preview=True)
            await asyncio.sleep(0.5)
        except Exception as exc:
            logging.error(f"fbatch send chunk error: {exc}")

    import tempfile, os as _os
    full_lines = [f"✅ {len(topics)} topics found | range {start_topic} → {end_topic}\n"]
    for tid, info in sorted_topics:
        name = info["name"] or f"Topic {tid}"
        f_link = _supergroup_msg_url(chat_id, tid, info["min"])
        l_link = _supergroup_msg_url(chat_id, tid, info["max"])
        full_lines.append(f"{name}\nF - {f_link}\nL - {l_link}")
    full_text = "\n\n".join(full_lines)

    tmp_path = None
    try:
        fd, tmp_path = tempfile.mkstemp(suffix=".txt", prefix="fbatch_")
        _os.close(fd)
        with open(tmp_path, "w", encoding="utf-8") as f:
            f.write(full_text)
        await client.send_document(
            chat_id=message.chat.id,
            document=tmp_path,
            caption=f"✅ {len(topics)} topics | range {start_topic}→{end_topic}",
            file_name=f"topics_{_raw_chat(chat_id)}_{start_topic}_{end_topic}.txt"
        )
    except Exception as exc:
        logging.error(f"fbatch send file error: {exc}")
    finally:
        if tmp_path and _os.path.exists(tmp_path):
            try:
                _os.remove(tmp_path)
            except Exception:
                pass

    try:
        await status_msg.delete()
    except Exception:
        pass


def parse_tme_link(link: str):
    link = link.strip()
    m3 = re.match(r"https?://t\.me/c/(\d+)/(\d+)/(\d+)", link)
    if m3:
        return int(f"-100{m3.group(1)}"), int(m3.group(3))
    m2 = re.match(r"https?://t\.me/c/(\d+)/(\d+)", link)
    if m2:
        return int(f"-100{m2.group(1)}"), int(m2.group(2))
    raise ValueError(f"Cannot parse link: {link}")


def db_channel_short_id(db_channel: int) -> str:
    s = str(db_channel)
    if s.startswith("-100"):
        return s[4:]
    return s.lstrip("-")


@StreamBot.on_message(filters.private & filters.user(list(Var.ADMIN_IDS)) & filters.command('fwd'))
async def fwd_command(client: Client, message: Message):
    user_id = message.from_user.id
    _fwd_sessions[user_id] = {'state': 'waiting_links'}
    await message.reply_text(
        "📝 Send subjects with F/L links from the *source* channel:\n\n"
        "Format:\n"
        "SubjectName\n"
        "F - https://t.me/c/SOURCE/237\n"
        "L - https://t.me/c/SOURCE/251\n\n"
        "Bot must be admin in the source channel."
    )


async def _run_fwd_processing(client: Client, message: Message):
    try:
        links_text = message.text.strip()
        subjects_data = []
        current_subject = None
        current_first = None
        current_last = None

        for line in links_text.split('\n'):
            line = line.strip()
            if not line:
                continue
            if not line.startswith('F -') and not line.startswith('L -'):
                if current_subject and current_first and current_last:
                    subjects_data.append({'subject': current_subject, 'first': current_first, 'last': current_last})
                current_subject = line
                current_first = None
                current_last = None
            elif line.startswith('F -'):
                current_first = line.replace('F -', '').strip()
            elif line.startswith('L -'):
                current_last = line.replace('L -', '').strip()

        if current_subject and current_first and current_last:
            subjects_data.append({'subject': current_subject, 'first': current_first, 'last': current_last})

        if not subjects_data:
            await message.reply("❌ No valid subjects found.")
            return

        status_msg = await message.reply_text(f"🚀 Starting forward of {len(subjects_data)} subject(s)...")
        db_short = db_channel_short_id(Var.DB_CHANNEL)
        result_lines = []

        for idx, subject_info in enumerate(subjects_data, 1):
            subject_name = subject_info['subject']
            try:
                src_chat_id, first_msg_id = parse_tme_link(subject_info['first'])
                _, last_msg_id = parse_tme_link(subject_info['last'])
                start_id = min(first_msg_id, last_msg_id)
                end_id = max(first_msg_id, last_msg_id)
                total = end_id - start_id + 1

                await status_msg.edit_text(
                    f"📤 Forwarding {subject_name}...\n"
                    f"Subject {idx}/{len(subjects_data)} | Messages: {total}"
                )

                fwd_first_id = None
                fwd_last_id = None
                forwarded_count = 0
                failed_count = 0
                skipped_count = 0
                first_error = None

                for msg_id in range(start_id, end_id + 1):
                    try:
                        src_msg = await client.get_messages(src_chat_id, msg_id)
                        if src_msg is None or getattr(src_msg, 'empty', True):
                            skipped_count += 1
                            await asyncio.sleep(0.2)
                            continue

                        has_content = bool(
                            src_msg.text or src_msg.media or src_msg.document
                            or src_msg.video or src_msg.audio or src_msg.photo
                            or src_msg.voice or src_msg.video_note
                            or src_msg.sticker or src_msg.animation
                        )
                        if not has_content:
                            skipped_count += 1
                            await asyncio.sleep(0.2)
                            continue

                        copied = await client.copy_message(
                            chat_id=Var.DB_CHANNEL,
                            from_chat_id=src_chat_id,
                            message_id=msg_id
                        )

                        if copied and getattr(copied, 'id', None):
                            if fwd_first_id is None or copied.id < fwd_first_id:
                                fwd_first_id = copied.id
                            if fwd_last_id is None or copied.id > fwd_last_id:
                                fwd_last_id = copied.id
                            forwarded_count += 1
                        else:
                            failed_count += 1

                        await asyncio.sleep(0.3)

                    except FloodWait as e:
                        await asyncio.sleep(e.value + 2)
                        try:
                            copied = await client.copy_message(chat_id=Var.DB_CHANNEL, from_chat_id=src_chat_id, message_id=msg_id)
                            if copied and getattr(copied, 'id', None):
                                if fwd_first_id is None or copied.id < fwd_first_id:
                                    fwd_first_id = copied.id
                                if fwd_last_id is None or copied.id > fwd_last_id:
                                    fwd_last_id = copied.id
                                forwarded_count += 1
                        except Exception as retry_err:
                            if first_error is None:
                                first_error = str(retry_err)
                            failed_count += 1
                    except Exception as msg_err:
                        if first_error is None:
                            first_error = str(msg_err)
                        failed_count += 1

                    if (msg_id - start_id + 1) % 20 == 0:
                        await status_msg.edit_text(
                            f"📤 {subject_name}: {forwarded_count} copied, {skipped_count} skipped\n"
                            f"Progress: {msg_id - start_id + 1}/{total}"
                        )

                if fwd_first_id and fwd_last_id:
                    f_link = f"https://t.me/c/{db_short}/{fwd_first_id}"
                    l_link = f"https://t.me/c/{db_short}/{fwd_last_id}"
                    result_lines.append(f"{subject_name}\nF - {f_link}\nL - {l_link}")
                    await status_msg.edit_text(
                        f"✅ {subject_name} done!\n"
                        f"Copied: {forwarded_count} | Skipped: {skipped_count} | Failed: {failed_count}\n"
                        f"F - {f_link}\nL - {l_link}\n\nProgress: {idx}/{len(subjects_data)}"
                    )
                else:
                    result_lines.append(f"{subject_name}\n❌ No messages forwarded")
                    await status_msg.edit_text(
                        f"❌ {subject_name}: nothing forwarded\n"
                        f"Skipped: {skipped_count} | Failed: {failed_count}\n"
                        f"Progress: {idx}/{len(subjects_data)}"
                    )

                await asyncio.sleep(1)

            except Exception as e:
                logging.error(f"Error forwarding {subject_name}: {e}", exc_info=True)
                result_lines.append(f"{subject_name}\n❌ Error: {str(e)}")

        final_text = "✅ Forward complete! New DB channel links:\n\n" + "\n\n".join(result_lines)
        await message.reply_text(final_text, disable_web_page_preview=True)

    except Exception as e:
        await message.reply(f"❌ Error: {str(e)}")


@StreamBot.on_message((filters.private) & (filters.document | filters.audio | filters.photo), group=3)
async def private_receive_handler(c: Client, m: Message):
    try:
        intermediate_link, caption = await create_intermediate_link(m)
        reply_markup = InlineKeyboardMarkup([[InlineKeyboardButton("GENERATE STREAM 🎬", url=intermediate_link)]])
        response_text = f"📁 <b>{caption}</b>\n\n🔗 Click the button below to generate your stream link:"
        await m.reply_text(text=response_text, reply_markup=reply_markup, disable_web_page_preview=True, quote=True)
    except Exception as e:
        await m.reply_text(f"❌ Error processing file: {str(e)}", quote=True)
        print(f"Error in private_receive_handler: {e}")


@StreamBot.on_message((filters.private) & (filters.video | filters.audio), group=4)
async def private_receive_handler_video(c: Client, m: Message):
    try:
        intermediate_link, caption = await create_intermediate_link(m)
        reply_markup = InlineKeyboardMarkup([[InlineKeyboardButton("GENERATE STREAM 🎬", url=intermediate_link)]])
        response_text = f"🎥 <b>{caption}</b>\n\n🔗 Click the button below to generate your stream link:"
        await m.reply_text(text=response_text, reply_markup=reply_markup, disable_web_page_preview=True, quote=True)
    except Exception as e:
        await m.reply_text(f"❌ Error processing file: {str(e)}", quote=True)
        print(f"Error in private_receive_handler_video: {e}")
