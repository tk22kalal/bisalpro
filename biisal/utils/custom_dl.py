import math
import asyncio
import logging
from biisal.vars import Var
from typing import Dict, Union
from biisal.bot import work_loads
from pyrogram import Client, utils, raw
from .file_properties import get_file_ids
from pyrogram.session import Session, Auth
from pyrogram.errors import AuthBytesInvalid
from biisal.server.exceptions import FIleNotFound
from pyrogram.file_id import FileId, FileType, ThumbnailSource

class ByteStreamer:
    def __init__(self, client: Client):
        """
        A custom class to stream files from Telegram with prefetching.
        """
        self.clean_timer = 30 * 60
        self.client: Client = client
        self.cached_file_ids: Dict[int, FileId] = {}
        asyncio.create_task(self.clean_cache())

    async def get_file_properties(self, id: int) -> FileId:
        if id not in self.cached_file_ids:
            await self.generate_file_properties(id)
        return self.cached_file_ids[id]
    
    async def generate_file_properties(self, id: int) -> FileId:
        file_id = await get_file_ids(self.client, Var.BIN_CHANNEL, id)
        if not file_id:
            raise FIleNotFound
        self.cached_file_ids[id] = file_id
        return self.cached_file_ids[id]

    async def generate_media_session(self, client: Client, file_id: FileId) -> Session:
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
            client.media_sessions[file_id.dc_id] = media_session
        return media_session

    @staticmethod
    async def get_location(file_id: FileId):
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

    async def yield_file(
        self,
        file_id: FileId,
        index: int,
        offset: int,
        first_part_cut: int,
        last_part_cut: int,
        part_count: int,
        chunk_size: int,
    ):
        """
        Optimized yield_file with background prefetching to stop buffering.
        """
        client = self.client
        work_loads[index] += 1
        
        # Maxsize 3 means we keep 3 chunks (3MB) ready in the background buffer.
        # This stops the 'gap' between requesting chunks from Telegram.
        queue = asyncio.Queue(maxsize=3)
        
        # This task runs in the background to fetch data
        async def producer():
            current_offset = offset
            try:
                media_session = await self.generate_media_session(client, file_id)
                location = await self.get_location(file_id)
                
                for _ in range(part_count):
                    try:
                        r = await media_session.send(
                            raw.functions.upload.GetFile(
                                location=location, offset=current_offset, limit=chunk_size
                            ),
                        )
                        if isinstance(r, raw.types.upload.File):
                            await queue.put(r.bytes)
                        else:
                            await queue.put(None)
                            break
                        current_offset += chunk_size
                    except Exception as e:
                        logging.error(f"Producer error: {e}")
                        await queue.put(None)
                        break
                # Signal completion
                await queue.put(None)
            except Exception as e:
                logging.error(f"Media Session error: {e}")
                await queue.put(None)

        # Start the background downloader
        producer_task = asyncio.create_task(producer())

        try:
            for current_part in range(1, part_count + 1):
                chunk = await queue.get()
                
                if chunk is None:
                    break

                if part_count == 1:
                    yield chunk[first_part_cut:last_part_cut]
                elif current_part == 1:
                    yield chunk[first_part_cut:]
                elif current_part == part_count:
                    yield chunk[:last_part_cut]
                else:
                    yield chunk
                
                queue.task_done()
                
        except Exception as e:
            logging.error(f"Yield error: {e}")
        finally:
            # Cleanup
            producer_task.cancel()
            work_loads[index] -= 1
            logging.debug(f"Finished yielding file with client {index}.")

    async def clean_cache(self) -> None:
        while True:
            await asyncio.sleep(self.clean_timer)
            self.cached_file_ids.clear()
            logging.debug("Cleaned the cache")
