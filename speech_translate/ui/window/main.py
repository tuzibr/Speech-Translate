import os
import subprocess
import sys
from platform import processor, release, system, version
from signal import SIGINT, signal  # Import the signal module to handle Ctrl+C
from threading import Thread
from time import sleep, strftime, time
from tkinter import Canvas, Frame, Menu, StringVar, Tk, Toplevel, filedialog, ttk
from typing import Callable, Dict, Literal, Optional

import pystray
from loguru import logger
from PIL import Image, ImageDraw, ImageTk
from tkhtmlview import HTMLText

from speech_translate._constants import APP_NAME
from speech_translate._logging import init_logging
from speech_translate._path import dir_debug, dir_export, dir_log, p_app_icon, p_splash_image
from speech_translate._version import __version__
from speech_translate.linker import bc, sj
from speech_translate.ui.custom.checkbutton import CustomCheckButton
from speech_translate.ui.custom.combobox import CategorizedComboBox, ComboboxWithKeyNav
from speech_translate.ui.custom.dialog import (
    AlignmentDialog,
    FileImportDialog,
    RefinementDialog,
    TranslateResultDialog,
    prompt_with_choices,
)
from speech_translate.ui.custom.message import mbox
from speech_translate.ui.custom.tooltip import tk_tooltip, tk_tooltips
from speech_translate.utils.audio.device import (
    get_default_host_api,
    get_default_input_device,
    get_default_output_device,
    get_host_apis,
    get_input_devices,
    get_output_devices,
)
from speech_translate.utils.helper import (
    bind_focus_recursively,
    emoji_img,
    kill_thread,
    native_notify,
    open_folder,
    open_url,
    popup_menu,
    rate_similarity,
    tb_copy_only,
    up_first_case,
    windows_os_only,
)
from speech_translate.utils.tk.style import get_current_theme, get_theme_list, init_theme, set_ui_style
from speech_translate.utils.translate.language import (
    TL_ENGINE_SOURCE_DICT,
    TL_ENGINE_TARGET_DICT,
    WHISPER_LANG_LIST,
    get_whisper_lang_source,
)
from speech_translate.utils.whisper.download import (
    download_model,
    get_default_download_root,
    verify_model_faster_whisper,
    verify_model_whisper,
)
from speech_translate.utils.whisper.helper import append_dot_en, create_hallucination_filter, model_keys


# monkey patch subprocess.run
class NoConsolePopen(subprocess.Popen):
    """
    A custom Popen class that disables creation of a console window in Windows.
    """
    def __init__(self, args, **kwargs):
        if system() == 'Windows' and 'startupinfo' not in kwargs:
            kwargs['startupinfo'] = subprocess.STARTUPINFO()
            kwargs['startupinfo'].dwFlags |= subprocess.STARTF_USESHOWWINDOW
        super().__init__(args, **kwargs)


subprocess.Popen = NoConsolePopen


# modify static_ffmpeg add_paths
def add_ffmpeg_to_path(weak=False) -> bool:
    """Add the ffmpeg executable to the path"""
    # pylint: disable=import-outside-toplevel, protected-access
    if getattr(sys, "frozen", False):
        from static_ffmpeg import _add_paths, run
        run.sys.stdout = sys.stderr
        if weak:
            has_ffmpeg = _add_paths._has("ffmpeg") is not None
            has_ffprobe = _add_paths._has("ffprobe") is not None
            if has_ffmpeg and has_ffprobe:
                return False
        ffmpeg, _ = run.get_or_fetch_platform_executables_else_raise()
        os.environ["PATH"] = os.pathsep.join([os.path.dirname(ffmpeg), os.environ["PATH"]])
        return True
    else:
        from static_ffmpeg import _add_paths
        return _add_paths.add_paths()


# Function to handle Ctrl+C and exit just like clicking the exit button
def signal_handler(_sig, _frame):
    logger.info("Received Ctrl+C, exiting...")
    assert bc.mw is not None
    bc.mw.root.after(0, bc.mw.quit_app)


signal(SIGINT, signal_handler)  # Register the signal handler for Ctrl+C


