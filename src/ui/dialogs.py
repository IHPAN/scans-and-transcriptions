import os
import tkinter as tk
from tkinter import messagebox

import ttkbootstrap as ttk
from ttkbootstrap.constants import BOTTOM, BOTH, LEFT, RIGHT, VERTICAL, X, Y
from ttkbootstrap.widgets.scrolled import ScrolledFrame

from app.models import AppConfig
from services.config_service import load_app_config, save_app_config
from services.datalab_service import DEFAULT_DATALAB_MODE, DEFAULT_DATALAB_OUTPUT_FORMAT
from services.gemini_service import (
    model_code_for_label,
    model_label_for_code,
    model_labels,
)
from services.mistral_service import DEFAULT_MISTRAL_OCR_MODEL
from services.ollama_service import DEFAULT_OLLAMA_BASE_URL, list_models, normalize_base_url
from ui.window_utils import set_scaled_geometry


def open_batch_dialog(app):
    if app.is_transcribing:
        messagebox.showwarning(app.t["msg_warning"], app.t["msg_batch_warning_text"], parent=app.root)
        return

    if not app.file_pairs:
        messagebox.showinfo(app.t["msg_missing_files"], app.t["msg_missing_files_text"], parent=app.root)
        return

    batch_win = tk.Toplevel(app.root)
    batch_win.title(app.t["batch_win_title"])
    set_scaled_geometry(batch_win, 800, 700, parent=app.root)
    batch_win.transient(app.root)
    batch_win.grab_set()

    ttk.Label(batch_win, text=app.t["batch_label_text"], font=("Segoe UI", 12, "bold")).pack(pady=10)
    ttk.Label(batch_win, text=app.t["batch_label_info"], bootstyle="secondary", font=("Segoe UI", 9)).pack(
        pady=(0, 10)
    )

    list_frame = ttk.Frame(batch_win)
    list_frame.pack(fill=BOTH, expand=True, padx=10, pady=5)

    app.batch_vars = {}
    app.batch_tree = ttk.Treeview(
        list_frame,
        columns=("selected", "name", "status"),
        show="headings",
        selectmode="browse",
    )
    app.batch_tree.heading("selected", text="")
    app.batch_tree.heading("name", text=app.t["batch_column_file"])
    app.batch_tree.heading("status", text=app.t["batch_column_status"])
    app.batch_tree.column("selected", width=40, minwidth=40, stretch=False, anchor="center")
    app.batch_tree.column("name", width=520, minwidth=180, stretch=True)
    app.batch_tree.column("status", width=160, minwidth=120, stretch=False)

    scrollbar = ttk.Scrollbar(list_frame, orient=VERTICAL, command=app.batch_tree.yview)
    app.batch_tree.configure(yscrollcommand=scrollbar.set)
    app.batch_tree.pack(side=LEFT, fill=BOTH, expand=True)
    scrollbar.pack(side=RIGHT, fill=Y)

    for idx, pair in enumerate(app.file_pairs):
        txt_path = pair["txt"]
        should_select = False
        status_text = ""

        if not os.path.exists(txt_path):
            should_select = True
            status_text = app.t["batch_status_text1"]
        elif os.path.getsize(txt_path) == 0:
            should_select = True
            status_text = app.t["batch_status_text2"]
        else:
            status_text = app.t["batch_status_text3"]

        item_id = str(idx)
        app.batch_vars[idx] = should_select
        app.batch_tree.insert("", "end", iid=item_id, values=("☑" if should_select else "☐", pair["name"], status_text))

    def toggle_item(item_id):
        if not item_id:
            return
        idx = int(item_id)
        app.batch_vars[idx] = not app.batch_vars.get(idx, False)
        selected_mark = "☑" if app.batch_vars[idx] else "☐"
        values = list(app.batch_tree.item(item_id, "values"))
        if values:
            values[0] = selected_mark
            app.batch_tree.item(item_id, values=values)

    def on_tree_click(event):
        item_id = app.batch_tree.identify_row(event.y)
        if item_id:
            toggle_item(item_id)

    def on_tree_space(_event):
        selection = app.batch_tree.selection()
        if selection:
            toggle_item(selection[0])
        return "break"

    app.batch_tree.bind("<ButtonRelease-1>", on_tree_click)
    app.batch_tree.bind("<space>", on_tree_space)

    btn_panel = ttk.Frame(batch_win, padding=10)
    btn_panel.pack(fill=X, side=BOTTOM)

    app.batch_log_label = ttk.Label(batch_win, text=app.t["batch_log_label"], bootstyle="inverse-secondary")
    app.batch_log_label.pack(fill=X, side=BOTTOM, padx=10)

    app.batch_progress = ttk.Progressbar(batch_win, mode="determinate", bootstyle="success-striped")
    app.batch_progress.pack(fill=X, side=BOTTOM, padx=10, pady=5)

    def select_all():
        for idx in app.batch_vars:
            app.batch_vars[idx] = True
            values = list(app.batch_tree.item(str(idx), "values"))
            if values:
                values[0] = "☑"
                app.batch_tree.item(str(idx), values=values)

    def select_none():
        for idx in app.batch_vars:
            app.batch_vars[idx] = False
            values = list(app.batch_tree.item(str(idx), "values"))
            if values:
                values[0] = "☐"
                app.batch_tree.item(str(idx), values=values)

    def start_batch():
        selected_indices = [idx for idx, selected in app.batch_vars.items() if selected]
        if not selected_indices:
            messagebox.showwarning("Info", app.t["batch_no_files_selected"], parent=batch_win)
            return
        app.batch_controller.start_batch(selected_indices, batch_win, btn_start, btn_cancel_batch)

    ttk.Button(btn_panel, text=app.t["batch_select_all"], command=select_all, bootstyle="outline-secondary").pack(
        side=LEFT, padx=5
    )
    ttk.Button(btn_panel, text=app.t["batch_unselect_all"], command=select_none, bootstyle="outline-secondary").pack(
        side=LEFT, padx=5
    )

    btn_start = ttk.Button(btn_panel, text=app.t["btn_start"], command=start_batch, bootstyle="danger")
    btn_start.pack(side=RIGHT, padx=5)

    btn_cancel_batch = ttk.Button(
        btn_panel,
        text=app.t["btn_batch_cancel"],
        command=app.batch_controller.cancel_batch_processing,
        bootstyle="outline-danger",
        state="disabled",
    )
    btn_cancel_batch.pack(side=RIGHT, padx=5)


