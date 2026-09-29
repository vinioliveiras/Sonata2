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
#include <memory>
#include <string>

#include <wayfire/core.hpp>
#include <wayfire/opengl.hpp>
#include <wayfire/view.hpp>
#include <wayfire/toplevel-view.hpp>
#include <wayfire/plugin.hpp>
#include <wayfire/output.hpp>
#include <wayfire/view-transform.hpp>
#include <wayfire/signal-definitions.hpp>
#include <wayfire/config/config-manager.hpp>

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

varying highp vec2 uvpos;

void main()
{
    vec4 c = get_pixel(uvpos);
    vec2 p = vec2(uvpos.x * size.x, (1.0 - uvpos.y) * size.y);
    vec2 lo = rect.xy;
    vec2 hi = rect.xy + rect.zw;
    if (radius > 0.0 && p.x >= lo.x && p.y >= lo.y && p.x <= hi.x && p.y <= hi.y)
    {
        vec2 q = clamp(p, lo + vec2(radius), hi - vec2(radius));
        float d = length(p - q);
        c *= clamp(radius + 0.5 - d, 0.0, 1.0);
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
    wf::option_wrapper_t<int> radius_opt{"sonata-corners/radius"};

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
        auto bbox = self->get_children_bounding_box();
        wlr_box fb_geom = data.target.framebuffer_box_from_geometry_box(data.target.geometry);
        auto view_box   = data.target.framebuffer_box_from_geometry_box(bbox);
        view_box.x -= fb_geom.x;
        view_box.y -= fb_geom.y;
        float x = view_box.x, y = view_box.y, w = view_box.width, h = view_box.height;

        /* the frame, relative to the texture's top left */
        auto g = view->get_geometry();
        double inset = decoration_shadow(view);
        float rx = g.x + inset - bbox.x;
        float ry = g.y + inset - bbox.y;
        float rw = g.width - 2 * inset;
        float rh = g.height - 2 * inset;
        float radius = std::max(0, (int)radius_opt);

        auto data_ptr = wf::get_core().get_data<corners_program_t>(program_name);
        static const float vertexData[] = {
            -1.0f, -1.0f,
            1.0f, -1.0f,
            1.0f, 1.0f,
            -1.0f, 1.0f
        };
        static const float texCoords[] = {
            0.0f, 0.0f,
            1.0f, 0.0f,
            1.0f, 1.0f,
            0.0f, 1.0f
        };

        data.pass->custom_gles_subpass(data.target, [&]
        {
            auto src_tex = get_texture(1.0);
            auto gl_tex  = wf::gles_texture_t{src_tex};

            data_ptr->program.use(gl_tex.type);
            data_ptr->program.uniform2f("size", bbox.width, bbox.height);
            data_ptr->program.uniform4f("rect", glm::vec4{rx, ry, rw, rh});
            data_ptr->program.uniform1f("radius", radius);
            data_ptr->program.attrib_pointer("position", 2, 0, vertexData);
            data_ptr->program.attrib_pointer("texcoord", 2, 0, texCoords);
            data_ptr->program.uniformMatrix4f("mvp", wf::gles::output_transform(data.target));
            GL_CALL(glActiveTexture(GL_TEXTURE0));
            data_ptr->program.set_active_texture(gl_tex);

            wf::gles::bind_render_buffer(data.target);
            GL_CALL(glViewport(x, fb_geom.height - y - h, w, h));
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

class corners_node_t : public wf::scene::view_2d_transformer_t
{
    wayfire_toplevel_view view;

  public:
    corners_node_t(wayfire_toplevel_view view) : wf::scene::view_2d_transformer_t(view)
    {
        this->view = view;
    }

    void gen_render_instances(std::vector<render_instance_uptr>& instances,
        damage_callback push_damage, wf::output_t *shown_on) override
    {
        instances.push_back(std::make_unique<corners_render_instance_t>(this, push_damage, view));
    }
};

class sonata_corners_t : public wf::plugin_interface_t
{
    const std::string transformer_name = "sonata-corners";
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
        } else if (!wanted(view) && have)
        {
            tnode->rem_transformer(have);
        }
    }

    void update_all()
    {
        for (auto& v : wf::get_core().get_all_views())
        {
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

  public:
    void init() override
    {
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
        update_all();
    }

    void fini() override
    {
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
