# coding=utf-8
import os
import sys

# just some slight modifications to support sum and iter again
import traceback

from subzero.sandbox import restore_builtins

module = sys.modules['__main__']
restore_builtins(module, {})

globals = getattr(module, "__builtins__")["globals"]
for key, value in getattr(module, "__builtins__").iteritems():
    if key != "globals":
        globals()[key] = value

import logger

sys.modules["logger"] = logger

import subliminal
import subliminal_patch
import support

import interface
sys.modules["interface"] = interface

from subzero.constants import OS_PLEX_USERAGENT, PERSONAL_MEDIA_IDENTIFIER
from subzero import intent
from interface.menu import *
from support.plex_media import convert_media_to_parts, get_media_item_ids, scan_parts
from support import helpers
from support.subtitlehelpers import get_subtitles_from_metadata, force_utf8
from support.storage import store_subtitle_info, whack_missing_parts
from support.config import config


def Start():
    HTTP.CacheTime = 0
    HTTP.Headers['User-agent'] = OS_PLEX_USERAGENT

    # configured cache to be in memory as per https://github.com/Diaoul/subliminal/issues/303
    subliminal.region.configure('dogpile.cache.memory')

    # init defaults; perhaps not the best idea to use ValidatePrefs here, but we'll see
    ValidatePrefs()
    Log.Debug(config.full_version)

    if not config.plex_api_working:
        Log.Error(lib_unaccessible_error)
        return

    scheduler.run()


def init_subliminal_patches():
    # configure custom subtitle destination folders for scanning pre-existing subs
    dest_folder = config.subtitle_destination_folder
    subliminal_patch.patch_video.CUSTOM_PATHS = [dest_folder] if dest_folder else []
    subliminal_patch.patch_provider_pool.DOWNLOAD_TRIES = int(Prefs['subtitles.try_downloads'])
    subliminal_patch.patch_providers.addic7ed.USE_BOOST = bool(Prefs['provider.addic7ed.boost'])


def download_best_subtitles(video_part_map, min_score=0):
    hearing_impaired = Prefs['subtitles.search.hearingImpaired']
    languages = config.lang_list
    if not languages:
        return

    missing_languages = False
    for video, part in video_part_map.iteritems():
        if not Prefs['subtitles.save.filesystem']:
            # scan for existing metadata subtitles
            meta_subs = get_subtitles_from_metadata(part)
            for language, subList in meta_subs.iteritems():
                if subList:
                    video.subtitle_languages.add(language)
                    Log.Debug("Found metadata subtitle %s for %s", language, video)

        if not (languages - video.subtitle_languages):
            Log.Debug('All languages %r exist for %s', languages, video)
            continue
        missing_languages = True
        break

    if missing_languages:
        Log.Debug("Download best subtitles using settings: min_score: %s, hearing_impaired: %s" % (min_score, hearing_impaired))

        return subliminal.api.download_best_subtitles(video_part_map.keys(), languages, min_score, hearing_impaired, providers=config.providers,
                                                      provider_configs=config.provider_settings)
    Log.Debug("All languages for all requested videos exist. Doing nothing.")


def save_subtitles(videos, subtitles):
    if Prefs['subtitles.save.filesystem']:
        Log.Debug("Using filesystem as subtitle storage")
        save_subtitles_to_file(subtitles)
        storage = "filesystem"
    else:
        Log.Debug("Using metadata as subtitle storage")
        save_subtitles_to_metadata(videos, subtitles)
        storage = "metadata"

    store_subtitle_info(videos, subtitles, storage)


def save_subtitles_to_file(subtitles):
    fld_custom = Prefs["subtitles.save.subFolder.Custom"].strip() if bool(Prefs["subtitles.save.subFolder.Custom"]) else None

    for video, video_subtitles in subtitles.items():
        if not video_subtitles:
            continue

        fld = None
        if fld_custom or Prefs["subtitles.save.subFolder"] != "current folder":
            # specific subFolder requested, create it if it doesn't exist
            fld_base = os.path.split(video.name)[0]
            if fld_custom:
                if fld_custom.startswith("/"):
                    # absolute folder
                    fld = fld_custom
                else:
                    fld = os.path.join(fld_base, fld_custom)
            else:
                fld = os.path.join(fld_base, Prefs["subtitles.save.subFolder"])
            if not os.path.exists(fld):
                os.makedirs(fld)
        subliminal.api.save_subtitles(video, video_subtitles, directory=fld, single=Prefs['subtitles.only_one'],
                                      encode_with=force_utf8 if Prefs['subtitles.enforce_encoding'] else None)


