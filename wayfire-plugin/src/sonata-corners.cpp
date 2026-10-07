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
#include <cctype>
#include <cmath>
#include <chrono>
#include <memory>
#include <string>

#include <wayfire/core.hpp>
#include <wayfire/scene.hpp>
#include <wayfire/scene-operations.hpp>
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
#include <wayfire/seat.hpp>
#include <wayfire/nonstd/wlroots-full.hpp>     // wlr_surface (the FPS counter watches its commits)
#include <wayfire/plugins/ipc/ipc-method-repository.hpp>
#include <wayfire/plugins/ipc/ipc-helpers.hpp>
#include <wayfire/plugins/common/shared-core-data.hpp>
#include <deque>
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
#ifdef SONATA_OUTPUT_CAPTURE
    #include "ext-image-capture-source-v1-protocol.h"   /* generated (meson.build) */
#endif
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
uniform float seam;           /* where the client's surface starts under the title bar, < 0: none */
/* rows under the seam that may carry the title bar's tint twice: pixdecor
 * reaches 1 px under the client (5 px maximized). Kept small: content that
 * starts lower (a toolbar's controls, Music's LCD) must never be touched. */
const float SEAM_BAND = 8.0;
/* maximized: pixdecor's stubs below the overlap rows (see main) */
const float STUB_ROWS = 4.0;
const float STUB_LEFT = 64.0;    /* its button area: 7 + 3 x 12 + 2 x 8 = 59, and its edge */
const float STUB_RIGHT = 12.0;   /* its right edge (9 px) */

varying highp vec2 uvpos;