class AppTray:
    """
    Tray app
    """
    def __init__(self):
        self.tray_app = None  # type: ignore
        self.menu = None  # type: ignore
        self.menu_items = None  # type: ignore
        bc.tray = self
        self.__create_tray()

    def __image_fallback(self, width, height, color1, color2):
        # Generate an image and draw a pattern
        image = Image.new("RGB", (width, height), color1)
        dc = ImageDraw.Draw(image)
        dc.rectangle((width // 2, 0, width, height // 2), fill=color2)
        dc.rectangle((0, height // 2, width // 2, height), fill=color2)

        return image

    def __create_tray(self):
        try:
            ico = Image.open(p_app_icon)
        except Exception:
            ico = self.__image_fallback(64, 64, "black", "white")
        try:
            self.menu_items = (
                pystray.MenuItem(f"{APP_NAME} {__version__}", lambda *args: None, enabled=False),  # do nothing
                pystray.MenuItem("Show Main Window", self.open_app),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem(
                    "View",
                    pystray.Menu(
                        pystray.MenuItem("About", lambda *args: bc.mw.open_about()),  # type: ignore
                        pystray.MenuItem("Settings", lambda *args: bc.mw.open_setting()),  # type: ignore
                        pystray.MenuItem("Log", lambda *args: bc.mw.open_log()),  # type: ignore
                        pystray.Menu.SEPARATOR,
                        pystray.MenuItem("Export Directory", lambda *args: bc.mw.open_export_dir()),  # type: ignore
                        pystray.MenuItem("Log Directory", lambda *args: bc.mw.open_log_dir()),  # type: ignore
                        pystray.MenuItem("Debug Directory", lambda *args: bc.mw.open_debug_dir()),  # type: ignore
                        pystray.MenuItem("Model Directory", lambda *args: bc.mw.open_model_dir()),  # type: ignore
                    )
                ),
                pystray.MenuItem(
                    "Show",
                    pystray.Menu(
                        pystray.MenuItem("Transcribed Speech Subtitle Window",
                                         lambda *args: bc.mw.open_detached_tcw()),  # type: ignore
                        pystray.MenuItem("Translated Speech Subtitle Window",
                                         lambda *args: bc.mw.open_detached_tlw()),  # type: ignore
                    )
                ),
                pystray.MenuItem(
                    "Action",
                    pystray.Menu(
                        pystray.MenuItem(
                            "Record",
                            lambda *args: self.open_app() or bc.mw.root.after(0, bc.mw.rec)  # type: ignore
                        ),
                        pystray.MenuItem(
                            "Import File",
                            lambda *args: self.open_app() or bc.mw.root.after(0, bc.mw.import_file)  # type: ignore
                        ),
                        pystray.MenuItem(
                            "Align Result",
                            lambda *args: self.open_app() or bc.mw.root.after(0, bc.mw.align_file)  # type: ignore
                        ),
                        pystray.MenuItem(
                            "Refine Result",
                            lambda *args: self.open_app() or bc.mw.root.after(0, bc.mw.refine_file)  # type: ignore
                        ),
                        pystray.MenuItem(
                            "Translate Result",
                            lambda *args: self.open_app() or bc.mw.root.after(0, bc.mw.translate_file)  # type: ignore
                        ),
                    )
                ),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem(
                    "Visit Repository", lambda *args: open_url("https://github.com/Dadangdut33/Speech-Translate")
                ),
                pystray.MenuItem(
                    "Read Wiki", lambda *args: open_url("https://github.com/Dadangdut33/Speech-Translate/wiki")
                ),
                pystray.MenuItem("Check for Update", lambda *args: bc.mw.check_update()),  # type: ignore
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("Exit", self.exit_app),
                pystray.MenuItem("Hidden onclick", self.open_app, default=True,
                                 visible=False),  # onclick the icon will open_app
            )
            self.menu = pystray.Menu(*self.menu_items)
            self.tray_app = pystray.Icon("Speech Translate", ico, f"Speech Translate V{__version__}", self.menu)
            self.tray_app.run_detached()

            logger.info("Tray created successfully")
        except Exception as e:
            logger.exception(e)
            logger.error(f"Error creating tray: {e}")
            native_notify("Error creating tray app for speech translate", f"{e}")

    def open_app(self):
        assert bc.mw is not None
        bc.mw.show()

    def exit_app(self):
        assert bc.mw is not None
        bc.mw.root.after(0, bc.mw.quit_app)


class MainWindow:
    """
    Main window of the app
    """
    def __init__(self):
        # ------------------ Window ------------------
        # UI
        bc.mw = self
        self.root = Tk()
        self.root.title(APP_NAME + " - Booting up...")
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.root.minsize(600, 300)
        self.root.wm_attributes("-topmost", False)  # Default False
        self.root.lift()
        self.root.attributes('-topmost', True)
        self.root.after_idle(self.root.attributes, '-topmost', False)
        try:
            self.root.iconbitmap(p_app_icon)
        except Exception:
            pass

        # splash
        try:
            self.splash_img = Image.open(p_splash_image)
            self.splash_img = self.splash_img.resize((640, 360))
        except Exception:
            self.splash_img = Image.new("RGB", (640, 360), "black")

        self.root.geometry("640x350")
        self.canvas_splash = Canvas(self.root, width=640, height=345, bg="black", highlightthickness=0)
        self.canvas_splash.pack(side="top", fill="both", expand=True)

        self.img_splash = ImageTk.PhotoImage(self.splash_img)
        self.canvas_splash.create_image(0, 170, image=self.img_splash, anchor="w")
        self.root.update()

        # Flags
        self.always_on_top: bool = False
        self.notified_hidden: bool = False
        self.prompting = False

        # Styles
        self.style = ttk.Style()
        bc.style = self.style

        init_theme()
        bc.native_theme = get_current_theme()  # get first theme before changing
        bc.theme_lists = list(get_theme_list())

        # rearrange some positions
        try:
            bc.theme_lists.remove("sv")
        except Exception:  # sv theme is not available
            bc.theme_lists.remove("sun-valley-dark")
            bc.theme_lists.remove("sun-valley-light")

        bc.theme_lists.remove(bc.native_theme)  # remove native theme from list
        bc.theme_lists.insert(0, bc.native_theme)  # add native theme to top of list
        logger.debug(f"Available Theme to use: {bc.theme_lists}")
        bc.theme_lists.insert(len(bc.theme_lists), "custom")

        set_ui_style(sj.cache["theme"])

        dark = "dark" in sj.cache["theme"]
        bc.wrench_emoji = emoji_img(16, "🔧", dark)
        bc.open_emoji = emoji_img(16, "🚀", dark)
        bc.trash_emoji = emoji_img(16, "🗑️", dark)
        bc.folder_emoji = emoji_img(16, "📁", dark)
        bc.reset_emoji = emoji_img(16, "🔄", dark)
        bc.question_emoji = emoji_img(16, "❔", dark)
        bc.mic_emoji = emoji_img(20, "🎤", dark)
        bc.speaker_emoji = emoji_img(20, "🔊", dark)
        self.root.update()

        def cuda_check():
            bc.cuda = check_cuda_and_gpu()
            logger.info(f"GPU: {get_gpu_info()} | CUDA: {bc.cuda}")

        Thread(target=cuda_check, daemon=True).start()
        # ------------------ Frames ------------------
        self.f1_toolbar = ttk.Frame(self.root)
        self.f1_toolbar.pack(side="top", fill="x", expand=False, pady=(5, 0))

        self.f2_textbox = ttk.Frame(self.root)
        self.f2_textbox.pack(side="top", fill="both", expand=True)

        self.f3_toolbar = ttk.Frame(self.root)
        self.f3_toolbar.pack(side="top", fill="x", expand=False)

        self.f4_statusbar = ttk.Frame(self.root)
        self.f4_statusbar.pack(side="bottom", fill="x", expand=False)

        # ------------------ Elements ------------------
        # -- f1_toolbar
        # model
        self.lbl_model = ttk.Label(self.f1_toolbar, text="Transcribe Model:")
        self.lbl_model.pack(side="left", fill="x", padx=5, pady=5, expand=False)

        self.cb_model = ComboboxWithKeyNav(self.f1_toolbar, values=model_keys, state="readonly")
        self.cb_model.set(sj.cache["model_mw"])
        self.cb_model.pack(side="left", fill="x", padx=5, pady=5, expand=True)
        self.cb_model.bind("<<ComboboxSelected>>", self.cb_model_change)
        tk_tooltips(
            [self.lbl_model, self.cb_model],
            "Each Whisper model have different requirements. The larger the model, the more accurate " \
            "it will be but it will need more resources and time to do its task.\n\nIn terms of speed, " \
            "they are relatively like this:\n- Tiny: ~32x speed\n- Base: ~16x speed\n- Small: ~6x speed" \
            "\n- Medium: ~2x speed\n- Large: ~1x speed\n\n *It is recommended to use Faster-Whisper to " \
            "make the model run 4 times faster for the same accuracy while using less memory " \
            "(you can change this option in setting)",
            wrap_len=400,
        )

        # engine
        self.lbl_engine = ttk.Label(self.f1_toolbar, text="Translate:")
        self.lbl_engine.pack(side="left", fill="x", padx=5, pady=5, expand=False)

        self.cb_engine = CategorizedComboBox(
            self.root, self.f1_toolbar, {
                "Whisper": model_keys,
                "Google Translate": [],
                "DEEPL Translate": [],
                "MyMemoryTranslator": [],
                "LibreTranslate": [],
            }, self.cb_engine_change
        )
        self.cb_engine.set(sj.cache["tl_engine_mw"])
        self.cb_engine.pack(side="left", fill="x", padx=5, pady=5, expand=True)
        tk_tooltips(
            [self.lbl_engine, self.cb_engine],
            "Same as transcribe, larger models are more accurate but are slower and require more power.\n"
            "\nIt is recommended to use google translate for the best result.\n\nIf you want full offline capability, "
            "you can use libretranslate and then host it locally in your PC",
            wrap_len=400,
        )

        # from
        self.lbl_source = ttk.Label(self.f1_toolbar, text="From:")
        self.lbl_source.pack(side="left", padx=5, pady=5)

        self.cb_source_lang = ComboboxWithKeyNav(self.f1_toolbar, state="readonly")
        self.cb_source_lang.set(sj.cache["source_lang_mw"])
        self.cb_source_lang.pack(side="left", padx=5, pady=5, fill="x", expand=True)
        self.cb_source_lang.bind("<<ComboboxSelected>>", lambda _: sj.save_key("source_lang_mw", self.cb_source_lang.get()))

        # to
        self.lbl_to = ttk.Label(self.f1_toolbar, text="To:")
        self.lbl_to.pack(side="left", padx=5, pady=5)

        self.cb_target_lang = ComboboxWithKeyNav(
            self.f1_toolbar, values=[up_first_case(x) for x in WHISPER_LANG_LIST], state="readonly"
        )
        self.cb_target_lang.set(sj.cache["target_lang_mw"])
        self.cb_target_lang.pack(side="left", padx=5, pady=5, fill="x", expand=True)
        self.cb_target_lang.bind("<<ComboboxSelected>>", lambda _: sj.save_key("target_lang_mw", self.cb_target_lang.get()))

        # swap
        self.btn_swap = ttk.Button(self.f1_toolbar, text="Swap", command=self.cb_swap_lang)
        self.btn_swap.pack(side="left", padx=5, pady=5)

        # clear
        self.btn_clear = ttk.Button(self.f1_toolbar, text="Clear", command=self.tb_clear, style="Accent.TButton")
        self.btn_clear.pack(side="left", padx=5, pady=5)

        # -- f2_textBox
        self.tb_transcribed_bg = Frame(self.f2_textbox, bg="#7E7E7E")
        self.tb_transcribed_bg.pack(side="left", fill="both", expand=True, padx=5, pady=5)

        self.sb_transcribed = ttk.Scrollbar(self.tb_transcribed_bg)
        self.sb_transcribed.pack(side="right", fill="y")

        self.tb_transcribed = HTMLText(self.tb_transcribed_bg, height=5, width=25, background=bc.bg_color)
        self.tb_transcribed.bind("<Key>", tb_copy_only)
        self.tb_transcribed.pack(side="left", fill="both", expand=True, padx=1, pady=1)
        self.tb_transcribed.configure(yscrollcommand=self.sb_transcribed.set)
        self.sb_transcribed.configure(command=self.tb_transcribed.yview)

        self.tb_translated_bg = Frame(self.f2_textbox, bg="#7E7E7E")
        self.tb_translated_bg.pack(side="left", fill="both", expand=True, padx=5, pady=5)

        self.sb_translated = ttk.Scrollbar(self.tb_translated_bg)
        self.sb_translated.pack(side="right", fill="y")

        self.tb_translated = HTMLText(self.tb_translated_bg, height=5, width=25, background=bc.bg_color)
        self.tb_translated.bind("<Key>", tb_copy_only)
        self.tb_translated.pack(fill="both", expand=True, padx=1, pady=1)
        self.tb_translated.configure(yscrollcommand=self.sb_translated.set)
        self.sb_translated.configure(command=self.tb_translated.yview)

        # -- f3_toolbar
        self.f3_1 = ttk.Frame(self.f3_toolbar)
        self.f3_1.pack(side="left", fill="x", expand=True)

        self.f3_1_row1 = ttk.Frame(self.f3_1)
        self.f3_1_row1.pack(side="top", fill="x")

        self.f3_1_row2 = ttk.Frame(self.f3_1)
        self.f3_1_row2.pack(side="top", fill="x")

        self.f3_1_row3 = ttk.Frame(self.f3_1)
        self.f3_1_row3.pack(side="top", fill="x")

        # -- hostAPI
        self.lbl_host_api = ttk.Label(self.f3_1_row1, text="HostAPI:", font="TkDefaultFont 9 bold", width=10)
        self.lbl_host_api.pack(side="left", padx=5, pady=0, ipady=0)
        tk_tooltip(
            self.lbl_host_api,
            "HostAPI for the input device. There are many hostAPI for your device and it is recommended to follow the "
            "default value, other than that it might not work or crash the app.",
            wrap_len=350,
        )

        self.cb_host_api = ComboboxWithKeyNav(self.f3_1_row1, values=[], state="readonly")
        self.cb_host_api.bind(
            "<<ComboboxSelected>>", lambda _: sj.save_key("hostAPI", self.cb_host_api.get()) or self.host_api_change()
        )
        self.cb_host_api.pack(side="left", padx=5, pady=0, ipady=0, expand=True, fill="x")

        self.btn_config_host_api = ttk.Button(
            self.f3_1_row1,
            image=bc.wrench_emoji,
            compound="center",
            width=3,
            command=lambda: popup_menu(self.root, self.menu_host_api),
        )
        self.btn_config_host_api.pack(side="left", padx=5, pady=0, ipady=0)
        self.menu_host_api = self.input_device_menu("hostAPI")

        # -- mic
        self.lbl_mic = ttk.Label(self.f3_1_row2, text="Microphone:", font="TkDefaultFont 9 bold", width=10)
        self.lbl_mic.pack(side="left", padx=5, pady=0, ipady=0)
        tk_tooltip(self.lbl_mic, "Microphone for the input device.")

        self.cb_mic = ComboboxWithKeyNav(self.f3_1_row2, values=[], state="readonly")
        self.cb_mic.bind("<<ComboboxSelected>>", lambda _: sj.save_key("mic", self.cb_mic.get()))
        self.cb_mic.pack(side="left", padx=5, pady=0, ipady=0, expand=True, fill="x")

        self.btn_config_mic = ttk.Button(
            self.f3_1_row2,
            image=bc.wrench_emoji,
            compound="center",
            width=3,
            command=lambda: popup_menu(self.root, self.menu_mic),
        )
        self.btn_config_mic.pack(side="left", padx=5, pady=0, ipady=0)

        self.menu_mic = self.input_device_menu("mic")

        # -- speaker
        self.lbl_speaker = ttk.Label(self.f3_1_row3, text="Speaker:", font="TkDefaultFont 9 bold", width=10)
        self.lbl_speaker.pack(side="left", padx=5, pady=0, ipady=0)
        tk_tooltip(self.lbl_speaker, "Speaker to record the system audio")

        self.cb_speaker = ComboboxWithKeyNav(self.f3_1_row3, values=[], state="readonly")
        self.cb_speaker.bind("<<ComboboxSelected>>", lambda _: sj.save_key("speaker", self.cb_speaker.get()))
        self.cb_speaker.pack(side="left", padx=5, pady=0, ipady=0, expand=True, fill="x")

        self.btn_config_speaker = ttk.Button(
            self.f3_1_row3,
            image=bc.wrench_emoji,
            compound="center",
            width=3,
            command=lambda: popup_menu(self.root, self.menu_speaker),
        )
        self.btn_config_speaker.pack(side="left", padx=5, pady=0, ipady=0)

        self.menu_speaker = self.input_device_menu("speaker")

        # -- separator
        self.sep_btn_f3_r1 = ttk.Separator(self.f3_1_row1, orient="vertical")
        self.sep_btn_f3_r1.pack(side="left", fill="y", pady=0, ipady=0)

        self.sep_btn_f3_r2 = ttk.Separator(self.f3_1_row2, orient="vertical")
        self.sep_btn_f3_r2.pack(side="left", fill="y", pady=0, ipady=0)

        self.sep_btn_f3_r3 = ttk.Separator(self.f3_1_row3, orient="vertical")
        self.sep_btn_f3_r3.pack(side="left", fill="y", pady=0, ipady=0)

        # ------
        self.f3_2 = ttk.Frame(self.f3_toolbar)
        self.f3_2.pack(side="left", fill="x")

        self.f3_2_row1 = ttk.Frame(self.f3_2)
        self.f3_2_row1.pack(side="top", fill="x")

        self.f3_2_row2 = ttk.Frame(self.f3_2)
        self.f3_2_row2.pack(side="top", fill="x")

        self.f3_2_row3 = ttk.Frame(self.f3_2)
        self.f3_2_row3.pack(side="top", fill="x")

        self.lbl_task = ttk.Label(self.f3_2_row1, text="Task:", font="TkDefaultFont 9 bold", width=10)
        self.lbl_task.pack(side="left", padx=5, pady=5, ipady=0)

        self.cbtn_task_transcribe = CustomCheckButton(
            self.f3_2_row2,
            sj.cache["transcribe_mw"],
            lambda x: sj.save_key("transcribe_mw", x) or self.cbtn_task_change(),
            text="Transcribe"
        )
        self.cbtn_task_transcribe.pack(side="left", padx=5, pady=3, ipady=0)

        self.cbtn_task_translate = CustomCheckButton(
            self.f3_2_row3,
            sj.cache["translate_mw"],
            lambda x: sj.save_key("translate_mw", x) or self.cbtn_task_change(),
            text="Translate"
        )
        self.cbtn_task_translate.pack(side="left", padx=5, pady=3, ipady=0)

        # ------
        self.f3_3 = ttk.Frame(self.f3_toolbar)
        self.f3_3.pack(side="left", fill="x")

        self.f3_3_row1 = ttk.Frame(self.f3_3)
        self.f3_3_row1.pack(side="top", fill="x")

        self.f3_3_row2 = ttk.Frame(self.f3_3)
        self.f3_3_row2.pack(side="top", fill="x")

        self.f3_3_row3 = ttk.Frame(self.f3_3)
        self.f3_3_row3.pack(side="top", fill="x")

        self.lbl_temp = ttk.Label(self.f3_3_row1, text="Input:", font="TkDefaultFont 9 bold", width=10)
        self.lbl_temp.pack(side="left", padx=5, pady=5, ipady=0)

        self.strvar_input = StringVar()
        self.radio_mic = ttk.Radiobutton(
            self.f3_3_row2,
            text="Microphone",
            value="mic",
            width=12,
            command=lambda: sj.save_key("input", "mic"),
            variable=self.strvar_input,
        )
        self.radio_mic.pack(side="left", padx=5, pady=3, ipady=0)

        self.radio_speaker = ttk.Radiobutton(
            self.f3_3_row3,
            text="Speaker",
            value="speaker",
            width=12,
            command=lambda: sj.save_key("input", "speaker"),
            variable=self.strvar_input,
        )
        self.radio_speaker.pack(side="left", padx=5, pady=3, ipady=0)
        self.strvar_input.set("mic" if sj.cache["input"] == "mic" else "speaker")

        # ------
        self.f3_4 = ttk.Frame(self.f3_toolbar)
        self.f3_4.pack(side="left", fill="x")

        self.f3_4_row1 = ttk.Frame(self.f3_4)
        self.f3_4_row1.pack(side="top", fill="x")

        self.f3_4_row2 = ttk.Frame(self.f3_4)
        self.f3_4_row2.pack(side="top", fill="x")

        self.btn_record = ttk.Button(self.f3_4_row1, text="Record", command=self.rec)
        self.btn_record.pack(side="right", padx=5, pady=5)
        tk_tooltip(self.btn_record, "Record sound from selected input device and process it according to set task")

        self.btn_import_file = ttk.Button(self.f3_4_row2, text="Import file", command=self.import_file)
        self.btn_import_file.pack(side="right", padx=5, pady=5)
        tk_tooltip(self.btn_import_file, "Transcribe/Translate from a file (video or audio)")

        # button
        self.btn_copy = ttk.Button(self.f3_4_row1, text="Copy", command=lambda: popup_menu(self.root, self.menu_copy))
        self.btn_copy.pack(side="right", padx=5, pady=5)
        tk_tooltip(self.btn_copy, "Copy the text to clipboard", wrap_len=250)

        self.menu_copy = Menu(self.root, tearoff=0)
        self.menu_copy.add_command(label="Copy transcribed text", command=lambda: self.copy_tb("transcribed"))
        self.menu_copy.add_command(label="Copy translated text", command=lambda: self.copy_tb("translated"))

        self.btn_tool = ttk.Button(self.f3_4_row2, text="Tool", command=lambda: popup_menu(self.root, self.menu_tool))
        self.btn_tool.pack(side="right", padx=5, pady=5)
        tk_tooltip(
            self.btn_tool,
            "Collection of tools to help you with adjusting the result.",
            wrap_len=250,
        )

        self.menu_tool = Menu(self.root, tearoff=0)
        self.menu_tool.add_command(label="Export Recorded Results", command=self.export_result)
        self.menu_tool.add_command(label="Align Results", command=self.align_file)
        self.menu_tool.add_command(label="Refine Results", command=self.refine_file)
        self.menu_tool.add_command(label="Translate Results (Whisper Result in .json)", command=self.translate_file)

        # -- f4_statusbar
        # load bar
        self.load_bar = ttk.Progressbar(self.f4_statusbar, orient="horizontal", length=100, mode="determinate")
        self.load_bar.pack(side="left", padx=5, pady=5, fill="x", expand=True)

        # ------------------ Menubar ------------------
        self.menubar = Menu(self.root)
        self.fm_file = Menu(self.menubar, tearoff=0)
        self.fm_file.add_checkbutton(label="Stay on top", command=self.toggle_always_on_top)
        self.fm_file.add_separator()
        self.fm_file.add_command(label="Hide", command=self.root.withdraw)
        self.fm_file.add_command(label="Exit", command=self.quit_app)
        self.menubar.add_cascade(label="File", menu=self.fm_file)

        self.fm_view = Menu(self.menubar, tearoff=0)
        self.fm_view.add_command(label="Settings", command=self.open_setting, accelerator="F2")
        self.fm_view.add_command(label="Log", command=self.open_log, accelerator="Ctrl+F1")
        self.fm_view.add_separator()
        self.fm_view.add_command(label="Export Directory", command=self.open_export_dir)
        self.fm_view.add_command(label="Log Directory", command=self.open_log_dir)
        self.fm_view.add_command(label="Debug Directory", command=self.open_debug_dir)
        self.fm_view.add_command(label="Model Directory", command=self.open_model_dir)
        self.menubar.add_cascade(label="View", menu=self.fm_view)

        self.fm_show = Menu(self.menubar, tearoff=0)
        self.fm_show.add_command(
            label="Transcribed Speech Subtitle Window", command=self.open_detached_tcw, accelerator="F3"
        )
        self.fm_show.add_command(label="Translated Speech Subtitle Window", command=self.open_detached_tlw, accelerator="F4")
        self.menubar.add_cascade(label="Show", menu=self.fm_show)

        self.fm_help = Menu(self.menubar, tearoff=0)
        self.fm_help.add_command(label="About", command=self.open_about, accelerator="F1")
        self.fm_help.add_command(
            label="Open documentation / wiki",
            command=lambda: open_url("https://github.com/Dadangdut33/Speech-Translate/wiki")
        )
        self.fm_help.add_command(
            label="Visit Repository", command=lambda: open_url("https://github.com/Dadangdut33/Speech-Translate")
        )
        self.menubar.add_cascade(label="Help", menu=self.fm_help)
        self.fm_help.add_separator()
        self.fm_help.add_command(label="Check for updates", command=self.check_update)

        self.root.configure(menu=self.menubar)

        # ------------------ Bind keys ------------------
        self.root.bind("<Control-F1>", self.open_log)
        self.root.bind("<F1>", self.open_about)
        self.root.bind("<F2>", self.open_setting)
        self.root.bind("<F3>", self.open_detached_tcw)
        self.root.bind("<F4>", self.open_detached_tlw)

        # ------------------ on Start ------------------
        self.root.update()
        bind_focus_recursively(self.root, self.root)
        self.__on_init()

    # on start
    def __on_init(self):
        # update widget on start
        self.cb_input_device_init()
        self.root.update()
        self.cb_engine_change(sj.cache["tl_engine_mw"])
        self.root.update()
        self.cbtn_task_change()
        self.root.update()

        windows_os_only([self.radio_speaker, self.cb_speaker, self.lbl_speaker, self.btn_config_speaker])
        self.root.update()

        Thread(target=create_hallucination_filter, args=["rec", True], daemon=True).start()
        Thread(target=create_hallucination_filter, args=["file", True], daemon=True).start()

        self.root.title(APP_NAME)
        self.root.geometry(sj.cache["mw_size"])
        self.canvas_splash.destroy()
        self.root.update()

        Thread(target=self.check_ffmpeg_start, daemon=True).start()

        def first_open():
            if mbox(
                "Hello! :)", "Welcome to Speech Translate!\n\nIt seems like this is your first time using the app."
                " Would you like to open the documentation to learn more about the app?"
                "\n\n*You can also open it later from the help menu.", 3, self.root
            ):
                open_url("https://github.com/Dadangdut33/Speech-Translate/wiki")
            sj.save_key("first_open", False)

        if sj.cache["first_open"]:
            self.root.after(2500, first_open)

    def check_ffmpeg_start(self):
        """
        Check ffmpeg on start
        
        FFmpeg should already be included when you are using the prebuilt version of the app.
        But if you install from pip or build it yourself, this is probably going to download ffmpeg in the background.
        """
        def check_ffmpeg():
            try:
                logger.debug("Checking ffmpeg...")
                add_ffmpeg_to_path()
                logger.debug("Checking ffmpeg done")
                bc.has_ffmpeg = True
            except Exception as e:
                logger.exception(e)
                logger.error("Failed to check ffmpeg")
                native_notify("Failed to check ffmpeg", "Please check the log for more info.")

        check_ffmpeg_thread = Thread(target=check_ffmpeg, daemon=True)
        check_ffmpeg_thread.start()
        t_start = time()
        notified = False
        probably_downloading = False

        # wait 5 seconds, if not done yet, tell user to wait for a bit
        while check_ffmpeg_thread.is_alive():
            if time() - t_start > 5 and not notified:
                logger.debug("Checking is long... notifying user that it is probably downloading ffmpeg")
                native_notify(
                    "Downloading ffmpeg in background", "Please wait until its done before using the app to avoid any error."
                )
                notified = True
                probably_downloading = True
            sleep(1)

        if probably_downloading:
            logger.debug("Downloading ffmpeg done")
            native_notify("Finished downloading ffmpeg", "You can now use the app without any worry :)")

    # ------------------ Handle window ------------------
    def save_win_size(self):
        """
        Save window size
        """
        w = self.root.winfo_width()
        h = self.root.winfo_height()
        if w > 600 and h > 300:
            sj.save_key("mw_size", f"{w}x{h}")

    def cleanup(self):
        bc.disable_rec()
        bc.disable_file_tc()
        bc.disable_file_tl()

        logger.info("Stopping tray...")
        if bc.tray and bc.tray.tray_app:
            bc.tray.tray_app.stop()

        # destroy windows
        logger.info("Destroying windows...")
        bc.sw.root.destroy()  # type: ignore
        bc.about.root.destroy()  # type: ignore
        bc.ex_tcw.root.destroy()  # type: ignore
        bc.ex_tlw.root.destroy()  # type: ignore
        self.root.destroy()

        if bc.dl_thread and bc.dl_thread.is_alive():
            logger.info("Killing download process...")
            bc.cancel_dl = True

    # Quit the app
    def quit_app(self):
        # save window size
        self.save_win_size()
        bc.sw.save_win_size()  # type: ignore

        self.cleanup()
        logger.info("Exiting...")
        try:
            os._exit(0)
        except SystemExit:
            logger.info("Exit successful")

    def restart_app(self):
        logger.debug("Restarting app...")
        logger.debug(f"Flag tc: {bc.transcribing_file} | Flag tl: {bc.translating_file} | Flag rec: {bc.recording}")
        logger.debug(f"Flag file_processing: {bc.file_processing} | Flag dl: {bc.dl_thread and bc.dl_thread.is_alive()}")
        if bc.transcribing_file or bc.translating_file or bc.recording or bc.file_processing or (
            bc.dl_thread and bc.dl_thread.is_alive()
        ):
            # prompt
            if not mbox(
                "Restarting app...",
                "There is a process still running, are you sure you want to restart the app?"
                "\n\nThis will stop the process "
                "and may cause data loss!",
                3,
            ):
                return

        # save window size
        self.save_win_size()
        bc.sw.save_win_size()  # type: ignore

        self.cleanup()
        logger.info("Restarting...")  # restart
        main(with_log_init=False)

    # Show window
    def show(self):
        self.root.after(0, self.root.deiconify)

    # Close window
    def on_close(self):
        self.save_win_size()

        # if tray available, make hidden
        if bc.tray:
            # Only show notification once
            if not self.notified_hidden and not sj.cache["supress_hidden_to_tray"]:
                native_notify("Hidden to tray", "The app is still running in the background.")
                self.notified_hidden = True

            self.root.withdraw()
        # else quit app, ask confirmation first
        else:
            if mbox(
                "Exit confirmation", "Are you sure you want to exit the app?\n\n" \
                "(This will close every running process in the app)", 3,
                self.root
            ):
                self.quit_app()

    # Toggle Stay on top
    def toggle_always_on_top(self):
        self.always_on_top = not self.always_on_top
        self.root.wm_attributes("-topmost", self.always_on_top)

    def open_export_dir(self):
        open_folder(sj.cache["dir_export"] if sj.cache["dir_export"] != "auto" else dir_export)

    def open_log_dir(self):
        open_folder(sj.cache["dir_log"] if sj.cache["dir_log"] != "auto" else dir_log)

    def open_debug_dir(self):
        open_folder(dir_debug)

    def open_model_dir(self):
        open_folder(sj.cache["dir_model"] if sj.cache["dir_log"] != "auto" else get_default_download_root())

    # ------------------ With After ------------------
    # So that we can call it from outside the mainloop
    # If not yet initialized, will add delay to the call
    def open_about(self, _event=None):
        if not bc.about:
            self.root.after(2000, self.open_about)
            return
        self.root.after(0, bc.about.show)  # type: ignore

    def check_update(self, _event=None):
        if not bc.about:
            self.root.after(2000, self.check_update)
            return
        self.root.after(0, lambda: bc.about.check_for_update(notify_up_to_date=True))  # type: ignore

    def open_setting(self, _event=None):
        if not bc.sw:
            self.root.after(2000, self.open_setting)
            return
        self.root.after(0, bc.sw.show)  # type: ignore

    def open_log(self, _event=None):
        if not bc.lw:
            self.root.after(2000, self.open_log)
            return
        self.root.after(0, bc.lw.show)  # type: ignore

    def open_detached_tcw(self, _event=None):
        if not bc.ex_tcw:
            self.root.after(2000, self.open_detached_tcw)
            return
        self.root.after(0, bc.ex_tcw.show)  # type: ignore

    def open_detached_tlw(self, _event=None):
        if not bc.ex_tlw:
            self.root.after(2000, self.open_detached_tlw)
            return
        self.root.after(0, bc.ex_tlw.show)  # type: ignore

    # ------------------ Functions ------------------
    # error
    def error_notif(self, err: str, use_mbox=False, title="Unexpected Error!"):
        if use_mbox:
            mbox(title, err, 2, self.root)
        else:
            native_notify(title, err)

    def copy_tb(self, mode: Literal["transcribed", "translated"]):
        tb_dict = {"transcribed": self.tb_transcribed, "translated": self.tb_translated}

        self.root.clipboard_clear()
        self.root.clipboard_append(tb_dict[mode].get("1.0", "end"))
        self.root.update()

        self.btn_copy.configure(text="Copied!")

        # reset after .7 second
        self.root.after(700, lambda: self.btn_copy.configure(text="Copy"))

    # mic
    def cb_input_device_init(self):
        """
        Initialize input device combobox

        Will check previous options and set to default if not available.
        If default is not available, will show a warning.
        """
        success, host_detail = get_default_host_api()
        if success:
            assert isinstance(host_detail, Dict)
            def_host = str(host_detail["name"])
        else:
            def_host = ""

        self.cb_host_api["values"] = get_host_apis()
        self.cb_mic["values"] = get_input_devices(def_host)
        self.cb_speaker["values"] = get_output_devices(def_host)

        # Setting previous options
        if sj.cache["hostAPI"] not in self.cb_host_api["values"]:
            self.host_api_set_default(on_init=True)
        else:
            self.cb_host_api.set(sj.cache["hostAPI"])

        # if the previous mic is not available, set to default
        if sj.cache["mic"] not in self.cb_mic["values"]:
            self.mic_set_default()
        else:
            self.cb_mic.set(sj.cache["mic"])

        # If the previous speaker is not available, set to default
        if sj.cache["speaker"] not in self.cb_speaker["values"]:
            self.speaker_set_default()
        else:
            self.cb_speaker.set(sj.cache["speaker"])

    def input_device_menu(self, mode: Literal["hostAPI", "mic", "speaker"]):
        """
        Return a menu for input device combobox

        Args:
            theType (Literal["hostAPI", "mic", "speaker"]): The type of the combobox

        Returns:
            List[str]: A list of menu items
        """
        refresh_dict = {
            "hostAPI": self.host_api_refresh,
            "mic": self.mic_refresh,
            "speaker": self.speaker_refresh,
        }

        set_default_dict = {
            "hostAPI": self.host_api_set_default,
            "mic": self.mic_set_default,
            "speaker": self.speaker_set_default,
        }

        get_default_dict = {
            "hostAPI": get_default_host_api,
            "mic": get_default_input_device,
            "speaker": get_default_output_device,
        }

        updated_menu = Menu(self.btn_config_host_api, tearoff=0)
        updated_menu.add_command(label="Refresh", command=refresh_dict[mode])
        updated_menu.add_command(label="Set to default", command=set_default_dict[mode])

        success, default_host = get_default_dict[mode]()
        if success:
            assert isinstance(default_host, Dict)
            updated_menu.add_separator()
            updated_menu.add_command(label=f"Default: {default_host['name']}", state="disabled")

        return updated_menu

    def host_api_change(self, _event=None):
        """
        Change hostAPI combobox

        Will try to keep the previous mic and speaker if available.
        If not available, will try to get the default device (which may not match because of the difference in hostAPI).
        """
        self.cb_mic["values"] = get_input_devices(self.cb_host_api.get())
        self.cb_speaker["values"] = get_output_devices(self.cb_host_api.get())

        if "[WARNING]" not in self.cb_mic.get():
            # Search mic
            prev_name = self.cb_mic.get().split("|")[1].strip()
            found, index = False, 0
            for i, name in enumerate(self.cb_mic["values"]):
                if prev_name in name:
                    found, index = True, i
                    break
            if found:
                self.cb_mic.current(index)
            else:
                self.mic_set_default()

        if "[WARNING]" not in self.cb_speaker.get():
            # Search speaker
            prev_name = self.cb_speaker.get().split("|")[1].strip()
            found, index = False, 0
            for i, name in enumerate(self.cb_speaker["values"]):
                if prev_name in name:
                    found, index = True, i
                    break
            if found:
                self.cb_speaker.current(index)
            else:
                self.speaker_set_default()

    def host_api_refresh(self, _event=None):
        """
        Refresh hostAPI list and check whether the current hostAPI is still available.
        """
        self.cb_host_api["values"] = get_host_apis()
        # verify if the current hostAPI is still available
        if self.cb_host_api.get() not in self.cb_host_api["values"]:
            self.cb_host_api.current(0)

        self.menu_host_api = self.input_device_menu("hostAPI")

    def host_api_set_default(self, _event=None, on_init=False):
        """
        Set hostAPI to default. Will update the list first.
        -> Show warning error if no default hostAPI found
        -> Set to default hostAPI if found, but will set to the first hostAPI if the default hostAPI is not available
        """
        self.host_api_refresh()  # update list
        success, default_host = get_default_host_api()
        if not success:
            if not ["supress_device_warning"]:
                self.error_notif(str(default_host))

            self.cb_host_api.set("[ERROR] Getting default hostAPI failed")
        else:
            assert isinstance(default_host, Dict)
            if default_host["name"] not in self.cb_host_api["values"]:
                logger.warning(f"Default hostAPI {default_host['name']} not found, set to {self.cb_host_api['values'][0]}")
                if not ["supress_device_warning"]:
                    self.error_notif(
                        f"Default hostAPI {default_host['name']} not found, set to {self.cb_host_api['values'][0]}"
                    )
                self.cb_host_api.current(0)
            else:
                self.cb_host_api.set(default_host["name"])
            sj.save_key("hostAPI", self.cb_host_api.get())

        # update the mic and speaker combobox
        if not on_init:
            self.host_api_change()

    # mic
    def mic_refresh(self, _event=None):
        """
        Refresh microphone list while also checking if the device is still available.
        """
        self.cb_mic["values"] = get_input_devices(self.cb_host_api.get())
        if self.cb_mic.get() not in self.cb_mic["values"]:
            self.cb_mic.current(0)

        self.menu_mic = self.input_device_menu("mic")

    def mic_set_default(self, _event=None):
        """
        Set microphone to default. Will update the list first.
        -> Show warning error if no default mic found
        -> Will search from the currently updated list and set it to the first mic if the default mic is not available
        """
        self.mic_refresh()  # update list
        success, default_device = get_default_input_device()
        if not success:
            if not ["supress_device_warning"]:
                self.error_notif(str(default_device))

            self.cb_mic.set("[WARNING] No default mic found")
        else:
            assert isinstance(default_device, Dict)
            found = False
            index = 0
            for i, name in enumerate(self.cb_mic["values"]):
                if rate_similarity(default_device["name"], name) > 0.6:
                    found = True
                    index = i
                    break

            if not found:
                logger.warning(f"Default mic {default_device['name']} not found, set to {self.cb_mic['values'][0]}")
                if not ["supress_device_warning"]:
                    self.error_notif(f"Default mic {default_device['name']} not found, set to {self.cb_mic['values'][0]}")
                self.cb_mic.current(0)
            else:
                self.cb_mic.set(self.cb_mic["values"][index])
            sj.save_key("mic", self.cb_mic.get())

    # speaker
    def speaker_refresh(self, _event=None):
        """
        Refresh speaker list while also checking if the device is still available.
        """
        self.cb_speaker["values"] = get_output_devices(self.cb_host_api.get())
        if self.cb_speaker.get() not in self.cb_speaker["values"]:
            self.cb_speaker.current(0)

        self.menu_speaker = self.input_device_menu("speaker")

    def speaker_set_default(self, _event=None):
        """
        Set speaker to default.  Will update the list first.
        -> If fail to get speaker, show warning error.
        """
        self.speaker_refresh()  # update list
        success, default_device = get_default_output_device()

        if not success:
            if not ["supress_device_warning"]:
                self.error_notif(str(default_device))

            self.cb_speaker.set("[WARNING] No default speaker found")
        else:
            assert isinstance(default_device, Dict)
            found = False
            index = 0
            for i, name in enumerate(self.cb_speaker["values"]):
                if rate_similarity(default_device["name"], name) > 0.6:
                    found = True
                    index = i
                    break
            if not found:
                logger.warning(f"Default speaker {default_device['name']} not found, set to {self.cb_speaker['values'][0]}")
                if not ["supress_device_warning"]:
                    self.error_notif(
                        f"Default speaker {default_device['name']} not found, set to {self.cb_speaker['values'][0]}"
                    )
                self.cb_speaker.current(0)
            else:
                self.cb_speaker.set(self.cb_speaker["values"][index])
            sj.save_key("speaker", self.cb_speaker.get())

    # clear textboxes
    def tb_clear(self):
        bc.clear_all()

    # Swap textboxes
    def tb_swap_content(self):
        bc.swap_textbox()

    # swap select language and textbox
    def cb_swap_lang(self):
        # swap lang
        temp_target = self.cb_target_lang.get()
        temp_source = self.cb_source_lang.get()
        self.cb_source_lang.set(temp_target)
        self.cb_target_lang.set(temp_source)

        if self.cb_target_lang.get() == "Auto detect":
            self.cb_target_lang.current(0)

        sj.save_key("source_lang_mw", self.cb_source_lang.get())
        sj.save_key("target_lang_mw", self.cb_target_lang.get())
        bc.swap_textbox()

    def cb_model_change(self, _event=None):
        self.cbtn_task_change()  # check because the model changed
        sj.save_key("model_mw", self.cb_model.get())

    def cb_engine_change(self, _event=None):
        # check if engine is whisper and currently in translate only mode
        # if yes, will make the transcribe model combobox disabled
        if _event in model_keys and "selected" in self.cbtn_task_translate.state(
        ) and "selected" not in self.cbtn_task_transcribe.state():
            self.cb_model.configure(state="disabled")
        else:
            self.cb_model.configure(state="readonly")

        # Then update the target cb list with checks
        self.cbtn_task_change()  # updating source_lang with check of task
        self.cb_target_lang["values"] = TL_ENGINE_TARGET_DICT[self.cb_engine.get()]

        # check if the target lang is not in the new list
        if self.cb_target_lang.get() not in self.cb_target_lang["values"]:
            self.cb_target_lang.current(0)
            sj.save_key("target_lang_mw", self.cb_target_lang.get())

        if _event:
            sj.save_key("tl_engine_mw", _event)

    # change mode
    def cbtn_task_change(self, _event=None):
        # tc & tl
        if "selected" in self.cbtn_task_transcribe.state() and "selected" in self.cbtn_task_translate.state():
            self.tb_translated_bg.pack_forget()
            self.tb_translated.pack_forget()

            self.tb_transcribed_bg.pack_forget()
            self.tb_transcribed.pack_forget()

            self.tb_transcribed_bg.pack(side="left", fill="both", expand=True, padx=5, pady=5)
            self.tb_transcribed.pack(fill="both", expand=True, padx=1, pady=1)

            self.tb_translated_bg.pack(side="left", fill="both", expand=True, padx=5, pady=5)
            self.tb_translated.pack(fill="both", expand=True, padx=1, pady=1)

            self.cb_source_lang.configure(state="readonly")
            self.cb_target_lang.configure(state="readonly")
            self.cb_engine.configure(state="readonly")
            self.cb_model.configure(state="readonly")
            self.enable_rec()

            # check if engine is whisper
            if self.cb_engine.get() in model_keys:
                cur_cb_engine = self.cb_engine.get()
                if "V3" in cur_cb_engine and "V3" not in self.cb_model.get():
                    # making sure that Cantonese only present when both model and engine is V3
                    cur_cb_engine = cur_cb_engine.replace("V3", "V2")

                get_whisper_lang = get_whisper_lang_source(cur_cb_engine)
                self.cb_source_lang["values"] = get_whisper_lang
            else:
                # if not whisper, just take directly from the dict
                self.cb_source_lang["values"] = TL_ENGINE_SOURCE_DICT[self.cb_engine.get()]

        # tc only
        elif "selected" in self.cbtn_task_transcribe.state() and "selected" not in self.cbtn_task_translate.state():
            self.tb_transcribed_bg.pack(side="left", fill="both", expand=True, padx=5, pady=5)
            self.tb_transcribed.pack(fill="both", expand=True, padx=1, pady=1)

            self.tb_translated_bg.pack_forget()
            self.tb_translated.pack_forget()

            self.cb_source_lang.configure(state="readonly")
            self.cb_target_lang.configure(state="disabled")
            self.cb_engine.configure(state="disabled")
            self.cb_model.configure(state="readonly")
            self.enable_rec()

            # if tc only, use whisper as language selection
            self.cb_source_lang["values"] = get_whisper_lang_source(self.cb_model.get())

        # tl only
        elif "selected" not in self.cbtn_task_transcribe.state() and "selected" in self.cbtn_task_translate.state():
            self.tb_transcribed_bg.pack_forget()
            self.tb_transcribed.pack_forget()

            self.tb_translated_bg.pack(side="left", fill="both", expand=True, padx=5, pady=5)
            self.tb_translated.pack(fill="both", expand=True, padx=1, pady=1)

            self.cb_source_lang.configure(state="readonly")
            self.cb_target_lang.configure(state="readonly")
            self.cb_engine.configure(state="readonly")
            if self.cb_engine.get() in model_keys:
                self.cb_model.configure(state="disabled")
            else:
                self.cb_model.configure(state="readonly")

            self.enable_rec()
            # check if engine is whisper
            if self.cb_engine.get() in model_keys:
                # if engine is whisper then make sure to use engine to get the source lang
                self.cb_source_lang["values"] = get_whisper_lang_source(self.cb_engine.get())
            else:
                # if not whisper, just take directly from the dict
                self.cb_source_lang["values"] = TL_ENGINE_SOURCE_DICT[self.cb_engine.get()]

        else:  # both not selected
            self.cb_source_lang.configure(state="disabled")
            self.cb_target_lang.configure(state="disabled")
            self.cb_engine.configure(state="disabled")
            self.cb_model.configure(state="disabled")
            self.disable_rec()

        # check if the source lang is not in the new list
        if self.cb_source_lang.get() not in self.cb_source_lang["values"]:
            self.cb_source_lang.current(0)
            sj.save_key("source_lang_f_import", self.cb_source_lang.get())

    def disable_rec(self):
        self.btn_record.configure(state="disabled")
        self.tb_transcribed.configure(state="disabled")
        self.tb_translated.configure(state="disabled")

    def enable_rec(self):
        self.btn_record.configure(state="normal")
        self.tb_transcribed.configure(state="normal")
        self.tb_translated.configure(state="normal")

    def disable_interactions(self):
        self.cbtn_task_transcribe.configure(state="disabled")
        self.cbtn_task_translate.configure(state="disabled")
        self.cb_host_api.configure(state="disabled")
        self.cb_mic.configure(state="disabled")
        self.cb_speaker.configure(state="disabled")
        self.btn_swap.configure(state="disabled")
        self.btn_record.configure(state="disabled")
        self.btn_import_file.configure(state="disabled")
        self.btn_tool.configure(state="disabled")
        self.cb_model.configure(state="disabled")
        self.cb_engine.configure(state="disabled")
        self.cb_source_lang.configure(state="disabled")
        self.cb_target_lang.configure(state="disabled")
        self.radio_mic.configure(state="disabled")
        self.radio_speaker.configure(state="disabled")

    def enable_interactions(self):
        self.cbtn_task_transcribe.configure(state="normal")
        self.cbtn_task_translate.configure(state="normal")
        self.cb_host_api.configure(state="readonly")
        self.cb_mic.configure(state="readonly")
        self.cb_speaker.configure(state="readonly")
        self.btn_swap.configure(state="normal")
        self.btn_record.configure(state="normal")
        self.btn_import_file.configure(state="normal")
        self.btn_tool.configure(state="normal")
        self.radio_mic.configure(state="normal")
        self.radio_speaker.configure(state="normal")

        # if task is translate
        if "selected" not in self.cbtn_task_translate.state():
            self.cb_engine.configure(state="disabled")
            self.cb_target_lang.configure(state="disabled")
        else:
            self.cb_engine.configure(state="readonly")
            self.cb_target_lang.configure(state="readonly")

        # if task is transcribe
        if "selected" not in self.cbtn_task_transcribe.state():
            self.cb_model.configure(state="disabled")
        else:
            self.cb_model.configure(state="readonly")

        # if engine is whisper and currently in translate only mode
        if self.cb_engine.get() in model_keys and "selected" in self.cbtn_task_translate.state(
        ) and "selected" not in self.cbtn_task_transcribe.state():
            self.cb_model.configure(state="disabled")
            self.cb_source_lang.configure(state="disabled")
        else:
            self.cb_model.configure(state="readonly")
            self.cb_source_lang.configure(state="readonly")

        if "selected" not in self.cbtn_task_transcribe.state() and "selected" not in self.cbtn_task_translate.state():
            self.disable_rec()
        else:
            self.enable_rec()

    def start_lb(self):
        """
        Start load bar
        """
        self.load_bar.configure(mode="indeterminate")
        self.load_bar.start(15)

    def stop_lb(self, mode: Literal["mic", "speaker", "file", None] = None):
        """
        Stop load bar

        Parameters
        ----------
        mode : Literal[&quot;mic&quot;, &quot;speaker&quot;, &quot;file&quot;, None], optional
            The mode, by default None, by default None
        """
        self.load_bar.stop()
        self.load_bar.configure(mode="determinate")

        # **change text only**, the function is already set before in the rec function
        if mode == "mic" or mode == "speaker":
            if not bc.recording:
                return
            self.btn_record.configure(text="Stop")
        elif mode == "file":
            self.btn_import_file.configure(text="Import", command=self.import_file)
            self.enable_interactions()

    def get_args(self):
        return (
            "selected" in self.cbtn_task_transcribe.state(),
            "selected" in self.cbtn_task_translate.state(),
            self.cb_model.get(),
            self.cb_engine.get(),
            self.cb_source_lang.get().lower(),
            self.cb_target_lang.get().lower(),
            self.cb_mic.get(),
            self.cb_speaker.get(),
        )

    # ------------------ Export ------------------
    def export_rec(self, mode: Literal["Transcribe", "Translate"]):
        initial_name = f"{mode}d {strftime('%Y-%m-%d %H-%M-%S')}"
        text = str(self.tb_transcribed.get(1.0, "end")) if mode == "Transcribe" else str(self.tb_translated.get(1.0, "end"))
        results = bc.tc_sentences if mode == "Transcribe" else bc.tl_sentences

        # check types. If results contains str that means export is only .txt
        if not any(isinstance(res, str) for res in results):
            valid_types = (
                ("Text File", "*.txt"), ("SubRip Subtitle (SRT)", "*.srt"), ("Advanced Substation Alpha (ASS)", "*.ass"),
                ("Video Text to Track (VTT)", "*.vtt"), ("JavaScript Object Notation (JSON)", "*.json"),
                ("Tab Separated Values (TSV)", "*.tsv"), ("Comma Separated Values (CSV)", "*.csv")
            )
        else:
            valid_types = (("Text File", "*.txt"), )

        file_path = filedialog.asksaveasfilename(
            defaultextension=".txt",
            initialfile=initial_name,
            filetypes=valid_types,
            title=f"Select Format to Export {mode}d text From Record",
            confirmoverwrite=True
        )

        if len(file_path) == 0:  # cancel
            return

        f_name, f_ext = os.path.splitext(file_path)

        if "txt" in f_ext:
            logger.debug(f"Exporting {mode}d text to {file_path}")
            # open file write it
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(text)
        else:
            try:
                # pylint: disable=import-outside-toplevel
                from speech_translate.utils.whisper.save import save_output_stable_ts
                if len(results) == 0:
                    res = results[0]
                    save_output_stable_ts(res, f_name, [f_ext.replace(".", "")], sj)
                else:
                    for i, res in enumerate(results):
                        save_name = f"{f_name}/exported_{i}"  # folderize it
                        logger.debug(f"Exporting {mode}d text to {save_name}")

                        save_output_stable_ts(res, save_name, [f_ext.replace(".", "")], sj)
            except Exception as e:
                logger.exception(e)
                self.error_notif(str(e))

        # open folder
        open_folder(file_path)

    def export_result(self):
        # check based on mode
        if "selected" in self.cbtn_task_transcribe.state() and "selected" not in self.cbtn_task_translate.state():
            text = str(self.tb_transcribed.get(1.0, "end"))

            if len(text.strip()) == 0:
                mbox("Could not export!", "No text to export", 1)
                return

            self.export_rec("Transcribe")
        elif "selected" not in self.cbtn_task_transcribe.state() and "selected" in self.cbtn_task_translate.state():
            text = str(self.tb_translated.get(1.0, "end"))

            if len(text.strip()) == 0:
                mbox("Could not export!", "No text to export", 1)
                return

            self.export_rec("Translate")
        elif "selected" in self.cbtn_task_transcribe.state() and "selected" in self.cbtn_task_translate.state():
            if self.prompting:
                return

            self.prompting = True
            picked = prompt_with_choices(
                self.root, "Choose Result to Export", "Which result do you wish to export?",
                ["Transcribe", "Translate", "Both Transcribe and Translate"]
            )
            self.prompting = False

            if picked is None:
                return

            if "Transcribe" in picked:
                text = str(self.tb_transcribed.get(1.0, "end"))

                if len(text.strip()) == 0:
                    mbox("Could not export Transcribed text!", "No text to export", 1)
                else:
                    self.export_rec("Transcribe")

            if "Translate" in picked:
                text = str(self.tb_translated.get(1.0, "end"))

                if len(text.strip()) == 0:
                    mbox("Could not export Translated text!", "No text to export", 1)
                else:
                    self.export_rec("Translate")

    def destroy_transient_toplevel(self, name, similar=False):
        for child in self.root.winfo_children():
            if isinstance(child, Toplevel):
                try:
                    if child.title() == name:
                        child.destroy()
                        break
                    if similar and name in child.title():
                        child.destroy()
                        break
                except Exception as e:
                    if "bad window path name" in str(e):
                        logger.debug(f"Window {name} already destroyed")
                    else:
                        logger.exception(e)

    def model_dl_cancel(self, **kwargs):
        if not mbox("Cancel confirmation", "Are you sure you want to cancel downloading?", 3, self.root):
            return

        logger.info("Cancelling download...")
        if kwargs.get("enabler", None):
            logger.debug("Running enabler...")
            kwargs["enabler"]()
        bc.cancel_dl = True  # Raise flag to stop

    def after_model_dl(self, taskname: str, task: Optional[Callable] = None, **kwargs):
        if kwargs.get("enabler", None):
            kwargs["enabler"]()

        # ask if user wants to continue using the model
        if task and mbox("Model is now Ready!", f"Continue task? ({taskname})", 3, self.root):
            task()

    def check_model(self, key: str, is_english: bool, taskname: str, task: Optional[Callable] = None, **kwargs):
        model_name = append_dot_en(key, is_english, sj.cache["use_en_model"])
        try:
            if kwargs.get("disabler", None):
                logger.debug("Running disabler...")
                kwargs["disabler"]()

            # check model first
            use_faster_whisper = sj.cache["use_faster_whisper"]
            if kwargs.pop("force_original_whisper", False):
                use_faster_whisper = False

            model_dir = sj.cache["dir_model"] if sj.cache["dir_model"] != "auto" else get_default_download_root()
            if use_faster_whisper:
                ok = verify_model_faster_whisper(model_name, model_dir)
            else:
                ok = verify_model_whisper(model_name, model_dir)

            if not ok:
                if mbox(
                    "Model is not downloaded yet!",
                    f"`{model_name + '` Whisper'  if not use_faster_whisper else model_name + '` Faster Whisper'}" \
                    " Model not found! You will need to download it first!\n\nDo you want to download it now?",
                    3,
                    self.root,
                ):
                    # if true will download the model, after that, the function will run after_func if successfull
                    logger.info("Downloading model...")
                    try:

                        def check_failed():
                            if kwargs.get("enabler", None):
                                logger.debug("Running enabler...")
                                kwargs["enabler"]()

                        # pylint: disable=unnecessary-lambda
                        dl_kwargs = {
                            "after_func": lambda: self.after_model_dl(taskname, task, **kwargs),
                            "use_faster_whisper": use_faster_whisper,
                            "cancel_func": lambda: self.model_dl_cancel(**kwargs),
                            "failed_func": lambda: check_failed()
                        }

                        if sj.cache["dir_model"] != "auto":
                            dl_kwargs = {"download_root": sj.cache["dir_model"]}

                        bc.dl_thread = Thread(
                            target=download_model,
                            args=(model_name, self.root),
                            kwargs=dl_kwargs,
                            daemon=True,
                        )
                        bc.dl_thread.start()
                    except Exception as e:
                        logger.exception(e)
                        self.error_notif(str(e))

                # return false to stop previous task regardless of the answer
                return False, ""
            else:
                return True, model_name
        except Exception as e:
            if "HTTPSConnectionPool" in str(e):
                logger.error("No Internet Connection! / Host might be down")
                if sj.cache["bypass_no_internet"]:
                    logger.info("Bypassing no internet check")
                    return True, model_name  # here we assume model is downloaded
                else:
                    self.error_notif(
                        "Fail to check for model!", title="No Internet Connection! / Host might be down", use_mbox=True
                    )
                    return False, ""
            else:
                logger.exception(e)
                self.error_notif(str(e), use_mbox=True)

                return False, ""
        finally:
            if kwargs.get("enabler", None):
                if bc.dl_thread and bc.dl_thread.is_alive():
                    logger.debug("Download is still running, enabler skipped...")
                else:
                    logger.debug("Running enabler...")
                    kwargs["enabler"]()

    # ------------------ Rec ------------------
    def rec(self):
        if bc.dl_thread and bc.dl_thread.is_alive():
            mbox(
                "Please wait! A model is being downloaded",
                "A Model is still being downloaded! Please wait until it finishes first!",
                1,
            )
            return

        # if rec widget is disabled, return
        if "disabled" in self.btn_record.state():
            return

        is_speaker = "selected" in self.radio_speaker.state()
        if is_speaker and system() != "Windows":  # double checking. Speaker input is only available on Windows
            mbox(
                "Not available",
                "This feature is only available on Windows."
                "\n\nIn order to record PC sound from OS other than Windows you will need to create a virtual audio loopback"
                " to pass the speaker output as an input. You can use software like PulseAudio or Blackhole to do this."
                "\n\nAfter that you can change your default input device to the virtual audio loopback.",
                0,
                self.root,
            )
            return

        # Checking args
        tc, tl, m_key, tl_engine, source, target, mic, speaker = self.get_args()
        if source == target and tl:
            mbox("Invalid options!", "Source and target language cannot be the same", 2)
            return

        # check model first
        tl_whisper = tl_engine in model_keys
        model_tc = None
        m_check_kwargs = {"disabler": self.disable_interactions, "enabler": self.enable_interactions}

        if (tl and not tl_whisper) or tc:  # check tc model if tc or tl only but not whisper
            status, model_tc = self.check_model(m_key, source == "english", "recording", self.rec, **m_check_kwargs)
            if not status:
                return

        if tl and tl_whisper:  # if tl and using whisper engine, check model
            status, tl_engine = self.check_model(tl_engine, source == "english", "recording", self.rec, **m_check_kwargs)
            if not status:
                return

        # if only tl and using whisper, replace model_tc with engine
        if tl and not tc and tl_whisper:
            model_tc = tl_engine

        assert model_tc is not None, (
            "model_tc is not set, this should not happened. " \
            "Report this as a bug at https://github.com/Dadangdut33/Speech-Translate/issues"
        )

        # check when using libre
        if tl and tl_engine == "LibreTranslate":
            # check wether the link is set or not
            if sj.cache["libre_link"].strip() == "":
                mbox(
                    "LibreTranslate host/URL is not set!",
                    "LibreTranslate host/URL is not set! Please set it first in the settings!",
                    2,
                )
                return False

            # check api key
            if not sj.cache["supress_libre_api_key_warning"] and sj.cache["libre_api_key"].strip() == "":
                if not mbox(
                    "LibreTranslate API key is not set!",
                    "WARNING!! LibreTranslate API key is not set! Do you want to continue anyway?",
                    3,
                    self.root,
                ):
                    return False

        # ui changes
        self.tb_clear()
        self.start_lb()
        self.disable_interactions()
        self.btn_record.configure(text="Loading", command=self.rec_stop, state="normal")

        bc.enable_rec()  # Flag update    # Disable recording is by button input

        # Start thread
        try:
            from speech_translate.utils.audio.record import record_session  # pylint: disable=import-outside-toplevel
            device = mic if not is_speaker else speaker
            rec_thread = Thread(
                target=record_session,
                args=(source, target, tl_engine, model_tc, device, tc, tl, is_speaker),
                daemon=True,
            )
            rec_thread.start()
        except Exception as e:
            logger.exception(e)
            self.error_notif(str(e))
            self.rec_stop()
            self.after_rec_stop()

    def rec_stop(self):
        logger.info("Recording Stopped")
        bc.disable_rec()
        kill_thread(bc.rec_tc_thread)
        kill_thread(bc.rec_tl_thread)

        self.btn_record.configure(text="Stopping...", state="disabled")

    def after_rec_stop(self):
        try:
            self.load_bar.stop()
            self.load_bar.configure(mode="determinate")
            self.btn_record.configure(text="Record", command=self.rec)
            self.enable_interactions()
        except Exception as e:
            logger.exception(e)

    # From file
    def import_file(self):
        if bc.dl_thread and bc.dl_thread.is_alive():
            mbox(
                "Please wait! A model is being downloaded",
                "A Model is still being downloaded! Please wait until it finishes first!",
                1,
            )
            return

        # if import widget is disabled, return
        if "disabled" in self.btn_import_file.state():
            return

        def do_process(m_key, tl_engine, source, target, tc, tl, files):
            nonlocal prompt
            # lang is lowered when send from FileImportDialog
            if source == target and tl:
                mbox("Invalid options!", "Source and target language cannot be the same", 2)
                return False

            # check model first
            tl_whisper = tl_engine in model_keys
            model_tc = None
            m_check_kwargs = {"disabler": prompt.disable_interactions, "enabler": prompt.enable_interactions}

            if (tl and not tl_whisper) or tc:  # check tc model if tc or tl only but not whisper
                status, model_tc = self.check_model(m_key, source == "english", "file import", **m_check_kwargs)
                if not status:
                    return False

            if tl and tl_whisper:  # if tl and using whisper engine, check model
                status, tl_engine = self.check_model(tl_engine, source == "english", "file import", **m_check_kwargs)
                if not status:
                    return False

            # if only tl and using whisper, replace model_tc with engine
            if tl and not tc and tl_whisper:
                model_tc = tl_engine

            assert model_tc is not None, (
                "model_tc is not set, this should not happened. " \
                "Report this as a bug at https://github.com/Dadangdut33/Speech-Translate/issues"
            )

            # check when using libre
            if tl and tl_engine == "LibreTranslate":
                # check wether host/url is set or not
                if sj.cache["libre_link"].strip() == "":
                    mbox(
                        "LibreTranslate host/URL is not set!",
                        "LibreTranslate host/URL is not set! Please set it first in the settings!",
                        2,
                    )
                    return False

                # check api key
                if not sj.cache["supress_libre_api_key_warning"] and sj.cache["libre_api_key"].strip() == "":
                    if not mbox(
                        "LibreTranslate API key is not set!",
                        "WARNING!! LibreTranslate API key is not set! Do you want to continue anyway?",
                        3,
                        self.root,
                    ):
                        return False

            # ui changes
            self.tb_clear()
            self.start_lb()
            self.disable_interactions()
            self.btn_import_file.configure(text="Loading", command=lambda: self.from_file_stop(True), state="normal")

            bc.enable_file_process()  # Flag update

            # Start thread
            try:
                from speech_translate.utils.audio.file import process_file  # pylint: disable=import-outside-toplevel
                f_import_thread = Thread(
                    target=process_file, args=(list(files), model_tc, source, target, tc, tl, tl_engine), daemon=True
                )
                f_import_thread.start()

                return True
            except Exception as e:
                logger.exception(e)
                self.error_notif(str(e))
                self.from_file_stop()

                return False

        self.disable_interactions()
        prompt = FileImportDialog(self.root, "Import Files", do_process, sj.cache["theme"])
        self.root.wait_window(prompt.root)  # wait for the prompt to close
        self.enable_interactions()

    def from_file_stop(self, prompt=False, notify=True, master=None):
        if prompt:
            if not mbox(
                "Confirm", "Are you sure you want to cancel the file transcribe/translate process?", 3,
                self.root if master is None else master
            ):
                return

        logger.info("Stopping file import processing...")
        bc.disable_file_process()
        bc.disable_file_tc()
        bc.disable_file_tl()
        self.destroy_transient_toplevel("File Import Progress")

        if notify:
            mbox(
                "Cancelled",
                f"Cancelled file import processing\n\nTranscribed {bc.file_tced_counter} "
                f"and translated {bc.file_tled_counter} file",
                0,
                self.root,
            )

        self.load_bar.stop()
        self.load_bar.configure(mode="determinate")
        self.btn_import_file.configure(text="Import file", command=self.import_file)
        self.enable_interactions()
        logger.info("Stopped")

    def refine_file(self):
        if bc.dl_thread and bc.dl_thread.is_alive():
            mbox(
                "Please wait! A model is being downloaded",
                "A Model is still being downloaded! Please wait until it finishes first!",
                1,
            )
            return

        # if tool widget is disabled, return
        if "disabled" in self.btn_tool.state():
            return

        def do_process(m_key, files):
            nonlocal prompt
            # file = (source_file, mod_file)
            # check model first
            m_check_kwargs = {
                "disabler": prompt.disable_interactions,
                "enabler": prompt.enable_interactions,
                "force_original_whisper": True
            }
            status, model_tc = self.check_model(m_key, False, "file refinement", **m_check_kwargs)
            if not status:
                return False

            # ui changes
            self.tb_clear()
            self.start_lb()
            self.disable_interactions()

            bc.enable_file_process()  # Flag update

            # Start thread
            try:
                from speech_translate.utils.audio.file import mod_result  # pylint: disable=import-outside-toplevel
                refine_thread = Thread(target=mod_result, args=(files, model_tc, "refinement"), daemon=True)
                refine_thread.start()

                return True
            except Exception as e:
                logger.exception(e)
                self.error_notif(str(e))
                self.refinement_stop()

                return False

        self.disable_interactions()
        prompt = RefinementDialog(self.root, "Refine Result", do_process, sj.cache["theme"])
        self.root.wait_window(prompt.root)  # wait for the prompt to close
        self.enable_interactions()

    def refinement_stop(self, prompt=False, notify=True, master=None):
        if prompt:
            if not mbox(
                "Confirm", "Are you sure you want to cancel the refinement process?", 3,
                self.root if master is None else master
            ):
                return

        logger.info("Stopping refinement...")
        bc.disable_file_process()
        self.destroy_transient_toplevel("File Refinement Progress")

        if notify:
            mbox(
                "Cancelled",
                f"Cancelled refinement process\n\nRefined {bc.mod_file_counter} file",
                0,
                self.root,
            )

        self.load_bar.stop()
        self.load_bar.configure(mode="determinate")
        self.enable_interactions()
        logger.info("Stopped")

    def align_file(self):
        if bc.dl_thread and bc.dl_thread.is_alive():
            mbox(
                "Please wait! A model is being downloaded",
                "A Model is still being downloaded! Please wait until it finishes first!",
                1,
            )
            return

        # if tool widget is disabled, return
        if "disabled" in self.btn_tool.state():
            return

        def do_process(m_key, files):
            nonlocal prompt
            # file = (source_file, mod_file, lang)
            # filter lang to check all english or not
            all_english = True
            for file in files:
                if file[2].lower() != "english":
                    all_english = False
                    break

            # load .en model if all language is english
            logger.debug(f"all_english: {all_english} {'(load .en model because all in english)' if all_english else ''}")
            m_check_kwargs = {
                "disabler": prompt.disable_interactions,
                "enabler": prompt.enable_interactions,
                "force_original_whisper": True
            }
            status, model_tc = self.check_model(m_key, all_english, "file alignment", **m_check_kwargs)
            if not status:
                return False

            # ui changes
            self.tb_clear()
            self.start_lb()
            self.disable_interactions()

            bc.enable_file_process()  # Flag update

            # Start thread
            try:
                from speech_translate.utils.audio.file import mod_result  # pylint: disable=import-outside-toplevel
                align_thread = Thread(target=mod_result, args=(files, model_tc, "alignment"), daemon=True)
                align_thread.start()

                return True
            except Exception as e:
                logger.exception(e)
                self.error_notif(str(e))
                self.alignment_stop()

                return False

        self.disable_interactions()
        prompt = AlignmentDialog(self.root, "Align Result", do_process, sj.cache["theme"])
        self.root.wait_window(prompt.root)  # wait for the prompt to close
        self.enable_interactions()

    def alignment_stop(self, prompt=False, notify=True, master=None):
        if prompt:
            if not mbox(
                "Confirm", "Are you sure you want to cancel the alignment process?", 3,
                self.root if master is None else master
            ):
                return

        logger.info("Stopping alignment...")
        bc.disable_file_process()
        self.destroy_transient_toplevel("File Alignment Progress")

        if notify:
            mbox(
                "Cancelled",
                f"Cancelled alignment process\n\nAligned {bc.mod_file_counter} file",
                0,
                self.root,
            )

        self.load_bar.stop()
        self.load_bar.configure(mode="determinate")
        self.enable_interactions()
        logger.info("Stopped")

    def translate_file(self):
        if bc.dl_thread and bc.dl_thread.is_alive():
            mbox(
                "Please wait! A model is being downloaded",
                "A Model is still being downloaded! Please wait until it finishes first!",
                1,
            )
            return

        # if tool widget is disabled, return
        if "disabled" in self.btn_tool.state():
            return

        def do_process(tl_engine, lang_target, files):
            # lang is lowered when send from TranslateResultDialog
            # no check because not using any model and no need for ffmpeg
            # ui changes
            self.tb_clear()
            self.start_lb()
            self.disable_interactions()

            bc.enable_file_process()

            # check when using libre
            if tl_engine == "LibreTranslate":
                # check wether host/URL is set or not
                if sj.cache["libre_link"].strip() == "":
                    mbox(
                        "LibreTranslate host/URL is not set!",
                        "LibreTranslate host/URL is not set! Please set it first in the settings!",
                        2,
                    )
                    return False

                # check api key
                if not sj.cache["supress_libre_api_key_warning"] and sj.cache["libre_api_key"].strip() == "":
                    if not mbox(
                        "LibreTranslate API key is not set!",
                        "WARNING!! LibreTranslate API key is not set! Do you want to continue anyway?",
                        3,
                        self.root,
                    ):
                        return False

            # Start thread
            try:
                from speech_translate.utils.audio.file import translate_result  # pylint: disable=import-outside-toplevel
                res_tl_thread = Thread(target=translate_result, args=(files, tl_engine, lang_target), daemon=True)
                res_tl_thread.start()

                return True
            except Exception as e:
                logger.exception(e)
                self.error_notif(str(e))
                self.translate_stop()

                return False

        self.disable_interactions()
        prompt = TranslateResultDialog(self.root, "Translate Whisper Result", do_process, sj.cache["theme"])
        self.root.wait_window(prompt.root)  # wait for the prompt to close
        self.enable_interactions()

    def translate_stop(self, prompt=False, notify=True, master=None):
        if prompt:
            if not mbox(
                "Confirm", "Are you sure you want to cancel the result translation process?", 3,
                self.root if master is None else master
            ):
                return

        logger.info("Stopping translation...")
        bc.disable_file_process()
        self.destroy_transient_toplevel("Result File Translation Progress")

        if notify:
            mbox(
                "Cancelled",
                f"Cancelled translation process\n\nTranslated {bc.mod_file_counter} file",
                0,
                self.root,
            )

        self.load_bar.stop()
        self.load_bar.configure(mode="determinate")
        self.enable_interactions()
        logger.info("Stopped")


def get_gpu_info():
    result = ""
    try:
        from torch import cuda  # pylint: disable=import-outside-toplevel
        gpu_count = cuda.device_count()
        if gpu_count == 0:
            result = "No GPU detected"
        elif gpu_count == 1:
            result = cuda.get_device_name(0)
        else:
            result = f"{gpu_count} GPUs detected"
    except Exception as e:
        logger.exception(e)
        result = "Failed to detect GPU"

    return result


def check_cuda_and_gpu():
    result = ""
    try:
        from torch import cuda  # pylint: disable=import-outside-toplevel
        if not cuda.is_available():
            result = "CUDA is not available! Using CPU instead"
        else:
            count = cuda.device_count()
            gpus = [cuda.get_device_name(i) for i in range(count)]
            result = f"Detected {count} GPU(s): {', '.join(gpus)}"
    except Exception as e:
        logger.exception(e)
        result = "CUDA fail to check! Failed to detect GPU"

    return result


def main(with_log_init=True):
    if with_log_init:
        init_logging(sj.cache["log_level"])
    logger.info(f"App Version: {__version__} - TIME: {strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info(f"OS: {system()} {release()} {version()} | CPU: {processor()}")
    logger.debug(f"Sys args: {sys.argv}")
    logger.debug("Loading UI...")
    # check tray
    if "--no-tray" in sys.argv:
        logger.debug("No tray mode enabled")
    else:
        AppTray()  # Start tray app in the background
    # --- GUI ---
    main_ui = MainWindow()

    # pylint: disable=import-outside-toplevel
    from speech_translate.ui.window.transcribed import TcsWindow
    main_ui.root.update()
    TcsWindow(main_ui.root)
    main_ui.root.update()

    from speech_translate.ui.window.translated import TlsWindow
    main_ui.root.update()
    TlsWindow(main_ui.root)
    main_ui.root.update()

    from speech_translate.ui.window.setting import SettingWindow
    main_ui.root.update()
    SettingWindow(main_ui.root)
    main_ui.root.update()

    from speech_translate.ui.window.about import AboutWindow
    main_ui.root.update()
    AboutWindow(main_ui.root)
    main_ui.root.update()

    from speech_translate.ui.window.log import LogWindow
    main_ui.root.update()
    LogWindow(main_ui.root)
    main_ui.root.update()

    main_ui.root.mainloop()  # Start mainloop
