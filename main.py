"""
老年视频播放器 - 安卓 Kivy 版（最终稳定版）
修复历史记录排序逻辑：新记录插入头部，截断保留最新。
修复：缩略图加载后按压反馈失效bug
"""

import os
import json
import hashlib
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from functools import partial

from kivy.app import App
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.gridlayout import GridLayout
from kivy.uix.button import Button
from kivy.uix.image import Image
from kivy.uix.label import Label
from kivy.uix.scrollview import ScrollView
from kivy.uix.screenmanager import ScreenManager, Screen
from kivy.uix.video import Video
from kivy.uix.slider import Slider
from kivy.uix.popup import Popup
from kivy.clock import Clock
from kivy.core.window import Window
from kivy.utils import get_color_from_hex
from kivy.logger import Logger
from kivy.metrics import dp
from kivy.graphics import Color, Rectangle

# ---------- 安卓专用模块 ----------
try:
    from android.permissions import request_permissions, Permission, check_permission
    from android.storage import primary_external_storage_path
    ANDROID = True
except ImportError:
    ANDROID = False

try:
    from jnius import autoclass, cast
    JNIUS_AVAILABLE = True
except ImportError:
    JNIUS_AVAILABLE = False

# ---------- 配置 ----------
SUPPORTED_EXT = ('.mp4', '.avi', '.mkv', '.mov', '.wmv', '.flv')
MIN_RESUME_DURATION = 180  # 秒
RESUME_OFFSET = 10         # 秒
STATE_FILE = "playback_state.json"
THUMBNAIL_CACHE_DIR = "thumbnails"
MAX_HISTORY = 50
THUMBNAIL_WORKERS = 2
CACHE_EXPIRE_DAYS = 180

# ---------- 存储路径获取 ----------
def get_storage_path():
    if ANDROID:
        try:
            path = primary_external_storage_path()
            if path and os.path.exists(path):
                return path
        except:
            pass
        try:
            Context = autoclass('android.content.Context')
            activity = autoclass('org.kivy.android.PythonActivity').mActivity
            ext_dir = activity.getExternalFilesDir(None)
            if ext_dir:
                parent = ext_dir.getParentFile()
                if parent:
                    parent = parent.getParentFile()
                    if parent:
                        parent = parent.getParentFile()
                        if parent:
                            path = str(parent.getParentFile())
                            if path and os.path.exists(path):
                                return path
        except:
            pass
        try:
            path = os.environ.get('EXTERNAL_STORAGE', '')
            if path and os.path.exists(path):
                return path
        except:
            pass
        try:
            return primary_external_storage_path() or "/storage/emulated/0/"
        except:
            return "/storage/emulated/0/"
    else:
        return os.path.expanduser("~/Desktop")

ROOT_FOLDER = os.path.join(get_storage_path(), "老人视频")

# ---------- 权限 ----------
def request_storage_permissions():
    if not ANDROID:
        return
    try:
        Build = autoclass('android.os.Build')
        sdk_int = Build.VERSION.SDK_INT
    except:
        sdk_int = 0

    perms = []
    if sdk_int >= 33:
        perms.append(Permission.READ_MEDIA_VIDEO)
    else:
        perms.append(Permission.READ_EXTERNAL_STORAGE)
    request_permissions(perms)

def open_app_settings():
    try:
        Intent = autoclass('android.content.Intent')
        Settings = autoclass('android.provider.Settings')
        Uri = autoclass('android.net.Uri')
        intent = Intent(Settings.ACTION_MANAGE_APP_ALL_FILES_ACCESS_PERMISSION)
        intent.setData(Uri.parse("package:" + App.get_running_app().package_name))
        activity = autoclass('org.kivy.android.PythonActivity').mActivity
        activity.startActivity(intent)
    except:
        pass

