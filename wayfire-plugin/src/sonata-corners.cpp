/*
 * sonata-corners: rounded corners for windows Wayfire decorates (Chrome,
 * Spotify, terminals, X11 apps -- anything drawn by pixdecor or Wayfire's
 * own decoration). The decoration rounds its own frame, but the app's
 * content is a rectangle that reaches the bottom corners; this plugin clips
 * all four corners of the window to `sonata-corners/radius`, so they match
 * Sonata's own windows (which round themselves: client-side decorated
 * windows are left alone). Fullscreen windows are never touched (no extra
 * pass, direct scan-out stays possible for games and video).
 *
 * Based on the structure of the keycolor plugin from wayfire-plugins-extra
 * (Scott Moreau, MIT).
 *
 * The MIT License (MIT)
 * Copyright (c) 2026 Vinicius Oliveira (Sonata 2)
 *
 * Permission is hereby granted, free of charge, to any person obtaining a copy
 * of this software and associated documentation files (the "Software"), to deal
 * in the Software without restriction, including without limitation the rights
 * to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
 * copies of the Software, and to permit persons to whom the Software is
 * furnished to do so, subject to the following conditions:
 *
 * The above copyright notice and this permission notice shall be included in all
 * copies or substantial portions of the Software.
 *
 * THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
 * IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
 * FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
 * AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
 * LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
 * OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
 * SOFTWARE.
 */

#include <map>
#include <wayfire/render-manager.hpp>
#include <wayfire/output-layout.hpp>
#include <algorithm>
#include <chrono>
#include <memory>
#include <string>

#include <wayfire/core.hpp>
#include <wayfire/scene.hpp>
#include <vector>
#include <wayfire/opengl.hpp>
#include <wayfire/view.hpp>
#include <wayfire/toplevel-view.hpp>
#include <wayfire/plugin.hpp>
#include <wayfire/output.hpp>
#include <wayfire/view-transform.hpp>
#include <wayfire/signal-definitions.hpp>
#include <wayfire/config/config-manager.hpp>
#include <wayfire/render.hpp>
#include <wayfire/util.hpp>
#include <sys/stat.h>
#include <ctime>
#include <drm_fourcc.h>

extern "C"
{
#include <wlr/render/drm_format_set.h>
#include <wlr/render/wlr_renderer.h>
#include <wlr/types/wlr_ext_foreign_toplevel_list_v1.h>
#include <wlr/types/wlr_ext_image_capture_source_v1.h>
#include <wlr/interfaces/wlr_ext_image_capture_source_v1.h>
#include <wlr/types/wlr_ext_image_copy_capture_v1.h>
}

static const char *vertex_shader =
    R"(
#version 100

attribute highp vec2 position;
attribute highp vec2 texcoord;

varying highp vec2 uvpos;

uniform mat4 mvp;

void main() {
   gl_Position = mvp * vec4(position.xy, 0.0, 1.0);
   uvpos = texcoord;
}
)";

/* The window's frame is `rect` (x, y, w, h in logical pixels, from the top
 * left of the texture); pixels inside it but beyond a corner's arc become
 * transparent (antialiased over one pixel). Pixels outside the frame (the
 * decoration's shadow) are left as they are. The shape is symmetric, so the
 * texture's vertical orientation doesn't matter as long as the shadow is. */
static const char *fragment_shader =
    R"(
#version 100
@builtin_ext@
@builtin@

precision highp float;

uniform vec2 size;
uniform vec4 rect;
uniform float radius;
uniform vec4 shadow;          /* pixdecor's shadow colour (premultiplied), 0 without it */
uniform float shadow_radius;
uniform float square_top;     /* 1: maximized, the top corners meet the menu bar */
uniform vec4 fill;            /* the title bar's colour (premultiplied) */
uniform vec4 outline;         /* hairline along the rounded frame (premultiplied), 0: none */

varying highp vec2 uvpos;