void main()
{
    vec4 c = get_pixel(uvpos);
    float content_a = c.a;        /* the window's own pixel, before anything here */
    vec2 p = vec2(uvpos.x * size.x, (1.0 - uvpos.y) * size.y);
    vec2 lo = rect.xy;
    vec2 hi = rect.xy + rect.zw;
    float overlap = square_top > 0.5 ? 5.0 : 1.0;      /* rows pixdecor's title bar reaches under the client */
    /* pixdecor draws behind the client: its stubs only show through a
     * see-through toolbar (Sonata's glass). Where the client is opaque just
     * below those rows (a browser's tab strip), what shows there is the
     * client itself -- left alone (Vini: Chrome's Tab Search button and
     * Firefox's first tab lost their top, maximized). */
    bool opaque_below = seam >= 0.0 && p.y >= seam && p.y < seam + SEAM_BAND &&      /* (a fetch: those rows only) */
        get_pixel(vec2(uvpos.x, 1.0 - (seam + overlap + STUB_ROWS + 0.5) / size.y)).a > 0.99;
    if (opaque_below)
    {
        /* the client's own pixels */
    }
    else if (seam >= 0.0 && fill.a > 0.0 && p.y >= seam && p.y < seam + overlap && p.x >= lo.x && p.x <= hi.x)
    {
        /* those rows are the title bar: Sonata's toolbars leave them clear
         * (ui/window.py), but pixdecor paints its button area and right
         * edge there twice (a dark stub at either end, maximized) -- the
         * title bar's own colour, exactly. Opaque pixels too: those stubs
         * are opaque, and these rows lie under the (translucent) title bar
         * for every app anyway. */
        c = fill;
    }
    else if (square_top > 0.5 && seam >= 0.0 && p.y >= seam + overlap && p.y < seam + overlap + STUB_ROWS &&
             ((p.x >= lo.x && p.x < lo.x + STUB_LEFT) || (p.x > hi.x - STUB_RIGHT && p.x <= hi.x)))
    {
        /* maximized, pixdecor draws with its 4 px border shift: its button
         * area (left) and right edge reach 4 more rows under the client, as
         * opaque dark stubs (measured: seam+5..seam+8, 61 px / 9 px wide).
         * What they hide is the client's toolbar: take the same column just
         * below them. */
        c = get_pixel(vec2(uvpos.x, 1.0 - (seam + overlap + STUB_ROWS + 0.5) / size.y));
    }
    else if (seam >= 0.0 && p.y >= seam && p.y < seam + SEAM_BAND && p.x >= lo.x && p.x <= hi.x)
    {
        /* pixdecor's title bar reaches a few pixels under the client's top
         * (1 px, 5 px maximized): a see-through toolbar there got the title
         * bar's tint twice -- a dark seam between the two glasses. Such a
         * pixel is denser than the same column further down; take the extra
         * layer (the title bar's colour behind the client) out again. */
        /* the plain toolbar: its left edge, below the band */
        vec4 plain = get_pixel(vec2((lo.x + 3.0) / size.x, 1.0 - (seam + SEAM_BAND) / size.y));
        vec4 below = get_pixel(vec2(uvpos.x, 1.0 - (seam + SEAM_BAND) / size.y));
        /* a column that is plain toolbar below the band (not a control that
         * starts there: copying from one smeared its text upwards), or a
         * pixel with exactly one extra title-bar layer over the toolbar */
        bool plain_column = length(below - plain) < 0.02;
        float twice = plain.a + (1.0 - plain.a) * fill.a;
        if (plain.a > 0.2 && plain.a < 0.99 &&
            ((plain_column && c.a > plain.a + 0.02) || (fill.a > 0.0 && abs(c.a - twice) < 0.02)))
        {
            c = plain;
        }
    }
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
        /* only around something drawn: Steam clears its window (transparent)
         * for ~2 s before it goes, and the hairline alone stayed (Vini) */
        ring *= smoothstep(0.0, 0.3, content_a);
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

static std::string option_str(const std::string& name)
{
    auto opt = wf::get_core().config->get_option(name);
    return opt ? opt->get_value_str() : "";
}

static int option_int(const std::string& name, int fallback)
{
    try {
        return std::stoi(option_str(name));
    } catch (...)
    {
        return fallback;
    }
}

static glm::vec4 option_color(const std::string& name, bool premultiply)
{
    auto col = wf::option_type::from_string<wf::color_t>(option_str(name));
    if (!col)
    {
        return glm::vec4{0, 0, 0, 0};
    }

    float k = premultiply ? col->a : 1.0f;
    return glm::vec4{col->r * k, col->g * k, col->b * k, col->a};
}

/* Every option the corners read, looked up and parsed once, then again
 * after each config reload (reload_config_signal: Settings writes the ini).
 * Read per window per frame (~10 lookups + parses each, at 180 Hz) before.
 * Read without option_wrapper_t: a wrapper throws (and aborts Wayfire)
 * when the plugin's XML wasn't loaded when Wayfire started. */
struct corners_options_t
{
    bool rounded_engine = false;   /* pixdecor draws its rounded frame + shadow */
    bool max_shadows    = false;   /* pixdecor/maximized_shadows */
    int shadow_radius   = 0;       /* pixdecor/shadow_radius (0: unset or bad) */
    glm::vec4 shadow_color{0, 0, 0, 0};   /* premultiplied */
    float radius = 10;             /* corner radius drawn */
    glm::vec4 fg_fill{0, 0, 0, 0}, bg_fill{0, 0, 0, 0};   /* stored premultiplied by Sonata */
    glm::vec4 outline{0, 0, 0, 0}; /* premultiplied */
    std::vector<std::string> own_frame_apps;   /* sonata-corners/own_frame_apps */

    void load()
    {
        rounded_engine = option_str("pixdecor/overlay_engine") == "rounded_corners";
        max_shadows    = option_str("pixdecor/maximized_shadows") == "true";
        shadow_radius  = option_int("pixdecor/shadow_radius", 0);
        shadow_color   = option_color("pixdecor/shadow_color", true);
        /* pixdecor's own corner radius when it rounds the frame: the arcs meet */
        int r = rounded_engine ? option_int("pixdecor/rounded_corner_radius", -1) : -1;
        radius  = std::max(0, r >= 0 ? r : option_int("sonata-corners/radius", 10));
        fg_fill = option_color("pixdecor/fg_color", false);
        bg_fill = option_color("pixdecor/bg_color", false);
        outline = option_color("sonata-corners/outline", true);
        own_frame_apps.clear();
        std::string list = option_str("sonata-corners/own_frame_apps"), word;
        for (char c : list + " ")
        {
            if ((c == ' ') || (c == ','))
            {
                if (!word.empty())
                {
                    std::transform(word.begin(), word.end(), word.begin(),
                        [] (unsigned char ch) { return std::tolower(ch); });
                    own_frame_apps.push_back(word);
                }

                word.clear();
            } else
            {
                word += c;
            }
        }
    }
};

static bool options_valid = false;

static const corners_options_t& options()
{
    static corners_options_t cached;
    if (!options_valid)
    {
        cached.load();
        options_valid = true;
    }

    return cached;
}

/* Shadow around pixdecor's frame (part of the window geometry): the frame
 * itself is inset by it. 0 without pixdecor's rounded engine, or when a
 * tiled window has no shadow. */
static double decoration_shadow(wayfire_toplevel_view view)
{
    auto& o = options();
    auto m  = view->toplevel()->current().margins;
    if (!o.rounded_engine || ((m.left <= 0) && (m.top <= 0)))
    {
        return 0;               /* no decoration (an app drawing its own frame: Steam) */
    }

    bool tiled = view->pending_tiled_edges() != 0;
    if (tiled && !o.max_shadows)
    {
        return 0;
    }

    return 2.0 * o.shadow_radius;
}

/* pixdecor's shadow colour (premultiplied) and radius, when its rounded
 * engine draws a shadow; zeros otherwise */
static void decoration_shadow_style(glm::vec4& color, float& radius)
{
    auto& o = options();
    color  = o.rounded_engine ? o.shadow_color : glm::vec4{0, 0, 0, 0};
    radius = o.rounded_engine ? o.shadow_radius : 0;
}

static float corner_radius()
{
    return options().radius;
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
    bool alloc_failed = false;     /* logged once per failure streak */

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
        /* The window is drawn into a buffer first (render). When the GPU
         * refuses that buffer (NVIDIA's GBM: "gbm_bo_create failed: Invalid
         * argument"), Wayfire rendered into nothing and the whole session
         * crashed back to the login screen. Then: the window as it is, this
         * frame, without rounded corners. Same size and scale as render()'s
         * get_updated_contents, which then finds the buffer ready. */
        auto bbox = self->get_children_bounding_box();
        auto res  = self->inner_content.allocate(wf::dimensions(bbox), target.scale);
        if (res == wf::buffer_reallocation_result_t::FAILED)
        {
            if (!alloc_failed)
            {
                LOGE("sonata-corners: no buffer for ", view->get_app_id(), " ", bbox.width, "x", bbox.height,
                    ": drawn without rounded corners");
            }

            alloc_failed = true;
            for (auto& ch : this->children)
            {
                ch->schedule_instructions(instructions, target, damage);
            }

            return;
        }

        if (res == wf::buffer_reallocation_result_t::REALLOCATED)
        {
            self->cached_damage |= bbox;    /* a new buffer: everything again */
        }

        alloc_failed = false;
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
            if (!src_tex || !src_tex->get_wlr_texture())
            {
                return;     /* no buffer this frame (Wayfire with the skip-frame fix) */
            }

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
            /* stored premultiplied (Sonata writes them so: pixdecor blends them as such) */
            data_ptr->program.uniform4f("fill", view->activated ? options().fg_fill : options().bg_fill);
            data_ptr->program.uniform4f("outline", options().outline);
            /* the client's surface top (below pixdecor's title bar), for the seam */
            auto m = view->toplevel()->current().margins;
            data_ptr->program.uniform1f("seam", m.top > inset ? float(g.y + m.top - bbox.y) : -1.0f);
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
/* the top of a window that is always blurred: title bar + a toolbar */
/* bumped with every change of the plugin (tests/test_regressions.py checks it) */
#define SONATA_CORNERS_BUILD "2026-10-07.5 clicks read in a burst held apart"
static const int TOP_GLASS = 96;

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

        /* the frame as drawn (the rounded corners are cut out of it, not
         * out of the shadow around it) */
        auto g = view->get_geometry();
        int inset = (int)decoration_shadow(view);
        wf::geometry_t f{g.x + inset, g.y + inset, g.width - 2 * inset, g.height - 2 * inset};
        /* the corners as drawn: the real radius (+1 for the antialiased
         * edge), never more than half the frame */
        int c = std::min<int>((int)std::ceil(corner_radius()) + 1, (int)(std::min(f.width, f.height) / 2));
        for (auto corner : {wf::geometry_t{f.x, f.y, c, c}, wf::geometry_t{f.x + f.width - c, f.y, c, c},
                            wf::geometry_t{f.x, f.y + f.height - c, c, c},
                            wf::geometry_t{f.x + f.width - c, f.y + f.height - c, c, c}})
        {
            if (c > 0)
            {
                region ^= wf::regionf_t{corner};
            }
        }

        /* Title bar and toolbar always go through the blur: a few pixels
         * there were reported opaque but drawn see-through, and with the
         * wallpaper culled behind them they showed as a dark band between
         * the glass title bar and a glass toolbar (Preview, Notes). The
         * strip is cheap to blur; the window body below stays skipped. */
        region ^= wf::regionf_t{wf::geometry_t{f.x, f.y, f.width, TOP_GLASS}};

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
    if (!s->buffer.get_buffer())
    {
        /* nothing to copy: say so, or the client waits for this frame forever */
        wlr_ext_image_copy_capture_frame_v1_fail(frame, EXT_IMAGE_COPY_CAPTURE_FRAME_V1_FAILURE_REASON_UNKNOWN);
        return;
    }

    if (wlr_ext_image_copy_capture_frame_v1_copy_buffer(frame, s->buffer.get_buffer(), wf::get_core().renderer))
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
    std::vector<std::unique_ptr<window_source_t>> retired;   /* finished, freed on idle */
    wf::wl_idle_call free_retired;
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
                    wlr_ext_image_capture_source_v1_finish(&found->second->base);
                    /* this lambda lives in the source's on_unmap: destroying the
                     * source now would destroy the running callback (UB).
                     * Freed on idle, after the signal returned. */
                    self->retired.push_back(std::move(found->second));
                    self->sources.erase(found);
                    self->free_retired.run_once([self] () { self->retired.clear(); });
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
        free_retired.disconnect();
        retired.clear();
    }
};


