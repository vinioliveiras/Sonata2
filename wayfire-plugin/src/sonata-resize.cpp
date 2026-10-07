/*
 * sonata-resize: Wayfire's resize plugin (MIT, the Wayfire authors) with one
 * addition for Sonata -- "Show window contents while resizing" (Settings >
 * Desktop & Dock). Off: while the edge is dragged only the window's
 * background (a plain panel in its colours) follows the pointer; the app gets
 * the new size once, on release, and its contents fade in. Apps that re-lay
 * out slowly (Chrome with a heavy page) no longer judder, and nothing is
 * re-laid out 60 times a second. On (default): Wayfire's live resize.
 *
 * Loaded instead of "resize" (tools/wayfire-config.sh); reads resize's own
 * options (resize/activate...), plus sonata-resize/{live,fill,border}.
 *
 * And the title bar's top edge (Vini): a double-click there did two tiny
 * resizes -- the window grew or shrank a little, depending on where on the
 * title bar you clicked. A double-click on the top edge now does what a
 * double-click on the title bar does (sonata-resize/double_click, set with
 * Settings' "Double-click a window's title bar to": Zoom, Minimize or
 * nothing), and a click that doesn't move (< DEAD_ZONE px) never resizes.
 *
 * And zoom (maximize / restore): Wayfire's grid crossfaded a picture of the
 * window at its old size into the new one -- two title bars over each other
 * for a moment (Vini: "a smear"). Like macOS, the window's frame (the same
 * background panel the outline resize uses) grows or shrinks to its new
 * place in ZOOM_MS, and the window's contents fade in there once the app
 * has drawn itself at that size (the outline resize's fade). Grid's own
 * animation is off (config/wayfire.ini: [grid] type = none);
 * sonata-resize/zoom turns this one off.
 *
 * And the edge (Vini): a band just inside every window's edge resizes it
 * (edge_grab_node_t), not only the space outside the frame.
 */
#include <wayfire/compositor-view.hpp>
#include <wayfire/render-manager.hpp>
#include <wayfire/view-transform.hpp>
#include <wayfire/util/duration.hpp>
#include <wayfire/util.hpp>
#include <wayfire/workarea.hpp>
#include <wayfire/scene-render.hpp>
#include <wayfire/scene-operations.hpp>
#include <wayfire/opengl.hpp>
#include <wayfire/core.hpp>
#include "wayfire/geometry.hpp"
#include "wayfire/plugins/common/input-grab.hpp"
#include "wayfire/scene-input.hpp"
#include "wayfire/txn/transaction-manager.hpp"
#include <wayfire/toplevel.hpp>
#include <wayfire/window-manager.hpp>
#include <cmath>
#include <cstdlib>
#include <wayfire/config/config-manager.hpp>
#include <wayfire/per-output-plugin.hpp>
#include <wayfire/output.hpp>
#include <wayfire/view.hpp>
#include <wayfire/core.hpp>
#include <wayfire/workspace-set.hpp>
#include <linux/input.h>
#include <wayfire/signal-definitions.hpp>
#include <wayfire/plugins/wobbly/wobbly-signal.hpp>
#include <wayfire/nonstd/wlroots-full.hpp>
#include <wlr/util/edges.h>
#include <chrono>

/* -- the window's background while it is resized without its contents --------------- */
namespace
{
const char *GHOST_VS = R"(
#version 100
attribute highp vec2 position;
varying highp vec2 pos;
uniform mat4 mvp;
void main() {
   gl_Position = mvp * vec4(position.xy, 0.0, 1.0);
   pos = position.xy;
}
)";

/* A rounded rectangle (the window's corner radius) in the window background,
 * a 1 px hairline inside its edge and a soft shadow below it, like the window
 * it stands for. Colours are straight alpha; output premultiplied. */
const char *GHOST_FS = R"(
#version 100
precision highp float;
varying highp vec2 pos;
uniform vec4 rect;
uniform float radius;
uniform vec4 fill;
uniform vec4 border;
uniform float alpha;
float sdf(vec2 p) {
    vec2 h = rect.zw * 0.5;
    vec2 d = abs(p - (rect.xy + h)) - (h - vec2(radius));
    return length(max(d, 0.0)) + min(max(d.x, d.y), 0.0) - radius;
}
void main() {
    float d = sdf(pos);
    float inside = clamp(0.5 - d, 0.0, 1.0);
    float ring = clamp(1.0 - abs(d + 0.5), 0.0, 1.0) * inside;
    float ds = max(sdf(pos - vec2(0.0, 1.0)), 0.0);              /* almost none, like the windows' */
    float shadow = 0.14 * exp(-pow(ds / 4.0, 2.0)) * (1.0 - inside);
    vec4 c = vec4(0.0, 0.0, 0.0, shadow);
    vec4 f = vec4(fill.rgb * fill.a, fill.a) * inside;
    c = f + c * (1.0 - f.a);
    vec4 b = vec4(border.rgb * border.a, border.a) * ring;
    c = b + c * (1.0 - b.a);
    gl_FragColor = c * alpha;
}
)";

const int GHOST_MARGIN = 48;           // room for the shadow around the panel

struct ghost_program_t : public wf::custom_data_t
{
    OpenGL::program_t program;
};
}

class ghost_node_t : public wf::scene::node_t
{
    class instance_t : public wf::scene::simple_render_instance_t<ghost_node_t>
    {
      public:
        using simple_render_instance_t::simple_render_instance_t;

