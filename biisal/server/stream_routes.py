import re
import time
import math
import logging
import secrets
import mimetypes
import asyncio
from datetime import datetime, timezone
from aiohttp import web
from aiohttp.http_exceptions import BadStatusLine
from pyrogram.errors import FloodWait
from pyrogram.enums import ParseMode
from urllib.parse import quote_plus
from biisal.bot import multi_clients, work_loads, StreamBot
from biisal.server.exceptions import FIleNotFound, InvalidHash
from biisal import StartTime, __version__
from ..utils.time_format import get_readable_time
from ..utils.custom_dl import ByteStreamer
from biisal.utils.render_template import render_page
from biisal.utils.database import Database
from biisal.utils.file_properties import get_name, get_hash
from biisal.utils.human_readable import humanbytes
from biisal.vars import Var

stream_log = logging.getLogger("stream.routes")

routes = web.RouteTableDef()

db = Database(Var.DATABASE_URL, Var.name)


async def render_prepare_page(temp_data):
    try:
        with open("biisal-file-stream-pro/biisal/template/prepare.html") as f:
            template_content = f.read()
    except FileNotFoundError:
        try:
            with open("biisal/template/prepare.html") as f:
                template_content = f.read()
        except FileNotFoundError:
            return "<html><body><h1>Error: prepare.html template not found</h1></body></html>"

    file_size = humanbytes(temp_data.get('file_size', 0))
    file_name = temp_data.get('file_name', 'Unknown File')
    caption = temp_data.get('caption', file_name)
    mime_type = temp_data.get('mime_type', 'application/octet-stream')
    tag = mime_type.split("/")[0].strip() if mime_type else 'file'

    template_content = template_content.replace("{{file_name}}", file_name)
    template_content = template_content.replace("{{caption}}", caption)
    template_content = template_content.replace("{{file_size}}", file_size)
    template_content = template_content.replace("{{token}}", temp_data['token'])
    template_content = template_content.replace("{{tag}}", tag)

    return template_content


@routes.get("/favicon.ico")
async def favicon_handler(_):
    return web.Response(status=204)


@routes.get("/robots.txt")
async def robots_handler(_):
    try:
        with open("robots.txt", "r") as f:
            content = f.read()
        return web.Response(text=content, content_type="text/plain")
    except FileNotFoundError:
        return web.Response(
            text="User-agent: *\nAllow: /\n",
            content_type="text/plain"
        )


@routes.get("/", allow_head=True)
async def root_route_handler(_):
    telegram_bot = "Not connected"
    if hasattr(StreamBot, 'username') and StreamBot.username:
        telegram_bot = "@" + StreamBot.username
    return web.json_response(
        {
            "server_status": "running",
            "uptime": get_readable_time(int(time.time() - StartTime)),
            "telegram_bot": telegram_bot,
            "connected_bots": len(multi_clients),
            "loads": dict(
                ("bot" + str(c + 1), l)
                for c, (_, l) in enumerate(
                    sorted(work_loads.items(), key=lambda x: x[1], reverse=True)
                )
            ),
            "version": __version__,
        }
    )


@routes.get(r"/prepare/{token}", allow_head=True)
async def prepare_stream_handler(request: web.Request):
    try:
        token = request.match_info["token"]
        serve_domain = Var.SERVE_DOMAIN if Var.SERVE_DOMAIN in ('web', 'webx') else None
        temp_data = await db.get_temp_file(token, serve_domain=serve_domain)
        if not temp_data:
            return web.Response(text="Link expired or not found", status=404)
        return web.Response(text=await render_prepare_page(temp_data), content_type='text/html')
    except Exception as e:
        logging.error(f"Error in prepare_stream_handler: {e}")
        return web.Response(text="Error loading page", status=500)