#ifdef SONATA_OUTPUT_CAPTURE
/* ---- Screen capture without Sonata's own controls ---------------------------------------------
 * Vini: the drawing palette (livedraw, "sonata2-draw-palette") is on screen
 * while recording or sharing, never in what's recorded or shared. Captures
 * of a whole display (ext-image-copy-capture: xdg-desktop-portal-wlr,
 * grim, wf-recorder) come from this plugin's own per-display source instead
 * of wlroots' (whose global is hidden): it renders the display's scene
 * again into a buffer of its own, with the surfaces named in
 * sonata-corners/capture_hidden left out (a transformer around them that
 * draws nothing while this capture renders), and the pointer painted in
 * when the capture asks for it. wlr-screencopy (older tools) still copies
 * the screen as shown. */

static bool capture_rendering = false;      /* the hidden surfaces draw nothing meanwhile */

static std::vector<std::string> option_words(const std::string& name);

static std::vector<std::string> capture_hidden_ids()
{
    return option_words("sonata-corners/capture_hidden");
}

/* The programs that get Sonata's display capture (recorders, screen sharing):
 * every other one -- screenshots (grim) -- keeps wlroots' own. Vini: with
 * Sonata's for everyone, screenshots failed ("failed to copy output"). */
static bool capture_client(const wl_client *client)
{
    pid_t pid = 0;
    wl_client_get_credentials(const_cast<wl_client*>(client), &pid, nullptr, nullptr);
    if (pid <= 0)
    {
        return false;
    }

    char path[64];
    snprintf(path, sizeof(path), "/proc/%d/comm", (int)pid);
    FILE *f = fopen(path, "r");
    if (!f)
    {
        return false;
    }

    char comm[64] = {0};
    if (!fgets(comm, sizeof(comm), f))
    {
        comm[0] = 0;
    }

    fclose(f);
    std::string name = comm;
    while (!name.empty() && ((name.back() == '\n') || (name.back() == ' ')))
    {
        name.pop_back();
    }

    for (auto& want : option_words("sonata-corners/capture_clients"))
    {
        /* /proc's comm is cut at 15 characters */
        if (!name.empty() && (want.compare(0, name.size(), name) == 0) && (name.size() >= std::min<size_t>(15, want.size())))
        {
            return true;
        }
    }

    return false;
}

static std::vector<std::string> option_words(const std::string& name)
{
    std::vector<std::string> out;
    std::string list = option_str(name), word;
    for (char c : list + " ")
    {
        if ((c == ' ') || (c == ','))
        {
            if (!word.empty())
            {
                out.push_back(word);
            }

            word.clear();
        } else
        {
            word += c;
        }
    }

    return out;
}

class capture_hide_instance_t : public wf::scene::render_instance_t
{
    std::vector<wf::scene::render_instance_uptr> kids;

  public:
    capture_hide_instance_t(std::vector<wf::scene::render_instance_uptr> kids) : kids(std::move(kids))
    {}

    void schedule_instructions(std::vector<wf::scene::render_instruction_t>& instructions,
        const wf::render_target_t& target, wf::regionf_t& damage) override
    {
        if (capture_rendering)
        {
            return;                         /* left out of the capture */
        }

        for (auto& k : kids)
        {
            k->schedule_instructions(instructions, target, damage);
        }
    }

    void presentation_feedback(wf::output_t *output) override
    {
        for (auto& k : kids)
        {
            k->presentation_feedback(output);
        }
    }

    wf::scene::direct_scanout try_scanout(wf::output_t *output) override
    {
        for (auto& k : kids)
        {
            auto r = k->try_scanout(output);
            if (r != wf::scene::direct_scanout::SKIP)
            {
                return r;
            }
        }

        return wf::scene::direct_scanout::SKIP;
    }

    void compute_visibility(wf::output_t *output, wf::regionf_t& visible) override
    {
        for (auto& k : kids)
        {
            k->compute_visibility(output, visible);
        }
    }
};

