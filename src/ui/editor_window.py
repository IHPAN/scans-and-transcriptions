""" przeglądarka skanów i transkrypcji """
import os
import re
import json
import logging
import threading
import time
import warnings
import tkinter as tk
from tkinter import filedialog, messagebox
from PIL import Image
import ttkbootstrap as ttk
from ttkbootstrap.constants import *
from ttkbootstrap.widgets.tableview import Tableview

from app.models import AppConfig
from app.paths import prompt_file, prompts_dir
from services.cache_service import calculate_checksum, get_ner_json_path, save_cache
from services.config_service import load_app_config, load_localization, save_app_config
from services.datalab_service import (
    DEFAULT_DATALAB_MODE,
    DEFAULT_DATALAB_OUTPUT_FORMAT,
    convert_image as datalab_convert_image,
    get_api_key as get_datalab_api_key,
)
from services.export_service import (
    collect_ner_rows,
    export_docx,
    export_html_tables_as_files,
    export_html_tables_xlsx,
    export_markdown,
    export_merged_html_table,
    export_tei,
    export_txt,
    unique_ner_names,
    write_ner_csv,
)
from services.gemini_service import (
    DEFAULT_ANALYSIS_MODEL,
    DEFAULT_API_TIMEOUT_SECONDS,
    DEFAULT_BOX_MODEL,
    DEFAULT_FIX_MODEL,
    DEFAULT_HTR_MODEL,
    build_nominative_map,
    correct_transcription,
    extract_entities,
    locate_entities,
    stream_transcribe_image,
    transcribe_image,
    verify_transcription,
)
from services.image_filter_service import ensure_filtered_image, is_generated_filter_image
from services.mistral_service import (
    DEFAULT_MISTRAL_OCR_MODEL,
    get_api_key as get_mistral_api_key,
    ocr_image as mistral_ocr_image,
)
from services.ollama_service import (
    DEFAULT_OLLAMA_BASE_URL,
    DEFAULT_OLLAMA_MODEL,
    build_nominative_map as ollama_build_nominative_map,
    correct_transcription as ollama_correct_transcription,
    extract_entities as ollama_extract_entities,
    locate_entities as ollama_locate_entities,
    stream_transcribe_image as ollama_stream_transcribe_image,
    transcribe_image as ollama_transcribe_image,
    verify_transcription as ollama_verify_transcription,
)
from services.prompt_service import DEFAULT_PROMPT_TEMPLATE, ensure_prompt_dir, read_default_prompt, read_prompt
from services.pdf_service import can_extract_pdf_pages, extract_pdf_pages
from services.text_service import build_diff_ranges, prepare_text_for_tei, tag_entities_tei, tk_index_from_offset
from services.usage_log_service import append_usage_log, read_usage_log
from ui.batch_controller import BatchController
from ui.canvas_controller import CanvasController
from ui.dialogs import (
    edit_current_prompt as open_prompt_editor_dialog,
    open_batch_dialog as open_batch_dialog_window,
    open_settings_dialog as open_settings_dialog_window,
)
from ui.window_utils import set_scaled_geometry

# Aplikacja pracuje na lokalnych, zaufanych skanach archiwalnych, które bywają bardzo duże.
Image.MAX_IMAGE_PIXELS = None
warnings.simplefilter("ignore", Image.DecompressionBombWarning)
logger = logging.getLogger(__name__)

class ToolTip:
    """ klasa tworząca dymek z podpowiedzią z opóźnieniem (500ms) """
    def __init__(self, widget, text, delay=500):
        self.widget = widget
        self.text = text
        self.delay = delay
        self.tip_window = None
        self.id = None  # ID planowanego zdarzenia .after()

        self.widget.tooltip = self

        self.widget.bind("<Enter>", self.schedule)
        self.widget.bind("<Leave>", self.unschedule)
        self.widget.bind("<ButtonPress>", self.unschedule) # ukrywanie po kliknięciu

    def update_text(self, new_text):
        """ metoda do zmiany treści podpowiedzi po zmianie języka """
        self.text = new_text

    def schedule(self, event=None):
        """ planowanie wyświetlenia dymka po upływie self.delay """
        self.unschedule()
        self.id = self.widget.after(self.delay, self.show_tip)

    def unschedule(self, event=None):
        """ anuluje planowanie i usuwa okno dymka """
        if self.id:
            id_to_cancel = self.id
            self.id = None
            self.widget.after_cancel(id_to_cancel)

        # zamkanie okna jeśli istnieje
        if self.tip_window:
            tw = self.tip_window
            self.tip_window = None
            tw.destroy()

    def show_tip(self):
        """ wyświetlanie okna z dymkiem i podpowiedzią """
        if not self.text:
            return

        # obliczanie pozycji (nad widgetem)
        x = self.widget.winfo_rootx() + 10
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 5

        self.tip_window = tw = tk.Toplevel(self.widget)
        tw.wm_overrideredirect(True)
        tw.wm_geometry(f"+{x}+{y}")
        tw.attributes("-topmost", True) # zawsze nad oknem głównym

        label = tk.Label(tw, text=self.text, justify=tk.LEFT,
                         background="#ffffe1", relief=tk.SOLID, borderwidth=1,
                         font=("Segoe UI", "9", "normal"), padx=8, pady=4)
        label.pack()


