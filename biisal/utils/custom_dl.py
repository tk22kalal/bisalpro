import os
import math
import asyncio
import logging
import tempfile
import aiofiles
from biisal.vars import Var
from typing import Dict, Optional, Union
from biisal.bot import work_loads
from pyrogram import Client, utils, raw
from .file_properties import get_file_ids
from pyrogram.session import Session, Auth
from pyrogram.errors import AuthBytesInvalid
from biisal.server.exceptions import FIleNotFound
from pyrogram.file_id import FileId, FileType, ThumbnailSource


# ── Prefetch cache ──────────────────────────────────────────────────────────
# Keyed by Telegram media_id so the same physical file is only downloaded once
# even if it is accessed via different message IDs.
_file_cache: Dict[int, "CacheEntry"] = {}

# Maximum number of background prefetch downloads running at the same time.
# Keeps disk and memory usage bounded.
_MAX_CONCURRENT_PREFETCH = 3


class CacheEntry:
    """State for one file's background prefetch download."""

    __slots__ = ("path", "total_size", "bytes_written", "complete", "task", "error")

    def __init__(self, path: str, total_size: int) -> None:
        self.path = path
        self.total_size = total_size
        self.bytes_written: int = 0
        self.complete: bool = False
        self.task: Optional[asyncio.Task] = None
        self.error: Optional[Exception] = None


# ── ByteStreamer ────────────────────────────────────────────────────────────