class capture_hide_node_t : public wf::scene::transformer_base_node_t
{
  public:
    capture_hide_node_t() : wf::scene::transformer_base_node_t(false)
    {}

    std::string stringify() const override
    {
        return "sonata capture-hidden";
    }

    void gen_render_instances(std::vector<wf::scene::render_instance_uptr>& instances,
        wf::scene::damage_callback push_damage, wf::output_t *shown_on) override
    {
        std::vector<wf::scene::render_instance_uptr> kids;
        for (auto& ch : get_children())
        {
            ch->gen_render_instances(kids, push_damage, shown_on);
        }

        instances.push_back(std::make_unique<capture_hide_instance_t>(std::move(kids)));
    }
};

static const std::string capture_hide_name = "sonata-capture-hidden";

/* a view named in capture_hidden: kept out of the display captures */
static void capture_hide_update(wayfire_view view)
{
    if (!view || !view->get_transformed_node())
    {
        return;
    }

    auto ids  = capture_hidden_ids();
    bool want = std::find(ids.begin(), ids.end(), view->get_app_id()) != ids.end();
    auto tnode = view->get_transformed_node();
    auto have  = tnode->get_transformer(capture_hide_name);
    if (want && !have)
    {
        tnode->add_transformer(std::make_shared<capture_hide_node_t>(), 0, capture_hide_name);
    } else if (!want && have)
    {
        tnode->rem_transformer(have);
    }
}

struct output_source_t
{
    wlr_ext_image_capture_source_v1 base; /* first: wl_container_of */
    wf::output_t *output = nullptr;
    wf::auxilliary_buffer_t buffer;
    int started    = 0;
    bool cursors   = false;
    bool pending   = false;
    int64_t last_us = 0;                  /* when the last picture was made */
    wf::wl_idle_call idle;
    /* at most one picture per display refresh: a recorder asking again at
     * once got hundreds of identical frames a second. (Waiting for the
     * display's own next frame instead broke screenshots: grim's copy failed.) */
    wf::wl_timer<false> later;

    static output_source_t *from(wlr_ext_image_capture_source_v1 *b)
    {
        return reinterpret_cast<output_source_t*>(b);
    }

    /* the capture's size (the display's, in its pixels) and formats: the
     * display's own (dma-buf and shared memory) when its swapchain is known */
    void constraints(int w, int h)
    {
        auto wo = output->handle;
        if (!wo->swapchain ||
            !wlr_ext_image_capture_source_v1_set_constraints_from_swapchain(&base, wo->swapchain,
                wf::get_core().renderer) || !base.shm_formats)
        {
            if (!base.shm_formats)
            {
                base.shm_formats     = (uint32_t*)calloc(2, sizeof(uint32_t));
                base.shm_formats[0]  = DRM_FORMAT_ARGB8888;
                base.shm_formats[1]  = DRM_FORMAT_XRGB8888;
                base.shm_formats_len = 2;
            }
        }

        base.width  = std::max(1, w);
        base.height = std::max(1, h);
        wl_signal_emit_mutable(&base.events.constraints_update, nullptr);
    }

    wf::dimensions_t expected_size() const
    {
        auto og = output->get_layout_geometry();
        float scale = output->handle->scale;
        return {(int)std::round(og.width * scale), (int)std::round(og.height * scale)};
    }

    /* the display's picture without the hidden surfaces, then a frame event */
    void produce()
    {
        pending = false;
        if (!output || !started)
        {
            return;
        }

        timespec t;
        clock_gettime(CLOCK_MONOTONIC, &t);
        last_us = (int64_t)t.tv_sec * 1000000 + t.tv_nsec / 1000;
        auto og    = output->get_layout_geometry();
        float scale = output->handle->scale;
        if (buffer.allocate(wf::dimensions(og), scale) == wf::buffer_reallocation_result_t::FAILED)
        {
            buffer.free();
            frame_event(0, 0);                /* no picture: copy_frame fails the client's frame */
            return;
        }

        auto size = buffer.get_size();
        if (((int)base.width != size.width) || ((int)base.height != size.height))
        {
            constraints(size.width, size.height);
        }

        wf::render_target_t target{buffer};
        target.geometry = og;
        target.scale    = scale;
        std::vector<wf::scene::render_instance_uptr> instances;
        wf::get_core().scene()->gen_render_instances(instances, [] (auto) {}, output);
        wf::render_pass_params_t params;
        params.background_color = {0, 0, 0, 1};
        params.damage    = og;
        params.target    = target;
        params.instances = &instances;
        params.flags     = wf::RPASS_CLEAR_BACKGROUND;
        capture_rendering = true;
        wf::render_pass_t::run(params);
        capture_rendering = false;
        if (cursors)
        {
            paint_cursors();
        }

        frame_event(size.width, size.height);
    }

    void frame_event(int w, int h)
    {
        pixman_region32_t damage;
        pixman_region32_init_rect(&damage, 0, 0, w, h);
        wlr_ext_image_capture_source_v1_frame_event ev{};
        ev.damage = &damage;
        wl_signal_emit_mutable(&base.events.frame, &ev);
        pixman_region32_fini(&damage);
    }

    /* the pointer, as the display shows it (it's on its own plane, not in the scene) */
    void paint_cursors()
    {
        auto wo = output->handle;
        auto pass = wlr_renderer_begin_buffer_pass(wf::get_core().renderer, buffer.get_buffer(), nullptr);
        if (!pass)
        {
            return;
        }

        wlr_output_cursor *c;
        wl_list_for_each(c, &wo->cursors, link)
        {
            if (!c->enabled || !c->visible || !c->texture)
            {
                continue;
            }

            wlr_render_texture_options opts{};
            opts.texture = c->texture;
            opts.src_box = c->src_box;
            opts.dst_box = {(int)(c->x - c->hotspot_x), (int)(c->y - c->hotspot_y), (int)c->width, (int)c->height};
            opts.transform = c->transform;
            wlr_render_pass_add_texture(pass, &opts);
        }

        wlr_render_pass_submit(pass);
    }
};