def edit_current_prompt(app):
    if not app.current_prompt_path or not os.path.exists(app.current_prompt_path):
        messagebox.showwarning(app.t["msg_edit_prompt_error_title"], app.t["msg_edit_prompt_error_text"], parent=app.root)
        return

    edit_win = tk.Toplevel(app.root)
    edit_win.title(app.t["edit_win_title"] + f": {os.path.basename(app.current_prompt_path)}")
    set_scaled_geometry(edit_win, 850, 600, parent=app.root)
    edit_win.transient(app.root)
    edit_win.grab_set()

    btn_frame = ttk.Frame(edit_win)
    btn_frame.pack(side=BOTTOM, fill=X, pady=15)

    def save_prompt_changes():
        new_content = txt_edit.get(1.0, tk.END).strip()
        if not new_content:
            messagebox.showwarning(app.t["msg_error_title"], app.t["msg_save_prompt_empty"], parent=edit_win)
            return

        try:
            with open(app.current_prompt_path, "w", encoding="utf-8") as f:
                f.write(new_content)
            app.prompt_text = new_content
            messagebox.showinfo(app.t["msg_save_prompt_title"], app.t["msg_save_prompt_text"], parent=edit_win)
            edit_win.destroy()
        except Exception as e:
            messagebox.showerror(app.t["msg_save_prompy_error_title"], str(e), parent=edit_win)

    def restore_from_file():
        if messagebox.askyesno(app.t["msg_prompt_restore_title"], app.t["msg_prompt_restore_text"], parent=edit_win):
            try:
                with open(app.current_prompt_path, "r", encoding="utf-8") as f:
                    content = f.read()
                txt_edit.delete(1.0, tk.END)
                txt_edit.insert(tk.END, content)
            except Exception as e:
                messagebox.showerror(app.t["msg_error_title"], app.t["msg_prompt_restore_error"] + f": {e}", parent=edit_win)

    def on_close_prompt_edit():
        current_content = txt_edit.get(1.0, tk.END).strip()
        if current_content != app.prompt_text.strip():
            if messagebox.askyesno(app.t["msg_on_close_title"], app.t["msg_on_close_text"], parent=edit_win):
                edit_win.destroy()
        else:
            edit_win.destroy()

    edit_win.protocol("WM_DELETE_WINDOW", on_close_prompt_edit)

    btn_save = ttk.Button(btn_frame, text=app.t["btn_save_prompt"], command=save_prompt_changes, bootstyle="success")
    btn_save.pack(side=RIGHT, padx=20)

    btn_restore = ttk.Button(
        btn_frame,
        text=app.t["btn_restore_prompt"],
        command=restore_from_file,
        bootstyle="outline-secondary",
    )
    btn_restore.pack(side=LEFT, padx=20)

    text_container = ttk.Frame(edit_win)
    text_container.pack(fill=BOTH, expand=True, padx=15, pady=(15, 0))

    scrollbar = ttk.Scrollbar(text_container, orient=VERTICAL)
    scrollbar.pack(side=RIGHT, fill=Y)

    txt_edit = tk.Text(text_container, font=("Consolas", 11), wrap=tk.WORD, undo=True, yscrollcommand=scrollbar.set)
    txt_edit.insert(tk.END, app.prompt_text)
    txt_edit.pack(side=LEFT, fill=BOTH, expand=True)

    scrollbar.config(command=txt_edit.yview)
    txt_edit.focus_set()


