from biisal.vars import Var
from biisal.bot import StreamBot
from biisal.utils.human_readable import humanbytes
from biisal.utils.file_properties import get_file_ids
from biisal.server.exceptions import InvalidHash
import urllib.parse
import logging
import jinja2


async def render_page(id, secure_hash, src=None, player=None):
    file_data = await get_file_ids(StreamBot, int(Var.BIN_CHANNEL), int(id))

    if file_data.unique_id[:6] != secure_hash:
        logging.debug(f"link hash: {secure_hash} - {file_data.unique_id[:6]}")
        logging.debug(f"Invalid hash for message ID {id}")
        raise InvalidHash

    src = urllib.parse.urljoin(
        Var.URL,
        f"{id}/{urllib.parse.quote_plus(file_data.file_name)}?hash={secure_hash}",
    )

    mime_type = file_data.mime_type or ""
    tag = mime_type.split("/")[0].strip() or "video"
    file_size = humanbytes(file_data.file_size)
    file_name = (file_data.file_name or "").replace("_", " ")

    if tag in ("video", "audio"):
        # Pick player template
        if player == "videojs":
            template_file = "biisal/template/req_videojs.html"
        else:
            template_file = "biisal/template/req.html"
    else:
        template_file = "biisal/template/dl.html"

    with open(template_file) as f:
        template = jinja2.Template(f.read())

    return template.render(
        file_name=file_name,
        file_url=src,
        file_size=file_size,
        file_unique_id=file_data.unique_id,
        tag=tag,
        player=player or "plyr",
    )
from biisal.vars import Var
from biisal.bot import StreamBot
from biisal.utils.human_readable import humanbytes
from biisal.utils.file_properties import get_file_ids
from biisal.server.exceptions import InvalidHash
import urllib.parse
import logging
import jinja2


async def render_page(id, secure_hash, src=None, player=None):
    file_data = await get_file_ids(StreamBot, int(Var.BIN_CHANNEL), int(id))

    if file_data.unique_id[:6] != secure_hash:
        logging.debug(f"link hash: {secure_hash} - {file_data.unique_id[:6]}")
        logging.debug(f"Invalid hash for message ID {id}")
        raise InvalidHash

    src = urllib.parse.urljoin(
        Var.URL,
        f"{id}/{urllib.parse.quote_plus(file_data.file_name)}?hash={secure_hash}",
    )

    mime_type = file_data.mime_type or ""
    tag = mime_type.split("/")[0].strip() or "video"
    file_size = humanbytes(file_data.file_size)
    file_name = (file_data.file_name or "").replace("_", " ")

    if tag in ("video", "audio"):
        # Pick player template
        if player == "videojs":
            template_file = "biisal/template/req_videojs.html"
        else:
            template_file = "biisal/template/req.html"
    else:
        template_file = "biisal/template/dl.html"

    with open(template_file) as f:
        template = jinja2.Template(f.read())

    return template.render(
        file_name=file_name,
        file_url=src,
        file_size=file_size,
        file_unique_id=file_data.unique_id,
        tag=tag,
        player=player or "plyr",
    )
from biisal.vars import Var
from biisal.bot import StreamBot
from biisal.utils.human_readable import humanbytes
from biisal.utils.file_properties import get_file_ids
from biisal.server.exceptions import InvalidHash
import urllib.parse
import logging
import jinja2


async def render_page(id, secure_hash, src=None, player=None):
    file_data = await get_file_ids(StreamBot, int(Var.BIN_CHANNEL), int(id))

    if file_data.unique_id[:6] != secure_hash:
        logging.debug(f"link hash: {secure_hash} - {file_data.unique_id[:6]}")
        logging.debug(f"Invalid hash for message ID {id}")
        raise InvalidHash

    src = urllib.parse.urljoin(
        Var.URL,
        f"{id}/{urllib.parse.quote_plus(file_data.file_name)}?hash={secure_hash}",
    )

    mime_type = file_data.mime_type or ""
    tag = mime_type.split("/")[0].strip() or "video"
    file_size = humanbytes(file_data.file_size)
    file_name = (file_data.file_name or "").replace("_", " ")

    if tag in ("video", "audio"):
        # Pick player template
        if player == "videojs":
            template_file = "biisal/template/req_videojs.html"
        else:
            template_file = "biisal/template/req.html"
    else:
        template_file = "biisal/template/dl.html"

    with open(template_file) as f:
        template = jinja2.Template(f.read())

    return template.render(
        file_name=file_name,
        file_url=src,
        file_size=file_size,
        file_unique_id=file_data.unique_id,
        tag=tag,
        player=player or "plyr",
    )
from biisal.vars import Var
from biisal.bot import StreamBot
from biisal.utils.human_readable import humanbytes
from biisal.utils.file_properties import get_file_ids
from biisal.server.exceptions import InvalidHash
import urllib.parse
import logging
import jinja2


async def render_page(id, secure_hash, src=None, player=None):
    file_data = await get_file_ids(StreamBot, int(Var.BIN_CHANNEL), int(id))

    if file_data.unique_id[:6] != secure_hash:
        logging.debug(f"link hash: {secure_hash} - {file_data.unique_id[:6]}")
        logging.debug(f"Invalid hash for message ID {id}")
        raise InvalidHash

    src = urllib.parse.urljoin(
        Var.URL,
        f"{id}/{urllib.parse.quote_plus(file_data.file_name)}?hash={secure_hash}",
    )

    mime_type = file_data.mime_type or ""
    tag = mime_type.split("/")[0].strip() or "video"
    file_size = humanbytes(file_data.file_size)
    file_name = (file_data.file_name or "").replace("_", " ")

    if tag in ("video", "audio"):
        # Pick player template
        if player == "videojs":
            template_file = "biisal/template/req_videojs.html"
        else:
            template_file = "biisal/template/req.html"
    else:
        template_file = "biisal/template/dl.html"

    with open(template_file) as f:
        template = jinja2.Template(f.read())

    return template.render(
        file_name=file_name,
        file_url=src,
        file_size=file_size,
        file_unique_id=file_data.unique_id,
        tag=tag,
        player=player or "plyr",
    )