void main()
{
    vec4 c = get_pixel(uvpos);
    vec2 p = vec2(uvpos.x * size.x, (1.0 - uvpos.y) * size.y);
    vec2 lo = rect.xy;
    vec2 hi = rect.xy + rect.zw;
    bool top = p.y < lo.y + radius;
    if (square_top > 0.5 && top && p.x >= lo.x && p.x <= hi.x && p.y >= lo.y)
    {
        /* maximized: square top corners, so the title bar and the menu bar
         * read as one glass -- fill the hole pixdecor's rounded frame left */
        vec2 q = clamp(p, lo + vec2(radius), hi - vec2(radius));
        float outside = clamp(length(p - q) - radius + 0.5, 0.0, 1.0);
        c = c * (1.0 - outside) + fill * outside;
    }
    else if (radius > 0.0 && p.x >= lo.x && p.y >= lo.y && p.x <= hi.x && p.y <= hi.y)
    {
        vec2 q = clamp(p, lo + vec2(radius), hi - vec2(radius));
        float d = length(p - q);
        float cover = clamp(radius + 0.5 - d, 0.0, 1.0);
        /* beyond the arc: the decoration's shadow, as pixdecor draws it
         * there (it was hidden under the window's square corner) */
        float da = max(0.0, d - radius);
        float k = shadow_radius > 0.0 ? exp(-pow(da / shadow_radius, 2.0)) : 0.0;
        c = c * cover + shadow * k * (1.0 - cover);
    }
    if (outline.a > 0.0 && square_top < 0.5)
    {
        /* macOS hairline: 1 px inside the rounded frame (replaces pixdecor's
         * square border, which clashed with the arcs) */
        vec2 half_size = (hi - lo) * 0.5;
        vec2 dd = abs(p - (lo + half_size)) - (half_size - vec2(radius));
        float sd = length(max(dd, 0.0)) + min(max(dd.x, dd.y), 0.0) - radius;
        float ring = clamp(1.0 - abs(sd + 0.5), 0.0, 1.0) * step(sd, 0.5);
        c = outline * ring + c * (1.0 - outline.a * ring);
    }
    gl_FragColor = c;
}
)";

static const std::string program_name = "sonata_corners_program";
static int program_ref_count;

