# Sonata's Wayfire plugins (built and installed by install.sh into
# ~/.local/share/wayfire/plugin-manager/install, a folder Wayfire searches
# for plugins and their metadata on its own).
#   meson setup build --prefix ~/.local/share/wayfire/plugin-manager/install --libdir lib
#   meson install -C build
project('sonata-wayfire-plugins', 'cpp', version: '1.0', license: 'MIT',
  meson_version: '>=0.59.0', default_options: ['cpp_std=c++17', 'warning_level=1'])

wayfire = dependency('wayfire')
add_project_arguments(['-DWLR_USE_UNSTABLE', '-DWAYFIRE_PLUGIN'], language: 'cpp')

shared_module('sonata-corners', 'src/sonata-corners.cpp',
  dependencies: [wayfire],
  install: true, install_dir: get_option('libdir') / 'wayfire')

install_data('metadata/sonata-corners.xml',
  install_dir: get_option('datadir') / 'wayfire' / 'metadata')