static void output_source_start(wlr_ext_image_capture_source_v1 *b, bool with_cursors)
{
    auto s = output_source_t::from(b);
    s->started++;
    s->cursors = s->cursors || with_cursors;
}

static void output_source_stop(wlr_ext_image_capture_source_v1 *b)
{
    auto s = output_source_t::from(b);
    s->started = std::max(0, s->started - 1);
    if (!s->started)
    {
        s->cursors = false;
    }
}

static void output_source_request_frame(wlr_ext_image_capture_source_v1 *b, bool)
{
    auto s = output_source_t::from(b);
    if (s->pending)
    {
        return;
    }

    s->pending = true;
    timespec t;
    clock_gettime(CLOCK_MONOTONIC, &t);
    int64_t now = (int64_t)t.tv_sec * 1000000 + t.tv_nsec / 1000;
    int mhz     = (s->output && s->output->handle->refresh > 0) ? s->output->handle->refresh : 60000;
    int64_t gap = 1000000000LL / mhz;                       /* one refresh, in µs */
    int64_t wait = s->last_us + gap - now;
    if (wait <= 1000)
    {
        s->idle.run_once([s] () { s->produce(); });          /* after this request returns */
    } else
    {
        s->later.set_timeout((uint32_t)((wait + 999) / 1000), [s] () { s->produce(); });
    }
}

static void output_source_copy_frame(wlr_ext_image_capture_source_v1 *b,
    wlr_ext_image_copy_capture_frame_v1 *frame, wlr_ext_image_capture_source_v1_frame_event*)
{
    auto s = output_source_t::from(b);
    if (!s->buffer.get_buffer())
    {
        /* nothing to copy: say so, or the client waits for this frame forever */
        wlr_ext_image_copy_capture_frame_v1_fail(frame, EXT_IMAGE_COPY_CAPTURE_FRAME_V1_FAILURE_REASON_UNKNOWN);
        return;
    }

    if (wlr_ext_image_copy_capture_frame_v1_copy_buffer(frame, s->buffer.get_buffer(), wf::get_core().renderer))
    {
        timespec now;
        clock_gettime(CLOCK_MONOTONIC, &now);
        wlr_ext_image_copy_capture_frame_v1_ready(frame, WL_OUTPUT_TRANSFORM_NORMAL, &now);
    }
}

static const wlr_ext_image_capture_source_v1_interface output_source_impl = {
    .start = output_source_start,
    .stop  = output_source_stop,
    .request_frame = output_source_request_frame,
    .copy_frame    = output_source_copy_frame,
    .get_pointer_cursor = nullptr,
};

class output_capture_t
{
    wl_global *global = nullptr;
    std::unique_ptr<wf::wayland_global_filter_t> filter;
    std::map<wf::output_t*, std::unique_ptr<output_source_t>> sources;
    std::vector<std::unique_ptr<output_source_t>> retired;
    wf::wl_idle_call free_retired;

    static output_capture_t*& instance()
    {
        static output_capture_t *self = nullptr;
        return self;
    }

    static void handle_destroy(wl_client*, wl_resource *resource)
    {
        wl_resource_destroy(resource);
    }

    static void handle_create_source(wl_client *client, wl_resource*, uint32_t new_id, wl_resource *output_res)
    {
        auto self = instance();
        wlr_output *wo = wlr_output_from_resource(output_res);
        wf::output_t *out = (self && wo) ? wf::get_core().output_layout->find_output(wo) : nullptr;
        if (!out)
        {
            wlr_ext_image_capture_source_v1_create_resource(nullptr, client, new_id);   /* inert */
            return;
        }

        auto it = self->sources.find(out);
        if (it == self->sources.end())
        {
            auto src = std::make_unique<output_source_t>();
            wlr_ext_image_capture_source_v1_init(&src->base, &output_source_impl);
            src->output = out;
            auto sz = src->expected_size();
            src->constraints(sz.width, sz.height);
            it = self->sources.emplace(out, std::move(src)).first;
        }

        wlr_ext_image_capture_source_v1_create_resource(&it->second->base, client, new_id);
    }

    static const struct ext_output_image_capture_source_manager_v1_interface *impl()
    {
        static const struct ext_output_image_capture_source_manager_v1_interface i = {
            .create_source = handle_create_source,
            .destroy = handle_destroy,
        };
        return &i;
    }

    static void bind(wl_client *client, void*, uint32_t version, uint32_t id)
    {
        wl_resource *res = wl_resource_create(client, &ext_output_image_capture_source_manager_v1_interface,
            version, id);
        if (!res)
        {
            wl_client_post_no_memory(client);
            return;
        }

        wl_resource_set_implementation(res, impl(), nullptr, nullptr);
    }

  public:
    wf::signal::connection_t<wf::output_removed_signal> on_output_removed =
        [=] (wf::output_removed_signal *ev)
    {
        auto it = sources.find(ev->output);
        if (it != sources.end())
        {
            it->second->output = nullptr;
            it->second->later.disconnect();
            wlr_ext_image_capture_source_v1_finish(&it->second->base);
            retired.push_back(std::move(it->second));
            sources.erase(it);
            free_retired.run_once([=] () { retired.clear(); });
        }
    };

    void init()
    {
        if (capture_hidden_ids().empty())
        {
            return;                         /* nothing to leave out: wlroots' own captures */
        }

        instance() = this;
        global = wl_global_create(wf::get_core().display, &ext_output_image_capture_source_manager_v1_interface,
            1, nullptr, bind);
        /* wlroots' own per-display source is hidden: ours stands in for it */
        auto theirs = wf::get_core().protocols.output_image_capture_source;
        wl_global *their_global = theirs ? theirs->global : nullptr;
        filter = wf::get_core().create_global_filter();
        /* recorders and screen sharing see ours (without Sonata's controls),
         * everything else (screenshots) wlroots' own */
        wl_global *ours = global;
        filter->set_filter([their_global, ours] (const wl_client *c, const wl_global *g)
        {
            if ((g != their_global) && (g != ours))
            {
                return true;
            }

            bool recorder = capture_client(c);
            return (g == ours) ? recorder : !recorder;
        });
        wf::get_core().output_layout->connect(&on_output_removed);
        LOGI("sonata-corners: display capture without Sonata's controls ready");
    }