namespace wf
{
namespace scene
{
namespace sonata_corners
{
class corners_program_t : public wf::custom_data_t
{
  public:
    OpenGL::program_t program;
};

/* Shadow around pixdecor's frame (part of the window geometry): the frame
 * itself is inset by it. 0 without pixdecor's rounded engine, or when a
 * tiled window has no shadow. */
static double decoration_shadow(wayfire_toplevel_view view)
{
    auto& cfg = *wf::get_core().config;
    auto engine = cfg.get_option("pixdecor/overlay_engine");
    auto radius = cfg.get_option("pixdecor/shadow_radius");
    auto max_shadows = cfg.get_option("pixdecor/maximized_shadows");
    if (!engine || !radius || (engine->get_value_str() != "rounded_corners"))
    {
        return 0;
    }

    bool tiled = view->pending_tiled_edges() != 0;
    if (tiled && (!max_shadows || (max_shadows->get_value_str() != "true")))
    {
        return 0;
    }

    try {
        return 2.0 * std::stoi(radius->get_value_str());
    } catch (...)
    {
        return 0;
    }
}

static std::string option_str(const std::string& name)
{
    auto opt = wf::get_core().config->get_option(name);
    return opt ? opt->get_value_str() : "";
}

/* pixdecor's shadow colour (premultiplied) and radius, when its rounded
 * engine draws a shadow; zeros otherwise */
static void decoration_shadow_style(glm::vec4& color, float& radius)
{
    color  = glm::vec4{0, 0, 0, 0};
    radius = 0;
    if (option_str("pixdecor/overlay_engine") != "rounded_corners")
    {
        return;
    }

    auto col = wf::option_type::from_string<wf::color_t>(option_str("pixdecor/shadow_color"));
    if (col)
    {
        color = glm::vec4{col->r * col->a, col->g * col->a, col->b * col->a, col->a};
    }

    try {
        radius = std::stoi(option_str("pixdecor/shadow_radius"));
    } catch (...)
    {
        radius = 0;
    }
}

/* The radius option, read without option_wrapper_t: a wrapper throws (and
 * aborts Wayfire) when the plugin's XML wasn't loaded when Wayfire started. */
static float corner_radius()
{
    /* pixdecor's own corner radius when it rounds the frame: the arcs meet */
    if (option_str("pixdecor/overlay_engine") == "rounded_corners")
    {
        try {
            return std::max(0, std::stoi(option_str("pixdecor/rounded_corner_radius")));
        } catch (...)
        {}
    }

    auto opt = wf::get_core().config->get_option("sonata-corners/radius");
    if (!opt)
    {
        return 10;
    }

    try {
        return std::max(0, std::stoi(opt->get_value_str()));
    } catch (...)
    {
        return 10;
    }
}

class corners_render_instance_t :
    public wf::scene::transformer_render_instance_t<transformer_base_node_t>
{
    wf::signal::connection_t<node_damage_signal> on_node_damaged =
        [=] (node_damage_signal *ev)
    {
        push_to_parent(ev->region);
    };

    transformer_base_node_t *self;
    wayfire_toplevel_view view;
    damage_callback push_to_parent;

  public:
    corners_render_instance_t(transformer_base_node_t *self, damage_callback push_damage,
        wayfire_toplevel_view view) :
        wf::scene::transformer_render_instance_t<transformer_base_node_t>(self, push_damage,
            view->get_output())
    {
        this->self = self;
        this->view = view;
        this->push_to_parent = push_damage;
        self->connect(&on_node_damaged);
    }

    void schedule_instructions(std::vector<render_instruction_t>& instructions,
        const wf::render_target_t& target, wf::regionf_t& damage) override
    {
        instructions.push_back(render_instruction_t{
                        .instance = this,
                        .target   = target,
                        .damage   = damage & self->get_bounding_box(),
                    });
    }

    void render(const wf::scene::render_instruction_t& data) override
    {
        /* Drawn like Wayfire's own textures: the window's box in logical
         * coordinates through the target's orthographic projection, over the
         * whole render buffer. (A hand-made glViewport over the box flipped
         * it vertically inside the output: the window showed up lower than
         * where it really is, and the pointer "missed" -- Chrome, Spotify.) */
        auto bbox = self->get_children_bounding_box();
        const float x1 = bbox.x, y1 = bbox.y, x2 = bbox.x + bbox.width, y2 = bbox.y + bbox.height;

        /* the frame, relative to the texture's top left */
        auto g = view->get_geometry();
        double inset = decoration_shadow(view);
        float rx = g.x + inset - bbox.x;
        float ry = g.y + inset - bbox.y;
        float rw = g.width - 2 * inset;
        float rh = g.height - 2 * inset;
        float radius = corner_radius();

        auto data_ptr = wf::get_core().get_data<corners_program_t>(program_name);
        const float vertexData[] = {
            x1, y2,
            x2, y2,
            x2, y1,
            x1, y1,
        };
        static const float texCoords[] = {
            0.0f, 0.0f,
            1.0f, 0.0f,
            1.0f, 1.0f,
            0.0f, 1.0f
        };

        data.pass->custom_gles_subpass(data.target, [&]
        {
            /* Always the children rendered to the node's buffer: the zero-copy
             * path hands out the client buffer as is (no decoration) */
            auto src_tex = self->get_updated_contents(bbox, data.target.scale, this->children);
            auto gl_tex  = wf::gles_texture_t{src_tex};

            data_ptr->program.use(gl_tex.type);
            data_ptr->program.uniform2f("size", bbox.width, bbox.height);
            data_ptr->program.uniform4f("rect", glm::vec4{rx, ry, rw, rh});
            data_ptr->program.uniform1f("radius", radius);
            glm::vec4 shadow_color;
            float shadow_r;
            decoration_shadow_style(shadow_color, shadow_r);
            if (inset <= 0)
            {
                /* no shadow around this frame (maximized, tiled): nothing
                 * beyond the arc -- a tint there showed as a dark square */
                shadow_color = glm::vec4{0, 0, 0, 0};
                shadow_r     = 0;
            }
            bool maximized = view->pending_tiled_edges() == wf::TILED_EDGES_ALL;
            data_ptr->program.uniform1f("square_top", maximized ? 1.0f : 0.0f);
            auto fill = wf::option_type::from_string<wf::color_t>(
                option_str(view->activated ? "pixdecor/fg_color" : "pixdecor/bg_color"));
            /* stored premultiplied (Sonata writes them so: pixdecor blends them as such) */
            data_ptr->program.uniform4f("fill", fill ? glm::vec4{fill->r, fill->g, fill->b, fill->a} :
                glm::vec4{0, 0, 0, 0});
            auto line = wf::option_type::from_string<wf::color_t>(option_str("sonata-corners/outline"));
            data_ptr->program.uniform4f("outline", line ?
                glm::vec4{line->r * line->a, line->g * line->a, line->b * line->a, line->a} :
                glm::vec4{0, 0, 0, 0});
            data_ptr->program.uniform4f("shadow", shadow_color);
            data_ptr->program.uniform1f("shadow_radius", shadow_r);
            data_ptr->program.attrib_pointer("position", 2, 0, vertexData);
            data_ptr->program.attrib_pointer("texcoord", 2, 0, texCoords);
            data_ptr->program.uniformMatrix4f("mvp", wf::gles::render_target_orthographic_projection(data.target));
            GL_CALL(glActiveTexture(GL_TEXTURE0));
            data_ptr->program.set_active_texture(gl_tex);

            wf::gles::bind_render_buffer(data.target);
            GL_CALL(glEnable(GL_BLEND));
            GL_CALL(glBlendFunc(GL_ONE, GL_ONE_MINUS_SRC_ALPHA));
            for (const auto& box : data.damage)
            {
                wf::gles::render_target_logic_scissor(data.target, box);
                GL_CALL(glDrawArrays(GL_TRIANGLE_FAN, 0, 4));
            }

            GL_CALL(glDisable(GL_BLEND));
            GL_CALL(glActiveTexture(GL_TEXTURE0));
            GL_CALL(glBindTexture(GL_TEXTURE_2D, 0));
            GL_CALL(glBindFramebuffer(GL_FRAMEBUFFER, 0));
            data_ptr->program.deactivate();
        });
    }
};

/* A plain transformer node: no geometric transform at all, so pointer
 * input reaches the window exactly where it is drawn (view_2d_transformer_t,
 * used before, maps input through its own transform). */
class corners_node_t : public wf::scene::transformer_base_node_t, public wf::scene::opaque_region_node_t
{
    wayfire_toplevel_view view;

