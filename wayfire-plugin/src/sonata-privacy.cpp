/*
 * Sonata privacy: privileged Wayland protocols only for Sonata.
 *
 * Wayfire offers every client the protocols a desktop shell needs: capture
 * any screen or window (screencopy, image-copy-capture, export-dmabuf), see
 * and control every window (foreign-toplevel), read and set the clipboard
 * at any time (data-control), type and move the pointer (virtual keyboard,
 * virtual pointer), lock the screen, draw over everything (layer-shell),
 * change displays and their colours. GNOME and KDE give these only to their
 * own shell; apps go through the portals, which ask first.
 *
 * Here the same: those globals are hidden from every client except
 *   - Sonata's own processes (python3 -m sonata2 ...),
 *   - the screen-sharing portal (xdg-desktop-portal-wlr: it asks with
 *     Sonata's picker),
 *   - the helper tools Sonata runs (grim, wf-recorder, wl-copy, wtype...)
 *     when Sonata (or the portal) started them -- the same tool started by
 *     an app gets nothing,
 *   - programs named in sonata-privacy/trusted (input methods, a VNC server,
 *     a status bar of one's own...).
 * A client in another mount namespace (Flatpak, Snap, any sandbox) is never
 * trusted, whatever it calls itself. Every hidden global is logged once per
 * client (session.log) so a program that stopped working can be found.
 *
 * Copyright (c) 2026 Vini. GPL-3.0-or-later.
 */
#include <wayfire/core.hpp>
#include <wayfire/plugin.hpp>
#include <wayfire/util/log.hpp>
#include <wayfire/option-wrapper.hpp>
#include <wayland-server-core.h>

#include <cstring>
#include <fstream>
#include <map>
#include <memory>
#include <set>
#include <sstream>
#include <string>
#include <sys/stat.h>
#include <unistd.h>
#include <vector>

namespace sonata_privacy
{
/* interface names of the privileged globals */
static const std::set<std::string> PRIVILEGED = {
    "zwlr_screencopy_manager_v1",
    "zwlr_export_dmabuf_manager_v1",
    "ext_image_copy_capture_manager_v1",
    "ext_output_image_capture_source_manager_v1",
    "ext_foreign_toplevel_image_capture_source_manager_v1",
    "zwlr_foreign_toplevel_manager_v1",
    "ext_foreign_toplevel_list_v1",
    "zwlr_data_control_manager_v1",
    "ext_data_control_manager_v1",
    "zwp_virtual_keyboard_manager_v1",
    "zwlr_virtual_pointer_manager_v1",
    "ext_session_lock_manager_v1",
    "zwlr_input_inhibit_manager_v1",
    "zwlr_layer_shell_v1",
    "zwlr_gamma_control_manager_v1",
    "zwlr_output_manager_v1",
    "zwlr_output_power_manager_v1",
};

/* tools Sonata runs: trusted only when Sonata (or the portal) started them */
static const std::set<std::string> HELPERS = {
    "grim", "slurp", "wf-recorder", "wl-copy", "wl-paste", "wtype", "wlsunset",
    "wlr-randr", "swayidle", "wayvnc",
};

static const std::set<std::string> SERVICES = {"xdg-desktop-portal-wlr"};

std::string read_file(const std::string& path)
{
    std::ifstream f(path, std::ios::binary);
    std::stringstream ss;
    ss << f.rdbuf();
    return ss.str();
}

std::vector<std::string> cmdline(pid_t pid)
{
    std::vector<std::string> out;
    std::string raw = read_file("/proc/" + std::to_string(pid) + "/cmdline");
    size_t start = 0;
    for (size_t i = 0; i < raw.size(); i++)
    {
        if (raw[i] == '\0')
        {
            out.push_back(raw.substr(start, i - start));
            start = i + 1;
        }
    }

    if (start < raw.size())
    {
        out.push_back(raw.substr(start));
    }

    return out;
}

std::string exe_name(pid_t pid)
{
    char buf[4096];
    ssize_t n = readlink(("/proc/" + std::to_string(pid) + "/exe").c_str(), buf, sizeof(buf) - 1);
    if (n <= 0)
    {
        return "";
    }

    buf[n] = '\0';
    std::string path(buf);
    auto deleted = path.find(" (deleted)");
    if (deleted != std::string::npos)
    {
        path.resize(deleted);
    }

    auto slash = path.rfind('/');
    return slash == std::string::npos ? path : path.substr(slash + 1);
}

pid_t parent_of(pid_t pid)
{
    /* /proc/<pid>/stat: "pid (comm) state ppid ..." -- comm may hold spaces/parens */
    std::string st = read_file("/proc/" + std::to_string(pid) + "/stat");
    auto close = st.rfind(')');
    if (close == std::string::npos)
    {
        return 0;
    }

    std::istringstream rest(st.substr(close + 2));
    std::string state;
    pid_t ppid = 0;
    rest >> state >> ppid;
    return ppid;
}

bool same_mount_namespace(pid_t pid)
{
    struct stat mine, theirs;
    if (stat("/proc/self/root", &mine) != 0)
    {
        return false;
    }

    if (stat(("/proc/" + std::to_string(pid) + "/root").c_str(), &theirs) != 0)
    {
        return false;
    }

    return mine.st_dev == theirs.st_dev && mine.st_ino == theirs.st_ino;
}

bool is_sonata(pid_t pid)
{
    if (exe_name(pid).rfind("python", 0) != 0)
    {
        return false;
    }

    auto args = cmdline(pid);
    for (size_t i = 0; i + 1 < args.size(); i++)
    {
        if ((args[i] == "-m") && (args[i + 1] == "sonata2"))
        {
            return true;
        }
    }

    return false;
}

bool is_service(pid_t pid)
{
    return SERVICES.count(exe_name(pid)) > 0;
}

class privacy_plugin : public wf::plugin_interface_t
{
    std::unique_ptr<wf::wayland_global_filter_t> filter;
    wf::option_wrapper_t<std::string> trusted_opt{"sonata-privacy/trusted"};
    wf::option_wrapper_t<bool> enabled_opt{"sonata-privacy/enabled"};
    wf::option_wrapper_t<bool> enforce_opt{"sonata-privacy/enforce"};