def open_settings_dialog(app):
    settings_win = tk.Toplevel(app.root)
    settings_win.title(app.t["settings_win_title"])
    set_scaled_geometry(settings_win, 760, 760, parent=app.root)
    settings_win.resizable(True, True)
    settings_win.transient(app.root)
    settings_win.grab_set()

    btn_row = ttk.Frame(settings_win, padding=(15, 10))
    btn_row.pack(fill=X, side=BOTTOM)

    notebook = ttk.Notebook(settings_win)
    notebook.pack(fill=BOTH, expand=True, padx=15, pady=15)
    general_tab_frame = ttk.Frame(notebook)
    gemini_tab_frame = ttk.Frame(notebook)
    ollama_tab_frame = ttk.Frame(notebook)
    mistral_tab_frame = ttk.Frame(notebook)
    datalab_tab_frame = ttk.Frame(notebook)
    litellm_tab_frame = ttk.Frame(notebook)
    notebook.add(general_tab_frame, text=app.t["settings_tab_general"])
    notebook.add(gemini_tab_frame, text=app.t["settings_tab_gemini"])
    notebook.add(ollama_tab_frame, text=app.t["settings_tab_ollama"])
    notebook.add(mistral_tab_frame, text=app.t["settings_tab_mistral"])
    notebook.add(datalab_tab_frame, text=app.t["settings_tab_datalab"])
    notebook.add(litellm_tab_frame, text="LiteLLM")

    general_tab = ScrolledFrame(general_tab_frame, autohide=False)
    gemini_tab = ScrolledFrame(gemini_tab_frame, autohide=False)
    ollama_tab = ScrolledFrame(ollama_tab_frame, autohide=False)
    mistral_tab = ScrolledFrame(mistral_tab_frame, autohide=False)
    datalab_tab = ScrolledFrame(datalab_tab_frame, autohide=False)
    litellm_tab = ScrolledFrame(litellm_tab_frame, autohide=False)
    general_tab.pack(fill=BOTH, expand=True, padx=10, pady=10)
    gemini_tab.pack(fill=BOTH, expand=True, padx=10, pady=10)
    ollama_tab.pack(fill=BOTH, expand=True, padx=10, pady=10)
    mistral_tab.pack(fill=BOTH, expand=True, padx=10, pady=10)
    datalab_tab.pack(fill=BOTH, expand=True, padx=10, pady=10)
    litellm_tab.pack(fill=BOTH, expand=True, padx=10, pady=10)

    config = load_app_config(app.config_file)
    provider_var = tk.StringVar(value=config.llm_provider)
    litellm_endpoint_var = tk.StringVar(value=config.litellm_endpoint)
    litellm_model_var = tk.StringVar(value=config.litellm_model)
    api_key_var = tk.StringVar(value=config.api_key)
    htr_model_var = tk.StringVar(value=model_label_for_code("htr", config.htr_model))
    fix_model_var = tk.StringVar(value=model_label_for_code("fix", config.fix_model))
    analysis_model_var = tk.StringVar(value=model_label_for_code("analysis", config.analysis_model))
    box_model_var = tk.StringVar(value=model_label_for_code("box", config.box_model))
    ollama_base_url_var = tk.StringVar(value=config.ollama_base_url or DEFAULT_OLLAMA_BASE_URL)
    ollama_htr_model_var = tk.StringVar(value=config.ollama_htr_model)
    ollama_fix_model_var = tk.StringVar(value=config.ollama_fix_model)
    ollama_analysis_model_var = tk.StringVar(value=config.ollama_analysis_model)
    ollama_box_model_var = tk.StringVar(value=config.ollama_box_model)
    ollama_remove_table_headers_var = tk.BooleanVar(value=config.ollama_remove_table_headers)
    ollama_pretty_html_var = tk.BooleanVar(value=config.ollama_pretty_html)
    mistral_ocr_model_var = tk.StringVar(value=config.mistral_ocr_model or DEFAULT_MISTRAL_OCR_MODEL)
    mistral_include_blocks_var = tk.BooleanVar(value=config.mistral_include_blocks)
    mistral_table_format_var = tk.StringVar(value=config.mistral_table_format)
    datalab_output_format_var = tk.StringVar(value=config.datalab_output_format or DEFAULT_DATALAB_OUTPUT_FORMAT)
    datalab_mode_var = tk.StringVar(value=config.datalab_mode or DEFAULT_DATALAB_MODE)
    api_timeout_var = tk.IntVar(value=config.api_timeout_seconds)
    stream_transcription_var = tk.BooleanVar(value=config.stream_transcription)
    batch_parallel_workers_var = tk.IntVar(value=config.batch_parallel_workers)

    ttk.Label(general_tab, text=app.t["settings_provider_label"], font=("Segoe UI", 10, "bold")).pack(anchor="w")
    provider_combo = ttk.Combobox(
        general_tab,
        state="readonly",
        values=["gemini", "ollama", "mistral", "datalab", "litellm"],
        textvariable=provider_var,
        width=16,
    )
    provider_combo.pack(anchor="w", pady=(2, 10))
    provider_combo.focus_set()

    ttk.Separator(general_tab).pack(fill=X, pady=(4, 10))

    ttk.Label(litellm_tab, text=app.t["litellm_endpoint_label"]).pack(anchor="w")
    ttk.Entry(litellm_tab, textvariable=litellm_endpoint_var).pack(fill=X, pady=(2, 10))
    ttk.Label(litellm_tab, text=app.t["litellm_model_label"]).pack(anchor="w")
    ttk.Entry(litellm_tab, textvariable=litellm_model_var).pack(fill=X, pady=(2, 10))
    ttk.Label(litellm_tab, text=app.t["litellm_key_help"], wraplength=640).pack(anchor="w")

    ttk.Label(gemini_tab, text=app.t["settings_api_key_label"], font=("Segoe UI", 10, "bold")).pack(anchor="w")
    ttk.Label(
        gemini_tab,
        text=app.t["settings_api_key_help"],
        bootstyle="secondary",
        font=("Segoe UI", 8),
        wraplength=640,
        justify=LEFT,
    ).pack(anchor="w", pady=(2, 10))

    entry = ttk.Entry(gemini_tab, textvariable=api_key_var, width=72, show="*")
    entry.pack(fill=X, pady=(0, 6))

    show_var = tk.BooleanVar(value=False)

    def toggle_visibility():
        entry.config(show="" if show_var.get() else "*")

    ttk.Checkbutton(
        gemini_tab,
        text=app.t["settings_show_key"],
        variable=show_var,
        command=toggle_visibility,
        bootstyle="round-toggle",
    ).pack(anchor="w", pady=(0, 12))

    ttk.Separator(gemini_tab).pack(fill=X, pady=(4, 10))

    ttk.Label(gemini_tab, text=app.t["settings_models_label"], font=("Segoe UI", 10, "bold")).pack(anchor="w")
    ttk.Label(
        gemini_tab,
        text=app.t["settings_models_help"],
        bootstyle="secondary",
        font=("Segoe UI", 8),
        wraplength=640,
        justify=LEFT,
    ).pack(anchor="w", pady=(2, 10))

    ttk.Label(gemini_tab, text=app.t["settings_htr_model_label"]).pack(anchor="w")
    htr_combo = ttk.Combobox(gemini_tab, state="readonly", values=model_labels("htr"), textvariable=htr_model_var)
    htr_combo.pack(fill=X, pady=(2, 8))

    ttk.Label(gemini_tab, text=app.t["settings_fix_model_label"]).pack(anchor="w")
    fix_combo = ttk.Combobox(gemini_tab, state="readonly", values=model_labels("fix"), textvariable=fix_model_var)
    fix_combo.pack(fill=X, pady=(2, 8))

    ttk.Label(gemini_tab, text=app.t["settings_analysis_model_label"]).pack(anchor="w")
    analysis_combo = ttk.Combobox(
        gemini_tab,
        state="readonly",
        values=model_labels("analysis"),
        textvariable=analysis_model_var,
    )
    analysis_combo.pack(fill=X, pady=(2, 10))

    ttk.Label(gemini_tab, text=app.t["settings_box_model_label"]).pack(anchor="w")
    box_combo = ttk.Combobox(gemini_tab, state="readonly", values=model_labels("box"), textvariable=box_model_var)
    box_combo.pack(fill=X, pady=(2, 10))

    ttk.Separator(ollama_tab).pack(fill=X, pady=(4, 10))

    ttk.Label(ollama_tab, text=app.t["settings_ollama_label"], font=("Segoe UI", 10, "bold")).pack(anchor="w")
    ttk.Label(
        ollama_tab,
        text=app.t["settings_ollama_help"],
        bootstyle="secondary",
        font=("Segoe UI", 8),
        wraplength=680,
        justify=LEFT,
    ).pack(anchor="w", pady=(2, 8))

    ttk.Label(ollama_tab, text=app.t["settings_ollama_base_url_label"]).pack(anchor="w")
    ollama_base_url_entry = ttk.Entry(ollama_tab, textvariable=ollama_base_url_var, width=72)
    ollama_base_url_entry.pack(fill=X, pady=(2, 8))

    ollama_model_values = []
    try:
        ollama_model_values = list_models(ollama_base_url_var.get(), timeout_seconds=2)
    except Exception:
        ollama_model_values = []

    ttk.Label(ollama_tab, text=app.t["settings_htr_model_label"]).pack(anchor="w")
    ollama_htr_combo = ttk.Combobox(ollama_tab, values=ollama_model_values, textvariable=ollama_htr_model_var)
    ollama_htr_combo.pack(fill=X, pady=(2, 6))

    ttk.Label(ollama_tab, text=app.t["settings_fix_model_label"]).pack(anchor="w")
    ollama_fix_combo = ttk.Combobox(ollama_tab, values=ollama_model_values, textvariable=ollama_fix_model_var)
    ollama_fix_combo.pack(fill=X, pady=(2, 6))

    ttk.Label(ollama_tab, text=app.t["settings_analysis_model_label"]).pack(anchor="w")
    ollama_analysis_combo = ttk.Combobox(ollama_tab, values=ollama_model_values, textvariable=ollama_analysis_model_var)
    ollama_analysis_combo.pack(fill=X, pady=(2, 6))

    ttk.Label(ollama_tab, text=app.t["settings_box_model_label"]).pack(anchor="w")
    ollama_box_combo = ttk.Combobox(ollama_tab, values=ollama_model_values, textvariable=ollama_box_model_var)
    ollama_box_combo.pack(fill=X, pady=(2, 8))

    def refresh_ollama_models():
        try:
            models = list_models(ollama_base_url_var.get(), timeout_seconds=5)
        except Exception as e:
            messagebox.showerror(app.t["msg_error_title"], str(e), parent=settings_win)
            return
        for combo in [ollama_htr_combo, ollama_fix_combo, ollama_analysis_combo, ollama_box_combo]:
            combo.config(values=models)
        messagebox.showinfo(app.t["msg_save_config_title"], app.t["settings_ollama_models_loaded"], parent=settings_win)

    ttk.Button(
        ollama_tab,
        text=app.t["settings_ollama_refresh_models"],
        command=refresh_ollama_models,
        bootstyle="outline-secondary",
    ).pack(anchor="w", pady=(0, 10))

    ttk.Checkbutton(
        ollama_tab,
        text=app.t["settings_ollama_remove_table_headers"],
        variable=ollama_remove_table_headers_var,
        bootstyle="round-toggle",
    ).pack(anchor="w", pady=(0, 10))

    ttk.Checkbutton(
        ollama_tab,
        text=app.t["settings_ollama_pretty_html"],
        variable=ollama_pretty_html_var,
        bootstyle="round-toggle",
    ).pack(anchor="w", pady=(0, 10))

    ttk.Separator(mistral_tab).pack(fill=X, pady=(4, 10))

    ttk.Label(mistral_tab, text=app.t["settings_mistral_label"], font=("Segoe UI", 10, "bold")).pack(anchor="w")
    ttk.Label(
        mistral_tab,
        text=app.t["settings_mistral_help"],
        bootstyle="secondary",
        font=("Segoe UI", 8),
        wraplength=680,
        justify=LEFT,
    ).pack(anchor="w", pady=(2, 8))

    ttk.Label(mistral_tab, text=app.t["settings_mistral_key_label"]).pack(anchor="w")
    ttk.Label(
        mistral_tab,
        text=app.t["settings_mistral_key_help"],
        bootstyle="secondary",
        font=("Segoe UI", 8),
        wraplength=680,
        justify=LEFT,
    ).pack(anchor="w", pady=(2, 10))

    ttk.Label(mistral_tab, text=app.t["settings_mistral_ocr_model_label"]).pack(anchor="w")
    mistral_model_entry = ttk.Entry(mistral_tab, textvariable=mistral_ocr_model_var, width=72)
    mistral_model_entry.pack(fill=X, pady=(2, 8))

    ttk.Label(mistral_tab, text=app.t["settings_mistral_table_format_label"]).pack(anchor="w")
    mistral_table_format_combo = ttk.Combobox(
        mistral_tab,
        state="readonly",
        values=["markdown", "html"],
        textvariable=mistral_table_format_var,
        width=16,
    )
    mistral_table_format_combo.pack(anchor="w", pady=(2, 10))

    ttk.Checkbutton(
        mistral_tab,
        text=app.t["settings_mistral_include_blocks"],
        variable=mistral_include_blocks_var,
        bootstyle="round-toggle",
    ).pack(anchor="w", pady=(0, 10))

    ttk.Label(
        mistral_tab,
        text=app.t["settings_mistral_prompt_note"],
        bootstyle="secondary",
        font=("Segoe UI", 8),
        wraplength=680,
        justify=LEFT,
    ).pack(anchor="w", pady=(2, 10))

    ttk.Separator(datalab_tab).pack(fill=X, pady=(4, 10))

    ttk.Label(datalab_tab, text=app.t["settings_datalab_label"], font=("Segoe UI", 10, "bold")).pack(anchor="w")
    ttk.Label(
        datalab_tab,
        text=app.t["settings_datalab_help"],
        bootstyle="secondary",
        font=("Segoe UI", 8),
        wraplength=680,
        justify=LEFT,
    ).pack(anchor="w", pady=(2, 8))

    ttk.Label(datalab_tab, text=app.t["settings_datalab_key_label"]).pack(anchor="w")
    ttk.Label(
        datalab_tab,
        text=app.t["settings_datalab_key_help"],
        bootstyle="secondary",
        font=("Segoe UI", 8),
        wraplength=680,
        justify=LEFT,
    ).pack(anchor="w", pady=(2, 10))

    ttk.Label(datalab_tab, text=app.t["settings_datalab_output_format_label"]).pack(anchor="w")
    datalab_output_format_combo = ttk.Combobox(
        datalab_tab,
        state="readonly",
        values=["markdown", "html", "json"],
        textvariable=datalab_output_format_var,
        width=16,
    )
    datalab_output_format_combo.pack(anchor="w", pady=(2, 10))

    ttk.Label(datalab_tab, text=app.t["settings_datalab_mode_label"]).pack(anchor="w")
    datalab_mode_combo = ttk.Combobox(
        datalab_tab,
        state="readonly",
        values=["fast", "balanced", "accurate"],
        textvariable=datalab_mode_var,
        width=16,
    )
    datalab_mode_combo.pack(anchor="w", pady=(2, 10))

    ttk.Separator(general_tab).pack(fill=X, pady=(4, 10))

    ttk.Label(general_tab, text=app.t["settings_timeout_label"]).pack(anchor="w")
    timeout_spin = ttk.Spinbox(
        general_tab,
        from_=30,
        to=3600,
        increment=30,
        textvariable=api_timeout_var,
        width=10,
    )
    timeout_spin.pack(anchor="w", pady=(2, 4))
    ttk.Label(
        general_tab,
        text=app.t["settings_timeout_help"],
        bootstyle="secondary",
        font=("Segoe UI", 8),
        wraplength=640,
        justify=LEFT,
    ).pack(anchor="w", pady=(0, 10))

    ttk.Checkbutton(
        general_tab,
        text=app.t["settings_stream_transcription"],
        variable=stream_transcription_var,
        bootstyle="round-toggle",
    ).pack(anchor="w", pady=(0, 10))

    ttk.Label(general_tab, text=app.t["settings_batch_parallel_workers_label"]).pack(anchor="w")
    batch_parallel_workers_spin = ttk.Spinbox(
        general_tab,
        from_=1,
        to=8,
        increment=1,
        textvariable=batch_parallel_workers_var,
        width=10,
    )
    batch_parallel_workers_spin.pack(anchor="w", pady=(2, 4))
    ttk.Label(
        general_tab,
        text=app.t["settings_batch_parallel_workers_help"],
        bootstyle="secondary",
        font=("Segoe UI", 8),
        wraplength=640,
        justify=LEFT,
    ).pack(anchor="w", pady=(0, 10))

    def save_settings():
        app.llm_provider = provider_var.get()
        app.litellm_endpoint = litellm_endpoint_var.get().strip()
        app.litellm_model = litellm_model_var.get().strip()
        app.api_key = api_key_var.get().strip()
        app.htr_model = model_code_for_label("htr", htr_model_var.get())
        app.fix_model = model_code_for_label("fix", fix_model_var.get())
        app.analysis_model = model_code_for_label("analysis", analysis_model_var.get())
        app.box_model = model_code_for_label("box", box_model_var.get())
        app.ollama_base_url = normalize_base_url(ollama_base_url_var.get())
        app.ollama_htr_model = ollama_htr_model_var.get().strip()
        app.ollama_fix_model = ollama_fix_model_var.get().strip()
        app.ollama_analysis_model = ollama_analysis_model_var.get().strip()
        app.ollama_box_model = ollama_box_model_var.get().strip()
        app.ollama_remove_table_headers = bool(ollama_remove_table_headers_var.get())
        app.ollama_pretty_html = bool(ollama_pretty_html_var.get())
        app.mistral_ocr_model = mistral_ocr_model_var.get().strip() or DEFAULT_MISTRAL_OCR_MODEL
        app.mistral_include_blocks = bool(mistral_include_blocks_var.get())
        app.mistral_table_format = mistral_table_format_var.get() if mistral_table_format_var.get() in {"markdown", "html"} else "markdown"
        app.datalab_output_format = (
            datalab_output_format_var.get()
            if datalab_output_format_var.get() in {"markdown", "html", "json"}
            else DEFAULT_DATALAB_OUTPUT_FORMAT
        )
        app.datalab_mode = (
            datalab_mode_var.get()
            if datalab_mode_var.get() in {"fast", "balanced", "accurate"}
            else DEFAULT_DATALAB_MODE
        )
        try:
            app.api_timeout_seconds = max(30, min(int(api_timeout_var.get()), 3600))
        except (tk.TclError, ValueError):
            messagebox.showerror(
                app.t["msg_error_title"],
                app.t["settings_timeout_error"],
                parent=settings_win,
            )
            return
        app.stream_transcription = bool(stream_transcription_var.get())
        try:
            app.batch_parallel_workers = max(1, min(int(batch_parallel_workers_var.get()), 8))
        except (tk.TclError, ValueError):
            messagebox.showerror(
                app.t["msg_error_title"],
                app.t["settings_batch_parallel_workers_error"],
                parent=settings_win,
            )
            return
        try:
            save_app_config(
                AppConfig(
                    font_size=app.font_size,
                    current_lang=app.current_lang,
                    default_prompt=app.default_prompt,
                    last_folder=getattr(app, "last_folder", ""),
                    llm_provider=app.llm_provider,
                    litellm_endpoint=app.litellm_endpoint,
                    litellm_model=app.litellm_model,
                    api_key=app.api_key,
                    htr_model=app.htr_model,
                    fix_model=app.fix_model,
                    analysis_model=app.analysis_model,
                    box_model=app.box_model,
                    ollama_base_url=app.ollama_base_url,
                    ollama_htr_model=app.ollama_htr_model,
                    ollama_fix_model=app.ollama_fix_model,
                    ollama_analysis_model=app.ollama_analysis_model,
                    ollama_box_model=app.ollama_box_model,
                    ollama_remove_table_headers=app.ollama_remove_table_headers,
                    ollama_pretty_html=app.ollama_pretty_html,
                    mistral_ocr_model=app.mistral_ocr_model,
                    mistral_include_blocks=app.mistral_include_blocks,
                    mistral_table_format=app.mistral_table_format,
                    datalab_output_format=app.datalab_output_format,
                    datalab_mode=app.datalab_mode,
                    api_timeout_seconds=app.api_timeout_seconds,
                    stream_transcription=app.stream_transcription,
                    batch_parallel_workers=app.batch_parallel_workers,
                ),
                app.config_file,
            )
            messagebox.showinfo(app.t["msg_save_config_title"], app.t["msg_save_config_ok"], parent=settings_win)
            settings_win.destroy()
        except Exception as e:
            messagebox.showerror(
                app.t["msg_error_title"],
                app.t["msg_save_config_file_error"] + f": {e}",
                parent=settings_win,
            )

    ttk.Button(
        btn_row,
        text=app.t["btn_settings_cancel"],
        command=settings_win.destroy,
        bootstyle="outline-secondary",
    ).pack(side=LEFT)
    ttk.Button(
        btn_row,
        text=app.t["btn_settings_save"],
        command=save_settings,
        bootstyle="success",
    ).pack(side=RIGHT)

    settings_win.wait_window()