  public:
    corners_node_t(wayfire_toplevel_view view) : wf::scene::transformer_base_node_t(false)
    {
        this->view = view;
    }

    /* Pass the window's opaque region on (minus the rounded corners): the
     * blur transformer around this one only blurs behind what isn't opaque.
     * Without it every decorated window was blurred whole, every frame --
     * a maximized browser cost the compositor ~8 ms a frame. */
    wf::regionf_t get_opaque_region() const override
    {
        if (get_children().empty())
        {
            return {};
        }

        auto inner = dynamic_cast<wf::scene::opaque_region_node_t*>(get_children().front().get());
        if (!inner)
        {
            return {};
        }

        wf::regionf_t region = inner->get_opaque_region();
        auto b = get_children_bounding_box();
        const float c = 16;                        /* at least the corner radius */
        for (auto corner : {wf::geometry_t{(int)b.x, (int)b.y, (int)c, (int)c},
                            wf::geometry_t{(int)(b.x + b.width - c), (int)b.y, (int)c, (int)c},
                            wf::geometry_t{(int)b.x, (int)(b.y + b.height - c), (int)c, (int)c},
                            wf::geometry_t{(int)(b.x + b.width - c), (int)(b.y + b.height - c), (int)c, (int)c}})
        {
            region ^= wf::regionf_t{wf::geometry_t{corner}};
        }

        return region;
    }

    std::string stringify() const override
    {
        return "sonata-corners";
    }

    void gen_render_instances(std::vector<render_instance_uptr>& instances,
        damage_callback push_damage, wf::output_t *shown_on) override
    {
        instances.push_back(std::make_unique<corners_render_instance_t>(this, push_damage, view));
    }
};


/* ---- Window sharing ---------------------------------------------------------------------------
 * Screen sharing apps (through xdg-desktop-portal-wlr) can share a single
 * window when the compositor offers a capture source per toplevel
 * (ext-foreign-toplevel-image-capture-source-v1). Wayfire offers the
 * per-display ones only: this adds the per-window ones. A source renders
 * the window's contents (its surfaces, no title bar or shadow) into a
 * buffer whenever the capture asks for a frame; wlroots copies that into
 * the app's buffer. Needs Wayfire's ext-toplevel plugin (the window list
 * the portal picks from). */
struct window_source_t
{
    wlr_ext_image_capture_source_v1 base; /* first: wl_container_of */
    std::weak_ptr<wf::view_interface_t> view;
    wf::auxilliary_buffer_t buffer;
    int started = 0;
    bool pending = false;
    wf::wl_idle_call idle;
    wf::signal::connection_t<wf::view_unmapped_signal> on_unmap;