        void render(const wf::scene::render_instruction_t& data) override
        {
            auto box = self->get_bounding_box();
            auto r   = self->rect;
            const float x1 = box.x, y1 = box.y, x2 = box.x + box.width, y2 = box.y + box.height;
            const float verts[] = {x1, y2, x2, y2, x2, y1, x1, y1};
            auto prog = wf::get_core().get_data<ghost_program_t>();
            data.pass->custom_gles_subpass(data.target, [&]
            {
                if (!prog->program.get_program_id(wf::TEXTURE_TYPE_RGBA))
                {
                    prog->program.compile(GHOST_VS, GHOST_FS);
                }

                prog->program.use(wf::TEXTURE_TYPE_RGBA);
                prog->program.uniformMatrix4f("mvp", wf::gles::render_target_orthographic_projection(data.target));
                prog->program.uniform4f("rect", glm::vec4{r.x, r.y, r.width, r.height});
                prog->program.uniform1f("radius", self->radius);
                prog->program.uniform4f("fill", glm::vec4{self->fill.r, self->fill.g, self->fill.b, self->fill.a});
                prog->program.uniform4f("border",
                    glm::vec4{self->border.r, self->border.g, self->border.b, self->border.a});
                prog->program.uniform1f("alpha", self->alpha);
                prog->program.attrib_pointer("position", 2, 0, verts);
                wf::gles::bind_render_buffer(data.target);
                GL_CALL(glEnable(GL_BLEND));
                GL_CALL(glBlendFunc(GL_ONE, GL_ONE_MINUS_SRC_ALPHA));
                for (const auto& b : data.damage)
                {
                    wf::gles::render_target_logic_scissor(data.target, b);
                    GL_CALL(glDrawArrays(GL_TRIANGLE_FAN, 0, 4));
                }

                GL_CALL(glDisable(GL_BLEND));
                prog->program.deactivate();
            });
        }
    };

  public:
    wf::geometry_t rect{0, 0, 1, 1};
    wf::color_t fill{0.93, 0.93, 0.93, 0.95};
    wf::color_t border{0, 0, 0, 0.2};
    float radius = 10;
    float alpha  = 1;

    ghost_node_t() : node_t(false)
    {}

    void gen_render_instances(std::vector<wf::scene::render_instance_uptr>& instances,
        wf::scene::damage_callback push_damage, wf::output_t *output) override
    {
        instances.push_back(std::make_unique<instance_t>(this, push_damage, output));
    }

    wf::geometry_t get_bounding_box() override
    {
        return wf::geometry_t{rect.x - GHOST_MARGIN, rect.y - GHOST_MARGIN,
            rect.width + 2 * GHOST_MARGIN, rect.height + 2 * GHOST_MARGIN};
    }

    void set(wf::geometry_t r)
    {
        auto before = get_bounding_box();
        rect = r;
        wf::scene::damage_node(shared_from_this(), before);
        wf::scene::damage_node(shared_from_this(), get_bounding_box());
    }

    void damage()
    {
        wf::scene::damage_node(shared_from_this(), get_bounding_box());
    }
};

/* The window as seen: a window pixdecor decorates (terminals, X11 apps)
 * has its shadow inside its geometry -- the panel came out that much
 * bigger than the window (Vini). The same inset as sonata-corners.
 * `tiled`: the tiling to judge by (-1: the window's own now). */
static wf::geometry_t visible(wayfire_toplevel_view v, wf::geometry_t g, int tiled = -1)
{
    auto m = v->toplevel()->current().margins;
    if ((m.left <= 0) && (m.top <= 0))
    {
        return g;                                    // its own frame: geometry is the window
    }

    auto& cfg     = wf::get_core().config;
    auto engine   = cfg->get_option("pixdecor/overlay_engine");
    auto radius   = cfg->get_option("pixdecor/shadow_radius");
    auto max_shad = cfg->get_option("pixdecor/maximized_shadows");
    if (!engine || !radius || (engine->get_value_str() != "rounded_corners"))
    {
        return g;
    }

    uint32_t edges = (tiled < 0) ? v->pending_tiled_edges() : (uint32_t)tiled;
    if ((edges != 0) && (!max_shad || (max_shad->get_value_str() != "true")))
    {
        return g;
    }

    int inset = 2 * std::max(0, std::atoi(radius->get_value_str().c_str()));
    if ((g.width <= 2 * inset) || (g.height <= 2 * inset))
    {
        return g;
    }

    return {g.x + inset, g.y + inset, g.width - 2 * inset, g.height - 2 * inset};
}


/* -- holding the window's edge itself ------------------------------------------------
 * Vini: resizing, the pointer had to be just outside the window -- on its
 * shadow, never on the window: pixdecor's and GTK's handles are outside the
 * frame. Each window gets a band EDGE_IN px wide just inside its visible edge
 * (a node over the window, in its own stacking place) that shows the resize
 * pointer and starts the resize; corners are CORNER px long. Not on a
 * maximized, tiled or full-screen window. */
uint32_t band_edges(int x, int y, int w, int h, double px, double py);

class edge_grab_node_t : public wf::scene::node_t, public wf::pointer_interaction_t
{
    wayfire_toplevel_view view;
    uint32_t hovered = 0;

  public:
    edge_grab_node_t(wayfire_toplevel_view v) : wf::scene::node_t(false), view(v)
    {}

    uint32_t edges_at(wf::pointf_t at)
    {
        if (!view || !view->is_mapped() || view->pending_fullscreen() || view->pending_tiled_edges() ||
            !(view->get_allowed_actions() & wf::VIEW_ALLOW_RESIZE) ||
            (view->role == wf::VIEW_ROLE_DESKTOP_ENVIRONMENT))
        {
            return 0;
        }

        auto g = visible(view, view->get_geometry());
        return band_edges(g.x, g.y, g.width, g.height, at.x, at.y);
    }

    std::optional<wf::scene::input_node_t> find_node_at(const wf::pointf_t& at) override
    {
        if (edges_at(at))
        {
            return wf::scene::input_node_t{.node = this, .local_coords = at};
        }

        return {};
    }

    wf::pointer_interaction_t& pointer_interaction() override
    {
        return *this;
    }

    std::string stringify() const override
    {
        return "sonata-resize edge";
    }

    void show(wf::pointf_t at)
    {
        uint32_t e = edges_at(at);
        if (e && (e != hovered))
        {
            wf::get_core().set_cursor(wlr_xcursor_get_resize_name((wlr_edges)e));
        }

        hovered = e;
    }

    void handle_pointer_enter(wf::pointf_t at) override
    {
        hovered = 0;
        show(at);
    }

    void handle_pointer_motion(wf::pointf_t at, uint32_t) override
    {
        show(at);
    }

    void handle_pointer_leave() override
    {
        hovered = 0;
    }

    void handle_pointer_button(const wlr_pointer_button_event& ev) override
    {
        if ((ev.button == BTN_LEFT) && (ev.state == WL_POINTER_BUTTON_STATE_PRESSED) && hovered)
        {
            wf::get_core().default_wm->resize_request(view, hovered);   // sonata-resize takes it from here
        }
    }
};