    void fini()
    {
        filter.reset();
        if (global)
        {
            wl_global_destroy(global);
            global = nullptr;
        }

        for (auto& [o, src] : sources)
        {
            src->output = nullptr;
            src->later.disconnect();
            wlr_ext_image_capture_source_v1_finish(&src->base);
        }

        sources.clear();
        free_retired.disconnect();
        retired.clear();
        if (instance() == this)
        {
            instance() = nullptr;
        }
    }
};
#endif

/* FPS of the app in front (Control Center / menu bar "FPS", Vini): the
 * commits of the focused view's surface in the last second -- the frames a
 * game really hands over. Counted only while someone asks (IPC
 * "sonata/fps"); 5 s without a question and it lets go of the surface. */
class fps_counter_t
{
    wlr_surface *surface = nullptr;
    wf::wl_listener_wrapper on_commit, on_destroy;
    wf::wl_idle_call idle;
    std::deque<int64_t> stamps;
    std::deque<int64_t> micros;            /* the commits' times in µs, last FRAMETIME_US (the graph) */
    int64_t last_ask = 0, since = 0;
    static constexpr int64_t FRAMETIME_US = 4000000;

    static int64_t now_us()
    {
        timespec t;
        clock_gettime(CLOCK_MONOTONIC, &t);
        return (int64_t)t.tv_sec * 1000000 + t.tv_nsec / 1000;
    }

    void unwatch()
    {
        on_commit.disconnect();
        on_destroy.disconnect();
        surface = nullptr;
        stamps.clear();
        micros.clear();
    }

    void trim(int64_t now)
    {
        while (!stamps.empty() && (now - stamps.front() > 1000))
        {
            stamps.pop_front();
        }
    }

    void watch(wlr_surface *s)
    {
        if (s == surface)
        {
            return;
        }

        unwatch();
        if (!s)
        {
            return;
        }

        surface = s;
        since   = wf::get_current_time();
        on_commit.set_callback([this] (void*)
        {
            int64_t now = wf::get_current_time();
            if (now - last_ask > 5000)
            {
                /* nobody looks any more: let go, outside the signal's emission */
                idle.run_once([this] () { unwatch(); });
                return;
            }

            stamps.push_back(now);
            trim(now);
            int64_t us = now_us();
            micros.push_back(us);
            while (!micros.empty() && (us - micros.front() > FRAMETIME_US))
            {
                micros.pop_front();
            }
        });
        on_commit.connect(&s->events.commit);
        on_destroy.set_callback([this] (void*) { unwatch(); });
        on_destroy.connect(&s->events.destroy);
    }

  public:
    /* The app in front: the focused window -- or, while Control Center (a
     * panel) has the keyboard, the window focused last before it ("No app
     * drawing in front" with a game open, Vini). */
    static wayfire_view front_view()
    {
        auto active = wf::get_core().seat->get_active_view();
        if (wf::toplevel_cast(active))
        {
            return active;
        }

        wayfire_view best = nullptr;
        uint64_t best_ts  = 0;
        for (auto& v : wf::get_core().get_all_views())
        {
            auto t = wf::toplevel_cast(v);
            if (!t || !v->is_mapped() || t->minimized || (v->role != wf::VIEW_ROLE_TOPLEVEL))
            {
                continue;
            }

            uint64_t ts = v->get_surface_root_node()->keyboard_interaction().last_focus_timestamp;
            if (ts > best_ts)
            {
                best_ts = ts;
                best    = v;
            }
        }

        return best;
    }

    /* data {"frametimes": true}: also each frame's time (ms) of the last
     * seconds, oldest first (Control Center's frame-time graph) */
    wf::json_t ask(const wf::json_t& data = wf::json_t())
    {
        int64_t now = wf::get_current_time();
        last_ask = now;
        auto view = front_view();
        watch(view ? view->get_wlr_surface() : nullptr);
        trim(now);
        /* frames only age out on the next commit: an app that stopped drawing
         * (paused, frozen) kept its last seconds on the graph for good */
        int64_t us = now_us();
        while (!micros.empty() && (us - micros.front() > FRAMETIME_US))
        {
            micros.pop_front();
        }

        auto response = wf::ipc::json_ok();
        response["fps"]   = (int)stamps.size();
        response["ready"] = (bool)(surface && (now - since >= 1000));    /* a full second counted */
        response["app-id"] = view ? view->get_app_id() : std::string("");
        auto toplevel = wf::toplevel_cast(view);
        response["fullscreen"] = (bool)(toplevel && toplevel->pending_fullscreen());
        if (data.is_object() && data.has_member("frametimes"))
        {
            wf::json_t times = wf::json_t::array();
            for (size_t i = 1; i < micros.size(); i++)
            {
                times.append((double)(micros[i] - micros[i - 1]) / 1000.0);
            }

            response["frametimes"] = times;
        }

        return response;
    }

    void fini()
    {
        unwatch();
    }
};

/* Taps that games miss (Vini: the touchpad moved the pointer in games but a
 * tap never clicked; a USB mouse did). libinput sends a tap's press and
 * release together; games read the button once per frame (Wine / Proton
 * polling) and never see it down. A release that comes sooner than
 * MIN_CLICK_MS after its press is held back until then -- a real click
 * (always longer) is untouched; a new press of that button first lets the
 * held release through (double taps keep their order). */
static constexpr uint32_t MIN_CLICK_MS = 80;

/* How long a release must wait (0: it goes now) -- msec clocks wrap */
static uint32_t click_hold_ms(uint32_t pressed, uint32_t released)
{
    uint32_t took = released - pressed;
    return (took < MIN_CLICK_MS) ? (MIN_CLICK_MS - took) : 0;
}

