# coding=utf-8

import os
import subliminal
import helpers

from subzero import intent


def flatten_media(media, kind="series"):
    """
    iterates through media and returns the associated parts (videos)
    :param media:
    :param kind:
    :return:
    """
    parts = []
    if kind == "series":
        for season in media.seasons:
            for episode in media.seasons[season].episodes:
                ep = media.seasons[season].episodes[episode]
                for item in media.seasons[season].episodes[episode].items:
                    for part in item.parts:
                        parts.append({"video": part, "type": "episode", "title": ep.title, "series": media.title, "id": ep.id})
    else:
        for item in media.items:
            for part in item.parts:
                parts.append({"video": part, "type": "movie", "title": media.title, "id": media.id})
    return parts


IGNORE_FN = ("subzero.ignore", ".subzero.ignore", ".nosz")


def convert_media_to_parts(media, kind="series"):
    """
    returns a list of parts to be used later on; ignores folders with an existing "subzero.ignore" file
    :param media:
    :param kind:
    :return:
    """
    parts = flatten_media(media, kind=kind)
    if not Prefs["subtitles.ignore_fs"]:
        return parts

    use_parts = []
    check_ignore_paths = [".", "../"]
    if kind == "series":
        check_ignore_paths.append("../../")

    for part in parts:
        base_folder, fn = os.path.split(part["video"].file)

        ignore = False
        for rel_path in check_ignore_paths:
            fld = os.path.abspath(os.path.join(base_folder, rel_path))
            for ifn in IGNORE_FN:
                if os.path.isfile(os.path.join(fld, ifn)):
                    Log.Info(u'Ignoring "%s" because "%s" exists in "%s"', fn, ifn, fld)
                    ignore = True
                    break
            if ignore:
                break

        if not ignore:
            use_parts.append(part)
    return use_parts


def get_stream_fps(streams):
    """
    accepts a list of plex streams or a list of the plex api streams
    """
    for stream in streams:
        # video
        stream_type = getattr(stream, "type", getattr(stream, "stream_type", None))
        if stream_type == 1:
            print stream_type, dir(stream)
            return getattr(stream, "frameRate", getattr(stream, "frame_rate", "25.000"))
    return "25.000"


def get_media_item_ids(media, kind="series"):
    ids = []
    if kind == "movies":
        ids.append(media.id)
    else:
        for season in media.seasons:
            for episode in media.seasons[season].episodes:
                ids.append(media.seasons[season].episodes[episode].id)

    return ids


def scan_video(plex_video, ignore_all=False, hints=None):
    embedded_subtitles = not ignore_all and Prefs['subtitles.scan.embedded']
    external_subtitles = not ignore_all and Prefs['subtitles.scan.external']

    if ignore_all:
        Log.Debug("Force refresh intended.")

    Log.Debug("Scanning video: %s, subtitles=%s, embedded_subtitles=%s" % (plex_video.file, external_subtitles, embedded_subtitles))

    try:
        return subliminal.video.scan_video(plex_video.file, subtitles=external_subtitles, embedded_subtitles=embedded_subtitles,
                                           hints=hints or {}, video_fps=plex_video.fps)

    except ValueError:
        Log.Warn("File could not be guessed by subliminal")


def scan_parts(parts, kind="series"):
    """
    receives a list of parts containing dictionaries returned by flattenToParts
    :param parts:
    :param kind: series or movies
    :return: dictionary of subliminal.video.scan_video, key=subliminal scanned video, value=plex file part
    """
    ret = {}
    for part in parts:
        force_refresh = intent.get("force", part["id"])
        hints = helpers.get_item_hints(part["title"], kind, series=part["series"] if kind == "series" else None)
        part["video"].fps = get_stream_fps(part["video"].streams)
        scanned_video = scan_video(part["video"], ignore_all=force_refresh, hints=hints)
        if not scanned_video:
            continue

        scanned_video.id = part["id"]
        ret[scanned_video] = part["video"]
    return ret