static const char *EDGE_DATA = "sonata-resize-edge";

struct edge_grab_data_t : public wf::custom_data_t
{
    std::shared_ptr<edge_grab_node_t> node;
};

/** The edge band on a window (once). */
static void add_edge_grab(wayfire_toplevel_view v)
{
    if (!v || v->has_data(EDGE_DATA) || (v->role != wf::VIEW_ROLE_TOPLEVEL))
    {
        return;
    }

    auto data  = std::make_unique<edge_grab_data_t>();
    data->node = std::make_shared<edge_grab_node_t>(v);
    wf::scene::add_front(v->get_root_node(), data->node);
    v->store_data(std::move(data), EDGE_DATA);
}

static void remove_edge_grab(wayfire_toplevel_view v)
{
    if (auto data = v ? v->get_data<edge_grab_data_t>(EDGE_DATA) : nullptr)
    {
        wf::scene::remove_child(data->node);
        v->erase_data(EDGE_DATA);
    }
}

static constexpr int ZOOM_MS = 250;
static constexpr int DEAD_ZONE = 3;                  // px: less is a click, not a resize
static constexpr int EDGE_IN = 4;                    // px inside the window's edge that resize it
static constexpr int CORNER = 14;                    // px along an edge from a corner: the corner
static constexpr int64_t DOUBLE_CLICK_MS = 400;
static constexpr double DOUBLE_CLICK_PX = 6.0;

/** The edges a point EDGE_IN px or less inside the window (x, y, w, h)
 * resizes: one side, or a corner near one; 0 elsewhere (and outside). */
uint32_t band_edges(int x, int y, int w, int h, double px, double py)
{
    if ((px < x) || (py < y) || (px >= x + w) || (py >= y + h) || (w <= 2 * EDGE_IN) || (h <= 2 * EDGE_IN))
    {
        return 0;
    }

    bool l = px < x + EDGE_IN, r = px >= x + w - EDGE_IN;
    bool t = py < y + EDGE_IN, b = py >= y + h - EDGE_IN;
    if (!(l || r || t || b))
    {
        return 0;
    }

    // on an edge near a corner: that corner
    l = l || ((t || b) && (px < x + CORNER));
    r = r || ((t || b) && (px >= x + w - CORNER));
    t = t || ((l || r) && (py < y + CORNER));
    b = b || ((l || r) && (py >= y + h - CORNER));
    return (l ? WLR_EDGE_LEFT : r ? WLR_EDGE_RIGHT : 0) | (t ? WLR_EDGE_TOP : b ? WLR_EDGE_BOTTOM : 0);
}

/** A maximized window reaching past the work area (x, y, w, h) -- e.g.
 * behind the Dock. */
bool past_workarea(int gx, int gy, int gw, int gh, int wx, int wy, int ww, int wh)
{
    return (gx < wx) || (gy < wy) || (gx + gw > wx + ww) || (gy + gh > wy + wh);
}

/* A window brought back from maximized fits the work area (Vini: one bigger
 * than it came back under the Dock and the menu bar -- and, its size not
 * changing on screen, without the zoom): no bigger than it, moved inside. */
template<class N, class M>
void fit_inside(N& gx, N& gy, N& gw, N& gh, M wx, M wy, M ww, M wh)
{
    gw = std::min<N>(gw, ww);
    gh = std::min<N>(gh, wh);
    gx = std::max<N>(wx, std::min<N>(gx, wx + ww - gw));
    gy = std::max<N>(wy, std::min<N>(gy, wy + wh - gh));
}

/* The last press on a window's top edge (every display's plugin shares it). */
static struct
{
    int64_t at_ms = 0;
    wf::pointf_t where{0, 0};
    uint32_t view_id = 0;
} last_top_press;

static int64_t now_ms()
{
    using namespace std::chrono;
    return duration_cast<milliseconds>(steady_clock::now().time_since_epoch()).count();
}

/** A press on the top edge only (not a corner) that is the second of a
 * double-click on the same window, at the same spot. Remembers this one. */
bool top_edge_double_click(uint32_t edges, uint32_t view_id, wf::pointf_t at, int64_t when_ms)
{
    if (edges != WLR_EDGE_TOP)
    {
        last_top_press.at_ms = 0;
        return false;
    }

    bool twice = last_top_press.at_ms && (last_top_press.view_id == view_id) &&
        (when_ms - last_top_press.at_ms <= DOUBLE_CLICK_MS) &&
        (std::hypot(at.x - last_top_press.where.x, at.y - last_top_press.where.y) <= DOUBLE_CLICK_PX);
    last_top_press = {twice ? 0 : when_ms, at, view_id};
    return twice;
}

