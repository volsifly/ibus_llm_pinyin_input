cmake_minimum_required(VERSION 3.16)
project(fcitx5-ai-pinyin LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

set(FCITX5_DEV_ROOT "/tmp/fcitx5-dev-root" CACHE PATH "Extracted fcitx5 dev package root")

add_library(ai-pinyin SHARED ai_pinyin.cpp)
target_include_directories(ai-pinyin PRIVATE
  "${FCITX5_DEV_ROOT}/usr/include/Fcitx5/Core"
  "${FCITX5_DEV_ROOT}/usr/include/Fcitx5/Utils"
  "${FCITX5_DEV_ROOT}/usr/include/Fcitx5/Config"
)
target_link_libraries(ai-pinyin PRIVATE
  /usr/lib/x86_64-linux-gnu/libFcitx5Core.so.7
  /usr/lib/x86_64-linux-gnu/libFcitx5Utils.so.2
  /usr/lib/x86_64-linux-gnu/libFcitx5Config.so.6
)
set_target_properties(ai-pinyin PROPERTIES PREFIX "")

install(TARGETS ai-pinyin DESTINATION "${CMAKE_INSTALL_LIBDIR}/fcitx5")
install(FILES ai-pinyin-addon.conf RENAME ai-pinyin.conf DESTINATION "${CMAKE_INSTALL_DATADIR}/fcitx5/addon")
install(FILES ai-pinyin.conf DESTINATION "${CMAKE_INSTALL_DATADIR}/fcitx5/inputmethod")