    static window_source_t *from(wlr_ext_image_capture_source_v1 *b)
    {
        return reinterpret_cast<window_source_t*>(b);
    }

    void set_constraints(int w, int h)
    {
        base.width  = w;
        base.height = h;
        if (!base.shm_formats)
        {
            base.shm_formats     = (uint32_t*)calloc(2, sizeof(uint32_t));
            base.shm_formats[0]  = DRM_FORMAT_ARGB8888;
            base.shm_formats[1]  = DRM_FORMAT_XRGB8888;
            base.shm_formats_len = 2;
        }

        /* shared memory only: wlroots reads the window's picture back into
         * the app's buffer (reliable everywhere; a window is small enough) */
        wl_signal_emit_mutable(&base.events.constraints_update, nullptr);
    }

    /* the window's current picture, then a frame event (full damage) */
    void produce()
    {
        pending = false;
        auto v = view.lock();
        if (!v || !v->is_mapped() || !v->get_output() || !started)
        {
            return;
        }

        v->take_snapshot(buffer);
        auto size = buffer.get_size();
        if ((size.width <= 0) || (size.height <= 0))
        {
            return;
        }

        if (((int)base.width != size.width) || ((int)base.height != size.height))
        {
            set_constraints(size.width, size.height);
        }

        pixman_region32_t damage;
        pixman_region32_init_rect(&damage, 0, 0, size.width, size.height);
        wlr_ext_image_capture_source_v1_frame_event ev{};
        ev.damage = &damage;
        wl_signal_emit_mutable(&base.events.frame, &ev);
        pixman_region32_fini(&damage);
    }
};

static void window_source_start(wlr_ext_image_capture_source_v1 *b, bool)
{
    window_source_t::from(b)->started++;
}

static void window_source_stop(wlr_ext_image_capture_source_v1 *b)
{
    auto s = window_source_t::from(b);
    s->started = std::max(0, s->started - 1);
}

static void window_source_request_frame(wlr_ext_image_capture_source_v1 *b, bool)
{
    auto s = window_source_t::from(b);
    if (!s->pending)
    {
        s->pending = true;              /* after this request returns */
        s->idle.run_once([s] () { s->produce(); });
    }
}

static void window_source_copy_frame(wlr_ext_image_capture_source_v1 *b,
    wlr_ext_image_copy_capture_frame_v1 *frame, wlr_ext_image_capture_source_v1_frame_event*)
{
    auto s = window_source_t::from(b);
    if (s->buffer.get_buffer() &&
        wlr_ext_image_copy_capture_frame_v1_copy_buffer(frame, s->buffer.get_buffer(), wf::get_core().renderer))
    {
        timespec now;
        clock_gettime(CLOCK_MONOTONIC, &now);
        wlr_ext_image_copy_capture_frame_v1_ready(frame, WL_OUTPUT_TRANSFORM_NORMAL, &now);
    }
}

static const wlr_ext_image_capture_source_v1_interface window_source_impl = {
    .start = window_source_start,
    .stop  = window_source_stop,
    .request_frame = window_source_request_frame,
    .copy_frame    = window_source_copy_frame,
    .get_pointer_cursor = nullptr,
};

class window_capture_t
{
    std::map<wf::view_interface_t*, std::unique_ptr<window_source_t>> sources;
    wl_listener on_request;

    static window_capture_t*& instance()
    {
        static window_capture_t *self = nullptr;
        return self;
    }

