[app]
title = 老年视频播放器
package.name = elderlyvideoplayer
package.domain = org.elder.videoplayer

source.dir = .
source.include_exts = py,png,jpg,jpeg,svg,json,txt
source.exclude_dirs = tests, bin, .buildozer

version = 0.1

# 使用 develop 分支，修复 hostpython3 SSL 问题
p4a.branch = develop

# 直接使用 pyenv 的 Python 作为 hostpython3，跳过编译
android.hostpython = /home/hlkj/.pyenv/versions/3.10.0/bin/python3

# NDK 25b 是 ffpyplayer 兼容的版本
android.ndk = 25b
android.ndk_api = 24
android.api = 30
android.minapi = 24

# 不指定版本，让 p4a 自动选择兼容版本
requirements = python3,kivy,ffpyplayer,pyjnius,Pillow

android.archs = arm64-v8a,armeabi-v7a
android.orientation = portrait

android.permissions = READ_EXTERNAL_STORAGE,WRITE_EXTERNAL_STORAGE,READ_MEDIA_VIDEO

android.copy_libs = 1

log_level = 2

[buildozer]
log_level = 2
warn_on_root = 0