struct short_click_stretch_t
{
    struct held_t
    {
        wlr_pointer_button_event ev;
        std::unique_ptr<wf::wl_timer<false>> timer;
        wf::wl_listener_wrapper on_destroy;
    };
    std::map<std::pair<wlr_pointer*, uint32_t>, uint32_t> pressed_at;
    /* when the press was handed on (Wayfire's clock): a busy compositor (a
     * game keeping the GPU busy) reads input late, in bursts -- a 100 ms
     * click then went out as a press and a release together (Vini's log:
     * mouse clicks too, in Ravage) */
    std::map<std::pair<wlr_pointer*, uint32_t>, uint32_t> sent_at;
    std::map<std::pair<wlr_pointer*, uint32_t>, std::unique_ptr<held_t>> held;
    bool replaying = false;

    void release(std::pair<wlr_pointer*, uint32_t> key)
    {
        auto it = held.find(key);
        if (it == held.end())
        {
            return;
        }

        auto h = std::move(it->second);
        held.erase(it);
        wlr_pointer_button_event ev = h->ev;
        ev.time_msec = std::max(ev.time_msec, pressed_at[key] + MIN_CLICK_MS);
        replaying = true;
        wl_signal_emit_mutable(&key.first->events.button, &ev);   /* the usual way in, through wlr_cursor */
        replaying = false;
    }

    wf::signal::connection_t<wf::input_event_signal<wlr_pointer_button_event>> on_button =
        [=] (wf::input_event_signal<wlr_pointer_button_event> *sig)
    {
        auto ev = sig->event;
        if (replaying || !ev || !ev->pointer)
        {
            return;
        }

        log_button("in", ev, sig->mode);
        auto key = std::make_pair(ev->pointer, ev->button);
        if (ev->state == WL_POINTER_BUTTON_STATE_PRESSED)
        {
            release(key);                                         /* a held one first */
            pressed_at[key] = ev->time_msec;
            sent_at[key]    = wf::get_current_time();
            return;
        }

        auto at = pressed_at.find(key);
        uint32_t wait = (at == pressed_at.end()) ? 0 :
            std::max(click_hold_ms(at->second, ev->time_msec),                 /* a tap */
                click_hold_ms(sent_at[key], wf::get_current_time()));          /* read in one burst */
        if (!wait)
        {
            return;
        }

        sig->mode = wf::input_event_processing_mode_t::IGNORE;   /* later, see release() */
        auto h = std::make_unique<held_t>();
        h->ev = *ev;
        h->timer = std::make_unique<wf::wl_timer<false>>();
        auto ptr = ev->pointer;
        h->on_destroy.set_callback([this, ptr] (void*)
        {
            for (auto it = held.begin(); it != held.end();)
            {
                it = (it->first.first == ptr) ? held.erase(it) : std::next(it);
            }

            for (auto it = pressed_at.begin(); it != pressed_at.end();)
            {
                it = (it->first.first == ptr) ? pressed_at.erase(it) : std::next(it);
            }

            for (auto it = sent_at.begin(); it != sent_at.end();)
            {
                it = (it->first.first == ptr) ? sent_at.erase(it) : std::next(it);
            }
        });
        h->on_destroy.connect(&ptr->base.events.destroy);
        h->timer->set_timeout(wait, [this, key] { release(key); });
        held[key] = std::move(h);
    };

    /* [sonata-corners] debug_input = true: every button in session.log --
     * which device, which window gets it, and what Wayfire did with it
     * (Vini: touchpad clicks never reached a Proton game) */
    static void log_button(const char *when, wlr_pointer_button_event *ev,
        wf::input_event_processing_mode_t mode)
    {
        if (option_str("sonata-corners/debug_input") != "true")
        {
            return;
        }

        auto view = wf::get_core().get_cursor_focus_view();
        auto keys = wf::get_core().seat->get_active_view();     /* games ignore clicks when not focused */
        auto node = wf::get_core().get_cursor_focus();          /* the app itself, or a frame / edge over it */
        LOGI("sonata-input ", when, ": ", (ev->pointer && ev->pointer->base.name) ? ev->pointer->base.name : "?",
            " button ", ev->button, (ev->state == WL_POINTER_BUTTON_STATE_PRESSED) ? " down" : " up",
            " t=", ev->time_msec, " mode=", (int)mode, " -> ",
            view ? view->get_app_id() : std::string("(nothing)"),
            view ? (" \"" + view->get_title() + "\"") : std::string(""),
            " keyboard: ", keys ? keys->get_app_id() : std::string("(nothing)"),
            " node: ", node ? node->stringify() : std::string("(nothing)"));
    }

    wf::signal::connection_t<wf::keyboard_focus_changed_signal> on_focus =
        [=] (wf::keyboard_focus_changed_signal *sig)
    {
        if (option_str("sonata-corners/debug_input") == "true")
        {
            auto v = wf::node_to_view(sig->new_focus);
            LOGI("sonata-input focus -> ", v ? v->get_app_id() + " \"" + v->get_title() + "\"" :
                (sig->new_focus ? std::string("(a surface that isn't a window)") : std::string("(nothing)")));
        }
    };

    wf::signal::connection_t<wf::post_input_event_signal<wlr_pointer_button_event>> on_button_post =
        [=] (wf::post_input_event_signal<wlr_pointer_button_event> *sig)
    {
        if (sig->event)
        {
            log_button(replaying ? "post(replayed)" : "post", sig->event, wf::input_event_processing_mode_t::FULL);
        }
    };

    void init()
    {
        wf::get_core().connect(&on_button);
        wf::get_core().connect(&on_button_post);
        wf::get_core().connect(&on_focus);
    }

    void fini()
    {
        on_button.disconnect();
        on_button_post.disconnect();
        on_focus.disconnect();
        while (!held.empty())
        {
            release(held.begin()->first);
        }
    }
};

class sonata_corners_t : public wf::plugin_interface_t
{
    const std::string transformer_name = "sonata-corners";
    wf::wl_idle_call idle_update;
    wf::wl_timer<false> late_update;
    short_click_stretch_t short_clicks;      /* taps games can see */

    /* An app that draws its own square frame (Steam: sonata-corners/own_frame_apps,
     * exact app_ids -- its games, "steam_app_<id>", are not rounded). */
    static bool own_frame_app(wayfire_toplevel_view view)
    {
        auto& apps = options().own_frame_apps;
        std::string id = view->get_app_id();
        std::transform(id.begin(), id.end(), id.begin(), [] (unsigned char c) { return std::tolower(c); });
        return std::find(apps.begin(), apps.end(), id) != apps.end();
    }