# ---------- 缩略图 ----------
class ThumbnailGenerator:
    executor = ThreadPoolExecutor(max_workers=THUMBNAIL_WORKERS)

    @staticmethod
    def generate(video_path, callback):
        if not ANDROID or not JNIUS_AVAILABLE:
            def desktop_thumb():
                try:
                    texture = Video.generate_thumbnail(video_path, time=5.0, size=(200, 200))
                    if texture:
                        cache_dir = App.get_running_app().user_data_dir + "/" + THUMBNAIL_CACHE_DIR
                        if not os.path.exists(cache_dir):
                            os.makedirs(cache_dir)
                        hash_name = hashlib.md5(video_path.encode()).hexdigest() + ".jpg"
                        thumb_path = os.path.join(cache_dir, hash_name)
                        try:
                            from PIL import Image as PILImage
                            from kivy.core.image import Image as CoreImage
                            core_img = CoreImage(texture)
                            core_img.save(thumb_path)
                            Clock.schedule_once(lambda dt: callback(thumb_path) if callback else None, 0)
                        except ImportError:
                            Logger.warning("Thumbnail: PIL not installed, cannot save.")
                            Clock.schedule_once(lambda dt: callback(None) if callback else None, 0)
                    else:
                        Clock.schedule_once(lambda dt: callback(None) if callback else None, 0)
                except Exception as e:
                    Logger.error(f"Desktop thumbnail error: {e}")
                    Clock.schedule_once(lambda dt: callback(None) if callback else None, 0)
            threading.Thread(target=desktop_thumb).start()
            return

        def _generate():
            try:
                MediaMetadataRetriever = autoclass('android.media.MediaMetadataRetriever')
                Bitmap = autoclass('android.graphics.Bitmap')
                File = autoclass('java.io.File')
                FileOutputStream = autoclass('java.io.FileOutputStream')
                retriever = MediaMetadataRetriever()
                try:
                    retriever.setDataSource(video_path)
                    frame = retriever.getFrameAtTime(5000000, MediaMetadataRetriever.OPTION_CLOSEST)
                finally:
                    retriever.release()
                if frame is None:
                    Clock.schedule_once(lambda dt: callback(None) if callback else None, 0)
                    return
                cache_dir = App.get_running_app().user_data_dir + "/" + THUMBNAIL_CACHE_DIR
                if not os.path.exists(cache_dir):
                    os.makedirs(cache_dir)
                hash_name = hashlib.md5(video_path.encode()).hexdigest() + ".jpg"
                thumb_path = os.path.join(cache_dir, hash_name)
                out = FileOutputStream(File(thumb_path))
                frame.compress(Bitmap.CompressFormat.JPEG, 80, out)
                out.flush()
                out.close()
                Clock.schedule_once(lambda dt: callback(thumb_path) if callback else None, 0)
            except Exception as e:
                Logger.error(f"Thumbnail generation failed: {e}")
                Clock.schedule_once(lambda dt: callback(None) if callback else None, 0)

        ThumbnailGenerator.executor.submit(_generate)

    @staticmethod
    def shutdown():
        ThumbnailGenerator.executor.shutdown(wait=False)

# ---------- 缓存清理 ----------
def clean_old_cache():
    try:
        cache_dir = App.get_running_app().user_data_dir + "/" + THUMBNAIL_CACHE_DIR
    except:
        return
    if not os.path.exists(cache_dir):
        return
    now = time.time()
    expire_seconds = CACHE_EXPIRE_DAYS * 24 * 3600
    for fname in os.listdir(cache_dir):
        fpath = os.path.join(cache_dir, fname)
        if os.path.isfile(fpath):
            try:
                mtime = os.path.getmtime(fpath)
                if now - mtime > expire_seconds:
                    os.remove(fpath)
            except:
                pass

# ---------- 工具函数 ----------
def get_video_files(folder):
    if not os.path.exists(folder):
        return []
    files = []
    for f in os.listdir(folder):
        if f.lower().endswith(SUPPORTED_EXT):
            full = os.path.join(folder, f)
            if os.path.isfile(full):
                files.append(full)
    files.sort()
    return files

def scan_categories(root):
    categories = {}
    if not os.path.exists(root):
        return categories
    for cat in os.listdir(root):
        cat_path = os.path.join(root, cat)
        if os.path.isdir(cat_path):
            series_list = []
            for sub in os.listdir(cat_path):
                sub_path = os.path.join(cat_path, sub)
                if os.path.isdir(sub_path) and get_video_files(sub_path):
                    series_list.append(sub_path)
            series_list.sort()
            categories[cat] = series_list
    return categories

# ---------- 状态文件 ----------
def get_state_path():
    return os.path.join(App.get_running_app().user_data_dir, STATE_FILE)

