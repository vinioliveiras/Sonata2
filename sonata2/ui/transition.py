"""Standard motion. Every popover (menus, menu bar panels, Control
Center, Dock previews) opens with the same short fade + grow (CSS
animation, registered here).

Cross-fade for content that changes in place (a Files folder, a pane):
a picture of the old content, over the new, fading out.

    fade = ui.transition.CrossFade(child)    # a Gtk.Overlay around child
    fade.capture()                           # before the change
    ... change the content ...
    fade.play()                              # when the new content is there

Cheap: one texture and one opacity animation; nothing runs when idle."""
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Graphene, Gtk  # noqa: E402

from . import theme  # noqa: E402

theme.register("""
@keyframes sonata-open { from { opacity: 0; transform: scale(0.96); } to { opacity: 1; transform: none; } }
popover > contents { animation: sonata-open %(t_open)s %(ease_out)s; }
""", key="motion-open")

DURATION_MS = 200


class CrossFade(Gtk.Overlay):
    def __init__(self, child: Gtk.Widget, duration: int = DURATION_MS):
        super().__init__(child=child)
        self.duration = duration
        self._pic = None
        self._anim = None

    def capture(self) -> None:
        """Freeze how the content looks now (covers it until play())."""
        self._finish()
        child = self.get_child()
        native = self.get_native()
        w, h = child.get_width(), child.get_height()
        if not child.get_mapped() or w <= 0 or h <= 0 or native is None or native.get_renderer() is None:
            return
        snap = Gtk.Snapshot()
        Gtk.WidgetPaintable.new(child).snapshot(snap, w, h)
        node = snap.to_node()
        if node is None:
            return
        tex = native.get_renderer().render_texture(node, Graphene.Rect().init(0, 0, w, h))
        self._pic = Gtk.Picture(paintable=tex, can_target=False, content_fit=Gtk.ContentFit.FILL)
        self.add_overlay(self._pic)

    def play(self) -> None:
        """Fade the frozen picture out, revealing the new content."""
        pic = self._pic
        if pic is None or self._anim is not None:
            return
        target = Adw.PropertyAnimationTarget.new(pic, "opacity")
        self._anim = Adw.TimedAnimation.new(pic, 1.0, 0.0, self.duration, target)
        self._anim.set_easing(Adw.Easing.EASE_OUT_CUBIC)
        self._anim.connect("done", lambda *_: self._finish())
        self._anim.play()

    def _finish(self) -> None:
        if self._anim is not None:
            anim, self._anim = self._anim, None
            anim.skip() if anim.get_state() == Adw.AnimationState.PLAYING else None
        if self._pic is not None:
            self.remove_overlay(self._pic)
            self._pic = None