class ByteStreamer:
    def __init__(self, client: Client):
        """A custom class that holds the cache of a specific client and class functions.
        attributes:
            client: the client that the cache is for.
            cached_file_ids: a dict of cached file IDs.
            cached_file_properties: a dict of cached file properties.
        
        functions:
            generate_file_properties: returns the properties for a media of a specific message contained in Tuple.
            generate_media_session: returns the media session for the DC that contains the media file.
            yield_file: yield a file from telegram servers for streaming.
            
        This is a modified version of the <https://github.com/eyaadh/megadlbot_oss/blob/master/mega/telegram/utils/custom_download.py>
        Thanks to Eyaadh <https://github.com/eyaadh>
        """
        self.clean_timer = 30 * 60
        self.client: Client = client
        self.cached_file_ids: Dict[int, FileId] = {}
        asyncio.create_task(self.clean_cache())

    async def get_file_properties(self, id: int) -> FileId:
        """
        Returns the properties of a media of a specific message in a FIleId class.
        if the properties are cached, then it'll return the cached results.
        or it'll generate the properties from the Message ID and cache them.
        """
        if id not in self.cached_file_ids:
            await self.generate_file_properties(id)
            logging.debug(f"Cached file properties for message with ID {id}")
        return self.cached_file_ids[id]
    
    async def generate_file_properties(self, id: int) -> FileId:
        """
        Generates the properties of a media file on a specific message.
        returns ths properties in a FIleId class.
        """
        file_id = await get_file_ids(self.client, Var.BIN_CHANNEL, id)
        logging.debug(f"Generated file ID and Unique ID for message with ID {id}")
        if not file_id:
            logging.debug(f"Message with ID {id} not found")
            raise FIleNotFound
        self.cached_file_ids[id] = file_id
        logging.debug(f"Cached media message with ID {id}")
        return self.cached_file_ids[id]

    async def generate_media_session(self, client: Client, file_id: FileId) -> Session:
        """
        Generates the media session for the DC that contains the media file.
        This is required for getting the bytes from Telegram servers.
        """

        media_session = client.media_sessions.get(file_id.dc_id, None)

        if media_session is None:
            if file_id.dc_id != await client.storage.dc_id():
                media_session = Session(
                    client,
                    file_id.dc_id,
                    await Auth(
                        client, file_id.dc_id, await client.storage.test_mode()
                    ).create(),
                    await client.storage.test_mode(),
                    is_media=True,
                )
                await media_session.start()

                for _ in range(6):
                    exported_auth = await client.invoke(
                        raw.functions.auth.ExportAuthorization(dc_id=file_id.dc_id)
                    )

                    try:
                        await media_session.send(
                            raw.functions.auth.ImportAuthorization(
                                id=exported_auth.id, bytes=exported_auth.bytes
                            )
                        )
                        break
                    except AuthBytesInvalid:
                        logging.debug(
                            f"Invalid authorization bytes for DC {file_id.dc_id}"
                        )
                        continue
                else:
                    await media_session.stop()
                    raise AuthBytesInvalid
            else:
                media_session = Session(
                    client,
                    file_id.dc_id,
                    await client.storage.auth_key(),
                    await client.storage.test_mode(),
                    is_media=True,
                )
                await media_session.start()
            logging.debug(f"Created media session for DC {file_id.dc_id}")
            client.media_sessions[file_id.dc_id] = media_session
        else:
            logging.debug(f"Using cached media session for DC {file_id.dc_id}")
        return media_session


    @staticmethod
    async def get_location(file_id: FileId) -> Union[raw.types.InputPhotoFileLocation,
                                                     raw.types.InputDocumentFileLocation,
                                                     raw.types.InputPeerPhotoFileLocation,]:
        """
        Returns the file location for the media file.
        """
        file_type = file_id.file_type

        if file_type == FileType.CHAT_PHOTO:
            if file_id.chat_id > 0:
                peer = raw.types.InputPeerUser(
                    user_id=file_id.chat_id, access_hash=file_id.chat_access_hash
                )
            else:
                if file_id.chat_access_hash == 0:
                    peer = raw.types.InputPeerChat(chat_id=-file_id.chat_id)
                else:
                    peer = raw.types.InputPeerChannel(
                        channel_id=utils.get_channel_id(file_id.chat_id),
                        access_hash=file_id.chat_access_hash,
                    )

            location = raw.types.InputPeerPhotoFileLocation(
                peer=peer,
                volume_id=file_id.volume_id,
                local_id=file_id.local_id,
                big=file_id.thumbnail_source == ThumbnailSource.CHAT_PHOTO_BIG,
            )
        elif file_type == FileType.PHOTO:
            location = raw.types.InputPhotoFileLocation(
                id=file_id.media_id,
                access_hash=file_id.access_hash,
                file_reference=file_id.file_reference,
                thumb_size=file_id.thumbnail_size,
            )
        else:
            location = raw.types.InputDocumentFileLocation(
                id=file_id.media_id,
                access_hash=file_id.access_hash,
                file_reference=file_id.file_reference,
                thumb_size=file_id.thumbnail_size,
            )
        return location

    # ── Prefetch helpers ────────────────────────────────────────────────────

    async def ensure_prefetch(self, file_id: FileId, index: int) -> Optional[CacheEntry]:
        """
        Start a background full-file download for *file_id* if one is not
        already running.  Returns the CacheEntry so the caller can pass it to
        yield_file; returns None when prefetching is skipped (e.g. too many
        concurrent downloads).
        """
        key = file_id.media_id
        if key in _file_cache:
            return _file_cache[key]

        # Guard against running too many parallel downloads at once.
        active = sum(
            1 for e in _file_cache.values()
            if e.task is not None and not e.complete and e.error is None
        )
        if active >= _MAX_CONCURRENT_PREFETCH:
            logging.debug("Prefetch skipped: too many concurrent downloads")
            return None

        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".tgcache")
        tmp.close()
        entry = CacheEntry(path=tmp.name, total_size=file_id.file_size)
        _file_cache[key] = entry
        entry.task = asyncio.create_task(self._run_prefetch(file_id, index, entry))
        logging.debug(f"Prefetch started for media_id={key} size={file_id.file_size}")
        return entry

    async def _run_prefetch(self, file_id: FileId, index: int, entry: CacheEntry) -> None:
        """
        Background coroutine: downloads the entire file sequentially into
        entry.path, updating entry.bytes_written after each chunk so that
        yield_file can serve already-downloaded parts from disk.
        """
        chunk_size = 1024 * 1024  # 1 MB
        current_offset = 0
        try:
            media_session = await self.generate_media_session(self.client, file_id)
            location = await self.get_location(file_id)

            async with aiofiles.open(entry.path, "wb") as f:
                while current_offset < entry.total_size:
                    try:
                        r = await asyncio.wait_for(
                            media_session.send(
                                raw.functions.upload.GetFile(
                                    location=location,
                                    offset=current_offset,
                                    limit=chunk_size,
                                )
                            ),
                            timeout=30,
                        )
                    except asyncio.TimeoutError:
                        logging.warning(
                            f"Prefetch timeout at offset {current_offset}, retrying in 2s…"
                        )
                        await asyncio.sleep(2)
                        continue

                    if not isinstance(r, raw.types.upload.File) or not r.bytes:
                        break

                    await f.write(r.bytes)
                    await f.flush()
                    entry.bytes_written += len(r.bytes)
                    current_offset += chunk_size

                    # Yield control so we don't block the event loop
                    await asyncio.sleep(0)

            entry.complete = True
            logging.debug(
                f"Prefetch complete for media_id={file_id.media_id}: "
                f"{entry.bytes_written}/{entry.total_size} bytes"
            )
        except asyncio.CancelledError:
            logging.debug(f"Prefetch cancelled for media_id={file_id.media_id}")
        except Exception as exc:
            logging.warning(f"Prefetch failed for media_id={file_id.media_id}: {exc}")
            entry.error = exc

    # ── Streaming ───────────────────────────────────────────────────────────

    async def yield_file(
        self,
        file_id: FileId,
        index: int,
        offset: int,
        first_part_cut: int,
        last_part_cut: int,
        part_count: int,
        chunk_size: int,
        entry: Optional[CacheEntry] = None,
    ) -> Union[str, None]:
        """
        Custom generator that yields the bytes of the media file.

        For each chunk it first checks whether the prefetch background task has
        already written that region to the local temp file.  If so the chunk is
        read from disk (instant).  Otherwise it falls back to fetching the chunk
        directly from Telegram — identical to the original behaviour — while the
        background download continues uninterrupted.

        Modded from <https://github.com/eyaadh/megadlbot_oss/blob/master/mega/telegram/utils/custom_download.py#L20>
        Thanks to Eyaadh <https://github.com/eyaadh>
        """
        client = self.client
        work_loads[index] += 1
        logging.debug(f"Starting to yield file with client {index}.")

        media_session = await self.generate_media_session(client, file_id)
        location = await self.get_location(file_id)

        current_part = 1
        current_offset = offset

        try:
            while True:
                chunk = None

                # ── Try the local prefetch cache first ──────────────────────
                if entry is not None:
                    cache_end = current_offset + chunk_size
                    # The chunk is fully written when bytes_written has passed
                    # the end of this chunk, OR the download is complete.
                    if entry.bytes_written >= cache_end or entry.complete:
                        try:
                            async with aiofiles.open(entry.path, "rb") as f:
                                await f.seek(current_offset)
                                chunk = await f.read(chunk_size)
                        except Exception as cache_err:
                            logging.debug(f"Cache read failed at {current_offset}: {cache_err}")
                            chunk = None

                # ── Fall back to Telegram for this chunk ────────────────────
                if not chunk:
                    r = await media_session.send(
                        raw.functions.upload.GetFile(
                            location=location,
                            offset=current_offset,
                            limit=chunk_size,
                        )
                    )
                    if not isinstance(r, raw.types.upload.File) or not r.bytes:
                        break
                    chunk = r.bytes

                # ── Apply range cuts and yield ──────────────────────────────
                if part_count == 1:
                    yield chunk[first_part_cut:last_part_cut]
                elif current_part == 1:
                    yield chunk[first_part_cut:]
                elif current_part == part_count:
                    yield chunk[:last_part_cut]
                else:
                    yield chunk

                current_part += 1
                current_offset += chunk_size

                if current_part > part_count:
                    break

        except (TimeoutError, AttributeError):
            pass
        finally:
            logging.debug(f"Finished yielding file with {current_part} parts.")
            work_loads[index] -= 1

    async def clean_cache(self) -> None:
        """
        Periodically clears the in-memory file-ID cache and removes temp files
        for prefetch downloads that have finished (successfully or with an error).
        """
        while True:
            await asyncio.sleep(self.clean_timer)
            self.cached_file_ids.clear()
            logging.debug("Cleaned the file-ID cache")

            # Remove temp files for completed / failed prefetch entries.
            done_keys = [
                k for k, e in _file_cache.items()
                if e.complete or e.error is not None
            ]
            for k in done_keys:
                entry = _file_cache.pop(k, None)
                if entry and os.path.exists(entry.path):
                    try:
                        os.unlink(entry.path)
                        logging.debug(f"Deleted prefetch temp file: {entry.path}")
                    except Exception as unlink_err:
                        logging.warning(f"Could not delete temp file {entry.path}: {unlink_err}")