    /* Decorated by the compositor (margins), or an own-frame app; not fullscreen. */
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
        return (m.left > 0) || (m.top > 0) || own_frame_app(view);
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

    /* A submenu (a popup of a popup): Wayfire puts it inside its parent
     * menu's view node, next to (not inside) the parent's blur -- so it had
     * no glass, and its own blur there came out near-black. Lifted into the
     * layer the menu is in, in front of it, it is drawn like the menu itself:
     * straight onto the screen, with its own blur (the frosted glass). The
     * position stays: popup nodes carry global coordinates. */
    static void lift_nested_popup(wayfire_view view)
    {
        if (!view || (view->role != wf::VIEW_ROLE_UNMANAGED) || !view->get_root_node())
        {
            return;
        }

        auto root = view->get_root_node();
        wf::scene::node_t *n = root->parent();
        if (!n || !wf::node_to_view(n))
        {
            return;                                 /* already in a layer */
        }

        while (n && wf::node_to_view(n))
        {
            n = n->parent();
        }

        auto layer = n ? std::dynamic_pointer_cast<wf::scene::floating_inner_node_t>(n->shared_from_this()) :
            nullptr;
        if (layer)
        {
            wf::scene::readd_front(layer, root);
        }
    }

    void update_all()
    {
        for (auto& v : wf::get_core().get_all_views())
        {
            lift_nested_popup(v);
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

    /* options are cached (options()): read them again after a reload */
    wf::signal::connection_t<wf::reload_config_signal> on_reload =
        [=] (wf::reload_config_signal*) { options_valid = false; };
    wf::signal::connection_t<wf::view_mapped_signal> on_map =
        [=] (wf::view_mapped_signal *ev)
    {
#ifdef SONATA_OUTPUT_CAPTURE
        capture_hide_update(ev->view);
#endif
        update_soon();
    };
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
#ifdef SONATA_OUTPUT_CAPTURE
    output_capture_t output_capture;
#endif
    fps_counter_t fps;
    wf::shared_data::ref_ptr_t<wf::ipc::method_repository_t> ipc_repo;
    wf::ipc::method_callback ipc_fps = [=] (wf::json_t data)
    {
        return fps.ask(data);
    };
    /* which windows are rounded here, by view id (`sonata2 doctor windows`) */
    wf::ipc::method_callback ipc_rounded = [=] (wf::json_t)
    {
        auto response = wf::ipc::json_ok();
        wf::json_t ids = wf::json_t::array();
        for (auto& v : wf::get_core().get_all_views())
        {
            auto t = wf::toplevel_cast(v);
            if (t && t->get_transformed_node()->get_transformer(transformer_name))
            {
                ids.append((int)v->get_id());
            }
        }

        response["rounded"] = ids;
        return response;
    };

    /* The keyboard for a layer surface below the windows (the desktop, while
     * a name is typed): Wayfire only gives it to "exclusive" ones in the top
     * layers (Vini: a new folder's name had to be clicked before typing).
     * {"namespace": "sonata2-wallpaper", "output": "eDP-1"} */
    wf::ipc::method_callback ipc_focus_layer = [=] (wf::json_t data)
    {
        std::string ns  = wf::ipc::json_get_string(data, "namespace");
        std::string out = (data.is_object() && data.has_member("output")) ?
            wf::ipc::json_get_string(data, "output") : "";
        for (auto& v : wf::get_core().get_all_views())
        {
            if ((v->role != wf::VIEW_ROLE_DESKTOP_ENVIRONMENT) || !v->is_mapped() || (v->get_app_id() != ns))
            {
                continue;
            }

            if (!out.empty() && (!v->get_output() || (v->get_output()->to_string() != out)))
            {
                continue;
            }

            wf::get_core().seat->focus_view(v);
            auto response = wf::ipc::json_ok();
            response["id"] = (int)v->get_id();
            return response;
        }

        return wf::ipc::json_error("no such layer surface");
    };

    /* No pointer while the screen is dark (Vini: black, with the pointer in
     * the middle -- a surface mapped under a still pointer never gets it,
     * so its own "no cursor" can't apply). {"hidden": true | false} */
    wf::ipc::method_callback ipc_cursor = [=] (wf::json_t data)
    {
        bool hidden = data.is_object() && data.has_member("hidden") && data["hidden"].is_bool() &&
            data["hidden"].as_bool();
        hidden ? wf::get_core().hide_cursor() : wf::get_core().unhide_cursor();
        return wf::ipc::json_ok();
    };

  public:
    void init() override
    {
        ipc_repo->register_method("sonata/focus-layer", ipc_focus_layer);
        ipc_repo->register_method("sonata/cursor", ipc_cursor);
        ipc_repo->register_method("sonata/fps", ipc_fps);
        ipc_repo->register_method("sonata/rounded", ipc_rounded);
        /* which build runs (session.log): a fix is only in once install.sh rebuilt it */
        LOGI("sonata-corners: build ", SONATA_CORNERS_BUILD);
        short_clicks.init();
        window_capture.init();
#ifdef SONATA_OUTPUT_CAPTURE
        output_capture.init();
        for (auto& v : wf::get_core().get_all_views())
        {
            capture_hide_update(v);
        }
#endif
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
        options_valid = false;                 /* (re)loaded plugin: current values */
        wf::get_core().connect(&on_reload);
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
        ipc_repo->unregister_method("sonata/focus-layer");
        ipc_repo->unregister_method("sonata/cursor");
        wf::get_core().unhide_cursor();
        ipc_repo->unregister_method("sonata/fps");
        ipc_repo->unregister_method("sonata/rounded");
        fps.fini();
        short_clicks.fini();
        window_capture.fini();
#ifdef SONATA_OUTPUT_CAPTURE
        output_capture.fini();
#endif
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

            /* the palette's: its node's code goes with this library (a
             * reload would render through freed code) */
            if (auto t = v->get_transformed_node()->get_transformer(capture_hide_name))
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