class ManuscriptEditor:
    """ główna klasa aplikacji """
    def __init__(self, root):
        self.root = root
        self.current_lang = "PL" # domyślny język UI
        self.localization = {} # słownik wersji językowych
        self.local_file = "localization.json"
        self.languages = []
        self.load_lang()

        self.llm_provider = "gemini"
        self.api_key = ""
        self.default_prompt = ""
        self.htr_model = DEFAULT_HTR_MODEL
        self.fix_model = DEFAULT_FIX_MODEL
        self.analysis_model = DEFAULT_ANALYSIS_MODEL
        self.box_model = DEFAULT_BOX_MODEL
        self.ollama_base_url = DEFAULT_OLLAMA_BASE_URL
        self.ollama_htr_model = DEFAULT_OLLAMA_MODEL
        self.ollama_fix_model = DEFAULT_OLLAMA_MODEL
        self.ollama_analysis_model = DEFAULT_OLLAMA_MODEL
        self.ollama_box_model = DEFAULT_OLLAMA_MODEL
        self.ollama_remove_table_headers = False
        self.ollama_pretty_html = True
        self.mistral_ocr_model = DEFAULT_MISTRAL_OCR_MODEL
        self.mistral_include_blocks = False
        self.mistral_table_format = "markdown"
        self.datalab_output_format = DEFAULT_DATALAB_OUTPUT_FORMAT
        self.datalab_mode = DEFAULT_DATALAB_MODE
        self.api_timeout_seconds = DEFAULT_API_TIMEOUT_SECONDS
        self.stream_transcription = True
        self.batch_parallel_workers = 1
        self.prompt_text = ""
        self.prompt_filename_var = tk.StringVar(master=self.root, value="Brak (wybierz plik)")
        self.current_folder_var = tk.StringVar(master=self.root, value="Nie wybrano katalogu")
        self.current_folder_path = ""
        self.last_folder = ""
        self.current_prompt_path = None

        self.config_file = "config.json"
        self.font_family = "Consolas"
        self.font_size = 12

        self.load_config()
        self.t = self.localization[self.current_lang]

        self.root.title(self.t["title"])
        set_scaled_geometry(self.root, 1600, 900)
        self._init_environment()

        self.MODEL_PRICES = {
            "gemini-4-argon": (2.0, 10.0),
            "gemini-3.1-pro-preview": (2.0, 12.0),
            "gemini-3.5-flash": (1.5, 9.0),
            "gemini-3.1-flash-lite": (0.25, 1.5),
            "gemini-3-pro-image-previe": (2.0, 12.0),
            "gemini-3.1-flash-image": (0.5, 3.0),
            "gemini-3.8-flash": (0.75, 3.75),
        }

        self.file_pairs = []
        self.current_index = 0
        self.original_image = None
        self.processed_image = None
        self.tk_image = None
        self.scale = 1.0
        self.img_x = 0
        self.img_y = 0
        self.last_mouse_x = 0
        self.last_mouse_y = 0

        self.active_filter = "normal"

        self.is_transcribing = False
        self.btn_ai = None
        self.stop_batch_flag = False

        self.batch_log_label = None
        self.batch_vars = None
        self.batch_tree = None
        self.batch_progress = None

        self.last_entities = [] # zapamiętana lista nazw własnych dla bieżącej strony

        # główny kontener
        self.paned = ttk.Panedwindow(root, orient=HORIZONTAL)
        self.paned.pack(fill=BOTH, expand=True, padx=10, pady=10)

        # lewy panel (na obraz)
        self.left_frame = ttk.Frame(self.paned)
        self.paned.add(self.left_frame, weight=3)

        # ramka na canvas z obramowaniem
        self.canvas_frame = ttk.Labelframe(self.left_frame, text=self.t["frame_scan"], bootstyle="info")
        self.canvas_frame.pack(fill=BOTH, expand=True)

        # canvas
        self.canvas = tk.Canvas(self.canvas_frame,
                                bg="#2b2b2b",
                                highlightthickness=0,
                                cursor="fleur")
        self.canvas.pack(fill=BOTH, expand=True, padx=2, pady=2)

        # zdarzenia myszy
        self.canvas.bind("<ButtonPress-1>", self.on_mouse_down)
        self.canvas.bind("<B1-Motion>", self.on_mouse_drag)
        self.canvas.bind("<MouseWheel>", self.on_mouse_wheel)
        self.canvas.bind("<Button-4>", self.on_mouse_wheel)
        self.canvas.bind("<Button-5>", self.on_mouse_wheel)
        self.canvas.bind("<Button-3>", self.show_magnifier)
        self.canvas.bind("<B3-Motion>", self.update_magnifier)
        self.canvas.bind("<ButtonRelease-3>", self.hide_magnifier)

        self.magnifier_win = None
        self.mag_label = None
        self.tk_mag_img = None

        self.dragging_box_tag = None
        self.box_to_data_map = {}  # mapowanie ramek na skanie
        self.resizing_box_tag = None

        self.cursor_resizing = "bottom_right_corner"
        self.cursor_move = "fleur"
        if os.name == 'nt': # Windows
            self.cursor_resizing = "sizenwse" #?
            self.cursor_move = "fleur"

        self.active_box_tag = None
        self.box_action = None
        self.canvas_controller = CanvasController(self)
        self.batch_controller = BatchController(self)

        # pasek statusu pod obrazem
        self.image_tools = ttk.Frame(self.left_frame)
        self.image_tools.pack(fill=X, pady=5)

        # lewa strona paska (instrukcja + Zoom info)
        left_tools = ttk.Frame(self.image_tools)
        left_tools.pack(side=LEFT)

        self.zoom_label = ttk.Label(left_tools, text="Zoom: 100%", font=("Segoe UI", 9, "bold"))
        self.zoom_label.pack(side=LEFT, pady=5)
        self.lbl_left_tools = ttk.Label(left_tools,
                  text=self.t["left_tools"],
                  font=("Segoe UI", 8), bootstyle="secondary")
        self.lbl_left_tools.pack(side=LEFT, pady=5, padx=10)

        # prawa strona paska
        tools_frame = ttk.Frame(self.image_tools)
        tools_frame.pack(side=RIGHT)

        self.btn_fit = ttk.Button(tools_frame, text="<->", command=self.fit_to_width,
                   bootstyle="success-outline", padding=2)
        self.btn_fit.pack(side=LEFT, padx=(5,5))

        self.lbl_filters = ttk.Label(tools_frame, text=self.t["lbl_filters"], font=("Segoe UI", 8))
        self.lbl_filters.pack(side=LEFT)
        self.btn_reset = ttk.Button(tools_frame, text=self.t["filter_reset"], command=lambda: self.apply_filter("normal"),
                   bootstyle="outline-secondary", padding=2)
        self.btn_reset.pack(side=LEFT, padx=1)
        self.btn_contrast = ttk.Button(tools_frame, text=self.t["filter_contrast"], command=lambda: self.apply_filter("contrast"),
                   bootstyle="outline-info", padding=2)
        self.btn_contrast.pack(side=LEFT, padx=1)
        self.btn_inverse = ttk.Button(tools_frame, text=self.t["filter_invert"], command=lambda: self.apply_filter("invert"),
                   bootstyle="outline-dark", padding=2)
        self.btn_inverse.pack(side=LEFT, padx=(1,5))

        # prawy panel (na edytor transkrypcji)
        self.right_frame = ttk.Frame(self.paned)
        self.paned.add(self.right_frame, weight=1)

        # pasek ścieżki do katalogu ze skanami
        self.folder_status_frame = ttk.Frame(self.right_frame)
        self.folder_status_frame.pack(fill=X, padx=5, pady=(0, 5))

        self.lbl_folder_status = ttk.Label(self.folder_status_frame, text=self.t["folder_path"],
                  font=("Segoe UI", 8, "bold"))
        self.lbl_folder_status.pack(side=LEFT)

        # etykieta ze ścieżką
        ttk.Label(self.folder_status_frame, textvariable=self.current_folder_var,
                  font=("Segoe UI", 8), bootstyle="dark").pack(side=LEFT, padx=5)

        # przycisk zmiany folderu ze skanami
        self.btn_folder_change = ttk.Button(self.folder_status_frame,
                                       text=self.t["btn_folder_change"],
                                       command=self.select_folder,
                                       bootstyle="link-secondary",
                                       cursor="hand2", padding=0)

        self.btn_settings = ttk.Button(
            self.folder_status_frame,
            text=self.t["btn_settings"],
            command=self.open_settings_dialog,
            bootstyle="link-info",
            cursor="hand2",
            padding=0,
        )
        self.btn_pdf_import = ttk.Button(
            self.folder_status_frame,
            text=self.t["btn_pdf_import"],
            command=self.import_pdf_to_current_folder,
            bootstyle="link-primary",
            cursor="hand2",
            padding=0,
        )
        self.btn_settings.pack(side=RIGHT, padx=(0, 8))
        self.btn_pdf_import.pack(side=RIGHT, padx=(0, 8))
        self.btn_folder_change.pack(side=RIGHT)

        # ramka na tekst
        self.editor_frame = ttk.Labelframe(self.right_frame,
                                           text=self.t["frame_trans"],
                                           bootstyle="primary")
        self.editor_frame.pack(fill=BOTH, expand=True, padx=(5,0))

        # wiersz 1: nawigacja i wielkość fontu
        self.header_row1 = ttk.Frame(self.editor_frame)
        self.header_row1.pack(fill=X, padx=5, pady=2)

        self.file_info_var = tk.StringVar(value=self.t["file_info"])
        self.lbl_file_info = ttk.Label(self.header_row1, textvariable=self.file_info_var,
                  font=("Segoe UI", 10, "bold"), bootstyle="inverse-light")
        self.lbl_file_info.pack(side=LEFT, fill=X, expand=True)

        # wyszukiwanie w tekście transkrypcji
        search_frame = ttk.Frame(self.header_row1)
        search_frame.pack(side=LEFT, padx=20)

        self.search_var = tk.StringVar()
        self.search_entry = ttk.Entry(search_frame, textvariable=self.search_var,
                                      width=15, font=("Segoe UI", 9))
        self.search_entry.pack(side=LEFT, padx=2)
        self.search_entry.bind("<Return>", lambda e: self.perform_search())

        self.btn_search = ttk.Button(search_frame, text="→", command=self.perform_search,
                   bootstyle="outline-info", padding=0)
        self.btn_search.pack(side=LEFT)
        self.btn_cancelsearch = ttk.Button(search_frame, text="×", command=self.clear_search,
                   bootstyle="outline-info", padding=0)
        self.btn_cancelsearch.pack(side=LEFT)

        # font pola tekstowego z transkrypcją
        font_tools = ttk.Frame(self.header_row1)
        font_tools.pack(side=RIGHT)
        self.btn_smfont = ttk.Button(font_tools, text="A-", command=lambda: self.change_font_size(-1),
                   bootstyle="outline-secondary", width=3, padding=2)
        self.btn_smfont.pack(side=LEFT, padx=2)
        self.btn_bgfont = ttk.Button(font_tools, text="A+", command=lambda: self.change_font_size(1),
                   bootstyle="outline-secondary", width=3, padding=2)
        self.btn_bgfont.pack(side=LEFT, padx=2)

        # język interfejsu
        self.lang_sel = ttk.Combobox(font_tools, values=self.languages, width=5,
                                     state="readonly")
        self.lang_sel.set(self.current_lang)
        self.lang_sel.bind("<<ComboboxSelected>>", self.change_app_language)
        self.lang_sel.pack(side=LEFT, padx=5)

        # wiersz 2: narzędzia AI (NER/BOX)
        self.header_row2 = ttk.Frame(self.editor_frame)
        self.header_row2.pack(fill=X, padx=5, pady=2)

        # lewa strona wiersza 2: analiza treści, NER, BOX itp.
        ai_tools = ttk.Frame(self.header_row2)
        ai_tools.pack(side=LEFT)

        self.btn_ner = ttk.Button(ai_tools, text="NER", command=self.start_ner_analysis,
                                  bootstyle="success-outline", width=3, padding=2)
        self.btn_ner.pack(side=LEFT, padx=2)

        self.btn_box = ttk.Button(ai_tools, text="BOX", command=self.start_coordinates_analysis,
                                  bootstyle="success-outline", width=4, padding=2, state="disabled")
        self.btn_box.pack(side=LEFT, padx=2)

        self.btn_cls = ttk.Button(ai_tools, text="CLS", command=self.clear_all_annotations,
                                  bootstyle="success-outline", width=4, padding=2, state="disabled")
        self.btn_cls.pack(side=LEFT, padx=2)

        self.btn_leg = ttk.Button(ai_tools, text="LEG", command=self.show_legend,
                                  bootstyle="info-outline", width=4, padding=2)
        self.btn_leg.pack(side=LEFT, padx=2)

        self.btn_csv = ttk.Button(ai_tools, text="CSV", command=self.export_ner_to_csv,
                                  bootstyle="success-outline", width=4, padding=2)
        self.btn_csv.pack(side=LEFT, padx=2)

        self.btn_log = ttk.Button(ai_tools, text="LOG", command=self.show_usage_log,
                                  bootstyle="success-outline", width=4, padding=2)
        self.btn_log.pack(side=LEFT, padx=2)

        self.btn_fix_replace = ttk.Button(ai_tools, text="FIX2", command=self.start_fix_replace,
                                          bootstyle="warning-outline", width=4, padding=2)
        self.btn_fix_replace.pack(side=LEFT, padx=2)

        self.btn_verify = ttk.Button(ai_tools, text="FIX", command=self.start_verification,
                                     bootstyle="success-outline", width=4, padding=2)
        self.btn_verify.pack(side=LEFT, padx=2)

        ttk.Separator(self.editor_frame, orient=HORIZONTAL).pack(fill=X, padx=5, pady=2)

        # pole tekstowe z paskiem przewijania
        self.text_scroll = ttk.Scrollbar(self.editor_frame, orient=VERTICAL)
        self.text_area = tk.Text(self.editor_frame,
                                 font=(self.font_family, self.font_size),
                                 wrap=WORD, undo=True,
                                 bg="#333333", fg="white", insertbackground="white",
                                 yscrollcommand=self.text_scroll.set, bd=0, width=1)

        self.text_scroll.config(command=self.text_area.yview)
        self.text_scroll.pack(side=RIGHT, fill=Y)
        self.text_area.pack(side=LEFT, fill=BOTH, expand=True, padx=5, pady=5)

        # konfiguracja kolorów dla różnych rodzajów nazw własnych (NER)
        self.category_colors = {
            "PERS": "#f4d65f",  # Jasny żółty (Osoby)
            "LOC": "#C1FFC1",   # Jasny zielony (Miejsca)
            "ORG": "#D1EAFF"    # Jasny niebieski (Organizacje)
        }

        # konfiguracja kolorów tagów w edytorze na podstawie słownika
        for category, color in self.category_colors.items():
            self.text_area.tag_configure(category, background=color, foreground="black")

        # konfiguracja dla wyszukiwania w tekście transkrypcji
        self.text_area.tag_configure("search_highlight", background="#00ffff", foreground="black")

        # pasek narzędzi
        self.toolbar = ttk.Frame(self.right_frame, padding=(0, 10, 0, 5))
        self.toolbar.pack(fill=X, padx=(5,0))

        # przyciski
        self.btn_first = ttk.Button(self.toolbar,
                   text="|<",
                   command=self.first_file,
                   bootstyle="outline-secondary")
        self.btn_first.pack(side=LEFT, fill=X, expand=True, padx=2)

        self.btn_prev = ttk.Button(self.toolbar,
                   text="<<",
                   command=self.prev_file,
                   bootstyle="outline-secondary")
        self.btn_prev.pack(side=LEFT, fill=X, expand=True, padx=2)

        self.goto_frame = ttk.Frame(self.toolbar)
        self.goto_frame.pack(side=LEFT, padx=5)
        self.goto_label = ttk.Label(self.goto_frame, text=self.t["goto_label"])
        self.goto_label.pack(side=LEFT, padx=(0, 3))
        self.goto_var = tk.StringVar(master=self.root)
        self.goto_spin = ttk.Spinbox(
            self.goto_frame,
            from_=1,
            to=1,
            textvariable=self.goto_var,
            width=5,
        )
        self.goto_spin.pack(side=LEFT, padx=(0, 3))
        self.goto_spin.bind("<Return>", lambda _event: self.goto_file())
        self.btn_goto = ttk.Button(
            self.goto_frame,
            text=self.t["btn_goto"],
            command=self.goto_file,
            bootstyle="outline-secondary",
        )
        self.btn_goto.pack(side=LEFT)

        self.btn_save = ttk.Button(self.toolbar,
                   text=self.t["btn_save"],
                   command=self.save_current_text,
                   bootstyle="success")
        self.btn_save.pack(side=LEFT, fill=X, expand=True, padx=5)

        # Gemini
        frame_ai = ttk.Frame(self.toolbar)
        frame_ai.pack(side=LEFT, fill=X, expand=True)

        self.btn_ai = ttk.Button(frame_ai,
                                 text=self._ai_button_text(),
                                 command=self.start_ai_transcription,
                                 bootstyle="danger")
        self.btn_ai.pack(side=LEFT, fill=X, expand=True, padx=2)

        # Gemini seria
        self.btn_seria = ttk.Button(frame_ai,
                                    text=self.t["btn_batch"],
                                    command=self.open_batch_dialog,
                                    bootstyle="danger")
        self.btn_seria.pack(side=LEFT, fill=X, expand=True, padx=2)

        # zapis wyników
        self.btn_export = ttk.Menubutton(
            self.toolbar,
            text=self.t["btn_export"],
            bootstyle="info",
        )
        self.btn_export.pack(side=LEFT, fill=X, expand=True, padx=5)
        self._build_export_menu()

        self.btn_last = ttk.Button(self.toolbar,
                   text=">|",
                   command=self.last_file,
                   bootstyle="outline-secondary")
        self.btn_last.pack(side=RIGHT, fill=X, expand=True, padx=2)


        self.btn_next = ttk.Button(self.toolbar,
                   text=">>",
                   command=self.next_file,
                   bootstyle="outline-secondary")
        self.btn_next.pack(side=RIGHT, fill=X, expand=True, padx=2)


        # pasek stanu promptu
        self.prompt_status_frame = ttk.Frame(self.right_frame, bootstyle="light")
        self.prompt_status_frame.pack(fill=X, padx=(5,0), pady=(0, 5))

        # etykieta z nazwą bieżącego promptu (pliku z promptem)
        ttk.Label(self.prompt_status_frame, text="Prompt:",
                  font=("Segoe UI", 8, "bold")).pack(side=LEFT)

        ttk.Label(self.prompt_status_frame, textvariable=self.prompt_filename_var,
                  font=("Segoe UI", 8), bootstyle="dark").pack(side=LEFT, padx=(5,5), pady=2)

        # przycisk zmiany promptu
        self.btn_prompt_change = ttk.Button(self.prompt_status_frame, text=self.t["btn_prompt"], command=self.select_prompt_file,
                   bootstyle="link-secondary", cursor="hand2", padding=0)
        self.btn_prompt_change.pack(side=RIGHT, padx=5)

        # przycisk edycji promptu
        self.btn_edit_prompt = ttk.Button(self.prompt_status_frame, text=self.t["btn_edit_prompt"],
                                        command=self.edit_current_prompt,
                                        bootstyle="link-info", cursor="hand2", padding=0)
        self.btn_edit_prompt.pack(side=RIGHT, padx=5)

        # przycisk nowego promptu
        self.btn_new_prompt = ttk.Button(self.prompt_status_frame, text=self.t["btn_new_prompt"],
                                        command=self.create_new_prompt,
                                        bootstyle="link-success", cursor="hand2", padding=0)
        self.btn_new_prompt.pack(side=RIGHT, padx=5)

        # skróty klawiszowe
        self.root.bind("<Control-s>", lambda e: self.save_current_text())
        self.root.bind("<Alt-Left>", lambda e: self.prev_file())
        self.root.bind("<Alt-Right>", lambda e: self.next_file())
        self.root.bind("<Control-q>", lambda e: self.on_close())
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

        # pasek postępu (domyślnie ukryty)
        self.progress_bar = ttk.Progressbar(self.right_frame,
                                            mode='indeterminate',
                                            bootstyle="success-striped")

        # konfiguracja tagu aktywnej linii w edytorze transkrypcji
        self.text_area.tag_configure("active_line", background="#e8e8e8", foreground="black")

        # przesunięcie tagu aktywnej linii na sam dół hierarchii
        self.text_area.tag_lower("active_line")

        # powiązania zdarzeń aktualizujących podświetlenie linii
        self.text_area.bind("<KeyRelease>", self.update_active_line_highlight)
        self.text_area.bind("<ButtonRelease-1>", self.update_active_line_highlight)

        # tooltips
        self.btn_fit_tooltip = ToolTip(self.btn_fit, self.t["tt_btn_fit"])
        self.btn_ner_tooltip = ToolTip(self.btn_ner, self.t["tt_btn_ner"])
        self.btn_box_tooltip = ToolTip(self.btn_box, self.t["tt_btn_box"])
        self.btn_cls_tooltip = ToolTip(self.btn_cls, self.t["tt_btn_cls"])
        self.btn_leg_tooltip = ToolTip(self.btn_leg, self.t["tt_btn_leg"])
        self.btn_csv_tooltip = ToolTip(self.btn_csv, self.t["tt_btn_csv"])
        self.btn_log_tooltip = ToolTip(self.btn_log, self.t["tt_btn_log"])
        self.btn_verify_tooltip = ToolTip(self.btn_verify, self.t["tt_btn_verify"])
        self.btn_fix_replace_tooltip = ToolTip(self.btn_fix_replace, self.t["tt_btn_fix_replace"])
        self.btn_ai_tooltip = ToolTip(self.btn_ai, self.t["tt_btn_ai"])
        self.btn_seria_tooltip = ToolTip(self.btn_seria, self.t["tt_btn_seria"])
        self.btn_export_tooltip = ToolTip(self.btn_export, self.t["tt_btn_export"])
        self.btn_save_tooltip = ToolTip(self.btn_save, self.t["tt_btn_save"])
        self.btn_first_tooltip = ToolTip(self.btn_first, self.t["tt_btn_first"])
        self.btn_last_tooltip = ToolTip(self.btn_last, self.t["tt_btn_last"])
        self.btn_prev_tooltip = ToolTip(self.btn_prev, self.t["tt_btn_prev"])
        self.btn_next_tooltip = ToolTip(self.btn_next, self.t["tt_btn_next"])
        self.btn_goto_tooltip = ToolTip(self.btn_goto, self.t["tt_btn_goto"])
        self.btn_bgfont_tooltip = ToolTip(self.btn_bgfont, self.t["tt_btn_bgfont"])
        self.btn_smfont_tooltip = ToolTip(self.btn_smfont, self.t["tt_btn_smfont"])
        self.btn_search_tooltip = ToolTip(self.btn_search, self.t["tt_btn_search"])
        self.btn_cancelsearch_tooltip = ToolTip(self.btn_cancelsearch, self.t["tt_btn_cancelsearch"])
        self.btn_new_prompt_tooltip = ToolTip(self.btn_new_prompt, self.t["tt_btn_new_prompt"])
        self.btn_settings_tooltip = ToolTip(self.btn_settings, self.t["tt_btn_settings"])
        self.btn_pdf_import_tooltip = ToolTip(self.btn_pdf_import, self.t["tt_btn_pdf_import"])

        self.root.after(100, self.load_startup_folder)


    def _build_export_menu(self):
        self.export_menu = tk.Menu(self.btn_export, tearoff=0)
        self.export_menu.add_command(label=self.t["export_menu_txt"], command=self.export_all_data)
        self.export_menu.add_command(label=self.t["export_menu_md"], command=self.export_all_data_markdown)
        self.export_menu.add_command(label=self.t["export_menu_docx"], command=self.export_all_data_docx)
        self.export_menu.add_command(label=self.t["export_menu_html_table"], command=self.export_merged_html_table)
        self.export_menu.add_command(label=self.t["export_menu_html_tables_files"], command=self.export_html_tables_as_files)
        self.export_menu.add_command(label=self.t["export_menu_html_tables_xlsx"], command=self.export_html_tables_xlsx)
        self.export_menu.add_command(label=self.t["export_menu_tei"], command=self.export_to_tei_xml)
        self.btn_export.configure(menu=self.export_menu)


    def create_new_prompt(self):
        """ nowy plik promptu z szablonem """
        prompt_dir = ensure_prompt_dir()

        # wywołanie okna zapisu pliku
        new_filepath = filedialog.asksaveasfilename(
            title=self.t["select_new_prompt_title"],
            initialdir=prompt_dir,
            defaultextension=".txt",
            filetypes=[(self.t["file_type_text"], "*.txt")],
            parent=self.root
        )

        if not new_filepath:
            return

        try:
            # zapis szablonu do nowego pliku
            with open(new_filepath, 'w', encoding='utf-8') as f:
                f.write(DEFAULT_PROMPT_TEMPLATE)

            # załadowanie nowo utworzonego promptu jako aktywny
            if self.load_prompt_content(new_filepath):
                # otwarcie okna edycji, aby użytkownik mógł dostosować szablon
                self.edit_current_prompt()

        except Exception as e:
            messagebox.showerror(self.t["msg_error_title"], self.t["msg_prompt_create_error"] + f": {e}")


    def start_verification(self):
        """ uruchomienie procesu weryfikacji transkrypcji przez AI """
        if not self.file_pairs or self.is_transcribing:
            return

        # usuwanie dotychczasowych podświetleń
        self.clear_all_annotations()

        # aktualny tekst transkrypcji
        current_text = self.text_area.get(1.0, tk.END).strip()
        current_checksum = self._calculate_checksum(current_text)

        json_path = self._get_ner_json_path()

        # próba wczytania metadanych z json
        cache_checksum = ""
        if json_path and os.path.exists(json_path):
            try:
                with open(json_path, 'r', encoding='utf-8') as f:
                    cache_data = json.load(f)
                    cache_checksum = cache_data.get("checksum")
            except Exception as e:
                print(e)

        # jeżeli istnieje plik *.fix i suma kontrolna tekstu transkrypcji się nie zmieniła
        # wczytywanie różnic z dysku
        fix_path = os.path.splitext(self.file_pairs[self.current_index]['txt'])[0] + ".fix"
        if os.path.exists(fix_path) and current_checksum == cache_checksum:
            with open(fix_path, 'r', encoding='utf-8') as f:
                fixed_text = f.read()
                self._apply_diff(current_text, fixed_text)
            return

        if not self.ensure_non_ocr_ai_config():
            return

        # w innym przypadku- trzeba wywołać AI w celu anlizy
         # wyszarzenie przycisku FIX
        self.btn_verify.config(state="disabled", text="...")
        # progress bar
        self.progress_bar.pack(fill=X, pady=(0, 10), before=self.editor_frame)
        self.progress_bar.start(10)

        img_path = self.get_transcription_image_path(self.file_pairs[self.current_index])

        threading.Thread(target=self._verify_worker, args=(img_path, current_text), daemon=True).start()


    def _verify_worker(self, img_path, original_text):
        try:
            if self.llm_provider == "ollama":
                model, response = ollama_verify_transcription(
                    img_path,
                    original_text,
                    model_name=self.ollama_fix_model,
                    base_url=self.ollama_base_url,
                    timeout_seconds=self._ollama_timeout_seconds(),
                )
            else:
                model, response = verify_transcription(
                    self.api_key,
                    img_path,
                    original_text,
                    model_name=self.fix_model,
                    timeout_seconds=self.api_timeout_seconds,
                )

            if response.usage_metadata:
                self.root.after(0, lambda: self._log_model_usage(model, response.usage_metadata))

            if response.text:
                fixed_text = response.text.strip()
                # zapis do pliku tekstowego z rozszerzeniem *.fix
                fix_path = os.path.splitext(self.file_pairs[self.current_index]['txt'])[0] + ".fix"
                with open(fix_path, 'w', encoding='utf-8') as f:
                    f.write(fixed_text)

                self.root.after(0, lambda: self._apply_diff(original_text, fixed_text))

        except Exception as e:
            self.root.after(0, self._verify_finished)
            logger.exception("Błąd weryfikacji: %s", e)
        finally:
            self.root.after(0, self._verify_finished)


    def _verify_finished(self):
        """ aktualizacja GUI po zakończeniu pracy wątku obsługującego próbę poprawienia transkrypcji  """
        self.progress_bar.stop()
        self.progress_bar.pack_forget()
        self.btn_verify.config(state="normal", text="FIX")


    def start_fix_replace(self):
        """ uruchomienie korekty, która zastępuje bieżący tekst transkrypcji """
        if not self.file_pairs or self.is_transcribing:
            return

        current_text = self.text_area.get(1.0, tk.END).strip()
        if not current_text:
            messagebox.showwarning(self.t["msg_warning"], self.t["msg_fix_replace_empty"], parent=self.root)
            return

        if not self.ensure_non_ocr_ai_config():
            return

        if not messagebox.askyesno(
            self.t["msg_fix_replace_confirm_title"],
            self.t["msg_fix_replace_confirm_text"],
            parent=self.root,
        ):
            return

        self.clear_all_annotations()
        self.is_transcribing = True
        self.btn_fix_replace.config(state="disabled", text="...")
        self.text_area.config(state="disabled")
        self.progress_bar.pack(fill=X, pady=(0, 10), before=self.editor_frame)
        self.progress_bar.start(10)

        img_path = self.get_transcription_image_path(self.file_pairs[self.current_index])
        threading.Thread(target=self._fix_replace_worker, args=(img_path, current_text), daemon=True).start()


    def _fix_replace_worker(self, img_path, original_text):
        try:
            if self.llm_provider == "ollama":
                model, response = ollama_correct_transcription(
                    img_path,
                    original_text,
                    model_name=self.ollama_fix_model,
                    base_url=self.ollama_base_url,
                    timeout_seconds=self._ollama_timeout_seconds(),
                )
            else:
                model, response = correct_transcription(
                    self.api_key,
                    img_path,
                    original_text,
                    model_name=self.fix_model,
                    timeout_seconds=self.api_timeout_seconds,
                )

            if response.usage_metadata:
                self.root.after(0, lambda: self._log_model_usage(model, response.usage_metadata))

            corrected_text = (response.text or "").strip()
            if corrected_text:
                self.root.after(0, lambda: self._apply_fix_replace(corrected_text))
            else:
                self.root.after(
                    0,
                    lambda: messagebox.showerror(
                        self.t["msg_fix_replace_error_title"],
                        self.t["msg_fix_replace_empty_response"],
                        parent=self.root,
                    ),
                )
        except Exception as e:
            logger.exception("Błąd korekty FIX2: %s", e)
            self.root.after(
                0,
                lambda error=e: messagebox.showerror(
                    self.t["msg_fix_replace_error_title"],
                    str(error),
                    parent=self.root,
                ),
            )
        finally:
            self.root.after(0, self._fix_replace_finished)


    def _apply_fix_replace(self, corrected_text):
        self.text_area.config(state="normal")
        self.text_area.delete(1.0, tk.END)
        self.text_area.insert(tk.END, corrected_text)
        self.save_current_text(silent=True)
        messagebox.showinfo(
            self.t["msg_fix_replace_ok_title"],
            self.t["msg_fix_replace_ok_text"],
            parent=self.root,
        )


    def _fix_replace_finished(self):
        self.progress_bar.stop()
        self.progress_bar.pack_forget()
        self.text_area.config(state="normal")
        self.btn_fix_replace.config(state="normal", text="FIX2")
        self.is_transcribing = False


    def _apply_diff(self, old_text, new_text):
        """ podświetlenie różnic w edytorze na podstawie porównania tekstów """
        # konfiguracja tagu dla zmian
        self.text_area.tag_configure("diff_fix", background="#fc8686", foreground="black")

        messagebox.showinfo("Weryfikacja", "AI wygenerowało poprawki. Zmiany zostały zapisane w pliku .fix i podświetlone w edytorze.")

        for start_idx, end_idx in build_diff_ranges(old_text, new_text):
            self.text_area.tag_add("diff_fix", start_idx, end_idx)


    def _get_tk_index(self, text, offset):
        """ konwersja offsetu znaku na format 'linia.kolumna' dla Text widget """
        return tk_index_from_offset(text, offset)


    def export_to_tei_xml(self):
        """ eksportuje transkrypcje z bieżącego folderu do formatu
            TEI-XML z tagowaniem NER (jeżeli jest)
        """
        if not self.file_pairs:
            return

        target_path = filedialog.asksaveasfilename(
            title=self.t["filedialog_tei_title"],
            defaultextension=".xml",
            filetypes=[(self.t["filetype_xml"], "*.xml")],
            parent=self.root
        )
        if not target_path:
            return

        try:
            export_tei(self.file_pairs, target_path)
            messagebox.showinfo(self.t["msg_csv_ok_title"],
                                self.t["msg_xml_info_text"] + f":\n{os.path.basename(target_path)}")

        except Exception as e:
            messagebox.showerror(self.t["msg_xml_error_title"],
                                 self.t["msg_xml_error_text"] + f": {e}")


    def _prepare_text_for_tei(self, text):
        """ łączenie podzielonych słów i wierszy w logiczne akapity """
        return prepare_text_for_tei(text)


    def _tag_entities_tei(self, text, entities):
        """ otaczanie nazw własnych tagami TEI (persName, placeName, orgName) """
        return tag_entities_tei(text, entities)


    def change_app_language(self, event):
        """ zmiana języka interfejsu użytkownika """
        tmp = self.lang_sel.get()
        if tmp != self.current_lang:
            self.current_lang = tmp
            self.t = self.localization[self.current_lang]
            self.save_config()
            self.update_ui_text()
        self.lang_sel.selection_clear()


    def update_ui_text(self):
        """odświeżnie tekstów we wszystkich widżetach po zmianie języka"""

        self.root.title(self.t["title"])

        self.lbl_left_tools.config(text=self.t["left_tools"])

        self.canvas_frame.config(text=self.t["frame_scan"])
        self.editor_frame.config(text=self.t["frame_trans"])

        self.lbl_filters.config(text=self.t["lbl_filters"])
        self.btn_reset.config(text=self.t["filter_reset"])
        self.btn_contrast.config(text=self.t["filter_contrast"])
        self.btn_inverse.config(text=self.t["filter_invert"])
        self.btn_save.config(text=self.t["btn_save"])
        self.lbl_folder_status.config(text=self.t["folder_path"])
        self.btn_folder_change.config(text=self.t["btn_folder_change"])
        self.btn_settings.config(text=self.t["btn_settings"])
        self.btn_pdf_import.config(text=self.t["btn_pdf_import"])
        self.btn_seria.config(text=self.t["btn_batch"])
        self.btn_export.config(text=self.t["btn_export"])
        self._build_export_menu()
        self.goto_label.config(text=self.t["goto_label"])
        self.btn_goto.config(text=self.t["btn_goto"])
        self.btn_prompt_change.config(text=self.t["btn_prompt"])
        self.btn_edit_prompt.config(text=self.t["btn_edit_prompt"])
        self.btn_new_prompt.config(text=self.t["btn_new_prompt"])

        self.refresh_tooltips()


    def refresh_tooltips(self):
        """ odświeżanie podpowiedzi w aktualnym języku interfejsu """
        self.btn_fit_tooltip.update_text(self.t["tt_btn_fit"])
        self.btn_ner_tooltip.update_text(self.t["tt_btn_ner"])
        self.btn_box_tooltip.update_text(self.t["tt_btn_box"])
        self.btn_cls_tooltip.update_text(self.t["tt_btn_cls"])
        self.btn_leg_tooltip.update_text(self.t["tt_btn_leg"])
        self.btn_csv_tooltip.update_text(self.t["tt_btn_csv"])
        self.btn_ai_tooltip.update_text(self.t["tt_btn_ai"])
        self.btn_fix_replace_tooltip.update_text(self.t["tt_btn_fix_replace"])
        self.btn_seria_tooltip.update_text(self.t["tt_btn_seria"])
        self.btn_export_tooltip.update_text(self.t["tt_btn_export"])
        self.btn_save_tooltip.update_text(self.t["tt_btn_save"])
        self.btn_first_tooltip.update_text(self.t["tt_btn_first"])
        self.btn_last_tooltip.update_text(self.t["tt_btn_last"])
        self.btn_prev_tooltip.update_text(self.t["tt_btn_prev"])
        self.btn_next_tooltip.update_text(self.t["tt_btn_next"])
        self.btn_goto_tooltip.update_text(self.t["tt_btn_goto"])
        self.btn_bgfont_tooltip.update_text(self.t["tt_btn_bgfont"])
        self.btn_smfont_tooltip.update_text(self.t["tt_btn_smfont"])
        self.btn_search_tooltip.update_text(self.t["tt_btn_search"])
        self.btn_cancelsearch_tooltip.update_text(self.t["tt_btn_cancelsearch"])
        self.btn_new_prompt_tooltip.update_text(self.t["tt_btn_new_prompt"])
        self.btn_settings_tooltip.update_text(self.t["tt_btn_settings"])
        self.btn_pdf_import_tooltip.update_text(self.t["tt_btn_pdf_import"])


    def show_usage_log(self):
        """ wyświetlenie okna z historią zużycia tokenów i podsumowaniem kosztów"""
        if not self.file_pairs:
            return

        folder = os.path.dirname(self.file_pairs[0]['img'])
        log_path = os.path.join(folder, "tokens.log")

        if not os.path.exists(log_path):
            messagebox.showinfo("Log", self.t["msg_log_file"])
            return

        log_win = tk.Toplevel(self.root)
        log_win.title(self.t["log_win_title"])
        set_scaled_geometry(log_win, 900, 500, parent=self.root)

        # wykorzystanie elementu Tableview
        columns = [
            {"text": self.t["table_data"], "stretch": True},
            {"text": self.t["table_model"], "stretch": True},
            {"text": self.t["table_input"], "stretch": False},
            {"text": self.t["table_output"], "stretch": False},
            {"text": self.t["table_thinking"], "stretch": False},
            {"text": self.t["table_cost"], "stretch": False}
        ]

        row_data, total_cost = read_usage_log(folder)

        tv = Tableview(log_win, coldata=columns, rowdata=row_data, paginated=True,
                       searchable=True, bootstyle="info")
        tv.pack(fill=BOTH, expand=True, padx=10, pady=10)

        footer = ttk.Label(log_win, text=self.t["total_cost"] + f": ${total_cost:.4f}",
                           font=("Segoe UI", 10, "bold"))
        footer.pack(pady=10)


    def _log_api_usage(self, model_name, usage_metadata):
        """ obliczanie kosztu użycia API i zapis w logu w bieżącym folderze ze skanami"""
        if not self.file_pairs or not usage_metadata:
            return

        folder = os.path.dirname(self.file_pairs[0]['img'])

        try:
            append_usage_log(folder, self.MODEL_PRICES, model_name, usage_metadata)
        except Exception as e:
            print(self.t["msg_log_error"] + f": {e}")


    def export_ner_to_csv(self):
        """ eksport NER do CSV z mianownikiem i kontekstem z całego katalogu """
        if not self.file_pairs:
            return

        if not self.ensure_non_ocr_ai_config():
            return

        target_path = filedialog.asksaveasfilename(
            title=self.t["file_dialog_csv"],
            defaultextension=".csv",
            filetypes=[(self.t["file_type_csv"], "*.csv")],
            parent=self.root
        )
        if not target_path:
            return

        try:
            all_data_to_process = collect_ner_rows(self.file_pairs)
            names = unique_ner_names(all_data_to_process)
        except Exception as e:
            messagebox.showerror(self.t["msg_csv_error"], str(e))
            return

        if not all_data_to_process:
            messagebox.showinfo(self.t["msg_csv_info_title"], self.t["msg_csv_info_text"])
            return

        self.btn_ai.config(state="disabled")
        threading.Thread(target=self._ner_export_worker,
                         args=(names, all_data_to_process, target_path),
                         daemon=True).start()


    def _ner_export_worker(self, names_list, full_records, target_path):
        """ wątek AI: mianownik + zapis 5 kolumn do CSV """
        try:
            if self.llm_provider == "ollama":
                nominative_map, usage_entries = ollama_build_nominative_map(
                    names_list,
                    model_name=self.ollama_analysis_model,
                    base_url=self.ollama_base_url,
                    timeout_seconds=self._ollama_timeout_seconds(),
                )
            else:
                nominative_map, usage_entries = build_nominative_map(
                    self.api_key,
                    names_list,
                    model_name=self.analysis_model,
                    timeout_seconds=self.api_timeout_seconds,
                )
            for model, usage_metadata in usage_entries:
                self._log_model_usage(model, usage_metadata)

            write_ner_csv(
                target_path,
                full_records,
                nominative_map,
                [
                    self.t["csv_column_orgname"],
                    self.t["csv_column_nominative"],
                    self.t["csv_column_category"],
                    self.t["csv_column_file"],
                ],
            )

            self.root.after(0, lambda: messagebox.showinfo(self.t["msg_csv_ok_title"],
                                                           self.t["msg_csv_ok_text"] + f":\n{target_path}"))

        except Exception as e:
            self.root.after(0, lambda: messagebox.showerror(self.t["msg_csv_error_text"], str(e)))
        finally:
            self.root.after(0, lambda: self.btn_ai.config(state="normal", text=self._ai_button_text()))


    def update_active_line_highlight(self, event=None):
        """ podświetlanie linii, w której aktualnie znajduje się kursor """
        # usuwanie starego podświetlenia
        self.text_area.tag_remove("active_line", "1.0", tk.END)

        # pobieranie początku i końca bieżącej linii
        line_start = self.text_area.index("insert linestart")
        line_end = self.text_area.index("insert lineend + 1c")

        # nakładanie tagu
        self.text_area.tag_add("active_line", line_start, line_end)

        # active_line jest zawsze pod kategoriami NER
        for tag in ["PERS", "LOC", "ORG"]:
            self.text_area.tag_raise(tag, "active_line")


    def show_legend(self):
        """ wyświetlanie małego okna z opisem kolorów NER """
        leg_win = tk.Toplevel(self.root)
        leg_win.title(self.t["leg_win_title"])
        set_scaled_geometry(leg_win, 550, 240, parent=self.root)
        leg_win.resizable(False, False)
        leg_win.transient(self.root)

        leg_win.grab_set()

        # główny kontener z marginesem
        container = ttk.Frame(leg_win, padding=15)
        container.pack(fill=BOTH, expand=True)

        ttk.Label(container, text=self.t["label_ner_category"],
                  font=("Segoe UI", 10, "bold")).pack(pady=(0, 10))

        # definicje opisów dla kategorii
        descriptions = {
            "PERS": self.t["desc_ner_pers"],
            "LOC": self.t["desc_ner_loc"],
            "ORG": self.t["desc_ner_org"]
        }

        for cat, color in self.category_colors.items():
            row = ttk.Frame(container)
            row.pack(fill=X, pady=3)

            cv = tk.Canvas(row, width=18, height=18, highlightthickness=0, bd=0)
            cv.pack(side=LEFT, padx=(0, 10))
            cv.create_rectangle(0, 0, 18, 18, fill=color, outline="gray")

            # nazwa kategorii i opis
            ttk.Label(row, text=f"{cat}: ", font=("Segoe UI", 9, "bold")).pack(side=LEFT)
            ttk.Label(row, text=descriptions.get(cat, ""), font=("Segoe UI", 8)).pack(side=LEFT)

        # przycisk zamknięcia okna
        btn_leg_close = ttk.Button(container, text=self.t["btn_leg_close"], command=leg_win.destroy,
                   bootstyle="secondary-link")
        btn_leg_close.pack(side=BOTTOM, pady=(10, 0))


    def clear_all_annotations(self):
        """ usuwanie wszystkich ramek ze skanu i podświetlenia z tekstu """
        # czyszczenie skanu
        self.canvas.delete("ner_box")

        # czyszczenie podświetleń w tekście (NER + wyszukiwanie i różnice z funkcji fix)
        for tag in ["PERS", "LOC", "ORG", "search_highlight", "diff_fix"]:
            self.text_area.tag_remove(tag, "1.0", tk.END)

        # resetowanie stanu przycisków
        self.btn_cls.config(state="disabled")
        self.btn_box.config(state="disabled")


    def _calculate_checksum(self, text):
        """ suma kontrolna SHA-256 dla tekstu transkrypcji """
        return calculate_checksum(text)


    def _get_ner_json_path(self):
        """ ścieżka do pliku .json z metadanymi dla aktualnego skanu """
        if not self.file_pairs:
            return None
        txt_path = self.file_pairs[self.current_index]['txt']
        return get_ner_json_path(txt_path)


    def clear_search(self):
        """ czyści wyniki wyszukiwania """
        self.text_area.tag_remove("search_highlight", "1.0", tk.END)
        self.search_var.set("")


    def perform_search(self):
        """ wyszukuje i podświetla frazę w tekście transkrypcji """
        # czyszczenie poprzednich wyników wyszukiwania
        self.text_area.tag_remove("search_highlight", "1.0", tk.END)
        # czyszczenie kolorowania nazw własnych
        for tag in ["PERS", "LOC", "ORG"]:
            self.text_area.tag_remove(tag, "1.0", tk.END)

        query = self.search_var.get().strip()
        if not query:
            return

        start_pos = "1.0"
        count = 0
        while True:
            # szukanie frazy (nocase=True dla ignorowania wielkości liter)
            start_pos = self.text_area.search(query, start_pos, stopindex=tk.END,
                                              nocase=True)
            if not start_pos:
                break

            # obliczanie końca frazy
            end_pos = f"{start_pos}+{len(query)}c"
            self.text_area.tag_add("search_highlight", start_pos, end_pos)

            # przewijanie do pierwszego znalezionego wyniku
            if count == 0:
                self.text_area.see(start_pos)

            start_pos = end_pos
            count += 1

        if count == 0:
            # mignięcie ramką entry na czerwono przy braku wyników
            self.search_entry.config(bootstyle="danger")
            self.root.after(500, lambda: self.search_entry.config(bootstyle="default"))


    def _parse_coordinates_response(self, text):
        """ wyodrębnia nazwy i współrzędne [y1, x1, y2, x2] z odpowiedzi modelu """
        results = []
        # wyszukiwanie wzorca: nazwa, nazwa_kategorii [ymin, xmin, ymax, xmax]
        pattern = r"(.*?)\s*,(.*?)\s*\[(\d+),\s*(\d+),\s*(\d+),\s*(\d+)\]"
        matches = re.findall(pattern, text)

        for m in matches:
            results.append({
                'name': m[0].strip(),
                'category': m[1].strip(),
                'coords': [int(x) for x in m[2:]]
            })
        return results


    def _on_text_modified(self, event):
        """
        automatyczne usuwanie podświetlenia nazw własnych i ramek ze skanu
        przy edycji tekstu
        """
        for tag in ["PERS", "LOC", "ORG"]:
            self.text_area.tag_remove(tag, "1.0", tk.END)
        self.canvas.delete("ner_box") # usuwanie ramek z obrazu


    def start_ner_analysis(self):
        """ inicjacja procesu ekstrakcji nazw własnych przez AI lub
            wczytanie nazw własnych z pliku metadanych, w osobnym wątku
        """
        text = self.text_area.get(1.0, tk.END).strip()
        if not text or self.is_transcribing:
            return

        current_checksum = self._calculate_checksum(text)
        json_path = self._get_ner_json_path()

        # próba wczytania metadanych z json
        if json_path and os.path.exists(json_path):
            try:
                with open(json_path, 'r', encoding='utf-8') as f:
                    cache_data = json.load(f)

                # wczytywanie jeżeli suma kontrolna się zgadza
                if cache_data.get("checksum") == current_checksum:
                    self.last_entities = cache_data.get("entities", {})
                    self.text_area.tag_remove("PERS", "1.0", tk.END) # czyszczenie poprzednich
                    self.text_area.tag_remove("LOC", "1.0", tk.END)
                    self.text_area.tag_remove("ORG", "1.0", tk.END)
                    self._apply_ner_categories(self.last_entities)
                    self.btn_ner.config(state="normal")
                    self.btn_cls.config(state="normal")
                    return

            except Exception as e:
                print(self.t["msg_ner_metadata_error"] + f": {e}")

        if not self.ensure_non_ocr_ai_config():
            return

        # wywołanie AI jeżeli brak pliku json z metadanymi
        # wyszarzenie przycisku NER , włączenie paska pastępu
        self.btn_ner.config(state="disabled")
        self.progress_bar.pack(fill=X, pady=(0, 10), before=self.editor_frame)
        self.progress_bar.start(10)

        # suma kontrolna przekazywana do wątku, w celu zapisu w json po analizie AI
        thread = threading.Thread(target=self._ner_worker,
                                  args=(text, current_checksum), daemon=True)
        thread.start()


    def _ner_worker(self, text, checksum):
        """ dodatkowa analiza tekstu przez Gemini w celu uzyskania listy nazw własnych:
            osoby, miejsca, instytucje """
        try:
            logger.info("NER: generowanie wyników")
            if self.llm_provider == "ollama":
                model, response = ollama_extract_entities(
                    text,
                    model_name=self.ollama_analysis_model,
                    base_url=self.ollama_base_url,
                    timeout_seconds=self._ollama_timeout_seconds(),
                )
            else:
                model, response = extract_entities(
                    self.api_key,
                    text,
                    model_name=self.analysis_model,
                    timeout_seconds=self.api_timeout_seconds,
                )

            if response.usage_metadata:
                self._log_model_usage(model, response.usage_metadata)

            if response.text:
                logger.info("NER: wyniki przygotowane")
                json_str = response.text.replace("```json", "").replace("```", "").strip()
                entities_dict = json.loads(json_str)
                self.last_entities = entities_dict

                # zapis metdanych NER do pliku *.json z usunięciem ewentualnych współrzędnych ramek
                # nowe nazwy własne oznaczają konjieczność wyszukania nowych ramek na skanie
                self._save_ner_cache(entities=entities_dict, coordinates=[], checksum=checksum)

                self.root.after(0, self._apply_ner_categories, entities_dict)
        except Exception as e:
            print(self.t["msg_ner_error"] + f": {e}")
            self.root.after(0, self._ner_finished)
        finally:
            self.root.after(0, self._ner_finished)


    def _ner_finished(self):
        """ aktualizacja GUI po zakończeniu pracy wątku """
        self.progress_bar.stop()
        self.progress_bar.pack_forget()
        self.btn_ner.config(state="normal")


    def _save_ner_cache(self, entities=None, coordinates=None, checksum=None):
        """ zapis wyników NER, współrzędnych i sum kontrolnych do pliku .json """
        try:
            txt_path = self.file_pairs[self.current_index]['txt'] if self.file_pairs else None
            save_cache(
                txt_path,
                entities=entities,
                coordinates=coordinates,
                checksum=checksum,
            )
        except Exception as e:
            print(self.t["msg_ner_json_error"] + f": {e}")


    def _apply_ner_categories(self, entities_dict):
        """ podświetlenie nazw własnych z ulepszoną obsługą długich fraz i znaków interpunkcyjnych """
        # czyszczenie poprzednich tagów
        for tag in ["PERS", "LOC", "ORG"]:
            self.text_area.tag_remove(tag, "1.0", tk.END)

        #czyszczenie ewentualnego podświetlenia wyników wyszukiwania
        self.text_area.tag_remove("search_highlight", "1.0", tk.END)

        for category, names in entities_dict.items():
            if not names:
                continue

            # sortowanie nazwy od najdłuższych, aby uniknąć błędów przy zagnieżdżonych nazwach
            sorted_names = sorted(names, key=len, reverse=True)

            for name in sorted_names:
                name = name.strip()
                if len(name) < 2:
                    continue

                # budowa wzorca regex
                parts = []
                words = name.split()

                for i, word in enumerate(words):
                    word_pattern = ""
                    for j, char in enumerate(word):
                        word_pattern += re.escape(char)
                        if j < len(word) - 1:
                            word_pattern += r"(?:-?\s*\n?\s*)?"
                    parts.append(word_pattern)

                search_pattern = r"\s+".join(parts)
                search_pattern = search_pattern.replace(r"\s+", r"(?:-?\s*[\s\n]+\s*)")

                start_pos = "1.0"
                while True:
                    match_count = tk.IntVar()
                    start_pos = self.text_area.search(search_pattern, start_pos,
                                                    stopindex=tk.END, nocase=True,
                                                    regexp=True, count=match_count)
                    if not start_pos:
                        break

                    # obliczanie końca
                    end_pos = self.text_area.index(f"{start_pos}+{match_count.get()}c")

                    # nakładanie koloru
                    self._apply_tag_excluding_newlines(category, start_pos, end_pos)
                    #self.text_area.tag_add(category, start_pos, end_pos)
                    start_pos = end_pos

        # widoczność przycisków
        if any(entities_dict.values()):
            self.btn_box.config(state="normal")
            self.btn_cls.config(state="normal")


    def _apply_tag_excluding_newlines(self, tag_name, start, end):
        """ funkcja nakładająca tag tylko na tekst, omijając znaki nowej linii """
        curr = start
        while self.text_area.compare(curr, "<", end):
            # koniec bieżącej linii
            line_end = self.text_area.index(f"{curr} lineend")

            # ograniczenie, jeśli koniec linii jest dalej niż koniec dopasowania
            if self.text_area.compare(line_end, ">", end):
                line_end = end

            # nakładanie tagu na fragment bieżącej linii
            if self.text_area.compare(curr, "<", line_end):
                self.text_area.tag_add(tag_name, curr, line_end)

            # przejście do początku następnej linii
            curr = self.text_area.index(f"{line_end} + 1c")
            if not curr:
                break


    def start_coordinates_analysis(self):
        """ uruchamianie rysowania lokalizacji nazw na obrazie """
        if not self.last_entities or not self.original_image:
            return

        text = self.text_area.get(1.0, tk.END).strip()
        current_checksum = self._calculate_checksum(text)
        json_path = self._get_ner_json_path()

        # odczytywanie metadanych z pliku json
        if json_path and os.path.exists(json_path):
            try:
                with open(json_path, 'r', encoding='utf-8') as f:
                    cache_data = json.load(f)

                # jeśli suma kontrolna się zgadza i istnieją metadane
                if cache_data.get("checksum") == current_checksum and "coordinates" in cache_data:
                    self._draw_boxes_only(cache_data["coordinates"])
                    return

            except Exception as e:
                print(e)

        if not self.ensure_non_ocr_ai_config():
            return

        # brak metadanych - wywołanie AI
        # wyszarzenie przycisku BOX
        self.btn_box.config(state="disabled", text="..." )
        # progress bar
        self.progress_bar.pack(fill=X, pady=(0, 10), before=self.editor_frame)
        self.progress_bar.start(10)

        threading.Thread(target=self._box_worker, args=(current_checksum,), daemon=True).start()


    def _box_worker(self, checksum):
        try:
            current_pair = self.file_pairs[self.current_index]

            entities_to_find = []
            for cat, names in self.last_entities.items():
                for name in names:
                    entities_to_find.append((name, cat))

            if self.llm_provider == "ollama":
                model, response = ollama_locate_entities(
                    self.get_transcription_image_path(current_pair),
                    entities_to_find,
                    model_name=self.ollama_box_model,
                    base_url=self.ollama_base_url,
                    timeout_seconds=self._ollama_timeout_seconds(),
                )
            else:
                model, response = locate_entities(
                    self.api_key,
                    self.get_transcription_image_path(current_pair),
                    entities_to_find,
                    model_name=self.box_model,
                    timeout_seconds=self.api_timeout_seconds,
                )

            if response.usage_metadata:
                self._log_model_usage(model, response.usage_metadata)

            if response.text:
                coordinates_data = self._parse_coordinates_response(response.text)

                # zapis współrzędnych ramek do pliku JSON z metadanymi
                self._save_ner_cache(entities=None, coordinates=coordinates_data, checksum=checksum)

                self.root.after(0, self._draw_boxes_only, coordinates_data)

        except Exception as e:
            print(self.t["msg_box_error"] + f": {e}")
            self.root.after(0, self._box_finished)
        finally:
            self.root.after(0, self._box_finished)


    def _box_finished(self):
        """ aktualizacja GUI po zakończeniu pracy wątku obsługującego wyszukiwanie nazw na skanie  """
        self.progress_bar.stop()
        self.progress_bar.pack_forget()
        self.btn_box.config(state="normal", text="BOX")


    def _draw_boxes_only(self, entities_data):
        """ rysowanie ramki na canvasie """
        self.canvas_controller.draw_boxes_only(entities_data)


    def _on_box_hover(self, event, entity_tag):
        """ weryfikacja czy kursor jest nad uchwytem lub ramką """
        return self.canvas_controller.on_box_hover(event, entity_tag)


    def _on_box_resize_start(self, event, entity_tag):
        """ inicjacja zmiany rozmiaru i blokada ruchu obrazu """
        self.resizing_box_tag = entity_tag
        self.last_mouse_x = event.x
        self.last_mouse_y = event.y
        # kursor zmiany rozmiaru
        self.canvas.config(cursor=self.cursor_resizing)
        return "break" # blokowanie przesuwanie skanu


    def _on_box_drag(self, event):
        """ wykonuje przesuwanie lub zmianę rozmiaru zależnie od box_action """
        return self.canvas_controller.on_box_drag(event)


    def _on_box_release(self, event):
        """ finalizacja operacji i zapis do JSON """
        return self.canvas_controller.on_box_release(event)


    def _on_box_delete(self, event, entity_tag):
        """ usuwanie ramki z obrazu i aktualizacja pliku JSON """
        return self.canvas_controller.on_box_delete(event, entity_tag)


    def _on_box_press(self, event, entity_tag):
        """ rozpoznaje czy użytkownik chce przesuwać, czy zmieniać rozmiar """
        return self.canvas_controller.on_box_press(event, entity_tag)


    def apply_filter(self, mode):
        """ zastosuj filtr dla bieżącego skanu """
        self.canvas_controller.apply_filter(mode)


    def load_lang(self):
        """ wczytywanie wersji językowych z pliku JSON """
        try:
            self.localization, self.languages = load_localization(self.local_file)
        except Exception as e:
            print(f"Blad wczytywania pliku jezykowego: {e}")


    def load_config(self):
        """ wczytywanie ustawień z pliku JSON """
        try:
            config = load_app_config(self.config_file)
            self.font_size = config.font_size
            self.current_lang = config.current_lang
            self.default_prompt = config.default_prompt
            self.last_folder = config.last_folder
            self.llm_provider = config.llm_provider
            self.htr_model = config.htr_model
            self.fix_model = config.fix_model
            self.analysis_model = config.analysis_model
            self.box_model = config.box_model
            self.ollama_base_url = config.ollama_base_url
            self.ollama_htr_model = config.ollama_htr_model
            self.ollama_fix_model = config.ollama_fix_model
            self.ollama_analysis_model = config.ollama_analysis_model
            self.ollama_box_model = config.ollama_box_model
            self.ollama_remove_table_headers = config.ollama_remove_table_headers
            self.ollama_pretty_html = config.ollama_pretty_html
            self.mistral_ocr_model = config.mistral_ocr_model
            self.mistral_include_blocks = config.mistral_include_blocks
            self.mistral_table_format = config.mistral_table_format
            self.datalab_output_format = config.datalab_output_format
            self.datalab_mode = config.datalab_mode
            self.api_timeout_seconds = config.api_timeout_seconds
            self.stream_transcription = config.stream_transcription
            self.batch_parallel_workers = config.batch_parallel_workers
            if not self.api_key:
                self.api_key = config.api_key
        except Exception as e:
            print(f"Blad wczytywania pliku konfiguracyjnego: {e}")


    def save_config(self):
        """ zapisywanie ustawienia do pliku JSON """
        try:
            existing_config = load_app_config(self.config_file)
            save_app_config(
                AppConfig(
                    font_size=self.font_size,
                    current_lang=self.current_lang,
                    default_prompt=self.default_prompt,
                    last_folder=getattr(self, "last_folder", existing_config.last_folder),
                    llm_provider=getattr(self, "llm_provider", existing_config.llm_provider),
                    api_key=existing_config.api_key,
                    htr_model=getattr(self, "htr_model", existing_config.htr_model),
                    fix_model=getattr(self, "fix_model", existing_config.fix_model),
                    analysis_model=getattr(self, "analysis_model", existing_config.analysis_model),
                    box_model=getattr(self, "box_model", existing_config.box_model),
                    ollama_base_url=getattr(self, "ollama_base_url", existing_config.ollama_base_url),
                    ollama_htr_model=getattr(self, "ollama_htr_model", existing_config.ollama_htr_model),
                    ollama_fix_model=getattr(self, "ollama_fix_model", existing_config.ollama_fix_model),
                    ollama_analysis_model=getattr(
                        self,
                        "ollama_analysis_model",
                        existing_config.ollama_analysis_model,
                    ),
                    ollama_box_model=getattr(self, "ollama_box_model", existing_config.ollama_box_model),
                    ollama_remove_table_headers=getattr(
                        self,
                        "ollama_remove_table_headers",
                        existing_config.ollama_remove_table_headers,
                    ),
                    ollama_pretty_html=getattr(
                        self,
                        "ollama_pretty_html",
                        existing_config.ollama_pretty_html,
                    ),
                    mistral_ocr_model=getattr(self, "mistral_ocr_model", existing_config.mistral_ocr_model),
                    mistral_include_blocks=getattr(
                        self,
                        "mistral_include_blocks",
                        existing_config.mistral_include_blocks,
                    ),
                    mistral_table_format=getattr(
                        self,
                        "mistral_table_format",
                        existing_config.mistral_table_format,
                    ),
                    datalab_output_format=getattr(
                        self,
                        "datalab_output_format",
                        existing_config.datalab_output_format,
                    ),
                    datalab_mode=getattr(self, "datalab_mode", existing_config.datalab_mode),
                    api_timeout_seconds=getattr(
                        self,
                        "api_timeout_seconds",
                        existing_config.api_timeout_seconds,
                    ),
                    stream_transcription=getattr(
                        self,
                        "stream_transcription",
                        existing_config.stream_transcription,
                    ),
                    batch_parallel_workers=getattr(
                        self,
                        "batch_parallel_workers",
                        existing_config.batch_parallel_workers,
                    ),
                ),
                self.config_file,
            )
        except Exception as e:
            print(self.t["msg_save_config_file_error"] + f": {e}")


    def change_font_size(self, delta):
        """ zmiana rozmiar fontu edytora i zapis do pliku z configiem """
        new_size = self.font_size + delta
        if new_size < 6:
            new_size = 6
        if new_size > 72:
            new_size = 72

        self.font_size = new_size
        self.text_area.configure(font=(self.font_family, self.font_size))

        self.save_config()


    def on_text_zoom(self, event):
        """ zmiana rozmaru fontu"""
        delta = 0
        if event.num == 5 or event.delta < 0:
            delta = -1
        elif event.num == 4 or event.delta > 0:
            delta = 1

        self.change_font_size(delta)
        return "break"


    def _init_environment(self):
        """ ładowanie zmiennych środowiskowych i promptu """
        if self.default_prompt:
            self.prompt_filename_var.set(self.default_prompt)
        else:
            self.prompt_filename_var.set("")

        if self.default_prompt:
            prompt_path = prompt_file(self.default_prompt)
            if not os.path.exists(prompt_path):
                messagebox.showerror(self.t["msg_prompt_file_missing"], self.default_prompt, parent=self.root)
                return

            try:
                self.current_prompt_path, self.prompt_text = read_default_prompt(self.default_prompt)
                self.prompt_filename_var.set(f"{self.default_prompt}")
            except Exception as e:
                messagebox.showerror(self.t["msg_error_title"],
                                     self.t["msg_file_prompt_error"] + f" {self.default_prompt}: {e}",
                                     parent=self.root)


    def _display_folder_path(self, folder_path):
        if len(folder_path) > 40:
            return "..." + folder_path[-37:]
        return folder_path


    def load_startup_folder(self):
        """Wczytuje ostatni katalog albo otwiera dialog wyboru przy pierwszym starcie."""
        if self.last_folder and os.path.isdir(self.last_folder):
            self.current_folder_path = self.last_folder
            self.current_folder_var.set(self._display_folder_path(self.last_folder))
            self.load_file_list(self.last_folder)
            return

        self.select_folder()


    def select_folder(self):
        """ wybór folderu """
        if self.is_transcribing:
            return

        initial_dir = self.last_folder if self.last_folder and os.path.isdir(self.last_folder) else os.getcwd()

        folder_path = filedialog.askdirectory(
            title=self.t["select_folder_title"],
            initialdir=initial_dir,
            parent=self.root
        )

        if folder_path:
            self.current_folder_path = folder_path
            self.last_folder = folder_path
            self.current_folder_var.set(self._display_folder_path(folder_path))
            self.save_config()

            # załadowanie plików
            self.load_file_list(folder_path)


    def load_file_list(self, folder):
        """ ładowanie listy plików skanów ze wskazanego folderu"""
        try:
            self.current_folder_path = folder
            self.last_folder = folder
            self.current_folder_var.set(self._display_folder_path(folder))
            all_files = os.listdir(folder)
            images = [
                f for f in all_files
                if f.lower().endswith(('.jpg', '.jpeg', '.png'))
                and not is_generated_filter_image(f)
            ]
            pdf_files = [f for f in all_files if f.lower().endswith(".pdf")]
            images.sort()
            pdf_files.sort()

            if not images and pdf_files:
                if messagebox.askyesno(
                    self.t["msg_pdf_import_title"],
                    self.t["msg_pdf_import_question"],
                    parent=self.root,
                ):
                    created_images = self._import_pdf_files(folder, pdf_files)
                    if created_images:
                        messagebox.showinfo(
                            self.t["msg_pdf_import_title"],
                            self.t["msg_pdf_import_success"] + f": {len(created_images)}",
                            parent=self.root,
                        )
                        all_files = os.listdir(folder)
                        images = [
                            f for f in all_files
                            if f.lower().endswith(('.jpg', '.jpeg', '.png'))
                            and not is_generated_filter_image(f)
                        ]
                        images.sort()

            self.file_pairs = []
            for img in images:
                base = os.path.splitext(img)[0]
                self.file_pairs.append({
                    'img': os.path.join(folder, img),
                    'txt': os.path.join(folder, base + ".txt"),
                    'name': base
                })

            if not self.file_pairs:
                messagebox.showinfo("Info", self.t["scan_files_missing"], parent=self.root)
                self.original_image = None
                self.processed_image = None
                self.canvas.delete("all")
                self.text_area.delete(1.0, tk.END)
                self.file_info_var.set(self.t["scan_folder_empty"])
                self._update_goto_control()
                return

            self.current_index = 0
            self._update_goto_control()
            self.load_pair(0)
            self.root.after(100, self.fit_to_width)
        except Exception as e:
            messagebox.showerror(self.t["msg_error_title"],
                                 self.t["msg_folder_scan_error"] + f": {e}", parent=self.root)


    def _import_pdf_files(self, folder, pdf_files):
        if not can_extract_pdf_pages():
            messagebox.showerror(
                self.t["msg_error_title"],
                self.t["msg_pdf_import_unavailable"],
                parent=self.root,
            )
            return []

        progress_win = tk.Toplevel(self.root)
        progress_win.title(self.t["msg_pdf_import_title"])
        set_scaled_geometry(progress_win, 520, 140, parent=self.root)
        progress_win.resizable(False, False)
        progress_win.transient(self.root)
        progress_win.grab_set()

        frame = ttk.Frame(progress_win, padding=15)
        frame.pack(fill=BOTH, expand=True)

        status_var = tk.StringVar(value=self.t["msg_pdf_import_progress_prepare"])
        ttk.Label(frame, textvariable=status_var, font=("Segoe UI", 10)).pack(anchor="w", pady=(0, 10))

        progress_var = tk.DoubleVar(value=0)
        progress_bar = ttk.Progressbar(
            frame,
            mode="determinate",
            maximum=100,
            variable=progress_var,
            bootstyle="info-striped",
        )
        progress_bar.pack(fill=X, pady=(0, 8))

        details_var = tk.StringVar(value="")
        ttk.Label(frame, textvariable=details_var, bootstyle="secondary").pack(anchor="w")

        def update_pdf_progress(processed, total, pdf_name, page_number, pdf_total_pages):
            if total:
                progress_var.set((processed / total) * 100)
            status_var.set(self.t["msg_pdf_import_progress"])
            details_var.set(
                self.t["msg_pdf_import_progress_details"].format(
                    pdf=pdf_name,
                    page=page_number,
                    pdf_pages=pdf_total_pages,
                    processed=processed,
                    total=total,
                )
            )
            progress_win.update_idletasks()

        self.root.update_idletasks()
        try:
            return extract_pdf_pages(
                folder,
                pdf_files,
                progress_callback=update_pdf_progress,
            )
        finally:
            if progress_win.winfo_exists():
                progress_win.destroy()


    def import_pdf_to_current_folder(self):
        if self.is_transcribing:
            return

        if not self.current_folder_path:
            messagebox.showwarning(
                self.t["msg_warning"],
                self.t["msg_pdf_import_choose_folder"],
                parent=self.root,
            )
            return

        pdf_path = filedialog.askopenfilename(
            title=self.t["select_pdf_import_title"],
            initialdir=self.current_folder_path,
            filetypes=[(self.t["file_type_pdf"], "*.pdf")],
            parent=self.root,
        )
        if not pdf_path:
            return

        if os.path.dirname(pdf_path) != self.current_folder_path:
            from shutil import copy2

            copied_pdf_name = os.path.basename(pdf_path)
            copied_pdf_path = os.path.join(self.current_folder_path, copied_pdf_name)
            if not os.path.exists(copied_pdf_path):
                copy2(pdf_path, copied_pdf_path)
            pdf_name = copied_pdf_name
        else:
            pdf_name = os.path.basename(pdf_path)

        created_images = self._import_pdf_files(self.current_folder_path, [pdf_name])
        if not created_images:
            return

        messagebox.showinfo(
            self.t["msg_pdf_import_title"],
            self.t["msg_pdf_import_success"] + f": {len(created_images)}",
            parent=self.root,
        )
        self.load_file_list(self.current_folder_path)


    def load_pair(self, index):
        """ ładowanie par plików: skan i transkrypcja """
        if not self.file_pairs:
            return
        pair = self.file_pairs[index]
        self._update_goto_control()

        # reset
        self.last_entities = []
        self.btn_box.config(state="disabled")
        self.btn_cls.config(state="disabled")
        self.canvas.delete("ner_box")

        # aktualizacja nagłówka
        self.file_info_var.set(f"[{index + 1}/{len(self.file_pairs)}] {pair['name']}")

        # skan
        try:
            self.original_image = Image.open(pair['img'])
            self.processed_image = self.original_image.copy()
            self.active_filter = "normal"

            # obsługa dopasowania obrazu do szerokości canvas
            canvas_w = self.canvas.winfo_width()

            # jeśli szerokość canvasu jest jednak jeszcze nieznana
            if canvas_w <= 1:
                # fallback: np. 60% szerokości okna
                canvas_w = self.root.winfo_width() * 0.6

            # obliczanie skali tak, by obraz zajął całą szerokość (z małym marginesem 10px)
            self.scale = (canvas_w - 10) / self.original_image.width

            # ograniczenie, by skan nie był zbyt wielki przy małych plikach
            if self.scale > 2.0:
                self.scale = 2.0

            self.img_x, self.img_y = 0, 0
            self.redraw_image()
        except Exception as e:
            print(e)

        # tekst
        self.text_area.delete(1.0, tk.END)
        if os.path.exists(pair['txt']):
            try:
                with open(pair['txt'], 'r', encoding='utf-8') as f:
                    self.text_area.insert(tk.END, f.read())
            except Exception as e:
                print(e)

        self.text_area.focus_set()
        self.text_area.mark_set("insert", "1.0")
        self.text_area.see("1.0")
        self.root.after(10, self.update_active_line_highlight)


    def redraw_image(self):
        """ odrysowywanie obrazu """
        self.canvas_controller.redraw_image()


    def fit_to_width(self):
        """ wymusza dopasowanie obrazu do aktualnej szerokości panelu """
        self.canvas_controller.fit_to_width()


    def load_prompt_content(self, filepath):
        """ wczytuje treść promptu z pliku """
        try:
            self.prompt_text = read_prompt(filepath)
            filename = os.path.basename(filepath)
            self.prompt_filename_var.set(f"{filename}")
            self.current_prompt_path = filepath
            self.default_prompt = filename
            self.save_config()

            return True
        except Exception as e:
            messagebox.showerror(self.t["msg_load_prompt_error_title"],
                                 self.t["msg_load_prompt_error_text"] + f":\n{e}", parent=self.root)
            return False


    def select_prompt_file(self):
        """ okno dialogowe wyboru pliku promptu """
        prompt_path = prompts_dir()
        filename = filedialog.askopenfilename(
            title=self.t["select_prompt_title"],
            initialdir=prompt_path,
            filetypes=[(self.t["file_type_text"], "*.txt"), (self.t["file_type_all"], "*.*")],
            parent=self.root
        )

        self.root.focus_set()
        self.root.update_idletasks()

        if filename:
            self.load_prompt_content(filename)


    def on_mouse_down(self, event):
        """ obsługa myszy - naciśnięcie klawisza """
        self.canvas_controller.on_mouse_down(event)


    def on_mouse_drag(self, event):
        """ obsługa myszy - przesuwanie """
        self.canvas_controller.on_mouse_drag(event)


    def on_mouse_wheel(self, event):
        """ obsługa myszy - skrolowanie kółkiem - zoom """
        self.canvas_controller.on_mouse_wheel(event)


    def save_current_text(self, silent=False):
        """
        zapis bieżącej zawartości pola tekstowego w pliku,
        parametr 'silent=True' wyłącza 'mruganie' etykietą (przy przełączaniu stron).
        """
        if not self.file_pairs:
            return
        pair = self.file_pairs[self.current_index]
        content = self.text_area.get(1.0, tk.END).strip()
        if content:
            content += "\n"

        try:
            with open(pair['txt'], 'w', encoding='utf-8') as f:
                f.write(content)

            # komunikat tylko jeśli nie jest to tryb silent
            if not silent:
                original_text = self.file_info_var.get()
                if self.t["text_saved"] not in original_text:
                    self.file_info_var.set(original_text + " " + self.t["text_saved"])
                    self.root.after(1000, lambda: self.refresh_label_safely(self.current_index))

        except Exception as e:
            messagebox.showerror(self.t["text_save_error"], str(e), parent=self.root)


    def refresh_label_safely(self, expected_index):
        """ pomocnicza funkcja przywracająca czystą nazwę pliku po zniknięciu komunikatu """
        if self.current_index == expected_index and self.file_pairs:
            pair = self.file_pairs[self.current_index]
            self.file_info_var.set(f"[{self.current_index + 1}/{len(self.file_pairs)}] {pair['name']}")


    def _update_goto_control(self):
        total = len(self.file_pairs)
        if not hasattr(self, "goto_spin"):
            return

        max_value = total if total else 1
        self.goto_spin.config(to=max_value)
        if total:
            self.goto_var.set(str(self.current_index + 1))
            self.goto_spin.config(state="normal")
            self.btn_goto.config(state="normal")
        else:
            self.goto_var.set("")
            self.goto_spin.config(state="disabled")
            self.btn_goto.config(state="disabled")


    def goto_file(self):
        """Przejście do obrazu o numerze podanym przez użytkownika."""
        if self.is_transcribing:
            return

        if not self.file_pairs:
            return

        try:
            target_number = int(self.goto_var.get())
        except (TypeError, ValueError):
            messagebox.showerror(
                self.t["msg_error_title"],
                self.t["msg_goto_invalid"] + f" 1-{len(self.file_pairs)}.",
                parent=self.root,
            )
            self._update_goto_control()
            return

        if target_number < 1 or target_number > len(self.file_pairs):
            messagebox.showerror(
                self.t["msg_error_title"],
                self.t["msg_goto_invalid"] + f" 1-{len(self.file_pairs)}.",
                parent=self.root,
            )
            self._update_goto_control()
            return

        target_index = target_number - 1
        if target_index == self.current_index:
            return

        self.save_current_text(silent=True)
        self.current_index = target_index
        self.load_pair(self.current_index)


    def first_file(self):
        """ przejście do pierwszego pliku """
        if self.is_transcribing:
            return

        self.save_current_text(silent=True)
        if self.current_index != 0:
            self.current_index = 0

            self.load_pair(self.current_index)


    def next_file(self):
        """ przejście do następnego pliku """
        if self.is_transcribing:
            return

        self.save_current_text(silent=True)
        if self.current_index < len(self.file_pairs) - 1:
            self.current_index += 1

            self.load_pair(self.current_index)


    def prev_file(self):
        """ przejście do poprzedniego pliku """
        if self.is_transcribing:
            return

        self.save_current_text(silent=True)
        if self.current_index > 0:
            self.current_index -= 1

            self.load_pair(self.current_index)


    def last_file(self):
        """ przejście do ostatniego pliku """
        if self.is_transcribing:
            return

        self.save_current_text(silent=True)
        if self.current_index < len(self.file_pairs) - 1:
            self.current_index = len(self.file_pairs) - 1

            self.load_pair(self.current_index)


    def export_all_data(self):
        """ eksport wszystkich transkrypcji do jednego pliku txt """
        self.save_current_text(silent=True)

        if not self.file_pairs:
            messagebox.showwarning(self.t["msg_export_txt_missing_title"],
                                   self.t["msg_export_txt_missing_text"], parent=self.root)
            return

        target_path = filedialog.asksaveasfilename(
            title=self.t["file_dialog_export_txt_title"],
            defaultextension=".txt",
            filetypes=[(self.t["msg_export_txt_text"], "*.txt")],
            parent=self.root
        )

        if not target_path:
            return

        try:
            export_txt(self.file_pairs, target_path)
            messagebox.showinfo(self.t["msg_csv_ok_title"],
                                self.t["msg_export_txt_text"] + f":\n{os.path.basename(target_path)}",
                                parent=self.root)

        except Exception as e:
            messagebox.showerror(self.t["msg_export_error_title"],
                                 self.t["msg_export_error_text"] + f":\n{e}", parent=self.root)


    def export_all_data_markdown(self):
        """ eksport wszystkich transkrypcji do jednego pliku markdown z separatorami stron """
        self.save_current_text(silent=True)

        if not self.file_pairs:
            messagebox.showwarning(self.t["msg_export_txt_missing_title"],
                                   self.t["msg_export_txt_missing_text"], parent=self.root)
            return

        target_path = filedialog.asksaveasfilename(
            title=self.t["file_dialog_export_md_title"],
            defaultextension=".md",
            filetypes=[(self.t["file_type_markdown"], "*.md")],
            parent=self.root,
        )

        if not target_path:
            return

        try:
            export_markdown(self.file_pairs, target_path)
            messagebox.showinfo(
                self.t["msg_csv_ok_title"],
                self.t["msg_export_md_text"] + f":\n{os.path.basename(target_path)}",
                parent=self.root,
            )
        except Exception as e:
            messagebox.showerror(self.t["msg_export_error_title"],
                                 self.t["msg_export_error_text"] + f":\n{e}", parent=self.root)


    def export_merged_html_table(self):
        """ eksport tabel HTML z wielu transkrypcji do jednej tabeli """
        self.save_current_text(silent=True)

        if not self.file_pairs:
            messagebox.showwarning(self.t["msg_export_txt_missing_title"],
                                   self.t["msg_export_txt_missing_text"], parent=self.root)
            return

        target_path = filedialog.asksaveasfilename(
            title=self.t["file_dialog_export_html_table_title"],
            defaultextension=".html",
            filetypes=[(self.t["file_type_html"], "*.html")],
            parent=self.root,
        )

        if not target_path:
            return

        try:
            row_count = export_merged_html_table(self.file_pairs, target_path)
            messagebox.showinfo(
                self.t["msg_csv_ok_title"],
                self.t["msg_export_html_table_text"] + f":\n{os.path.basename(target_path)}\n"
                + self.t["msg_export_html_table_rows"] + f": {row_count}",
                parent=self.root,
            )
        except Exception as e:
            messagebox.showerror(self.t["msg_export_error_title"],
                                 self.t["msg_export_error_text"] + f":\n{e}", parent=self.root)


    def export_html_tables_as_files(self):
        """ zapisuje tabele HTML z transkrypcji jako osobne pliki obok plików txt """
        self.save_current_text(silent=True)

        if not self.file_pairs:
            messagebox.showwarning(self.t["msg_export_txt_missing_title"],
                                   self.t["msg_export_txt_missing_text"], parent=self.root)
            return

        try:
            table_count = export_html_tables_as_files(self.file_pairs)
            messagebox.showinfo(
                self.t["msg_csv_ok_title"],
                self.t["msg_export_html_tables_files_text"] + f": {table_count}",
                parent=self.root,
            )
        except Exception as e:
            messagebox.showerror(self.t["msg_export_error_title"],
                                 self.t["msg_export_error_text"] + f":\n{e}", parent=self.root)


    def export_html_tables_xlsx(self):
        """ eksportuje tabele HTML z transkrypcji do skoroszytu XLSX """
        self.save_current_text(silent=True)

        if not self.file_pairs:
            messagebox.showwarning(self.t["msg_export_txt_missing_title"],
                                   self.t["msg_export_txt_missing_text"], parent=self.root)
            return

        target_path = filedialog.asksaveasfilename(
            title=self.t["file_dialog_export_html_tables_xlsx_title"],
            defaultextension=".xlsx",
            filetypes=[(self.t["file_type_xlsx"], "*.xlsx")],
            parent=self.root,
        )

        if not target_path:
            return

        try:
            table_count = export_html_tables_xlsx(self.file_pairs, target_path)
            messagebox.showinfo(
                self.t["msg_csv_ok_title"],
                self.t["msg_export_html_tables_xlsx_text"] + f":\n{os.path.basename(target_path)}\n"
                + self.t["msg_export_html_tables_xlsx_tables"] + f": {table_count}",
                parent=self.root,
            )
        except Exception as e:
            messagebox.showerror(self.t["msg_export_error_title"],
                                 self.t["msg_export_error_text"] + f":\n{e}", parent=self.root)


    def export_all_data_docx(self):
        """ eksport do pliku docx z łączeniem wyrazów """
        self.save_current_text(True)
        if not self.file_pairs:
            return

        path = filedialog.asksaveasfilename(
            title=self.t["file_dialog_export_docx_title"],
            defaultextension=".docx",
            filetypes=[(self.t["file_type_docx"], "*.docx")],
            parent=self.root)

        if not path:
            return

        try:
            export_docx(self.file_pairs, path)
            messagebox.showinfo(self.t["msg_csv_ok_title"],
                                self.t["msg_export_docx_text"] + f":\n{os.path.basename(path)}",
                                parent=self.root)

        except Exception as e:
            messagebox.showerror(self.t["msg_export_error_title"], str(e), parent=self.root)


    def on_close(self):
        """ bezpieczne zamknięcie aplikacji z zapisem """
        try:
            self.save_current_text(silent=True)
        except Exception as e:
            print(e)

        self.root.destroy()


    def show_magnifier(self, event):
        """ utworzenie okna lupy po naciśnięciu prawego przycisku myszy """
        self.canvas_controller.show_magnifier(event)


    def update_magnifier(self, event):
        """ aktualizacja pozycji okna i wycinany fragment obrazu podczas ruchu myszy """
        self.canvas_controller.update_magnifier(event)


    def hide_magnifier(self, event):
        """ zamykanie okna lupy po zwolnieniu prawego przycisku myszy """
        self.canvas_controller.hide_magnifier(event)


    def open_batch_dialog(self):
        """ otwiera okno dialogowe do przetwarzania seryjnego """
        open_batch_dialog_window(self)


    def _refresh_batch_list_ui(self):
        """ aktualizacja checkboxów: odznaczanie tych, które mają już transkrypcję """
        self.batch_controller.refresh_batch_list_ui()


    def cancel_batch_processing(self):
        """ ustawienie flagi przerwania przetwarzania seryjnego """
        self.batch_controller.cancel_batch_processing()


    def _batch_worker(self, selected_indices, window, btn_start, btn_cancel_batch):
        """ wątek przetwarzający listę plików """
        self.batch_controller.batch_worker(selected_indices, window, btn_start, btn_cancel_batch)


    def _update_batch_ui(self, message, progress_value):
        """ pomocnicza funkcja do aktualizacji UI w oknie batch """
        self.batch_controller.update_batch_ui(message, progress_value)


    def _ai_button_text(self):
        provider = getattr(self, "llm_provider", "gemini")
        if provider == "ollama":
            return "Ollama"
        if provider == "mistral":
            return "Mistral"
        if provider == "datalab":
            return "Datalab"
        return "Gemini"


    def _ollama_timeout_seconds(self):
        return max(int(getattr(self, "api_timeout_seconds", 300)), 900)


    def _transcribe_current_provider(self, image_path):
        if self.llm_provider == "ollama":
            return ollama_transcribe_image(
                self.prompt_text,
                image_path,
                model_name=self.ollama_htr_model,
                base_url=self.ollama_base_url,
                timeout_seconds=self._ollama_timeout_seconds(),
                remove_table_headers=self.ollama_remove_table_headers,
                pretty_html=self.ollama_pretty_html,
            )
        if self.llm_provider == "mistral":
            return mistral_ocr_image(
                image_path,
                model_name=self.mistral_ocr_model,
                timeout_seconds=self.api_timeout_seconds,
                include_blocks=self.mistral_include_blocks,
                table_format=self.mistral_table_format,
            )
        if self.llm_provider == "datalab":
            return datalab_convert_image(
                image_path,
                output_format=self.datalab_output_format,
                mode=self.datalab_mode,
                timeout_seconds=self.api_timeout_seconds,
            )
        return transcribe_image(
            self.api_key,
            self.prompt_text,
            image_path,
            model_name=self.htr_model,
            timeout_seconds=self.api_timeout_seconds,
        )


    def _stream_transcribe_current_provider(self, image_path):
        if self.llm_provider == "ollama":
            return ollama_stream_transcribe_image(
                self.prompt_text,
                image_path,
                model_name=self.ollama_htr_model,
                base_url=self.ollama_base_url,
                timeout_seconds=self._ollama_timeout_seconds(),
            )
        return stream_transcribe_image(
            self.api_key,
            self.prompt_text,
            image_path,
            model_name=self.htr_model,
            timeout_seconds=self.api_timeout_seconds,
        )


    def _log_model_usage(self, model, usage_metadata):
        if usage_metadata:
            self._log_api_usage(model, usage_metadata)


    def _call_gemini_api(self, image_path):
        """ wspólna funkcja wołająca API, zwraca tekst transkrypcji """
        model, response = self._transcribe_current_provider(image_path)
        self._log_model_usage(model, response.usage_metadata)

        return response.text

    def get_transcription_image_path(self, pair):
        """Zwraca oryginalny obraz albo zapisaną kopię z aktywnym filtrem."""
        return ensure_filtered_image(pair["img"], self.active_filter)


    def start_ai_transcription(self):
        """ inicjuje proces transkrypcji w tle """
        if not self.file_pairs or self.is_transcribing:
            return

        if self.llm_provider not in {"mistral", "datalab"} and not self.prompt_text:
            messagebox.showerror(self.t["prompt_config_error1"],
                                 self.t["prompt_config_error2"],
                                 parent=self.root)
            return

        if not self.ensure_ai_config():
            return

        current_text = self.text_area.get(1.0, tk.END).strip()
        if current_text:
            if not messagebox.askyesno(
                self.t["msg_overwrite_transcription_title"],
                self.t["msg_overwrite_transcription_text"],
                parent=self.root,
            ):
                return

        # blokada interfejsu
        self.is_transcribing = True
        self.btn_ai.config(state="disabled", text=self.t["btn_ai_process"])
        self.text_area.config(state="disabled") # bg="#222222" ?
        self.progress_bar.pack(fill=X, pady=(0, 10), before=self.editor_frame)
        self.progress_bar.start(10)

        current_pair = self.file_pairs[self.current_index]
        img_path = self.get_transcription_image_path(current_pair)

        # uruchomienie wątku
        thread = threading.Thread(target=self._single_worker, args=(img_path,))
        thread.daemon = True
        thread.start()


    def _single_worker(self, image_path):
        """ wątek dla pojedynczego pliku z obsługą strumieniowania """
        start_time = time.monotonic()
        try:
            if self.llm_provider in {"ollama", "mistral", "datalab"} or not self.stream_transcription:
                model, response = self._transcribe_current_provider(image_path)
                if response.usage_metadata:
                    self.root.after(0, lambda: self._log_model_usage(model, response.usage_metadata))
                elapsed_seconds = time.monotonic() - start_time
                self.root.after(0, self._single_finished, True, response.text or "", elapsed_seconds)
                return

            model, stream = self._stream_transcribe_current_provider(image_path)

            # czyszczenie pola tekstowego przed startem strumienia (w wątku głównym)
            self.root.after(0, self._clear_text_for_transcription)

            # iteracja po strumieniu odpowiedzi
            loop_usage_metadata = None
            for response in stream:
                if response.text:
                    # przekazanie fragmentu tekstu do aktualizacji UI
                    self.root.after(0, self._append_stream_text, response.text)
                if response.usage_metadata:
                    loop_usage_metadata = response.usage_metadata

            if loop_usage_metadata:
                self.root.after(0, lambda: self._log_model_usage(model, loop_usage_metadata))

            elapsed_seconds = time.monotonic() - start_time
            self.root.after(0, self._single_finished, True, "", elapsed_seconds)
        except Exception as e:
            elapsed_seconds = time.monotonic() - start_time
            self.root.after(0, self._single_finished, False, str(e), elapsed_seconds)


    def _append_stream_text(self, text):
        """ dodawanie fragmentu tekstu do edytora w czasie rzeczywistym """
        self.text_area.config(state="normal")
        self.text_area.insert(tk.END, text)
        self.text_area.see(tk.END)
        self.text_area.config(state="disabled") # blokada powraca na czas trwania procesu


    def _clear_text_for_transcription(self):
        """Czyści pole tekstowe przed rozpoczęciem nowej transkrypcji."""
        self.text_area.config(state="normal")
        self.text_area.delete(1.0, tk.END)
        self.text_area.config(state="disabled")


    def _single_finished(self, success, content, elapsed_seconds=None):
        """ aktualizacja GUI po zakończeniu pracy wątku """
        self.progress_bar.stop()
        self.progress_bar.pack_forget()
        self.is_transcribing = False
        self.btn_ai.config(state="normal", text=self._ai_button_text())
        self.text_area.config(state="normal")

        if success:
            if content:
                self.text_area.delete(1.0, tk.END)
                self.text_area.insert(tk.END, content)
            # zapisywanie finalnej wersji po zakończeniu strumieniowania
            self.save_current_text(True)
            message = self.t["msg_transcription_ok"]
            if elapsed_seconds is not None:
                message += f"\n{self.t['msg_transcription_elapsed']}: {elapsed_seconds:.1f} s"
            messagebox.showinfo(self.t["msg_csv_ok_title"],
                                message,
                                parent=self.root)
            self.root.focus_set()
        else:
            messagebox.showerror(self.t["msg_transcription_error_title"],
                                 "Nie udało się wykonać transkrypcji.",
                                 detail=str(content),
                                 parent=self.root)
            self.root.focus_set()


    def edit_current_prompt(self):
        """ Otwiera okno edycji aktualnego promptu """
        open_prompt_editor_dialog(self)


    def ensure_api_key(self):
        """Sprawdza, czy klucz API jest dostępny; w razie braku otwiera ustawienia."""
        if self.api_key:
            return True

        messagebox.showinfo(
            self.t["apikey_config_error1"],
            self.t["apikey_config_error2"],
            parent=self.root,
        )
        self.open_settings_dialog()
        return bool(self.api_key)


    def ensure_ai_config(self):
        """Sprawdza konfigurację dostawcy modelu przed wywołaniem AI."""
        provider = getattr(self, "llm_provider", "gemini")
        if provider == "ollama":
            return True
        if provider == "mistral":
            if get_mistral_api_key():
                return True
            messagebox.showinfo(
                self.t["apikey_config_error1"],
                self.t["mistral_apikey_config_error"],
                detail=self.t["mistral_apikey_config_detail"],
                parent=self.root,
            )
            self.open_settings_dialog()
            return bool(get_mistral_api_key())
        if provider == "datalab":
            if get_datalab_api_key():
                return True
            messagebox.showinfo(
                self.t["apikey_config_error1"],
                self.t["datalab_apikey_config_error"],
                detail=self.t["datalab_apikey_config_detail"],
                parent=self.root,
            )
            self.open_settings_dialog()
            return bool(get_datalab_api_key())
        return self.ensure_api_key()


    def ensure_non_ocr_ai_config(self):
        """Sprawdza konfigurację funkcji wymagających modelu promptowanego."""
        if getattr(self, "llm_provider", "gemini") in {"mistral", "datalab"}:
            messagebox.showinfo(
                self.t["msg_warning"],
                self.t["ocr_provider_only_info"],
                parent=self.root,
            )
            return False
        return self.ensure_ai_config()


    def open_settings_dialog(self):
        """ Otwiera okno ustawień aplikacji """
        open_settings_dialog_window(self)
        if self.btn_ai:
            self.btn_ai.config(text=self._ai_button_text())


# ----------------------------------- MAIN -------------------------------------
if __name__ == "__main__":
    from ui.main_window import launch

    launch()
