import glob
import os
from math import floor
from pathlib import Path
from time import time

import wget
import yt_dlp as ytdl

from paimon import Config, Message, paimon, pool
from paimon.plugins.misc.upload import upload
from paimon.utils import humanbytes, time_formatter

LOGGER = paimon.getLogger(__name__)


@paimon.on_cmd(
    "ytinfo",
    about={
        "header": "Get info from ytdl",
        "description": "Get information of the link without downloading",
        "examples": "{tr}ytinfo link",
        "others": "To get info about direct links, use `{tr}head link`",
    },
)
async def ytinfo(message: Message):
    """get info from a link"""
    await message.edit("Hold on \u23f3 ..")
    _exracted = await _yt_getInfo(message.input_or_reply_str)
    if isinstance(_exracted, ytdl.utils.YoutubeDLError):
        await message.err(str(_exracted))
        return
    out = """
**Title** >>
__{title}__

**Uploader** >>
__{uploader}__

{table}
    """.format_map(
        _exracted
    )
    if _exracted["thumb"]:
        _tmp = await pool.run_in_thread(wget.download)(
            _exracted["thumb"], os.path.join(Config.DOWN_PATH, f"{time()}.jpg")
        )
        await message.reply_photo(_tmp, caption=out)
        await message.delete()
        os.remove(_tmp)
    else:
        await message.edit(out)


@paimon.on_cmd(
    "ytdl",
    about={
        "header": "Download from youtube and upload to Telegram.",
        "options": {
            "-a": "select the audio u-id",
            "-v": "select the video u-id",
            "-m": "extract the mp3 in 320kbps",
            "-s": "Download to Bot",
        },
        "examples": [
            "{tr}ytdl link",
            "{tr}ytdl -a12 -v120 link",
            "{tr}ytdl -m -s link will upload the mp3",
            "{tr}ytdl -m -s -d link will upload ",
            "the mp3 as a document",
        ],
    },
    del_pre=True,
)
async def ytDown(message: Message):
    """download from a link"""
    edited = False
    startTime = c_time = time()

    def __progress(data: dict):
        nonlocal edited, c_time
        diff = time() - c_time
        if data["status"] == "downloading" and (
            not edited or diff >= Config.EDIT_SLEEP_TIMEOUT
        ):
            c_time = time()
            edited = True
            eta = data.get("eta")
            speed = data.get("speed")
            if not (eta and speed):
                return
            out = "**Speed** >> {}/s\n**ETA** >> {}\n".format(
                humanbytes(speed), time_formatter(eta)
            )
            out += f'**File Name** >> `{data["filename"]}`\n\n'
            current = data.get("downloaded_bytes")
            total = data.get("total_bytes")
            if current and total:
                percentage = int(current) * 100 / int(total)
                out += f"Progress >> {int(percentage)}%\n"
                out += "[{}{}]".format(
                    "".join(
                        (
                            Config.FINISHED_PROGRESS_STR
                            for _ in range(floor(percentage / 5))
                        )
                    ),
                    "".join(
                        (
                            Config.UNFINISHED_PROGRESS_STR
                            for _ in range(20 - floor(percentage / 5))
                        )
                    ),
                )
            paimon.loop.create_task(message.edit(out))

    await message.edit("Hold on \u23f3 ..")
    reply = message.reply_to_message
    input_ = reply.text if reply else message.filtered_input_str
    for i in input_.split():
        if "http" in i:
            url = i
    if bool(message.flags):
        desiredFormat1 = str(message.flags.get("a", ""))
        desiredFormat2 = str(message.flags.get("v", ""))
        if "m" in message.flags:
            retcode = await _mp3Dl([url], __progress, startTime)
        elif all(k in message.flags for k in ("a", "v")):
            # 1st format must contain the video
            desiredFormat = "+".join([desiredFormat2, desiredFormat1])
            retcode = await _tubeDl([url], __progress, startTime, desiredFormat)
        elif "a" in message.flags:
            desiredFormat = desiredFormat1
            retcode = await _tubeDl([url], __progress, startTime, desiredFormat)
        elif "v" in message.flags:
            desiredFormat = desiredFormat2 + "+bestaudio"
            retcode = await _tubeDl([url], __progress, startTime, desiredFormat)
        else:
            retcode = await _tubeDl([url], __progress, startTime, None)
    else:
        retcode = await _tubeDl([url], __progress, startTime, None)
    _fpath = ""
    if retcode == 0:
        for _path in glob.glob(os.path.join(Config.DOWN_PATH, str(startTime), "*")):
            if not _path.lower().endswith((".jpg", ".png", ".webp")):
                _fpath = _path
    if not _fpath:
        return await message.err("nothing found !")
    await message.edit(
        f"**YTDL completed in {round(time() - startTime)} seconds**\n`{_fpath}`"
    )
    if "s" in message.flags:
        await message.edit(str(retcode))
    else:
        await upload(message, Path(_fpath))