def load_records():
    state_path = get_state_path()
    if os.path.exists(state_path):
        try:
            with open(state_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
                return data if isinstance(data, list) else [data] if data else []
        except:
            return []
    return []

def save_records(records):
    # 保留前 MAX_HISTORY 条（最新在头部）
    if len(records) > MAX_HISTORY:
        records = records[:MAX_HISTORY]
    state_path = get_state_path()
    with open(state_path, 'w', encoding='utf-8') as f:
        json.dump(records, f, ensure_ascii=False, indent=2)

# ---------- ThumbButton ----------
class ThumbButton(BoxLayout):
    def __init__(self, video_path=None, **kwargs):
        super().__init__(**kwargs)
        self.size_hint = (1, None)
        self.height = dp(180)
        self.orientation = 'vertical'
        self.spacing = dp(5)
        self.padding = dp(5)

        self.thumb_image = Image(keep_ratio=True, allow_stretch=False, size_hint=(1, 0.8))
        self.thumb_image.source = ''
        self.thumb_image.canvas.before.clear()
        with self.thumb_image.canvas.before:
            self.bg_color = Color(*get_color_from_hex('#9b59b6'))
            self.rect = Rectangle(size=self.thumb_image.size, pos=self.thumb_image.pos)
        self.thumb_image.bind(size=self._update_rect, pos=self._update_rect)

        self.label = Label(text=os.path.basename(video_path) if video_path else '',
                           font_size='16sp', halign='center', valign='middle',
                           size_hint=(1, 0.2), text_size=(self.width, None))
        self.label.bind(size=self._update_label_size)

        self.add_widget(self.thumb_image)
        self.add_widget(self.label)

        self.video_path = video_path
        self.thumbnail_path = None
        self.on_press_callback = None
        self._pressed = False

    def _update_rect(self, instance, value):
        self.rect.size = instance.size
        self.rect.pos = instance.pos

    def _update_label_size(self, instance, value):
        instance.text_size = (instance.width, None)

    def set_thumbnail(self, path):
        if path and os.path.exists(path):
            self.thumbnail_path = path
            self.thumb_image.source = path
            self.thumb_image.reload()
            self.label.text = ''
        else:
            if not self.thumb_image.source:
                self.label.text = os.path.basename(self.video_path) if self.video_path else ''

    def on_touch_down(self, touch):
        if self.collide_point(*touch.pos):
            self._pressed = True
            if hasattr(self, 'bg_color'):
                self.bg_color.rgb = (0.7, 0.7, 0.7)
            touch.grab(self)
            return True
        return super().on_touch_down(touch)

    def on_touch_up(self, touch):
        if touch.grab_current is self:
            if self._pressed and self.collide_point(*touch.pos):
                if self.on_press_callback:
                    self.on_press_callback()
            self._pressed = False
            if hasattr(self, 'bg_color'):
                self.bg_color.rgb = get_color_from_hex('#9b59b6')
            touch.ungrab(self)
            return True
        return super().on_touch_up(touch)

# ---------- 屏幕定义 ----------
class MainScreen(Screen):
    def on_enter(self):
        self.clear_widgets()
        app = App.get_running_app()
        if not os.path.exists(ROOT_FOLDER):
            self.show_folder_missing_popup()
            return
        self.build_main_ui()

    def build_main_ui(self):
        layout = BoxLayout(orientation='vertical', padding=dp(20), spacing=dp(20))
        title = Label(text="老年视频播放器", font_size='36sp', size_hint_y=0.15)
        layout.add_widget(title)
        btn_layout = GridLayout(cols=3, spacing=dp(30), size_hint_y=0.7)
        btn1 = Button(text='黄梅戏', on_press=lambda x: self.go_to_category('黄梅戏'),
                      background_color=get_color_from_hex('#e74c3c'), color=(1,1,1,1), font_size='24sp')
        btn2 = Button(text='电视剧', on_press=lambda x: self.go_to_category('电视剧'),
                      background_color=get_color_from_hex('#3498db'), color=(1,1,1,1), font_size='24sp')
        btn3 = Button(text='历史播放', on_press=lambda x: self.go_to_history(),
                      background_color=get_color_from_hex('#e67e22'), color=(1,1,1,1), font_size='24sp')
        btn_layout.add_widget(btn1)
        btn_layout.add_widget(btn2)
        btn_layout.add_widget(btn3)
        layout.add_widget(btn_layout)
        refresh_btn = Button(text='🔄 刷新', font_size='24sp', size_hint_y=0.1,
                             background_color=get_color_from_hex('#2ecc71'))
        refresh_btn.bind(on_press=lambda x: self.refresh_data())
        layout.add_widget(refresh_btn)
        self.add_widget(layout)
        app.scan_categories()

    def show_folder_missing_popup(self):
        content = BoxLayout(orientation='vertical', spacing=dp(10))
        content.add_widget(Label(text='未找到【老人视频】文件夹，\n请在手机内部存储根目录创建此文件夹，放入视频。'))
        btn_layout = BoxLayout(size_hint_y=0.3)
        settings_btn = Button(text='跳转设置开启权限')
        cancel_btn = Button(text='知道了')
        btn_layout.add_widget(settings_btn)
        btn_layout.add_widget(cancel_btn)
        content.add_widget(btn_layout)
        popup = Popup(title='提示', content=content, size_hint=(0.8, None), height=dp(250))
        settings_btn.bind(on_press=lambda x: self.go_settings())
        cancel_btn.bind(on_press=popup.dismiss)
        popup.open()

    def go_settings(self):
        open_app_settings()

    def go_to_category(self, cat_name):
        app = App.get_running_app()
        if cat_name in app.categories:
            app.current_category = cat_name
            self.manager.current = 'series_list'
        else:
            popup = Popup(title='提示', content=Label(text=f'没有“{cat_name}”分类'), size_hint=(0.6, None), height=dp(150))
            popup.open()

    def go_to_history(self):
        self.manager.current = 'history_list'

    def refresh_data(self):
        app = App.get_running_app()
        app.scan_categories()
        self.manager.current = 'main'

class SeriesListScreen(Screen):
    def on_enter(self):
        self.clear_widgets()
        app = App.get_running_app()
        cat = app.current_category
        if cat not in app.categories or not app.categories[cat]:
            popup = Popup(title='提示', content=Label(text=f'“{cat}”分类下没有剧集'), size_hint=(0.6, None), height=dp(150))
            popup.open()
            self.manager.current = 'main'
            return
        layout = BoxLayout(orientation='vertical', padding=dp(10), spacing=dp(10))
        back = Button(text='⬅ 返回', size_hint_y=0.08, font_size='24sp',
                      background_color=get_color_from_hex('#95a5a6'))
        back.bind(on_press=lambda x: setattr(self.manager, 'current', 'main'))
        layout.add_widget(back)
        scroll = ScrollView()
        grid = GridLayout(cols=3, spacing=dp(20), size_hint_y=None)
        grid.bind(minimum_height=grid.setter('height'))
        for series_path in app.categories[cat]:
            videos = get_video_files(series_path)
            if videos:
                btn = ThumbButton(video_path=videos[0])
                btn.on_press_callback = lambda p=series_path: self.on_series_click(p)
                grid.add_widget(btn)
                ThumbnailGenerator.generate(videos[0], lambda path: btn.set_thumbnail(path))
        scroll.add_widget(grid)
        layout.add_widget(scroll)
        self.add_widget(layout)

    def on_series_click(self, series_path):
        app = App.get_running_app()
        app.current_series = series_path
        app.current_videos = get_video_files(series_path)
        if not app.current_videos:
            popup = Popup(title='提示', content=Label(text='该剧集没有视频'), size_hint=(0.6, None), height=dp(150))
            popup.open()
            return
        self.manager.current = 'video_list'

class VideoListScreen(Screen):
    def on_enter(self):
        self.clear_widgets()
        app = App.get_running_app()
        if not app.current_videos:
            self.manager.current = 'series_list'
            return
        layout = BoxLayout(orientation='vertical', padding=dp(10), spacing=dp(10))
        back = Button(text='⬅ 返回剧集', size_hint_y=0.08, font_size='24sp',
                      background_color=get_color_from_hex('#95a5a6'))
        back.bind(on_press=lambda x: setattr(self.manager, 'current', 'series_list'))
        layout.add_widget(back)
        scroll = ScrollView()
        grid = GridLayout(cols=3, spacing=dp(20), size_hint_y=None)
        grid.bind(minimum_height=grid.setter('height'))
        for video_path in app.current_videos:
            btn = ThumbButton(video_path=video_path)
            btn.on_press_callback = lambda p=video_path: self.on_video_click(p)
            grid.add_widget(btn)
            ThumbnailGenerator.generate(video_path, lambda path: btn.set_thumbnail(path))
        scroll.add_widget(grid)
        layout.add_widget(scroll)
        self.add_widget(layout)

    def on_video_click(self, video_path):
        app = App.get_running_app()
        app.current_video = video_path
        try:
            idx = app.current_videos.index(video_path)
        except ValueError:
            idx = 0
        app.current_index = idx
        self.manager.current = 'player'

class HistoryListScreen(Screen):
    def on_enter(self):
        self.clear_widgets()
        app = App.get_running_app()
        records = load_records()
        # 过滤无效记录（已删除的视频）
        valid_records = []
        for rec in records:
            series_path = rec.get('series_path')
            video_idx = rec.get('video_index', 0)
            if series_path and os.path.exists(series_path):
                videos = get_video_files(series_path)
                if videos and video_idx < len(videos):
                    valid_records.append(rec)
        # 更新存储（去除无效记录）
        if len(valid_records) != len(records):
            save_records(valid_records)

        if not valid_records:
            popup = Popup(title='提示', content=Label(text='没有历史记录'), size_hint=(0.6, None), height=dp(150))
            popup.open()
            self.manager.current = 'main'
            return

        # 直接遍历（最新已在头部）
        layout = BoxLayout(orientation='vertical', padding=dp(10), spacing=dp(10))
        back = Button(text='⬅ 返回', size_hint_y=0.08, font_size='24sp',
                      background_color=get_color_from_hex('#95a5a6'))
        back.bind(on_press=lambda x: setattr(self.manager, 'current', 'main'))
        layout.add_widget(back)
        scroll = ScrollView()
        grid = GridLayout(cols=3, spacing=dp(20), size_hint_y=None)
        grid.bind(minimum_height=grid.setter('height'))
        for rec in valid_records:
            series_path = rec['series_path']
            video_idx = rec['video_index']
            videos = get_video_files(series_path)
            if not videos or video_idx >= len(videos):
                continue
            video_path = videos[video_idx]
            btn = ThumbButton(video_path=video_path)
            btn.on_press_callback = lambda r=rec: self.on_history_click(r)
            grid.add_widget(btn)
            ThumbnailGenerator.generate(video_path, lambda path: btn.set_thumbnail(path))
        scroll.add_widget(grid)
        layout.add_widget(scroll)
        self.add_widget(layout)

    def on_history_click(self, record):
        app = App.get_running_app()
        series_path = record['series_path']
        video_idx = record['video_index']
        position = record.get('position', 0)
        if not os.path.exists(series_path):
            return
        videos = get_video_files(series_path)
        if not videos or video_idx >= len(videos):
            return
        app.current_series = series_path
        app.current_videos = videos
        app.current_video = videos[video_idx]
        app.current_index = video_idx
        app.resume_position = max(0, position - RESUME_OFFSET)
        self.manager.current = 'player'

class PlayerScreen(Screen):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.video = None
        self.is_playing = False
        self._position_callback = None
        self._duration_callback = None
        self._state_callback = None
        self._error_callback = None
        self._next_triggered = False

    def on_enter(self):
        self._next_triggered = False
        self.clear_widgets()
        app = App.get_running_app()
        if not app.current_video:
            self.manager.current = 'main'
            return
        layout = BoxLayout(orientation='vertical')
        self.video = Video(source=app.current_video, state='play')
        self._position_callback = self.video.bind(position=self.on_position)
        self._duration_callback = self.video.bind(duration=self.on_duration)
        self._state_callback = self.video.bind(state=self.on_video_state)
        self._error_callback = self.video.bind(error=self.on_video_error)

        control = BoxLayout(size_hint_y=0.12, spacing=dp(10), padding=dp(10))
        with control.canvas.before:
            Color(0, 0, 0, 0.5)
            self.control_rect = Rectangle(size=control.size, pos=control.pos)
        control.bind(size=self._update_control_rect, pos=self._update_control_rect)

        self.play_btn = Button(text='⏸', font_size='28sp', background_normal='')
        self.play_btn.bind(on_press=self.toggle_play)
        self.progress = Slider(min=0, max=1000, value=0)
        self.progress.bind(on_touch_up=self.seek)
        self.time_label = Label(text='00:00 / 00:00', font_size='20sp', color=(1,1,1,1))
        back_btn = Button(text='⬅', font_size='28sp', background_normal='')
        back_btn.bind(on_press=lambda x: self.go_back())

        control.add_widget(self.play_btn)
        control.add_widget(self.progress)
        control.add_widget(self.time_label)
        control.add_widget(back_btn)

        layout.add_widget(self.video)
        layout.add_widget(control)
        self.add_widget(layout)

        if app.resume_position and app.resume_position > 0:
            Clock.schedule_once(lambda dt: self.video.seek(app.resume_position) if self.video else None, 0.8)
            app.resume_position = 0

    def _update_control_rect(self, instance, value):
        self.control_rect.size = instance.size
        self.control_rect.pos = instance.pos

    def toggle_play(self, instance):
        if not self.video:
            return
        if self.video.state == 'play':
            self.video.state = 'pause'
            self.play_btn.text = '▶'
        else:
            self.video.state = 'play'
            self.play_btn.text = '⏸'

    def seek(self, instance, touch):
        if instance.collide_point(*touch.pos) and self.video and self.video.duration > 0:
            val = instance.value
            self.video.seek(val / 1000.0 * self.video.duration)

    def on_position(self, instance, pos):
        if self.video and self.video.duration > 0:
            duration = self.video.duration
            self.progress.value = pos / duration * 1000
            self.time_label.text = f"{self._format_time(pos)} / {self._format_time(duration)}"
            if pos >= duration - 0.5 and duration > 0:
                if not self._next_triggered:
                    self._next_triggered = True
                    self.go_to_next()
        if pos > MIN_RESUME_DURATION:
            self.save_state(pos)

    def on_duration(self, instance, duration):
        pass

    def on_video_state(self, instance, state):
        pass

    def on_video_error(self, instance, error):
        popup = Popup(title='播放错误', content=Label(text='视频无法播放，文件可能损坏或格式不支持。'),
                      size_hint=(0.7, None), height=dp(150))
        popup.open()
        self.go_back()

    def _format_time(self, seconds):
        if seconds < 0 or not isinstance(seconds, (int, float)):
            return "00:00"
        m = int(seconds // 60)
        s = int(seconds % 60)
        return f"{m:02d}:{s:02d}"

    def save_state(self, pos):
        app = App.get_running_app()
        if not app.current_series or not app.current_videos:
            return
        records = load_records()
        current = {'series_path': app.current_series, 'video_index': app.current_index, 'position': pos}
        # 更新或插入（新记录插到头部）
        updated = False
        for i, rec in enumerate(records):
            if rec.get('series_path') == app.current_series:
                records[i] = current
                updated = True
                break
        if not updated:
            records.insert(0, current)  # 新记录放最前面
        save_records(records)

    def go_to_next(self):
        app = App.get_running_app()
        next_idx = app.current_index + 1
        if next_idx < len(app.current_videos):
            app.current_video = app.current_videos[next_idx]
            app.current_index = next_idx
            self.on_enter()
        else:
            popup = Popup(title='提示', content=Label(text='本剧集全部播放完毕'), size_hint=(0.6, None), height=dp(150))
            popup.open()
            self.go_back()

    def go_back(self):
        if self.video:
            self.video.state = 'stop'
            if self._position_callback:
                self.video.unbind(position=self._position_callback)
            if self._duration_callback:
                self.video.unbind(duration=self._duration_callback)
            if self._state_callback:
                self.video.unbind(state=self._state_callback)
            if self._error_callback:
                self.video.unbind(error=self._error_callback)
            self.video = None
        app = App.get_running_app()
        app.current_video = None
        self.manager.current = 'video_list'

    def on_leave(self):
        self.go_back()

# ---------- 应用主类 ----------
class ElderPlayerApp(App):
    def build(self):
        request_storage_permissions()
        self.categories = {}
        self.current_category = ''
        self.current_series = ''
        self.current_videos = []
        self.current_video = ''
        self.current_index = 0
        self.resume_position = 0

        sm = ScreenManager()
        sm.add_widget(MainScreen(name='main'))
        sm.add_widget(SeriesListScreen(name='series_list'))
        sm.add_widget(VideoListScreen(name='video_list'))
        sm.add_widget(HistoryListScreen(name='history_list'))
        sm.add_widget(PlayerScreen(name='player'))
        return sm

    def on_start(self):
        clean_old_cache()

    def scan_categories(self):
        self.categories = scan_categories(ROOT_FOLDER)

    def on_stop(self):
        ThumbnailGenerator.shutdown()

if __name__ == '__main__':
    ElderPlayerApp().run()