@routes.get(r"/api/generate/{token}")
async def generate_stream_handler(request: web.Request):
    try:
        token = request.match_info["token"]
        player = request.rel_url.query.get("player", "plyr")
        serve_domain = Var.SERVE_DOMAIN if Var.SERVE_DOMAIN in ('web', 'webx') else None
        temp_data = await db.get_temp_file(token, serve_domain=serve_domain)
        if not temp_data:
            return web.json_response(
                {"success": False, "error": "Link expired or not found"},
                status=404,
                content_type='application/json'
            )

        client = StreamBot
        original_msg = await client.get_messages(temp_data['from_chat_id'], temp_data['message_id'])
        if not original_msg:
            return web.json_response(
                {"success": False, "error": "Original message not found"},
                status=404,
                content_type='application/json'
            )

        max_retries = 3
        log_msg = None
        for attempt in range(max_retries):
            try:
                log_msg = await original_msg.copy(
                    chat_id=Var.BIN_CHANNEL,
                    caption=temp_data['caption'][:1024],
                    parse_mode=ParseMode.HTML
                )
                break
            except FloodWait as e:
                if attempt < max_retries - 1:
                    await asyncio.sleep(e.value)
                else:
                    return web.json_response(
                        {"success": False, "error": "Server is busy. Please try again in a few seconds."},
                        status=429,
                        content_type='application/json'
                    )
            except Exception as copy_error:
                logging.error(f"Error copying message (attempt {attempt + 1}): {copy_error}")
                if attempt < max_retries - 1:
                    await asyncio.sleep(2)
                else:
                    return web.json_response(
                        {"success": False, "error": "Failed to process file. Please try again."},
                        status=500,
                        content_type='application/json'
                    )

        if not log_msg:
            return web.json_response(
                {"success": False, "error": "Failed to process file after retries"},
                status=500,
                content_type='application/json'
            )

        file_name = get_name(log_msg) or temp_data['file_name'] or "file"
        if isinstance(file_name, bytes):
            file_name = file_name.decode('utf-8', errors='ignore')
        file_name = re.sub(r"[\r\n\t\x00-\x1f\x7f]", "", str(file_name)).strip() or "file"
        file_hash = get_hash(log_msg)

        request_host = request.host
        forwarded_proto = request.headers.get('X-Forwarded-Proto', '').lower()
        if forwarded_proto in ('https', 'http'):
            scheme = forwarded_proto
        elif Var.HAS_SSL:
            scheme = 'https'
        else:
            scheme = request.scheme if request.scheme else 'http'
        base_url = f"{scheme}://{request_host}/"

        stream_link = f"{base_url}watch/{log_msg.id}/{quote_plus(file_name)}?hash={file_hash}&player={player}"

        response_data = {
            "success": True,
            "stream_url": stream_link,
            "file_name": file_name
        }
        if temp_data.get('thumbnail_url'):
            response_data['thumbnail_url'] = temp_data['thumbnail_url']

        return web.json_response(response_data, content_type='application/json')

    except Exception as e:
        logging.error(f"Error in generate_stream_handler: {e}", exc_info=True)
        return web.json_response(
            {"success": False, "error": "Server error. Please try again later."},
            status=500,
            content_type='application/json'
        )


@routes.get(r"/api/download/{token}")
async def generate_download_handler(request: web.Request):
    try:
        token = request.match_info["token"]
        serve_domain = Var.SERVE_DOMAIN if Var.SERVE_DOMAIN in ('web', 'webx') else None
        temp_data = await db.get_temp_file(token, serve_domain=serve_domain)
        if not temp_data:
            return web.json_response(
                {"success": False, "error": "Link expired or not found"},
                status=404,
                content_type='application/json'
            )

        client = StreamBot
        original_msg = await client.get_messages(temp_data['from_chat_id'], temp_data['message_id'])
        if not original_msg:
            return web.json_response(
                {"success": False, "error": "Original message not found"},
                status=404,
                content_type='application/json'
            )

        max_retries = 3
        log_msg = None
        for attempt in range(max_retries):
            try:
                log_msg = await original_msg.copy(
                    chat_id=Var.BIN_CHANNEL,
                    caption=temp_data['caption'][:1024],
                    parse_mode=ParseMode.HTML
                )
                break
            except FloodWait as e:
                if attempt < max_retries - 1:
                    await asyncio.sleep(e.value)
                else:
                    return web.json_response(
                        {"success": False, "error": "Server is busy. Please try again in a few seconds."},
                        status=429,
                        content_type='application/json'
                    )
            except Exception as copy_error:
                logging.error(f"Error copying message (attempt {attempt + 1}): {copy_error}")
                if attempt < max_retries - 1:
                    await asyncio.sleep(2)
                else:
                    return web.json_response(
                        {"success": False, "error": "Failed to process file. Please try again."},
                        status=500,
                        content_type='application/json'
                    )

        if not log_msg:
            return web.json_response(
                {"success": False, "error": "Failed to process file after retries"},
                status=500,
                content_type='application/json'
            )

        file_name = get_name(log_msg) or temp_data['file_name'] or "file"
        if isinstance(file_name, bytes):
            file_name = file_name.decode('utf-8', errors='ignore')
        file_name = re.sub(r"[\r\n\t\x00-\x1f\x7f]", "", str(file_name)).strip() or "file"
        file_hash = get_hash(log_msg)

        request_host = request.host
        forwarded_proto = request.headers.get('X-Forwarded-Proto', '').lower()
        if forwarded_proto in ('https', 'http'):
            scheme = forwarded_proto
        elif Var.HAS_SSL:
            scheme = 'https'
        else:
            scheme = request.scheme if request.scheme else 'http'
        base_url = f"{scheme}://{request_host}/"

        download_link = f"{base_url}{log_msg.id}/{quote_plus(file_name)}?hash={file_hash}&download=1"

        response_data = {
            "success": True,
            "download_url": download_link,
            "file_name": file_name
        }
        if temp_data.get('thumbnail_url'):
            response_data['thumbnail_url'] = temp_data['thumbnail_url']

        return web.json_response(response_data, content_type='application/json')

    except Exception as e:
        logging.error(f"Error in generate_download_handler: {e}", exc_info=True)
        return web.json_response(
            {"success": False, "error": "Server error. Please try again later."},
            status=500,
            content_type='application/json'
        )


