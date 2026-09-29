# SPDX-FileCopyrightText: 2019-2026 Valéry Febvre
# SPDX-License-Identifier: GPL-3.0-or-later
# Author: Valéry Febvre <vfebvre@easter-eggs.com>
# Note from Copier: I give full credit to this guy up here

from gi.repository import Adw
from gi.repository import Gdk
from gi.repository import GLib
from gi.repository import GObject
from gi.repository import Graphene
from gi.repository import Gsk
from gi.repository import Gtk


class SynopsisFading(Gtk.Widget):
    __gtype_name__ = 'SynopsisFading'

    def __init__(self):
        super().__init__()

        self.__max_height = 95
        self.__faded = False

        self.min_height = self.__max_height
        self.current_height = 0
        self.allocated_width = 0

        self.animation = Adw.SpringAnimation(
            widget=self,
            value_from=0,
            value_to=1,
            spring_params=Adw.SpringParams.new(damping_ratio=0.5, mass=1, stiffness=50),
            target=Adw.CallbackAnimationTarget.new(self.animate),
            initial_velocity=1,
            epsilon=0.001,
            clamp=True
        )
        self.child = None

    @GObject.Property(type=bool, default=False)
    def faded(self):
        return self.__faded

    @faded.setter
    def faded(self, value):
        self.__faded = value

    @GObject.Property(type=int, default=95)
    def max_height(self):
        return self.__max_height

    @max_height.setter
    def max_height(self, value):
        if self.max_height == value:
            return

        self.__max_height = value

        if self.child:
            if self.allocated_width > 0:
                width = self.allocated_width
            else:
                _, width, _, _ = self.child.measure(Gtk.Orientation.HORIZONTAL, -1)

            _, child_height, _, _ = self.child.measure(Gtk.Orientation.VERTICAL, width)
            if child_height <= value:
                target_height = child_height
            else:
                target_height = value
        else:
            target_height = value

        self.animation_height_start = self.current_height
        self.animation_height_end = target_height
        self.animation.play()

    def animate(self, value):
        self.current_height = Adw.lerp(self.animation_height_start, self.animation_height_end, value)
        self.queue_resize()

    def do_measure(self, orientation, for_size):
        if not self.child:
            return 0, 0, -1, -1

        if orientation == Gtk.Orientation.HORIZONTAL:
            return self.child.measure(orientation, for_size)

        if for_size == -1:
            if self.allocated_width > 0:
                for_size = self.allocated_width
            else:
                _, for_size, _, _ = self.child.measure(Gtk.Orientation.HORIZONTAL, -1)

        _, child_height, _, _ = self.child.measure(Gtk.Orientation.VERTICAL, for_size)
        if child_height <= self.max_height:
            target_height = child_height
        else:
            target_height = self.max_height

        if self.animation.get_state() == Adw.AnimationState.IDLE:
            self.current_height = target_height

        return self.current_height, self.current_height, -1, -1

    def do_size_allocate(self, width, height, baseline):
        if self.allocated_width != width:
            self.allocated_width = width
            if self.child:
                _, child_height, _, _ = self.child.measure(Gtk.Orientation.VERTICAL, width)

                if self.min_height != self.max_height or child_height < self.min_height:
                    self.current_height = child_height
                GLib.idle_add(self.queue_resize)

        if self.child:
            _, child_height, _, _ = self.child.measure(Gtk.Orientation.VERTICAL, width)
            if child_height > height:
                child_height = child_height
            else:
                child_height = height

            self.child.allocate(width, child_height, baseline, None)

        self.update_faded()

    def do_snapshot(self, snapshot):
        if not self.child:
            return

        width = self.get_width()
        height = self.get_height()
        if height <= 0:
            return

        _, child_height, _, _ = self.child.measure(Gtk.Orientation.VERTICAL, width)
        if child_height <= height:
            self.snapshot_child(self.child, snapshot)
            return

        # Fading
        snapshot.push_mask(Gsk.MaskMode.ALPHA)

        stop_offset = Adw.lerp(0.25, 1, (height - self.min_height) / (child_height - self.min_height))

        cs0 = Gsk.ColorStop()
        cs0.color = Gdk.RGBA(1, 1, 1, 1)
        cs0.offset = stop_offset
        cs1 = Gsk.ColorStop()
        cs1.color = Gdk.RGBA(1, 1, 1, 0)
        cs1.offset = 1

        snapshot.append_linear_gradient(
            Graphene.Rect.init(Graphene.Rect.alloc(), 0, 0, width, height),
            Graphene.Point.init(Graphene.Point.alloc(), 0, 0),
            Graphene.Point.init(Graphene.Point.alloc(), 0, height),
            [cs0, cs1]
        )

        snapshot.pop()

        self.snapshot_child(self.child, snapshot)

        snapshot.pop()

    def get_request_mode(self):
        return Gtk.SizeRequestMode.HEIGHT_FOR_WIDTH

    def set_child(self, child):
        if self.child:
            self.child.unparent()

        self.child = child
        self.child.set_parent(self)

    def set_markup(self, text):
        self.__faded = None
        self.__max_height = self.min_height
        self.current_height = self.min_height
        self.allocated_width = 0
        self.child.set_markup(text)

    def set_revealed(self, revealed):
        self.max_height = self.child.get_height() if revealed else self.min_height

    def update_faded(self):
        new_value = False

        if self.child:
            if self.allocated_width > 0:
                width = self.allocated_width
            else:
                _, width, _, _ = self.child.measure(Gtk.Orientation.HORIZONTAL, -1)
            _, child_height, _, _ = self.child.measure(Gtk.Orientation.VERTICAL, width)

            new_value = child_height > self.min_height

        if self.faded != new_value:
            self.faded = new_value