    static void handle_request(wl_listener *listener, void *data)
    {
        auto self = instance();
        auto req  = static_cast<wlr_ext_foreign_toplevel_image_capture_source_manager_v1_request*>(data);
        auto raw  = self ? static_cast<wf::view_interface_t*>(req->toplevel_handle->data) : nullptr;
        wayfire_view view;
        for (auto& v : wf::get_core().get_all_views())
        {
            if (v.get() == raw)
            {
                view = v;
            }
        }

        if (!view)
        {
            /* a window gone meanwhile: an empty source that never produces a frame */
            static window_source_t *inert = [] ()
            {
                auto w = new window_source_t();
                wlr_ext_image_capture_source_v1_init(&w->base, &window_source_impl);
                return w;
            }();
            wlr_ext_foreign_toplevel_image_capture_source_manager_v1_request_accept(req, &inert->base);
            return;
        }

        auto it = self->sources.find(raw);
        if (it == self->sources.end())
        {
            auto src = std::make_unique<window_source_t>();
            wlr_ext_image_capture_source_v1_init(&src->base, &window_source_impl);
            src->view = view->shared_from_this();
            auto g = view->get_surface_root_node()->get_bounding_box();
            src->set_constraints(std::max(1, (int)g.width), std::max(1, (int)g.height));
            src->on_unmap = [self, raw] (wf::view_unmapped_signal*)
            {
                /* the window closed: its capture ends */
                auto found = self->sources.find(raw);
                if (found != self->sources.end())
                {
                    auto dead = std::move(found->second);
                    self->sources.erase(found);
                    wlr_ext_image_capture_source_v1_finish(&dead->base);
                }
            };
            view->connect(&src->on_unmap);
            it = self->sources.emplace(raw, std::move(src)).first;
        }

        wlr_ext_foreign_toplevel_image_capture_source_manager_v1_request_accept(req, &it->second->base);
    }

  public:
    void init()
    {
        /* one global per compositor run, even if the plugin is reloaded */
        static wlr_ext_foreign_toplevel_image_capture_source_manager_v1 *manager =
            wlr_ext_foreign_toplevel_image_capture_source_manager_v1_create(wf::get_core().display, 1);
        instance() = this;
        if (manager)
        {
            on_request.notify = handle_request;
            wl_signal_add(&manager->events.new_request, &on_request);
            LOGI("sonata-corners: window sharing (ext-foreign-toplevel-image-capture-source-v1) ready");
        }
    }

    void fini()
    {
        if (instance() == this)
        {
            wl_list_remove(&on_request.link);
            instance() = nullptr;
        }

        for (auto& [v, src] : sources)
        {
            wlr_ext_image_capture_source_v1_finish(&src->base);
        }

        sources.clear();
    }
};

class sonata_corners_t : public wf::plugin_interface_t
{
    const std::string transformer_name = "sonata-corners";
    /* typeid(wf::scene::blur_node_t).name(): the blur plugin's transformer */
    static inline const std::string blur_name = "N2wf5scene11blur_node_tE";
    wf::wl_idle_call idle_update;
    wf::wl_timer<false> late_update;

    /* Decorated by the compositor (margins) and not fullscreen. */
    static bool wanted(wayfire_toplevel_view view)
    {
        if (!view || !view->is_mapped() || (view->role != wf::VIEW_ROLE_TOPLEVEL))
        {
            return false;
        }

        if (view->pending_fullscreen() || view->toplevel()->current().fullscreen)
        {
            return false;
        }

        auto m = view->toplevel()->current().margins;
        return (m.left > 0) || (m.top > 0);
    }

    void update(wayfire_toplevel_view view)
    {
        if (!view)
        {
            return;
        }

        auto tnode = view->get_transformed_node();
        auto have  = tnode->get_transformer(transformer_name);
        if (wanted(view) && !have)
        {
            /* innermost: the corners are cut before any other transform */
            tnode->add_transformer(std::make_shared<corners_node_t>(view), 0, transformer_name);
            auto g = view->get_geometry();
            auto m = view->toplevel()->current().margins;
            LOGI("sonata-corners: ", view->get_app_id(), " geometry ", g.x, ",", g.y, " ", g.width, "x",
                g.height, " margins ", m.left, "/", m.top, "/", m.right, "/", m.bottom);
        } else if (!wanted(view) && have)
        {
            tnode->rem_transformer(have);
        }
    }

