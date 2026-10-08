import threading
import gi
import re
import html
import math
import cairo

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from difflib import SequenceMatcher
from gi.repository import Adw, Gtk, Gdk, Gio, GLib, Pango, GdkPixbuf, GObject
from curl_cffi import requests

from . import flaresolverr
from .metadata.anilist import AniListClient
from .backends.animepahe import AnimepaheBackend, LIBRARY_DIR
from .synopsis_fading import SynopsisFading
from .downloads import DownloadManager, Status

COVER_CACHE = Path.home() / ".cache" / "ani-dm" / "covers"
COVER_W, COVER_H = 140, 199
COVER_MARGIN = 6


def _norm(s):
    return re.sub(r"[^a-z0-9]", "", s.lower())


def _similar(a, b):
    return SequenceMatcher(None, _norm(a), _norm(b)).ratio()


class ProgressRing(Gtk.DrawingArea):
    def __init__(self, size=40):
        super().__init__()
        self._fraction = 0.0
        self.set_content_width(size)
        self.set_content_height(size)
        self.set_draw_func(self._draw)

    def set_fraction(self, fraction):
        self._fraction = max(0.0, min(1.0, fraction))
        self.queue_draw()

    def _draw(self, _area, cr, width, height, *_):
        cx, cy = width / 2, height / 2
        radius = min(cx, cy) - 3
        cr.set_line_width(4)
        cr.set_line_cap(cairo.LINE_CAP_ROUND)
        cr.set_source_rgba(1, 1, 1, 0.2)
        cr.arc(cx, cy, radius, 0, 2 * math.pi)
        cr.stroke()
        if self._fraction > 0:
            cr.set_source_rgb(0.21, 0.52, 0.89)
            start = -math.pi / 2
            cr.arc(cx, cy, radius, start, start + 2 * math.pi * self._fraction)
            cr.stroke()


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
        self._dl_toast = None
        self._dl_user_dismissed = False
        self._queue_btns = []
        self.manager = DownloadManager(
            self.backend, LIBRARY_DIR,
            on_change=lambda: GLib.idle_add(self._refresh_downloads),
        )
        self.skip_delete_confirm = False

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

        search_header = Adw.HeaderBar()
        search_header.pack_start(self._make_queue_btn())

        search_toolbar = Adw.ToolbarView()
        search_toolbar.add_top_bar(search_header)
        search_toolbar.set_content(box)

        search_page = Adw.NavigationPage(child=search_toolbar, title="Search")

        self.navview = Adw.NavigationView()
        self.navview.add(search_page)

        self.toasts = Adw.ToastOverlay()
        self.toasts.set_child(self.navview)
        self.set_content(self.toasts)

    def _make_queue_btn(self):
        btn = Gtk.Button()
        btn.add_css_class("flat")
        btn.set_tooltip_text("Show downloads")

        spinner = Gtk.Spinner()
        pause_icon = Gtk.Image(icon_name="media-playback-pause-symbolic")
        stack = Gtk.Stack()
        stack.add_named(spinner, "busy")
        stack.add_named(pause_icon, "paused")
        btn.set_child(stack)

        btn.set_visible(False)
        btn.connect("clicked", self._on_queue_btn)
        btn._spinner = spinner
        btn._stack = stack
        self._queue_btns.append(btn)
        return btn

    def _set_queue_btns_visible(self, visible, paused=False):
        for btn in self._queue_btns:
            btn.set_visible(visible)
            if visible and not paused:
                btn._stack.set_visible_child_name("busy")
                btn._spinner.start()
            else:
                btn._spinner.stop()
                btn._stack.set_visible_child_name("paused")

    def _on_queue_btn(self, _btn):
        self._dl_user_dismissed = False
        self._refresh_downloads()

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
        self._refresh_downloads()

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

    def _build_dl_toast(self):
        self._dl_ring = ProgressRing(size=40)
        self._dl_title = Gtk.Label(xalign=0)
        self._dl_title.add_css_class("heading")
        self._dl_title.set_ellipsize(Pango.EllipsizeMode.END)
        self._dl_title.set_max_width_chars(22)
        self._dl_sub = Gtk.Label(xalign=0)
        self._dl_sub.add_css_class("dim-label")

        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        text.set_hexpand(True)
        text.append(self._dl_title)
        text.append(self._dl_sub)

        self._dl_pause_btn = Gtk.Button()
        self._dl_pause_btn.add_css_class("flat")
        self._dl_pause_btn.set_valign(Gtk.Align.CENTER)
        self._dl_pause_btn.connect("clicked", self._on_dl_pause)

        stop_btn = Gtk.Button(icon_name="media-playback-stop-symbolic")
        stop_btn.add_css_class("flat")
        stop_btn.set_valign(Gtk.Align.CENTER)
        stop_btn.connect("clicked", self._on_dl_stop)

        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        box.append(self._dl_ring)
        box.append(text)
        box.append(self._dl_pause_btn)
        box.append(stop_btn)

        toast = Adw.Toast(timeout=0)
        toast.set_custom_title(box)
        toast.connect("dismissed", self._on_dl_toast_dismissed)
        self._dl_toast = toast

    def _on_dl_pause(self, _btn):
        if self.manager.is_paused():
            self.manager.resume()
        else:
            self.manager.pause()

    def _on_dl_stop(self, _btn):
        self.manager.stop()

    def _on_dl_toast_dismissed(self, toast):
        if self._dl_toast is toast:
            self._dl_toast = None
            self._dl_user_dismissed = True
            self._refresh_downloads()

    def _refresh_downloads(self):
        page = self.navview.get_visible_page()
        if isinstance(page, EpisodePage):
            page.refresh_download_states()

        pending = self.manager.pending_count()

        if pending == 0:
            t = self._dl_toast
            self._dl_toast = None
            self._dl_user_dismissed = False
            self._set_queue_btns_visible(False)
            if t is not None:
                t.dismiss()
            return

        if self._dl_user_dismissed:
            self._set_queue_btns_visible(True, self.manager.is_paused())
            return
        self._set_queue_btns_visible(False)

        if self._dl_toast is None:
            self._build_dl_toast()
            self.toasts.add_toast(self._dl_toast)

        paused = self.manager.is_paused()
        self._dl_pause_btn.set_icon_name(
            "media-playback-start-symbolic" if paused
            else "media-playback-pause-symbolic"
        )

        job = self.manager.current_job()
        if paused:
            self._dl_ring.set_fraction(0.0)
            self._dl_title.set_label("Paused")
            self._dl_sub.set_label(f"{pending} in queue")
        elif job is not None:
            self._dl_ring.set_fraction(job.percent / 100.0)
            self._dl_title.set_label(f"{job.anime.title} · Ep {job.episode.number}")
            extra = pending - 1
            if extra > 0:
                self._dl_sub.set_label(
                    f"{job.speed} · {extra} more queued" if job.speed
                    else f"{extra} more queued")
            else:
                self._dl_sub.set_label(job.speed or "Downloading…")


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

        actions = Gio.SimpleActionGroup()
        redl = Gio.SimpleAction.new("redownload", GLib.VariantType.new("s"))
        redl.connect("activate", self._act_redownload)
        actions.add_action(redl)
        dele = Gio.SimpleAction.new("delete", GLib.VariantType.new("s"))
        dele.connect("activate", self._act_delete)
        actions.add_action(dele)
        self.insert_action_group("ep", actions)

        self.download_btn = Gtk.Button(label="Download")
        self.download_btn.add_css_class("suggested-action")
        self.download_btn.set_sensitive(False)
        self.download_btn.connect("clicked", self._on_download)

        header = Adw.HeaderBar()
        header.pack_start(self.window._make_queue_btn())
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
            check.set_halign(Gtk.Align.CENTER)
            done_icon = Gtk.Image(icon_name="object-select-symbolic")
            done_icon.add_css_class("success")
            spinner = Gtk.Spinner()
            queued = Gtk.Label(label="Queued")
            queued.add_css_class("dim-label")

            status = Gtk.Stack()
            status.set_valign(Gtk.Align.CENTER)
            status.set_halign(Gtk.Align.CENTER)
            status.set_size_request(24, 24)
            status.add_named(check, "select")
            status.add_named(done_icon, "done")
            status.add_named(spinner, "busy")
            status.add_named(queued, "queued")
            row.add_prefix(status)
            row.set_activatable_widget(check)

            menu_btn = Gtk.MenuButton()
            menu_btn.set_icon_name("view-more-symbolic")
            menu_btn.add_css_class("flat")
            menu_btn.set_valign(Gtk.Align.CENTER)
            menu_btn.set_popover(self._make_episode_menu(ep))
            menu_btn.set_visible(False)
            row.add_suffix(menu_btn)

            row._episode = ep
            row._check = check
            row._status = status
            row._status_spinner = spinner
            row._menu_btn = menu_btn
            self.listbox.append(row)
        self.list_spinner.stop()
        self.download_btn.set_sensitive(True)
        self.refresh_download_states()

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
        for ep in selected:
            self.window.manager.enqueue(self.anime, ep)

    def _make_episode_menu(self, ep):
        menu = Gio.Menu()
        menu.append("Redownload", f"ep.redownload::{ep.number}")
        menu.append("Delete", f"ep.delete::{ep.number}")
        return Gtk.PopoverMenu.new_from_model(menu)

    def _act_redownload(self, _action, param):
        number = int(param.get_string())
        ep = self._episode_by_number(number)
        if ep is not None:
            self.window.manager.enqueue(self.anime, ep)

    def _act_delete(self, _action, param):
        number = int(param.get_string())
        ep = self._episode_by_number(number)
        if ep is not None:
            self._confirm_delete(ep)

    def _episode_by_number(self, number):
        for row in self._rows():
            if row._episode.number == number:
                return row._episode
        return None

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

    def _downloaded_numbers(self):
        numbers = set()
        for row in self._rows():
            ep = row._episode
            if self.window.backend.library_path(self.anime, ep, LIBRARY_DIR).exists():
                numbers.add(ep.number)
        return numbers

    def refresh_download_states(self):
        if getattr(self, "anime", None) is None:
            return
        downloaded = self._downloaded_numbers()
        for row in self._rows():
            ep = row._episode
            st = self.window.manager.status_for(self.anime, ep)
            if st == Status.DOWNLOADING:
                row._status.set_visible_child_name("busy")
                row._status_spinner.start()
            elif st == Status.QUEUED:
                row._status.set_visible_child_name("queued")
                row._status_spinner.stop()
            elif ep.number in downloaded:
                row._status.set_visible_child_name("done")
                row._status_spinner.stop()
            else:
                row._status.set_visible_child_name("select")
                row._status_spinner.stop()
            row._menu_btn.set_visible(
                st in (Status.DOWNLOADING, Status.QUEUED)
                or ep.number in downloaded
            )

    def _confirm_delete(self, ep):
        if self.window.skip_delete_confirm:
            self._do_delete(ep)
            return

        dialog = Adw.MessageDialog(
            transient_for=self.window,
            heading="Delete this episode?",
            body=f"Episode {ep.number} will be moved to Trash.",
        )
        check = Gtk.CheckButton(label="Don't ask again this session")
        dialog.set_extra_child(check)
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("delete", "Delete")
        dialog.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")
        dialog.connect("response", self._on_delete_response, ep, check)
        dialog.present()

    def _on_delete_response(self, _dialog, response, ep, check):
        if response != "delete":
            return
        if check.get_active():
            self.window.skip_delete_confirm = True
        self._do_delete(ep)

    def _do_delete(self, ep):
        path = self.window.backend.library_path(self.anime, ep, LIBRARY_DIR)
        try:
            Gio.File.new_for_path(str(path)).trash(None)
        except GLib.Error:
            pass
        for row in self._rows():
            if row._episode.number == ep.number:
                row._check.set_active(False)
                break
        self.refresh_download_states()


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
