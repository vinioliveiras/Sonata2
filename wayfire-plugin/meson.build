# Sonata's Wayfire plugins (built and installed by install.sh into
# ~/.local/share/wayfire/plugin-manager/install, a folder Wayfire searches
# for plugins and their metadata on its own).
#   meson setup build --prefix ~/.local/share/wayfire/plugin-manager/install --libdir lib
#   meson install -C build
project('sonata-wayfire-plugins', ['c', 'cpp'], version: '1.0', license: 'GPL-3.0-or-later',
  meson_version: '>=0.59.0', default_options: ['cpp_std=c++17', 'warning_level=1'])

wayfire = dependency('wayfire')
# window sharing calls wlroots and libdrm directly (usually already in wayfire's own flags)
wlroots = dependency('wlroots-0.20', 'wlroots-0.19', required: false)
libdrm = dependency('libdrm', required: false)
add_project_arguments(['-DWLR_USE_UNSTABLE', '-DWAYFIRE_PLUGIN'], language: 'cpp')

# display captures without Sonata's own controls (the drawing palette): the
# capture-source protocol's server code, from wayland-protocols
corners_src = ['src/sonata-corners.cpp']
corners_args = []
wl_protocols = dependency('wayland-protocols', required: false)
wl_scanner = find_program('wayland-scanner', required: false)
# (needs a Wayfire that names wlroots' own display-capture global, to hide it)
has_capture_global = meson.get_compiler('cpp').compiles(
  '#include <wayfire/core.hpp>\nvoid f() { (void)wf::get_core().protocols.output_image_capture_source; }',
  dependencies: [wayfire, wlroots], args: ['-DWLR_USE_UNSTABLE', '-std=c++17'],
  name: 'Wayfire exposes the display-capture global')
if wl_protocols.found() and wl_scanner.found() and wlroots.found() and has_capture_global
  capture_xml = wl_protocols.get_variable('pkgdatadir') / 'staging' / 'ext-image-capture-source' / \
    'ext-image-capture-source-v1.xml'
  corners_src += custom_target('ext-image-capture-source-v1-protocol.h', input: capture_xml,
    output: 'ext-image-capture-source-v1-protocol.h', command: [wl_scanner, 'server-header', '@INPUT@', '@OUTPUT@'])
  corners_src += custom_target('ext-image-capture-source-v1-protocol.c', input: capture_xml,
    output: 'ext-image-capture-source-v1-protocol.c', command: [wl_scanner, 'private-code', '@INPUT@', '@OUTPUT@'])
  # its code names ext_foreign_toplevel_handle_v1_interface: wlroots keeps its
  # own copy private, so the plugin failed to load ("undefined symbol") and
  # every window lost its rounded corners -- our own copy, private to us too
  toplevel_xml = wl_protocols.get_variable('pkgdatadir') / 'staging' / 'ext-foreign-toplevel-list' / \
    'ext-foreign-toplevel-list-v1.xml'
  corners_src += custom_target('ext-foreign-toplevel-list-v1-protocol.c', input: toplevel_xml,
    output: 'ext-foreign-toplevel-list-v1-protocol.c', command: [wl_scanner, 'private-code', '@INPUT@', '@OUTPUT@'])
  corners_args += ['-DSONATA_OUTPUT_CAPTURE']
endif

shared_module('sonata-corners', corners_src,
  dependencies: [wayfire, wlroots, libdrm], cpp_args: corners_args,
  install: true, install_dir: get_option('libdir') / 'wayfire')

# Wayfire's resize with "Show window contents while resizing" (off: the
# window's background follows the pointer, its contents fade in on release)
shared_module('sonata-resize', 'src/sonata-resize.cpp',
  dependencies: [wayfire, wlroots],
  install: true, install_dir: get_option('libdir') / 'wayfire')

# Privileged Wayland protocols (screen capture, window control, clipboard,
# virtual input...) only for Sonata and the portal (GNOME / KDE do the same)
shared_module('sonata-privacy', 'src/sonata-privacy.cpp',
  dependencies: [wayfire],
  install: true, install_dir: get_option('libdir') / 'wayfire')

install_data('metadata/sonata-corners.xml', 'metadata/sonata-resize.xml', 'metadata/sonata-privacy.xml',
  install_dir: get_option('datadir') / 'wayfire' / 'metadata')