    /* A submenu (a popup of a popup) is drawn inside its parent menu's node,
     * so the parent's blur already covers it; blurring it again samples the
     * parent's offscreen buffer (empty) and the submenu turns near-black. */
    static void unblur_nested_popup(wayfire_view view)
    {
        if (!view || (view->role != wf::VIEW_ROLE_UNMANAGED) || !view->get_root_node())
        {
            return;
        }

        auto parent = view->get_root_node()->parent();
        if (!parent || !wf::node_to_view(parent))
        {
            return;
        }

        auto tnode = view->get_transformed_node();
        if (auto blur = tnode->get_transformer(blur_name))
        {
            tnode->rem_transformer(blur);
        }
    }

    void update_all()
    {
        for (auto& v : wf::get_core().get_all_views())
        {
            unblur_nested_popup(v);
            update(wf::toplevel_cast(v));
        }
    }

    /* state changes (decorated, fullscreen) settle after the signal: check on idle */
    void update_soon()
    {
        idle_update.run_once([=] () { update_all(); });
        /* the decoration's margins arrive with a later commit */
        late_update.set_timeout(250, [=] () { update_all(); });
    }

    wf::signal::connection_t<wf::view_mapped_signal> on_map =
        [=] (wf::view_mapped_signal*) { update_soon(); };
    wf::signal::connection_t<wf::view_fullscreen_signal> on_fullscreen =
        [=] (wf::view_fullscreen_signal*) { update_soon(); };
    wf::signal::connection_t<wf::view_decoration_state_updated_signal> on_decoration =
        [=] (wf::view_decoration_state_updated_signal*) { update_soon(); };
    wf::signal::connection_t<wf::view_tiled_signal> on_tiled =
        [=] (wf::view_tiled_signal*) { update_soon(); };

    /* A window being dragged is drawn by Wayfire as an overlay at the very
     * front of the scene -- over the Dock and the menu bar. macOS keeps them
     * on top: the drag overlay goes right behind the TOP layer. */
    bool reordering = false;
    wf::signal::connection_t<wf::scene::root_node_update_signal> on_root_update =
        [=] (wf::scene::root_node_update_signal *ev)
    {
        if (reordering || !(ev->flags & wf::scene::update_flag::CHILDREN_LIST))
        {
            return;
        }

        auto root = wf::get_core().scene();
        auto top  = root->layers[(size_t)wf::scene::layer::TOP];
        std::vector<wf::scene::node_ptr> front, rest;
        bool past_top = false;
        for (auto& child : root->get_children())
        {
            if (child == top)
            {
                past_top = true;
            }

            if (!past_top && (child->stringify().rfind("move-drag", 0) == 0) &&
                (child->stringify().rfind("move-drag-view", 0) != 0))
            {
                front.push_back(child);
            } else
            {
                rest.push_back(child);
            }
        }

        if (front.empty())
        {
            return;
        }

        std::vector<wf::scene::node_ptr> list;
        for (auto& child : rest)
        {
            list.push_back(child);
            if (child == top)                     /* front-to-back: right behind TOP */
            {
                list.insert(list.end(), front.begin(), front.end());
            }
        }

        reordering = true;
        if (root->set_children_list(list))
        {
            wf::scene::update(root, wf::scene::update_flag::CHILDREN_LIST);
        }

        reordering = false;
    };

    /* Frame pacing per display, for Sonata's logs: a burst of repaints (an
     * animation) that ran slower than the display's refresh is written to
     * session.log once it ends -- "sonata-perf: HDMI-A-1 180 Hz: 42 frames,
     * median 11.1 ms, worst 27.8 ms, 3 late, render 1.2 ms". Paired with
     * the apps' own sonata2-frames lines it shows who is slow: the
     * compositor (these) or the app. */
    struct perf_t
    {
        wf::output_t *output = nullptr;
        std::vector<double> starts, costs;
        std::chrono::steady_clock::time_point pre;
        wf::effect_hook_t pre_hook, post_hook;
    };
    std::map<wf::output_t*, std::unique_ptr<perf_t>> perf;

    static double now_ms()
    {
        using namespace std::chrono;
        return duration<double, std::milli>(steady_clock::now().time_since_epoch()).count();
    }