    struct client_info
    {
        bool trusted = false;
        std::string name;
        std::set<std::string> logged;
        wl_listener destroy;
        privacy_plugin *self = nullptr;
    };

    std::map<const wl_client*, std::unique_ptr<client_info>> clients;

    std::set<std::string> trusted_names()
    {
        std::set<std::string> out;
        std::istringstream in((std::string)trusted_opt);
        std::string w;
        while (in >> w)
        {
            out.insert(w);
        }

        return out;
    }

    bool trusted_pid(pid_t pid)
    {
        if ((pid <= 0) || !same_mount_namespace(pid))
        {
            return false;               /* a sandbox: never */
        }

        std::string exe = exe_name(pid);
        if (is_sonata(pid) || is_service(pid) || trusted_names().count(exe))
        {
            return true;
        }

        if (HELPERS.count(exe))
        {
            pid_t p = parent_of(pid);
            for (int depth = 0; depth < 4 && p > 1; depth++, p = parent_of(p))
            {
                if (is_sonata(p) || is_service(p))
                {
                    return true;
                }
            }
        }

        return false;
    }

    client_info *info_for(const wl_client *client)
    {
        auto it = clients.find(client);
        if (it != clients.end())
        {
            return it->second.get();
        }

        auto info = std::make_unique<client_info>();
        pid_t pid = 0;
        uid_t uid;
        gid_t gid;
        wl_client_get_credentials((wl_client*)client, &pid, &uid, &gid);
        info->trusted = trusted_pid(pid);
        info->name    = exe_name(pid) + " (pid " + std::to_string(pid) + ")";
        info->self    = this;
        info->destroy.notify = [] (wl_listener *l, void *data)
        {
            client_info *ci = wl_container_of(l, ci, destroy);
            wl_list_remove(&ci->destroy.link);
            ci->self->clients.erase((const wl_client*)data);
        };
        wl_client_add_destroy_listener((wl_client*)client, &info->destroy);
        auto *raw = info.get();
        clients[client] = std::move(info);
        return raw;
    }

  public:
    void init() override
    {
        filter = wf::get_core().create_global_filter();
        filter->set_filter([=] (const wl_client *client, const wl_global *global)
        {
            if (!enabled_opt)
            {
                return true;
            }

            const wl_interface *iface = wl_global_get_interface(global);
            if (!iface || !PRIVILEGED.count(iface->name))
            {
                return true;
            }

            client_info *ci = info_for(client);
            if (ci->trusted)
            {
                return true;
            }

            bool enforce = enforce_opt;
            if (ci->logged.insert(iface->name).second)
            {
                LOGI("sonata-privacy: ", iface->name, enforce ? " hidden from " : " would be hidden from ",
                    ci->name);
            }

            return !enforce;            /* learning mode: only logged */
        });
    }

    void fini() override
    {
        filter.reset();
        for (auto& [client, info] : clients)
        {
            wl_list_remove(&info->destroy.link);
        }

        clients.clear();
    }
};
}

DECLARE_WAYFIRE_PLUGIN(sonata_privacy::privacy_plugin);
