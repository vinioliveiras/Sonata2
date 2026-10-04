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
 */
#include <wayfire/compositor-view.hpp>
#include <wayfire/render-manager.hpp>
#include <wayfire/view-transform.hpp>
#include <wayfire/util/duration.hpp>
#include <wayfire/scene-render.hpp>
#include <wayfire/scene-operations.hpp>
#include <wayfire/opengl.hpp>
#include <wayfire/core.hpp>
#include "wayfire/geometry.hpp"
#include "wayfire/plugins/common/input-grab.hpp"
#include "wayfire/scene-input.hpp"
#include "wayfire/txn/transaction-manager.hpp"
#include <wayfire/toplevel.hpp>
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
    float ds = max(sdf(pos - vec2(0.0, 10.0)), 0.0);
    float shadow = 0.32 * exp(-pow(ds / 22.0, 2.0)) * (1.0 - inside);
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
        output->connect(&on_view_disappeared);
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

        if (!output->activate_plugin(&grab_interface))
        {
            return false;
        }

        input_grab->set_wants_raw_input(true);
        input_grab->grab_input(wf::scene::layer::OVERLAY);

        grab_start = get_input_coords();
        grabbed_geometry = view->get_geometry();
        if (view->pending_tiled_edges())
        {
            view->toplevel()->pending().tiled_edges = 0;
        }

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
        if (outline)
        {
            begin_outline();
        }

        return true;
    }

    void input_pressed(uint32_t state)
    {
        if (state != WLR_BUTTON_RELEASED)
        {
            return;
        }

        input_grab->ungrab_input();
        output->deactivate_plugin(&grab_interface);

        if (view && outline)
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

    void input_motion()
    {
        auto input = get_input_coords();
        int dx     = (int)input.x - (int)grab_start.x;
        int dy     = (int)input.y - (int)grab_start.y;

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
        finish_fade();
        close_ghost();
    }

    // -- Sonata ------------------------------------------------------------------------
    /* The window as seen: a window pixdecor decorates (terminals, X11 apps)
     * has its shadow inside its geometry -- the panel came out that much
     * bigger than the window (Vini). The same inset as sonata-corners. */
    static wf::geometry_t visible(wayfire_toplevel_view v, wf::geometry_t g)
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

        if ((v->pending_tiled_edges() != 0) && (!max_shad || (max_shad->get_value_str() != "true")))
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
        fade_started = true;
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
            fading->damage();
            on_new_size.disconnect();
            fading = nullptr;
        }

        output->render->rem_effect(&fade_hook);
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
