import threading
import gi
import re
import html

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from difflib import SequenceMatcher
from gi.repository import Adw, Gtk, Gdk, GLib, Pango, GdkPixbuf, GObject
from curl_cffi import requests

from . import flaresolverr
from .metadata.anilist import AniListClient
from .backends.animepahe import AnimepaheBackend, LIBRARY_DIR
from .synopsis_fading import SynopsisFading

COVER_CACHE = Path.home() / ".cache" / "ani-dm" / "covers"
COVER_W, COVER_H = 140, 199
COVER_MARGIN = 6


def _norm(s):
    return re.sub(r"[^a-z0-9]", "", s.lower())


def _similar(a, b):
    return SequenceMatcher(None, _norm(a), _norm(b)).ratio()


class AniDmWindow(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="Ani-dm")
        self.set_default_size(1000, 700)
        self._cover_size = (COVER_W, COVER_H)
        self.connect("notify::default-width", self._on_resize)

        COVER_CACHE.mkdir(parents=True, exist_ok=True)

        self.anilist = AniListClient()
        self.backend = AnimepaheBackend()
        threading.Thread(target=self.backend.warm_clearance, daemon=True).start()
        self.cover_pool = ThreadPoolExecutor(max_workers=4)

        self.search_entry = Gtk.SearchEntry()
        self.search_entry.set_placeholder_text("Search anime…")
        self.search_entry.connect("activate", self._on_search)

        self.spinner = Gtk.Spinner()

        self.results = Gtk.FlowBox()
        self.results.set_selection_mode(Gtk.SelectionMode.NONE)
        self.results.set_valign(Gtk.Align.START)
        self.results.set_homogeneous(True)
        self.results.set_column_spacing(12)
        self.results.set_row_spacing(12)
        self.results.set_min_children_per_line(2)
        self.results.set_max_children_per_line(999)
        self.results.connect("child-activated", self._on_child_activated)

        scroller = Gtk.ScrolledWindow()
        scroller.set_child(self.results)
        scroller.set_vexpand(True)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        box.set_margin_top(12)
        box.set_margin_bottom(12)
        box.set_margin_start(12)
        box.set_margin_end(12)
        box.append(self.search_entry)
        box.append(self.spinner)
        box.append(scroller)

        search_toolbar = Adw.ToolbarView()
        search_toolbar.add_top_bar(Adw.HeaderBar())
        search_toolbar.set_content(box)

        search_page = Adw.NavigationPage(child=search_toolbar, title="Search")

        self.navview = Adw.NavigationView()
        self.navview.add(search_page)

        self.toasts = Adw.ToastOverlay()
        self.toasts.set_child(self.navview)
        self.set_content(self.toasts)

    def _compute_cover_size(self):
        container_width = self.get_width() or self.get_default_size()[0]
        tile = COVER_W + COVER_MARGIN * 2
        nb = max(2, container_width // tile)
        width = (container_width // nb) - COVER_MARGIN * 2
        height = (width * COVER_H) // COVER_W
        self._cover_size = (int(width), int(height))

    def _on_child_activated(self, _flowbox, child):
        info = child.get_child().info
        self.navview.push(EpisodePage(self, info))

    def _on_search(self, entry):
        query = entry.get_text().strip()
        if not query:
            return
        self._set_loading(True)
        threading.Thread(target=self._do_search, args=(query,), daemon=True).start()

    def _do_search(self, query):
        try:
            results = self.anilist.search(query)
        except Exception as e:
            GLib.idle_add(self._on_search_error, str(e))
            return
        GLib.idle_add(self._on_search_done, results)

    def _on_search_done(self, results):
        self._clear_results()
        self._compute_cover_size()
        for info in results:
            card, picture = self._make_card(info)
            self.results.insert(card, -1)
            self.cover_pool.submit(self._load_cover, info, picture)
        self._set_loading(False)

    def _on_search_error(self, message):
        self._set_loading(False)
        self.toasts.add_toast(Adw.Toast(title=f"Search failed: {message}"))

    def _make_card(self, info):
        w, h = self._cover_size
        picture = Gtk.Picture()
        picture.set_size_request(w, h)

        cover_wrap = Gtk.Box()
        cover_wrap.add_css_class("cover")
        cover_wrap.set_overflow(Gtk.Overflow.HIDDEN)
        cover_wrap.set_size_request(w, h)
        cover_wrap.set_halign(Gtk.Align.CENTER)
        cover_wrap.append(picture)

        title = Gtk.Label(label=info.title)
        title.set_wrap(True)
        title.set_lines(2)
        title.set_ellipsize(Pango.EllipsizeMode.END)
        title.set_justify(Gtk.Justification.CENTER)
        title.set_max_width_chars(18)
        title.set_width_chars(18)
        title.set_valign(Gtk.Align.START)
        title.set_size_request(-1, 40)

        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        card.set_hexpand(True)
        card.append(cover_wrap)
        card.append(title)
        card.info = info
        return card, picture

    @staticmethod
    def _scale_crop(pixbuf, tw, th):
        sw, sh = pixbuf.get_width(), pixbuf.get_height()
        scale = max(tw / sw, th / sh)
        nw, nh = round(sw * scale), round(sh * scale)
        scaled = pixbuf.scale_simple(nw, nh, GdkPixbuf.InterpType.BILINEAR)
        cropped = GdkPixbuf.Pixbuf.new(
            scaled.get_colorspace(), scaled.get_has_alpha(),
            scaled.get_bits_per_sample(), tw, th,
        )
        scaled.copy_area((nw - tw) // 2, (nh - th) // 2, tw, th, cropped, 0, 0)
        return cropped

    def _load_cover(self, info, picture):
        if not info.cover_url:
            return
        cache_file = COVER_CACHE / f"{info.anilist_id}.img"
        try:
            if cache_file.exists():
                data = cache_file.read_bytes()
            else:
                resp = requests.get(info.cover_url, impersonate="chrome", timeout=15)
                resp.raise_for_status()
                data = resp.content
                cache_file.write_bytes(data)
            loader = GdkPixbuf.PixbufLoader()
            loader.write(data)
            loader.close()
            pixbuf = self._scale_crop(loader.get_pixbuf(), COVER_W, COVER_H)
            texture = Gdk.Texture.new_for_pixbuf(pixbuf)
        except Exception:
            return
        GLib.idle_add(picture.set_paintable, texture)

    def _clear_results(self):
        child = self.results.get_first_child()
        while child is not None:
            self.results.remove(child)
            child = self.results.get_first_child()

    def _set_loading(self, loading):
        self.search_entry.set_sensitive(not loading)
        if loading:
            self.spinner.start()
        else:
            self.spinner.stop()

    def _on_resize(self, *args):
        def do_resize():
            self._compute_cover_size()
            w, h = self._cover_size
            child = self.results.get_first_child()
            while child is not None:
                card = child.get_child()
                cover_wrap = card.get_first_child()
                cover_wrap.set_size_request(w, h)
                picture = cover_wrap.get_first_child()
                picture.set_size_request(w, h)
                child = child.get_next_sibling()
            return False
        GLib.idle_add(do_resize)


class AniDmApp(Adw.Application):
    def __init__(self):
        super().__init__(application_id="com.saka.anidm")

    def do_startup(self):
        Adw.Application.do_startup(self)
        self._load_css()
        flaresolverr.start()

    def _load_css(self):
        provider = Gtk.CssProvider()
        provider.load_from_string(".cover { border-radius: 12px; }")
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(),
            provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )

    def do_activate(self):
        AniDmWindow(self).present()

    def do_shutdown(self):
        flaresolverr.stop()
        Adw.Application.do_shutdown(self)


class EpisodePage(Adw.NavigationPage):
    def __init__(self, window, info):
        super().__init__(title=info.title)
        self.window = window
        self.info = info
        self.anime = None

        self.download_btn = Gtk.Button(label="Download")
        self.download_btn.add_css_class("suggested-action")
        self.download_btn.set_sensitive(False)
        self.download_btn.connect("clicked", self._on_download)

        header = Adw.HeaderBar()
        header.pack_end(self.download_btn)

        self.pick_btn = Gtk.Button(label="Wrong match?")
        self.pick_btn.connect("clicked", self._on_pick)
        header.pack_end(self.pick_btn)

        self.stack = Gtk.Stack()

        cover = Gtk.Picture()
        cover.set_size_request(168, 240)
        cover.add_css_class("cover")
        cover.set_overflow(Gtk.Overflow.HIDDEN)
        cover.set_valign(Gtk.Align.START)
        cover.set_halign(Gtk.Align.START)
        self._detail_cover = cover

        titles = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        titles.set_valign(Gtk.Align.END)
        titles.set_hexpand(True)

        title_main = Gtk.Label(label=info.english or info.romaji or "")
        title_main.add_css_class("title-1")
        title_main.set_wrap(True)
        title_main.set_xalign(0)
        titles.append(title_main)

        if info.romaji and info.romaji != (info.english or ""):
            title_sub = Gtk.Label(label=info.romaji)
            title_sub.add_css_class("dim-label")
            title_sub.set_wrap(True)
            title_sub.set_xalign(0)
            titles.append(title_sub)

        meta_bits = []
        if info.format:
            meta_bits.append(info.format)
        if info.episodes:
            meta_bits.append(f"{info.episodes} episodes")
        if info.year:
            meta_bits.append(str(info.year))
        if info.season:
            meta_bits.append(info.season.capitalize())
        if info.score:
            meta_bits.append(f"★ {info.score}%")

        if meta_bits:
            meta_label = Gtk.Label(label="  ·  ".join(meta_bits))
            meta_label.add_css_class("dim-label")
            meta_label.set_xalign(0)
            meta_label.set_wrap(True)
            meta_label.set_margin_top(8)
            titles.append(meta_label)

        if info.genres:
            genre_label = Gtk.Label(label=", ".join(info.genres[:4]))
            genre_label.add_css_class("dim-label")
            genre_label.add_css_class("caption")
            genre_label.set_xalign(0)
            genre_label.set_wrap(True)
            genre_label.set_margin_top(2)
            titles.append(genre_label)

        self.match_label = Gtk.Label()
        self.match_label.add_css_class("dim-label")
        self.match_label.add_css_class("caption")
        self.match_label.set_xalign(0)
        self.match_label.set_margin_top(8)
        titles.append(self.match_label)

        detail_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=16)
        detail_row.append(cover)
        detail_row.append(titles)

        self._summary_expanded = False

        summary_child = Gtk.Label(hexpand=True, xalign=0, wrap=True,
                                  wrap_mode=Pango.WrapMode.WORD_CHAR)
        summary_child.add_css_class("document")

        self.synopsis = SynopsisFading()
        self.synopsis.set_child(summary_child)
        self.synopsis.set_markup(self._clean_summary(info.description))
        self.synopsis.set_margin_top(12)

        self.more_btn = Gtk.ToggleButton(label="Show More")
        self.more_btn.add_css_class("circular")
        self.more_btn.set_halign(Gtk.Align.CENTER)
        self.more_btn.get_child().set_margin_start(24)
        self.more_btn.get_child().set_margin_end(24)
        self.more_btn.connect("toggled", self._toggle_summary)
        self.synopsis.bind_property(
            "faded", self.more_btn, "visible",
            GObject.BindingFlags.SYNC_CREATE
        )

        self.list_spinner = Gtk.Spinner()
        self.list_spinner.set_margin_top(12)

        self.listbox = Gtk.ListBox()
        self.listbox.set_selection_mode(Gtk.SelectionMode.NONE)
        self.listbox.add_css_class("boxed-list")

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        content.set_margin_top(12)
        content.set_margin_start(12)
        content.set_margin_end(12)
        content.set_margin_bottom(12)
        content.append(detail_row)
        content.append(self.synopsis)
        content.append(self.more_btn)
        content.append(self.list_spinner)
        content.append(self.listbox)

        clamp = Adw.Clamp()
        clamp.set_maximum_size(768)
        clamp.set_child(content)

        list_scroller = Gtk.ScrolledWindow()
        list_scroller.set_vexpand(True)
        list_scroller.set_child(clamp)

        self.stack.add_named(list_scroller, "list")

        toolbar = Adw.ToolbarView()
        toolbar.add_top_bar(header)
        toolbar.set_content(self.stack)
        self.set_child(toolbar)

        self.stack.set_visible_child_name("list")
        self.list_spinner.start()
        threading.Thread(target=self._load, daemon=True).start()
        threading.Thread(target=self._load_detail_cover, daemon=True).start()

    def _make_pick_button(self, label):
        btn = Gtk.Button(label=label)
        btn.add_css_class("suggested-action")
        btn.set_halign(Gtk.Align.CENTER)
        btn.connect("clicked", self._on_pick)
        return btn

    def _match_anime(self, info):
        want_year = info.year
        want_season = (info.season or "").lower()
        target_norms = [_norm(t) for t in (info.english, info.romaji) if t]
        best = None
        best_score = 0.0

        seen = {}
        for title in (info.english, info.romaji):
            if not title:
                continue
            try:
                results = self.window.backend.search(title)
            except Exception:
                continue
            for r in results:
                seen[r.slug] = r

        for r in seen.values():
            title_score = max(
                (_similar(t, r.title) for t in (info.english, info.romaji) if t),
                default=0.0,
            )

            bonus = 0.0
            r_season = (getattr(r, "season", "") or "").lower()
            if want_year and r.year == want_year:
                bonus += 0.25
                if want_season and r_season == want_season:
                    bonus += 0.25
            if info.episodes and r.episodes and info.episodes == r.episodes:
                bonus += 0.15
            if _norm(r.title) in target_norms:
                bonus += 0.35

            score = title_score + bonus
            if score > best_score:
                best_score = score
                best = r

        if best is not None and best_score >= 1.15:
            return best
        return None

    def _load(self):
        try:
            anime = self._match_anime(self.info)
            if anime is None:
                GLib.idle_add(self._on_error, "Not found on animepahe.")
                return
            episodes = self.window.backend.list_episodes(anime)
        except Exception as e:
            GLib.idle_add(self._on_error, str(e))
            return
        GLib.idle_add(self._on_loaded, anime, episodes)

    def _on_loaded(self, anime, episodes):
        self._clear_list()
        self.anime = anime
        self.match_label.set_text(f"animepahe match: {anime.title}")
        for ep in episodes:
            row = Adw.ActionRow()
            row.set_title(f"Episode {ep.number}")
            if ep.duration or ep.audio:
                row.set_subtitle(f"{ep.duration} · {ep.audio}")
            check = Gtk.CheckButton()
            check.set_valign(Gtk.Align.CENTER)
            row.add_prefix(check)
            row.set_activatable_widget(check)
            row._episode = ep
            row._check = check
            self.listbox.append(row)
        self.list_spinner.stop()
        self.download_btn.set_sensitive(True)

    def _on_error(self, message):
        self.list_spinner.stop()
        self.match_label.set_text(f"Couldn't load episodes: {message}")

    def _rows(self):
        row = self.listbox.get_first_child()
        while row is not None:
            yield row
            row = row.get_next_sibling()

    def _on_download(self, _btn):
        selected = [row._episode for row in self._rows() if row._check.get_active()]
        if not selected:
            self.window.toasts.add_toast(Adw.Toast(title="Select at least one episode."))
            return
        self.download_btn.set_sensitive(False)
        threading.Thread(target=self._download, args=(selected,), daemon=True).start()

    def _download(self, episodes):
        done = 0
        for ep in episodes:
            try:
                self.window.backend.download(self.anime, ep, str(LIBRARY_DIR))
                done += 1
                GLib.idle_add(self._toast, f"Downloaded episode {ep.number}")
            except Exception as e:
                GLib.idle_add(self._toast, f"Episode {ep.number} failed: {e}")
        GLib.idle_add(self._download_finished, done, len(episodes))

    def _toast(self, msg):
        self.window.toasts.add_toast(Adw.Toast(title=msg))

    def _download_finished(self, done, total):
        self.download_btn.set_sensitive(True)
        self._toast(f"Finished: {done}/{total} downloaded")

    def _on_pick(self, _btn):
        self.window.navview.push(PickerPage(self.window, self.info, self))

    def load_from_anime(self, anime):
        self.anime = anime
        self.list_spinner.start()
        self._clear_list()
        threading.Thread(target=self._load_chosen, args=(anime,), daemon=True).start()

    def _load_chosen(self, anime):
        try:
            episodes = self.window.backend.list_episodes(anime)
        except Exception as e:
            GLib.idle_add(self._on_error, str(e))
            return
        GLib.idle_add(self._on_loaded, anime, episodes)

    def _clear_list(self):
        row = self.listbox.get_first_child()
        while row is not None:
            self.listbox.remove(row)
            row = self.listbox.get_first_child()

    @staticmethod
    def _clean_summary(desc):
        if not desc:
            return "No summary available."
        text = re.sub(r"<\s*br\s*/?\s*>", " ", desc)
        text = re.sub(r"<[^>]+>", "", text)
        text = html.unescape(text)
        text = re.sub(r"\(Source:.*?\)", "", text, flags=re.IGNORECASE | re.DOTALL)
        text = re.sub(r"\s+", " ", text)
        return text.strip() or "No summary available."

    def _load_detail_cover(self):
        info = self.info
        url = info.cover_url_large or info.cover_url
        if not url:
            return
        cache_file = COVER_CACHE / f"{info.anilist_id}_lg.img"
        try:
            if cache_file.exists():
                data = cache_file.read_bytes()
            else:
                resp = requests.get(url, impersonate="chrome", timeout=15)
                resp.raise_for_status()
                data = resp.content
                cache_file.write_bytes(data)
            loader = GdkPixbuf.PixbufLoader()
            loader.write(data)
            loader.close()
            pixbuf = AniDmWindow._scale_crop(loader.get_pixbuf(), 168, 240)
            texture = Gdk.Texture.new_for_pixbuf(pixbuf)
        except Exception:
            return
        GLib.idle_add(self._detail_cover.set_paintable, texture)

    def _toggle_summary(self, button):
        active = button.get_active()
        self.more_btn.set_label("Show Less" if active else "Show More")
        self.synopsis.set_revealed(active)


class PickerPage(Adw.NavigationPage):
    def __init__(self, window, info, episode_page):
        super().__init__(title="Pick the right anime")
        self.window = window
        self.info = info
        self.episode_page = episode_page

        self.entry = Gtk.SearchEntry()
        self.entry.set_placeholder_text("Search animepahe directly…")
        self.entry.set_text(info.english or info.romaji or "")
        self.entry.connect("activate", self._on_search)

        self.spinner = Gtk.Spinner()

        self.listbox = Gtk.ListBox()
        self.listbox.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.listbox.add_css_class("boxed-list")
        self.listbox.connect("row-activated", self._on_row)

        scroller = Gtk.ScrolledWindow()
        scroller.set_vexpand(True)
        clamp = Adw.Clamp()
        clamp.set_child(self.listbox)
        scroller.set_child(clamp)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.set_margin_top(8)
        box.set_margin_start(12)
        box.set_margin_end(12)
        box.set_margin_bottom(12)
        box.append(self.entry)
        box.append(self.spinner)
        box.append(scroller)

        toolbar = Adw.ToolbarView()
        toolbar.add_top_bar(Adw.HeaderBar())
        toolbar.set_content(box)
        self.set_child(toolbar)

        self._search(self.entry.get_text())

    def _on_search(self, entry):
        self._search(entry.get_text().strip())

    def _search(self, query):
        if not query:
            return
        self.spinner.start()
        threading.Thread(target=self._do_search, args=(query,), daemon=True).start()

    def _do_search(self, query):
        try:
            results = self.window.backend.search(query)
        except Exception:
            results = []
        GLib.idle_add(self._show, results)

    def _show(self, results):
        self.spinner.stop()
        row = self.listbox.get_first_child()
        while row is not None:
            self.listbox.remove(row)
            row = self.listbox.get_first_child()

        if not results:
            empty = Adw.ActionRow()
            empty.set_title("No results")
            self.listbox.append(empty)
            return

        for anime in results:
            r = Adw.ActionRow()
            r.set_activatable(True)
            r.set_title(anime.title)
            bits = []
            if anime.year:
                bits.append(str(anime.year))
            if getattr(anime, "season", None):
                bits.append(anime.season)
            if anime.episodes:
                bits.append(f"{anime.episodes} eps")
            if bits:
                r.set_subtitle(" · ".join(bits))
            r._anime = anime
            self.listbox.append(r)

    def _on_row(self, _listbox, row):
        anime = getattr(row, "_anime", None)
        if anime is None:
            return
        self.window.navview.pop()
        self.episode_page.load_from_anime(anime)


def main():
    AniDmApp().run(None)


if __name__ == "__main__":
    main()