    static void report(perf_t *p)
    {
        auto& t = p->starts;
        if (t.size() < 8)
        {
            return;
        }

        std::vector<double> gaps;
        for (size_t i = 1; i < t.size(); i++)
        {
            gaps.push_back(t[i] - t[i - 1]);
        }

        std::sort(gaps.begin(), gaps.end());
        double refresh = p->output->handle->refresh > 0 ? p->output->handle->refresh / 1000.0 : 60.0;
        double period  = 1000.0 / refresh, median = gaps[gaps.size() / 2], worst = gaps.back();
        if (median < period * 1.3)
        {
            return;                         /* kept up with the display: nothing to say */
        }

        int late = 0;
        for (double g : gaps)
        {
            late += g > period * 1.5;
        }

        double cost = 0;
        for (double c : p->costs)
        {
            cost += c;
        }

        LOGI("sonata-perf: ", p->output->to_string(), " ", (int)(refresh + 0.5), " Hz: ", t.size(),
            " frames, median ", median, " ms, worst ", worst, " ms, ", late, " late, render ",
            p->costs.empty() ? 0.0 : cost / p->costs.size(), " ms");
    }

    void track(wf::output_t *o)
    {
        if (perf.count(o))
        {
            return;
        }

        auto p = std::make_unique<perf_t>();
        p->output = o;
        auto raw = p.get();
        p->pre_hook = [raw] ()
        {
            double now = now_ms();
            if (!raw->starts.empty() && ((now - raw->starts.back() > 250) || (raw->starts.size() >= 600)))
            {
                report(raw);                /* a pause ends the burst */
                raw->starts.clear();
                raw->costs.clear();
            }

            raw->starts.push_back(now);
            raw->pre = std::chrono::steady_clock::now();
        };
        p->post_hook = [raw] ()
        {
            raw->costs.push_back(std::chrono::duration<double, std::milli>(
                std::chrono::steady_clock::now() - raw->pre).count());
        };
        o->render->add_effect(&p->pre_hook, wf::OUTPUT_EFFECT_PRE);
        o->render->add_effect(&p->post_hook, wf::OUTPUT_EFFECT_POST);
        perf[o] = std::move(p);
    }

    wf::signal::connection_t<wf::output_added_signal> on_output_added =
        [=] (wf::output_added_signal *ev) { track(ev->output); };
    wf::signal::connection_t<wf::output_pre_remove_signal> on_output_removed =
        [=] (wf::output_pre_remove_signal *ev)
    {
        auto it = perf.find(ev->output);
        if (it != perf.end())
        {
            ev->output->render->rem_effect(&it->second->pre_hook);
            ev->output->render->rem_effect(&it->second->post_hook);
            perf.erase(it);
        }
    };

    window_capture_t window_capture;

  public:
    void init() override
    {
        window_capture.init();
        if (!wf::get_core().is_gles2())
        {
            LOGE("sonata-corners needs the GLES2 renderer");
            return;
        }

        if (!wf::get_core().get_data<corners_program_t>(program_name))
        {
            auto data = std::make_unique<corners_program_t>();
            wf::gles::run_in_context([&]
            {
                data->program.compile(vertex_shader, fragment_shader);
            });
            wf::get_core().store_data(std::move(data), program_name);
        }

        program_ref_count++;
        wf::get_core().connect(&on_map);
        wf::get_core().connect(&on_fullscreen);
        wf::get_core().connect(&on_decoration);
        wf::get_core().connect(&on_tiled);
        wf::get_core().scene()->connect(&on_root_update);
        wf::get_core().output_layout->connect(&on_output_added);
        wf::get_core().output_layout->connect(&on_output_removed);
        for (auto o : wf::get_core().output_layout->get_outputs())
        {
            track(o);
        }

        update_all();
    }

    void fini() override
    {
        window_capture.fini();
        for (auto& [o, p] : perf)
        {
            o->render->rem_effect(&p->pre_hook);
            o->render->rem_effect(&p->post_hook);
        }

        perf.clear();
        for (auto& v : wf::get_core().get_all_views())
        {
            if (auto t = v->get_transformed_node()->get_transformer(transformer_name))
            {
                v->get_transformed_node()->rem_transformer(t);
            }
        }

        if (--program_ref_count > 0)
        {
            return;
        }

        auto data = wf::get_core().get_data<corners_program_t>(program_name);
        wf::gles::run_in_context_if_gles([&]
        {
            data->program.free_resources();
        });
        wf::get_core().erase_data(program_name);
    }
};
}
}
}

DECLARE_WAYFIRE_PLUGIN(wf::scene::sonata_corners::sonata_corners_t);