def save_subtitles_to_metadata(videos, subtitles):
    for video, video_subtitles in subtitles.items():
        mediaPart = videos[video]
        for subtitle in video_subtitles:
            content = force_utf8(subtitle.text) if Prefs['subtitles.enforce_encoding'] else subtitle.content
            mediaPart.subtitles[Locale.Language.Match(subtitle.language.alpha2)][subtitle.page_link] = Proxy.Media(content, ext="srt")


def update_local_media(metadata, media, media_type="movies"):
    # Look for subtitles
    if media_type == "movies":
        for item in media.items:
            for part in item.parts:
                support.localmedia.find_subtitles(part)
        return

    # Look for subtitles for each episode.
    for s in media.seasons:
        # If we've got a date based season, ignore it for now, otherwise it'll collide with S/E folders/XML and PMS
        # prefers date-based (why?)
        if int(s) < 1900 or metadata.guid.startswith(PERSONAL_MEDIA_IDENTIFIER):
            for e in media.seasons[s].episodes:
                for i in media.seasons[s].episodes[e].items:

                    # Look for subtitles.
                    for part in i.parts:
                        support.localmedia.find_subtitles(part)
        else:
            pass


class SubZeroAgent(object):
    agent_type = None
    agent_type_verbose = None
    languages = [Locale.Language.English]
    primary_provider = False
    score_prefs_key = None

    def __init__(self, *args, **kwargs):
        super(SubZeroAgent, self).__init__(*args, **kwargs)
        self.agent_type = "movies" if isinstance(self, Agent.Movies) else "series"
        self.name = "Sub-Zero Subtitles (%s, %s)" % (self.agent_type_verbose, config.get_version())

    def search(self, results, media, lang):
        Log.Debug("Sub-Zero %s, %s search" % (config.version, self.agent_type))
        results.Append(MetadataSearchResult(id='null', score=100))

    def update(self, metadata, media, lang):
        Log.Debug("Sub-Zero %s, %s update called" % (config.version, self.agent_type))

        if not media:
            Log.Error("Called with empty media, something is really wrong with your setup!")
            return

        set_refresh_menu_state(media, media_type=self.agent_type)

        item_ids = []
        try:
            init_subliminal_patches()
            parts = convert_media_to_parts(media, kind=self.agent_type)
            use_score = Prefs[self.score_prefs_key]
            scanned_parts = scan_parts(parts, kind=self.agent_type)
            subtitles = download_best_subtitles(scanned_parts, min_score=int(use_score))
            item_ids = get_media_item_ids(media, kind=self.agent_type)

            whack_missing_parts(scanned_parts)

            if subtitles:
                # fixme: doesn't get triggered necessarily
                save_subtitles(scanned_parts, subtitles)

            update_local_media(metadata, media, media_type=self.agent_type)

        finally:
            # update the menu state
            set_refresh_menu_state(None)

            # notify any running tasks about our finished update
            for item_id in item_ids:
                scheduler.signal("updated_metadata", item_id)

                # resolve existing intent for that id
                intent.resolve("force", item_id)


class SubZeroSubtitlesAgentMovies(SubZeroAgent, Agent.Movies):
    contributes_to = ['com.plexapp.agents.imdb', 'com.plexapp.agents.xbmcnfo', 'com.plexapp.agents.themoviedb', 'com.plexapp.agents.hama']
    score_prefs_key = "subtitles.search.minimumMovieScore"
    agent_type_verbose = "Movies"


class SubZeroSubtitlesAgentTvShows(SubZeroAgent, Agent.TV_Shows):
    contributes_to = ['com.plexapp.agents.thetvdb', 'com.plexapp.agents.thetvdbdvdorder', 'com.plexapp.agents.xbmcnfotv',
                      'com.plexapp.agents.hama']
    score_prefs_key = "subtitles.search.minimumTVScore"
    agent_type_verbose = "TV"