@routes.get(r"/watch/{path:.+}", allow_head=True)
async def stream_handler(request: web.Request):
    try:
        path = request.match_info["path"]
        match = re.search(r"^([a-zA-Z0-9_-]{6})(\d+)$", path)
        if match:
            secure_hash = match.group(1)
            id = int(match.group(2))
        else:
            id = int(re.search(r"(\d+)(?:\/\S+)?", path).group(1))
            secure_hash = request.rel_url.query.get("hash")
        player = request.rel_url.query.get("player")
        return web.Response(text=await render_page(id, secure_hash, player=player), content_type='text/html')
    except InvalidHash as e:
        raise web.HTTPForbidden(text=e.message)
    except FIleNotFound as e:
        raise web.HTTPNotFound(text=e.message)
    except (AttributeError, BadStatusLine, ConnectionResetError):
        pass
    except Exception as e:
        logging.critical(e.with_traceback(None))
        raise web.HTTPInternalServerError(text=str(e))


@routes.get(r"/{path:.+}", allow_head=True)
async def path_handler(request: web.Request):
    try:
        path = request.match_info["path"]
        match = re.search(r"^([a-zA-Z0-9_-]{6})(\d+)$", path)
        if match:
            secure_hash = match.group(1)
            id = int(match.group(2))
        else:
            id = int(re.search(r"(\d+)(?:\/\S+)?", path).group(1))
            secure_hash = request.rel_url.query.get("hash")
        return await media_streamer(request, id, secure_hash)
    except InvalidHash as e:
        raise web.HTTPForbidden(text=e.message)
    except FIleNotFound as e:
        raise web.HTTPNotFound(text=e.message)
    except (AttributeError, BadStatusLine, ConnectionResetError):
        pass
    except Exception as e:
        logging.critical(e.with_traceback(None))
        raise web.HTTPInternalServerError(text=str(e))


class_cache = {}

_client_home_dcs: dict = {}


async def _home_dc(index: int) -> int:
    if index not in _client_home_dcs:
        _client_home_dcs[index] = await multi_clients[index].storage.dc_id()
    return _client_home_dcs[index]


async def media_streamer(request: web.Request, id: int, secure_hash: str):
    range_header = request.headers.get("Range", 0)

    index = min(work_loads, key=work_loads.get)
    faster_client = multi_clients[index]

    if Var.MULTI_CLIENT:
        logging.info(f"Client {index} is now serving {request.remote}")

    if faster_client in class_cache:
        tg_connect = class_cache[faster_client]
    else:
        tg_connect = ByteStreamer(faster_client)
        class_cache[faster_client] = tg_connect

    file_id = await tg_connect.get_file_properties(id)

    if file_id.unique_id[:6] != secure_hash:
        raise InvalidHash

    file_size = file_id.file_size

    if range_header:
        from_bytes, until_bytes = range_header.replace("bytes=", "").split("-")
        from_bytes = int(from_bytes)
        until_bytes = int(until_bytes) if until_bytes else file_size - 1
    else:
        from_bytes = request.http_range.start or 0
        until_bytes = (request.http_range.stop or file_size) - 1

    if (until_bytes > file_size) or (from_bytes < 0) or (until_bytes < from_bytes):
        return web.Response(
            status=416,
            body="416: Range not satisfiable",
            headers={"Content-Range": f"bytes */{file_size}"},
        )

    chunk_size = 1024 * 1024
    until_bytes = min(until_bytes, file_size - 1)

    offset = from_bytes - (from_bytes % chunk_size)
    first_part_cut = from_bytes - offset
    last_part_cut = until_bytes % chunk_size + 1

    req_length = until_bytes - from_bytes + 1
    part_count = math.ceil(until_bytes / chunk_size) - math.floor(offset / chunk_size)
    body = tg_connect.yield_file(
        file_id, index, offset, first_part_cut, last_part_cut, part_count, chunk_size
    )

    mime_type = file_id.mime_type
    file_name = file_id.file_name
    disposition = "attachment"

    # Sanitize filename — strip newlines/carriage-returns and other control
    # characters that would make aiohttp reject the Content-Disposition header.
    if file_name:
        file_name = re.sub(r"[\r\n\t\x00-\x1f\x7f]", "", str(file_name)).strip()
        if not file_name:
            file_name = None

    if mime_type:
        if not file_name:
            try:
                file_name = f"{secrets.token_hex(2)}.{mime_type.split('/')[1]}"
            except (IndexError, AttributeError):
                file_name = f"{secrets.token_hex(2)}.unknown"
    else:
        if file_name:
            mime_type = mimetypes.guess_type(file_id.file_name)
        else:
            mime_type = "application/octet-stream"
            file_name = f"{secrets.token_hex(2)}.unknown"

    return web.Response(
        status=206 if range_header else 200,
        body=body,
        headers={
            "Content-Type": f"{mime_type}",
            "Content-Range": f"bytes {from_bytes}-{until_bytes}/{file_size}",
            "Content-Length": str(req_length),
            "Content-Disposition": f'{disposition}; filename="{file_name}"',
            "Accept-Ranges": "bytes",
        },
    )