@paimon.on_cmd(
    "ytdes",
    about={
        "header": "Get the video description",
        "description": "Get information of the link without downloading",
        "examples": "{tr}ytdes link",
    },
)
async def ytdes(message: Message):
    """get description from a link"""
    await message.edit("Hold on \u23f3 ..")
    description = await _yt_description(message.input_or_reply_str)
    if isinstance(description, ytdl.utils.YoutubeDLError):
        await message.err(str(description))
        return
    if description:
        out = "--Description--\n\n\t"
        out += description
    else:
        out = "No descriptions found :("
    await message.edit_or_send_as_file(out)


@pool.run_in_thread
def _yt_description(link):
    try:
        x = ytdl.YoutubeDL({"no-playlist": True, "logger": LOGGER}).extract_info(
            link, download=False
        )
    except Exception as y_e:  # pylint: disable=broad-except
        LOGGER.exception(y_e)
        return y_e
    else:
        return x.get("description", "")


@pool.run_in_thread
def _yt_getInfo(link):
    try:
        x = ytdl.YoutubeDL({"no-playlist": True, "logger": LOGGER}).extract_info(
            link, download=False
        )
        thumb = x.get("thumbnail", "")
        formats = x.get("formats", [x])
        out = "No formats found :("
        if formats:
            out = "--U-ID   |   Reso.  |   Extension--\n"
        for i in formats:
            out += (
                f"`{i.get('format_id', '')} | {i.get('format_note', None)}"
                f" | {i.get('ext', None)}`\n"
            )
    except Exception as y_e:  # pylint: disable=broad-except
        LOGGER.exception(y_e)
        return y_e
    else:
        return {
            "thumb": thumb,
            "table": out,
            "uploader": x.get("uploader_id", None),
            "title": x.get("title", None),
        }


@pool.run_in_thread
def _supported(url):
    ies = ytdl.extractor.gen_extractors()
    return any(ie.suitable(url) and ie.IE_NAME != "generic" for ie in ies)


@pool.run_in_thread
def _tubeDl(url: list, prog, starttime, uid=None):
    _opts = {
        "outtmpl": os.path.join(
            Config.DOWN_PATH, str(starttime), "%(title)s-%(format)s.%(ext)s"
        ),
        "logger": LOGGER,
        "writethumbnail": True,
        "prefer_ffmpeg": True,
        "postprocessors": [{"key": "FFmpegMetadata"}],
    }
    _quality = {"format": "bestvideo+bestaudio/best" if not uid else str(uid)}
    _opts.update(_quality)
    try:
        x = ytdl.YoutubeDL(_opts)
        x.add_progress_hook(prog)
        dloader = x.download(url)
    except Exception as y_e:  # pylint: disable=broad-except
        LOGGER.exception(y_e)
        return y_e
    else:
        return dloader


@pool.run_in_thread
def _mp3Dl(url, prog, starttime):
    _opts = {
        "outtmpl": os.path.join(Config.DOWN_PATH, str(starttime), "%(title)s.%(ext)s"),
        "logger": LOGGER,
        "writethumbnail": True,
        "prefer_ffmpeg": True,
        "format": "bestaudio/best",
        "postprocessors": [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "320",
            },
            # {'key': 'EmbedThumbnail'},  ERROR: Conversion failed!
            {"key": "FFmpegMetadata"},
        ],
    }
    try:
        x = ytdl.YoutubeDL(_opts)
        x.add_progress_hook(prog)
        dloader = x.download(url)
    except Exception as y_e:  # pylint: disable=broad-except
        LOGGER.exception(y_e)
        return y_e
    else:
        return dloader