class wayfire_resize : public wf::per_output_plugin_instance_t, public wf::pointer_interaction_t,
    public wf::touch_interaction_t
{
    wf::signal::connection_t<wf::view_resize_request_signal> on_resize_request =
        [=] (wf::view_resize_request_signal *request)
    {
        if (!request->view)
        {
            return;
        }

        auto touch = wf::get_core().get_touch_position(0);
        if (!std::isnan(touch.x) && !std::isnan(touch.y))
        {
            is_using_touch = true;
        } else
        {
            is_using_touch = false;
        }

        was_client_request = true;
        preserve_aspect    = false;
        LOGI("sonata-resize: resize asked by the app, edges ", request->edges);
        initiate(request->view, request->edges);
    };

    wf::signal::connection_t<wf::view_disappeared_signal> on_view_disappeared =
        [=] (wf::view_disappeared_signal *ev)
    {
        if (ev->view == view)
        {
            outline = false;
            close_ghost();
            view = nullptr;
            input_pressed(WLR_BUTTON_RELEASED);
        }

        if (fading && (ev->view == fading))
        {
            finish_fade();
            close_ghost();
        }

        if (zooming && (ev->view == zooming))
        {
            end_zoom(false);
        }
    };

    wf::button_callback activate_binding;
    wf::button_callback activate_binding_preserve_aspect;

    wayfire_toplevel_view view;

    bool was_client_request, is_using_touch;
    bool preserve_aspect = false;
    wf::pointf_t grab_start;
    wf::geometry_t grabbed_geometry;

    uint32_t edges;

    wf::option_wrapper_t<int> user_min_width{"resize/min_width"};
    wf::option_wrapper_t<int> user_min_height{"resize/min_height"};
    wf::option_wrapper_t<double> corner_threshold{"resize/corner_threshold"};
    wf::option_wrapper_t<wf::buttonbinding_t> button{"resize/activate"};
    wf::option_wrapper_t<wf::buttonbinding_t> button_preserve_aspect{
        "resize/activate_preserve_aspect"};
    std::unique_ptr<wf::input_grab_t> input_grab;

    // -- Sonata: resize with the window's background only, contents fade in after --
    wf::option_wrapper_t<bool> live{"sonata-resize/live"};
    wf::option_wrapper_t<std::string> double_click{"sonata-resize/double_click"};
    wf::option_wrapper_t<bool> zoom_enabled{"sonata-resize/zoom"};

    // -- zoom: the frame grows / shrinks, the contents fade in at the new size --
    wayfire_toplevel_view zooming;
    wf::geometry_t zoom_from, zoom_hint, zoom_seen;     // zoom_seen: where it started, as seen
    int zoom_tiled = -1;                                 // the tiling it goes to
    wf::animation::simple_animation_t zoom{wf::create_option(ZOOM_MS),
        wf::animation::smoothing::circle};
    wf::effect_hook_t zoom_hook = [=] () { zoom_step(); };

    /* Vini: out of full screen (a video in Chrome), a maximized window
     * sometimes kept the whole display -- under the Dock. Maximized windows
     * are put back inside the work area once full screen ends and whenever
     * the work area changes. */
    wf::wl_timer<false> refit_timer;
    /* Vini: maximized, Claude's title bar stayed at the restored width
     * (and restored, its frame a strip too wide) -- an app slower than the
     * fade (Electron) gets its frame redrawn again a while after it ends */
    wf::wl_timer<false> frame_timer;
    std::weak_ptr<wf::view_interface_t> frame_view;
    int frame_checks = 0;
    wf::signal::connection_t<wf::view_fullscreen_signal> on_fullscreen = [=] (wf::view_fullscreen_signal *ev)
    {
        if (!ev->state)
        {
            refit_soon();
        }
    };
    wf::signal::connection_t<wf::workarea_changed_signal> on_workarea = [=] (wf::workarea_changed_signal*)
    {
        refit_soon();
    };

    void refit_soon()
    {
        refit_timer.disconnect();
        refit_timer.set_timeout(150, [=] () { refit_maximized(); });   // after the window took its new state
    }

    void refit_maximized()
    {
        auto wa = output->workarea->get_workarea();
        for (auto& v : output->wset()->get_views(wf::WSET_MAPPED_ONLY))
        {
            if ((v->pending_tiled_edges() != wf::TILED_EDGES_ALL) || v->pending_fullscreen() ||
                (v.get() == zooming.get()))
            {
                continue;
            }

            auto g = v->get_geometry();
            if (past_workarea(g.x, g.y, g.width, g.height, wa.x, wa.y, wa.width, wa.height))
            {
                LOGI("sonata-resize: a maximized window back inside the work area");
                v->toplevel()->pending().geometry = wa;
                wf::get_core().tx_manager->schedule_object(v->toplevel());
            }
        }
    }

    wf::signal::connection_t<wf::view_mapped_signal> on_view_mapped = [=] (wf::view_mapped_signal *ev)
    {
        add_edge_grab(toplevel_cast(ev->view));
    };
    wf::signal::connection_t<wf::view_tile_request_signal> on_tile_request =
        [=] (wf::view_tile_request_signal *ev)
    {
        if ((ev->edges == 0) && ev->view && (ev->desired_size.width > 0))
        {
            auto wa = output->workarea->get_workarea();     // (the Dock hidden: the whole display)
            auto& d = ev->desired_size;
            fit_inside(d.x, d.y, d.width, d.height, wa.x, wa.y, wa.width, wa.height);
            fit_restored_soon(ev->view);                     // in case another plugin took it first
        }

        start_zoom(ev->view, ev->desired_size, ev->edges);
    };

    wf::wl_timer<false> fit_timer;
    std::weak_ptr<wf::view_interface_t> fit_view;

    void fit_restored_soon(wayfire_toplevel_view v)
    {
        fit_view = v->shared_from_this();
        fit_timer.disconnect();
        fit_timer.set_timeout(50, [=] ()
        {
            auto t = toplevel_cast(fit_view.lock());
            if (!t || !t->is_mapped() || t->pending_tiled_edges() || t->pending_fullscreen())
            {
                return;
            }

            auto wa = output->workarea->get_workarea();
            auto g  = t->toplevel()->pending().geometry;
            if (past_workarea(g.x, g.y, g.width, g.height, wa.x, wa.y, wa.width, wa.height))
            {
                fit_inside(g.x, g.y, g.width, g.height, wa.x, wa.y, wa.width, wa.height);
                LOGI("sonata-resize: a restored window fitted into the work area ", g);
                t->toplevel()->pending().geometry = g;
                wf::get_core().tx_manager->schedule_object(t->toplevel());
            }
        });
    }
    bool moved = false;                                  // past the dead zone this drag
    wf::option_wrapper_t<wf::color_t> fill{"sonata-resize/fill"};
    wf::option_wrapper_t<wf::color_t> border{"sonata-resize/border"};
    static constexpr const char *FADE = "sonata-resize";
    std::shared_ptr<ghost_node_t> ghost;                 // the background following the pointer
    wf::option_wrapper_t<int> corner_radius{"sonata-corners/radius"};
    wf::geometry_t ghost_geometry;
    bool outline = false;                                // this drag resizes the background only
    wayfire_toplevel_view fading;                        // contents fading in at the new size
    wf::animation::simple_animation_t fade{wf::create_option(200)};
    bool fade_started = false;
    uint32_t fade_requested_at = 0;
    wf::effect_hook_t fade_hook = [=] () { fade_step(); };

    wf::signal::connection_t<wf::view_geometry_changed_signal> on_new_size =
        [=] (wf::view_geometry_changed_signal *ev)
    {
        if (fading && (ev->view == fading) && !fade_started)
        {
            start_fade();                                // the app drew itself at the new size
        }
    };
    wf::plugin_activation_data_t grab_interface = {
        .name = "resize",
        .capabilities = wf::CAPABILITY_GRAB_INPUT | wf::CAPABILITY_MANAGE_DESKTOP,
    };

  public:
    void init() override
    {
        input_grab = std::make_unique<wf::input_grab_t>("resize", output, nullptr, this, this);

        activate_binding = [=] (auto)
        {
            return activate(false);
        };

        activate_binding_preserve_aspect = [=] (auto)
        {
            return activate(true);
        };

        output->add_button(button, &activate_binding);
        output->add_button(button_preserve_aspect, &activate_binding_preserve_aspect);
        grab_interface.cancel = [=] ()
        {
            input_pressed(WLR_BUTTON_RELEASED);
        };

        output->connect(&on_resize_request);
        LOGI("sonata-resize: ready on ", output->to_string(), ", live=", (bool)live);
        output->connect(&on_view_mapped);
        output->connect(&on_fullscreen);
        output->connect(&on_workarea);
        for (auto& v : wf::get_core().get_all_views())
        {
            if (auto t = toplevel_cast(v); t && t->is_mapped() && (t->get_output() == output))
            {
                add_edge_grab(t);
            }
        }
        output->connect(&on_view_disappeared);
        output->connect(&on_tile_request);
    }

    bool activate(bool should_preserve_aspect)
    {
        auto view = toplevel_cast(wf::get_core().get_cursor_focus_view());
        if (view)
        {
            is_using_touch     = false;
            was_client_request = false;
            preserve_aspect    = should_preserve_aspect;
            initiate(view);
        }

        return false;
    }

    void handle_pointer_button(const wlr_pointer_button_event& event) override
    {
        if ((event.state == WL_POINTER_BUTTON_STATE_RELEASED) && was_client_request &&
            (event.button == BTN_LEFT))
        {
            return input_pressed(event.state);
        }

        if ((event.button != wf::buttonbinding_t(button).get_button()) &&
            (event.button != wf::buttonbinding_t(button_preserve_aspect).get_button()))
        {
            return;
        }

        input_pressed(event.state);
    }

    void handle_pointer_motion(wf::pointf_t pointer_position, uint32_t time_ms) override
    {
        input_motion();
    }

    void handle_touch_up(uint32_t time_ms, int finger_id, wf::pointf_t lift_off_position) override
    {
        if (finger_id == 0)
        {
            input_pressed(WLR_BUTTON_RELEASED);
        }
    }

    void handle_touch_motion(uint32_t time_ms, int finger_id, wf::pointf_t position) override
    {
        if (finger_id == 0)
        {
            input_motion();
        }
    }

    /* Returns the currently used input coordinates in global compositor space */
    wf::pointf_t get_global_input_coords()
    {
        wf::pointf_t input;
        if (is_using_touch)
        {
            input = wf::get_core().get_touch_position(0);
        } else
        {
            input = wf::get_core().get_cursor_position();
        }

        return input;
    }

    /* Returns the currently used input coordinates in output-local space */
    wf::pointf_t get_input_coords()
    {
        auto og = output->get_layout_geometry();
        return get_global_input_coords() - wf::origin(og);
    }

    /* Calculate resize edges, grab starts at (sx, sy), view's geometry is vg */
    uint32_t calculate_edges(wf::geometry_t vg, wf::pointf_t input)
    {
        int sx     = (int)input.x;
        int sy     = (int)input.y;
        int view_x = sx - vg.x;
        int view_y = sy - vg.y;

        const double threshold = static_cast<double>(corner_threshold);
        const bool in_left     = view_x < vg.width * threshold;
        const bool in_right    = view_x >= vg.width * (1.0 - threshold);
        const bool in_top    = view_y < vg.height * threshold;
        const bool in_bottom = view_y >= vg.height * (1.0 - threshold);

        if (in_left || in_right || in_top || in_bottom)
        {
            uint32_t edges = 0;
            if (in_left)
            {
                edges |= WLR_EDGE_LEFT;
            } else if (in_right)
            {
                edges |= WLR_EDGE_RIGHT;
            }

            if (in_top)
            {
                edges |= WLR_EDGE_TOP;
            } else if (in_bottom)
            {
                edges |= WLR_EDGE_BOTTOM;
            }

            return edges;
        }

        uint32_t edges = 0;
        if (view_x < vg.width / 2)
        {
            edges |= WLR_EDGE_LEFT;
        } else
        {
            edges |= WLR_EDGE_RIGHT;
        }

        if (view_y < vg.height / 2)
        {
            edges |= WLR_EDGE_TOP;
        } else
        {
            edges |= WLR_EDGE_BOTTOM;
        }

        return edges;
    }

    bool initiate(wayfire_toplevel_view view, uint32_t forced_edges = 0)
    {
        if (!view || (view->role == wf::VIEW_ROLE_DESKTOP_ENVIRONMENT) ||
            !view->is_mapped() || view->pending_fullscreen())
        {
            return false;
        }

        this->edges = forced_edges ?: calculate_edges(
            view->get_bounding_box(), get_input_coords());

        if ((edges == 0) || !(view->get_allowed_actions() & wf::VIEW_ALLOW_RESIZE))
        {
            return false;
        }

        if (!is_using_touch && top_edge_double_click(edges, view->get_id(), get_input_coords(), now_ms()))
        {
            title_bar_double_click(view);                // the title bar's action, not a resize
            return false;
        }

        if (!output->activate_plugin(&grab_interface))
        {
            return false;
        }

        input_grab->set_wants_raw_input(true);
        input_grab->grab_input(wf::scene::layer::OVERLAY);

        grab_start = get_input_coords();
        moved = false;
        grabbed_geometry = view->get_geometry();
        /* (no longer tiled, the outline: on the first real move, start_resizing --
         * a click alone left a maximized window "unmaximized" and, with live off,
         * hid it behind a ghost at its old size: the double-click that followed
         * zoomed it with half a title bar, Vini) */

        this->view = view;

        auto og = view->get_bounding_box();
        int anchor_x = og.x;
        int anchor_y = og.y;

        if (edges & WLR_EDGE_LEFT)
        {
            anchor_x += og.width;
        }

        if (edges & WLR_EDGE_TOP)
        {
            anchor_y += og.height;
        }

        start_wobbly(view, anchor_x, anchor_y);
        wf::get_core().set_cursor(wlr_xcursor_get_resize_name((wlr_edges)edges));
        // Sonata: without its contents (the option off), only the background follows
        outline = !live;
        LOGI("sonata-resize: start, live=", (bool)live, " client=", was_client_request);
        return true;
    }

    /** The pointer really moves: the window leaves its tiling, the outline shows. */
    void start_resizing()
    {
        if (view->pending_tiled_edges())
        {
            view->toplevel()->pending().tiled_edges = 0;
        }

        if (outline)
        {
            begin_outline();
        }
    }

    void input_pressed(uint32_t state)
    {
        if (state != WLR_BUTTON_RELEASED)
        {
            return;
        }

        input_grab->ungrab_input();
        output->deactivate_plugin(&grab_interface);

        if (view && outline && moved)
        {
            end_outline();                               // the app gets its size now, once
        }

        outline = false;
        if (view)
        {
            end_wobbly(view);

            wf::view_change_workspace_signal workspace_may_changed;
            workspace_may_changed.view = this->view;
            workspace_may_changed.to   = output->wset()->get_current_workspace();
            workspace_may_changed.old_workspace_valid = false;
            output->emit(&workspace_may_changed);
        }
    }

    // Convert resize edges to gravity
    uint32_t calculate_gravity(uint32_t resize_edges)
    {
        uint32_t gravity = 0;
        if (resize_edges & WLR_EDGE_LEFT)
        {
            gravity |= WLR_EDGE_RIGHT;
        }

        if (resize_edges & WLR_EDGE_RIGHT)
        {
            gravity |= WLR_EDGE_LEFT;
        }

        if (resize_edges & WLR_EDGE_TOP)
        {
            gravity |= WLR_EDGE_BOTTOM;
        }

        if (resize_edges & WLR_EDGE_BOTTOM)
        {
            gravity |= WLR_EDGE_TOP;
        }

        return gravity;
    }

    wf::dimensions_t calculate_min_size()
    {
        // Min size, if not set to something larger, is 1x1 + decoration size
        wf::dimensions_t min_size = view->toplevel()->get_min_size();
        min_size.width  = std::max(1, min_size.width);
        min_size.height = std::max(1, min_size.height);
        min_size = wf::containing_size(wf::expand_dimensions_by_margins(
            wf::dimensionsf_t{min_size}, view->toplevel()->pending().margins));

        min_size.width  = std::max(min_size.width, static_cast<int>(user_min_width));
        min_size.height = std::max(min_size.height, static_cast<int>(user_min_height));

        return min_size;
    }

    wf::dimensions_t calculate_max_size(wf::dimensions_t min)
    {
        // Max size is whatever is set by the client, if not set, then it is MAX_INT
        wf::dimensions_t max_size = view->toplevel()->get_max_size();
        int64_t width, height;
        const int horizontal_margins =
            std::ceil(view->toplevel()->pending().margins.left + view->toplevel()->pending().margins.right);
        const int vertical_margins =
            std::ceil(view->toplevel()->pending().margins.top + view->toplevel()->pending().margins.bottom);

        if (max_size.width > 0)
        {
            width = static_cast<int64_t>(max_size.width) + horizontal_margins;
        } else
        {
            width = std::numeric_limits<decltype(max_size.width)>::max();
        }

        if (max_size.height > 0)
        {
            height = static_cast<int64_t>(max_size.height) + vertical_margins;
        } else
        {
            height = std::numeric_limits<decltype(max_size.height)>::max();
        }

        // Sanitize values in case desired.width/height gets negative for example.
        max_size.width = static_cast<decltype(max_size.width)>(std::clamp(width,
            static_cast<int64_t>(min.width),
            static_cast<int64_t>(std::numeric_limits<decltype(max_size.width)>::max())));
        max_size.height = static_cast<decltype(max_size.height)>(std::clamp(height,
            static_cast<int64_t>(min.height),
            static_cast<int64_t>(std::numeric_limits<decltype(max_size.height)>::max())));

        return max_size;
    }

    bool does_shrink(int dx, int dy)
    {
        if (std::abs(dx) > std::abs(dy))
        {
            return dx < 0;
        } else
        {
            return dy < 0;
        }
    }

    void title_bar_double_click(wayfire_toplevel_view v)
    {
        std::string what = double_click;
        LOGI("sonata-resize: double-click on the top edge: ", what);
        if (what == "minimize")
        {
            wf::get_core().default_wm->minimize_request(v, true);
        } else if (what != "none")
        {
            bool zoomed = v->pending_tiled_edges() == wf::TILED_EDGES_ALL;
            wf::get_core().default_wm->tile_request(v, zoomed ? 0 : wf::TILED_EDGES_ALL);
        }
    }

    void input_motion()
    {
        auto input = get_input_coords();
        int dx     = (int)input.x - (int)grab_start.x;
        int dy     = (int)input.y - (int)grab_start.y;
        if (!moved)
        {
            if ((std::abs(dx) < DEAD_ZONE) && (std::abs(dy) < DEAD_ZONE))
            {
                return;                                  // a click: the size stays
            }

            moved = true;
            start_resizing();
        }

        wf::geometry_t desired = grabbed_geometry;
        double ratio = 1.0;
        if (preserve_aspect)
        {
            ratio = (double)desired.width / desired.height;
        }

        if (edges & WLR_EDGE_LEFT)
        {
            desired.x     += dx;
            desired.width -= dx;
        } else if (edges & WLR_EDGE_RIGHT)
        {
            desired.width += dx;
        }

        if (edges & WLR_EDGE_TOP)
        {
            desired.y += dy;
            desired.height -= dy;
        } else if (edges & WLR_EDGE_BOTTOM)
        {
            desired.height += dy;
        }

        auto min_size = calculate_min_size();
        auto max_size = calculate_max_size(min_size);
        auto desired_unconstrained = desired;
        if (preserve_aspect)
        {
            float new_ratio = 1.0 * desired.width / desired.height;
            if ((new_ratio < ratio) ^ does_shrink(desired.width - grabbed_geometry.width,
                desired.height - grabbed_geometry.height))
            {
                // If we do not shrink: the window is taller than it should be, so expand width first.
                // If we do shrink: the window is wider than it should be, so shrink width first.
                desired.width = std::clamp(int(desired.height * ratio),
                    min_size.width, max_size.width);
                desired.height = std::clamp(int(desired.width / ratio),
                    min_size.height, max_size.height);
                // Clamp once more to ensure we fit the min/max sizes.
                desired.width = std::clamp(int(desired.height * ratio),
                    min_size.width, max_size.width);
            } else
            {
                // The window is wider than it should be, so expand height first.
                desired.height = std::clamp(int(desired.width / ratio),
                    min_size.height, max_size.height);
                desired.width = std::clamp(int(desired.height * ratio),
                    min_size.width, max_size.width);
                // Clamp once more to ensure we fit the min/max sizes.
                desired.height = std::clamp(int(desired.width / ratio),
                    min_size.height, max_size.height);
            }
        } else
        {
            desired.width  = wf::clamp(desired.width, min_size.width, max_size.width);
            desired.height = wf::clamp(desired.height, min_size.height, max_size.height);
        }

        // If we had to change the size due to ratio/min/max constraints, make sure to keep the gravity
        // correct.
        if (edges & WLR_EDGE_LEFT)
        {
            desired.x += desired_unconstrained.width - desired.width;
        }

        if (edges & WLR_EDGE_TOP)
        {
            desired.y += desired_unconstrained.height - desired.height;
        }

        if (outline)
        {
            ghost_geometry = desired;
            if (ghost)
            {
                ghost->set(visible(view, desired));
            }

            return;
        }

        if (wf::fdimensions(view->toplevel()->pending().geometry) != wf::fdimensions(desired))
        {
            view->toplevel()->pending().gravity  = calculate_gravity(edges);
            view->toplevel()->pending().geometry = desired;
            wf::get_core().tx_manager->schedule_object(view->toplevel());
        }
    }

    void fini() override
    {
        if (input_grab->is_grabbed())
        {
            input_pressed(WLR_BUTTON_RELEASED);
        }

        output->rem_binding(&activate_binding);
        output->rem_binding(&activate_binding_preserve_aspect);
        refit_timer.disconnect();
        frame_timer.disconnect();
        fit_timer.disconnect();
        for (auto& v : wf::get_core().get_all_views())
        {
            if (auto t = toplevel_cast(v); t && (t->get_output() == output))
            {
                remove_edge_grab(t);
            }
        }

        end_zoom(false);
        finish_fade();
        close_ghost();
    }

    /** Maximize / restore asked (before the window gets its new geometry). */
    void start_zoom(wayfire_toplevel_view v, wf::geometry_t hint, uint32_t to_tiled)
    {
        /* resizing now: the grab, never `view` -- it keeps the last window
         * resized after the release (Vini: once a window had been resized,
         * maximize / restore never zoomed again; "no zoom (resizing)") */
        bool resizing = input_grab->is_grabbed();
        if (!zoom_enabled || !v || !v->is_mapped() || resizing || v->pending_fullscreen() ||
            (v->get_output() != output) || output->is_plugin_active("move"))
        {
            /* (Vini: some sizes never zoomed -- which reason, in session.log) */
            if (v && zoom_enabled)
            {
                LOGI("sonata-resize: no zoom (", resizing ? "resizing" : "",
                    v->pending_fullscreen() ? "fullscreen" : "", (v->get_output() != output) ? "other display" : "",
                    output->is_plugin_active("move") ? "moving" : "", ")");
            }

            return;      // (a resize keeps its own ghost; a maximized window dragged off its place follows the pointer)
        }

        end_zoom(false);
        finish_fade();
        close_ghost();
        zooming   = v;
        zoom_from  = v->get_geometry();
        zoom_hint  = hint;
        zoom_tiled = (int)to_tiled;
        if (!wf::get_core().get_data<ghost_program_t>())
        {
            wf::get_core().store_data(std::make_unique<ghost_program_t>());
        }

        ghost = std::make_shared<ghost_node_t>();
        ghost->fill   = fill;
        ghost->border = border;
        ghost->radius = std::max(0, (int)corner_radius);
        zoom_seen     = visible(v, zoom_from);           // the window as seen, not its shadow
        ghost->rect   = zoom_seen;
        wf::scene::add_front(output->node_for_layer(wf::scene::layer::TOP), ghost);
        ghost->damage();
        set_alpha(v, 0.0);                               // only the frame shows while it moves
        zoom.animate(0.0, 1.0);
        output->render->add_effect(&zoom_hook, wf::OUTPUT_EFFECT_PRE);
        output->render->schedule_redraw();
    }

    /** Where the window goes: its pending geometry once the tile is decided. */
    wf::geometry_t zoom_target()
    {
        auto g = zooming->toplevel()->pending().geometry;
        if ((g.width <= 0) || (g.height <= 0) || (g == zoom_from))
        {
            return (zoom_hint.width > 0) ? zoom_hint : g;
        }

        return g;
    }

    void zoom_step()
    {
        if (!zooming)
        {
            return;
        }

        double t = zoom;
        auto to  = zoom_target();
        if (ghost)
        {
            // from the window as seen to the window as it will be seen (tiled: no shadow)
            auto a = zoom_seen, b = visible(zooming, to, zoom_tiled);
            ghost->set({
                (int)std::round(a.x + (b.x - a.x) * t), (int)std::round(a.y + (b.y - a.y) * t),
                std::max(1, (int)std::round(a.width + (b.width - a.width) * t)),
                std::max(1, (int)std::round(a.height + (b.height - a.height) * t))});
        }

        if (!zoom.running())
        {
            end_zoom(true);
            return;
        }

        output->render->schedule_redraw();
    }

    /** The frame has arrived: the contents fade in when the app has drawn
     * itself at the new size (the outline resize's fade, at most 400 ms later). */
    void end_zoom(bool fade_in)
    {
        if (!zooming)
        {
            return;
        }

        output->render->rem_effect(&zoom_hook);
        auto v = zooming;
        zooming = nullptr;
        if (!fade_in)
        {
            set_alpha(v, 1.0);
            v->get_transformed_node()->rem_transformer(FADE);
            close_ghost();
            return;
        }

        fading = v;
        fade_started = false;
        fade_requested_at = wf::get_current_time();
        fading->connect(&on_new_size);
        output->render->add_effect(&fade_hook, wf::OUTPUT_EFFECT_PRE);
        if (wf::dimensions(v->get_geometry()) == wf::dimensions(v->toplevel()->pending().geometry))
        {
            start_fade();                                // already drawn at its new size
        }

        output->render->schedule_redraw();
    }

    // -- Sonata ------------------------------------------------------------------------

    void begin_outline()
    {
        finish_fade();
        ghost_geometry = view->get_geometry();
        if (!wf::get_core().get_data<ghost_program_t>())
        {
            wf::get_core().store_data(std::make_unique<ghost_program_t>());
        }

        ghost = std::make_shared<ghost_node_t>();
        ghost->fill   = fill;
        ghost->border = border;
        ghost->radius = std::max(0, (int)corner_radius);
        ghost->rect   = visible(view, ghost_geometry);
        wf::scene::add_front(output->node_for_layer(wf::scene::layer::TOP), ghost);
        ghost->damage();
        set_alpha(view, 0.0);                            // only the background shows while dragging
        LOGI("sonata-resize: contents hidden while resizing ", ghost_geometry);
    }

    void end_outline()
    {
        auto geometry = ghost_geometry;
        fading = view;
        fade_started = false;
        fade_requested_at = wf::get_current_time();
        fading->connect(&on_new_size);
        output->render->add_effect(&fade_hook, wf::OUTPUT_EFFECT_PRE);
        if (wf::fdimensions(view->toplevel()->pending().geometry) != wf::fdimensions(geometry) ||
            (wf::origin(view->toplevel()->pending().geometry) != wf::origin(geometry)))
        {
            view->toplevel()->pending().gravity  = calculate_gravity(edges);
            view->toplevel()->pending().geometry = geometry;
            wf::get_core().tx_manager->schedule_object(view->toplevel());
        } else
        {
            start_fade();                                // nothing changed
        }

        output->render->schedule_redraw();
    }

    void start_fade()
    {
        fade_started = true;                             // (first: redraw_frame's signal comes back here)
        if (fading)
        {
            redraw_frame(fading);                        // its frame at the new size as it fades in
        }

        fade.animate(0.0, 1.0);
    }

    void fade_step()
    {
        if (!fading)
        {
            return;
        }

        // a slow (or frozen) app: show it anyway after a moment
        if (!fade_started && (wf::get_current_time() - fade_requested_at > 400))
        {
            start_fade();
        }

        if (fade_started)
        {
            double a = fade;
            set_alpha(fading, a);
            if (ghost)
            {
                ghost->alpha = 1.0 - a;                  // the background gives way to the window
                ghost->set(visible(fading, fading->get_geometry()));
            }

            if (!fade.running())
            {
                finish_fade();
                close_ghost();
                return;
            }
        }

        output->render->schedule_redraw();
    }

    void finish_fade()
    {
        if (fading)
        {
            fading->get_transformed_node()->rem_transformer(FADE);
            on_new_size.disconnect();
            redraw_frame(fading);
            check_frame_later(fading);
            fading = nullptr;
        }

        output->render->rem_effect(&fade_hook);
    }

    /** redraw_frame again FRAME_CHECKS_MS later (each), for apps that draw
     * themselves at the new size only after the fade */
    static constexpr int FRAME_CHECKS_MS[] = {250, 750, 2000};

    void check_frame_later(wayfire_toplevel_view v)
    {
        frame_view   = v->shared_from_this();
        frame_checks = 0;
        frame_timer.disconnect();
        frame_timer.set_timeout(FRAME_CHECKS_MS[0], [=] { frame_check(); });
    }

    void frame_check()
    {
        auto v = toplevel_cast(frame_view.lock());
        if (!v || !v->is_mapped())
        {
            return;
        }

        redraw_frame(v);
        /* (Vini: after a resize, Claude's frame stayed at the old size while
         * the app drew itself bigger -- what Wayfire and the app each say) */
        auto s  = v->get_wlr_surface();
        auto pg = v->toplevel()->pending().geometry;
        LOGI("sonata-resize: frame check ", frame_checks, " ", v->get_app_id(), " geometry ", v->get_geometry(),
            " pending ", pg, " surface ", s ? s->current.width : -1, "x", s ? s->current.height : -1);
        if (++frame_checks < (int)(sizeof(FRAME_CHECKS_MS) / sizeof(FRAME_CHECKS_MS[0])))
        {
            frame_timer.set_timeout(FRAME_CHECKS_MS[frame_checks] - FRAME_CHECKS_MS[frame_checks - 1],
                [=] { frame_check(); });
        }
    }

    /** The title bar drawn again at the window's size, now. Vini: zoomed
     * from its title bar, a terminal / Claude (pixdecor) kept its title bar
     * cut at the old width -- pixdecor redraws only the part of its frame it
     * worked out for the size it last saw; telling it the geometry changed
     * makes it work it out again, at the size the window has now. */
    void redraw_frame(wayfire_toplevel_view v)
    {
        wf::view_geometry_changed_signal ev;
        ev.view = v;
        ev.old_geometry = v->get_geometry();
        v->emit(&ev);
        v->damage();
    }

    void close_ghost()
    {
        if (ghost)
        {
            ghost->damage();
            wf::scene::remove_child(ghost);
            ghost = nullptr;
        }
    }

    void set_alpha(wayfire_toplevel_view v, double alpha)
    {
        auto tmgr = v->get_transformed_node();
        auto tr   = tmgr->get_transformer<wf::scene::view_2d_transformer_t>(FADE);
        if (!tr)
        {
            tr = std::make_shared<wf::scene::view_2d_transformer_t>(v);
            tmgr->add_transformer(tr, wf::TRANSFORMER_2D, FADE);
        }

        tr->alpha = alpha;
        v->damage();
    }
};

DECLARE_WAYFIRE_PLUGIN(wf::per_output_plugin_t<wayfire_resize>);
